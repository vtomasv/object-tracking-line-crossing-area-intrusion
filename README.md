# Object Tracking — Zone Analyzer Web UI

> **Deep learning based object tracking with line crossing and area intrusion detection — Web UI version**

A fully web-based tool built on top of [yas-sim/object-tracking-line-crossing-area-intrusion](https://github.com/yas-sim/object-tracking-line-crossing-area-intrusion).  
Upload any video, draw virtual lines (wires) and polygonal areas directly on the first frame, then let the system process the video and show you the annotated result with crossing counts and intrusion statistics.

---

## Features

| Feature | Description |
|---|---|
| **Video Upload** | Drag-and-drop or browse — supports MP4, AVI, MOV, MKV, H264 |
| **Interactive Zone Editor** | Draw wires (2-point lines) and areas (polygons) directly on the video frame |
| **Object Detection** | Uses Intel OpenVINO `pedestrian-detection-adas-0002` (falls back to OpenCV HOG) |
| **Object Tracking** | Re-identification with cosine distance + Hungarian algorithm |
| **Line Crossing** | Counts crossings per direction for each wire |
| **Area Intrusion** | Counts objects inside each defined area per frame |
| **Live Progress** | Server-Sent Events stream for real-time processing progress |
| **Downloadable Result** | Annotated video available for download |
| **Docker Compose** | One-command deployment |

---

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/) installed
- (Optional) Intel OpenVINO compatible CPU/GPU for accelerated inference

### Run with Docker Compose

```bash
# Clone the repository
git clone https://github.com/vtomasv/object-tracking-line-crossing-area-intrusion.git
cd object-tracking-line-crossing-area-intrusion

# Build and start
docker compose up --build

# Open in browser
open http://localhost:8000
```

The first build downloads Python packages and may take 3–5 minutes.  
Subsequent starts are fast since Docker caches the image.

---

## Usage

### Step 1 — Upload Video
- Drag and drop a video file onto the upload area, or click **Seleccionar archivo**.
- Alternatively, click **Cargar video de muestra** to use the bundled `people-detection.264` demo.

### Step 2 — Define Zones
- The first frame of the video is shown as a canvas background.
- Select **Wire** (line) or **Área** (polygon) from the toolbar.
- **Wire**: click 2 points — the line is saved automatically.
- **Área**: click 3+ points, then press **Enter** or double-click to close the polygon.
- Give each zone a name and color before saving.
- Use the undo button to remove the last point, or trash to clear all zones.

### Step 3 — Process
- Adjust the confidence threshold and inference device.
- Click **Iniciar Procesamiento** — a progress bar updates in real time.

### Step 4 — Results
- Watch the annotated video directly in the browser.
- Download the MP4 result file.
- View per-wire crossing counts (both directions) and per-area intrusion statistics.

---

## Architecture

```
object-tracking-line-crossing-area-intrusion/
├── backend/
│   ├── main.py              # FastAPI application (REST API + background processing)
│   └── requirements.txt
├── frontend/
│   ├── index.html           # Single-page application
│   ├── style.css
│   └── app.js               # Canvas drawing, API calls, progress streaming
├── models/
│   └── intel/               # Pre-trained OpenVINO IR models
│       ├── pedestrian-detection-adas-0002/
│       └── person-reidentification-retail-0277/
├── data/
│   └── people-detection.264 # Sample video
├── uploads/                 # (runtime) uploaded videos
├── results/                 # (runtime) processed videos
├── Dockerfile
├── docker-compose.yml
└── README.md
```

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check + OpenVINO status |
| `POST` | `/api/videos/upload` | Upload a video file |
| `POST` | `/api/videos/sample` | Register the bundled sample video |
| `GET` | `/api/videos/{id}/frame?t=0` | Get a frame at time t (JPEG) |
| `POST` | `/api/process` | Start a processing job |
| `GET` | `/api/jobs/{id}` | Get job status and stats |
| `GET` | `/api/jobs/{id}/stream` | SSE stream for live progress |

---

## Running Locally (without Docker)

```bash
# Install dependencies
pip install -r backend/requirements.txt
pip install ffmpeg-python  # optional, for re-encoding

# Start the server
cd object-tracking-line-crossing-area-intrusion
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Hardware Notes (Apple M3 Max)

- OpenVINO 2025 supports ARM64 (Apple Silicon) via CPU device.
- The HOG fallback uses OpenCV's built-in people detector — no OpenVINO required.
- For best performance, use `device: CPU` with `OMP_NUM_THREADS=8` or higher.
- GPU acceleration via Metal is not yet supported by OpenVINO on macOS.

---

## Credits

- Original project: [yas-sim/object-tracking-line-crossing-area-intrusion](https://github.com/yas-sim/object-tracking-line-crossing-area-intrusion)
- Detection models: [Intel Open Model Zoo](https://github.com/openvinotoolkit/open_model_zoo)
- Inference: [Intel OpenVINO Toolkit](https://docs.openvino.ai/)

## License

Apache 2.0 — see [LICENSE](LICENSE)
