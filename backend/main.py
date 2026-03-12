"""
Object Tracking Web API - Backend FastAPI
Provides REST API for video upload, zone configuration, processing and results streaming.
"""

import os
import sys
import uuid
import json
import time
import shutil
import asyncio
import threading
import random
from pathlib import Path
from typing import Optional, List, Dict, Any

import cv2
import numpy as np
import yaml
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"
MODEL_DIR  = BASE_DIR / "models" / "intel"
DATA_DIR   = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "frontend"

for d in [UPLOAD_DIR, RESULT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── FastAPI App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Object Tracking Web API",
    description="API for video-based object tracking with line crossing and area intrusion detection",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── In-memory job store ───────────────────────────────────────────────────────
jobs: Dict[str, Dict] = {}

# ─── Pydantic models ──────────────────────────────────────────────────────────
class Point(BaseModel):
    x: float
    y: float

class Zone(BaseModel):
    id: str
    type: str          # "wire" | "area"
    name: str
    points: List[Point]
    color: Optional[str] = "#00ff00"

class ProcessRequest(BaseModel):
    video_id: str
    zones: List[Zone]
    confidence: float = 0.75
    device: str = "CPU"

# ─── Line / Area helpers (ported from original) ────────────────────────────────

def _line(p1, p2):
    A = p1[1] - p2[1]
    B = p2[0] - p1[0]
    C = p1[0]*p2[1] - p2[0]*p1[1]
    return A, B, -C

def check_intersect(p1, p2, p3, p4):
    tc1 = (p1[0]-p2[0])*(p3[1]-p1[1]) + (p1[1]-p2[1])*(p1[0]-p3[0])
    tc2 = (p1[0]-p2[0])*(p4[1]-p1[1]) + (p1[1]-p2[1])*(p1[0]-p4[0])
    td1 = (p3[0]-p4[0])*(p1[1]-p3[1]) + (p3[1]-p4[1])*(p3[0]-p1[0])
    td2 = (p3[0]-p4[0])*(p2[1]-p3[1]) + (p3[1]-p4[1])*(p3[0]-p2[0])
    return tc1*tc2 < 0 and td1*td2 < 0

def calc_vector_angle(p1, p2, p3, p4):
    u = np.array([p2[0]-p1[0], p2[1]-p1[1]], dtype=float)
    v = np.array([p4[0]-p3[0], p4[1]-p3[1]], dtype=float)
    n = np.linalg.norm(u) * np.linalg.norm(v)
    if n == 0:
        return 0
    c = np.clip(np.dot(u, v) / n, -1.0, 1.0)
    a = np.degrees(np.arccos(c))
    return a if u[0]*v[1] - u[1]*v[0] < 0 else 360 - a

def point_in_polygon(polygon, test_point):
    if len(polygon) < 3:
        return False
    prev = polygon[-1]
    count = 0
    for pt in polygon:
        if test_point[1] >= min(prev[1], pt[1]) and test_point[1] <= max(prev[1], pt[1]):
            if pt[1] != prev[1]:
                grad = (pt[0]-prev[0]) / (pt[1]-prev[1])
                lx = prev[0] + (test_point[1]-prev[1]) * grad
                if lx < test_point[0]:
                    count += 1
        prev = pt
    return count % 2 == 1

# ─── OpenVINO / fallback loaders ──────────────────────────────────────────────

def try_load_openvino():
    try:
        import openvino as ov
        return ov
    except ImportError:
        return None

def try_load_munkres():
    try:
        from munkres import Munkres
        return Munkres
    except ImportError:
        return None

def try_load_scipy():
    try:
        from scipy.spatial import distance
        return distance
    except ImportError:
        return None

# ─── Processing engine ────────────────────────────────────────────────────────

NUM_COLORS = 200
TRACK_COLORS = [[random.randint(64,255), random.randint(64,255), random.randint(64,255)]
                for _ in range(NUM_COLORS)]

class TrackedObject:
    def __init__(self, pos, feature, obj_id=-1):
        self.pos        = pos
        self.feature    = feature
        self.id         = obj_id
        self.trajectory = []
        self.time       = time.monotonic()

class ObjectTracker:
    def __init__(self, timeout=10, similarity_threshold=0.4):
        self.next_id   = 0
        self.timeout   = timeout
        self.threshold = similarity_threshold
        self.db: List[TrackedObject] = []

    def evict(self):
        now = time.monotonic()
        self.db = [o for o in self.db if o.time + self.timeout >= now]

    def track(self, objects: List[TrackedObject], distance_fn):
        if not objects:
            return
        if self.db:
            matrix = [[distance_fn(objects[j].feature, self.db[i].feature)
                       for j in range(len(objects))]
                      for i in range(len(self.db))]
            Munkres = try_load_munkres()
            if Munkres:
                combos = Munkres().compute(matrix)
                for db_i, obj_i in combos:
                    if matrix[db_i][obj_i] < self.threshold:
                        o = objects[obj_i]
                        d = self.db[db_i]
                        o.id      = d.id
                        d.feature = o.feature
                        d.time    = time.monotonic()
                        cx = (o.pos[0]+o.pos[2])//2
                        cy = (o.pos[1]+o.pos[3])//2
                        d.trajectory.append([cx, cy])
                        o.trajectory = d.trajectory
        for o in objects:
            if o.id == -1:
                o.id = self.next_id
                self.next_id += 1
                cx = (o.pos[0]+o.pos[2])//2
                cy = (o.pos[1]+o.pos[3])//2
                o.trajectory = [[cx, cy]]
                self.db.append(o)
                self.db[-1].trajectory = o.trajectory
                self.db[-1].time = time.monotonic()

    def draw_trajectories(self, img, objects):
        for o in objects:
            if len(o.trajectory) > 1:
                col = TRACK_COLORS[o.id % NUM_COLORS]
                cv2.polylines(img, np.array([o.trajectory], np.int32), False, col, 2)


def process_video_job(job_id: str, video_path: str, zones: List[Zone],
                      confidence: float, device: str):
    """Background worker: runs the full detection pipeline."""
    job = jobs[job_id]
    job["status"]   = "processing"
    job["progress"] = 0.0

    try:
        ov       = try_load_openvino()
        distance = try_load_scipy()

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps_in       = cap.get(cv2.CAP_PROP_FPS) or 25
        width        = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height       = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        result_path = str(RESULT_DIR / f"{job_id}.mp4")
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(result_path, fourcc, fps_in, (width, height))

        # ── Build zone structures ──────────────────────────────────────────
        wire_zones = []
        area_zones = []
        for z in zones:
            pts = [(int(p.x * width), int(p.y * height)) for p in z.points]
            if z.type == "wire":
                wire_zones.append({
                    "id": z.id, "name": z.name, "color": z.color,
                    "points": pts, "count1": 0, "count2": 0
                })
            elif z.type == "area":
                area_zones.append({
                    "id": z.id, "name": z.name, "color": z.color,
                    "contour": np.array(pts, dtype=np.int32),
                    "count": 0
                })

        # ── Load OpenVINO models if available ─────────────────────────────
        use_ov = ov is not None
        ireq_det = ireq_reid = None
        model_det_shape = model_reid_shape = None

        if use_ov:
            try:
                det_path  = str(MODEL_DIR / "pedestrian-detection-adas-0002" / "FP16" / "pedestrian-detection-adas-0002.xml")
                reid_path = str(MODEL_DIR / "person-reidentification-retail-0277" / "FP16" / "person-reidentification-retail-0277.xml")
                ov_config = {"CACHE_DIR": str(BASE_DIR / "cache")}
                m_det  = ov.Core().read_model(det_path)
                model_det_shape = m_det.input().get_shape()
                compiled_det  = ov.compile_model(m_det,  device, ov_config)
                m_reid = ov.Core().read_model(reid_path)
                model_reid_shape = m_reid.input().get_shape()
                compiled_reid = ov.compile_model(m_reid, device, ov_config)
                ireq_det  = compiled_det.create_infer_request()
                ireq_reid = compiled_reid.create_infer_request()
                print(f"[INFO] OpenVINO models loaded on {device}")
            except Exception as e:
                print(f"[WARN] OpenVINO models not loaded: {e}. Falling back to HOG.")
                use_ov = False

        # ── Fallback: HOG person detector ─────────────────────────────────
        hog = None
        if not use_ov:
            hog = cv2.HOGDescriptor()
            hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

        tracker = ObjectTracker(timeout=10, similarity_threshold=0.4)

        # ── Stats ─────────────────────────────────────────────────────────
        stats = {
            "total_frames":     total_frames,
            "processed_frames": 0,
            "total_detections": 0,
            "unique_objects":   0,
            "wire_events":      {wz["name"]: {"count1": 0, "count2": 0} for wz in wire_zones},
            "area_events":      {az["name"]: {"max_count": 0, "total_intrusions": 0} for az in area_zones},
            "fps_avg":          0.0,
        }

        frame_idx = 0
        t_start   = time.perf_counter()

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            objects: List[TrackedObject] = []

            # ── Detect ────────────────────────────────────────────────────
            if use_ov and ireq_det is not None:
                blob = cv2.resize(frame, (int(model_det_shape[3]), int(model_det_shape[2])))
                blob = blob.transpose((2,0,1)).reshape(list(model_det_shape))
                ireq_det.infer({0: blob})
                dets = ireq_det.get_tensor("detection_out").data.reshape((200, 7))
                for det in dets:
                    if det[2] > confidence:
                        xmin = max(0, int(det[3] * width))
                        ymin = max(0, int(det[4] * height))
                        xmax = min(width,  int(det[5] * width))
                        ymax = min(height, int(det[6] * height))
                        crop = frame[ymin:ymax, xmin:xmax]
                        if crop.size == 0:
                            continue
                        rb = cv2.resize(crop, (int(model_reid_shape[3]), int(model_reid_shape[2])))
                        rb = rb.transpose((2,0,1)).reshape(model_reid_shape)
                        ireq_reid.infer({0: rb})
                        feat = ireq_reid.get_output_tensor(0).data.ravel()
                        objects.append(TrackedObject([xmin,ymin,xmax,ymax], feat))
            else:
                scale = 0.5
                small = cv2.resize(frame, (int(width*scale), int(height*scale)))
                rects, _ = hog.detectMultiScale(small, winStride=(8,8), padding=(4,4), scale=1.05)
                for (x,y,w,h) in rects:
                    xmin = int(x/scale); ymin = int(y/scale)
                    xmax = int((x+w)/scale); ymax = int((y+h)/scale)
                    feat = np.random.rand(256).astype(np.float32)
                    objects.append(TrackedObject([xmin,ymin,xmax,ymax], feat))

            # ── Track ─────────────────────────────────────────────────────
            if distance:
                dist_fn = distance.cosine
            else:
                dist_fn = lambda a, b: float(1 - np.dot(a,b)/(np.linalg.norm(a)*np.linalg.norm(b)+1e-9))

            tracker.track(objects, dist_fn)
            tracker.evict()

            outimg = frame.copy()
            tracker.draw_trajectories(outimg, objects)

            # ── Wire crossing ─────────────────────────────────────────────
            for wz in wire_zones:
                p0, p1 = wz["points"][0], wz["points"][1]
                for obj in objects:
                    traj = obj.trajectory
                    if len(traj) > 1:
                        tp0 = tuple(traj[-2]); tp1 = tuple(traj[-1])
                        if check_intersect(tp0, tp1, p0, p1):
                            angle = calc_vector_angle(tp0, tp1, p0, p1)
                            if angle < 180:
                                wz["count1"] += 1
                                stats["wire_events"][wz["name"]]["count1"] += 1
                            else:
                                wz["count2"] += 1
                                stats["wire_events"][wz["name"]]["count2"] += 1
                col = _hex_to_bgr(wz["color"])
                cv2.line(outimg, p0, p1, col, 3)
                cv2.putText(outimg, f"{wz['name']} >{wz['count1']} <{wz['count2']}",
                            (p0[0], max(0, p0[1]-8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)

            # ── Area intrusion ────────────────────────────────────────────
            for az in area_zones:
                az["count"] = 0
                for obj in objects:
                    cx = (obj.pos[0]+obj.pos[2])//2
                    cy = (obj.pos[1]+obj.pos[3])//2
                    if point_in_polygon(az["contour"].tolist(), (cx, cy)):
                        az["count"] += 1
                        stats["area_events"][az["name"]]["total_intrusions"] += 1
                if az["count"] > stats["area_events"][az["name"]]["max_count"]:
                    stats["area_events"][az["name"]]["max_count"] = az["count"]
                col = _hex_to_bgr(az["color"]) if az["count"] == 0 else (0, 0, 255)
                cv2.polylines(outimg, [az["contour"]], True, col, 3)
                cx2, cy2 = np.mean(az["contour"], axis=0).astype(int)
                cv2.putText(outimg, f"{az['name']}:{az['count']}",
                            (int(cx2), int(cy2)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)

            # ── Bounding boxes ────────────────────────────────────────────
            for obj in objects:
                oid = obj.id
                col = TRACK_COLORS[oid % NUM_COLORS]
                xmin, ymin, xmax, ymax = obj.pos
                cv2.rectangle(outimg, (xmin,ymin), (xmax,ymax), col, 2)
                cv2.putText(outimg, f"ID={oid}", (xmin, max(0,ymin-6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)

            # ── FPS overlay ───────────────────────────────────────────────
            elapsed = time.perf_counter() - t_start
            cur_fps = (frame_idx+1) / elapsed if elapsed > 0 else 0
            cv2.putText(outimg, f"FPS:{cur_fps:.1f}  Frame:{frame_idx+1}/{total_frames}",
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,0), 4)
            cv2.putText(outimg, f"FPS:{cur_fps:.1f}  Frame:{frame_idx+1}/{total_frames}",
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)

            writer.write(outimg)
            frame_idx += 1
            stats["processed_frames"] = frame_idx
            stats["total_detections"] += len(objects)
            stats["unique_objects"]    = tracker.next_id
            stats["fps_avg"]           = cur_fps

            job["progress"] = round(frame_idx / max(total_frames, 1) * 100, 1)
            job["stats"]    = stats

        cap.release()
        writer.release()

        # Re-encode with h264 for browser compatibility
        h264_path = str(RESULT_DIR / f"{job_id}_h264.mp4")
        ret = os.system(f'ffmpeg -y -i "{result_path}" -vcodec libx264 -acodec aac "{h264_path}" 2>/dev/null')
        if ret == 0 and os.path.exists(h264_path) and os.path.getsize(h264_path) > 0:
            os.replace(h264_path, result_path)

        job["status"]        = "done"
        job["progress"]      = 100.0
        job["result_video"]  = f"/results/{job_id}.mp4"
        job["stats"]         = stats

    except Exception as e:
        import traceback
        job["status"]  = "error"
        job["message"] = str(e)
        job["trace"]   = traceback.format_exc()
        print(f"[ERROR] Job {job_id}: {e}\n{job['trace']}")


def _hex_to_bgr(hex_color: str):
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (b, g, r)


# ─── API Endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    ov = try_load_openvino()
    return {"status": "ok", "openvino": ov is not None, "version": "1.0.0"}


@app.post("/api/videos/upload")
async def upload_video(file: UploadFile = File(...)):
    """Upload a video file and return its metadata."""
    allowed = {".mp4", ".avi", ".mov", ".mkv", ".264", ".h264", ".webm"}
    suffix  = Path(file.filename).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(400, f"Unsupported format: {suffix}")

    video_id  = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{video_id}{suffix}"

    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return _video_meta(video_id, save_path, file.filename)


@app.post("/api/videos/sample")
async def load_sample_video():
    """Register the bundled sample video and return its metadata."""
    sample = DATA_DIR / "people-detection.264"
    if not sample.exists():
        raise HTTPException(404, "Sample video not found")

    video_id  = "sample-" + str(uuid.uuid4())[:8]
    save_path = UPLOAD_DIR / f"{video_id}.264"
    shutil.copy(str(sample), str(save_path))
    return _video_meta(video_id, save_path, "people-detection.264")


def _video_meta(video_id: str, path: Path, original_name: str) -> dict:
    cap    = cv2.VideoCapture(str(path))
    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps    = cap.get(cv2.CAP_PROP_FPS)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur    = frames / fps if fps > 0 else 0
    ret, thumb_frame = cap.read()
    cap.release()

    thumb_path = UPLOAD_DIR / f"{video_id}_thumb.jpg"
    if ret:
        cv2.imwrite(str(thumb_path), cv2.resize(thumb_frame, (320, 180)))

    return {
        "video_id":  video_id,
        "filename":  original_name,
        "path":      str(path),
        "width":     width,
        "height":    height,
        "fps":       round(fps, 2),
        "frames":    frames,
        "duration":  round(dur, 2),
        "thumbnail": f"/uploads/{video_id}_thumb.jpg",
    }


@app.get("/api/videos/{video_id}/frame")
def get_frame(video_id: str, t: float = 0.0):
    """Return a single frame at time t (seconds) as JPEG."""
    files = [f for f in UPLOAD_DIR.glob(f"{video_id}.*")
             if not f.name.endswith("_thumb.jpg")]
    if not files:
        raise HTTPException(404, "Video not found")
    cap = cv2.VideoCapture(str(files[0]))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        raise HTTPException(404, "Frame not found")
    _, buf = cv2.imencode(".jpg", frame)
    return StreamingResponse(iter([buf.tobytes()]), media_type="image/jpeg")


@app.post("/api/process")
async def start_processing(req: ProcessRequest, background_tasks: BackgroundTasks):
    """Start a background processing job."""
    files = [f for f in UPLOAD_DIR.glob(f"{req.video_id}.*")
             if not f.name.endswith("_thumb.jpg")]
    if not files:
        raise HTTPException(404, "Video not found")

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "job_id":       job_id,
        "status":       "queued",
        "progress":     0.0,
        "message":      "",
        "result_video": None,
        "stats":        None,
    }

    background_tasks.add_task(
        process_video_job, job_id, str(files[0]),
        req.zones, req.confidence, req.device,
    )
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    return jobs[job_id]


@app.get("/api/jobs/{job_id}/stream")
def stream_job(job_id: str):
    """Server-Sent Events stream for job progress."""
    def event_generator():
        while True:
            if job_id not in jobs:
                yield f"data: {json.dumps({'error': 'not found'})}\n\n"
                break
            job = jobs[job_id]
            yield f"data: {json.dumps(job)}\n\n"
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.5)
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/videos")
def list_videos():
    result = []
    for f in UPLOAD_DIR.iterdir():
        if f.suffix.lower() in {".mp4",".avi",".mov",".mkv",".264",".h264",".webm"}:
            vid_id = f.stem
            result.append({
                "video_id":  vid_id,
                "filename":  f.name,
                "size":      f.stat().st_size,
                "thumbnail": f"/uploads/{vid_id}_thumb.jpg",
            })
    return result


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: str):
    for f in UPLOAD_DIR.glob(f"{video_id}*"):
        f.unlink(missing_ok=True)
    return {"deleted": video_id}


# ─── Static file serving ───────────────────────────────────────────────────────
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/results", StaticFiles(directory=str(RESULT_DIR)), name="results")
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
