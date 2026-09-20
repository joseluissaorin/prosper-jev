/* La mesa: el parte, las dudas, las actas, el libro de la casa y la nota. Todo se pinta de /datos/, que sale de las trazas. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const coma = (v, n = 1) => Number(v).toFixed(n).replace(".", ",");
  const miles = (n) => Number(n || 0).toLocaleString("es-ES");
  const json = async (u) => { const r = await fetch(u); if (!r.ok) throw new Error(u); return r.json(); };
  const RES = { BOOK: "Cita", RESCHEDULE: "Cambio", CANCEL: "Anulación", REGISTER: "Alta", ESCALATE: "112", NO_ACTION: "Sin gestión" };
  const IDIOMA = { es: "castellano", en: "inglés", ca: "catalán", gl: "gallego", eu: "euskera", fr: "francés" };
  const guardado = (k, def) => { try { return JSON.parse(localStorage.getItem(k) || "null") ?? def; } catch { return def; } };
  const guardar = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };
  const barra = (nombre, bien, casos) => {
    const pct = casos ? Math.round((bien / casos) * 100) : 0;
    return `<div class="barra-fila"><span>${esc(nombre)}</span><span class="pista"><i class="${pct < 80 ? "baja" : ""}" style="width:${pct}%"></i></span><span class="n">${miles(bien)}/${miles(casos)}</span></div>`;
  };

  // ------------------------------------------------------------------ el parte
  (async () => {
    let p; try { p = await json("/datos/parte.json"); } catch { return; }
    const dias = p.dias || [];
    const pintar = (i) => {
      const d = dias[i]; if (!d) return;
      $("parte-texto").innerHTML = `<h3>${esc(d.titulo)}</h3>` + (d.parrafos || []).map((x) =>
        `<p>${esc(x.texto)}${(x.evidencia || []).map((id, k) => `<a class="ev" href="/acta?id=${encodeURIComponent(id)}" title="Ver el acta que lo prueba">acta ${k + 1}</a>`).join("")}</p>`).join("") +
        `<p class="firma">Dígame, recepción de Clínica Arenal. Si algo de esto no le cuadra, pulse en el acta y léalo usted.</p>`;
      const c = d.cifras || {}, id = c.idiomas || {};
      const fila = (k, v) => (v == null ? "" : `<div><span>${k}</span><b>${v}</b></div>`);
      $("parte-cifras").innerHTML = `<div class="dias">${dias.map((x, k) => `<button type="button" data-i="${k}" class="${k === i ? "activo" : ""}">${esc(new Date(x.fecha + "T12:00:00").toLocaleDateString("es-ES", { weekday: "short", day: "numeric", month: "short" }))}</button>`).join("")}</div>` +
        fila("llamadas", miles(c.llamadas)) + fila("con algo escrito", miles(c.gestiones)) + fila("citas nuevas", miles(c.citas)) + fila("cambios", miles(c.cambios)) +
        fila("anulaciones", miles(c.anulaciones)) + fila("altas", miles(c.altas)) + fila("urgencias al 112", miles(c.urgencias)) + fila("declinadas", miles(c.declinadas)) +
        fila("sin hueco", miles(c.sin_hueco)) + Object.entries(id).map(([k, v]) => fila("en " + (IDIOMA[k] || k), miles(v))).join("") +
        fila("Jev, mediana", c.mediana_jev_ms ? c.mediana_jev_ms + " ms" : null) + fila("ya pensadas al callar", c.especulacion_pct != null ? coma(c.especulacion_pct) + " %" : null);
      $("parte-cifras").querySelectorAll(".dias button").forEach((b) => b.addEventListener("click", () => pintar(+b.dataset.i)));
    };
    pintar(0);
  })();

  // ------------------------------------------------------------------ las dudas
  (async () => {
    let d; try { d = await json("/datos/dudas.json"); } catch { return; }
    const resueltas = guardado("digame-dudas", {});
    const TIPO = { margen: "margen estrecho", no_supe: "no lo supe", oido: "lo oí mal", invento: "el modelo inventó" };
    const efecto = (x, r) => x.tipo === "no_supe" ? `Anotado: «${esc(r)}». Entra en el banco de respuestas y esta llamada queda como caso de prueba.`
      : x.tipo === "invento" ? `Anotado: «${esc(r)}». La guardia se queda como está y la llamada entra en el ensayo.`
      : `Anotado: «${esc(r)}». Queda como criterio de la casa y esta llamada entra en el ensayo como caso de prueba.`;
    const pintar = () => {
      const lista = d.dudas || [], pend = lista.filter((x) => !resueltas[x.id]).length;
      $("p-dudas").textContent = pend ? pend : "";
      $("dudas-lista").innerHTML = lista.map((x) => {
        const r = resueltas[x.id];
        const ops = x.tipo === "no_supe"
          ? `<form class="ops" data-id="${x.id}"><input type="text" placeholder="Cuénteselo aquí" style="flex:1;min-width:8rem;border:1px solid var(--tinta);background:none;font:inherit;font-size:.95rem;padding:.35rem .5rem"><button type="submit">Decírselo</button></form>`
          : `<div class="ops" data-id="${x.id}">${(x.opciones || ["Sí", "No"]).map((o) => `<button type="button">${esc(o)}</button>`).join("")}</div>`;
        return `<article class="duda ${r ? "resuelta" : ""}"><span class="rotulo"><span class="t">${esc(TIPO[x.tipo] || x.tipo)}</span></span><h4>${esc(x.titulo)}</h4>` +
          (x.cita ? `<blockquote>${esc(x.cita)}</blockquote>` : "") + `<span class="detalle">${esc(x.detalle || "")}</span>` +
          `<a class="ver" href="/acta?id=${encodeURIComponent(x.acta)}">Leer el acta →</a>` +
          (r ? `<p class="hecho">${efecto(x, r)}</p>` : `<p class="pregunta">${esc(x.pregunta || "")}</p>${ops}`) + `</article>`;
      }).join("");
    };
    $("dudas-lista").addEventListener("click", (e) => {
      const b = e.target.closest(".ops button[type=button]"); if (!b) return;
      resueltas[b.parentElement.dataset.id] = b.textContent; guardar("digame-dudas", resueltas); pintar();
    });
    $("dudas-lista").addEventListener("submit", (e) => {
      e.preventDefault();
      const f = e.target.closest("form"), v = f && f.querySelector("input").value.trim(); if (!v) return;
      resueltas[f.dataset.id] = v; guardar("digame-dudas", resueltas); pintar();
    });
    pintar();
  })();

  // ------------------------------------------------------------------ las actas
  (async () => {
    let ind; try { ind = await json("/datos/actas/indice.json"); } catch { return; }
    const todas = ind.actas || [], dest = new Set(ind.destacadas || []);
    $("cab-total").textContent = miles(ind.total); $("p-actas").textContent = miles(ind.total);
    const cuenta = {};
    for (const a of todas) for (const e of a.etiquetas || []) cuenta[e] = (cuenta[e] || 0) + 1;
    const etiquetas = Object.entries(cuenta).sort((a, b) => b[1] - a[1]).slice(0, 16);
    let filtro = "destacadas", n = 40;
    const vista = () => (filtro === "todas" ? todas : filtro === "destacadas" ? todas.filter((a) => dest.has(a.id)) : todas.filter((a) => (a.etiquetas || []).includes(filtro)));
    const pintar = () => {
      $("actas-filtros").innerHTML = [["destacadas", "Destacadas", dest.size], ["todas", "Todas", todas.length], ...etiquetas.map(([e, c]) => [e, e, c])]
        .map(([k, t, c]) => `<button type="button" data-k="${esc(k)}" class="${k === filtro ? "activo" : ""}">${esc(t)} · ${miles(c)}</button>`).join("");
      const v = vista();
      $("actas-lista").innerHTML = v.slice(0, n).map((a) => {
        const r0 = String(a.resultado || "NO_ACTION").split(",")[0].trim();
        const f = new Date(a.fecha);
        return `<a class="acta-fila" href="/acta?id=${encodeURIComponent(a.id)}"><time>${esc(f.toLocaleString("es-ES", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Madrid" }))}</time>` +
          `<span class="res ${r0}">${esc(String(a.resultado || "NO_ACTION").split(",").map((x) => RES[x.trim()] || x.trim()).join(" + "))}</span>` +
          `<span class="resumen">${esc(a.resumen || "")}</span>` +
          `<span class="meta">${esc(IDIOMA[a.idioma] || a.idioma || "")} · ${Math.round(a.dur || 0)} s · ${a.turnos || 0} turnos${a.dudas ? ` · <span style="color:var(--terracota)">${a.dudas} ${a.dudas === 1 ? "duda" : "dudas"}</span>` : ""}</span></a>`;
      }).join("") || `<p class="rotulo" style="padding:1rem 0">Ninguna con ese filtro.</p>`;
      $("actas-mas").hidden = v.length <= n;
      $("actas-cuenta").textContent = `${miles(Math.min(n, v.length))} de ${miles(v.length)}`;
    };
    $("actas-filtros").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; filtro = b.dataset.k; n = 40; pintar(); });
    $("actas-mas").addEventListener("click", () => { n += 80; pintar(); });
    pintar();
  })();

  // ------------------------------------------------------------------ el libro de la casa
  (async () => {
    let l; try { l = await json("/datos/libro.json"); } catch { return; }
    const propias = guardado("digame-reglas", []);
    const TIPO = { volante: "volante", edad: "edad", cobertura: "cobertura", cupo: "cupo", baja: "baja", sede: "sede", horario: "horario", limite: "límite", duracion: "duración", otro: "de la casa" };
    const pintar = () => {
      const ed = (l.edicion || 1) + propias.length;
      $("cab-edicion").textContent = ed; $("p-libro").textContent = `ed. ${ed}`;
      const filas = (l.reglas || []).map((r) => {
        const en = r.ensayo;
        return `<article class="regla"><div><h4>${esc(r.texto)}</h4><span class="tipo">${esc(TIPO[r.tipo] || r.tipo)}</span></div><div class="prueba">` +
          `aplicada en <b>${miles(r.aplicada)}</b> ${r.aplicada === 1 ? "llamada real" : "llamadas reales"}<br>` +
          (en ? `ensayo: <b>${miles(en.bien)} de ${miles(en.casos)}</b> bien<br>` : `ensayo: sin casos propios todavía<br>`) +
          (r.actas || []).slice(0, 3).map((id, k) => `<a href="/acta?id=${encodeURIComponent(id)}">acta ${k + 1} →</a>`).join("") + `</div></article>`;
      }).join("");
      const mias = propias.map((r) => `<article class="regla"><div><h4>${esc(r.texto)}</h4><span class="tipo">${esc(TIPO[r.tipo] || r.tipo)} · dictada por usted</span></div><div class="prueba">ensayo antes de publicar: <b>${r.bien} de ${r.casos}</b> bien<br>publicada en la edición ${r.edicion}<br><a href="#" data-quitar="${r.id}">retirarla (volver a la edición anterior)</a></div></article>`).join("");
      $("libro-lista").innerHTML = filas + mias;
    };
    $("libro-lista").addEventListener("click", (e) => {
      const a = e.target.closest("[data-quitar]"); if (!a) return; e.preventDefault();
      const i = propias.findIndex((r) => String(r.id) === a.dataset.quitar); if (i >= 0) { propias.splice(i, 1); guardar("digame-reglas", propias); pintar(); }
    });

    // dictar una regla pasa por la puerta: leerla en voz alta, un «sí» claro, ensayo y publicación
    const clasificar = (t) => /volante|derivaci/i.test(t) ? "volante" : /años|edad|menor|mayor de|niñ/i.test(t) ? "edad" : /segur|mutua|cubre|póliza|poliza|sanitas|adeslas|asisa|dkv|mapfre/i.test(t) ? "cobertura"
      : /minutos|duran|duración/i.test(t) ? "duracion" : /mañana|tarde|lunes|martes|miércoles|jueves|viernes|sábado|domingo|horario|cierra|abre|festivo/i.test(t) ? "horario" : /baja|vacaciones|no viene|ausente/i.test(t) ? "baja" : "otro";
    const LECTURA = { volante: "No daré esa cita si en la ficha no consta el volante; lo diré así y ofreceré pedirlo.", edad: "Miraré la fecha de nacimiento de la ficha, no lo que me digan, antes de ofrecer hueco.", cobertura: "Antes de decir que no, preguntaré si tienen otro seguro o si prefieren venir como privado.",
      duracion: "Buscaré solo huecos de esa duración y no partiré ninguno.", horario: "Filtraré la agenda con esa franja antes de leer ninguna oferta.", baja: "Ofreceré a otro profesional de la misma especialidad o su primera fecha a la vuelta.", otro: "No encaja en ninguna plantilla verificable: mientras no la pueda ensayar con oráculo, en lo que toque a esta regla bajaré sola a «propone» y decidirá una persona." };
    $("dictar").addEventListener("submit", (e) => {
      e.preventDefault();
      const texto = $("dictar-texto").value.trim().replace(/^[«"“]|[»"”]$/g, ""); if (texto.length < 8) return;
      const tipo = clasificar(texto), verificable = tipo !== "otro";
      $("lectura").innerHTML = `<div class="lectura"><span class="rotulo">La puerta · se lo leo antes de escribirlo</span>Entendido. La regla sería: <b style="font-weight:500">«${esc(texto)}»</b>. La archivo como regla de <em>${esc(TIPO[tipo])}</em>. ${esc(LECTURA[tipo])} Antes de publicarla, la ensayo. ¿Es eso?<div class="ops"><button type="button" class="boton voz" id="regla-si">Sí, ensáyela</button><button type="button" class="boton hueco" id="regla-no">No, la corrijo</button></div></div>`;
      $("regla-no").onclick = () => { $("lectura").innerHTML = ""; $("dictar-texto").focus(); };
      $("regla-si").onclick = async () => {
        const casos = 40, caja = $("lectura").querySelector(".lectura");
        caja.innerHTML = `<span class="rotulo">Ensayo general · ${casos} llamantes</span><span id="ens-txt">Genero pacientes que chocan con la regla y otros que no…</span><div class="barra-fila" style="grid-template-columns:1fr 4rem;margin-top:.7rem"><span class="pista"><i id="ens-barra" style="width:0%"></i></span><span class="n" id="ens-n">0/${casos}</span></div>`;
        const bien = verificable ? casos - (Math.random() < 0.5 ? 0 : 1) : 0;
        for (let i = 1; i <= casos; i++) { await new Promise((r) => setTimeout(r, 45)); $("ens-barra").style.width = (i / casos) * 100 + "%"; $("ens-n").textContent = `${i}/${casos}`; }
        const edicion = (l.edicion || 1) + propias.length + 1;
        if (verificable) {
          propias.push({ id: Date.now(), texto, tipo, casos, bien, edicion }); guardar("digame-reglas", propias); pintar();
          caja.innerHTML = `<span class="rotulo" style="color:var(--azul)">Publicada · edición ${edicion}</span>${bien} de ${casos} llamantes la respetaron${bien < casos ? "; el que falló queda en las dudas para que usted lo lea" : ""}. Desde la próxima llamada la aplico. Si se arrepiente, retirarla es volver a la edición ${edicion - 1}. <span class="rotulo" style="display:block;margin-top:.5rem">En esta demostración el ensayo es una simulación y la regla se queda en su navegador.</span>`;
        } else {
          propias.push({ id: Date.now(), texto, tipo, casos: 0, bien: 0, edicion }); guardar("digame-reglas", propias); pintar();
          caja.innerHTML = `<span class="rotulo">Publicada como regla libre · edición ${edicion}</span>No he podido construir un oráculo para ensayarla, así que no la doy por verificada: cuando una llamada toque esta regla, propondré y decidirá una persona. La confianza también va por regla.`;
        }
        $("dictar-texto").value = "";
      };
    });
    pintar();
  })();

  // ------------------------------------------------------------------ la nota
  (async () => {
    let n; try { n = await json("/datos/nota.json"); } catch { return; }
    const nota = coma(n.nota, 1);
    $("p-nota").textContent = nota;
    $("nota-num").firstChild.textContent = nota;
    $("nota-pie").textContent = `${miles(n.bien)} de ${miles(n.casos)} llamadas de ensayo bien · ${n.pasadas} pasadas`;
    $("nota-familias").innerHTML = (n.por_familia || []).map((f) => barra(f.nombre || f.familia, f.bien, f.casos)).join("");
    $("nota-comp").innerHTML = (n.por_comportamiento || []).slice(0, 12).map((f) => barra(f.nombre || f.comportamiento, f.bien, f.casos)).join("");
    $("nota-reparto").innerHTML = (n.reparto || []).slice(0, 12).map((f) => barra(f.nombre || f.persona, f.bien, f.casos)).join("");
    $("nota-varianza").innerHTML = (n.varianza || []).slice(0, 8).map((f) => barra("semilla " + f.semilla, f.bien, f.casos)).join("");
    const v = n.voz || {};
    $("nota-voz").innerHTML = `<b>${miles(v.bien)} de ${miles(v.casos)}</b> llamadas de voz bien<br>del fin de voz a la primera palabra:<br>mediana <b>${miles(v.mediana_ms)} ms</b> · p90 <b>${miles(v.p90_ms)} ms</b>`;
    const c = n.coste;
    $("nota-coste").innerHTML = c ? `llamada media <b>${coma(c.media_usd, 3)} $</b> · p90 <b>${coma(c.p90_usd, 3)} $</b><br>` + (c.desglose || []).map((d) => `${esc(d.parte)}: <b>${coma(d.usd, 5)} $</b>`).join("<br>") + `<br><span style="color:var(--apagado)">${esc(c.nota || "")}</span>` : "Se mide desde hoy en cada llamada.";
    const la = n.latencias;
    $("nota-lat").innerHTML = la ? (la.filas || []).map((f) => `${esc(f.que)}: <b>${esc(f.mediana)}</b> <span style="color:var(--apagado)">(p90 ${esc(f.p90)})</span>`).join("<br>") + (la.especulacion ? `<br>respuesta ya pensada al callar: <b>${coma(la.especulacion.pct)} %</b> de los turnos, con <b>${miles(la.especulacion.ventaja_mediana_ms)} ms</b> de ventaja` : "") + `<br><span style="color:var(--apagado)">N = ${miles(la.n)} llamadas de voz</span>` : "";
    const m = n.marcador || {};
    $("nota-marcador").innerHTML = `<b>${m.puntos} de ${m.de}</b> puntos<br>los <b>${m.problemas}</b> problemas, con todos sus casos acreditados<br><span style="color:var(--apagado)">marcador automático y literal: o coincide lo declarado, o cero</span>`;
    if (n.guardias && n.guardias.length) {
      $("nota-guardias").innerHTML = `<span class="rotulo" style="display:block;padding:.8rem 0 0"><b>Guardias</b> · defectos del modelo, del oído o de la conversación que el código vio y paró · N = ${miles(n.guardias_n || 0)}</span>` +
        n.guardias.map((g) => `<article><h4>${esc(g.modo)}</h4><p><span class="rotulo">${esc(g.familia)} · <span style="text-transform:none;letter-spacing:0">${esc(g.evento)}</span></span></p><span class="estado">${miles(g.llamadas)} llamadas · ${esc(String(g.por_100))} de cada 100</span></article>`).join("") +
        (n.guardias_resumen ? `<p style="margin:1rem 0 0;font-style:italic;max-width:48rem">${esc(n.guardias_resumen)}</p>` : "");
    }
    $("nota-fallos").innerHTML = `<span class="rotulo" style="display:block;padding:.8rem 0 0"><b>Modos de fallo abiertos</b> · lo que sabemos que aún hace mal</span>` +
      (n.modos_de_fallo || []).map((f) => `<article><h4>${esc(f.nombre)}</h4><p>${esc(f.que)}</p><span class="estado">${esc(f.estado)}</span></article>`).join("");
    $("suspensos").innerHTML = `<span class="rotulo" style="display:block;padding:.9rem 0 .2rem"><b>Suspensos</b> · se pueden leer enteros</span>` + (n.suspensos || []).map((s) =>
      `<details class="suspenso"><summary><span class="fam">${esc(s.familia)}</span><span class="obj">${esc(s.objetivo)}</span><span class="pq">${esc(String(s.por_que || "").slice(0, 150))}</span></summary><div class="turnos">` +
      (s.turnos || []).map((t) => `<p><b>Llama</b>${esc(t.dijo || "")}</p><p class="ag"><b>Recepción</b>${esc(t.agente || "")}</p>`).join("") + `</div></details>`).join("");
  })();
})();
