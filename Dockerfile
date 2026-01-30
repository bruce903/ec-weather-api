# Environment Canada HRDPS Weather API
# Fetches weather data directly from Environment Canada

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for netCDF
RUN apt-get update && apt-get install -y \
    libhdf5-dev \
    libnetcdf-dev \
    build-essential \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .

# Expose port
EXPOSE 8080

# Run with gunicorn for production
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120", "app:app"]
