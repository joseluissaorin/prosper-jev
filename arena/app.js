/* La arena de la recepción.
 *
 * Un cuerpo de arena que va tomando forma con lo que hace el agente. Cada grano conserva su identidad; lo que
 * cambia es la forma a la que tiende, y cuánto se suelta o se asienta:
 *
 *   reposo       duna asentada               arena quieta, nadie llama
 *   escucha      masa que respira            quien llama habla; los parciales la rizan
 *   percibe      anillo                      Jev juzga el turno (Sistema 1)
 *   identifica   núcleo denso                la ficha aparece
 *   busca        abanico de arena suelta     la agenda: lo posible, más denso donde hay más huecos
 *   ofrece       gota                        la arena se concentra en un hueco
 *   hecho        sello asentado (azul)       reserva, cambio, anulación o alta declarados
 *   alarma       dispersión roja             urgencia derivada al 112
 *   declina      hueco                       fuera de ámbito
 *
 * Las ondas salen del centro cuando habla el agente. En directo lee el monitor del agente (wss://…/monitor);
 * sin conexión, reproduce una llamada real grabada (demo.json). Las cifras (DNI, teléfonos) se enmascaran.
 */
(() => {
  "use strict";
  const CFG = window.ARENA_CONFIG || {};
  const qs = new URLSearchParams(location.search);
  // el enlace privado (con token) se recuerda en este dispositivo: la app instalada abre sin parámetros
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("arena") || "{}"); } catch {}
  if (qs.get("t")) {
    saved = { ws: qs.get("ws") || "", call: qs.get("call") || "", t: qs.get("t") };
    try { localStorage.setItem("arena", JSON.stringify(saved)); } catch {}
  }
  if (qs.get("olvidar") !== null) { try { localStorage.removeItem("arena"); } catch {} saved = {}; }
  const WS_URL = qs.get("ws") || saved.ws || CFG.ws || "";
  const TOKEN = qs.get("t") || saved.t || CFG.token || "";
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------------ lienzo
  const cv = document.getElementById("arena");
  const ctx = cv.getContext("2d", { alpha: false });
  let W = 0, H = 0, DPR = 1, N = 0;
  let X, Y, VX, VY, TX, TY, A, R, S, TINT; // posición, velocidad, destino, ángulo propio, radio propio, tamaño, tinte
  const PAPEL = "#f3eee4";
  const COLS = ["rgba(42,36,28,", "rgba(31,86,200,", "rgba(184,57,43,"];

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = innerWidth; H = innerHeight;
    cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR);
    cv.style.width = W + "px"; cv.style.height = H + "px";
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    const want = Math.round(Math.min(3200, Math.max(1400, W * H / 180)));
    if (want !== N) alloc(want);
  }
  function alloc(n) {
    const old = N;
    const keep = (a) => a;
    const nx = new Float32Array(n), ny = new Float32Array(n), nvx = new Float32Array(n), nvy = new Float32Array(n);
    const ntx = new Float32Array(n), nty = new Float32Array(n), na = new Float32Array(n), nr = new Float32Array(n);
    const ns = new Float32Array(n), nt = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      if (i < old) { nx[i] = X[i]; ny[i] = Y[i]; nvx[i] = VX[i]; nvy[i] = VY[i]; na[i] = A[i]; nr[i] = R[i]; ns[i] = S[i]; nt[i] = TINT[i]; }
      else {
        nx[i] = Math.random() * W; ny[i] = H * (0.55 + Math.random() * 0.1);
        na[i] = Math.random() * Math.PI * 2; nr[i] = Math.sqrt(Math.random());
        ns[i] = 0.7 + Math.random() * 0.9; nt[i] = 0;
      }
    }
    X = nx; Y = ny; VX = nvx; VY = nvy; TX = ntx; TY = nty; A = na; R = nr; S = ns; TINT = nt; N = n;
    keep();
  }

  // ------------------------------------------------------------------ ruido suave (para que la arena respire)
  function noise(x, y, t) {
    return Math.sin(x * 1.7 + t * 0.9) * 0.5 + Math.sin(y * 2.3 - t * 0.7) * 0.3 + Math.sin((x + y) * 3.1 + t * 1.3) * 0.2;
  }

  // ------------------------------------------------------------------ formas
  const state = { shape: "reposo", since: 0, loose: 0.15, energy: 0, tint: 0, focus: 0.5, fanCount: 0 };
  const center = () => ({ cx: W / 2, cy: Math.min(H * 0.36, H / 2 - 60), R: Math.min(W, H) * (W < 600 ? 0.30 : 0.22) });

  function targets(t) {
    const { cx, cy, R: RR } = center();
    const sh = state.shape;
    for (let i = 0; i < N; i++) {
      const a = A[i], r = R[i];
      let x, y;
      if (sh === "reposo") {                                  // duna asentada
        const u = (i / N) * 2 - 1, spread = W * 0.46;
        x = W / 2 + u * spread;
        const h = RR * 0.55 * Math.exp(-(u * u) * 3.2);
        y = cy + RR * 0.85 - r * h;
      } else if (sh === "escucha") {                          // masa que respira
        const k = 1 + 0.10 * noise(Math.cos(a), Math.sin(a), t) + 0.10 * state.energy;
        x = cx + Math.cos(a) * r * RR * k; y = cy + Math.sin(a) * r * RR * k * 0.92;
      } else if (sh === "percibe") {                          // anillo que gira
        const rr = RR * (0.78 + 0.22 * r) * 0.95, aa = a + t * 0.9;
        x = cx + Math.cos(aa) * rr; y = cy + Math.sin(aa) * rr;
      } else if (sh === "identifica") {                       // núcleo denso
        const rr = RR * 0.42 * Math.pow(r, 0.8);
        x = cx + Math.cos(a) * rr; y = cy + Math.sin(a) * rr;
      } else if (sh === "busca") {                            // abanico: lo posible, más denso cerca
        const d = Math.pow(r, 1.6), ang = (a / (Math.PI * 2) - 0.5) * 1.15;
        const L = RR * 2.1, ox = cx - RR * 0.9;
        x = ox + Math.cos(ang) * d * L; y = cy + Math.sin(ang) * d * L * 0.9 + 0.08 * RR * noise(a, r, t);
      } else if (sh === "ofrece") {                           // gota: casi toda la arena en un hueco
        const hole = i % 10 < 7;
        if (hole) {
          const rr = RR * 0.28 * Math.pow(r, 0.7);
          x = cx + RR * 0.55 + Math.cos(a) * rr; y = cy - RR * 0.1 + Math.sin(a) * rr;
        } else {
          const d = Math.pow(r, 1.6), ang = (a / (Math.PI * 2) - 0.5) * 1.15;
          x = cx - RR * 0.9 + Math.cos(ang) * d * RR * 1.6; y = cy + Math.sin(ang) * d * RR * 1.4;
        }
      } else if (sh === "hecho") {                            // sello asentado con su canto
        const edge = i % 7 === 0;
        const rr = edge ? RR * (0.66 + 0.03 * r) : RR * 0.6 * Math.pow(r, 0.55);
        x = cx + Math.cos(a) * rr; y = cy + Math.sin(a) * rr;
      } else if (sh === "alarma") {                           // se dispersa hacia los bordes
        const rr = Math.max(W, H) * (0.45 + 0.35 * r);
        x = cx + Math.cos(a) * rr; y = cy + Math.sin(a) * rr;
      } else if (sh === "declina") {                          // hueco en el centro
        const rr = RR * (0.7 + 0.35 * r);
        x = cx + Math.cos(a) * rr; y = cy + Math.sin(a) * rr;
      } else {
        x = cx; y = cy;
      }
      TX[i] = x; TY[i] = y;
    }
  }

  const SHAPE = {
    reposo: { loose: 0.08, k: 0.020 }, escucha: { loose: 0.55, k: 0.030 }, percibe: { loose: 0.25, k: 0.045 },
    identifica: { loose: 0.10, k: 0.050 }, busca: { loose: 0.85, k: 0.028 }, ofrece: { loose: 0.30, k: 0.040 },
    hecho: { loose: 0.04, k: 0.060 }, alarma: { loose: 1.0, k: 0.020 }, declina: { loose: 0.30, k: 0.035 },
  };
  let back = null;
  function setShape(name, holdMs = 0, then = null) {
    state.shape = name; state.since = performance.now();
    if (back) clearTimeout(back);
    back = holdMs && then ? setTimeout(() => setShape(then), holdMs) : null;
    const tint = name === "hecho" ? 1 : name === "alarma" ? 2 : 0;
    for (let i = 0; i < N; i++) TINT[i] = tint && (tint === 2 || i % 3 === 0) ? tint : 0;
  }

  const waves = [];
  function ripple(strength = 1, x = null, y = null) {
    if (REDUCED) return;
    const { cx, cy } = center();
    waves.push({ t0: performance.now(), s: strength, x: x ?? cx, y: y ?? cy });
  }

  // ------------------------------------------------------------------ el dedo y el cursor en la arena
  // Pasar aparta los granos (más cuanto más rápido); un toque lanza una onda desde ahí; mantener pulsado los recoge.
  const ptr = { x: -1e4, y: -1e4, vx: 0, vy: 0, on: false, down: false, t: 0, hold: 0 };
  function mover(e) {
    const now = performance.now(), dt = Math.max(8, now - ptr.t);
    if (ptr.on) { ptr.vx = (e.clientX - ptr.x) / dt * 16; ptr.vy = (e.clientY - ptr.y) / dt * 16; }
    ptr.x = e.clientX; ptr.y = e.clientY; ptr.t = now; ptr.on = true;
  }
  const sobreArena = (e) => !(e.target.closest && e.target.closest(".sheet, .top"));
  addEventListener("pointermove", (e) => { if (sobreArena(e) || ptr.down) mover(e); else ptr.on = false; }, { passive: true });
  addEventListener("pointerdown", (e) => {
    if (!sobreArena(e)) return;
    mover(e); ptr.down = true; ptr.hold = performance.now();
    ripple(0.9, e.clientX, e.clientY);
  }, { passive: true });
  const soltar = (e) => {
    if (!ptr.down) return;
    if (performance.now() - ptr.hold > 350) ripple(1.3, ptr.x, ptr.y);   // lo recogido se reparte al soltar
    ptr.down = false;
    if (e.pointerType !== "mouse") { ptr.on = false; ptr.x = ptr.y = -1e4; }
  };
  addEventListener("pointerup", soltar, { passive: true });
  addEventListener("pointercancel", soltar, { passive: true });
  addEventListener("pointerleave", () => { ptr.on = false; ptr.x = ptr.y = -1e4; });

  // ------------------------------------------------------------------ bucle
  let last = performance.now();
  function frame(now) {
    const dt = Math.min(48, now - last) / 16.67; last = now;
    const t = now / 1000;
    targets(t);
    const cfg = SHAPE[state.shape] || SHAPE.reposo;
    const loose = cfg.loose + state.energy * 0.6, k = cfg.k;
    state.energy *= 0.94;
    ptr.vx *= 0.85; ptr.vy *= 0.85;
    const { cx, cy } = center();
    for (let w = waves.length - 1; w >= 0; w--) if (now - waves[w].t0 > 1400) waves.splice(w, 1);
    for (let i = 0; i < N; i++) {
      let ax = (TX[i] - X[i]) * k, ay = (TY[i] - Y[i]) * k;
      if (!REDUCED) {
        ax += loose * 0.35 * noise(X[i] * 0.01, Y[i] * 0.01, t + i * 0.0007);
        ay += loose * 0.35 * noise(Y[i] * 0.01, X[i] * 0.01, t * 1.1 - i * 0.0005);
      }
      if (ptr.on) {                                          // el dedo aparta la arena, o la recoge si mantiene pulsado
        const dx = X[i] - ptr.x, dy = Y[i] - ptr.y, d2 = dx * dx + dy * dy;
        const RAD = ptr.down ? 110 : 70;
        if (d2 < RAD * RAD) {
          const d = Math.sqrt(d2) + 1e-3, fall = 1 - d / RAD;
          if (ptr.down && performance.now() - ptr.hold > 350) { ax -= (dx / d) * fall * 1.4; ay -= (dy / d) * fall * 1.4; }
          else {
            const sp = Math.min(3, 0.6 + Math.hypot(ptr.vx, ptr.vy) * 0.25);
            ax += (dx / d) * fall * sp + ptr.vx * fall * 0.08; ay += (dy / d) * fall * sp + ptr.vy * fall * 0.08;
          }
        }
      }
      for (const wv of waves) {                              // ondas: la voz del agente, o un toque
        const age = (now - wv.t0) / 1000, front = age * Math.max(W, H) * 0.55;
        const dx = X[i] - wv.x, dy = Y[i] - wv.y, d = Math.hypot(dx, dy) + 1e-3;
        const g = Math.exp(-((d - front) ** 2) / 1800) * 0.9 * wv.s * (1 - age / 1.4);
        ax += (dx / d) * g; ay += (dy / d) * g;
      }
      VX[i] = (VX[i] + ax * dt) * 0.86; VY[i] = (VY[i] + ay * dt) * 0.86;
      X[i] += VX[i] * dt; Y[i] += VY[i] * dt;
    }
    ctx.fillStyle = PAPEL; ctx.fillRect(0, 0, W, H);
    for (let c = 0; c < 3; c++) {
      for (const alpha of [0.35, 0.62, 0.85]) {
        ctx.fillStyle = COLS[c] + alpha + ")";
        for (let i = 0; i < N; i++) {
          if (TINT[i] !== c) continue;
          const band = S[i] < 1.0 ? 0.35 : S[i] < 1.3 ? 0.62 : 0.85;
          if (band !== alpha) continue;
          const s = S[i] * (W < 600 ? 1.15 : 1.3);
          ctx.fillRect(X[i], Y[i], s, s);
        }
      }
    }
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------------ texto
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  // cifras personales: DNI, NIE y teléfonos se enseñan a medias
  const mask = (s) => String(s || "").replace(/([XYZxyz]?\d[\d\s.-]{5,}\d)([A-Za-z]?)/g, (m, d, l) => {
    const digits = d.replace(/\D/g, "");
    return digits.slice(0, 2) + "•".repeat(Math.max(0, digits.length - 4)) + digits.slice(-2) + (l ? "•" : "");
  });
  function frase(texto, kicker, clase) {
    $("frase").innerHTML = texto;
    $("kicker").textContent = kicker;
    $("kicker").className = "kicker" + (clase ? " " + clase : "");
  }
  let lineas = [];
  function linea(quien, texto, parcial = false) {
    if (parcial && lineas.length && lineas[lineas.length - 1].parcial) lineas.pop();
    else if (!parcial && lineas.length && lineas[lineas.length - 1].parcial && quien === "llama") lineas.pop();
    lineas.push({ quien, texto: mask(texto), parcial });
    lineas = lineas.slice(-6);
    $("dialogo").innerHTML = lineas.map((l) =>
      `<p class="${l.quien}${l.parcial ? " parcial" : ""}"><span class="quien">${l.quien === "llama" ? "Llama" : "Recepción"}</span>${esc(l.texto)}</p>`).join("");
  }
  const cifra = (id, v) => { $(id).textContent = v; };

  // ------------------------------------------------------------------ eventos del agente → forma y frase
  const ACT = { confirm: "dice que sí", reject: "dice que no", correct: "corrige algo", provide_info: "da datos", ask_question: "pregunta",
                backchannel: "asiente", end_call: "se despide", unclear: "no se entiende" };
  const INTENT = { book: "pedir cita", reschedule: "cambiar una cita", cancel: "anular una cita", register: "darse de alta", info: "informarse" };
  let turno = 0, siguiendo = null;
  const hora = (iso) => { try { return new Date(iso).toLocaleString("es-ES", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Madrid" }); } catch { return iso; } };

  function onEvent(e) {
    const tipo = e.type;
    if (tipo === "call_started") {
      turno = 0; lineas = []; $("dialogo").innerHTML = "";
      setShape("escucha"); frase("Entra una llamada.", "En línea");
      $("dot").className = "dot vivo"; cifra("c-estado", "en línea"); cifra("c-turno", "0");
    } else if (tipo === "vad") {
      if (e.state === "speech") { state.energy = Math.min(1, state.energy + 0.4); if (!["ofrece", "hecho", "alarma"].includes(state.shape)) setShape("escucha"); }
    } else if (tipo === "partial") {
      state.energy = Math.min(1, state.energy + 0.25); linea("llama", e.text, true);
    } else if (tipo === "final") {
      turno += 1; cifra("c-turno", String(turno)); linea("llama", e.text);
    } else if (tipo === "perception") {
      if (e.ms > 0) cifra("c-jev", `${e.ms} ms`);
      if (e.phase !== "parcial") {
        const j = e.j || {}, pick = (v) => (Array.isArray(v) ? v[0] : v && v.c);
        const act = pick(j.act), it = pick(j.intent);
        if (!["ofrece", "hecho", "alarma"].includes(state.shape)) setShape("percibe", 700, "escucha");
        frase(`Jev entiende que <em>${ACT[act] || "habla"}</em>${it && INTENT[it] ? ` y quiere ${INTENT[it]}` : ""}.`, "Sistema 1 · Jev");
      }
    } else if (tipo === "agent") {
      linea("recepcion", e.text); ripple(1);
      if (state.shape === "busca" && /\d{1,2}(:\d{2})?\s*(am|pm|h)|\b\d{1,2}:\d{2}\b|\?/i.test(e.text || "")) {
        setShape("ofrece"); frase("Propone un hueco.", "Propuesta");
      }
    } else if (tipo === "latency" && e.stage && e.stage.startsWith("fin de voz")) {
      cifra("c-resp", `${(e.ms / 1000).toFixed(2).replace(".", ",")} s`);
    } else if (tipo === "trace" && e.event) {
      traza(e.event);
    } else if (tipo === "report") {
      const r = e.report || {};
      if (state.shape !== "hecho" && state.shape !== "alarma") setShape("reposo");
      frase(`Llamada terminada${r.outcome ? `: <em>${esc(r.outcome.toLowerCase())}</em>` : ""}.`, "Fin");
      $("dot").className = "dot"; cifra("c-estado", "terminada");
      setTimeout(() => { if (state.shape === "hecho") setShape("reposo"); }, 6000);
    }
  }

  // v2 (planificador con herramientas): cada herramienta mueve la arena
  const TOOL = {
    identify_patient: (ok) => ok ? ["identifica", "Ficha encontrada.", "Identidad"] : [null, "Comprobando quién es…", "Identidad"],
    list_appointments: () => ["identifica", "Mirando sus citas.", "Citas"],
    find_slots: () => ["busca", "Mirando la agenda.", "Agenda"],
    prepare_cancellation: () => ["ofrece", "Prepara la anulación y la lee antes de hacerla.", "Propuesta"],
    prepare_registration: () => ["ofrece", "Lee los datos del alta antes de hacerla.", "Propuesta"],
    confirm_booking: (ok) => ok ? ["hecho", "Cita reservada.", "Hecho"] : [null, "Aún no: falta un «sí» a esa cita.", "Puerta"],
    confirm_cancellation: (ok) => ok ? ["hecho", "Cita anulada.", "Hecho"] : [null, "Aún no: falta un «sí» a esa anulación.", "Puerta"],
    confirm_registration: (ok) => ok ? ["hecho", "Alta hecha.", "Hecho"] : [null, "Aún no: falta un «sí» a los datos.", "Puerta"],
    nearest_site: () => [null, "Busca la sede más cercana.", "Sedes"],
    clinic_info: () => [null, "Lo mira en el catálogo de la clínica.", "Catálogo"],
    decline: () => ["declina", "Eso no se puede hacer: <em>se declina</em>.", "Límites"],
  };
  const fallo = (r) => /error|not found|blocked|denied|refus|no se|missing|required|not allowed|needs/i.test(String(r || ""));

  function traza(ev) {
    const k = ev.kind;
    if (k === "tool" && TOOL[ev.name]) {
      const [forma, texto, kicker] = TOOL[ev.name](!fallo(ev.result));
      if (forma) setShape(forma, forma === "identifica" ? 1600 : 0, forma === "identifica" ? "escucha" : null);
      frase(texto, kicker);
      return;
    }
    if (k === "gate" && ev.name === "puerta" && !ev.ok) { frase("La puerta espera un «sí» claro antes de escribir en la agenda.", "Seguridad"); return; }
    if (k === "gate" && ev.name === "negativa") { setShape("declina", 2500, "escucha"); frase("Eso no se puede hacer: <em>se declina</em>.", "Límites"); return; }
    if (k === "declared") { frase(`Declarado: <em>${esc((ev.actions || []).join(", ").toLowerCase() || "sin acción")}</em>.`, "Hecho"); return; }
    if (k === "jev_down") { frase("Jev no responde: Flash-Lite hace de Sistema 1 mientras tanto.", "Respaldo"); return; }
    if (k === "speculation_reused") { frase("La respuesta ya estaba pensada mientras hablaba.", "Especulación"); return; }
    if (k === "gate" && ev.name === "identidad" && ev.ok) {
      setShape("identifica", 1600, "escucha");
      const quien = (ev.detail || "").split("(")[0].replace(/^P\d+\s*/, "").trim();
      frase(`Ficha encontrada: <em>${esc(quien)}</em>.`, "Identidad");
    } else if (k === "identity") {
      frase("Comprobando quién es…", "Identidad");
    } else if (k === "availability") {
      setShape("busca");
      frase(`Mirando la agenda: <em>${ev.found || 0} huecos</em> posibles.`, "Agenda");
    } else if (k === "offer") {
      setShape("ofrece");
      frase(`Ofrece el <em>${esc(hora(ev.slot))}</em>.`, "Propuesta");
    } else if (k === "offer_rejected") {
      setShape("busca"); frase("No le encaja: vuelve a buscar.", "Agenda");
    } else if (k === "gate" && ev.name === "envío" && ev.ok) {
      const verbo = (ev.detail || "").split(" ")[0];
      const txt = { book: "Cita reservada", reschedule: "Cita cambiada", cancel: "Cita anulada", register: "Alta hecha", "no-action": "Declarado sin acción", escalate: "Derivado a urgencias" }[verbo] || "Declarado";
      if (verbo !== "no-action" && verbo !== "escalate") setShape("hecho");
      frase(`${txt}.`, "Hecho");
    } else if (k === "gate" && ev.name === "triaje" && !ev.ok) {
      setShape("alarma"); frase("Urgencia: <em>que llame al 112</em>.", "Alarma", "alarma");
      $("dot").className = "dot alarma";
    } else if (k === "gate" && ev.name === "límites" && !ev.ok) {
      setShape("declina", 2500, "escucha"); frase("Eso no se puede hacer: <em>se declina</em>.", "Límites");
    } else if (k === "system2") {
      frase(`Pregunta abierta: responde el <em>Sistema 2</em>, con la guardia de Jev.`, "Sistema 2");
    } else if (k === "second_opinion" && ev.used) {
      frase("Segunda opinión sobre el audio: <em>cifras recuperadas</em>.", "Oído");
    } else if (k === "lang") {
      frase(`Idioma: <em>${{ en: "inglés", es: "español", ca: "catalán" }[ev.lang] || ev.lang}</em>.`, "Idioma");
    } else if (k === "new_task") {
      setShape("escucha"); frase("Otra gestión en la misma llamada.", "Tarea nueva");
    }
  }

  // ------------------------------------------------------------------ fuentes: directo y demostración
  let ws = null, demoTimer = null, modo = "directo";
  const llamadas = new Map();

  function pintarLlamadas() {
    const nav = $("llamadas");
    nav.innerHTML = [...llamadas.keys()].slice(-6).map((id) =>
      `<button type="button" data-id="${esc(id)}" class="${id === siguiendo ? "activa" : ""}">${esc(id.slice(0, 8))}</button>`).join("");
    nav.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => seguir(b.dataset.id)));
  }
  function seguir(id) {
    siguiendo = id; pintarLlamadas();
    setShape("escucha"); lineas = []; turno = 0;
    for (const e of llamadas.get(id) || []) onEvent(e);
  }
  function recibir(e) {
    const id = e.call_id || e.callSid || "?";
    if (!llamadas.has(id)) { llamadas.set(id, []); }
    llamadas.get(id).push(e);
    if (llamadas.size > 12) llamadas.delete(llamadas.keys().next().value);
    if ((e.type === "call_started" && !soloEsta) || siguiendo === null) { siguiendo = id; pintarLlamadas(); }
    if (id === siguiendo) onEvent(e);
  }

  function directo() {
    if (!WS_URL) return false;
    modo = "directo"; $("mode").textContent = "Ver demostración"; $("mode").setAttribute("aria-pressed", "false");
    try { ws = new WebSocket(WS_URL + (TOKEN ? (WS_URL.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN) : "")); }
    catch { return false; }
    ws.onopen = () => { frase("Conectada al agente. Esperando llamada.", "En directo"); cifra("c-estado", "a la espera"); };
    ws.onmessage = (m) => { try { recibir(JSON.parse(m.data)); } catch {} };
    ws.onclose = () => { if (modo === "directo") { cifra("c-estado", "sin conexión"); setTimeout(() => modo === "directo" && directo(), 4000); } };
    return true;
  }

  async function demo() {
    modo = "demo"; if (ws) { try { ws.close(); } catch {} ws = null; }
    $("mode").textContent = WS_URL ? "Ver en directo" : "Demostración"; $("mode").setAttribute("aria-pressed", "true");
    let evs = [];
    try { evs = await (await fetch("demo.json", { cache: "no-store" })).json(); } catch { frase("No hay demostración grabada.", "Demostración"); return; }
    llamadas.clear(); siguiendo = null;
    const play = () => {
      if (modo !== "demo") return;
      const t0 = performance.now(), base = evs[0]?._rx || 0;
      let i = 0;
      const tick = () => {
        if (modo !== "demo") return;
        const el = (performance.now() - t0) / 1000;
        while (i < evs.length && evs[i]._rx - base <= el) recibir(evs[i++]);
        if (i < evs.length) demoTimer = setTimeout(tick, 40);
        else demoTimer = setTimeout(() => { setShape("reposo"); setTimeout(play, 3500); }, 4000);
      };
      tick();
    };
    frase("Una llamada real, grabada.", "Demostración");
    play();
  }

  $("mode").addEventListener("click", () => {
    if (demoTimer) clearTimeout(demoTimer);
    if (modo === "demo") { if (!directo()) demo(); } else demo();
  });

  // la llamada propia (llamar.js): se sigue su monitor, el de la instancia local
  let soloEsta = null;
  window.ARENA = {
    seguirLlamada(monUrl, token, sid) {
      if (demoTimer) clearTimeout(demoTimer);
      modo = "llamada"; soloEsta = sid; llamadas.clear(); siguiendo = sid;
      if (ws) { try { ws.onclose = null; ws.close(); } catch {} }
      ws = new WebSocket(monUrl + (token ? (monUrl.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(token) : ""));
      ws.onmessage = (m) => { try { const e = JSON.parse(m.data); if ((e.call_id || "") === soloEsta) recibir(e); } catch {} };
      frase("Llamando a la recepción…", "Tu llamada"); $("dot").className = "dot vivo";
      $("mode").textContent = "Ver demostración";
    },
  };

  // ------------------------------------------------------------------ arranque
  addEventListener("resize", resize);
  resize(); setShape("reposo"); requestAnimationFrame(frame);
  if (!directo()) demo();
})();
