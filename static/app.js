const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(window.location.search);

const defaultRoi = [
  { x: 0.15, y: 0.15 },
  { x: 0.85, y: 0.15 },
  { x: 0.85, y: 0.9 },
  { x: 0.15, y: 0.9 },
];

let state = {
  me: null,
  system: null,
  cameras: [],
  activeCamera: null,
  ws: null,
  roi: [...defaultRoi],
  dragIndex: null,
  roiEditorVisible: false,
  drawMode: false,
  statusTimer: null,
  eventsTimer: null,
  hasUnsavedConfig: false,
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    if (response.status === 401) showAuth();
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

function normalizeTime(value, fallback) {
  const raw = String(value || fallback || "00:00").slice(0, 5);
  return `${raw}:00`;
}

function showAuth() {
  $("authScreen").classList.remove("hidden");
  document.querySelector(".app-shell").classList.add("hidden");
  state.me = null;
  const configured = Boolean(state.system?.auth?.google_configured);
  $("googleLogin").classList.toggle("disabled", !configured);
  if (configured) {
    $("googleLogin").href = "/auth/google";
    $("authHint").textContent = "O cadastro e criado automaticamente no primeiro acesso Google.";
  } else {
    $("googleLogin").removeAttribute("href");
    $("authHint").textContent = "OAuth Google pendente neste servidor. Configure as credenciais para ativar o fluxo real.";
  }
}

function showApp() {
  $("authScreen").classList.add("hidden");
  document.querySelector(".app-shell").classList.remove("hidden");
}

function setRoiEditorVisible(visible) {
  state.roiEditorVisible = visible;
  if (!visible) {
    state.drawMode = false;
    state.dragIndex = null;
  }
  ["drawRoi", "useFullFrame"].forEach((id) => $(id).classList.toggle("hidden", !visible));
  $("editRoi").textContent = visible ? "Ocultar ROI" : "Editar ROI";
  $("drawRoi").textContent = state.drawMode ? "Concluir ROI" : "Redesenhar";
  drawRoi();
}

function markConfigDirty() {
  if (!state.activeCamera) return;
  state.hasUnsavedConfig = true;
  $("analysisStateText").textContent = "Alterações pendentes";
  $("systemStatus").textContent = "Configuração alterada";
}

function activeAnalytics() {
  const afterEnabled = $("afterEnabled").checked;
  const groupEnabled = $("groupEnabled").checked;
  return {
    enabled: $("analyticsEnabled").checked || afterEnabled || groupEnabled,
    analysis_fps: Number($("analysisFps").value || 2),
    confidence_threshold: Number($("confidenceThreshold").value || 0.35),
    min_box_area_ratio: Number($("minBoxArea").value || 0.005),
    roi: state.roi,
    after_hours: {
      enabled: afterEnabled,
      start: normalizeTime($("afterStart").value, "18:00"),
      end: normalizeTime($("afterEnd").value, "06:00"),
      min_consecutive_hits: Number($("afterHits").value || 2),
      cooldown_s: 60,
    },
    group_loitering: {
      enabled: groupEnabled,
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
  $("analysisStateText").textContent = analytics.enabled ? "Detecção de pessoas ativa" : "Detecção pausada";
  $("analysisFps").value = analytics.analysis_fps || 2;
  $("confidenceThreshold").value = analytics.confidence_threshold || 0.35;
  $("minBoxArea").value = analytics.min_box_area_ratio || 0.005;
  $("afterEnabled").checked = Boolean(after.enabled);
  $("afterStart").value = (after.start || "18:00").slice(0, 5);
  $("afterEnd").value = (after.end || "06:00").slice(0, 5);
  $("afterHits").value = after.min_consecutive_hits || 2;
  $("groupEnabled").checked = Boolean(group.enabled);
  $("groupPeople").value = group.min_people || 3;
  $("groupDwell").value = group.dwell_s || 120;
  state.hasUnsavedConfig = false;
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
    button.innerHTML = `<strong>${escapeHtml(camera.name)}</strong><span>${escapeHtml(maskRtsp(camera.rtsp_url))}</span>`;
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
    const image = event.snapshot_url ? `<a href="${event.snapshot_url}" target="_blank" rel="noreferrer"><img src="${event.snapshot_url}" alt="Snapshot da ocorrencia"></a>` : "";
    const mail = event.notification_email ? `<span>E-mail: ${escapeHtml(event.notification_status || "pendente")}</span>` : "";
    item.innerHTML = `<strong>${escapeHtml(event.title)}</strong><span>${escapeHtml(event.message)}</span>${mail}${image}`;
    list.appendChild(item);
  });
}

async function refreshSystem() {
  const info = await api("/api/system");
  state.system = info;
  const detector = info.detector.available ? info.detector.backend : "sem YOLO";
  const device = info.detector.device || "cpu";
  $("systemStatus").textContent = `${info.stream_standard} | ${detector} ${device}`;
}

async function refreshMe() {
  try {
    state.me = await api("/api/me");
    showApp();
    $("userChip").textContent = `${state.me.email} | ${state.me.trial_days_remaining} dia(s)`;
    if (!state.me.trial_active) {
      $("systemStatus").textContent = "Periodo de demo expirado";
    }
  } catch (error) {
    showAuth();
    throw error;
  }
}

async function refreshCameras() {
  state.cameras = await api("/api/cameras");
  if (state.activeCamera) {
    const updated = state.cameras.find((item) => item.id === state.activeCamera.id);
    if (updated) state.activeCamera = updated;
  }
  renderCameras();
}

async function refreshEvents() {
  if (!state.me) return;
  const qs = state.activeCamera ? `?camera_id=${encodeURIComponent(state.activeCamera.id)}` : "";
  renderEvents(await api(`/api/events${qs}`));
}

async function refreshRuntimeStatus() {
  if (!state.me) return;
  if (!state.activeCamera) {
    $("runtimeBadge").textContent = "Sem camera";
    return;
  }
  try {
    const status = await api(`/api/cameras/${state.activeCamera.id}/status`);
    const age = status.last_frame_age_s === null ? "--" : `${status.last_frame_age_s.toFixed(1)}s`;
    const size = status.width && status.height ? `${status.width}x${status.height}` : "sem frame";
    $("runtimeBadge").textContent = `${status.state} | ${size} | ROI: ${status.detections} pessoa(s) | frame: ${age}`;
    const analysis = status.analysis || {};
    const group = analysis.group_loitering || {};
    $("roiPeopleMetric").textContent = analysis.roi_people ?? status.detections ?? 0;
    $("inferMetric").textContent = analysis.last_infer_ms === undefined ? "--" : `${analysis.last_infer_ms} ms`;
    $("groupProgressMetric").textContent = `${Math.round((group.progress || 0) * 100)}%`;
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
  const rect = imageContentRect();
  return {
    x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
    y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
  };
}

function imageContentRect() {
  const image = $("previewImage");
  const viewer = $("roiCanvas").getBoundingClientRect();
  if (!image.naturalWidth || !image.naturalHeight) return viewer;
  const imageRatio = image.naturalWidth / image.naturalHeight;
  const viewerRatio = viewer.width / viewer.height;
  if (viewerRatio > imageRatio) {
    const width = viewer.height * imageRatio;
    return {
      left: viewer.left + (viewer.width - width) / 2,
      top: viewer.top,
      width,
      height: viewer.height,
    };
  }
  const height = viewer.width / imageRatio;
  return {
    left: viewer.left,
    top: viewer.top + (viewer.height - height) / 2,
    width: viewer.width,
    height,
  };
}

function imageRectInCanvas() {
  const canvas = $("roiCanvas");
  const canvasRect = canvas.getBoundingClientRect();
  const imageRect = imageContentRect();
  return {
    left: imageRect.left - canvasRect.left,
    top: imageRect.top - canvasRect.top,
    width: imageRect.width,
    height: imageRect.height,
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
  const imageRect = imageRectInCanvas();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);
  if (!state.roiEditorVisible) return;
  if (!state.roi.length) return;
  ctx.beginPath();
  state.roi.forEach((point, index) => {
    const x = imageRect.left + point.x * imageRect.width;
    const y = imageRect.top + point.y * imageRect.height;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  if (!state.drawMode || state.roi.length >= 3) ctx.closePath();
  ctx.fillStyle = "rgba(20, 184, 166, 0.08)";
  ctx.strokeStyle = "#14b8a6";
  ctx.lineWidth = 2;
  if (state.roi.length >= 3) ctx.fill();
  ctx.stroke();
  state.roi.forEach((point) => {
    ctx.beginPath();
    ctx.arc(imageRect.left + point.x * imageRect.width, imageRect.top + point.y * imageRect.height, 7, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.strokeStyle = "#0f766e";
    ctx.lineWidth = 2;
    ctx.stroke();
  });
}

async function saveConfig() {
  if (!state.activeCamera) return;
  if (state.roi.length < 3) {
    $("systemStatus").textContent = "Conclua a ROI com pelo menos 3 pontos";
    return;
  }
  try {
    const camera = await api(`/api/cameras/${state.activeCamera.id}`, {
      method: "PATCH",
      body: JSON.stringify({ analytics: activeAnalytics() }),
    });
    state.activeCamera = camera;
    loadAnalytics(camera);
    await refreshCameras();
    setRoiEditorVisible(false);
    $("systemStatus").textContent = "Análise aplicada";
    await refreshRuntimeStatus();
  } catch (error) {
    $("systemStatus").textContent = "Erro ao aplicar análise";
    console.error(error);
  }
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

function maskRtsp(value) {
  try {
    const url = new URL(value);
    return `${url.protocol}//${url.hostname}${url.port ? `:${url.port}` : ""}${url.pathname || ""}`;
  } catch {
    return "rtsp://camera";
  }
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
  await saveConfig();
});

$("saveConfig").onclick = saveConfig;
$("stopPreview").onclick = closePreview;
$("logoutButton").onclick = async () => {
  await api("/auth/logout", { method: "POST" }).catch(() => null);
  location.reload();
};
$("googleLogin").addEventListener("click", (event) => {
  if ($("googleLogin").classList.contains("disabled")) event.preventDefault();
});
$("refreshCameras").onclick = refreshCameras;
$("refreshEvents").onclick = refreshEvents;
$("editRoi").onclick = () => {
  setRoiEditorVisible(!state.roiEditorVisible);
};
$("drawRoi").onclick = () => {
  if (state.drawMode) {
    if (state.roi.length < 3) {
      $("systemStatus").textContent = "A ROI precisa de pelo menos 3 pontos";
      return;
    }
    state.drawMode = false;
    $("drawRoi").textContent = "Redesenhar";
    markConfigDirty();
    drawRoi();
    return;
  }
  setRoiEditorVisible(true);
  state.drawMode = true;
  state.roi = [];
  state.dragIndex = null;
  $("drawRoi").textContent = "Concluir ROI";
  markConfigDirty();
  drawRoi();
};
$("useFullFrame").onclick = () => {
  setRoiEditorVisible(true);
  state.drawMode = false;
  state.roi = [
    { x: 0.02, y: 0.02 },
    { x: 0.98, y: 0.02 },
    { x: 0.98, y: 0.98 },
    { x: 0.02, y: 0.98 },
  ];
  markConfigDirty();
  drawRoi();
};

$("roiCanvas").addEventListener("pointerdown", (event) => {
  if (!state.activeCamera || !state.roiEditorVisible) return;
  if (state.drawMode) {
    state.roi.push(canvasPoint(event));
    markConfigDirty();
    drawRoi();
    return;
  }
  state.dragIndex = nearestPoint(canvasPoint(event));
});

$("roiCanvas").addEventListener("pointermove", (event) => {
  if (!state.roiEditorVisible || state.dragIndex === null) return;
  state.roi[state.dragIndex] = canvasPoint(event);
  markConfigDirty();
  drawRoi();
});

window.addEventListener("pointerup", () => {
  state.dragIndex = null;
});
window.addEventListener("resize", drawRoi);

["analyticsEnabled", "afterEnabled", "groupEnabled"].forEach((id) => {
  $(id).addEventListener("change", () => {
    if (id === "afterEnabled" || id === "groupEnabled") $("analyticsEnabled").checked = true;
    $("analysisStateText").textContent = $("analyticsEnabled").checked ? "Detecção de pessoas ativa" : "Detecção pausada";
    markConfigDirty();
  });
});

[
  "analysisFps",
  "confidenceThreshold",
  "minBoxArea",
  "afterStart",
  "afterEnd",
  "afterHits",
  "groupPeople",
  "groupDwell",
].forEach((id) => {
  $(id).addEventListener("input", markConfigDirty);
  $(id).addEventListener("change", markConfigDirty);
});

refreshSystem()
  .then(() => {
    if (params.get("login") === "preview") {
      showAuth();
      return Promise.reject(new Error("login_preview"));
    }
    return refreshMe();
  })
  .then(refreshCameras)
  .then(refreshEvents)
  .catch((error) => {
    if (error.message !== "login_preview") console.error(error);
  });
state.statusTimer = window.setInterval(() => refreshRuntimeStatus().catch(console.error), 2000);
state.eventsTimer = window.setInterval(() => refreshEvents().catch(console.error), 5000);
