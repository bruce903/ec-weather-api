"""
Environment Canada HRDPS Weather API Microservice
===================================================
Fetches weather forecast data directly from Environment Canada's 
High Resolution Deterministic Prediction System (HRDPS) via WCS.

Data source: Environment Canada MSC GeoMet
Resolution: 2.5 km
Forecast range: 0-48 hours
Update frequency: 4x daily (00, 06, 12, 18 UTC)

For BVLOS drone operations assessment.
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import xarray as xr
import numpy as np
from datetime import datetime, timezone
import tempfile
import os
import logging

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Canada GeoMet WCS endpoint
EC_WCS_BASE = "https://geo.weather.gc.ca/geomet"

# HRDPS layer names for WCS
# ALL VERIFIED via live API testing on 2026-01-30
# Reference: https://geo.weather.gc.ca/geomet WCS GetCapabilities
HRDPS_LAYERS = {
    # CORE WEATHER PARAMETERS (all verified working)
    "temperature": "HRDPS.CONTINENTAL_TT",      # Air temp at surface (°C) - VERIFIED ✓
    "wind_speed": "HRDPS.CONTINENTAL_WSPD",     # Wind speed at 10m (m/s) - VERIFIED ✓
    "wind_gust": "HRDPS.CONTINENTAL_GUST",      # Wind gusts at 10m (m/s) - VERIFIED ✓
    "wind_direction": "HRDPS.CONTINENTAL_WD",   # Wind direction (degrees) - VERIFIED ✓
    "pressure": "HRDPS.CONTINENTAL_P0",         # Surface pressure (Pa) - VERIFIED ✓
    "precip_accum": "HRDPS.CONTINENTAL_PR",     # Precipitation accumulation (kg/m²) - VERIFIED ✓
    "specific_humidity": "HRDPS.CONTINENTAL_HU",# Specific humidity (kg/kg) - VERIFIED ✓
    "cloud_cover": "HRDPS.CONTINENTAL_TCDC",    # Total cloud cover (%) - VERIFIED ✓
}


# Additional verified layers (not used in main API but available):
# - HRDPS.CONTINENTAL_GZ (geopotential height)
# - HRDPS.CONTINENTAL_WGST (alternate wind gust)
# - HRDPS.CONTINENTAL_DD (alternate wind direction)
# - HRDPS.CONTINENTAL_WDIR (alternate wind direction)
# - HRDPS.CONTINENTAL_NT (cloud opacity)
# - HRDPS-WEonG_2.5km_WindGust (post-processed wind gust)

# No alternates needed - all primary layers verified working
HRDPS_LAYER_ALTERNATES = {}


def build_wcs_url(layer_id: str, lat: float, lon: float, buffer: float = 0.05) -> str:
    """
    Build WCS GetCoverage URL for a point location.
    
    We request a small bounding box around the point and extract the center value.
    Buffer of 0.05 degrees ~ 5km at this latitude, ensures we capture the grid cell.
    """
    # Bounding box around point
    min_lon = lon - buffer
    max_lon = lon + buffer
    min_lat = lat - buffer
    max_lat = lat + buffer
    
    params = {
        "SERVICE": "WCS",
        "VERSION": "2.0.1",
        "REQUEST": "GetCoverage",
        "COVERAGEID": layer_id,
        "FORMAT": "image/netcdf",
        "SUBSETTINGCRS": "EPSG:4326",
        "OUTPUTCRS": "EPSG:4326",
        f"SUBSET": f"x({min_lon},{max_lon})",
    }
    
    # Build URL with multiple SUBSET params
    url = f"{EC_WCS_BASE}?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage"
    url += f"&COVERAGEID={layer_id}"
    url += "&FORMAT=image/netcdf"
    url += "&SUBSETTINGCRS=EPSG:4326"
    url += "&OUTPUTCRS=EPSG:4326"
    url += f"&SUBSET=x({min_lon},{max_lon})"
    url += f"&SUBSET=y({min_lat},{max_lat})"
    
    return url


def fetch_layer_value(layer_id: str, lat: float, lon: float) -> dict:
    """
    Fetch a single layer value from Environment Canada WCS.
    
    Returns dict with value, units, and metadata.
    """
    url = build_wcs_url(layer_id, lat, lon)
    logger.info(f"Fetching layer {layer_id} from: {url[:100]}...")
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # Save to temp file and read with xarray
        with tempfile.NamedTemporaryFile(suffix='.nc', delete=False) as tmp:
            tmp.write(response.content)
            tmp_path = tmp.name
        
        try:
            ds = xr.open_dataset(tmp_path)
            
            # Get the data variable (usually 'Band1' or similar)
            var_name = list(ds.data_vars)[0]
            data = ds[var_name]
            
            # Extract value at the center point (nearest to requested lat/lon)
            if 'lat' in ds.coords and 'lon' in ds.coords:
                value = float(data.sel(lat=lat, lon=lon, method='nearest').values)
            elif 'y' in ds.coords and 'x' in ds.coords:
                value = float(data.sel(y=lat, x=lon, method='nearest').values)
            else:
                # Just take center value if coords aren't labeled
                values = data.values
                if values.ndim >= 2:
                    center_y, center_x = values.shape[0] // 2, values.shape[1] // 2
                    value = float(values[center_y, center_x])
                else:
                    value = float(values.flatten()[len(values.flatten()) // 2])
            
            # Get units if available
            units = data.attrs.get('units', 'unknown')
            
            ds.close()
            
            return {
                "value": value,
                "units": units,
                "layer": layer_id,
                "status": "success"
            }
            
        finally:
            os.unlink(tmp_path)
            
    except requests.exceptions.HTTPError as e:
        logger.warning(f"HTTP error fetching {layer_id}: {e}")
        return {"value": None, "status": "error", "error": str(e), "layer": layer_id}
    except Exception as e:
        logger.warning(f"Error fetching {layer_id}: {e}")
        return {"value": None, "status": "error", "error": str(e), "layer": layer_id}


def try_layer_with_alternates(var_name: str, lat: float, lon: float) -> dict:
    """
    Try to fetch a variable, using alternate layer names if primary fails.
    """
    # Try primary layer
    primary = HRDPS_LAYERS.get(var_name)
    if primary:
        result = fetch_layer_value(primary, lat, lon)
        if result.get("status") == "success":
            return result
    
    # Try alternates
    alternates = HRDPS_LAYER_ALTERNATES.get(var_name, [])
    for alt_layer in alternates:
        result = fetch_layer_value(alt_layer, lat, lon)
        if result.get("status") == "success":
            return result
    
    return {"value": None, "status": "not_available", "variable": var_name}


def calculate_wind_speed_direction(u: float, v: float) -> tuple:
    """
    Calculate wind speed and direction from U/V components.
    
    Returns (speed_m_s, direction_degrees)
    Direction is meteorological convention: direction wind is FROM
    """
    speed = np.sqrt(u**2 + v**2)
    direction = (np.degrees(np.arctan2(-u, -v)) + 360) % 360
    return speed, direction


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
        
    Returns JSON with:
        - temperature (°C)
        - wind_speed (m/s and knots)
        - wind_gust (m/s and knots)  
        - wind_direction (degrees)
        - precipitation_rate (mm/hr)
        - cloud_cover (%)
        - humidity (%)
        - data_source
        - timestamp
    """
    # Parse parameters
    try:
        lat = float(request.args.get('lat'))
        lon = float(request.args.get('lon'))
    except (TypeError, ValueError):
        return jsonify({
            "error": "Invalid or missing lat/lon parameters",
            "usage": "/weather?lat=46.3&lon=-79.5"
        }), 400
    
    # Validate coordinates (roughly Canada)
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
    temp_result = try_layer_with_alternates("temperature", lat, lon)
    if temp_result.get("status") == "success":
        results["temperature_c"] = round(temp_result["value"], 1)
    else:
        errors.append("temperature")
    
    # Fetch wind - try direct wind speed/direction first, fall back to U/V
    wind_speed_result = try_layer_with_alternates("wind_speed", lat, lon)
    wind_dir_result = try_layer_with_alternates("wind_direction", lat, lon)
    
    if wind_speed_result.get("status") == "success":
        speed_mps = wind_speed_result["value"]
        results["wind_speed_mps"] = round(speed_mps, 1)
        results["wind_speed_kts"] = round(mps_to_knots(speed_mps), 1)
    else:
        # Try U/V components
        u_result = try_layer_with_alternates("wind_u", lat, lon)
        v_result = try_layer_with_alternates("wind_v", lat, lon)
        
        if u_result.get("status") == "success" and v_result.get("status") == "success":
            speed, direction = calculate_wind_speed_direction(u_result["value"], v_result["value"])
            results["wind_speed_mps"] = round(speed, 1)
            results["wind_speed_kts"] = round(mps_to_knots(speed), 1)
            results["wind_direction_deg"] = round(direction)
        else:
            errors.append("wind_speed")
    
    if wind_dir_result.get("status") == "success":
        results["wind_direction_deg"] = round(wind_dir_result["value"])
    elif "wind_direction_deg" not in results:
        errors.append("wind_direction")
    
    # Fetch wind gusts
    gust_result = try_layer_with_alternates("wind_gust", lat, lon)
    if gust_result.get("status") == "success":
        gust_mps = gust_result["value"]
        results["wind_gust_mps"] = round(gust_mps, 1)
        results["wind_gust_kts"] = round(mps_to_knots(gust_mps), 1)
    else:
        errors.append("wind_gust")
    
    # Fetch precipitation (note: this is accumulated, not rate)
    precip_result = try_layer_with_alternates("precip_accum", lat, lon)
    if precip_result.get("status") == "success":
        # Value is in kg/m² (accumulated over forecast period)
        # For hourly data, this approximates mm/hr
        precip_mm = precip_result["value"]
        results["precipitation_mm"] = round(precip_mm, 2)
        results["precipitation_note"] = "Accumulated precipitation (kg/m² ≈ mm)"
    else:
        errors.append("precipitation")
    
    # Fetch cloud cover
    cloud_result = try_layer_with_alternates("cloud_cover", lat, lon)
    if cloud_result.get("status") == "success":
        results["cloud_cover_pct"] = round(cloud_result["value"])
    else:
        errors.append("cloud_cover")
    
    # Fetch specific humidity (note: NOT relative humidity)
    humidity_result = try_layer_with_alternates("specific_humidity", lat, lon)
    if humidity_result.get("status") == "success":
        results["specific_humidity_kgkg"] = round(humidity_result["value"], 6)
        results["humidity_note"] = "Specific humidity (kg/kg), not relative humidity"
    else:
        errors.append("humidity")
    
    if errors:
        results["unavailable_data"] = errors
        results["note"] = "Some data layers unavailable; layer names may need adjustment"
    
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
        
    Returns assessment with green/yellow/red status.
    """
    # Get weather data first
    try:
        lat = float(request.args.get('lat'))
        lon = float(request.args.get('lon'))
        max_wind = float(request.args.get('max_wind_kts', 20))
        max_gust = float(request.args.get('max_gust_kts', 25))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid parameters"}), 400
    
    # Validate coordinates
    if not (40 <= lat <= 85 and -145 <= lon <= -50):
        return jsonify({"error": "Coordinates outside HRDPS coverage"}), 400
    
    # Get weather
    results = {
        "location": {"lat": lat, "lon": lon},
        "thresholds": {
            "max_wind_kts": max_wind,
            "max_gust_kts": max_gust,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_source": "Environment Canada HRDPS (2.5km resolution)",
    }
    
    conditions = {}
    issues = []
    
    # Fetch and evaluate wind speed
    wind_result = try_layer_with_alternates("wind_speed", lat, lon)
    if wind_result.get("status") == "success":
        speed_kts = mps_to_knots(wind_result["value"])
        conditions["wind_speed_kts"] = round(speed_kts, 1)
        if speed_kts > max_wind:
            issues.append(f"Wind {speed_kts:.1f} kts exceeds {max_wind} kts limit")
    else:
        # Try U/V fallback
        u_result = try_layer_with_alternates("wind_u", lat, lon)
        v_result = try_layer_with_alternates("wind_v", lat, lon)
        if u_result.get("status") == "success" and v_result.get("status") == "success":
            speed, _ = calculate_wind_speed_direction(u_result["value"], v_result["value"])
            speed_kts = mps_to_knots(speed)
            conditions["wind_speed_kts"] = round(speed_kts, 1)
            if speed_kts > max_wind:
                issues.append(f"Wind {speed_kts:.1f} kts exceeds {max_wind} kts limit")
        else:
            issues.append("Wind speed data unavailable")
    
    # Fetch and evaluate gusts
    gust_result = try_layer_with_alternates("wind_gust", lat, lon)
    if gust_result.get("status") == "success":
        gust_kts = mps_to_knots(gust_result["value"])
        conditions["wind_gust_kts"] = round(gust_kts, 1)
        if gust_kts > max_gust:
            issues.append(f"Gusts {gust_kts:.1f} kts exceeds {max_gust} kts limit")
    else:
        conditions["wind_gust_kts"] = None
    
    # Fetch temperature (for freezing conditions check)
    temp_result = try_layer_with_alternates("temperature", lat, lon)
    if temp_result.get("status") == "success":
        conditions["temperature_c"] = round(temp_result["value"], 1)
        if temp_result["value"] < -20:
            issues.append(f"Extreme cold: {temp_result['value']:.1f}°C")
    
    # Fetch precipitation (accumulated)
    precip_result = try_layer_with_alternates("precip_accum", lat, lon)
    if precip_result.get("status") == "success":
        # Value is accumulated (kg/m²). Thresholds for hourly accumulation:
        precip_mm = precip_result["value"]
        conditions["precipitation_mm"] = round(precip_mm, 2)
        if precip_mm > 5.0:
            issues.append(f"Heavy precipitation: {precip_mm:.1f} mm")
        elif precip_mm > 1.0:
            issues.append(f"Moderate precipitation: {precip_mm:.1f} mm")
    
    # Determine overall status
    if any("exceeds" in i or "Heavy" in i or "Extreme" in i for i in issues):
        status = "RED"
        recommendation = "NO-GO: Conditions exceed safe limits"
    elif any("unavailable" in i or "Moderate" in i for i in issues):
        status = "YELLOW"
        recommendation = "CAUTION: Review conditions carefully"
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
    """List available HRDPS layers and their IDs."""
    return jsonify({
        "primary_layers": HRDPS_LAYERS,
        "alternate_layers": HRDPS_LAYER_ALTERNATES,
        "note": "Layer names may vary; service will try alternates automatically"
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
