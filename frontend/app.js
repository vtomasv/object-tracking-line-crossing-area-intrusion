/**
 * Object Tracking Zone Analyzer - Frontend Application
 * Handles video upload, interactive zone drawing, processing and results display.
 */

'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  currentStep:   1,
  videoId:       null,
  videoMeta:     null,
  zones:         [],          // finalized zones
  currentPoints: [],          // points being drawn
  currentTool:   'wire',      // 'wire' | 'area'
  jobId:         null,
  jobStats:      null,
  resultVideoUrl: null,
};

// ── Canvas ────────────────────────────────────────────────────────────────────
let canvas, ctx, bgImage;

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  canvas = document.getElementById('zone-canvas');
  ctx    = canvas.getContext('2d');

  setupDropZone();
  setupFileInput();
  setupCanvasEvents();
  setupToolRadios();
  checkHealth();
});

// ── Health check ──────────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const r = await fetch('/api/health');
    const d = await r.json();
    const badge = document.getElementById('ov-badge');
    if (d.openvino) {
      badge.textContent = 'OpenVINO: activo';
      badge.className   = 'badge bg-success';
    } else {
      badge.textContent = 'OpenVINO: no disponible (modo HOG)';
      badge.className   = 'badge bg-warning text-dark';
    }
  } catch {
    document.getElementById('ov-badge').textContent = 'API: desconectada';
    document.getElementById('ov-badge').className   = 'badge bg-danger';
  }
}

// ── Step navigation ───────────────────────────────────────────────────────────
function goToStep(n) {
  document.querySelectorAll('.step-panel').forEach(p => p.classList.add('d-none'));
  document.querySelectorAll('.step-btn').forEach(b => {
    b.classList.remove('active');
    if (parseInt(b.dataset.step) < n) b.classList.add('done');
    else b.classList.remove('done');
  });
  document.getElementById(`step-${n}`).classList.remove('d-none');
  document.querySelector(`.step-btn[data-step="${n}"]`).classList.add('active');
  state.currentStep = n;

  if (n === 2) initCanvas();
  if (n === 3) prepareProcessStep();
}

document.querySelectorAll('.step-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const s = parseInt(btn.dataset.step);
    if (s === 1) goToStep(1);
    if (s === 2 && state.videoId) goToStep(2);
    if (s === 3 && state.videoId) goToStep(3);
    if (s === 4 && state.jobStats) goToStep(4);
  });
});

// ── Drop zone & file input ────────────────────────────────────────────────────
function setupDropZone() {
  const dz = document.getElementById('drop-zone');
  dz.addEventListener('dragover',  e => { e.preventDefault(); dz.classList.add('drag-over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
  dz.addEventListener('drop', e => {
    e.preventDefault();
    dz.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) uploadVideo(file);
  });
  dz.addEventListener('click', () => document.getElementById('file-input').click());
}

function setupFileInput() {
  document.getElementById('file-input').addEventListener('change', e => {
    if (e.target.files[0]) uploadVideo(e.target.files[0]);
  });
}

async function uploadVideo(file) {
  const prog = document.getElementById('upload-progress');
  const bar  = document.getElementById('upload-bar');
  const pct  = document.getElementById('upload-pct');
  document.getElementById('upload-filename').textContent = file.name;
  prog.classList.remove('d-none');
  document.getElementById('video-info-card').classList.add('d-none');

  const form = new FormData();
  form.append('file', file);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/videos/upload');
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) {
        const p = Math.round(e.loaded / e.total * 100);
        bar.style.width = p + '%';
        pct.textContent = p + '%';
      }
    };
    xhr.onload = () => {
      if (xhr.status === 200) {
        const meta = JSON.parse(xhr.responseText);
        state.videoId   = meta.video_id;
        state.videoMeta = meta;
        showVideoInfo(meta);
        resolve(meta);
      } else {
        showAlert('Error al subir el video: ' + xhr.responseText, 'danger');
        reject();
      }
    };
    xhr.onerror = () => { showAlert('Error de red al subir el video.', 'danger'); reject(); };
    xhr.send(form);
  });
}

function showVideoInfo(meta) {
  document.getElementById('video-thumb').src = meta.thumbnail + '?t=' + Date.now();
  document.getElementById('video-name').textContent   = meta.filename;
  document.getElementById('video-res').textContent    = `${meta.width}×${meta.height}`;
  document.getElementById('video-fps').textContent    = meta.fps;
  document.getElementById('video-dur').textContent    = meta.duration;
  document.getElementById('video-frames').textContent = meta.frames;
  document.getElementById('video-info-card').classList.remove('d-none');
}

async function loadSampleVideo() {
  showAlert('Cargando video de muestra…', 'info');
  try {
    const r = await fetch('/api/videos/sample', { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    const meta = await r.json();
    state.videoId   = meta.video_id;
    state.videoMeta = meta;
    showVideoInfo(meta);
    document.getElementById('upload-progress').classList.add('d-none');
  } catch (e) {
    showAlert('No se pudo cargar el video de muestra: ' + e.message, 'warning');
  }
}

// ── Canvas / Zone drawing ─────────────────────────────────────────────────────
function initCanvas() {
  if (!state.videoMeta) return;
  const { width, height } = state.videoMeta;

  // Load first frame as background
  const img = new Image();
  img.onload = () => {
    bgImage = img;
    resizeCanvas();
    redraw();
  };
  img.onerror = () => {
    bgImage = null;
    resizeCanvas();
    redraw();
  };
  img.src = `/api/videos/${state.videoId}/frame?t=0`;

  window.addEventListener('resize', () => { resizeCanvas(); redraw(); });
}

function resizeCanvas() {
  if (!state.videoMeta) return;
  const container = canvas.parentElement;
  const maxW = container.clientWidth;
  const ratio = state.videoMeta.height / state.videoMeta.width;
  canvas.width  = maxW;
  canvas.height = Math.min(maxW * ratio, 480);
}

function toCanvasCoords(x, y) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (x - rect.left) * (canvas.width  / rect.width),
    y: (y - rect.top)  * (canvas.height / rect.height),
  };
}

function toNormCoords(cx, cy) {
  return { x: cx / canvas.width, y: cy / canvas.height };
}

function setupCanvasEvents() {
  canvas.addEventListener('click',     onCanvasClick);
  canvas.addEventListener('dblclick',  onCanvasDblClick);
  canvas.addEventListener('mousemove', onCanvasMouseMove);
  document.addEventListener('keydown', e => {
    if (e.key === 'Enter') finishCurrentZone();
    if (e.key === 'Escape') { state.currentPoints = []; redraw(); }
  });
}

let mousePos = null;
function onCanvasMouseMove(e) {
  mousePos = toCanvasCoords(e.clientX, e.clientY);
  redraw();
}

function onCanvasClick(e) {
  const pt = toCanvasCoords(e.clientX, e.clientY);
  state.currentPoints.push(pt);

  // Wire: auto-finish after 2 points
  if (state.currentTool === 'wire' && state.currentPoints.length === 2) {
    finishCurrentZone();
    return;
  }
  redraw();
}

function onCanvasDblClick(e) {
  if (state.currentTool === 'area' && state.currentPoints.length >= 3) {
    // Remove the extra point added by the second click
    state.currentPoints.pop();
    finishCurrentZone();
  }
}

function setupToolRadios() {
  document.querySelectorAll('input[name="tool"]').forEach(r => {
    r.addEventListener('change', e => {
      state.currentTool   = e.target.value;
      state.currentPoints = [];
      redraw();
    });
  });
}

function redraw() {
  if (!canvas) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Background
  if (bgImage) {
    ctx.drawImage(bgImage, 0, 0, canvas.width, canvas.height);
    ctx.fillStyle = 'rgba(0,0,0,0.35)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  } else {
    ctx.fillStyle = '#111';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  // Draw finalized zones
  state.zones.forEach(z => drawZone(z, false));

  // Draw current in-progress zone
  if (state.currentPoints.length > 0) {
    const pts = state.currentPoints;
    const col = document.getElementById('zone-color-input').value;

    ctx.strokeStyle = col;
    ctx.lineWidth   = 2;
    ctx.setLineDash([6, 3]);
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);

    // Preview line to mouse
    if (mousePos) ctx.lineTo(mousePos.x, mousePos.y);

    if (state.currentTool === 'area') ctx.closePath();
    ctx.stroke();
    ctx.setLineDash([]);

    // Points
    pts.forEach((p, i) => {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 5, 0, Math.PI*2);
      ctx.fillStyle = i === 0 ? '#fff' : col;
      ctx.fill();
    });
  }
}

function drawZone(z, highlight) {
  const pts = z.points.map(p => ({ x: p.x * canvas.width, y: p.y * canvas.height }));
  if (pts.length < 2) return;

  ctx.strokeStyle = z.color;
  ctx.lineWidth   = highlight ? 3 : 2;
  ctx.setLineDash([]);

  if (z.type === 'wire') {
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    ctx.lineTo(pts[1].x, pts[1].y);
    ctx.stroke();
    // Arrow
    const angle = Math.atan2(pts[1].y - pts[0].y, pts[1].x - pts[0].x);
    const mx = (pts[0].x + pts[1].x) / 2;
    const my = (pts[0].y + pts[1].y) / 2;
    ctx.beginPath();
    ctx.moveTo(mx, my);
    ctx.lineTo(mx - 12*Math.cos(angle-0.4), my - 12*Math.sin(angle-0.4));
    ctx.lineTo(mx - 12*Math.cos(angle+0.4), my - 12*Math.sin(angle+0.4));
    ctx.closePath();
    ctx.fillStyle = z.color;
    ctx.fill();
  } else {
    ctx.fillStyle = hexToRgba(z.color, 0.15);
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }

  // Label
  const cx = pts.reduce((s,p)=>s+p.x,0)/pts.length;
  const cy = pts.reduce((s,p)=>s+p.y,0)/pts.length;
  ctx.fillStyle = z.color;
  ctx.font      = 'bold 13px sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(z.name, cx, cy - 6);
  ctx.textAlign = 'left';
}

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1,3),16);
  const g = parseInt(hex.slice(3,5),16);
  const b = parseInt(hex.slice(5,7),16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function finishCurrentZone() {
  const pts = state.currentPoints;
  if (state.currentTool === 'wire' && pts.length < 2) {
    showAlert('Una línea necesita exactamente 2 puntos.', 'warning'); return;
  }
  if (state.currentTool === 'area' && pts.length < 3) {
    showAlert('Un área necesita al menos 3 puntos.', 'warning'); return;
  }

  const name  = document.getElementById('zone-name-input').value.trim()
                || (state.currentTool === 'wire' ? `Línea ${state.zones.length+1}` : `Área ${state.zones.length+1}`);
  const color = document.getElementById('zone-color-input').value;

  const zone = {
    id:     crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(),
    type:   state.currentTool,
    name,
    color,
    points: pts.map(p => toNormCoords(p.x, p.y)),
  };

  state.zones.push(zone);
  state.currentPoints = [];
  document.getElementById('zone-name-input').value = '';
  // Cycle color
  document.getElementById('zone-color-input').value = randomColor();

  renderZonesList();
  redraw();
}

function undoLastPoint() {
  if (state.currentPoints.length > 0) {
    state.currentPoints.pop();
    redraw();
  } else if (state.zones.length > 0) {
    state.zones.pop();
    renderZonesList();
    redraw();
  }
}

function clearCanvas() {
  state.zones         = [];
  state.currentPoints = [];
  renderZonesList();
  redraw();
}

function deleteZone(id) {
  state.zones = state.zones.filter(z => z.id !== id);
  renderZonesList();
  redraw();
}

function renderZonesList() {
  const list  = document.getElementById('zones-list');
  const empty = document.getElementById('zones-empty');
  const count = document.getElementById('zone-count');
  count.textContent = state.zones.length;

  if (state.zones.length === 0) {
    empty.style.display = '';
    list.querySelectorAll('.zone-item').forEach(el => el.remove());
    return;
  }
  empty.style.display = 'none';
  list.querySelectorAll('.zone-item').forEach(el => el.remove());

  state.zones.forEach(z => {
    const div = document.createElement('div');
    div.className = 'zone-item';
    div.innerHTML = `
      <div class="zone-color-dot" style="background:${z.color}"></div>
      <span class="zone-type-badge zone-type-${z.type}">${z.type === 'wire' ? 'Wire' : 'Área'}</span>
      <span class="flex-grow-1 small text-truncate">${z.name}</span>
      <button class="btn btn-sm btn-link text-danger p-0" onclick="deleteZone('${z.id}')">
        <i class="bi bi-x-lg"></i>
      </button>`;
    list.appendChild(div);
  });
}

function randomColor() {
  const colors = ['#00ff88','#ff6b6b','#4ecdc4','#ffe66d','#a29bfe','#fd79a8','#74b9ff','#55efc4'];
  return colors[Math.floor(Math.random() * colors.length)];
}

// ── Process step ──────────────────────────────────────────────────────────────
function prepareProcessStep() {
  const summary = document.getElementById('process-zones-summary');
  const wires = state.zones.filter(z => z.type === 'wire').length;
  const areas = state.zones.filter(z => z.type === 'area').length;
  summary.innerHTML = `
    <div class="d-flex gap-3">
      <span><i class="bi bi-slash-lg text-warning me-1"></i>${wires} línea(s)</span>
      <span><i class="bi bi-pentagon text-info me-1"></i>${areas} área(s)</span>
    </div>
    <div class="mt-1">Video: <strong>${state.videoMeta?.filename || '—'}</strong></div>`;

  resetProcessing();
}

function resetProcessing() {
  document.getElementById('process-idle').classList.remove('d-none');
  document.getElementById('process-running').classList.add('d-none');
  document.getElementById('process-done').classList.add('d-none');
  document.getElementById('process-error').classList.add('d-none');
  document.getElementById('btn-start-process').disabled = false;
}

async function startProcessing() {
  if (!state.videoId) { showAlert('No hay video cargado.', 'warning'); return; }

  document.getElementById('btn-start-process').disabled = true;
  document.getElementById('process-idle').classList.add('d-none');
  document.getElementById('process-running').classList.remove('d-none');

  const body = {
    video_id:   state.videoId,
    zones:      state.zones,
    confidence: parseFloat(document.getElementById('conf-slider').value),
    device:     document.getElementById('device-select').value,
  };

  try {
    const r = await fetch('/api/process', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await r.text());
    const { job_id } = await r.json();
    state.jobId = job_id;
    pollJob(job_id);
  } catch (e) {
    showProcessError('Error al iniciar: ' + e.message);
  }
}

function pollJob(jobId) {
  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  es.onmessage = e => {
    const job = JSON.parse(e.data);
    updateProcessProgress(job);
    if (job.status === 'done' || job.status === 'error') {
      es.close();
      if (job.status === 'done') {
        state.jobStats      = job.stats;
        state.resultVideoUrl = job.result_video;
        showProcessDone(job);
      } else {
        showProcessError(job.message || 'Error desconocido');
      }
    }
  };
  es.onerror = () => {
    es.close();
    // Fallback to polling
    const interval = setInterval(async () => {
      try {
        const r = await fetch(`/api/jobs/${jobId}`);
        const job = await r.json();
        updateProcessProgress(job);
        if (job.status === 'done' || job.status === 'error') {
          clearInterval(interval);
          if (job.status === 'done') {
            state.jobStats       = job.stats;
            state.resultVideoUrl = job.result_video;
            showProcessDone(job);
          } else {
            showProcessError(job.message || 'Error desconocido');
          }
        }
      } catch { clearInterval(interval); }
    }, 1000);
  };
}

function updateProcessProgress(job) {
  const pct = job.progress || 0;
  document.getElementById('proc-pct').textContent = pct.toFixed(1) + '%';
  document.getElementById('proc-bar').style.width = pct + '%';

  if (job.stats) {
    const s = job.stats;
    document.getElementById('proc-stats').innerHTML = `
      <div class="col-6"><div class="stat-row"><span class="stat-label">Frames</span>
        <span class="stat-value text-info">${s.processed_frames}/${s.total_frames}</span></div></div>
      <div class="col-6"><div class="stat-row"><span class="stat-label">Detecciones</span>
        <span class="stat-value text-warning">${s.total_detections}</span></div></div>
      <div class="col-6"><div class="stat-row"><span class="stat-label">Objetos únicos</span>
        <span class="stat-value text-success">${s.unique_objects}</span></div></div>
      <div class="col-6"><div class="stat-row"><span class="stat-label">FPS promedio</span>
        <span class="stat-value text-primary">${(s.fps_avg||0).toFixed(1)}</span></div></div>`;
  }
}

function showProcessDone(job) {
  document.getElementById('process-running').classList.add('d-none');
  document.getElementById('process-done').classList.remove('d-none');
}

function showProcessError(msg) {
  document.getElementById('process-running').classList.add('d-none');
  document.getElementById('process-error').classList.remove('d-none');
  document.getElementById('process-error-msg').textContent = msg;
  document.getElementById('btn-start-process').disabled = false;
}

// ── Results step ──────────────────────────────────────────────────────────────
function showResults() {
  if (!state.resultVideoUrl) return;

  const vid = document.getElementById('result-video');
  vid.src = state.resultVideoUrl + '?t=' + Date.now();
  vid.load();

  const dlBtn = document.getElementById('btn-download');
  dlBtn.href = state.resultVideoUrl;

  renderStats(state.jobStats);
}

// Override goToStep to trigger showResults
const _origGoToStep = goToStep;
window.goToStep = function(n) {
  _origGoToStep(n);
  if (n === 4) showResults();
};

function renderStats(s) {
  if (!s) return;

  document.getElementById('stats-general').innerHTML = `
    <div class="stat-row"><span class="stat-label">Frames procesados</span>
      <span class="stat-value text-info">${s.processed_frames}</span></div>
    <div class="stat-row"><span class="stat-label">Total detecciones</span>
      <span class="stat-value text-warning">${s.total_detections}</span></div>
    <div class="stat-row"><span class="stat-label">Objetos únicos</span>
      <span class="stat-value text-success">${s.unique_objects}</span></div>
    <div class="stat-row"><span class="stat-label">FPS promedio</span>
      <span class="stat-value text-primary">${(s.fps_avg||0).toFixed(1)}</span></div>`;

  // Wire events
  const wireDiv = document.getElementById('stats-wires');
  const wireEvents = s.wire_events || {};
  if (Object.keys(wireEvents).length === 0) {
    wireDiv.innerHTML = '<p class="text-secondary small text-center py-2">Sin líneas definidas</p>';
  } else {
    wireDiv.innerHTML = Object.entries(wireEvents).map(([name, ev]) => `
      <div class="stat-row">
        <span class="stat-label">${name}</span>
        <div class="d-flex gap-2">
          <span class="badge bg-warning text-dark">→ ${ev.count1}</span>
          <span class="badge bg-info text-dark">← ${ev.count2}</span>
        </div>
      </div>`).join('');
  }

  // Area events
  const areaDiv = document.getElementById('stats-areas');
  const areaEvents = s.area_events || {};
  if (Object.keys(areaEvents).length === 0) {
    areaDiv.innerHTML = '<p class="text-secondary small text-center py-2">Sin áreas definidas</p>';
  } else {
    areaDiv.innerHTML = Object.entries(areaEvents).map(([name, ev]) => `
      <div class="stat-row">
        <span class="stat-label">${name}</span>
        <div class="d-flex gap-2">
          <span class="badge bg-danger">Max: ${ev.max_count}</span>
          <span class="badge bg-secondary">Total: ${ev.total_intrusions}</span>
        </div>
      </div>`).join('');
  }
}

// ── Alert helper ──────────────────────────────────────────────────────────────
function showAlert(msg, type = 'info') {
  const container = document.getElementById('content-area');
  const alert = document.createElement('div');
  alert.className = `alert alert-${type} alert-dismissible fade show position-fixed bottom-0 end-0 m-3`;
  alert.style.zIndex = 9999;
  alert.innerHTML = `${msg}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
  document.body.appendChild(alert);
  setTimeout(() => alert.remove(), 4000);
}
