# Environment Canada HRDPS Weather API

Direct weather data from Environment Canada's High Resolution Deterministic Prediction System (HRDPS) for BVLOS drone operations assessment.

## Status: ✅ ALL LAYERS VERIFIED (2026-01-30)

**All 8 BVLOS-critical layers verified working:**
- ✅ `HRDPS.CONTINENTAL_TT` - Air temperature (°C)
- ✅ `HRDPS.CONTINENTAL_WSPD` - Wind speed (m/s)
- ✅ `HRDPS.CONTINENTAL_GUST` - Wind gusts (m/s)
- ✅ `HRDPS.CONTINENTAL_WD` - Wind direction (degrees)
- ✅ `HRDPS.CONTINENTAL_P0` - Pressure (Pa)
- ✅ `HRDPS.CONTINENTAL_PR` - Precipitation (accumulated kg/m²)
- ✅ `HRDPS.CONTINENTAL_HU` - Specific humidity (kg/kg)
- ✅ `HRDPS.CONTINENTAL_TCDC` - Total cloud cover (%)

## Data Source

**Official Source:** Environment and Climate Change Canada - Meteorological Service of Canada  
**Model:** High Resolution Deterministic Prediction System (HRDPS)  
**Resolution:** 2.5 km  
**Forecast Range:** 0-48 hours  
**Update Frequency:** 4x daily (00, 06, 12, 18 UTC)  
**License:** [Open Government Licence - Canada](https://open.canada.ca/en/open-government-licence-canada)  
**Documentation:** https://eccc-msc.github.io/open-data/msc-data/nwp_hrdps/readme_hrdps_en/

## Why Direct Integration?

This microservice fetches data directly from Environment Canada's Web Coverage Service (WCS), providing:

- **Authoritative source** - Same data that feeds GFA charts and aviation forecasts
- **No middleman** - Direct from the government source
- **Free data** - Government open data, no API fees
- **BVLOS credibility** - Can cite "Environment Canada HRDPS" in operational documentation

## Quick Start

### 1. Local Development

```bash
pip install -r requirements.txt
python app.py
```

### 2. Test Endpoints

```bash
# Health check
curl http://localhost:8080/health

# Get weather for a location
curl "http://localhost:8080/weather?lat=46.3&lon=-79.5"

# Get BVLOS go/no-go assessment
curl "http://localhost:8080/bvlos-assessment?lat=46.3&lon=-79.5&max_wind_kts=20&max_gust_kts=25"
```

### 4. Deploy to Production

**Option A: Render.com (Recommended)**
- Create new Web Service
- Connect GitHub repo
- Set environment to Docker
- Deploy

**Option B: Railway**
- Connect GitHub repo
- Railway auto-detects Dockerfile
- Deploy

**Option C: Any Docker host**
```bash
docker build -t ec-weather-api .
docker run -p 8080:8080 ec-weather-api
```

## API Endpoints

### GET /health
Health check endpoint.

### GET /weather
Get comprehensive weather data for a location.

**Parameters:**
- `lat` (required): Latitude in decimal degrees
- `lon` (required): Longitude in decimal degrees

**Response:**
```json
{
  "location": {"lat": 46.3, "lon": -79.5},
  "data_source": "Environment Canada HRDPS",
  "resolution_km": 2.5,
  "temperature_c": -5.2,
  "wind_speed_kts": 12.3,
  "wind_gust_kts": 18.5,
  "wind_direction_deg": 270,
  "precipitation_rate_mmhr": 0.0,
  "cloud_cover_pct": 45,
  "humidity_pct": 78,
  "timestamp": "2025-01-30T18:00:00Z"
}
```

### GET /bvlos-assessment
Get BVLOS go/no-go weather assessment.

**Parameters:**
- `lat` (required): Latitude
- `lon` (required): Longitude
- `max_wind_kts` (optional, default: 20): Max wind speed threshold
- `max_gust_kts` (optional, default: 25): Max gust threshold

**Response:**
```json
{
  "location": {"lat": 46.3, "lon": -79.5},
  "status": "GREEN",
  "recommendation": "GO: Conditions within limits",
  "conditions": {
    "wind_speed_kts": 12.3,
    "wind_gust_kts": 18.5,
    "temperature_c": -5.2,
    "precipitation_mmhr": 0.0
  },
  "issues": [],
  "thresholds": {
    "max_wind_kts": 20,
    "max_gust_kts": 25
  },
  "data_source": "Environment Canada HRDPS (2.5km resolution)",
  "timestamp": "2025-01-30T18:00:00Z"
}
```

### GET /layers
List available HRDPS layers and their IDs.

## Integration with FlightOps Pro

### Supabase Edge Function

Create a Supabase Edge Function to proxy requests:

```typescript
// supabase/functions/bvlos-weather/index.ts
import { serve } from "https://deno.land/std@0.168.0/http/server.ts"

const EC_WEATHER_API = Deno.env.get('EC_WEATHER_API_URL') || 'https://your-service.onrender.com'

serve(async (req) => {
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  }

  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders })
  }

  try {
    const url = new URL(req.url)
    const lat = url.searchParams.get('lat')
    const lon = url.searchParams.get('lon')
    
    if (!lat || !lon) {
      return new Response(
        JSON.stringify({ error: 'Missing lat/lon parameters' }),
        { status: 400, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
      )
    }
    
    const response = await fetch(`${EC_WEATHER_API}/bvlos-assessment?lat=${lat}&lon=${lon}`)
    const data = await response.json()
    
    return new Response(
      JSON.stringify(data),
      { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    )
  } catch (error) {
    return new Response(
      JSON.stringify({ error: 'Weather service unavailable' }),
      { status: 503, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    )
  }
})
```

### React Component Example

```tsx
const BVLOSWeatherAssessment: React.FC<{ lat: number; lon: number }> = ({ lat, lon }) => {
  const [assessment, setAssessment] = useState(null);
  const [loading, setLoading] = useState(false);

  const fetchAssessment = async () => {
    setLoading(true);
    const { data, error } = await supabase.functions.invoke('bvlos-weather', {
      body: { lat, lon }
    });
    setAssessment(data);
    setLoading(false);
  };

  const statusColors = {
    GREEN: 'bg-green-500',
    YELLOW: 'bg-yellow-500',
    RED: 'bg-red-500'
  };

  return (
    <div className="p-4 border rounded">
      <h3 className="font-bold mb-2">BVLOS Weather Assessment</h3>
      <p className="text-xs text-gray-500 mb-2">
        Data: Environment Canada HRDPS (2.5km resolution)
      </p>
      
      {assessment && (
        <div className={`p-3 rounded ${statusColors[assessment.status]}`}>
          <span className="font-bold">{assessment.status}</span>
          <p>{assessment.recommendation}</p>
          
          <div className="mt-2 text-sm">
            <p>Wind: {assessment.conditions.wind_speed_kts} kts</p>
            <p>Gusts: {assessment.conditions.wind_gust_kts} kts</p>
            <p>Temp: {assessment.conditions.temperature_c}°C</p>
          </div>
        </div>
      )}
      
      <button onClick={fetchAssessment} disabled={loading}>
        {loading ? 'Checking...' : 'Check Weather'}
      </button>
    </div>
  );
};
```

## Troubleshooting

### Layer Names Not Working

Environment Canada occasionally changes layer naming conventions. Run `discover_layers.py` to find current working names.

### Timeout Errors

The WCS service can be slow. Default timeout is 30 seconds. Increase if needed.

### Coverage Errors

HRDPS coverage is approximately:
- Latitude: 40°N to 85°N
- Longitude: 145°W to 50°W

Requests outside this area will fail.

## License

This software is provided for Clarion Drone Academy's FlightOps Pro application.

Weather data is provided by Environment and Climate Change Canada under the Open Government Licence - Canada.
