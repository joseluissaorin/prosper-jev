/* El acta: la edición crítica de una llamada.
 *
 * El texto de la conversación a la izquierda; al margen, como glosas, los juicios de Jev (Sistema 1) con su
 * probabilidad; entre líneas, las marcas del núcleo (ficha, agenda, oferta); y, donde se escribió en la agenda,
 * el sello de la puerta. Sirve igual para una llamada en directo (se va componiendo), para reproducir una
 * grabada y para pintarla entera.
 *
 * Formato de un acta: ver producto/construir.py (eventos «dice», «marca», «puerta», «escrito»).
 */
(() => {
  "use strict";
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  // cifras personales: DNI, NIE y teléfonos se enseñan a medias
  const mask = (s) => String(s || "").replace(/([XYZxyz]?\d[\d\s.-]{5,}\d)([A-Za-z]?)/g, (m, d, l) => {
    const digits = d.replace(/\D/g, "");
    if (digits.length < 7) return m;
    return digits.slice(0, 2) + "•".repeat(Math.max(0, digits.length - 4)) + digits.slice(-2) + (l ? "•" : "");
  });
  const coma = (v, n = 2) => Number(v).toFixed(n).replace(".", ",");
  const reloj = (t) => { const s = Math.max(0, Math.round(t || 0)); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
  const RES = { BOOK: "Cita reservada", RESCHEDULE: "Cita cambiada", CANCEL: "Cita anulada", REGISTER: "Alta hecha", ESCALATE: "Derivada al 112", NO_ACTION: "Sin gestión" };
  const IDIOMA = { es: "castellano", en: "inglés", ca: "catalán", gl: "gallego", eu: "euskera", fr: "francés", de: "alemán", it: "italiano", pt: "portugués" };
  const fechaLarga = (iso) => { try { return new Date(iso).toLocaleString("es-ES", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Madrid" }); } catch { return iso || ""; } };
  const resTexto = (r) => String(r || "").split(",").map((x) => RES[x.trim()] || x.trim()).filter(Boolean).join(" · ") || "Sin gestión";
  const resClase = (r) => String(r || "NO_ACTION").split(",")[0].trim() || "NO_ACTION";

  // ------------------------------------------------------------------ glosas a partir de los juicios crudos de Jev (en directo)
  const ACTO = { confirm: "dice que sí", reject: "dice que no", correct: "corrige", provide_info: "da datos", ask_question: "pregunta",
                 backchannel: "asiente", end_call: "se despide", unclear: "no se entiende", greet: "saluda" };
  const QUIERE = { book: "quiere pedir cita", reschedule: "quiere cambiar una cita", cancel: "quiere anular", register: "quiere darse de alta", info: "quiere informarse" };
  const par = (v) => (Array.isArray(v) ? v : v && typeof v === "object" ? [v.c ?? v.choice, v.p ?? v.conf] : [v, null]);
  function glosasDeJev(j, ctx = {}) {
    const out = [];
    if (!j) return out;
    const [acto, ca] = par(j.act);
    if (acto && ACTO[acto]) out.push({ k: ACTO[acto], v: ca ?? 1, clase: (ca ?? 1) < 0.8 ? "floja" : "" });
    const [flag, cf] = par(j.red_flag);
    if (flag && flag !== "none" && (cf ?? 1) > 0.5) out.push({ k: "urgencia: " + flag, v: cf ?? 1, clase: "alerta" });
    const [oos, co] = par(j.oos);
    if (oos && oos !== "none" && (co ?? 1) > 0.5) out.push({ k: "fuera de límites", v: co ?? 1, clase: "alerta" });
    if (typeof j.manipulation === "number" && j.manipulation > 0.5) out.push({ k: "manipulación", v: j.manipulation, clase: "alerta" });
    if (typeof j.accepts === "number" && (ctx.hayOferta || j.accepts > 0.3)) out.push({ k: "acepta", v: j.accepts, clase: j.accepts > 0.3 && j.accepts < 0.85 ? "floja" : "" });
    if (typeof j.finished === "number") out.push({ k: "terminó", v: j.finished, clase: j.finished < 0.6 ? "floja" : "" });
    const [it, ci] = par(j.intent);
    if (it && QUIERE[it] && ctx.intencion !== it) { out.push({ k: QUIERE[it], v: ci ?? 1 }); ctx.intencion = it; }
    if (typeof j.for_other === "number" && j.for_other > 0.5) out.push({ k: "para otra persona", v: j.for_other });
    if (typeof j.asks_question === "number" && j.asks_question > 0.6 && acto !== "ask_question") out.push({ k: "y pregunta", v: j.asks_question });
    return out.slice(0, 5);
  }

  // ------------------------------------------------------------------ piezas
  function htmlGlosas(glosas, ms) {
    let h = (glosas || []).map((g) =>
      `<div class="glosa ${g.clase || ""}"><span class="k">${esc(g.k)}</span><span class="barra"><i style="width:${Math.round(Math.max(0, Math.min(1, g.v)) * 100)}%"></i></span><span class="v">${coma(g.v)}</span></div>`).join("");
    if (ms) h += `<div class="glosa ms"><span class="k">Jev · ${ms} ms</span></div>`;
    return h;
  }
  function nodoEvento(ev) {
    const d = document.createElement("div");
    if (ev.tipo === "dice") {
      d.className = `acta-linea ${ev.quien === "llama" ? "llama" : "recepcion"}${ev.parcial ? " parcial" : ""}`;
      const via = ev.via && ev.quien !== "llama" ? `<span class="via">${esc(ev.via)}</span>` : "";
      d.innerHTML = `<div class="habla">${ev.quien === "llama" ? "Llama" : "Recepción"}<time>${reloj(ev.t)}</time></div>` +
        `<div class="texto">${esc(mask(ev.texto))}${via}</div>` +
        `<div class="glosas">${htmlGlosas(ev.glosas, ev.ms)}</div>`;
    } else if (ev.tipo === "marca") {
      d.className = `acta-marca ${ev.clase || ""}`;
      d.innerHTML = `<span>${esc(mask(ev.texto))}</span>`;
    } else if (ev.tipo === "puerta") {
      d.className = `puerta ${ev.ok ? "abierta" : "cerrada"}`;
      const checks = (ev.checks || []).map((c) =>
        `<span class="chk ${c.ok === false ? "no" : ""}">${esc(c.k)}${typeof c.v === "number" ? ` <small>${coma(c.v)}</small>` : ""}</span>`).join("");
      d.innerHTML = `<div class="caja"><b>${ev.ok ? "PUERTA" : "PUERTA CERRADA"}</b>${checks}${ev.ok ? "" : `<span class="chk no">falta un «sí» claro: vuelve a preguntar</span>`}<div class="sello">PUERTA<br>· SÍ ·<br>CLARO</div></div>`;
    } else if (ev.tipo === "escrito") {
      d.className = "escrito";
      d.innerHTML = `<span><b>ESCRITO</b>${esc(mask(ev.texto))}</span>`;
    }
    return d;
  }

  function cabecera(acta) {
    const h = document.createElement("header");
    h.className = "acta-cab";
    const lineas = [
      acta.fecha ? `<b>${esc(fechaLarga(acta.fecha))}</b>` : "",
      `duración <b>${reloj(acta.dur)}</b> · ${esc(IDIOMA[acta.idioma] || acta.idioma || "")}`,
      acta.linea ? `línea ${esc(acta.linea)}${acta.paciente ? ` · <b>${esc(acta.paciente)}</b>` : ""}` : "",
      `Clínica Arenal · recepción`,
    ].filter(Boolean).join("<br>");
    h.innerHTML = `<h1><small>Acta de llamada · n.º ${esc(String(acta.id || "").slice(-8))}</small>Acta</h1><div class="ficha-acta">${lineas}</div>`;
    return h;
  }
  function resumen(acta) {
    const p = document.createElement("p");
    p.className = "acta-resumen";
    p.innerHTML = `<span class="res ${resClase(acta.resultado)}">${esc(resTexto(acta.resultado))}</span>${esc(mask(acta.resumen || ""))}`;
    return p;
  }
  function cifras(acta) {
    const c = acta.cifras || {};
    const f = document.createElement("footer");
    f.className = "acta-cifras";
    const items = [
      c.turnos != null ? `<span><b>${c.turnos}</b> turnos</span>` : "",
      c.mediana_jev_ms ? `<span>Jev, mediana <b>${c.mediana_jev_ms} ms</b></span>` : "",
      c.especulacion ? `<span><b>${c.especulacion}</b> respuestas ya pensadas mientras hablaba</span>` : "",
      c.api != null ? `<span><b>${c.api}</b> consultas a la agenda</span>` : "",
      c.dudas ? `<span><b>${c.dudas}</b> ${c.dudas === 1 ? "duda" : "dudas"}</span>` : "",
      acta.fin ? `<span>cuelga: <b>${{ goodbye: "tras despedirse", stop: "quien llama", disconnect: "se cortó" }[acta.fin] || esc(acta.fin)}</b></span>` : "",
      `<span>Habló con una máquina y se le dijo (art. 50 del Reglamento de IA)</span>`,
    ].filter(Boolean).join("");
    f.innerHTML = items;
    return f;
  }
  function cabezaColumnas() {
    const d = document.createElement("div");
    d.className = "cabeza-col rotulo";
    d.innerHTML = `<span>Quién</span><span>Lo que se dijo</span><span>Lo que entendió Jev</span>`;
    return d;
  }

  // ------------------------------------------------------------------ un acta que se va componiendo (directo y reproducción)
  function crear(cont) {
    cont.classList.add("acta");
    let parcialNodo = null, ultimaLlama = null;
    const api = {
      limpiar() { cont.innerHTML = ""; parcialNodo = null; ultimaLlama = null; cont.appendChild(cabezaColumnas()); },
      evento(ev) {
        if (ev.tipo === "dice" && ev.quien === "llama" && parcialNodo) { parcialNodo.remove(); parcialNodo = null; }
        const n = nodoEvento(ev);
        cont.appendChild(n);
        if (ev.tipo === "dice" && ev.quien === "llama") ultimaLlama = n;
        api.abajo();
        return n;
      },
      parcial(texto, t) {
        if (!parcialNodo) { parcialNodo = nodoEvento({ tipo: "dice", quien: "llama", texto, t, parcial: true }); cont.appendChild(parcialNodo); }
        else parcialNodo.querySelector(".texto").textContent = mask(texto);
        api.abajo();
      },
      glosar(glosas, ms) {
        const n = parcialNodo || ultimaLlama;
        if (n) n.querySelector(".glosas").innerHTML = htmlGlosas(glosas, ms);
      },
      abajo() { const sc = cont.closest(".pliego-cuerpo"); if (sc) sc.scrollTop = sc.scrollHeight; },
    };
    api.limpiar();
    return api;
  }

  function pintar(cont, acta, { conCabecera = true } = {}) {
    cont.innerHTML = "";
    cont.classList.add("acta");
    if (conCabecera) { cont.appendChild(cabecera(acta)); cont.appendChild(resumen(acta)); }
    cont.appendChild(cabezaColumnas());
    for (const ev of acta.eventos || []) cont.appendChild(nodoEvento(ev));
    if (conCabecera) cont.appendChild(cifras(acta));
    cont.querySelectorAll(".acta-linea, .acta-marca, .puerta, .escrito, .sello").forEach((n) => { n.style.animation = "none"; });
  }

  // reproduce un acta con sus tiempos (comprimiendo los silencios largos)
  function reproducir(cont, acta, { velocidad = 1.6, maxHueco = 2.2, onEvento = null, onFin = null } = {}) {
    const hoja = crear(cont);
    const evs = acta.eventos || [];
    let i = 0, parado = false, timer = null;
    const paso = () => {
      if (parado) return;
      if (i >= evs.length) { onFin && onFin(); return; }
      const ev = evs[i++];
      hoja.evento(ev);
      onEvento && onEvento(ev);
      const sig = evs[i];
      let espera = sig ? Math.min(maxHueco, Math.max(0.25, (sig.t || 0) - (ev.t || 0))) : 0;
      if (ev.tipo === "dice") espera = Math.max(espera, Math.min(2.6, String(ev.texto || "").length * 0.028));
      timer = setTimeout(paso, (espera * 1000) / velocidad);
    };
    paso();
    return { parar() { parado = true; if (timer) clearTimeout(timer); } };
  }

  window.Acta = { crear, pintar, reproducir, glosasDeJev, cabecera, resumen, cifras, mask, esc, coma, reloj, resTexto, resClase, fechaLarga, IDIOMA };
})();
