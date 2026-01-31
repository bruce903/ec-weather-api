"""
Environment Canada HRDPS Weather API Microservice
===================================================
Fetches weather forecast data directly from Environment Canada's 
High Resolution Deterministic Prediction System (HRDPS) via WMS GetFeatureInfo.

Data source: Environment Canada MSC GeoMet
Resolution: 2.5 km
Forecast range: 0-48 hours
Update frequency: 4x daily (00, 06, 12, 18 UTC)

For BVLOS drone operations assessment.
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from datetime import datetime, timezone
import logging

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Canada GeoMet WMS endpoint
EC_WMS_BASE = "https://geo.weather.gc.ca/geomet"

# HRDPS layer names - ALL VERIFIED via live API testing on 2026-01-30
HRDPS_LAYERS = {
    "temperature": "HRDPS.CONTINENTAL_TT",
    "wind_speed": "HRDPS.CONTINENTAL_WSPD",
    "wind_gust": "HRDPS.CONTINENTAL_GUST",
    "wind_direction": "HRDPS.CONTINENTAL_WD",
    "pressure": "HRDPS.CONTINENTAL_P0",
    "precip_accum": "HRDPS.CONTINENTAL_PR",
    "specific_humidity": "HRDPS.CONTINENTAL_HU",
    "cloud_cover": "HRDPS.CONTINENTAL_TCDC",
}


def fetch_layer_wms(layer_id: str, lat: float, lon: float) -> dict:
    """
    Fetch a single layer value using WMS GetFeatureInfo.
    This returns JSON directly - no NetCDF parsing needed.
    """
    # Build a small bounding box around the point
    buffer = 0.01  # ~1km
    min_lon = lon - buffer
    max_lon = lon + buffer
    min_lat = lat - buffer
    max_lat = lat + buffer
    
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetFeatureInfo",
        "LAYERS": layer_id,
        "QUERY_LAYERS": layer_id,
        "INFO_FORMAT": "application/json",
        "CRS": "EPSG:4326",
        "BBOX": f"{min_lat},{min_lon},{max_lat},{max_lon}",
        "WIDTH": "3",
        "HEIGHT": "3",
        "I": "1",  # Center pixel
        "J": "1",  # Center pixel
    }
    
    try:
        response = requests.get(EC_WMS_BASE, params=params, timeout=30)
        logger.info(f"Fetching {layer_id}: {response.status_code}")
        
        if response.status_code != 200:
            return {"value": None, "status": "error", "error": f"HTTP {response.status_code}"}
        
        data = response.json()
        
        # Extract value from GeoJSON response
        if "features" in data and len(data["features"]) > 0:
            props = data["features"][0].get("properties", {})
            # The value key varies - try common ones
            value = props.get("value") or props.get("GRAY_INDEX") or props.get("Band1")
            if value is not None:
                return {"value": float(value), "status": "success", "layer": layer_id}
        
        return {"value": None, "status": "no_data", "layer": layer_id}
        
    except requests.exceptions.Timeout:
        return {"value": None, "status": "timeout", "layer": layer_id}
    except Exception as e:
        logger.warning(f"Error fetching {layer_id}: {e}")
        return {"value": None, "status": "error", "error": str(e), "layer": layer_id}


def mps_to_knots(mps: float) -> float:
    """Convert meters per second to knots."""
    return mps * 1.94384


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "Environment Canada HRDPS Weather API",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


@app.route('/weather', methods=['GET'])
def get_weather():
    """
    Get HRDPS weather forecast for a specific location.
    
    Query Parameters:
        lat: Latitude (required, decimal degrees)
        lon: Longitude (required, decimal degrees)
    """
    try:
        lat = float(request.args.get('lat'))
        lon = float(request.args.get('lon'))
    except (TypeError, ValueError):
        return jsonify({"error": "Missing or invalid lat/lon parameters"}), 400
    
    # Validate coordinates for HRDPS coverage
    if not (40 <= lat <= 85 and -145 <= lon <= -50):
        return jsonify({
            "error": "Coordinates outside HRDPS coverage area",
            "coverage": "Approximately 40°N to 85°N, 145°W to 50°W"
        }), 400
    
    logger.info(f"Fetching weather for lat={lat}, lon={lon}")
    
    results = {
        "location": {"lat": lat, "lon": lon},
        "data_source": "Environment Canada HRDPS",
        "resolution_km": 2.5,
        "forecast_hours": 48,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    errors = []
    
    # Fetch temperature
    temp_result = fetch_layer_wms(HRDPS_LAYERS["temperature"], lat, lon)
    if temp_result.get("status") == "success":
        results["temperature_c"] = round(temp_result["value"], 1)
    else:
        errors.append("temperature")
    
    # Fetch wind speed
    wind_result = fetch_layer_wms(HRDPS_LAYERS["wind_speed"], lat, lon)
    if wind_result.get("status") == "success":
        speed_mps = wind_result["value"]
        results["wind_speed_mps"] = round(speed_mps, 1)
        results["wind_speed_kts"] = round(mps_to_knots(speed_mps), 1)
    else:
        errors.append("wind_speed")
    
    # Fetch wind direction
    dir_result = fetch_layer_wms(HRDPS_LAYERS["wind_direction"], lat, lon)
    if dir_result.get("status") == "success":
        results["wind_direction_deg"] = round(dir_result["value"])
    else:
        errors.append("wind_direction")
    
    # Fetch wind gusts
    gust_result = fetch_layer_wms(HRDPS_LAYERS["wind_gust"], lat, lon)
    if gust_result.get("status") == "success":
        gust_mps = gust_result["value"]
        results["wind_gust_mps"] = round(gust_mps, 1)
        results["wind_gust_kts"] = round(mps_to_knots(gust_mps), 1)
    else:
        errors.append("wind_gust")
    
    # Fetch precipitation
    precip_result = fetch_layer_wms(HRDPS_LAYERS["precip_accum"], lat, lon)
    if precip_result.get("status") == "success":
        results["precipitation_mm"] = round(precip_result["value"], 2)
    else:
        errors.append("precipitation")
    
    # Fetch cloud cover
    cloud_result = fetch_layer_wms(HRDPS_LAYERS["cloud_cover"], lat, lon)
    if cloud_result.get("status") == "success":
        results["cloud_cover_pct"] = round(cloud_result["value"])
    else:
        errors.append("cloud_cover")
    
    # Fetch humidity
    humidity_result = fetch_layer_wms(HRDPS_LAYERS["specific_humidity"], lat, lon)
    if humidity_result.get("status") == "success":
        results["specific_humidity_kgkg"] = round(humidity_result["value"], 6)
    else:
        errors.append("humidity")
    
    if errors:
        results["unavailable_data"] = errors
    
    return jsonify(results)


@app.route('/bvlos-assessment', methods=['GET'])
def bvlos_assessment():
    """
    Get BVLOS weather go/no-go assessment for a specific location.
    
    Query Parameters:
        lat: Latitude (required)
        lon: Longitude (required)
        max_wind_kts: Maximum wind speed threshold (default: 20)
        max_gust_kts: Maximum gust threshold (default: 25)
        max_precip_mm: Maximum precipitation threshold (default: 5)
        min_temp_c: Minimum temperature threshold (default: -25)
        max_temp_c: Maximum temperature threshold (default: 40)
    """
    try:
        lat = float(request.args.get('lat'))
        lon = float(request.args.get('lon'))
        max_wind = float(request.args.get('max_wind_kts', 20))
        max_gust = float(request.args.get('max_gust_kts', 25))
        max_precip = float(request.args.get('max_precip_mm', 5))
        min_temp = float(request.args.get('min_temp_c', -25))
        max_temp = float(request.args.get('max_temp_c', 40))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid parameters"}), 400
    
    if not (40 <= lat <= 85 and -145 <= lon <= -50):
        return jsonify({"error": "Coordinates outside HRDPS coverage"}), 400
    
    results = {
        "location": {"lat": lat, "lon": lon},
        "thresholds": {
            "max_wind_kts": max_wind,
            "max_gust_kts": max_gust,
            "max_precip_mm": max_precip,
            "min_temp_c": min_temp,
            "max_temp_c": max_temp
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_source": "Environment Canada HRDPS (2.5km resolution)",
    }
    
    conditions = {}
    issues = []
    
    # Check wind speed
    wind_result = fetch_layer_wms(HRDPS_LAYERS["wind_speed"], lat, lon)
    if wind_result.get("status") == "success":
        speed_kts = mps_to_knots(wind_result["value"])
        conditions["wind_speed_kts"] = round(speed_kts, 1)
        if speed_kts > max_wind:
            issues.append(f"Wind {speed_kts:.1f} kts exceeds {max_wind} kts limit")
    else:
        issues.append("Wind speed data unavailable")
    
    # Check gusts
    gust_result = fetch_layer_wms(HRDPS_LAYERS["wind_gust"], lat, lon)
    if gust_result.get("status") == "success":
        gust_kts = mps_to_knots(gust_result["value"])
        conditions["wind_gust_kts"] = round(gust_kts, 1)
        if gust_kts > max_gust:
            issues.append(f"Gusts {gust_kts:.1f} kts exceeds {max_gust} kts limit")
    else:
        conditions["wind_gust_kts"] = None
    
    # Check temperature
    temp_result = fetch_layer_wms(HRDPS_LAYERS["temperature"], lat, lon)
    if temp_result.get("status") == "success":
        temp_c = temp_result["value"]
        conditions["temperature_c"] = round(temp_c, 1)
        if temp_c < min_temp:
            issues.append(f"Temperature {temp_c:.1f}°C below {min_temp}°C minimum")
        elif temp_c > max_temp:
            issues.append(f"Temperature {temp_c:.1f}°C exceeds {max_temp}°C maximum")
    
    # Check precipitation
    precip_result = fetch_layer_wms(HRDPS_LAYERS["precip_accum"], lat, lon)
    if precip_result.get("status") == "success":
        precip_mm = precip_result["value"]
        conditions["precipitation_mm"] = round(precip_mm, 2)
        if precip_mm > max_precip:
            issues.append(f"Precipitation {precip_mm:.1f} mm exceeds {max_precip} mm limit")
    
    # Determine status
    if any("exceeds" in i or "below" in i for i in issues):
        status = "RED"
        recommendation = "NO-GO: Conditions exceed safe limits"
    elif any("unavailable" in i for i in issues):
        status = "YELLOW"
        recommendation = "CAUTION: Some data unavailable"
    elif not issues:
        status = "GREEN"
        recommendation = "GO: Conditions within limits"
    else:
        status = "YELLOW"
        recommendation = "CAUTION: Minor concerns noted"
    
    results["conditions"] = conditions
    results["issues"] = issues
    results["status"] = status
    results["recommendation"] = recommendation
    
    return jsonify(results)


@app.route('/layers', methods=['GET'])
def list_layers():
    """List available HRDPS layers."""
    return jsonify({
        "layers": HRDPS_LAYERS,
        "note": "Using WMS GetFeatureInfo for data retrieval"
    })


if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
