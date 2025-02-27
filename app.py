# app.py
import requests
from flask import Flask, render_template, request, jsonify, session
from geopy.distance import geodesic
from dotenv import load_dotenv
import os
import json
from datetime import datetime, timedelta
import polyline

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-key-change-this')

GOOGLE_MAPS_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY')
OPEN_CHARGE_API_KEY = os.getenv('OPEN_CHARGE_API_KEY')

# Vehicle range dictionary (in km)
vehicle_ranges = {
    'Model X': 500,
    'Model Y': 450,
    'Tata Nexon': 312,
    'Mahindra e2o': 140
}

@app.route('/')
def home():
    return render_template('index.html', google_maps_api_key=GOOGLE_MAPS_API_KEY)

@app.route('/search', methods=['POST'])  #@app.route defines a URL path (/search).methods=['POST'] allows data submission through forms.
def search():
    data = request.form
    from_location = data['from']
    to_location = data['to']
    vehicle_model = data['vehicle_model']
    battery_level = int(data['battery_level'])

    # Get multiple routes
    routes = get_routes(from_location, to_location) #get_routes function calls Google Directions API to fetch possible routes.
    if not routes:
        return "Error: Could not fetch routes. Please check your input and try again."

    # Calculate estimated range
    estimated_range = (battery_level / 100) * vehicle_ranges[vehicle_model]

    # Get charging stations for each route
    for route in routes:
        route['charging_stations'] = get_combined_charging_stations(route['points']) #get_combined_charging_stations function combines results from Google Places API and Open Charge Map API to find charging stations along the route.

    try:
        # Store only essential data in session
        simplified_routes = [] #simplified_routes is a list that will store only the essential data from each route.
        for route in routes:
            simplified_route = { #simplified_route is a dictionary that stores the essential data from each route.
                'id': route['id'],
                'distance': route['distance'],
                'duration': route['duration'],
                'summary': route.get('summary', ''),  # Include route summary
                'points': route['points'],
                'charging_stations': [
                    {
                        'lat': station['lat'],
                        'lng': station['lng'],
                        'name': station['name'],
                        'address': station.get('address', ''),
                        'source': station.get('source', '')
                    }
                    for station in route['charging_stations']
                ]
            }
            simplified_routes.append(simplified_route)

        session['routes'] = simplified_routes
    except Exception as e:
        print(f"Error storing routes in session: {e}")
        pass

    # Add a message if only one route is available
    single_route_message = None
    if len(routes) == 1:
        single_route_message = "Only one route is available for this journey. Alternative routes may be available for different locations or shorter distances."

    return render_template('route.html', 
                         routes=routes,
                         estimated_range=estimated_range,
                         from_location=from_location,
                         to_location=to_location,
                         single_route_message=single_route_message,
                         google_maps_api_key=GOOGLE_MAPS_API_KEY)

def get_routes(from_location, to_location):
    """
    Fetch routes from Google Directions API
    Returns multiple routes if available
    """
    url = "https://maps.googleapis.com/maps/api/directions/json"#url is the URL of the Google Directions API.
    params = {
        'origin': from_location,
        'destination': to_location,
        'alternatives': 'true',  # Request alternative routes
        'key': GOOGLE_MAPS_API_KEY
    }

    try:
        # Make request to Google Directions API
        print(f"Fetching routes from {from_location} to {to_location}")
        response = requests.get(url, params=params)#response is the response from the Google Directions API.
        response.raise_for_status()
        data = response.json()

        # Log API response details
        print(f"Google Directions API response status: {data['status']}")
        num_routes = len(data.get('routes', []))
        print(f"Number of routes returned: {num_routes}")

        if data['status'] != 'OK':
            print(f"Error response from Google Directions API: {data}")
            return None

        if num_routes == 0:
            print("No routes found")
            return None

        # Process each route
        routes = []
        for i, route in enumerate(data['routes']):
            points = []
            path = []
            distance = 0
            duration = 0

            # Get route summary if available
            summary = route.get('summary', '')

            # Process each leg of the route
            for leg in route['legs']:#for each leg in the route, the distance and duration are calculated.
                distance += leg['distance']['value']
                duration += leg['duration']['value']

                # Extract points along the route for charging station search
                for step in leg['steps']:
                    points.append({
                        'lat': step['end_location']['lat'],
                        'lng': step['end_location']['lng']
                    })
                    path.append(step['polyline']['points'])

            # Create route object with all necessary information
            routes.append({
                'id': i,
                'points': points,
                'path': path,
                'distance': round(distance / 1000, 2),  # Convert to km
                'duration': round(duration / 60),  # Convert to minutes
                'summary': summary,
                'charging_stations': []
            })

        print(f"Processed {len(routes)} routes successfully")
        if len(routes) == 1:
            print("Only one route available for this journey")
        return routes
    except Exception as e:
        print(f"Error fetching routes: {e}")
        return None

def get_combined_charging_stations(route_points):
    """
    Find charging stations along a route by combining results from
    Google Places API and Open Charge Map API
    """
    stations = [] #stations is a list that will store the charging stations found along the route.
    seen_stations = set()  #seen_stations is a set that will track the unique charging stations found along the route.

    # Sample points along the route to search for stations
    # Take every 5th point, but ensure at least 5 and at most 20 sample points
    step = max(1, len(route_points) // 10) #step is the number of points between each sample point.
    sample_points = route_points[::step]
    if len(sample_points) > 20:
        sample_points = sample_points[:20]

    # Search for stations at each sample point
    for point in sample_points:
        # Get stations from both APIs
        google_stations = get_google_charging_stations(point)
        ocm_stations = get_ocm_charging_stations(point)

        # Combine stations, avoiding duplicates
        for station in google_stations + ocm_stations: #for each station in the list of stations from both APIs, the station_id is calculated.
            station_id = f"{station['lat']}-{station['lng']}" #station_id is a unique identifier for each station.
            if station_id not in seen_stations: #if the station_id is not in the set of seen_stations, the station is added to the list of stations.
                seen_stations.add(station_id) #the station_id is added to the set of seen_stations.
                stations.append(station) #the station is added to the list of stations.

    return stations

def get_google_charging_stations(point):
    """
    Search for charging stations using Google Places API
    """
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        'location': f"{point['lat']},{point['lng']}",
        'radius': 5000,  # 5km radius
        'keyword': 'EV charging station',
        'type': 'electric_vehicle_charging_station',
        'key': GOOGLE_MAPS_API_KEY
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        stations = []
        if data.get('status') == 'OK':
            for place in data['results']:
                # Verify this is actually an EV charging station
                name = place.get('name', '').lower()
                types = [t.lower() for t in place.get('types', [])]

                is_ev_station = (
                    'charge' in name or 
                    'charging' in name or 
                    'ev' in name or 
                    'electric' in name or
                    'electric_vehicle_charging_station' in types
                )

                if is_ev_station:
                    stations.append({
                        'source': 'google',
                        'name': place.get('name', 'Unknown Station'),
                        'lat': place['geometry']['location']['lat'],
                        'lng': place['geometry']['location']['lng'],
                        'address': place.get('vicinity', 'Address not available'),
                        'rating': place.get('rating', 'N/A'),
                        'is_operational': place.get('business_status', '') == 'OPERATIONAL'
                    })
        return stations
    except Exception as e:
        print(f"Error fetching Google charging stations: {e}")
        return []

def get_ocm_charging_stations(point):
    url = "https://api.openchargemap.io/v3/poi"
    params = {
        'key': OPEN_CHARGE_API_KEY,
        'latitude': point['lat'],
        'longitude': point['lng'],
        'distance': 5,  # 5km radius
        'distanceunit': 'km',
        'maxresults': 10,
        'compact': True,
        'verbose': False,
        'operationalstatus': 'Operational'  # Only get operational stations
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        stations = []
        for station in data:
            # Skip stations with no connection data
            connections = station.get('Connections', [])
            if not connections:
                continue

            power_levels = [conn.get('PowerKW', 0) for conn in connections if conn.get('PowerKW')]
            max_power = max(power_levels) if power_levels else 0

            # Get connector types
            connector_types = []
            for conn in connections:
                conn_type = conn.get('ConnectionType', {}).get('Title')
                if conn_type and conn_type not in connector_types:
                    connector_types.append(conn_type)

            # Only include stations with at least one connector type
            if connector_types:
                stations.append({
                    'source': 'ocm',
                    'name': station.get('AddressInfo', {}).get('Title', 'Unknown Station'),
                    'lat': station.get('AddressInfo', {}).get('Latitude'),
                    'lng': station.get('AddressInfo', {}).get('Longitude'),
                    'address': station.get('AddressInfo', {}).get('AddressLine1', 'Address not available'),
                    'power_kw': max_power,
                    'is_operational': station.get('StatusType', {}).get('IsOperational', True),
                    'connectors': connector_types
                })
        return stations
    except Exception as e:
        print(f"Error fetching OCM charging stations: {e}")
        return []

@app.route('/amenities/<station_id>')
def amenities(station_id):
    # Get station data from the session with better error handling
    try:
        # Debug session data
        print("Session data keys:", list(session.keys()))

        routes = session.get('routes')
        if not routes:
            print("No routes found in session")
            error_msg = "Please search for a route first. The session may have expired."

            # Check if it's an AJAX request
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"error": error_msg}), 404
            return render_template('error.html', error=error_msg), 404

        route_id = request.args.get('route_id', 0, type=int)
        station_index = request.args.get('station_index', 0, type=int)

        print(f"Accessing route {route_id}, station {station_index}")
        print(f"Available routes: {len(routes)}")

        if route_id >= len(routes):
            error_msg = f"Route {route_id} not found. Only {len(routes)} routes available."
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"error": error_msg}), 404
            return render_template('error.html', error=error_msg), 404

        route = routes[route_id]
        if not route.get('charging_stations') or station_index >= len(route['charging_stations']):
            error_msg = f"Station {station_index} not found in route {route_id}."
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"error": error_msg}), 404
            return render_template('error.html', error=error_msg), 404

        station = route['charging_stations'][station_index]
        print(f"Found station: {station['name']}")

        # Get nearby amenities
        amenities = {
            'restaurant': [],
            'cafe': [],
            'restroom': [],
            'tourist_spot': []
        }

        try:
            # Get restaurants and cafes
            places_url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
            for amenity_type in ['restaurant', 'cafe']:
                params = {
                    'location': f"{station['lat']},{station['lng']}",
                    'radius': 1000,  # 1km radius
                    'type': amenity_type,
                    'key': GOOGLE_MAPS_API_KEY
                }

                response = requests.get(places_url, params=params)
                response.raise_for_status()
                data = response.json()

                if data.get('status') == 'OK':
                    for place in data['results'][:5]:  # Limit to top 5 results
                        amenities[amenity_type].append({
                            'name': place['name'],
                            'lat': place['geometry']['location']['lat'],
                            'lng': place['geometry']['location']['lng'],
                            'rating': place.get('rating'),
                            'vicinity': place.get('vicinity'),
                            'distance': calculate_distance(
                                station['lat'], station['lng'],
                                place['geometry']['location']['lat'],
                                place['geometry']['location']['lng']
                            )
                        })

            # Get tourist spots (using tourist_attraction type)
            params = {
                'location': f"{station['lat']},{station['lng']}",
                'radius': 2000,  # 2km radius for tourist spots
                'type': 'tourist_attraction',
                'key': GOOGLE_MAPS_API_KEY
            }

            response = requests.get(places_url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'OK':
                for place in data['results'][:5]:
                    amenities['tourist_spot'].append({
                        'name': place['name'],
                        'lat': place['geometry']['location']['lat'],
                        'lng': place['geometry']['location']['lng'],
                        'rating': place.get('rating'),
                        'vicinity': place.get('vicinity'),
                        'distance': calculate_distance(
                            station['lat'], station['lng'],
                            place['geometry']['location']['lat'],
                            place['geometry']['location']['lng']
                        )
                    })

            # Get restrooms (using convenience_store and shopping_mall as proxy)
            params = {
                'location': f"{station['lat']},{station['lng']}",
                'radius': 500,  # 500m radius for restrooms
                'type': 'shopping_mall|convenience_store',
                'key': GOOGLE_MAPS_API_KEY
            }

            response = requests.get(places_url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'OK':
                for place in data['results'][:3]:
                    amenities['restroom'].append({
                        'name': f"Restroom at {place['name']}",
                        'lat': place['geometry']['location']['lat'],
                        'lng': place['geometry']['location']['lng'],
                        'vicinity': place.get('vicinity'),
                        'distance': calculate_distance(
                            station['lat'], station['lng'],
                            place['geometry']['location']['lat'],
                            place['geometry']['location']['lng']
                        )
                    })

        except Exception as e:
            print(f"Error fetching amenities: {e}")
            # Return empty amenities rather than failing

        # Check if the request wants JSON
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'station': station,
                'amenities': amenities
            })

        # Otherwise return the HTML template
        return render_template('amenities.html',
                             station=station,
                             amenities=amenities,
                             google_maps_api_key=GOOGLE_MAPS_API_KEY)

    except Exception as e:
        print(f"Error accessing station data: {e}")
        error_msg = f"An error occurred: {str(e)}"
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"error": error_msg}), 500
        return render_template('error.html', error=error_msg), 500

def calculate_distance(lat1, lng1, lat2, lng2):
    """Calculate distance between two points in meters"""
    return int(geodesic((lat1, lng1), (lat2, lng2)).meters)

if __name__ == '__main__':
    app.run(debug=True)