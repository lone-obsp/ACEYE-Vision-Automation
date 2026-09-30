/* ACEYE frontend: intentionally dependency-free. All model output is inserted with textContent. */
(function () {
  "use strict";

  const API = "/api";
  const pageTitles = { overview: "运行总览", pipeline: "模块管线", reasoning: "理解与反馈", config: "运行配置", logs: "运行日志" };
  const phaseLabels = { stopped: "已停止", starting: "启动中", running: "运行中", stopping: "停止中", error: "异常" };
  const defaults = {
    phase: "stopped",
    config: {
      capture: { fps: 30, output: 0 },
      detection: { enabled: true, weights: "models/yolo26n-seg.engine", device: "cuda", imgsz: 544, confidence: .4 },
      ocr: { enabled: true, interval: 6, device: "cuda" },
      sam: { enabled: false, weights: "models/sam2.1-hiera-tiny.pt" },
      reasoner: { enabled: true, backend: "local", model: "Qwen3-VL-2B-Instruct", endpoint: "", interval: 1, goal: "判断当前屏幕状态，识别重要事件，并给出下一步建议" },
      output: { overlay: true },
      automation: { enabled: false, dry_run: true }
    },
    profile: "desktop", rhythm: { status: "stopped", game: "muse_dash", running: false, dry_run: false, capture_fps: 0, detected_notes: 0, active_tracks: 0, total_tracks: 0, game_foreground: false, foreground_process: "", input_suppressed: 0, frames_skipped_not_foreground: 0, predicted_hit_time_ms: null, last_action: null, scheduled_actions: 0, real_input_count: 0, sendinput_last_status: null, error: "", safety_stop_key: "f8" }, modules: {}, metrics: { capture_fps: 0, vision_fps: 0, latency_ms: 0, latest_age_ms: 0, yolo_latency_ms: 0, queue_depth: 0, frames: 0, dropped: 0, dropped_vision: 0 },
    vision: { frame_id: "—", objects: [], texts: [], events: [] }, reasoning: { text: "", actions: [] }, logs: [], job: { running: false, message: "" }, last_command: null
  };

  let state = structuredClone(defaults);
  let connected = false;
  let busy = false;
  let pollTimer = null;
  let logFilter = "all";
  const toastKeys = new Map();
  const tokenKey = "aceye_session_token";

  const $ = (id) => document.getElementById(id);
  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const text = (value, fallback = "—") => value === null || value === undefined || value === "" ? fallback : String(value);
  const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const escapeHTML = (value) => text(value, "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[ch]));
  const clone = (value) => value && typeof structuredClone === "function" ? structuredClone(value) : JSON.parse(JSON.stringify(value));

  function getToken() {
    const hash = new URLSearchParams(location.hash.replace(/^#/, ""));
    const supplied = hash.get("token");
    if (supplied) {
      sessionStorage.setItem(tokenKey, supplied);
      history.replaceState(null, "", location.pathname + location.search);
    }
    return sessionStorage.getItem(tokenKey) || "";
  }

  function headers() {
    const token = getToken();
    const value = { Accept: "application/json", "Content-Type": "application/json" };
    if (token) value["X-ACEYE-Token"] = token;
    return value;
  }

  async function request(path, options = {}, auth = true) {
    const opts = { ...options, headers: { ...(options.headers || {}), ...(auth ? headers() : { Accept: "application/json" }) } };
    const response = await fetch(API + path, opts);
    let body = null;
    try { body = await response.json(); } catch (_) { body = {}; }
    if (!response.ok) throw new Error(body.message || body.error || `请求失败（${response.status}）`);
    return body;
  }

  function applyState(next) {
    if (!next || typeof next !== "object") return;
    if (next.state && next.state.phase) next = next.state;
    state = { ...state, ...next, config: mergeConfig(state.config, next.config), rhythm: { ...state.rhythm, ...(next.rhythm || {}) }, metrics: { ...state.metrics, ...(next.metrics || {}) }, vision: { ...state.vision, ...(next.vision || {}) }, reasoning: { ...state.reasoning, ...(next.reasoning || {}) }, job: { ...state.job, ...(next.job || {}) } };
    renderAll();
  }

  const profileKey = (value) => String(value || "").toLowerCase();
  function profileMatches(selected, active) {
    const a = profileKey(active), s = profileKey(selected);
    if (!a) return true; // backend has not reported a profile yet
    if (a === s) return true;
    if (s === "osu" && a.includes("osu")) return true;
    if (s === "muse_dash" && a.includes("muse")) return true;
    if (s === "minecraft" && a.includes("minecraft")) return true;
    return false;
  }

  function mergeConfig(base, incoming) {
    const result = clone(base || defaults.config);
    if (!incoming) return result;
    Object.keys(result).forEach((group) => { if (incoming[group]) result[group] = { ...result[group], ...incoming[group] }; });
    return result;
  }

  function configDiffers(base, incoming) {
    const same = (a, b) => {
      if (b && typeof b === "object" && !Array.isArray(b)) return Object.keys(b).every((key) => same((a || {})[key], b[key]));
      if (Array.isArray(b)) return Array.isArray(a) && a.length === b.length && a.every((item, index) => same(item, b[index]));
      return a === b;
    };
    return !same(base || {}, incoming || {});
  }

  function setConnection(ok, message) {
    connected = ok;
    $("connectionText").textContent = message || (ok ? "后端已连接" : "后端不可用");
    [$("connectionDot"), $("sideDot")].forEach((dot) => { dot.classList.toggle("online", ok); dot.classList.toggle("error", !ok); });
  }

  function showToast(message, kind = "info", duration = 4300) {
    const key = `${kind}:${message}`;
    const last = toastKeys.get(key) || 0;
    const now = Date.now();
    // Suppress identical toasts fired within 5s of each other: the same
    // message must never stack up from repeated clicks or repeated polling.
    if (now - last < 5000) return;
    toastKeys.set(key, now);
    const item = document.createElement("div"); item.className = `toast ${kind}`; item.textContent = message;
    $("toastRegion").appendChild(item); window.setTimeout(() => item.remove(), duration);
  }

  function setBusy(value, button) {
    busy = value;
    const controls = qa("#startButton, #stopButton, #refreshButton, #reasonButton, #rhythmStartButton, #rhythmStopButton, #configForm button, #commandForm button, #moduleCards button");
    controls.forEach((control) => {
      if (value) { control.dataset.wasDisabled = control.disabled ? "1" : "0"; control.disabled = true; }
      else { control.disabled = control.dataset.wasDisabled === "1"; delete control.dataset.wasDisabled; }
    });
    if (button) button.classList.toggle("busy", value);
  }

  async function runAction(path, payload, message, button, options = {}) {
    if (busy && !options.force) return;
    const ownsBusy = !busy;
    if (ownsBusy) setBusy(true, button);
    try {
      const result = await request(path, { method: "POST", body: JSON.stringify(payload || {}) });
      applyState(result);
      if (message) showToast(message, "info");
      if (result.message) showToast(result.message, result.ok === false ? "warn" : "info");
      return result;
    } catch (error) {
      showToast(error.message, "error", 6500);
      setConnection(false, "请求失败");
      if (!options.quiet) appendLocalLog("error", error.message);
      return null;
    } finally { if (ownsBusy) setBusy(false, button); }
  }

  async function refreshState(showError = false) {
    try {
      const result = await request("/state"); applyState(result); setConnection(true, "后端已连接");
    } catch (error) {
      setConnection(false, "等待后端");
      if (showError) showToast(error.message, "error");
    }
  }

  async function bootstrap() {
    try {
      const health = await request("/health", {}, false);
      if (health.token) sessionStorage.setItem(tokenKey, health.token);
      setConnection(true, health.version ? `后端 ${health.version}` : "后端已连接");
      if (health.state) applyState(health.state);
    } catch (_) { setConnection(false, "等待后端"); }
    await refreshState(false);
    pollTimer = window.setInterval(() => refreshState(false), 1000);
  }

  function setView(view) {
    if (!pageTitles[view]) view = "overview";
    qa(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${view}`));
    qa(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
    $("pageTitle").textContent = pageTitles[view];
  }

  function phaseClass(phase) { return ["stopped", "starting", "running", "stopping", "error"].includes(phase) ? phase : "stopped"; }
  function moduleStatus(mod) { return typeof mod === "string" ? mod : text(mod && mod.status, "offline"); }
  function statusKind(status) {
    status = String(status).toLowerCase();
    if (["running", "ready", "ok", "available", "enabled"].includes(status)) return "online";
    if (["starting", "loading", "preparing", "busy"].includes(status)) return "busy";
    if (["error", "failed", "unavailable"].includes(status)) return "error";
    if (["missing-model", "degraded"].includes(status)) return "degraded";
    return "";
  }

  function renderStatus() {
    const phase = text(state.phase, "stopped").toLowerCase();
    const safePhase = phaseClass(phase);
    const pill = $("phasePill"); pill.className = `phase-pill ${safePhase}`; pill.textContent = phaseLabels[phase] || phase;
    $("phaseHint").textContent = state.job && state.job.message ? state.job.message : (phase === "running" ? "视觉管线正在处理最新帧" : "等待启动视觉管线");
    $("sidePhase").textContent = phaseLabels[phase] || phase;
    $("sideDot").classList.toggle("busy", ["starting", "stopping"].includes(phase));
    $("startButton").disabled = busy || ["running", "starting"].includes(phase);
    $("stopButton").disabled = busy || ["stopped", "stopping"].includes(phase);
    const metrics = state.metrics || {};
    if ($("modeSelect")) $("modeSelect").value = text(state.profile, "desktop");
    const capture = number(metrics.capture_fps), vision = number(metrics.vision_fps), latency = number(metrics.latency_ms);
    $("metricCapture").textContent = capture.toFixed(1); $("metricVision").textContent = vision.toFixed(1); $("metricLatency").textContent = Math.round(latency); $("metricFrames").textContent = number(metrics.frames).toLocaleString(); $("metricDropped").textContent = number(metrics.dropped).toLocaleString();
    if ($("metricQueue")) $("metricQueue").textContent = number(metrics.queue_depth).toFixed(0);
    if ($("metricYoloLatency")) $("metricYoloLatency").textContent = number(metrics.yolo_latency_ms).toFixed(1);
    if ($("metricAge")) $("metricAge").textContent = number(metrics.latest_age_ms || latency).toFixed(0);
    $("captureBar").style.width = `${Math.min(100, capture / Math.max(1, number(state.config?.capture?.fps, 30)) * 100)}%`;
    $("visionBar").style.width = `${Math.min(100, vision / Math.max(1, number(state.config?.capture?.fps, 30)) * 100)}%`;
    $("latencyBar").style.width = `${Math.min(100, latency / 200 * 100)}%`;
  }

  function renderRhythm() {
    const rhythm = state.rhythm || {};
    const status = text(rhythm.status, "stopped");
    $("rhythmStatus").textContent = status;
    $("rhythmStatus").className = `module-status ${statusKind(status)}`;
    $("rhythmGame").textContent = text(rhythm.game, "muse_dash");
    $("rhythmCapture").textContent = number(rhythm.capture_fps).toFixed(1);
    // "目标" must mean confirmed, actionable targets. The legacy counter
    // reported lane-blob counts (always ~4) even with no real target.
    const notes = rhythm.active_tracks !== undefined
      ? `${number(rhythm.active_tracks).toFixed(0)} / 共 ${number(rhythm.total_tracks).toFixed(0)}`
      : number(rhythm.detected_notes).toFixed(0);
    $("rhythmNotes").textContent = notes;
    $("rhythmScheduled").textContent = number(rhythm.scheduled_actions).toFixed(0);
    $("rhythmInputCount").textContent = number(rhythm.real_input_count).toFixed(0);
    $("rhythmSuppressed").textContent = number(rhythm.input_suppressed).toFixed(0);
    $("rhythmForeground").textContent = rhythm.game_foreground ? "游戏前台" : (rhythm.foreground_process ? `非前台 · ${rhythm.foreground_process}` : "非前台");
    $("rhythmForeground").className = `module-status ${rhythm.game_foreground ? "online" : "busy"}`;
    const inputStatus = rhythm.sendinput_last_status;
    $("rhythmInputStatus").textContent = inputStatus === null || inputStatus === undefined
      ? "尚未调用" : `${inputStatus}（${Number(inputStatus) === 0 ? "成功" : "失败"}）`;
    $("rhythmError").textContent = text(rhythm.error, "无");
    const hit = rhythm.predicted_hit_time_ms;
    $("rhythmHit").textContent = hit === null || hit === undefined ? "—" : `${number(hit).toFixed(1)} ms`;
    const action = rhythm.last_action;
    $("rhythmAction").textContent = action ? `${text(action.key, "action")} · ${text(action.target_ns, "—")}` : "—";
    const safetyKey = text(rhythm.safety_stop_key, "f8").toUpperCase();
    const runLabel = rhythm.running ? "运行中 · 真实输入" : "已停止 · 启动后使用真实输入";
    $("rhythmSafety").textContent = `${runLabel} · ${safetyKey} 安全停止`;
    $("rhythmStartButton").disabled = busy || Boolean(rhythm.running);
    $("rhythmStopButton").disabled = busy || !rhythm.running;
  }

  const moduleMeta = [
    { key: "capture", name: "屏幕捕获", description: "DXGI Desktop Duplication", glyph: "◉", profile: null, detail: () => `${number(state.config.capture?.fps, 30)} FPS`, value: () => `output ${number(state.config.capture?.output, 0)}` },
    { key: "detection", name: "实时检测", description: "YOLO26n-seg / TensorRT", glyph: "▣", profile: "vision", detail: () => state.config.detection?.device || "cuda", value: () => state.config.detection?.weights || "未指定" },
    { key: "ocr", name: "文字识别", description: "PaddleOCR ROI", glyph: "T", profile: "ocr", detail: () => state.config.ocr?.device || "cuda", value: () => `每 ${number(state.config.ocr?.interval, 6)} 帧` },
    { key: "sam", name: "精细分割", description: "SAM 2.1 Tiny", glyph: "◌", profile: "sam", runPath: "/segment", runLabel: "执行一次", detail: () => state.config.sam?.enabled ? "按需启用" : "未启用", value: () => state.config.sam?.weights || "未指定" },
    { key: "reasoner", name: "场景理解", description: "VLM / LLM reasoning", glyph: "✦", profile: "reasoner", runPath: "/reason", runLabel: "立即理解", detail: () => state.config.reasoner?.backend || "local", value: () => state.config.reasoner?.model || "未指定" },
    { key: "overlay", name: "屏幕 Overlay", description: "结构化结果绘制", glyph: "⊙", profile: null, detail: () => state.config.output?.overlay ? "已开启" : "已关闭", value: () => "无预览窗口" },
    { key: "input", name: "输入门控", description: "鼠标 / 键盘反馈", glyph: "⌁", profile: null, detail: () => state.config.automation?.dry_run ? "dry run" : "live", value: () => state.config.automation?.enabled ? "已授权" : "已阻止" }
  ];

  function moduleLine(meta) {
    const mod = state.modules?.[meta.key] || {};
    const status = moduleStatus(mod); const kind = statusKind(status);
    const line = document.createElement("div"); line.className = `module-line ${kind}`;
    const dot = document.createElement("span"); dot.className = `status-dot ${kind}`; line.appendChild(dot);
    const name = document.createElement("span"); name.className = "module-name"; name.textContent = meta.name; line.appendChild(name);
    const detail = document.createElement("small"); detail.textContent = status; line.appendChild(detail);
    return line;
  }

  function renderModules() {
    const summary = $("moduleSummary"); summary.replaceChildren(); moduleMeta.forEach((meta) => summary.appendChild(moduleLine(meta)));
    const cards = $("moduleCards"); cards.replaceChildren();
    moduleMeta.forEach((meta) => {
      const mod = state.modules?.[meta.key] || {}; const status = moduleStatus(mod); const kind = statusKind(status); const card = document.createElement("article"); card.className = "module-card";
      const head = document.createElement("div"); head.className = "module-card-head";
      const glyph = document.createElement("div"); glyph.className = "module-glyph"; glyph.textContent = meta.glyph; head.appendChild(glyph);
      const title = document.createElement("div"); const h = document.createElement("h3"); h.textContent = meta.name; const desc = document.createElement("p"); desc.textContent = meta.description; title.append(h, desc); head.appendChild(title);
      const statusEl = document.createElement("span"); statusEl.className = `module-status ${kind}`; statusEl.textContent = status; head.appendChild(statusEl); card.appendChild(head);
      const details = document.createElement("div"); details.className = "module-details"; details.appendChild(detailBox("当前参数", meta.detail())); details.appendChild(detailBox("状态信息", mod.message || meta.value())); card.appendChild(details);
      const foot = document.createElement("div"); foot.className = "module-card-foot"; const note = document.createElement("small"); note.textContent = mod.message || (meta.profile ? "准备模型可能需要下载或安装，请查看日志。" : "由运行时直接提供。"); foot.appendChild(note);
      const actions = document.createElement("div"); actions.className = "module-card-actions";
      const action = document.createElement("button"); action.className = "button button-secondary button-small"; action.textContent = meta.profile ? "准备模型" : "查看配置"; action.dataset.profile = meta.profile || ""; action.dataset.module = meta.key; action.dataset.openConfig = meta.profile ? "" : "1"; action.disabled = busy; actions.appendChild(action);
      if (meta.runPath) { const run = document.createElement("button"); run.className = "button button-ghost button-small"; run.textContent = meta.runLabel; run.dataset.run = meta.runPath; run.dataset.module = meta.key; run.disabled = busy; actions.appendChild(run); }
      foot.appendChild(actions); card.appendChild(foot); cards.appendChild(card);
    });
  }

  function detailBox(label, value) { const box = document.createElement("div"); box.className = "module-detail"; const key = document.createElement("span"); key.textContent = label; const val = document.createElement("b"); val.textContent = text(value); box.append(key, val); return box; }

  function renderVision() {
    const vision = state.vision || {}; $("visionFrame").textContent = `FRAME ${text(vision.frame_id, "—")}`;
    const result = $("visionResults"); result.replaceChildren(); const objects = Array.isArray(vision.objects) ? vision.objects : []; const texts = Array.isArray(vision.texts) ? vision.texts : [];
    if (!objects.length && !texts.length) { result.className = "result-stack empty-state"; result.textContent = "等待视觉管线输出"; } else { result.className = "result-stack"; objects.slice(0, 12).forEach((item) => result.appendChild(resultRow("OBJ", item.label || item.class || item.name || "object", item.confidence !== undefined ? `${(number(item.confidence) * 100).toFixed(0)}%` : `id ${text(item.track_id || item.id, "—")}`))); texts.slice(0, 12).forEach((item) => result.appendChild(resultRow("OCR", typeof item === "string" ? item : item.text || "", item.confidence !== undefined ? `${(number(item.confidence) * 100).toFixed(0)}%` : ""))); }
    const eventBox = $("eventResults"); eventBox.replaceChildren(); const events = Array.isArray(vision.events) ? vision.events : []; if (!events.length) { eventBox.className = "event-list empty-state"; eventBox.textContent = "暂无事件"; } else { eventBox.className = "event-list"; events.slice(0, 8).forEach((event) => { const row = document.createElement("div"); row.className = "event-row"; row.textContent = typeof event === "string" ? event : event.message || event.event || JSON.stringify(event); eventBox.appendChild(row); }); }
  }

  function resultRow(tag, label, value) { const row = document.createElement("div"); row.className = "result-row"; const badge = document.createElement("span"); badge.className = "result-tag"; badge.textContent = tag; const strong = document.createElement("strong"); strong.textContent = text(label, "未命名"); row.append(badge, strong); if (value) { const side = document.createElement("span"); side.textContent = value; row.appendChild(side); } return row; }

  function renderReasoning() {
    const reasoning = state.reasoning || {}; $("reasoningText").textContent = typeof reasoning.text === "string" ? reasoning.text || "等待模型输出…" : JSON.stringify(reasoning.text || {}, null, 2);
    const actions = Array.isArray(reasoning.actions) ? reasoning.actions : []; const list = $("actionsList"); list.replaceChildren(); $("actionMode").textContent = state.config.automation?.enabled && !state.config.automation?.dry_run ? "LIVE" : "DRY RUN"; $("actionMode").classList.toggle("live", state.config.automation?.enabled && !state.config.automation?.dry_run);
    if (!actions.length) { list.className = "action-list empty-state"; list.textContent = "暂无输入动作"; } else { list.className = "action-list"; actions.slice(0, 30).forEach((action) => { const row = document.createElement("div"); row.className = "action-row"; const icon = document.createElement("span"); icon.className = "action-icon"; icon.textContent = "↳"; const body = document.createElement("div"); const title = document.createElement("strong"); title.textContent = text(action.type || action.kind, "action"); const detail = document.createElement("small"); detail.textContent = action.text || action.key || action.button || (action.x !== undefined ? `x=${action.x}, y=${action.y}` : "已排队"); body.append(title, detail); row.append(icon, body); list.appendChild(row); }); }
    $("goalText").textContent = text(state.config.reasoner?.goal, "尚未设置目标");
  }

  function renderLastCommand() { const command = state.last_command; $("lastCommand").textContent = command ? `${text(command.text, "指令")} · ${text(command.message, command.ok === false ? "未执行" : "已接收")}` : "尚未发送指令"; }

  function renderLogs() {
    const logs = Array.isArray(state.logs) ? state.logs : []; const filtered = logFilter === "all" ? logs : logs.filter((entry) => String(entry.level || "info").toLowerCase() === logFilter); $("logCount").textContent = `${filtered.length} 条记录`; const list = $("logsList"); list.replaceChildren(); if (!filtered.length) { const empty = document.createElement("div"); empty.className = "empty-state"; empty.style.padding = "30px 16px"; empty.textContent = "暂无日志"; list.appendChild(empty); return; } filtered.slice().reverse().slice(0, 300).forEach((entry) => { const row = document.createElement("div"); row.className = "log-row"; const time = document.createElement("span"); time.className = "log-time"; time.textContent = text(entry.time, "—"); const level = String(entry.level || "info").toLowerCase(); const levelEl = document.createElement("span"); levelEl.className = `log-level ${level}`; levelEl.textContent = level.toUpperCase(); const msg = document.createElement("span"); msg.className = "log-message"; msg.textContent = text(entry.message, ""); row.append(time, levelEl, msg); list.appendChild(row); });
  }

  function appendLocalLog(level, message) { state.logs = [...(state.logs || []), { time: new Date().toLocaleTimeString("zh-CN", { hour12: false }), level, message }].slice(-300); renderLogs(); }

  function setValue(id, value) { const element = $(id); if (element) element.value = value ?? ""; }
  function setChecked(id, value) { const element = $(id); if (element) element.checked = Boolean(value); }
  function fillConfig() {
    const c = state.config || defaults.config; setValue("cfgCaptureFps", c.capture?.fps); setValue("cfgCaptureOutput", c.capture?.output); setChecked("cfgDetectionEnabled", c.detection?.enabled); setValue("cfgDetectionWeights", c.detection?.weights); setValue("cfgDetectionDevice", c.detection?.device); setValue("cfgDetectionImgsz", Array.isArray(c.detection?.imgsz) ? c.detection.imgsz.join(",") : c.detection?.imgsz); setValue("cfgDetectionConfidence", c.detection?.confidence); setChecked("cfgOcrEnabled", c.ocr?.enabled); setValue("cfgOcrInterval", c.ocr?.interval); setValue("cfgOcrDevice", c.ocr?.device); setChecked("cfgSamEnabled", c.sam?.enabled); setValue("cfgSamWeights", c.sam?.weights); setChecked("cfgReasonerEnabled", c.reasoner?.enabled); setValue("cfgReasonerBackend", c.reasoner?.backend); setValue("cfgReasonerInterval", c.reasoner?.interval); setValue("cfgReasonerModel", c.reasoner?.model); setValue("cfgReasonerEndpoint", c.reasoner?.endpoint); setValue("cfgReasonerGoal", c.reasoner?.goal); setChecked("cfgOverlay", c.output?.overlay); setChecked("cfgAutomation", c.automation?.enabled); setChecked("cfgDryRun", c.automation?.dry_run);
  }

  function readImageSize(value) { const parts = String(value || "").split(/[x,\s]+/).map((item) => Number(item)).filter((item) => Number.isFinite(item) && item >= 160); return parts.length > 1 ? parts.slice(0, 2) : (parts[0] || 544); }
  function readConfig() { return { capture: { fps: number($("cfgCaptureFps").value, 30), output: number($("cfgCaptureOutput").value, 0) }, detection: { enabled: $("cfgDetectionEnabled").checked, weights: $("cfgDetectionWeights").value.trim(), device: $("cfgDetectionDevice").value, imgsz: readImageSize($("cfgDetectionImgsz").value), confidence: number($("cfgDetectionConfidence").value, .4) }, ocr: { enabled: $("cfgOcrEnabled").checked, interval: number($("cfgOcrInterval").value, 6), device: $("cfgOcrDevice").value }, sam: { enabled: $("cfgSamEnabled").checked, weights: $("cfgSamWeights").value.trim() }, reasoner: { enabled: $("cfgReasonerEnabled").checked, backend: $("cfgReasonerBackend").value, interval: number($("cfgReasonerInterval").value, 1), model: $("cfgReasonerModel").value.trim(), endpoint: $("cfgReasonerEndpoint").value.trim(), goal: $("cfgReasonerGoal").value.trim() }, output: { overlay: $("cfgOverlay").checked }, automation: { enabled: $("cfgAutomation").checked, dry_run: $("cfgDryRun").checked } }; }

  async function submitCommand(command) { const input = $("commandInput"); const value = (command || input.value || "").trim(); if (!value || busy) return; input.value = ""; const result = await runAction("/command", { text: value }, null, q("#commandForm button")); if (result) appendLocalLog("info", `指令：${value}`); }

  function renderAll() { renderStatus(); renderRhythm(); renderModules(); renderVision(); renderReasoning(); renderLastCommand(); renderLogs(); }

  document.addEventListener("DOMContentLoaded", () => {
    $("modeSelect").addEventListener("change", async (event) => {
      const value = event.target.value;
      // Never POST a config write when the backend already reports this
      // profile: that was the source of the endless "已切换配置/配置已保存"
      // toast storm on every poll.
      if (profileMatches(value, state.profile)) return;
      const game = value === "muse_dash" ? "muse_dash" : value === "osu" ? "osu" : undefined;
      const result = await runAction("/config", { profile: value, ...(game ? { rhythm: { game } } : {}) }, `已切换到 ${value} 配置`, event.target);
      if (result && result.changed === false) { state.profile = value; renderStatus(); return; }
      if (result && result.ok === false) { event.target.value = text(state.profile, "desktop"); }
    });
    qa(".nav-item").forEach((item) => item.addEventListener("click", () => setView(item.dataset.view)));
    qa("[data-view-target]").forEach((item) => item.addEventListener("click", () => setView(item.dataset.viewTarget)));
    $("goalEditButton").addEventListener("click", () => setView("config"));
    $("startButton").addEventListener("click", (event) => runAction("/start", {}, "启动请求已发送", event.currentTarget));
    $("stopButton").addEventListener("click", (event) => runAction("/stop", {}, "停止请求已发送", event.currentTarget));
    $("emergencyButton").addEventListener("click", (event) => runAction("/emergency", {}, "紧急停止已发送", event.currentTarget, { force: true }));
    $("refreshButton").addEventListener("click", () => refreshState(true));
    $("reasonButton").addEventListener("click", (event) => runAction("/reason", {}, "理解请求已发送", event.currentTarget));
    $("rhythmStartButton").addEventListener("click", (event) => runAction("/rhythm/start", {}, "Rhythm 启动请求已发送", event.currentTarget));
    $("rhythmStopButton").addEventListener("click", (event) => runAction("/rhythm/stop", {}, "Rhythm 停止请求已发送", event.currentTarget));
    $("commandForm").addEventListener("submit", (event) => { event.preventDefault(); submitCommand(); });
    qa("[data-command]").forEach((button) => button.addEventListener("click", () => submitCommand(button.dataset.command)));
    $("moduleCards").addEventListener("click", (event) => { const button = event.target.closest("button"); if (!button) return; if (button.dataset.openConfig) { setView("config"); return; } if (button.dataset.run) { runAction(button.dataset.run, {}, `${button.dataset.module === "sam" ? "分割" : "理解"}请求已发送`, button); return; } if (!button.dataset.profile) return; const profile = button.dataset.profile; showToast(`正在准备 ${profile} 模型，安装或下载可能需要较长时间，请查看运行日志。`, "warn", 6000); runAction("/prepare", { profile }, null, button); });
    $("configForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const next = readConfig();
      // Only submit when the values actually differ from the active config.
      if (!configDiffers(state.config, next)) { $("configStatus").textContent = "无变化"; window.setTimeout(() => { $("configStatus").textContent = ""; }, 2000); return; }
      const result = await runAction("/config", next, "配置已提交", event.submitter);
      if (result && result.ok !== false) { $("configStatus").textContent = "已保存"; window.setTimeout(() => { $("configStatus").textContent = ""; }, 3000); }
    });
    $("reloadConfigButton").addEventListener("click", () => { fillConfig(); $("configStatus").textContent = "已撤销本地修改"; });
    $("clearLogViewButton").addEventListener("click", () => { $("logsList").replaceChildren(); $("logCount").textContent = "视图已清空"; });
    qa("[data-log-filter]").forEach((button) => button.addEventListener("click", () => { logFilter = button.dataset.logFilter; qa("[data-log-filter]").forEach((item) => item.classList.toggle("active", item === button)); renderLogs(); }));
    window.addEventListener("beforeunload", () => { if (pollTimer) window.clearInterval(pollTimer); });
    fillConfig(); renderAll(); bootstrap();
  });
})();
