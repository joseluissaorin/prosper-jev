"""Los modos de fallo, con nombre y con número: qué se rompe, qué lo para y cada cuánto pasa.

Lee los informes de llamada (`agent/calls/<id>.json`, o cualquier carpeta con ellos) y saca de sus trazas:

- la TAXONOMÍA: cada tipo de evento que denota un defecto o una guardia → qué modo de fallo es, qué guardia lo caza,
  en cuántas llamadas aparece, su tasa por 100 llamadas y una llamada de ejemplo para ir a mirarla;
- los modos DERIVADOS que no tienen evento propio (una herramienta que devuelve error, la puerta que se cierra ante
  una escritura, una llamada que pasa de 180 s, una llamada que cuelga sin declarar nada);
- cómo acaban las llamadas (outcome, reason, ended_by), cuánto duran, cuánto tarda cada paso de un turno (Jev, el
  planificador, las herramientas) y cuánto rinde la especulación;
- una ESTIMACIÓN del gasto medible con lo que ya guardan los informes antiguos (los nuevos traen el bloque `cost`).

    python agent/modos.py                                   # agent/calls, cerebro v2
    python agent/modos.py /tmp/nascalls --brain v2 --md > docs/modos-de-fallo.md
    python agent/modos.py /tmp/nascalls --desde 2026-09-19  # solo informes con fecha de fichero posterior
    python agent/modos.py --brain todos

Un evento de guardia NO es una llamada fallida: es un defecto del modelo, del oído o de la conversación que el
código vio y paró. Lo que esta tabla no puede ver son los fallos que nadie caza: para eso están los arneses.
Sin red y sin dependencias: solo lee ficheros.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from coste import PRECIOS  # noqa: E402
from estadistica import percentil, wilson  # noqa: E402

# Supuestos de la ESTIMACIÓN en dinero para los informes que no traen bloque `cost` (todos los anteriores al 20-09-2026).
# No son medidas: salen de contar caracteres sin red (prompt del planificador con el catálogo de la clínica simulada:
# 11 296 caracteres de sistema + 7 267 de herramientas; preguntas de Jev: 7 051) a ~4 caracteres por token, sin contar
# la conversación que se va acumulando. La salida, ~60 tokens por paso (sistema-1-sistema-2.md). Dos sesiones de oído.
SUPUESTOS = {"tok_jev": 1800, "tok_plan_entrada": 4700, "tok_plan_salida": 60, "sesiones_oido": 2}


def estimar(perc: float, pasos: float, chars: float, dur_s: float) -> dict:
    """USD de una llamada a partir de los recuentos de su traza, con los precios de lista de coste.py."""
    pe, ps = PRECIOS["planificador_mtok"]["openai/gpt-oss-120b"]
    u = {"jev": perc * SUPUESTOS["tok_jev"] * PRECIOS["jev_mtok"] / 1e6,
         "planificador": pasos * (SUPUESTOS["tok_plan_entrada"] * pe + SUPUESTOS["tok_plan_salida"] * ps) / 1e6,
         "voz (cota superior: como si nada estuviera en caché)": chars * PRECIOS["tts_kchar"]["elevenlabs"] / 1e3,
         "oído": dur_s / 60 * SUPUESTOS["sesiones_oido"] * PRECIOS["stt_min"]}
    u["total"] = sum(u.values())
    return u

# tipo de evento → (familia, el modo de fallo en una línea, la guardia que lo caza)
MODOS: dict[str, tuple[str, str, str]] = {
    # ---- el planificador inventa o altera datos
    "identifier_invented": ("modelo inventa", "El planificador pasa a identify_patient un DNI, teléfono o fecha de nacimiento que quien llama no ha dicho.",
                            "Solo valen las cifras que aparecen en lo dicho por quien llama; el dato se descarta y se busca sin él."),
    "nid_invented": ("modelo inventa", "Al dar de alta, el planificador rellena un DNI/NIE que nadie ha dictado.",
                     "register_patient lo rechaza y manda pedirlo dígito a dígito."),
    "phone_invented": ("modelo inventa", "Al dar de alta, teléfono inventado (o los dígitos del DNI puestos como teléfono).",
                       "register_patient lo rechaza y manda pedir el teléfono."),
    "dob_invented": ("modelo inventa", "Al dar de alta, fecha de nacimiento que quien llama no ha dicho.",
                     "register_patient la rechaza y manda pedirla."),
    "email_invented": ("modelo inventa", "Al dar de alta, correo electrónico inventado.", "register_patient lo descarta."),
    "address_invented": ("modelo inventa", "Al dar de alta, dirección inventada.", "register_patient la descarta."),
    "specialty_invented": ("modelo inventa", "Busca huecos de una especialidad que nadie ha pedido (la deduce de un síntoma o al azar).",
                           "find_slots se niega y manda preguntar para qué es la cita."),
    "name_placeholder": ("modelo inventa", "Identifica con un nombre de relleno («the patient», «caller», «unknown») en vez de preguntarlo.",
                         "identify_patient lo rechaza y pide nombre y apellidos."),
    "name_is_doctor": ("modelo inventa", "Toma el nombre del médico («con la doctora Ortiz») por el del paciente.",
                       "identify_patient compara con el cuadro médico y pide el nombre del paciente."),
    "truth_guard": ("modelo inventa", "La respuesta afirma algo que las herramientas no han hecho o dicho: «queda reservada» sin escritura, o una hora que no sale de ningún hueco.",
                    "Guardia de verdad: Jev juzga si la frase afirma una escritura y el código coteja las horas dichas con las de las herramientas; la frase se sustituye."),
    "guard": ("modelo inventa", "La respuesta repite en voz alta un dato protegido (fecha, DNI, teléfono) que quien llama no ha dicho.",
              "Se tachan de la frase las cifras que no vienen de quien llama."),
    # ---- el planificador añade o cambia restricciones
    "site_ignored": ("modelo restringe", "Filtra por una sede que nadie ha pedido y esconde huecos más tempranos en las otras.",
                     "find_slots quita el filtro de sede."),
    "site_corrected": ("modelo restringe", "Busca en una sede distinta de la que pidió quien llama (nombre mal oído o mal copiado).",
                       "La sede la fija Jev emparejando por sonido; find_slots impone esa."),
    "provider_ignored": ("modelo restringe", "Elige un médico por su cuenta cuando solo se pidió la especialidad.",
                         "find_slots quita el filtro de médico."),
    "specialty_corrected": ("modelo restringe", "Cambia la especialidad pedida por la que sugiere el síntoma.",
                            "find_slots impone la que pidió quien llama."),
    "constraints_ignored": ("modelo restringe", "Inventa cuándo (fechas, franja, días) sin que quien llama haya dicho nada temporal.",
                            "find_slots busca lo primero disponible."),
    "date_from_ignored": ("modelo restringe", "«Más tarde que mi cita» lo convierte en «otro día» y esconde los huecos del mismo día.",
                          "find_slots descarta ese date_from."),
    "insurers_ignored": ("modelo restringe", "Añade a la búsqueda seguros que quien llama no ha nombrado.",
                         "find_slots solo conserva los seguros nombrados."),
    # ---- la negativa y su motivo
    "decline_coerced": ("motivo de la negativa", "Declara un motivo de negativa que ninguna regla de la API ha dado en la llamada.",
                        "Se sustituye por el motivo que sí se ha visto en las herramientas."),
    "decline_oos": ("motivo de la negativa", "Pone una regla de cobertura como motivo cuando lo pedido estaba fuera de lo que se atiende por teléfono.",
                    "Se declara out_of_scope."),
    "decline_kept": ("motivo de la negativa", "Pisa un motivo firme ya establecido con uno débil.", "Se conserva el motivo firme."),
    "decline_with_offer": ("motivo de la negativa", "Da la llamada por denegada con una oferta todavía abierta sobre la mesa.",
                           "La negativa no se da por buena mientras haya oferta abierta."),
    "must_check_first": ("motivo de la negativa", "Va a negar (o a cerrar) sin haber mirado la agenda del paciente identificado.",
                         "Se bloquea una vez y se obliga a consultar antes."),
    # ---- la conversación
    "offer_rejected": ("conversación", "Quien llama rechaza el hueco leído.", "La oferta se retira y no se vuelve a proponer ese hueco."),
    "otra_forma": ("conversación", "Se ha pedido dos veces el mismo dato sin conseguirlo.", "La segunda vez se pide de otra forma (dígito a dígito, deletreado)."),
    "dejar_de_pedir": ("conversación", "Tres peticiones del mismo dato sin éxito: la llamada se está atascando.", "Se deja de pedir y se sigue por otra vía."),
    "colgar_pronto": ("conversación", "El agente iba a colgar antes de saber a qué llamaban.", "No se cuelga sin motivo de llamada."),
    "commit_on_hangup": ("conversación", "Quien llama cuelga justo después de aceptar, antes de que la puerta escriba.", "Al colgar se declara lo aceptado."),
    "leave_alternative": ("conversación", "El médico pedido está de baja y hay hueco antes con otro.", "find_slots añade la alternativa."),
    # ---- el oído y los turnos
    "undo": ("oído y turnos", "Se contestó a un turno que no había terminado: la persona siguió hablando.",
             "Deshacer: se restaura el estado anterior y se vuelve a planificar con la frase entera."),
    "barge_in": ("oído y turnos", "Quien llama habla encima del agente.", "Jev decide si es una interrupción de verdad; si lo es, el agente calla."),
    "escritura_retirada": ("oído y turnos", "Una escritura especulativa sobre un parcial mal oído.", "La escritura espera al definitivo; si no coincide, se retira."),
    "site_heard": ("oído y turnos", "El transcriptor destroza un nombre de sede; se recupera por sonido.", "Jev empareja por sonido contra las sedes reales."),
    # ---- infraestructura
    "jev_down": ("infraestructura", "Jev no responde (sin créditos, 5xx, timeout).", "El mismo juicio lo hace el planificador con salida estructurada: más lento, nunca a ciegas."),
    "planner_retry": ("infraestructura", "Un paso del planificador falla o agota el tiempo.", "Un reintento con más margen."),
    "planner_error": ("infraestructura", "El planificador falla también al reintentar.", "El turno se deshace y el agente pide que se lo repitan."),
    "circuit_open": ("infraestructura", "OpenRouter (o Gemini) deja de responder.", "Cortacircuitos con plazo: se usa el otro proveedor y se reprueba al cabo de 60 s."),
    "circuit_closed": ("infraestructura", "El proveedor caído vuelve a responder.", "El cortacircuitos se cierra."),
}
# eventos normales del funcionamiento: no son modos de fallo
NORMALES = {"line", "greet_lang", "gate", "perception", "planner", "tool", "composed", "fast_path", "prelookup", "prefetch_offer",
            "speculation_reused", "speculation_written", "would_write", "declared", "ack", "hold_ack", "filler", "lang",
            "identify_by_line", "nearest_site", "early_answer", "patient_is_caller", "backchannel"}
SUFIJOS = ("_invented", "_ignored", "_corrected", "_coerced", "_down", "_error", "_retry")
ORDEN = ["modelo inventa", "modelo restringe", "motivo de la negativa", "conversación", "oído y turnos", "infraestructura", "derivado", "sin catalogar"]


def cargar(carpeta: Path, brain: str, desde: float | None) -> tuple[list[dict], dict]:
    calls, skipped = [], collections.Counter()
    for f in sorted(carpeta.glob("*.json")):
        try:
            if desde and f.stat().st_mtime < desde:
                skipped["anteriores a --desde"] += 1
                continue
            d = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            skipped["ilegibles"] += 1
            continue
        if not isinstance(d, dict) or "trace" not in d or "call_id" not in d:
            skipped["no son informes de llamada"] += 1
            continue
        if brain != "todos" and (d.get("brain") or "v1") != brain:
            skipped[f"otro cerebro ({d.get('brain') or 'v1'})"] += 1
            continue
        d["_mtime"] = f.stat().st_mtime
        calls.append(d)
    return calls, dict(skipped)


def analizar(calls: list[dict]) -> dict:
    n = len(calls)
    por_kind: dict = collections.defaultdict(lambda: {"llamadas": 0, "eventos": 0, "ejemplo": None})
    dist = {k: collections.Counter() for k in ("outcome", "reason", "ended_by", "language")}
    dur, lat = [], collections.defaultdict(list)
    turnos = reusados = 0
    head, por_que = [], collections.Counter()
    gasto = {"perception": [], "planner_steps": [], "tool": [], "chars_agente": [], "con_cost": 0}
    herramientas = collections.Counter()
    rechazos: dict = collections.defaultdict(lambda: {"llamadas": 0, "eventos": 0, "ejemplo": None})
    for d in calls:
        tr = d.get("trace") or []
        kinds = collections.Counter()
        vistos_rechazo: set = set()
        for e in tr:
            k = e.get("kind")
            if not k:
                continue
            kinds[k] += 1
            if k == "perception" and e.get("ms"):
                lat["Jev (percepción)"].append(e["ms"])
            elif k == "planner" and e.get("ms") is not None:
                lat["planificador (paso que redacta)"].append(e["ms"])
            elif k == "composed" and e.get("ms") is not None:
                lat["planificador (paso con herramientas, redactado por el código)"].append(e["ms"])
            elif k == "tool":
                herramientas[e.get("name")] += 1
                if e.get("ms"):
                    lat["herramienta (incluye la API de la clínica)"].append(e["ms"])
                if e.get("ms"):
                    kinds["_tool_con_llm"] += 1            # las de ms=0 las lanza el código (camino rápido), sin paso del planificador
                r = str(e.get("result", ""))
                if r.startswith('{"error"'):
                    kinds["tool_error"] += 1
                    try:
                        msg = json.loads(r).get("error", "")
                    except Exception:  # noqa: BLE001
                        msg = r[11:]
                    clave = (e.get("name"), str(msg)[:88])
                    if clave not in vistos_rechazo:
                        vistos_rechazo.add(clave)
                        rechazos[clave]["llamadas"] += 1
                    rechazos[clave]["eventos"] += 1
                    rechazos[clave]["ejemplo"] = rechazos[clave]["ejemplo"] or d.get("call_id")
            elif k == "speculation_reused":
                if e.get("head_start_ms") is not None:
                    head.append(e["head_start_ms"])
                por_que[str(e.get("why", ""))[:40].split("(")[0].strip()] += 1
            elif k == "gate" and e.get("name") == "puerta" and e.get("ok") is False:
                kinds["puerta_cerrada"] += 1
        if (d.get("outcome") or "none") == "none":
            kinds["sin_declarar"] += 1
        if (d.get("duration_s") or 0) > 180:
            kinds["mas_de_180_s"] += 1
        for k, c in kinds.items():
            x = por_kind[k]
            x["llamadas"] += 1
            x["eventos"] += c
            x["ejemplo"] = x["ejemplo"] or d.get("call_id")
        for k in dist:
            dist[k][str(d.get(k) or "(sin dato)")] += 1
        if d.get("duration_s") is not None:
            dur.append(d["duration_s"])
        turnos += kinds["perception"]
        reusados += kinds["speculation_reused"]
        gasto["perception"].append(kinds["perception"])
        gasto["planner_steps"].append(kinds["planner"] + kinds["_tool_con_llm"])
        gasto["tool"].append(kinds["tool"])
        gasto["chars_agente"].append(sum(len(t.split(":", 1)[1].strip()) for t in d.get("transcript") or []
                                         if isinstance(t, str) and t.startswith("Receptionist:")))
        gasto["con_cost"] += 1 if isinstance(d.get("cost"), dict) and "usd_total" in d["cost"] else 0
    DERIVADOS = {
        "tool_error": ("derivado", "Una herramienta devuelve error al planificador (argumentos imposibles, paciente sin identificar, id desconocido).",
                       "El error vuelve al planificador como texto con la instrucción de qué hacer; no llega a quien llama."),
        "puerta_cerrada": ("derivado", "Se intenta escribir sin un «sí» claro a lo que se acaba de leer.",
                           "La PUERTA: sin lectura previa de eso mismo y sin «sí» de Jev, no se escribe."),
        "sin_declarar": ("derivado", "La llamada termina sin ninguna acción declarada (outcome «none»).",
                         "finalize() declara al colgar; lo que queda aquí son llamadas cortadas antes o informes de prueba."),
        "mas_de_180_s": ("derivado", "La llamada pasa de los 180 s que admite el marcador.", "Ninguna en código: se vigila con esta tabla y con la réplica."),
    }
    filas = []
    for k, x in por_kind.items():
        if k in NORMALES or k.startswith("_"):
            continue
        fam, desc, guardia = MODOS.get(k) or DERIVADOS.get(k) or (
            "sin catalogar", "Evento sin describir en agent/modos.py" + (" (parece una guardia por su nombre)." if k.endswith(SUFIJOS) else "."), "(sin dato)")
        lo, hi = wilson(x["llamadas"], n)
        filas.append({"kind": k, "familia": fam, "modo": desc, "guardia": guardia, "llamadas": x["llamadas"], "eventos": x["eventos"],
                      "por_100": 100 * x["llamadas"] / n if n else 0.0, "ic": (100 * lo, 100 * hi), "ejemplo": x["ejemplo"]})
    filas.sort(key=lambda r: (ORDEN.index(r["familia"]), -r["llamadas"]))
    no_vistos = sorted(k for k in MODOS if k not in por_kind)
    con_guardia = sum(1 for d in calls if any(e.get("kind") in MODOS and MODOS[e["kind"]][0] in ("modelo inventa", "modelo restringe", "motivo de la negativa")
                                              for e in d.get("trace") or []))
    return {"n": n, "filas": filas, "rechazos": sorted(({"tool": k[0], "error": k[1], **v} for k, v in rechazos.items()), key=lambda r: -r["llamadas"]), "no_vistos": no_vistos, "dist": dist, "dur": dur, "lat": lat, "turnos": turnos, "reusados": reusados,
            "head": head, "por_que": por_que, "gasto": gasto, "herramientas": herramientas, "con_guardia_modelo": con_guardia,
            "fechas": (min((d["_mtime"] for d in calls), default=None), max((d["_mtime"] for d in calls), default=None))}


# ---------------------------------------------------------------- salida

def _n(x, dec=0) -> str:
    return "(sin dato)" if x is None else f"{x:.{dec}f}".replace(".", ",")


def _q(xs) -> tuple:
    return len(xs), percentil(xs, 50), percentil(xs, 90), percentil(xs, 99)


def _tabla(cab: list[str], filas: list[list[str]], md: bool, anchos: list[int] | None = None) -> list[str]:
    if md:
        return ["| " + " | ".join(cab) + " |", "|" + "|".join("---" for _ in cab) + "|"] + ["| " + " | ".join(str(c).replace("|", "/") for c in f) + " |" for f in filas]
    w = anchos or [max(len(str(x)) for x in [c] + [f[i] for f in filas]) for i, c in enumerate(cab)]
    corta = lambda s, k: (str(s) if len(str(s)) <= k else str(s)[:k - 1] + "…").ljust(k)  # noqa: E731
    return ["  ".join(corta(c, w[i]) for i, c in enumerate(cab)), "  ".join("─" * k for k in w)] + ["  ".join(corta(c, w[i]) for i, c in enumerate(f)) for f in filas]


def informe(a: dict, md: bool, origen: str, brain: str, skipped: dict, top: int = 12) -> str:
    n = a["n"]
    h = (lambda t: f"\n## {t}\n") if md else (lambda t: f"\n━━ {t} " + "━" * max(4, 100 - len(t)))
    out = []
    f0, f1 = a["fechas"]
    rango = f"{datetime.fromtimestamp(f0):%d-%m-%Y %H:%M} → {datetime.fromtimestamp(f1):%d-%m-%Y %H:%M}" if f0 else "(sin dato)"
    if md:
        out += ["# Modos de fallo", "",
                f"Generado por `agent/modos.py` el {datetime.now():%d-%m-%Y %H:%M} sobre **N = {n} llamadas** (cerebro {brain}) de `{origen}`; "
                f"fecha de los informes: {rango}. Descartados: {', '.join(f'{v} {k}' for k, v in skipped.items()) or 'ninguno'}.", "",
                "Un evento de guardia **no es una llamada fallida**: es un defecto del modelo, del oído o de la conversación que el código vio y paró. "
                "La tasa es de llamadas afectadas por cada 100, con su intervalo de Wilson al 95 %. Lo que esta tabla no ve son los fallos "
                "que nadie caza: para eso están los arneses (`docs/rigor.md`).", "",
                "Las llamadas son las de todo el desarrollo (arnés de voz de Prosper, rondas de práctica y pruebas propias), con versiones "
                "distintas del agente: las tasas describen el periodo, no la versión de hoy. Use `--desde` para acotar."]
    else:
        out += [f"MODOS DE FALLO · N = {n} llamadas (cerebro {brain}) de {origen} · {rango}",
                f"descartados: {', '.join(f'{v} {k}' for k, v in skipped.items()) or 'ninguno'}"]
    if not n:
        return "\n".join(out + ["", "No hay llamadas que analizar."])
    out.append(h("Taxonomía: qué falla, qué lo para y cada cuánto"))
    cab = ["familia", "evento", "modo de fallo", "guardia", "llamadas", "por 100 (IC 95 %)", "eventos", "ejemplo"]
    filas = [[r["familia"], f"`{r['kind']}`" if md else r["kind"], r["modo"], r["guardia"], f"{r['llamadas']}/{n}",
              f"{_n(r['por_100'], 1)} ({_n(r['ic'][0], 1)}–{_n(r['ic'][1], 1)})", r["eventos"], f"`{r['ejemplo']}`" if md else r["ejemplo"]] for r in a["filas"]]
    out += _tabla(cab, filas, md, [16, 20, 58, 44, 9, 18, 7, 36])
    out.append("")
    out.append(f"Llamadas con al menos una guardia sobre el planificador (inventa, restringe o motivo): {a['con_guardia_modelo']}/{n} "
               f"({_n(100 * a['con_guardia_modelo'] / n, 1)} %).")
    if a["no_vistos"]:
        out.append("Modos catalogados que no aparecen en estas llamadas: " + ", ".join(f"`{k}`" if md else k for k in a["no_vistos"]) + ".")
    out.append(h("Rechazos de las herramientas: lo que el planificador intentó y el código no dejó"))
    out.append("Desglose de `tool_error`. El mensaje es el que recibe el planificador (en inglés, como su prompt); quien llama no lo oye." if md else
               "Desglose de tool_error. El mensaje es el que recibe el planificador (en inglés, como su prompt); quien llama no lo oye.")
    out.append("")
    out += _tabla(["herramienta", "rechazo", "llamadas", "por 100", "eventos", "ejemplo"],
                  [[r["tool"], r["error"], f"{r['llamadas']}/{n}", _n(100 * r["llamadas"] / n, 1), r["eventos"], f"`{r['ejemplo']}`" if md else r["ejemplo"]]
                   for r in a["rechazos"][:top + 3]], md, [22, 90, 9, 8, 7, 36])
    if len(a["rechazos"]) > top + 3:
        out.append("")
        out.append(f"… y otros {len(a['rechazos']) - top - 3} rechazos distintos, con {sum(r['eventos'] for r in a['rechazos'][top + 3:])} eventos en total.")
    out.append(h("Cómo acaban las llamadas"))
    for k, titulo in (("outcome", "Resultado declarado (outcome)"), ("reason", "Motivo (reason)"), ("ended_by", "Quién cierra (ended_by)"), ("language", "Idioma")):
        c = a["dist"][k]
        items = c.most_common(top)
        resto = sum(c.values()) - sum(v for _, v in items)
        out.append(("**" + titulo + ":** " if md else f"{titulo}: ") + " · ".join(f"{x} {v} ({_n(100 * v / n, 1)} %)" for x, v in items)
                   + (f" · otros {resto}" if resto else ""))
        if md:
            out.append("")
    nd, p50, p90, p99 = _q(a["dur"])
    out.append(h("Duración y latencia por paso"))
    out.append(f"Duración de la llamada (n={nd}): mediana {_n(p50, 1)} s · p90 {_n(p90, 1)} s · p99 {_n(p99, 1)} s · máximo {_n(max(a['dur']), 1)} s.")
    out.append("")
    out += _tabla(["paso", "n", "mediana (ms)", "p90 (ms)", "p99 (ms)"],
                  [[k, len(v), _n(percentil(v, 50)), _n(percentil(v, 90)), _n(percentil(v, 99))] for k, v in a["lat"].items()], md)
    out.append("")
    out.append("Herramientas más usadas: " + " · ".join(f"{k} {v}" for k, v in a["herramientas"].most_common(8)) + ".")
    out.append(h("Especulación"))
    lo, hi = wilson(a["reusados"], a["turnos"])
    nh, h50, h90, _ = _q(a["head"])
    out.append(f"Turnos (eventos de percepción): {a['turnos']} · respondidos con la especulación ya hecha: {a['reusados']} "
               f"({_n(100 * a['reusados'] / max(a['turnos'], 1), 1)} %, IC 95 % {_n(100 * lo, 1)}–{_n(100 * hi, 1)}).")
    out.append(f"Ventaja al reutilizarla (head_start_ms, n={nh}): mediana {_n(h50)} ms · p90 {_n(h90)} ms.")
    out.append("Por qué se dio por buena: " + " · ".join(f"{k or '(sin dato)'} {v}" for k, v in a["por_que"].most_common(5)) + ".")
    g = a["gasto"]
    out.append(h("Lo que gasta una llamada, contado en las trazas"))
    out += _tabla(["por llamada", "mediana", "p90", "media"],
                  [["juicios de Jev registrados (eventos perception)", _n(percentil(g["perception"], 50)), _n(percentil(g["perception"], 90)), _n(sum(g["perception"]) / n, 1)],
                   ["pasos del planificador registrados (planner + herramientas pedidas por él)", _n(percentil(g["planner_steps"], 50)), _n(percentil(g["planner_steps"], 90)), _n(sum(g["planner_steps"]) / n, 1)],
                   ["llamadas a herramientas", _n(percentil(g["tool"], 50)), _n(percentil(g["tool"], 90)), _n(sum(g["tool"]) / n, 1)],
                   ["caracteres dichos por el agente (transcripción)", _n(percentil(g["chars_agente"], 50)), _n(percentil(g["chars_agente"], 90)), _n(sum(g["chars_agente"]) / n, 1)]], md)
    out.append("")
    dur_media = sum(a["dur"]) / max(len(a["dur"]), 1)
    casos = [("llamada media", sum(g["perception"]) / n, sum(g["planner_steps"]) / n, sum(g["chars_agente"]) / n, dur_media),
             ("llamada p90 (cada recuento en su p90)", percentil(g["perception"], 90), percentil(g["planner_steps"], 90),
              percentil(g["chars_agente"], 90), percentil(a["dur"], 90))]
    out.append(("**Estimación en USD** " if md else "ESTIMACIÓN en USD ") + "(no es una medida: recuentos de la traza × supuestos de tokens × precios de lista de `agent/coste.py`). "
               f"Supuestos: {SUPUESTOS['tok_jev']} tokens por juicio de Jev, {SUPUESTOS['tok_plan_entrada']} de entrada y {SUPUESTOS['tok_plan_salida']} de salida "
               f"por paso del planificador (gpt-oss-120b en Groq), {SUPUESTOS['sesiones_oido']} sesiones de oído durante toda la llamada.")
    out.append("")
    filas_e = []
    for nombre, pc, pl, ch, du in casos:
        u = estimar(pc, pl, ch, du)
        filas_e.append([nombre] + [_n(v, 5) for v in u.values()])
    out += _tabla(["", "Jev", "planificador", "voz (cota superior, sin caché)", "oído", "total"], filas_e, md)
    out.append("")
    out.append(f"Informes con bloque `cost` medido: {g['con_cost']}/{n}. En los demás solo hay recuentos de la traza, que son una COTA INFERIOR: "
               "los juicios de Jev sobre parciales y los pasos de especulaciones descartadas no dejan evento. La fórmula y la estimación en dinero, en docs/rigor.md.")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Taxonomía de modos de fallo a partir de los informes de llamada")
    ap.add_argument("carpeta", nargs="?", default=str(HERE / "calls"))
    ap.add_argument("--brain", default="v2", help="v1 | v2 | todos (por defecto v2)")
    ap.add_argument("--desde", help="AAAA-MM-DD[THH:MM]: solo informes cuyo fichero sea posterior")
    ap.add_argument("--md", action="store_true", help="salida en Markdown")
    ap.add_argument("--json", action="store_true", help="los números en JSON (para docs/rigor.md)")
    a = ap.parse_args()
    desde = time.mktime(datetime.fromisoformat(a.desde).timetuple()) if a.desde else None
    calls, skipped = cargar(Path(a.carpeta), a.brain, desde)
    res = analizar(calls)
    if a.json:
        res["por_que"], res["herramientas"] = dict(res["por_que"]), dict(res["herramientas"])
        res["dist"] = {k: dict(v) for k, v in res["dist"].items()}
        res["lat"] = {k: {"n": len(v), "p50": percentil(v, 50), "p90": percentil(v, 90), "p99": percentil(v, 99)} for k, v in res["lat"].items()}
        for k in ("dur", "head"):
            v = res[k]
            res[k] = {"n": len(v), "p50": percentil(v, 50), "p90": percentil(v, 90), "p99": percentil(v, 99)}
        res["gasto"] = {k: ({"p50": percentil(v, 50), "p90": percentil(v, 90), "media": sum(v) / max(len(v), 1), "suma": sum(v)} if isinstance(v, list) else v)
                        for k, v in res["gasto"].items()}
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
        return
    print(informe(res, a.md, a.carpeta, a.brain, skipped))


if __name__ == "__main__":
    main()
