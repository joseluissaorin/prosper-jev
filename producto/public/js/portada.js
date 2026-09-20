/* La portada, de la tesis para abajo: las cifras reales, la propuesta por WhatsApp, el gemelo y el examen. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const coma = (v, n = 1) => Number(v).toFixed(n).replace(".", ",");
  const miles = (n) => Number(n).toLocaleString("es-ES");
  const json = async (u) => { const r = await fetch(u); if (!r.ok) throw new Error(u); return r.json(); };
  const poner = (id, v) => { const n = $(id); if (n && v != null) n.textContent = v; };

  // ------------------------------------------------------------------ cifras reales (salen de datos/, que sale de las trazas)
  (async () => {
    try {
      const ind = await json("/datos/actas/indice.json");
      for (const id of ["n-actas", "n-actas-2", "c-actas"]) poner(id, miles(ind.total));
    } catch {}
    try {
      const p = await json("/datos/parte.json");
      const d = p.dias && p.dias[0];
      if (d && d.parrafos && d.parrafos[0]) poner("m-parte", d.parrafos[0].texto.slice(0, 150).replace(/\s+\S*$/, "") + "…");
    } catch {}
    try {
      const d = await json("/datos/dudas.json");
      const x = d.dudas && d.dudas[0];
      if (x) $("m-duda").innerHTML = `<b style="font-weight:500">${esc(x.titulo)}.</b> ${esc(x.cita || "")} <span class="rotulo" style="display:block;margin-top:.4rem">${esc(x.detalle || "")}</span>`;
    } catch {}
    try {
      const l = await json("/datos/libro.json");
      const rs = (l.reglas || []).slice(0, 3);
      if (rs.length) $("m-libro").innerHTML = rs.map((r) => `${esc(r.texto)} <span class="rotulo" style="white-space:nowrap">· aplicada ${r.aplicada ?? 0}</span>`).join("<br>");
    } catch {}
    try {
      const n = await json("/datos/nota.json");
      const nota = coma(n.nota, 1);
      poner("c-nota", nota);
      $("nota-num").firstChild.textContent = nota;
      poner("nota-pie", `${miles(n.bien)} de ${miles(n.casos)} llamadas de ensayo bien · ${n.pasadas} pasadas`);
      $("nota-familias").innerHTML = (n.por_familia || []).slice(0, 8).map((f) => {
        const pct = f.casos ? Math.round((f.bien / f.casos) * 100) : 0;
        return `<div class="barra-fila"><span>${esc(f.nombre || f.familia)}</span><span class="pista"><i class="${pct < 80 ? "baja" : ""}" style="width:${pct}%"></i></span><span class="n">${f.bien}/${f.casos}</span></div>`;
      }).join("");
      $("nota-reparto").innerHTML = `<span class="rotulo" style="border:0;padding:0;width:100%">El reparto</span>` + (n.reparto || []).slice(0, 10).map((r) =>
        `<span>${esc(r.nombre || r.persona)}<small>${r.bien}/${r.casos}</small></span>`).join("");
      const a = n.ataques || {};
      contar("at-intentos", a.intentos || 0); contar("at-declinados", a.declinados || 0); contar("at-inventos", a.inventos_tirados || 0);
      $("fallos").innerHTML = `<span class="rotulo" style="display:block;padding:.8rem 0 0"><b>Modos de fallo, con nombre</b> · lo que sabemos que aún hace mal</span>` +
        (n.modos_de_fallo || []).map((f) => `<article><h4>${esc(f.nombre)}</h4><p>${esc(f.que)}</p><span class="estado">${esc(f.estado)}</span></article>`).join("");
    } catch {}
  })();
  function contar(id, hasta) {
    const n = $(id); if (!n) return;
    n.textContent = miles(hasta);                          // si nunca llega a verse animado, la cifra ya es la buena
    const io = new IntersectionObserver((es) => {
      if (!es[0].isIntersecting) return;
      io.disconnect();
      const t0 = performance.now(), dur = 1400;
      const paso = (t) => { const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3); n.textContent = miles(Math.round(hasta * e)); if (k < 1) requestAnimationFrame(paso); };
      requestAnimationFrame(paso);
    }, { threshold: 0.4 });
    io.observe(n);
  }

  // ------------------------------------------------------------------ nivel 1: la propuesta llega por WhatsApp
  const movil = $("movil");
  const espera = (ms) => new Promise((r) => setTimeout(r, ms));
  function burbuja(html, clase = "") {
    const b = document.createElement("div"); b.className = "burbuja " + clase; b.innerHTML = html; movil.appendChild(b); return b;
  }
  function respuestas(ops) {
    const d = document.createElement("div"); d.className = "respuestas";
    d.innerHTML = ops.map((o, i) => `<button type="button" data-i="${i}">${o.t}</button>`).join("");
    movil.appendChild(d);
    d.addEventListener("click", async (e) => {
      const b = e.target.closest("button"); if (!b) return;
      const o = ops[+b.dataset.i]; d.remove();
      burbuja(o.t, "mia"); await espera(650);
      await o.f();
    });
  }
  async function propuesta() {
    movil.querySelectorAll(".burbuja, .respuestas").forEach((n) => n.remove());
    burbuja(`<b style="font-weight:600">Propuesta de cita.</b> Marta Ruiz pide revisión con la doctora Ortiz. Le he ofrecido el <b style="font-weight:600">jueves 24 a las 10:00</b> en Arenal Centro y ha dicho «sí, perfecto».<span class="dato">acepta 0,98 · terminó 0,97 · 1 min 12 s<br>acta n.º 3f2a91c0 · lo leí en voz alta antes</span>`);
    respuestas([
      { t: "✓ Aprobar", f: async () => {
        burbuja(`<b style="font-weight:600">Escrito en la agenda.</b> Marta recibe la confirmación por WhatsApp y el acta queda archivada.`, "azul");
        await espera(1300);
        burbuja(`Con esta van <b style="font-weight:600">212 de 214</b> propuestas aprobadas sin cambios. ¿Quiere que a partir de ahora las escriba yo directamente?<span class="dato">eso es subir al nivel 2 · se puede bajar cuando quiera</span>`);
        respuestas([
          { t: "Sí, escriba usted", f: async () => { burbuja(`Hecho. Desde hoy escribo yo, con acta, y el paciente recibe lo acordado por si algo no cuadra. Cada mañana tendrá el parte.`, "azul"); await espera(2600); otra(); } },
          { t: "Todavía no", f: async () => { burbuja(`Entendido: sigo proponiendo y usted aprueba. Se lo volveré a preguntar cuando lleve un mes sin cambios.`); await espera(2600); otra(); } },
        ]);
      } },
      { t: "✎ Cambiar la hora", f: async () => {
        burbuja(`¿A qué hora se la cambio? Si me dice «a las 11:30», llamo a Marta, se lo propongo y, si dice que sí, se lo traigo otra vez para aprobar.`);
        await espera(2600); otra();
      } },
      { t: "☎ La llamo yo", f: async () => {
        burbuja(`Anotado: la llama usted. Le dejo en la ficha lo que pidió, para que no tenga que preguntárselo otra vez.<span class="dato">revisión · Dra. Ortiz · prefiere jueves por la mañana</span>`);
        await espera(2600); otra();
      } },
    ]);
  }
  function otra() { respuestas([{ t: "↺ Ver otra vez la propuesta", f: propuesta }, { t: "→ Probar una orden de nivel 3", f: operacion }]); }

  // nivel 3: una frase del dueño, leída y confirmada, y ella recoloca la mañana entera
  async function operacion() {
    movil.querySelectorAll(".burbuja, .respuestas").forEach((n) => n.remove());
    marcarModo("operacion");
    const cab = movil.querySelector(".movil-cab small"); if (cab) cab.textContent = "recepción · nivel 3 · opera";
    respuestas([{ t: "Decirle: «Mañana no viene la doctora Ortiz»", f: async () => {
      burbuja(`Entendido: bloqueo la agenda de la <b style="font-weight:600">Dra. Elena Ortiz</b> mañana, lunes 21. Hay <b style="font-weight:600">seis citas</b> afectadas. Puedo llamar a cada paciente, ofrecerle el primer hueco con la Dra. Roca o con la propia Dra. Ortiz a su vuelta, y traerle aquí lo que no consiga recolocar. ¿Lo hago?<span class="dato">la puerta: se lo leo antes de tocar nada</span>`);
      respuestas([
        { t: "Sí, llámelos", f: async () => {
          const pasos = ["08:02 · Marta R. → martes 22, 9:30, Dra. Roca", "08:05 · Julián P. → martes 22, 10:15, Dra. Roca", "08:09 · Carmen S. → jueves 24, 8:00, Dra. Ortiz", "08:14 · Iñaki A. → martes 22, 12:00, Dra. Roca", "08:21 · Laura R. → miércoles 23, 17:30, Dra. Roca"];
          const b = burbuja(`<b style="font-weight:600">Llamando…</b><span class="dato" id="op-lista"></span>`, "azul");
          for (const x of pasos) { await espera(900); b.querySelector("#op-lista").insertAdjacentHTML("beforeend", `${x} · acta<br>`); }
          await espera(1100);
          burbuja(`<b style="font-weight:600">Cinco recolocadas y una pendiente.</b> Don Antonio prefiere esperar a la doctora Ortiz y no hay hueco con ella hasta el jueves 1: se lo dejo en las dudas. Cada llamada tiene su acta, y cada paciente, lo acordado por WhatsApp.`);
          await espera(2600); otra();
        } },
        { t: "Solo avíselos por WhatsApp", f: async () => { burbuja(`Hecho: les escribo con dos huecos alternativos a cada uno. Lo que contesten pasa por la misma puerta, y a quien no conteste hoy a las 18:00 le llamo.`, "azul"); await espera(2600); otra(); } },
        { t: "No, ya me encargo yo", f: async () => { burbuja(`De acuerdo: solo bloqueo la agenda de mañana para que nadie reserve con ella. Las seis citas siguen como están y se las dejo listadas en el parte.`); await espera(2600); otra(); } },
      ]);
    } }]);
  }
  function marcarModo(m) { document.querySelectorAll("#movil-modos button").forEach((b) => b.classList.toggle("hueco", b.dataset.modo !== m)); }
  const modos = document.getElementById("movil-modos");
  if (modos) modos.addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; if (b.dataset.modo === "operacion") operacion(); else { const cab = movil.querySelector(".movil-cab small"); if (cab) cab.textContent = "recepción · nivel 1 · propone"; marcarModo("propuesta"); propuesta(); } });
  if (movil) new IntersectionObserver((es, io) => { if (es[0].isIntersecting) { io.disconnect(); propuesta(); } }, { threshold: 0.35 }).observe(movil);

  // ------------------------------------------------------------------ el gemelo: de una web a una recepcionista que contesta con su nombre
  const compone = $("compone"), hojaG = $("gemelo-hoja");
  const linea = (t, clase = "") => { const d = document.createElement("div"); d.className = clase; d.textContent = t; compone.appendChild(d); return d; };
  const PASOS = ["Abre la portada de su web", "Busca las páginas de equipo, servicios y contacto", "Lee quiénes son y qué hacen", "Apunta horarios, sedes y teléfono", "Separa lo que ha leído de lo que tiene que suponer", "Redacta las reglas de la casa, en borrador", "Compone el saludo, de usted"];
  let audio = null;

  // los nombres de campo del gemelo son identificadores (sin tildes): al enseñarlos se escriben como es debido
  const CAMPO = { direccion: "dirección", telefono: "teléfono", preguntas_frecuentes: "preguntas frecuentes", duraciones: "duraciones", categorias: "categorías", especialidades: "especialidades", precios: "precios" };
  const humano = (arr) => (arr || []).map((k) => CAMPO[k] || String(k).replace(/_/g, " ")).join(", ");
  // el saludo, con la hora que es de verdad en Madrid
  function saludoDeAhora(s) {
    const h = +new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", hour: "numeric", hour12: false }).format(new Date()) % 24;
    const dp = h >= 6 && h < 14 ? "buenos días" : h >= 14 && h < 21 ? "buenas tardes" : "buenas noches";
    return String(s || "").replace(/buenos d[ií]as|buenas tardes|buenas noches/i, dp);
  }
  function fuente(f) { return f === "web" ? `<small>de su web</small>` : `<small class="fuente-c">supuesto</small>`; }
  function pintarGemelo(g, paginas) {
    g.saludo = saludoDeAhora(g.saludo);
    const lista = (arr, f) => (arr && arr.length ? arr.map(f).join("") : `<li><span style="color:var(--apagado)">No lo dice su web.</span></li>`);
    const horario = (g.horario || []).map((h) => `<li><span>${esc(h.dias)}</span><small>${esc(h.abre)} a ${esc(h.cierra)}</small></li>`).join("");
    const sedes = (g.sedes || []).filter((s) => s.nombre || s.direccion).map((s) => `<li><span>${esc(s.nombre || "Sede")}</span><small>${esc(s.direccion || "")}</small></li>`).join("");
    hojaG.innerHTML = `<div class="gemelo-hoja">
      <header class="acta-cab"><h1><small>El gemelo de su recepción · borrador n.º 1</small>${esc(g.nombre || "Su clínica")}</h1>
        <div class="ficha-acta">${g.tipo ? `clínica <b>${esc(g.tipo)}</b><br>` : ""}${g.ciudad ? `${esc(g.ciudad)}<br>` : ""}${g.telefono ? `${esc(g.telefono)}<br>` : ""}${(g.idiomas || []).length ? esc(g.idiomas.join(" · ")) : ""}</div></header>
      ${g.lema ? `<p class="acta-resumen" style="font-style:italic">${esc(g.lema)}</p>` : ""}
      <div class="saludo"><button type="button" id="saludo-btn" aria-label="Escuchar el saludo">▶</button><div><span class="rotulo">Así descolgaría mañana · pulse para oírla</span><br><q id="saludo-txt">${esc(g.saludo || "")}</q></div></div>
      <div class="gemelo-cols">
        <div><h4>El equipo</h4><ul>${lista(g.equipo, (p) => `<li><span>${esc(p.nombre)}${p.especialidad ? `, <em>${esc(p.especialidad)}</em>` : ""}</span>${fuente(p.fuente)}</li>`)}</ul></div>
        <div><h4>Lo que se puede pedir</h4><ul>${lista((g.servicios || []).slice(0, 12), (s) => `<li><span>${esc(s.nombre)}${s.duracion_min ? ` <small>${s.duracion_min} min</small>` : ""}</span>${fuente(s.fuente)}</li>`)}</ul></div>
        <div><h4>Cuándo y dónde</h4><ul>${horario || `<li><span style="color:var(--apagado)">Su web no dice el horario: se lo preguntaría.</span></li>`}${sedes}${g.direccion && !sedes ? `<li><span>${esc(g.direccion)}</span></li>` : ""}${(g.seguros || []).length ? `<li><span>Seguros: ${esc(g.seguros.join(", "))}</span></li>` : ""}</ul></div>
        <div style="grid-column:1/-1"><h4>El libro de la casa · borrador para que usted lo corrija</h4><ul>${lista(g.reglas, (r) => `<li><span>${esc(r.texto)}</span>${fuente(r.fuente)}</li>`)}</ul></div>
        <div style="grid-column:1/-1"><h4>Lo que le preguntaría el primer día</h4><ul>${lista(g.preguntas_frecuentes, (p) => `<li><span>${esc(p.pregunta)}${p.respuesta ? ` <em style="color:var(--apagado)">${esc(p.respuesta)}</em>` : ""}</span>${p.respuesta ? fuente(p.fuente) : `<small class="fuente-c">no lo sé: ¿me lo cuenta?</small>`}</li>`)}</ul></div>
      </div>
      <p class="gemelo-nota"><b style="color:var(--tinta)">Leído de su web:</b> ${esc(humano(g.leido_de_la_web) || "nada seguro")}. <b style="color:var(--terracota)">Supuesto, para que lo corrija:</b> ${esc(humano(g.completado) || "nada")}. Nunca se inventan el nombre, la dirección, el teléfono, el horario ni los médicos.${paginas && paginas.length ? ` Páginas leídas: ${paginas.map((p) => esc(p.replace(/^https?:\/\//, ""))).join(" · ")}.` : ""}</p>
      <p style="margin:1.4rem 0 0;display:flex;gap:.6rem;flex-wrap:wrap"><a class="boton voz" href="mailto:jl@joseluissaorin.com?subject=${encodeURIComponent("Semana de escucha · " + (g.nombre || ""))}">Empezar la semana de escucha con este gemelo</a><button type="button" class="boton hueco" data-llamar-g>Mientras, llamar a la clínica de pruebas</button></p>
    </div>`;
    $("saludo-btn").addEventListener("click", () => decir(g.saludo));
    hojaG.querySelector("[data-llamar-g]").addEventListener("click", () => { scrollTo({ top: 0, behavior: "smooth" }); setTimeout(() => $("btn-llamar").click(), 400); });
    hojaG.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  async function decir(texto) {
    const b = $("saludo-btn"); if (!b || !texto) return;
    if (audio && !audio.paused) { audio.pause(); b.textContent = "▶"; return; }
    b.textContent = "…";
    try {
      const r = await fetch("/api/saludo", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ texto: texto.slice(0, 220) }) });
      if (!r.ok) throw new Error("sin voz");
      audio = new Audio(URL.createObjectURL(await r.blob()));
      audio.onended = () => { b.textContent = "▶"; };
      await audio.play(); b.textContent = "❚❚";
      window.Arena && window.Arena.onda(1);
    } catch { b.textContent = "▶"; $("saludo-txt").insertAdjacentHTML("afterend", ` <span class="rotulo">(la voz no está disponible ahora)</span>`); }
  }
  async function componer(url) {
    const btn = $("gemelo-btn");
    btn.disabled = true; btn.textContent = "Leyendo…"; compone.innerHTML = ""; hojaG.innerHTML = "";
    let i = 0;
    linea(PASOS[i++]);
    const tic = setInterval(() => { if (i < PASOS.length) { compone.lastChild.className = "ok"; linea(PASOS[i++]); } }, 1100);
    try {
      const t0 = performance.now();
      const r = await fetch("/api/gemelo", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ url }) });
      const j = await r.json().catch(() => ({}));
      if (j.ok) await new Promise((ok) => setTimeout(ok, Math.max(0, 3300 - (performance.now() - t0))));   // que dé tiempo a ver cómo compone
      clearInterval(tic);
      if (!j.ok) throw new Error(j.error || "modelo");
      compone.querySelectorAll("div").forEach((d) => { d.className = "ok"; });
      linea(j.cache ? "Gemelo compuesto (ya lo tenía leído de hoy)" : `Gemelo compuesto en ${coma((j.ms || 0) / 1000)} s`, "ok");
      pintarGemelo(j.gemelo, j.paginas);
    } catch (e) {
      clearInterval(tic);
      const MSG = { no_se_pudo_leer: "No he podido abrir esa web. Compruebe la dirección.", no_parece_una_clinica: "He leído la página, pero no parece la de una clínica o un centro con citas.", limite: "Hoy ya se han compuesto muchos gemelos desde aquí. Vuelva mañana o escríbanos.", modelo: "El modelo que lee la web no ha respondido. Inténtelo otra vez en un momento." };
      linea(MSG[e.message] || MSG.modelo, "mal");
    }
    btn.disabled = false; btn.textContent = "Componer";
  }
  $("gemelo-form").addEventListener("submit", (e) => {
    e.preventDefault();
    let u = $("gemelo-url").value.trim(); if (!u) return;
    if (!/^https?:\/\//i.test(u)) u = "https://" + u;
    componer(u);
  });
  const EJEMPLOS = (window.DIGAME_EJEMPLOS || []);
  if (EJEMPLOS.length) {
    $("gemelo-ejemplos").innerHTML = "o pruebe con una de verdad:" + EJEMPLOS.map((u) => `<button type="button" data-u="${esc(u)}">${esc(u.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, ""))}</button>`).join("");
    $("gemelo-ejemplos").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; $("gemelo-url").value = b.dataset.u; componer(b.dataset.u); });
  }
})();
