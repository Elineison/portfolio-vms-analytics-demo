const $ = (id) => document.getElementById(id);

const defaultRoi = [
  { x: 0.15, y: 0.15 },
  { x: 0.85, y: 0.15 },
  { x: 0.85, y: 0.9 },
  { x: 0.15, y: 0.9 },
];

let state = {
  cameras: [],
  activeCamera: null,
  ws: null,
  roi: [...defaultRoi],
  dragIndex: null,
  statusTimer: null,
  eventsTimer: null,
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

function activeAnalytics() {
  return {
    enabled: $("analyticsEnabled").checked,
    roi: state.roi,
    after_hours: {
      enabled: $("afterEnabled").checked,
      start: $("afterStart").value || "18:00",
      end: $("afterEnd").value || "06:00",
      min_consecutive_hits: Number($("afterHits").value || 2),
      cooldown_s: 60,
    },
    group_loitering: {
      enabled: $("groupEnabled").checked,
      min_people: Number($("groupPeople").value || 3),
      dwell_s: Number($("groupDwell").value || 120),
      cooldown_s: 120,
    },
  };
}

function loadAnalytics(camera) {
  const analytics = camera?.analytics || {};
  const after = analytics.after_hours || {};
  const group = analytics.group_loitering || {};
  state.roi = analytics.roi?.length >= 3 ? analytics.roi : [...defaultRoi];
  $("analyticsEnabled").checked = Boolean(analytics.enabled);
  $("afterEnabled").checked = Boolean(after.enabled);
  $("afterStart").value = (after.start || "18:00").slice(0, 5);
  $("afterEnd").value = (after.end || "06:00").slice(0, 5);
  $("afterHits").value = after.min_consecutive_hits || 2;
  $("groupEnabled").checked = Boolean(group.enabled);
  $("groupPeople").value = group.min_people || 3;
  $("groupDwell").value = group.dwell_s || 120;
  drawRoi();
}

function renderCameras() {
  const list = $("cameraList");
  list.innerHTML = "";
  if (!state.cameras.length) {
    list.innerHTML = '<div class="camera-item"><span>Nenhuma camera cadastrada.</span></div>';
    return;
  }
  state.cameras.forEach((camera) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "camera-item ghost";
    button.innerHTML = `<strong>${escapeHtml(camera.name)}</strong><span>${escapeHtml(camera.rtsp_url)}</span>`;
    button.onclick = () => selectCamera(camera.id);
    list.appendChild(button);
  });
}

function renderEvents(events) {
  const list = $("eventsList");
  list.innerHTML = "";
  if (!events.length) {
    list.innerHTML = '<div class="event-item"><span>Sem eventos ainda.</span></div>';
    return;
  }
  events.forEach((event) => {
    const item = document.createElement("div");
    item.className = "event-item";
    item.innerHTML = `<strong>${escapeHtml(event.title)}</strong><span>${escapeHtml(event.message)}</span>`;
    list.appendChild(item);
  });
}

async function refreshSystem() {
  const info = await api("/api/system");
  const detector = info.detector.available ? info.detector.backend : "sem YOLO";
  $("systemStatus").textContent = `${info.stream_standard} | detector: ${detector}`;
}

async function refreshCameras() {
  state.cameras = await api("/api/cameras");
  renderCameras();
}

async function refreshEvents() {
  const qs = state.activeCamera ? `?camera_id=${encodeURIComponent(state.activeCamera.id)}` : "";
  renderEvents(await api(`/api/events${qs}`));
}

async function refreshRuntimeStatus() {
  if (!state.activeCamera) {
    $("runtimeBadge").textContent = "Sem camera";
    return;
  }
  try {
    const status = await api(`/api/cameras/${state.activeCamera.id}/status`);
    const age = status.last_frame_age_s === null ? "--" : `${status.last_frame_age_s.toFixed(1)}s`;
    const size = status.width && status.height ? `${status.width}x${status.height}` : "sem frame";
    $("runtimeBadge").textContent = `${status.state} | ${size} | ROI: ${status.detections} pessoa(s) | frame: ${age}`;
  } catch (error) {
    $("runtimeBadge").textContent = "Status indisponivel";
  }
}

async function selectCamera(cameraId) {
  const camera = state.cameras.find((item) => item.id === cameraId);
  if (!camera) return;
  state.activeCamera = camera;
  $("activeCameraTitle").textContent = camera.name;
  $("emptyState").style.display = "none";
  loadAnalytics(camera);
  openPreview(camera.id);
  await refreshEvents();
  await refreshRuntimeStatus();
}

function openPreview(cameraId) {
  closePreview();
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  state.ws = new WebSocket(`${protocol}://${location.host}/ws/preview/${cameraId}?fps=12`);
  state.ws.binaryType = "blob";
  state.ws.onmessage = (event) => {
    const url = URL.createObjectURL(event.data);
    const image = $("previewImage");
    const previous = image.dataset.url;
    image.onload = () => {
      if (previous) URL.revokeObjectURL(previous);
      image.dataset.url = url;
      drawRoi();
    };
    image.src = url;
  };
  state.ws.onclose = () => {
    if (state.activeCamera) $("systemStatus").textContent = "Preview encerrado";
  };
}

function closePreview() {
  if (state.ws) {
    state.ws.close();
    state.ws = null;
  }
}

function canvasPoint(event) {
  const canvas = $("roiCanvas");
  const rect = canvas.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
    y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
  };
}

function nearestPoint(point) {
  let best = 0;
  let distance = Infinity;
  state.roi.forEach((candidate, index) => {
    const dx = point.x - candidate.x;
    const dy = point.y - candidate.y;
    const score = Math.sqrt(dx * dx + dy * dy);
    if (score < distance) {
      best = index;
      distance = score;
    }
  });
  return best;
}

function drawRoi() {
  const canvas = $("roiCanvas");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);
  if (!state.roi.length) return;
  ctx.beginPath();
  state.roi.forEach((point, index) => {
    const x = point.x * rect.width;
    const y = point.y * rect.height;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.closePath();
  ctx.fillStyle = "rgba(20, 184, 166, 0.18)";
  ctx.strokeStyle = "#14b8a6";
  ctx.lineWidth = 2;
  ctx.fill();
  ctx.stroke();
  state.roi.forEach((point) => {
    ctx.beginPath();
    ctx.arc(point.x * rect.width, point.y * rect.height, 7, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.strokeStyle = "#0f766e";
    ctx.lineWidth = 2;
    ctx.stroke();
  });
}

async function saveConfig() {
  if (!state.activeCamera) return;
  const camera = await api(`/api/cameras/${state.activeCamera.id}`, {
    method: "PATCH",
    body: JSON.stringify({ analytics: activeAnalytics() }),
  });
  state.activeCamera = camera;
  await refreshCameras();
  $("systemStatus").textContent = "Regras salvas";
  await refreshRuntimeStatus();
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}

$("cameraForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const camera = await api("/api/cameras", {
    method: "POST",
    body: JSON.stringify({
      name: $("cameraName").value.trim(),
      rtsp_url: $("rtspUrl").value.trim(),
    }),
  });
  $("cameraForm").reset();
  await refreshCameras();
  await selectCamera(camera.id);
});

$("saveConfig").onclick = saveConfig;
$("stopPreview").onclick = closePreview;
$("refreshCameras").onclick = refreshCameras;
$("refreshEvents").onclick = refreshEvents;
$("resetRoi").onclick = () => {
  state.roi = [...defaultRoi];
  drawRoi();
};
$("useFullFrame").onclick = () => {
  state.roi = [
    { x: 0.02, y: 0.02 },
    { x: 0.98, y: 0.02 },
    { x: 0.98, y: 0.98 },
    { x: 0.02, y: 0.98 },
  ];
  drawRoi();
};

$("roiCanvas").addEventListener("pointerdown", (event) => {
  if (!state.activeCamera) return;
  state.dragIndex = nearestPoint(canvasPoint(event));
});

$("roiCanvas").addEventListener("pointermove", (event) => {
  if (state.dragIndex === null) return;
  state.roi[state.dragIndex] = canvasPoint(event);
  drawRoi();
});

window.addEventListener("pointerup", () => {
  state.dragIndex = null;
});
window.addEventListener("resize", drawRoi);

refreshSystem().catch(console.error);
refreshCameras().catch(console.error);
refreshEvents().catch(console.error);
state.statusTimer = window.setInterval(() => refreshRuntimeStatus().catch(console.error), 2000);
state.eventsTimer = window.setInterval(() => refreshEvents().catch(console.error), 5000);
