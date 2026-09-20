/* La portada se vuelve la llamada.
 *
 * Llamar: el micrófono se pasa a 8 kHz y a µ-law en tramas de 20 ms (el protocolo de Twilio Media Streams, el mismo
 * que usa el teléfono de verdad) y va por /llamada; la voz de la recepcionista vuelve igual. Por /monitor llegan, en
 * directo, los juicios de Jev y las decisiones del núcleo: con eso se compone el acta mientras se habla.
 * Sin micrófono también hay función: se reproduce una llamada real con sus tiempos.
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const A = window.Acta, arena = () => window.Arena || { forma() {}, onda() {}, energia() {} };

  // pacientes de prueba de la clínica simulada (para identificarse por la línea o diciéndolo)
  const FICHAS = [
    { id: "marta", nombre: "Marta Ruiz Navarro", linea: "+34612345678", dice: "Llama desde su número: la reconocen. Nacida el 12-4-1987 · Sanitas" },
    { id: "laura", nombre: "Laura Ruiz Gómez", linea: "+34655667788", dice: "Tiene dos citas: pruebe a cambiar o anular una. Nacida el 14-9-1978 · AXA" },
    { id: "mario", nombre: "Mario García López", linea: null, dice: "Número oculto: diga su nombre y su DNI, 39958838 H · Sanitas" },
    { id: "nadie", nombre: "Alguien que no es paciente", linea: null, dice: "Le pedirán los datos para darle de alta, o pruebe a sacarle los de otro" },
  ];
  let ficha = FICHAS[0];

  const hoja = A.crear($("pliego-acta"));
  const estado = (html) => { $("pliego-estado").innerHTML = html; };
  const ahora = (html) => { $("pliego-ahora").innerHTML = html; };

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
  const WORKLET = `class Mic extends AudioWorkletProcessor {
    process(inputs) { const ch = inputs[0] && inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; } }
  registerProcessor("mic", Mic);`;

  let ctx = null, stream = null, node = null, ws = null, mon = null, enLlamada = false, t0 = 0, reloj = null;
  let sid = "", callSid = "", seq = 0, chunk = 0, resto = new Float32Array(0), frac = 0, cola8k = [];
  let playHead = 0, fuentes = [], salida = null, audioEl = null, nivel = 0;
  let eventos = [], jctx = {}, glosasPend = null, informe = null, idioma = "", cierreTimer = null, turnos = 0, jevMs = [], espec = 0;

  function frame8k(input, sr) {
    const step = sr / 8000, out = [];
    const buf = new Float32Array(resto.length + input.length);
    buf.set(resto); buf.set(input, resto.length);
    let pos = frac;
    while (pos + 1 < buf.length) { const i = Math.floor(pos), f = pos - i; out.push(buf[i] * (1 - f) + buf[i + 1] * f); pos += step; }
    const keep = Math.floor(pos);
    resto = buf.slice(keep); frac = pos - keep;
    return out;
  }
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
  function reproducirVoz(payload) {
    const mu = unb64(payload), n = mu.length, sr = ctx.sampleRate, m = Math.round(n * sr / 8000);
    const buf = ctx.createBuffer(1, m, sr), d = buf.getChannelData(0);
    for (let j = 0; j < m; j++) {
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
  function callar() { for (const s of fuentes) { try { s.stop(); } catch {} } fuentes = []; playHead = 0; }
  const hex = (n) => [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, "0")).join("");
  const t = () => (performance.now() - t0) / 1000;

  // ------------------------------------------------------------------ del monitor del agente al acta
  const hora = (iso) => { try { return new Date(iso).toLocaleString("es-ES", { weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit", timeZone: "Europe/Madrid" }); } catch { return iso; } };
  const num = (s) => { const m = String(s).match(/(\d[.,]\d+)/); return m ? parseFloat(m[1].replace(",", ".")) : undefined; };
  function checksDePuerta(detail, ok) {
    const out = [];
    for (const p of String(detail || "").split("·").map((s) => s.trim()).filter(Boolean)) {
      if (/leíd[ao]\s*=/.test(p)) out.push({ k: "leída en voz alta", ok: /=\s*True/i.test(p) });
      else if (/elige/.test(p)) out.push({ k: "elige esa (Jev)", v: num(p), ok: (num(p) ?? 1) >= 0.5 });
      else if (/«sí»/.test(p)) out.push({ k: "«sí» claro", v: num(p.split("(")[0]), ok });
      else if (p.length < 70) out.push({ k: p, ok });
    }
    return out.length ? out : [{ k: "«sí» claro a lo leído", ok }];
  }
  function anotar(ev) { ev.t = +t().toFixed(2); eventos.push(ev); hoja.evento(ev); return ev; }
  function marca(clase, texto, ok) { anotar({ tipo: "marca", clase, texto, ...(ok === undefined ? {} : { ok }) }); }
  const fallo = (r) => /error|not found|blocked|denied|refus|no se|missing|required|not allowed|needs/i.test(String(r || ""));
  const ESCRITO = { book: "Cita reservada", reschedule: "Cita cambiada", cancel: "Cita anulada", register: "Alta hecha" };

  function traza(e) {
    const k = e.kind;
    if (k === "gate" && e.name === "identidad" && e.ok) {
      const quien = A.mask((e.detail || "").replace(/^P\d+\s*/, "").split("·")[0].trim());
      arena().forma("identifica", 1600, "escucha"); ahora(`Ficha encontrada: <em>${A.esc(quien)}</em>.`);
      marca("identidad", `Ficha encontrada: ${quien}`, true);
    } else if (k === "availability") {
      arena().forma("busca"); ahora(`Mira la agenda: <em>${e.found || 0} huecos</em> posibles.`);
      marca("agenda", `Agenda: ${e.found || 0} huecos posibles`);
    } else if (k === "offer") {
      arena().forma("ofrece"); jctx.hayOferta = true; ahora(`Ofrece el <em>${A.esc(hora(e.slot))}</em>.`);
      marca("oferta", `Ofrece: ${hora(e.slot)}`);
    } else if (k === "offer_rejected") {
      arena().forma("busca"); ahora("No le encaja: vuelve a buscar."); marca("rechazo", "No le encaja: vuelve a buscar");
    } else if (k === "gate" && e.name === "puerta") {
      anotar({ tipo: "puerta", ok: !!e.ok, checks: checksDePuerta(e.detail, !!e.ok) });
      ahora(e.ok ? "La puerta se abre: hay un «sí» claro a lo leído." : "La puerta espera un «sí» claro antes de escribir.");
    } else if (k === "gate" && e.name === "envío" && e.ok) {
      const verbo = (e.detail || "").split(" ")[0];
      if (ESCRITO[verbo]) { arena().forma("hecho"); anotar({ tipo: "escrito", accion: verbo.toUpperCase(), texto: ESCRITO[verbo] + " en la agenda de pruebas" }); ahora(`<em>${ESCRITO[verbo]}.</em>`); }
      else if (verbo === "escalate") marca("alarma", "Declarado: urgencia derivada al 112");
      else marca("limite", "Declarado sin gestión, con su motivo");
    } else if (k === "gate" && e.name === "triaje" && !e.ok) {
      arena().forma("alarma"); ahora("Urgencia: <em>que llame al 112</em>. No se reserva."); marca("alarma", "Urgencia: deriva al 112 y no reserva");
    } else if (k === "gate" && (e.name === "límites" || e.name === "negativa") && !e.ok) {
      arena().forma("declina", 2500, "escucha"); ahora("Eso no se puede hacer: <em>se declina</em>."); marca("limite", "Límites: eso no se hace, y se declina");
    } else if (k === "speculation_reused") {
      espec += 1;
      marca("especulacion", e.head_start_ms ? `La respuesta ya estaba pensada ${A.coma(e.head_start_ms / 1000, 1)} s antes de que callara` : "La respuesta ya estaba pensada mientras hablaba");
    } else if (/_invented$/.test(k || "")) {
      marca("invento", "El planificador quiso fijar un dato que nadie dijo: el núcleo lo tiró");
    } else if (k === "system2") {
      marca("sistema2", "Pregunta abierta: contesta el Sistema 2, con la guardia de Jev"); ahora("Pregunta fuera de guion: responde el <em>Sistema 2</em>.");
    } else if (k === "lang" && e.lang && e.lang !== idioma) {
      const antes = idioma; idioma = e.lang;
      if (antes) marca("idioma", `Cambia de idioma: ${A.IDIOMA[e.lang] || e.lang}`);
    } else if (k === "jev_down") {
      marca("oido", "Jev no responde: Flash-Lite hace de Sistema 1 mientras tanto");
    } else if (k === "second_opinion" && e.used) {
      marca("oido", "Segunda opinión sobre el audio: cifras recuperadas");
    } else if (k === "undo") {
      marca("deshacer", "Seguía hablando: deshace y une las dos mitades del turno");
    } else if (k === "tool") {
      const ok = !fallo(e.result);
      if (e.name === "find_slots") { arena().forma("busca"); ahora("Mira la agenda."); }
      else if (e.name === "identify_patient" && !ok) ahora("Comprueba quién es…");
      else if (e.name === "clinic_info") { ahora("Lo mira en el catálogo de la clínica."); marca("agenda", "Lo mira en el catálogo de la clínica: los hechos no salen del modelo"); }
      else if (e.name === "decline") { arena().forma("declina", 2500, "escucha"); }
      else if (e.name === "list_appointments") { arena().forma("identifica", 1400, "escucha"); marca("agenda", "Mira las citas que ya tiene"); }
    }
  }

  function recibir(e) {
    const tipo = e.type;
    if (tipo === "call_started") { arena().forma("escucha"); ahora("Descuelga."); }
    else if (tipo === "vad") { if (e.state === "speech") { arena().energia(0.4); if (!["ofrece", "hecho", "alarma"].includes(arena().actual)) arena().forma("escucha"); } }
    else if (tipo === "partial") { arena().energia(0.25); hoja.parcial(e.text, t()); }
    else if (tipo === "final") {
      turnos += 1;
      const ev = anotar({ tipo: "dice", quien: "llama", texto: e.text });
      if (glosasPend) { ev.glosas = glosasPend.glosas; ev.ms = glosasPend.ms; hoja.glosar(ev.glosas, ev.ms); glosasPend = null; }
    } else if (tipo === "perception") {
      const copia = { ...jctx };
      const glosas = A.glosasDeJev(e.j || {}, e.phase === "parcial" ? copia : jctx);
      if (e.ms > 0) jevMs.push(e.ms);
      if (e.phase === "parcial") { hoja.glosar(glosas, e.ms); }
      else {
        const ult = [...eventos].reverse().find((x) => x.tipo === "dice" && x.quien === "llama");
        if (ult && !ult.glosas) { ult.glosas = glosas; ult.ms = e.ms; hoja.glosar(glosas, e.ms); } else glosasPend = { glosas, ms: e.ms };
        if (!["ofrece", "hecho", "alarma"].includes(arena().actual)) arena().forma("percibe", 700, "escucha");
      }
      estadoVivo();
    } else if (tipo === "agent") { anotar({ tipo: "dice", quien: "recepcion", texto: e.text }); arena().onda(1); }
    else if (tipo === "latency" && e.stage && e.stage.startsWith("fin de voz")) { ultimaResp = e.ms; estadoVivo(); }
    else if (tipo === "trace" && e.event) traza(e.event);
    else if (tipo === "report") { informe = e.report || {}; terminar(); }
  }
  let ultimaResp = 0;
  function estadoVivo() {
    const med = jevMs.length ? [...jevMs].sort((a, b) => a - b)[Math.floor(jevMs.length / 2)] : 0;
    estado(`<span class="vivo">En línea</span> · <b>${A.reloj(t())}</b>${med ? ` · Jev ${med} ms` : ""}${ultimaResp ? ` · respuesta ${A.coma(ultimaResp / 1000)} s` : ""}`);
  }

  // ------------------------------------------------------------------ llamar y colgar
  async function llamar() {
    const btn = $("btn-llamar-ya");
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
      salida = ctx.createMediaStreamDestination();
      audioEl = new Audio(); audioEl.autoplay = true; audioEl.srcObject = salida.stream;
      await audioEl.play().catch(() => {});
    } catch (e) {
      btn.disabled = false; btn.textContent = "Llamar ahora";
      soltarAudio();
      $("quien-aviso").innerHTML = "No hay acceso al micrófono. Revise el permiso del navegador o <button type=\"button\" class=\"secundario\" id=\"aviso-ver\">vea una llamada real</button>.";
      const b = $("aviso-ver"); if (b) b.onclick = () => verGrabada();
      return;
    }
    sid = "MZ" + hex(16); callSid = "CA" + hex(16); seq = 0; chunk = 0; resto = new Float32Array(0); frac = 0; cola8k = [];
    eventos = []; jctx = {}; glosasPend = null; informe = null; idioma = ""; turnos = 0; jevMs = []; espec = 0; ultimaResp = 0;
    hoja.limpiar();
    const base = (location.protocol === "https:" ? "wss://" : "ws://") + location.host;
    mon = new WebSocket(`${base}/monitor?call=${callSid}`);
    mon.onmessage = (m) => { try { recibir(JSON.parse(m.data)); } catch {} };
    ws = new WebSocket(`${base}/llamada`);
    ws.onopen = () => {
      const params = { call_id: callSid, ...(ficha.linea ? { from_number: ficha.linea } : {}) };
      ws.send(JSON.stringify({ event: "connected", protocol: "Call", version: "1.0.0" }));
      ws.send(JSON.stringify({ event: "start", sequenceNumber: String(++seq), streamSid: sid, start: {
        streamSid: sid, accountSid: "ACweb", callSid, tracks: ["inbound"], customParameters: params,
        mediaFormat: { encoding: "audio/x-mulaw", sampleRate: 8000, channels: 1 } } }));
      enLlamada = true; t0 = performance.now();
      document.body.classList.remove("eligiendo", "en-acta"); document.body.classList.add("en-llamada");
      pie("llamada"); estadoVivo(); ahora("Llamando a la recepción…");
      reloj = setInterval(estadoVivo, 1000);
      btn.disabled = false; btn.textContent = "Llamar ahora";
    };
    ws.onmessage = (m) => {
      let e; try { e = JSON.parse(m.data); } catch { return; }
      if (e.event === "media" && e.media && e.media.payload) reproducirVoz(e.media.payload);
      else if (e.event === "clear") callar();
    };
    ws.onclose = () => {
      if (!enLlamada) {
        btn.disabled = false; btn.textContent = "Llamar ahora"; soltarAudio();
        $("quien-aviso").textContent = "La recepción de pruebas no contesta ahora mismo (o tiene las líneas ocupadas). Puede ver una llamada real mientras tanto.";
        return;
      }
      colgar(false);
    };
  }
  function soltarAudio() {
    callar();
    if (stream) stream.getTracks().forEach((x) => x.stop());
    if (node) node.disconnect();
    if (ctx) ctx.close().catch(() => {});
    ctx = null; stream = null; node = null;
  }
  function colgar(avisar = true) {
    if (avisar && ws && ws.readyState === 1) {
      try { ws.send(JSON.stringify({ event: "stop", sequenceNumber: String(++seq), streamSid: sid, stop: { accountSid: "ACweb", callSid } })); } catch {}
    }
    try { ws && ws.close(); } catch {}
    ws = null; const estaba = enLlamada; enLlamada = false;
    soltarAudio();
    if (reloj) { clearInterval(reloj); reloj = null; }
    if (estaba && !informe) { ahora("Colgado. Cerrando el acta…"); cierreTimer = setTimeout(terminar, 2600); }
  }

  // ------------------------------------------------------------------ el acta de la llamada propia
  let actaPropia = null;
  function terminar() {
    if (cierreTimer) { clearTimeout(cierreTimer); cierreTimer = null; }
    if (actaPropia && actaPropia.id === callSid) return;
    if (enLlamada) colgar(false);
    try { mon && mon.close(); } catch {}
    mon = null;
    const r = informe || {};
    const escritos = eventos.filter((x) => x.tipo === "escrito");
    const med = jevMs.length ? [...jevMs].sort((a, b) => a - b)[Math.floor(jevMs.length / 2)] : 0;
    actaPropia = {
      id: callSid, fecha: new Date().toISOString(), dur: +t().toFixed(1), idioma: idioma || r.language || "es", cerebro: "v2",
      resultado: r.outcome || (escritos.length ? escritos.map((x) => x.accion).join(", ") : "NO_ACTION"), motivo: r.reason || null,
      linea: ficha.linea ? "reconocida" : "oculta", paciente: null, origen: "web",
      resumen: escritos.length ? escritos.map((x) => x.texto).join(" · ") : (r.reason ? `Sin gestión: ${r.reason}` : "Llamada desde la web, sin gestión en la agenda"),
      eventos, cifras: { turnos, mediana_jev_ms: med, especulacion: espec, dudas: eventos.filter((x) => x.tipo === "puerta" && !x.ok).length },
    };
    document.body.classList.remove("en-llamada"); document.body.classList.add("en-acta");
    estado(`<b>Su acta</b> · ${A.reloj(actaPropia.dur)} · ${turnos} turnos${med ? ` · Jev ${med} ms de mediana` : ""}`);
    ahora(`${A.esc(A.resTexto(actaPropia.resultado))}. Esto es lo que quedaría en el archivo de la clínica.`);
    pie("acta");
    if (arena().actual !== "hecho" && arena().actual !== "alarma") arena().forma("reposo");
  }

  async function guardarEnlace() {
    const b = $("btn-guardar");
    if (!actaPropia) return;
    b.disabled = true; b.textContent = "Guardando…";
    try {
      const r = await fetch("/api/acta", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(actaPropia) });
      const j = await r.json();
      if (!j.id) throw new Error("sin id");
      const url = `${location.origin}/acta?v=${j.id}`;
      try { await navigator.clipboard.writeText(url); } catch {}
      b.textContent = "Enlace copiado";
      ahora(`Su acta queda treinta días en <a href="${url}">${url.replace(/^https?:\/\//, "")}</a>`);
    } catch { b.disabled = false; b.textContent = "No se pudo guardar"; }
  }
  function imprimir() {
    if (!actaPropia) return;
    try { localStorage.setItem("digame-acta-propia", JSON.stringify(actaPropia)); } catch {}
    window.open("/acta?mia=1", "_blank");
  }

  // ------------------------------------------------------------------ sin micrófono: una llamada real, con sus tiempos
  let repro = null, indice = null, vistas = [];
  const FORMA_DE = { identidad: ["identifica", 1500, "escucha"], agenda: ["busca"], oferta: ["ofrece"], rechazo: ["busca"], alarma: ["alarma"], limite: ["declina", 2400, "escucha"] };
  async function verGrabada() {
    if (enLlamada) return;
    document.body.classList.remove("eligiendo", "en-acta"); document.body.classList.add("en-llamada");
    pie("grabada"); estado("Buscando una llamada real…"); ahora("");
    try {
      if (!indice) indice = await (await fetch("/datos/actas/indice.json")).json();
      let pool = (indice.destacadas && indice.destacadas.length ? indice.destacadas : indice.actas.slice(0, 30).map((x) => x.id));
      const idi = Object.fromEntries((indice.actas || []).map((x) => [x.id, x.idioma]));      // primero las que están en castellano o en catalán
      pool = [...pool].sort((a, b) => ({ es: 0, ca: 1 }[idi[a]] ?? 2) - ({ es: 0, ca: 1 }[idi[b]] ?? 2));
      let id = pool.find((x) => !vistas.includes(x)) || pool[Math.floor(Math.random() * pool.length)];
      vistas.push(id); if (vistas.length >= pool.length) vistas = [];
      const acta = await (await fetch(`/datos/actas/${id}.json`)).json();
      estado(`<span class="vivo">Llamada real</span> · ${A.esc(A.fechaLarga(acta.fecha))} · ${A.esc(A.IDIOMA[acta.idioma] || acta.idioma)} · ${A.reloj(acta.dur)}`);
      $("btn-acta-entera").href = `/acta?id=${encodeURIComponent(id)}`;
      arena().forma("escucha");
      if (repro) repro.parar();
      repro = A.reproducir($("pliego-acta"), acta, {
        onEvento(ev) {
          if (ev.tipo === "dice" && ev.quien === "llama") { arena().energia(0.5); if (!["ofrece", "hecho", "alarma"].includes(arena().actual)) arena().forma("percibe", 600, "escucha"); }
          else if (ev.tipo === "dice") arena().onda(1);
          else if (ev.tipo === "marca" && FORMA_DE[ev.clase]) arena().forma(...FORMA_DE[ev.clase]);
          else if (ev.tipo === "escrito") arena().forma("hecho");
          if (ev.tipo === "marca" || ev.tipo === "escrito") ahora(A.esc(ev.texto));
          else if (ev.tipo === "puerta") ahora(ev.ok ? "La puerta se abre: hay un «sí» claro a lo leído." : "La puerta espera un «sí» claro antes de escribir.");
        },
        onFin() { ahora(`${A.esc(A.resTexto(acta.resultado))}. ${A.esc(acta.resumen || "")}`); setTimeout(() => { if (arena().actual === "hecho") arena().forma("reposo"); }, 5000); },
      });
    } catch (e) { estado("No se pudo cargar la llamada."); }
  }
  function volver() {
    if (repro) { repro.parar(); repro = null; }
    if (enLlamada) colgar(true);
    document.body.classList.remove("eligiendo", "en-llamada", "en-acta");
    arena().forma("reposo");
  }
  function pie(modo) {
    for (const [id, m] of [["pie-llamada", "llamada"], ["pie-acta", "acta"], ["pie-grabada", "grabada"]]) $(id).hidden = m !== modo;
  }

  // ------------------------------------------------------------------ elegir quién llama
  function pintarFichas() {
    $("fichas").innerHTML = FICHAS.map((f) =>
      `<button type="button" data-id="${f.id}" class="${f === ficha ? "activa" : ""}"><strong>${f.nombre}</strong><span>${f.dice}</span></button>`).join("");
  }
  $("fichas").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    ficha = FICHAS.find((f) => f.id === b.dataset.id) || ficha; pintarFichas();
  });
  async function elegir() {
    document.body.classList.add("eligiendo"); pintarFichas(); arena().forma("escucha");
    try {
      const r = await (await fetch("/api/estado")).json();
      if (!r.recepcion) $("quien-aviso").textContent = "La recepción de pruebas no contesta ahora mismo. Puede ver una llamada real mientras tanto.";
      else if (r.activas >= 3) $("quien-aviso").textContent = "Las tres líneas de prueba están ocupadas. Espere un minuto o vea una llamada real.";
    } catch {}
  }

  $("btn-llamar").addEventListener("click", elegir);
  $("btn-llamar-ya").addEventListener("click", llamar);
  $("btn-cancelar").addEventListener("click", volver);
  $("btn-colgar").addEventListener("click", () => colgar(true));
  $("btn-guardar").addEventListener("click", guardarEnlace);
  $("btn-imprimir").addEventListener("click", imprimir);
  $("btn-otra").addEventListener("click", () => { document.body.classList.remove("en-acta"); elegir(); });
  $("btn-otra-grabada").addEventListener("click", verGrabada);
  document.querySelectorAll("[data-ver-grabada]").forEach((b) => b.addEventListener("click", verGrabada));
  document.querySelectorAll("[data-volver]").forEach((b) => b.addEventListener("click", volver));
  document.querySelectorAll("[data-llamar]").forEach((b) => b.addEventListener("click", () => { scrollTo({ top: 0, behavior: "smooth" }); setTimeout(elegir, 350); }));

  if (/[?&]grabada/.test(location.search)) setTimeout(verGrabada, 300);   // enlace directo a «ver una llamada real»

  (function medir() {
    const m = $("nivel");
    if (m) m.style.transform = `scaleX(${enLlamada ? Math.min(1, nivel * 3) : 0})`;
    nivel *= 0.9;
    requestAnimationFrame(medir);
  })();
})();
