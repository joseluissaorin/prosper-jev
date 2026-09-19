/* Llamar al agente desde el navegador, como si fuera una llamada de teléfono.
 *
 * Habla el mismo protocolo que usa Prosper (Twilio Media Streams): el micrófono se pasa a 8 kHz y a µ-law en tramas de
 * 20 ms, y la voz del agente vuelve igual. Va a la instancia LOCAL del agente (la de la clínica simulada): una reserva
 * hecha desde aquí nunca llega a la API real de Prosper. La voz del agente se reproduce por un <audio> para que la
 * cancelación de eco del navegador la tenga de referencia; con auriculares, mejor.
 */
(() => {
  "use strict";
  const CFG = window.ARENA_CONFIG || {};
  const qs = new URLSearchParams(location.search);
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("arena") || "{}"); } catch {}
  const CALL_URL = qs.get("call") || saved.call || CFG.call || "";
  const TOKEN = qs.get("t") || saved.t || CFG.token || "";
  if (!CALL_URL) return;                                  // sin instancia a la que llamar, no hay botón

  // pacientes de prueba de la clínica simulada (para identificarse por la línea o diciéndolo)
  const FICHAS = [
    { id: "marta", nombre: "Marta Ruiz Navarro", linea: "+34612345678", dice: "nacida el 12 de abril de 1987 · Sanitas" },
    { id: "mario", nombre: "Mario García López", linea: null, dice: "DNI 39958838 H · nacido el 30 de julio de 1975 · Sanitas" },
    { id: "laura", nombre: "Laura Ruiz Gómez", linea: "+34655667788", dice: "nacida el 14 de septiembre de 1978 · AXA · tiene dos citas" },
    { id: "oculto", nombre: "Número oculto", linea: null, dice: "te pedirán nombre y DNI o fecha de nacimiento" },
  ];

  const $ = (id) => document.getElementById(id);
  const panel = $("llamar");
  panel.hidden = false;
  document.body.classList.add("con-llamada");
  let ficha = FICHAS[0];
  $("fichas").innerHTML = FICHAS.map((f) =>
    `<button type="button" data-id="${f.id}" class="${f === ficha ? "activa" : ""}">${f.nombre}</button>`).join("");
  const pintarFicha = () => {
    $("ficha").textContent = `${ficha.nombre}${ficha.linea ? " · llama desde su número" : ""} · ${ficha.dice}`;
    document.querySelectorAll("#fichas button").forEach((b) => b.classList.toggle("activa", b.dataset.id === ficha.id));
  };
  $("fichas").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || enLlamada) return;
    ficha = FICHAS.find((f) => f.id === b.dataset.id) || ficha;
    pintarFicha();
  });
  pintarFicha();

  // ------------------------------------------------------------------ µ-law (G.711)
  const BIAS = 0x84, CLIP = 32635;
  function linToMu(s) {
    let sign = (s >> 8) & 0x80;
    if (sign) s = -s;
    if (s > CLIP) s = CLIP;
    s += BIAS;
    let exp = 7;
    for (let mask = 0x4000; (s & mask) === 0 && exp > 0; exp--, mask >>= 1);
    const mant = (s >> (exp + 3)) & 0x0f;
    return ~(sign | (exp << 4) | mant) & 0xff;
  }
  const MU = new Int16Array(256);
  for (let i = 0; i < 256; i++) {
    const u = ~i & 0xff, sign = u & 0x80, exp = (u >> 4) & 0x07, mant = u & 0x0f;
    let s = ((mant << 3) + BIAS) << exp;
    s -= BIAS;
    MU[i] = sign ? -s : s;
  }
  const b64 = (bytes) => { let s = ""; for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]); return btoa(s); };
  const unb64 = (str) => { const s = atob(str), out = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i); return out; };

  // captura: el worklet entrega el audio del micrófono a la frecuencia del contexto
  const WORKLET = `class Mic extends AudioWorkletProcessor {
    process(inputs) { const ch = inputs[0] && inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; } }
  registerProcessor("mic", Mic);`;

  let ctx = null, stream = null, node = null, ws = null, enLlamada = false;
  let sid = "", callSid = "", seq = 0, chunk = 0, resto = new Float32Array(0), frac = 0;
  let playHead = 0, fuentes = [], salida = null, audioEl = null, nivel = 0;

  function frame8k(input, sr) {
    // remuestreo lineal a 8 kHz, conservando la fase entre bloques
    const step = sr / 8000, out = [];
    const buf = new Float32Array(resto.length + input.length);
    buf.set(resto); buf.set(input, resto.length);
    let pos = frac;
    while (pos + 1 < buf.length) {
      const i = Math.floor(pos), f = pos - i;
      out.push(buf[i] * (1 - f) + buf[i + 1] * f);
      pos += step;
    }
    const keep = Math.floor(pos);
    resto = buf.slice(keep); frac = pos - keep;
    return out;
  }
  let cola8k = [];
  function enviarMic(ch) {
    if (!ws || ws.readyState !== 1) return;
    let pico = 0;
    for (let i = 0; i < ch.length; i++) pico = Math.max(pico, Math.abs(ch[i]));
    nivel = Math.max(pico, nivel * 0.85);
    cola8k = cola8k.concat(frame8k(ch, ctx.sampleRate));
    while (cola8k.length >= 160) {
      const trama = cola8k.splice(0, 160), mu = new Uint8Array(160);
      for (let i = 0; i < 160; i++) mu[i] = linToMu(Math.max(-32768, Math.min(32767, Math.round(trama[i] * 32767))));
      chunk += 1; seq += 1;
      ws.send(JSON.stringify({ event: "media", sequenceNumber: String(seq), streamSid: sid,
        media: { track: "inbound", chunk: String(chunk), timestamp: String(chunk * 20), payload: b64(mu) } }));
    }
  }

  function reproducir(payload) {
    const mu = unb64(payload), n = mu.length, sr = ctx.sampleRate, m = Math.round(n * sr / 8000);
    const buf = ctx.createBuffer(1, m, sr), d = buf.getChannelData(0);
    for (let j = 0; j < m; j++) {                          // de 8 kHz a la frecuencia del contexto
      const x = j * 8000 / sr, i = Math.floor(x), f = x - i;
      const a = MU[mu[Math.min(i, n - 1)]], b = MU[mu[Math.min(i + 1, n - 1)]];
      d[j] = (a * (1 - f) + b * f) / 32768;
    }
    const src = ctx.createBufferSource();
    src.buffer = buf; src.connect(salida);
    playHead = Math.max(playHead, ctx.currentTime + 0.05);
    src.start(playHead); playHead += buf.duration;
    fuentes.push(src);
    src.onended = () => { fuentes = fuentes.filter((x) => x !== src); };
  }
  function callar() {                                     // «clear»: el agente se calla al instante (interrupción)
    for (const s of fuentes) { try { s.stop(); } catch {} }
    fuentes = []; playHead = 0;
  }

  const hex = (n) => [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, "0")).join("");

  async function llamar() {
    const btn = $("btn-llamar");
    btn.disabled = true; btn.textContent = "Conectando…";
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      await ctx.resume();
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
      const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
      await ctx.audioWorklet.addModule(url);
      const mic = ctx.createMediaStreamSource(stream);
      node = new AudioWorkletNode(ctx, "mic");
      node.port.onmessage = (e) => enviarMic(e.data);
      mic.connect(node);
      // la voz del agente sale por un <audio>: así la cancelación de eco la reconoce
      salida = ctx.createMediaStreamDestination();
      audioEl = new Audio(); audioEl.autoplay = true; audioEl.srcObject = salida.stream;
      await audioEl.play().catch(() => {});
    } catch (e) {
      btn.disabled = false; btn.textContent = "Llamar";
      colgar(false);
      $("ficha").textContent = "No hay acceso al micrófono: revisa el permiso del navegador.";
      return;
    }
    sid = "MZ" + hex(16); callSid = "CA" + hex(16); seq = 0; chunk = 0; resto = new Float32Array(0); frac = 0; cola8k = [];
    const sep = CALL_URL.includes("?") ? "&" : "?";
    ws = new WebSocket(CALL_URL + (TOKEN ? `${sep}t=${encodeURIComponent(TOKEN)}` : ""));
    ws.onopen = () => {
      const params = { call_id: callSid, ...(ficha.linea ? { from_number: ficha.linea } : {}) };
      ws.send(JSON.stringify({ event: "connected", protocol: "Call", version: "1.0.0" }));
      ws.send(JSON.stringify({ event: "start", sequenceNumber: String(++seq), streamSid: sid, start: {
        streamSid: sid, accountSid: "ACweb", callSid, tracks: ["inbound"], customParameters: params,
        mediaFormat: { encoding: "audio/x-mulaw", sampleRate: 8000, channels: 1 } } }));
      enLlamada = true; panel.classList.add("en-llamada");
      btn.disabled = false; btn.textContent = "Colgar";
      window.ARENA && window.ARENA.seguirLlamada(CALL_URL.replace(/\/ws(\?.*)?$/, "/monitor"), TOKEN, callSid);
    };
    ws.onmessage = (m) => {
      let e; try { e = JSON.parse(m.data); } catch { return; }
      if (e.event === "media" && e.media && e.media.payload) reproducir(e.media.payload);
      else if (e.event === "clear") callar();
    };
    ws.onclose = (e) => {
      colgar(false);
      if (e.code === 4401 || e.code === 1006 && !enLlamada) $("ficha").textContent = "No se pudo llamar: el enlace no lleva un token válido o la recepción no responde.";
    };
  }

  function colgar(avisar = true) {
    if (avisar && ws && ws.readyState === 1) {
      try { ws.send(JSON.stringify({ event: "stop", sequenceNumber: String(++seq), streamSid: sid, stop: { accountSid: "ACweb", callSid } })); } catch {}
    }
    try { ws && ws.close(); } catch {}
    ws = null; enLlamada = false; callar();
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (node) node.disconnect();
    if (ctx) ctx.close().catch(() => {});
    ctx = null; stream = null; node = null;
    panel.classList.remove("en-llamada");
    const btn = $("btn-llamar"); btn.disabled = false; btn.textContent = "Llamar";
    pintarFicha();
  }

  $("btn-llamar").addEventListener("click", () => (enLlamada ? colgar(true) : llamar()));

  // medidor del micrófono
  (function medir() {
    const m = $("nivel");
    if (m) m.style.transform = `scaleX(${enLlamada ? Math.min(1, nivel * 3) : 0})`;
    nivel *= 0.9;
    requestAnimationFrame(medir);
  })();
})();
