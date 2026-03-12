# ── Base image ────────────────────────────────────────────────────────────────
# python:3.11-slim uses Debian Trixie on ARM64 (Apple M3/M4).
# libgl1-mesa-glx was removed in Trixie; use libgl1 instead (available on all arches).
FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/   /app/backend/
COPY frontend/  /app/frontend/
COPY models/    /app/models/
COPY data/      /app/data/

# Create writable directories
RUN mkdir -p /app/uploads /app/results /app/cache

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"

# Start server
CMD ["python3", "-m", "uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--timeout-keep-alive", "120"]
