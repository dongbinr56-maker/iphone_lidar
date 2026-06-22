"use strict";

const el = (id) => document.getElementById(id);
const recordButton = el("recordButton");
const recButtonLabel = el("recButtonLabel");
const recordProgress = el("recordProgress");
const recordProgressBar = el("recordProgressBar");
const recordState = el("recordState");
const stateCard = el("stateCard");
const preflightCard = el("preflightCard");
const preflightSummary = el("preflightSummary");
const checklist = el("checklist");
const blocker = el("blocker");
const sensorCard = el("sensorCard");
const sensorBadge = el("sensorBadge");
const sensorSource = el("sensorSource");
const sensorNote = el("sensorNote");
const outputCard = el("outputCard");
const outputBadge = el("outputBadge");
const outputList = el("outputList");
const camerasPill = el("camerasPill");
const camerasPillText = el("camerasPillText");
const relayPill = el("relayPill");
const relayPillText = el("relayPillText");
const clock = el("clock");
const cameraWall = el("cameraWall");
const wallEmpty = el("wallEmpty");
const footerMeta = el("footerMeta");
const tileTemplate = el("cameraTileTemplate");
const metaCard = el("metaCard");
const metaError = el("metaError");
const metaFields = {
  protocol: el("metaProtocol"),
  scenario: el("metaScenario"),
  glove: el("metaGlove"),
  torso: el("metaTorso"),
  operator: el("metaOperator"),
  mask: el("metaMask"),
  bag: el("metaBag"),
  adjunct: el("metaAdjunct"),
  lighting: el("metaLighting"),
  occlusion: el("metaOcclusion"),
  manikin: el("metaManikin"),
  calib: el("metaCalib"),
  notes: el("metaNotes"),
};

const metric = {
  ventOut: el("mVentOut"),
  val1: el("mVal1"),
  insp: el("mInsp"),
  sensorPk: el("mSensorPk"),
  allPk: el("mAllPk"),
};

let GO2RTC_API_PORT = 11984;
let RECORD_SECONDS = 30;
let COUNTDOWN_SECONDS = 3;
const cameras = new Map();
let startError = null;

const ceilSec = (v) => Math.ceil(Math.max(0, v || 0));
const blankToNull = (value) => {
  const text = String(value ?? "").trim();
  return text ? text : null;
};

function transportLabel(transport) {
  if (transport === "webrtc") return "영상 수신";
  if (transport === "mjpeg") return "대체 수신";
  return "수신 중";
}

function collectExperimentMeta() {
  const scenarioText = String(metaFields.scenario.value || "").trim();
  return {
    protocol_type: metaFields.protocol.value,
    scenario_id: scenarioText ? Number(scenarioText) : null,
    glove_condition: metaFields.glove.value,
    torso_clothing: metaFields.torso.value,
    operator_id_raw: metaFields.operator.value,
    mask_size: blankToNull(metaFields.mask.value),
    bag_type: blankToNull(metaFields.bag.value),
    adjunct_use: blankToNull(metaFields.adjunct.value),
    lighting: blankToNull(metaFields.lighting.value),
    occlusion_severity: blankToNull(metaFields.occlusion.value),
    manikin_type: blankToNull(metaFields.manikin.value),
    calib_version: blankToNull(metaFields.calib.value),
    notes: blankToNull(metaFields.notes.value) || "",
  };
}

function renderMetaError(result) {
  if (!result || result.state !== "error" || !result.meta_errors) {
    metaError.hidden = true;
    metaError.textContent = "";
    setState(metaCard, "wait");
    return "";
  }
  const parts = Object.entries(result.meta_errors).map(([field, msg]) => `${field}: ${msg}`);
  const message = parts.join(" · ");
  metaError.textContent = message;
  metaError.hidden = false;
  setState(metaCard, "error");
  return message;
}

/* ---------- camera tiles + WebRTC ---------- */

function buildCameraTiles(list) {
  cameras.clear();
  cameraWall.querySelectorAll(".tile").forEach((node) => node.remove());
  if (!list.length) {
    wallEmpty.textContent = ".env 파일에 RTSP_URL_105 / 106 / 107 설정이 필요합니다.";
    wallEmpty.hidden = false;
    return;
  }
  wallEmpty.hidden = true;
  for (const cam of list) {
    const node = tileTemplate.content.firstElementChild.cloneNode(true);
    node.dataset.cameraId = cam.id;
    node.querySelector(".tile-name").textContent = cam.label || `카메라 ${cam.id}`;
    const video = node.querySelector("video");
    const img = node.querySelector("img");
    img.alt = `${cam.label || cam.id} 대체 영상`;
    cameraWall.appendChild(node);
    cameras.set(cam.id, {
      id: cam.id,
      tile: node,
      video,
      img,
      stateText: node.querySelector(".tile-state-text"),
      foot: node.querySelector(".tile-foot"),
      transport: "live",
      pc: null,
    });
  }
}

function setTransport(cam, transport) {
  cam.transport = transport;
  cam.stateText.textContent = transportLabel(transport);
}

function startMjpegFallback(cam, reason) {
  if (cam.pc) {
    try { cam.pc.close(); } catch (_) {}
    cam.pc = null;
  }
  try { cam.video.srcObject = null; } catch (_) {}
  cam.video.style.display = "none";
  cam.img.src = `/stream/${encodeURIComponent(cam.id)}.mjpeg?t=${Date.now()}`;
  cam.img.style.display = "block";
  setTransport(cam, "mjpeg");
  console.warn(`[stream/${cam.id}] WebRTC fallback to MJPEG`, reason);
}

async function startWebRtc(cam) {
  try {
    const pc = new RTCPeerConnection({ iceServers: [] });
    cam.pc = pc;
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.ontrack = (ev) => {
      if (ev.streams[0]) cam.video.srcObject = ev.streams[0];
    };
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const resp = await fetch(
      `http://${window.location.hostname}:${GO2RTC_API_PORT}/api/webrtc?src=${encodeURIComponent(cam.id)}`,
      { method: "POST", headers: { "Content-Type": "application/sdp" }, body: offer.sdp },
    );
    if (!resp.ok) throw new Error(`WebRTC 응답 오류 ${resp.status}`);
    const answerSdp = await resp.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
    cam.video.style.display = "block";
    cam.img.style.display = "none";
    setTransport(cam, "webrtc");

    const watchdog = setTimeout(() => {
      if (cam.video.readyState < 2) startMjpegFallback(cam, "디코딩된 영상 프레임 없음");
    }, 4500);
    cam.video.addEventListener("playing", () => clearTimeout(watchdog), { once: true });
  } catch (err) {
    startMjpegFallback(cam, err);
  }
}

/* ---------- render helpers ---------- */

function setState(node, state) { if (node) node.dataset.state = state; }

function renderRecord(rec, preflightReady, blockers) {
  const busy = rec.state === "countdown" || rec.state === "recording";

  let label;
  if (startError) label = "녹화 시작 차단";
  else if (rec.state === "countdown") label = `${ceilSec(rec.remaining_seconds)}초 후 시작`;
  else if (rec.state === "recording") label = `녹화 중 · ${ceilSec(rec.remaining_seconds)}초`;
  else if (rec.state === "done") label = "녹화 완료";
  else if (rec.state === "error") label = `오류: ${rec.error || "알 수 없음"}`;
  else label = "대기 중";
  recordState.textContent = label;
  setState(stateCard, rec.state === "error" || startError ? "error" : busy ? "ok" : "wait");

  recordButton.disabled = busy || !preflightReady;
  recordButton.dataset.state = busy ? "busy" : "ready";
  recButtonLabel.textContent = busy
    ? (rec.state === "countdown" ? `${ceilSec(rec.remaining_seconds)}초 후 시작` : `녹화 중 · ${ceilSec(rec.remaining_seconds)}초`)
    : preflightReady ? "녹화 시작" : "녹화 조건 대기";
  recordButton.title = (!busy && !preflightReady && blockers && blockers.length) ? blockers.join("\n") : "";

  if (rec.state === "recording") {
    const elapsed = Math.min(RECORD_SECONDS, Math.max(0, RECORD_SECONDS - (rec.remaining_seconds || 0)));
    recordProgress.hidden = false;
    recordProgressBar.style.width = `${(elapsed / RECORD_SECONDS) * 100}%`;
  } else if (rec.state === "countdown") {
    const left = Math.min(COUNTDOWN_SECONDS, Math.max(0, rec.remaining_seconds || 0));
    recordProgress.hidden = false;
    recordProgressBar.style.width = `${((COUNTDOWN_SECONDS - left) / COUNTDOWN_SECONDS) * 100}%`;
  } else {
    recordProgress.hidden = true;
    recordProgressBar.style.width = "0%";
  }
  return busy;
}

function checkRow(state, html) {
  const li = document.createElement("li");
  li.className = "check";
  li.dataset.state = state;
  li.innerHTML = `<span class="check-icon"></span><span class="check-text">${html}</span>`;
  return li;
}

function renderPreflight(preflight) {
  if (!preflight || !preflight.checks) {
    preflightSummary.textContent = "확인 중";
    return;
  }
  const c = preflight.checks;
  const ready = !!preflight.ready_for_recording;
  setState(preflightCard, ready ? "ok" : "wait");

  const rows = [];
  rows.push(checkRow(c.cameras_ready ? "ok" : "wait",
    `카메라 준비 <b>${c.ready_cameras || 0}/${c.total_cameras || 0}</b>`));

  if (c.sensor_enabled) {
    rows.push(checkRow(c.mannequin_connected ? "ok" : "wait", "마네킹 센서 연결"));
    const age = Number.isFinite(c.latest_sensor_age_sec) ? `${c.latest_sensor_age_sec.toFixed(1)}초 전` : "수신 없음";
    rows.push(checkRow(c.mannequin_gt_ready ? "ok" : "wait", `기준값(val1) 최신 수신 · ${age}`));
    preflightSummary.textContent = ready ? "녹화 가능" : `전체 ${c.packet_count || 0} · val1 ${c.sensor_packet_count || 0}`;
  } else {
    rows.push(checkRow("ok", "마네킹 기준값 비활성화 (건너뜀)"));
    preflightSummary.textContent = ready ? "녹화 가능" : "조건 대기";
  }

  checklist.replaceChildren(...rows);

  if (!ready && preflight.blockers && preflight.blockers.length) {
    blocker.textContent = preflight.blockers.join(" · ");
    blocker.hidden = false;
  } else {
    blocker.hidden = true;
  }
}

function renderSensor(sensor) {
  const dash = () => { for (const k in metric) metric[k].textContent = "—"; };

  if (!sensor || !sensor.enabled) {
    setState(sensorCard, "wait");
    sensorBadge.textContent = "비활성화";
    sensorSource.textContent = "마네킹 기준값 비활성화";
    sensorNote.textContent = "";
    dash();
    return;
  }

  const bridge = sensor.sources && sensor.sources.abc_ws_bridge;
  const direct = sensor.sources && sensor.sources.direct_serial;
  const source = bridge && bridge.enabled ? "COM3 (8010 웹소켓 경유)" : "직렬 포트 직접 연결";
  sensorSource.textContent = source;

  const buffer = sensor.buffer || {};
  const packets = Number.isFinite(buffer.packets) ? buffer.packets : 0;
  const sensorPackets = Number.isFinite(buffer.sensor_packets) ? buffer.sensor_packets : 0;
  metric.allPk.textContent = packets.toLocaleString();
  metric.sensorPk.textContent = sensorPackets.toLocaleString();

  if (!sensor.connected) {
    setState(sensorCard, "error");
    sensorBadge.textContent = "연결 대기";
    const err = sensor.last_error || (bridge && bridge.last_error) || (direct && direct.last_error) || "연결 대기 중";
    sensorNote.textContent = err;
    metric.ventOut.textContent = "—";
    metric.val1.textContent = "—";
    metric.insp.textContent = "—";
    return;
  }

  const latest = buffer.latest_sensor;
  if (!latest) {
    setState(sensorCard, "wait");
    sensorBadge.textContent = "연결 정상";
    sensorNote.textContent = "기준값 대기: 아직 0xd0 val1 없음";
    metric.ventOut.textContent = "—";
    metric.val1.textContent = "—";
    metric.insp.textContent = "—";
    return;
  }

  const age = Number.isFinite(sensor.latest_sensor_age_sec)
    ? sensor.latest_sensor_age_sec
    : Math.max(0, Date.now() / 1000 - (latest.ts || 0));
  const limit = sensor.gt_ready_max_age_sec || 0;
  const val1 = latest.val1 != null ? latest.val1 : "—";
  const outputValue = latest.ventilation_output_value != null ? latest.ventilation_output_value : "—";
  const outputUnit = latest.ventilation_output_unit || "";
  const tInsp = Number.isFinite(latest.inspiratory_time_sec) ? `${latest.inspiratory_time_sec.toFixed(2)}s` : "—";

  metric.ventOut.textContent = outputUnit ? `${outputValue} ${outputUnit}` : `${outputValue}`;
  metric.val1.textContent = val1 === "—" ? "—" : `${val1} mL`;
  metric.insp.textContent = tInsp;

  if (sensor.gt_ready) {
    setState(sensorCard, "ok");
    sensorBadge.textContent = "기준값 정상";
    sensorNote.textContent = `최신 val1 ${age.toFixed(1)}초 전`;
  } else {
    setState(sensorCard, "wait");
    sensorBadge.textContent = "기준값 대기";
    sensorNote.textContent = `최신 val1 ${age.toFixed(1)}초 전 (기준 ${limit.toFixed(1)}초)`;
  }
}

function renderOutput(paths) {
  const entries = Object.entries(paths || {});
  if (!entries.length) {
    setState(outputCard, "wait");
    outputBadge.textContent = "없음";
    outputList.innerHTML = '<p class="output-empty">아직 저장된 파일이 없습니다.</p>';
    return;
  }
  setState(outputCard, "ok");
  outputBadge.textContent = `${entries.length}개 저장`;
  outputList.replaceChildren(...entries.map(([id, path]) => {
    const row = document.createElement("div");
    row.className = "output-row";
    const b = document.createElement("b");
    b.textContent = id;
    const span = document.createElement("span");
    span.textContent = path;
    span.title = path;
    row.append(b, span);
    return row;
  }));
}

function renderCameras(list, recording) {
  const isRecording = recording.state === "recording";
  for (const cam of list) {
    const entry = cameras.get(cam.camera_id);
    if (!entry) continue;
    const state = cam.has_frame ? (isRecording ? "rec" : "ok") : "wait";
    entry.tile.dataset.state = state;
    entry.stateText.textContent = cam.has_frame ? transportLabel(entry.transport) : "대기 중";
    const age = cam.frame_age_seconds;
    entry.foot.textContent = cam.error || (cam.has_frame
      ? `${cam.fps.toFixed(1)}fps · 지연 ${age === null ? "-" : age.toFixed(1)}s`
      : "프레임 대기 중");
  }
}

/* ---------- status loop ---------- */

async function refreshStatus() {
  let data;
  try {
    const resp = await fetch("/api/status");
    data = await resp.json();
  } catch (_) {
    return;
  }

  if (startError && Date.now() >= startError.until) startError = null;
  if (startError && data.recording.state === "idle") {
    recordState.textContent = `녹화 시작 차단: ${startError.message}`;
  }

  const preflightReady = data.preflight ? data.preflight.ready_for_recording : data.ready;
  renderRecord(data.recording, preflightReady, data.preflight ? data.preflight.blockers : []);
  renderPreflight(data.preflight);
  renderSensor(data.sensor);
  renderOutput(data.recording.output_paths);
  renderCameras(data.cameras, data.recording);

  const readyCount = data.cameras.filter((c) => c.has_frame).length;
  const total = data.cameras.length;
  camerasPill.dataset.state = total && readyCount === total ? "ok" : "wait";
  camerasPillText.textContent = `카메라 ${readyCount}/${total} 수신`;

  const relayReady = data.go2rtc && data.go2rtc.ready;
  relayPill.dataset.state = relayReady ? "ok" : "wait";
  relayPillText.textContent = relayReady ? "중계 준비됨" : "중계 대기";
}

recordButton.addEventListener("click", async () => {
  recordButton.disabled = true;
  try {
    const resp = await fetch("/api/record/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ experiment: collectExperimentMeta() }),
    });
    const result = await resp.json();
    const metaMessage = renderMetaError(result);
    startError = result.state === "error"
      ? { message: metaMessage || result.error || "알 수 없음", until: Date.now() + 5000 }
      : null;
    if (result.state !== "error") setState(metaCard, "ok");
  } catch (err) {
    startError = { message: String(err), until: Date.now() + 5000 };
  } finally {
    await refreshStatus();
  }
});

function tickClock() {
  const now = new Date();
  const p = (n) => String(n).padStart(2, "0");
  clock.textContent = `${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`;
}

async function boot() {
  tickClock();
  setInterval(tickClock, 1000);
  try {
    const resp = await fetch("/api/config");
    const config = await resp.json();
    GO2RTC_API_PORT = config.go2rtc_api_port || GO2RTC_API_PORT;
    RECORD_SECONDS = config.record_seconds || RECORD_SECONDS;
    COUNTDOWN_SECONDS = config.countdown_seconds || COUNTDOWN_SECONDS;
    footerMeta.textContent = `WebRTC :${GO2RTC_API_PORT} · 자동 종료 ${RECORD_SECONDS}s`;
    buildCameraTiles(config.cameras || []);
    for (const cam of cameras.values()) startWebRtc(cam);
  } catch (err) {
    wallEmpty.textContent = "설정을 불러오지 못했습니다.";
    wallEmpty.hidden = false;
  }

  window.addEventListener("beforeunload", () => {
    for (const cam of cameras.values()) {
      try { if (cam.pc) cam.pc.close(); } catch (_) {}
    }
  });

  await refreshStatus();
  setInterval(refreshStatus, 500);
}

boot();
