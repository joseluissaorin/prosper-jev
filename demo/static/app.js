const $ = (id) => document.getElementById(id);
let ws = null, micCtx = null, micStream = null, node = null, playCtx = null;
let nextTime = 0, sources = [], partialEl = null, inCall = false;

const LABELS = {
  act: "Qué hace", finished: "¿Ha terminado?", intent: "Qué quiere", emergency: "Urgencia (112)",
  urgent_today: "Médico hoy", manipulation: "Manipulación", offscript: "Fuera de guion", third_party: "Para otra persona",
  relation: "Para quién", lang: "Idioma", service: "Servicio", slot: "Hueco elegido", day_anchor: "Día",
  part_of_day: "Franja", doctor_pref: "Médico pedido", gives_name: "Dice su nombre",
};
const ACTS = { confirm: "confirma", reject: "rechaza", correct: "corrige", provide_info: "da un dato", ask_question: "pregunta",
  backchannel: "muletilla", end_call: "se despide", unclear: "no se entiende" };
const INTENTS = { book: "pedir cita", reschedule: "cambiar cita", cancel: "anular", info: "información", medical_now: "médico ya", unclear: "aún no está claro" };
const RISK = { emergency: 0.85, urgent_today: 0.8, manipulation: 0.8, offscript: 0.6, third_party: 0.7, finished: 0.35 };
const NOULS = ["finished", "emergency", "urgent_today", "manipulation", "offscript", "third_party"];
const CHOICES = ["act", "intent", "lang", "relation", "service", "slot", "day_anchor", "part_of_day"];

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

function addMsg(who, text, meta, extra = "") {
  const m = el("div", `msg ${who} ${extra}`);
  m.append(document.createTextNode(text));
  if (meta) m.append(el("span", "meta", meta));
  $("transcript").append(m);
  $("transcript").scrollTop = 1e9;
  return m;
}

function setPartial(text) {
  if (!partialEl) partialEl = addMsg("caller", text, "", "partial");
  else partialEl.firstChild.textContent = text;
  $("transcript").scrollTop = 1e9;
}

function finalCaller(text, meta) {
  if (partialEl) { partialEl.remove(); partialEl = null; }
  addMsg("caller", text, meta);
}

function renderBars(j, ms, hedged, phase, text) {
  $("said").textContent = `«${text}»`;
  $("jevMeta").textContent = `${phase} · ${ms} ms${hedged ? " · con petición duplicada" : ""}`;
  const box = $("bars"); box.innerHTML = "";
  for (const k of CHOICES) {
    const a = j[k]; if (!a) continue;
    const names = k === "act" ? ACTS : k === "intent" ? INTENTS : {};
    const row = el("div", "choice");
    row.append(el("span", "muted", `${LABELS[k] || k}: `));
    row.append(el("b", "", `${names[a.c] || a.c}`));
    row.append(el("span", "", ` ${a.conf.toFixed(2)} `));
    const alt = a.top.filter(([o]) => o !== a.c && a.top.length).slice(0, 2).map(([o, p]) => `${names[o] || o} ${p.toFixed(2)}`).join(" · ");
    if (alt) row.append(el("span", "alt", `(${alt})`));
    box.append(row);
  }
  for (const k of NOULS) {
    const a = j[k]; if (!a) continue;
    const r = el("div", `bar ${["emergency", "urgent_today", "manipulation"].includes(k) ? "risk" : ""}`);
    r.append(el("span", "lbl", LABELS[k] || k));
    const tr = el("div", "track"); const f = el("div", "fill"); f.style.width = `${Math.round(a.p * 100)}%`; tr.append(f);
    if (RISK[k] != null) { const t = el("div", "thr"); t.style.left = `${RISK[k] * 100}%`; tr.append(t); }
    r.append(tr); r.append(el("span", "val", a.p.toFixed(2)));
    box.append(r);
  }
}

const VALUES = {
  pending: { need: "saber qué necesita", identity: "identificar al paciente", identity_dob: "fecha de nacimiento", disambiguate: "distinguir entre fichas",
    new_phone: "teléfono para el alta", service: "especialidad", when: "día y hora", choose_slot: "elegir hueco", confirm: "confirmar la lectura",
    which_appt: "qué cita", waitlist: "lista de espera", emergency_check: "comprobar urgencia", anything_else: "¿algo más?", verified: "identificado" },
  intent: { book: "pedir cita", reschedule: "cambiar cita", cancel: "anular cita", info: "información", medical_now: "médico ya" },
  relation: { self: "para sí", child: "un hijo o hija", parent: "su padre o madre", partner: "su pareja", other_relative: "otro familiar", unrelated: "alguien que no es familiar" },
  outcome: { booked: "reservada", rescheduled: "cambiada", cancelled: "anulada", refused: "rechazada", escalated: "derivada", no_availability: "sin disponibilidad",
    identity_unresolved: "identidad sin resolver", info_only: "solo información", no_action: "sin acción" },
  service: { medicina_general: "medicina general", pediatria: "pediatría", cardiologia: "cardiología", traumatologia: "traumatología", ginecologia: "ginecología",
    dermatologia: "dermatología (no se ofrece)", psiquiatria: "psiquiatría (no se ofrece)", odontologia: "odontología (no se ofrece)", otro_no_ofrecido: "otro (no se ofrece)" },
};
const STATE_LABELS = { lang: "Idioma", pending: "Pendiente", intent: "Intención", relation: "Para quién", patient: "Paciente",
  candidates: "Candidatas", dob_heard: "Nacimiento oído", service: "Servicio", pref: "Preferencias", offered: "Ofrecido",
  chosen: "Elegido", outcome: "Resultado", reason: "Motivo", flags: "Marcas" };
function renderState(s) {
  const dl = $("state"); dl.innerHTML = "";
  for (const [k, lbl] of Object.entries(STATE_LABELS)) {
    let v = s[k];
    if (v == null || (Array.isArray(v) && !v.length) || (typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length)) continue;
    if (k === "pref") v = Object.entries(v).filter(([, x]) => x && x !== "any" && x !== "none").map(([a, b]) => `${a}: ${b}`).join(", ") || "ninguna";
    else if (k === "offered") v = Object.values(v).join(" | ");
    else if (Array.isArray(v)) v = v.join(" · ");
    else if (k === "lang") v = `${v}${s.lang_locked ? " (fijado)" : ""}`;
    else if (VALUES[k] && VALUES[k][v]) v = VALUES[k][v];
    dl.append(el("dt", "", lbl)); dl.append(el("dd", "", String(v)));
  }
}

function setLat(stage, ms) {
  let dd = document.querySelector(`[data-lat="${stage}"]`);
  if (!dd) { $("lat").append(el("dt", "", stage)); dd = el("dd"); dd.dataset.lat = stage; $("lat").append(dd); }
  dd.textContent = ms == null ? "—" : `${ms} ms`;
}

function addGate(e) {
  const li = el("li");
  li.append(el("span", e.ok ? "ok" : "ko", e.ok ? "✓" : "✗"));
  li.append(el("span", "", `${e.name}: ${e.detail}`));
  $("gates").append(li);
}

function addTrace(text) {
  const li = el("li", "", text); $("trace").prepend(li);
}

// ------------------------------------------------------------------ audio de salida (24 kHz)
function playChunk(buf) {
  if (!playCtx) playCtx = new AudioContext({ sampleRate: 24000 });
  const i16 = new Int16Array(buf); const f = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) f[i] = i16[i] / 32768;
  const ab = playCtx.createBuffer(1, f.length, 24000); ab.copyToChannel(f, 0);
  const src = playCtx.createBufferSource(); src.buffer = ab; src.connect(playCtx.destination);
  const t = Math.max(playCtx.currentTime + 0.02, nextTime); src.start(t); nextTime = t + ab.duration;
  sources.push(src); src.onended = () => { sources = sources.filter((s) => s !== src); };
}
function stopAudio() { for (const s of sources) { try { s.stop(); } catch {} } sources = []; nextTime = 0; speechSynthesis.cancel(); }

// ------------------------------------------------------------------ llamada
async function startCall() {
  $("transcript").innerHTML = ""; $("gates").innerHTML = ""; $("trace").innerHTML = ""; $("lat").innerHTML = "";
  $("report").textContent = "Aparece al colgar."; $("report").classList.add("muted");
  playCtx = playCtx || new AudioContext({ sampleRate: 24000 }); await playCtx.resume();
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onmessage = onMessage;
  ws.onclose = () => endUi("Llamada terminada");
  await new Promise((r) => (ws.onopen = r));
  ws.send(JSON.stringify({ type: "start", lang: $("lang").value, tts: $("tts").checked }));
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    micCtx = new AudioContext({ sampleRate: 16000 });
    await micCtx.audioWorklet.addModule("/static/capture-worklet.js");
    node = new AudioWorkletNode(micCtx, "capture");
    node.port.onmessage = (e) => {
      if (ws && ws.readyState === 1) ws.send(e.data.pcm);
      $("meter").style.width = `${Math.min(100, e.data.peak * 140)}%`;
    };
    micCtx.createMediaStreamSource(micStream).connect(node);
  } catch (err) {
    addTrace(`sin micrófono (${err.message}): usa la caja de texto`);
  }
  inCall = true;
  $("callBtn").textContent = "Colgar"; $("callBtn").classList.add("hang"); $("status").textContent = "En llamada";
}

function hangup() { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "hangup" })); setTimeout(() => ws && ws.close(), 1500); endUi("Colgando…"); }

function endUi(msg) {
  inCall = false;
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  if (micCtx) micCtx.close(); micCtx = null; micStream = null;
  $("callBtn").textContent = "Llamar"; $("callBtn").classList.remove("hang"); $("status").textContent = msg; $("meter").style.width = "0";
  loadAgenda();
}

function onMessage(ev) {
  if (ev.data instanceof ArrayBuffer) { playChunk(ev.data); return; }
  const m = JSON.parse(ev.data);
  switch (m.type) {
    case "call_started": addTrace(`llamada ${m.call_id}`); break;
    case "vad": $("vad").textContent = m.state === "speech" ? "hablando" : "en silencio"; $("vad").classList.toggle("on", m.state === "speech"); break;
    case "partial": setPartial(m.text); break;
    case "final": finalCaller(m.text, m.typed ? "escrito" : m.stt_ms != null ? `transcrito ${m.stt_ms} ms tras el fin de voz` : ""); if (m.stt_ms != null) setLat("STT definitivo", m.stt_ms); break;
    case "perception": renderBars(m.j, m.ms, m.hedged, m.phase, m.text); setLat(`Jev (${m.phase})`, m.ms); break;
    case "agent": {
      const meta = `${m.source}${m.act ? " · " + m.act : ""}${m.audio ? (m.cached ? " · voz en caché" : " · voz en vivo") : ""}`;
      addMsg("agent", m.text, meta, m.source === "sistema 2" ? "s2" : "");
      if (!m.audio && $("tts").checked) speakFallback(m.text);
      break;
    }
    case "tts_fallback": speakFallback(m.text); break;
    case "system2": addTrace(`Sistema 2: ${m.ok ? "pasa el filtro" : "bloqueado"} · LLM ${m.ms_llm} ms · filtro ${m.ms_guard} ms`); break;
    case "stop_audio": stopAudio(); addTrace(`voz cortada: ${m.reason}`); break;
    case "trace": {
      const e = m.event;
      if (e.kind === "gate") addGate(e);
      else if (e.kind !== "perception") addTrace(`${e.t}s ${e.kind} ${JSON.stringify(Object.fromEntries(Object.entries(e).filter(([k]) => !["t", "kind"].includes(k))))}`);
      break;
    }
    case "state": renderState(m.state); break;
    case "latency": setLat(m.stage, m.ms); break;
    case "log": addTrace(m.msg); break;
    case "report":
      $("report").textContent = JSON.stringify(m.report, null, 1); $("report").classList.remove("muted");
      loadAgenda();
      if (m.report.ended_by === "goodbye") setTimeout(() => { if (ws) ws.close(); }, 4000);
      break;
  }
}

function speakFallback(text) {
  const u = new SpeechSynthesisUtterance(text); u.lang = { es: "es-ES", ca: "ca-ES", gl: "gl-ES", eu: "eu-ES", en: "en-GB" }[$("lang").value] || "es-ES";
  speechSynthesis.speak(u);
}

async function loadAgenda() {
  const r = await fetch("/api/clinic").then((r) => r.json());
  const box = $("agenda"); box.innerHTML = "";
  for (const a of r.appointments) {
    const d = el("div", `a ${a.status === "cancelled" ? "cancelled" : ""}`);
    d.append(el("span", "muted", a.start.replace("T", " "))); d.append(el("span", "", `${a.patient} · ${a.doctor}`)); box.append(d);
  }
  box.append(el("div", "muted", `Jev: ${r.stats.jev_calls} peticiones · ${r.stats.jev_tokens} tokens · ${r.stats.jev_cost_usd} $`));
}

$("callBtn").onclick = () => (inCall ? hangup() : startCall());
$("typeForm").onsubmit = (e) => {
  e.preventDefault(); const t = $("typeInput").value.trim(); if (!t || !ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify({ type: "text", text: t })); $("typeInput").value = "";
};
$("tts").onchange = () => ws && ws.readyState === 1 && ws.send(JSON.stringify({ type: "tts", on: $("tts").checked }));
$("refreshAgenda").onclick = loadAgenda;
loadAgenda();
