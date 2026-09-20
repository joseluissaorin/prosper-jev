#!/usr/bin/env python3
"""Dígame: convierte las trazas reales de llamadas en los JSON que pinta el front-end.

    python3 producto/construir.py            # lee /tmp/digame_trazas
    TRAZAS=/otra/ruta python3 producto/construir.py

Genera en producto/public/datos/: actas/<id>.json, actas/indice.json, parte.json, dudas.json, libro.json y nota.json.
Solo biblioteca estándar. Todo sale de contar: nada inventado.
"""
from __future__ import annotations

import glob
import json
import os
import re
import statistics
import sys
import types
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
TRAZAS = os.environ.get("TRAZAS", "/tmp/digame_trazas")
SALIDA = os.path.join(AQUI, "public", "datos")

try:
    from zoneinfo import ZoneInfo
    MAD = ZoneInfo("Europe/Madrid")
except Exception:  # sin base de zonas: septiembre es horario de verano
    MAD = timezone(timedelta(hours=2))

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ---------------------------------------------------------------- la clínica (agent/fake_api.py, cargada sin FastAPI)
def cargar_clinica():
    """Importa agent/fake_api.py con FastAPI y pydantic de pega: solo hacen falta sus tablas."""
    try:
        f = types.ModuleType("fastapi")

        class _App:
            def __init__(self, *a, **k):
                pass

            def get(self, *a, **k):
                return lambda fn: fn
            post = get

        class _Exc(Exception):
            def __init__(self, *a, **k):
                pass
        f.FastAPI, f.HTTPException = _App, _Exc
        f.Header = f.Query = lambda *a, **k: None
        p = types.ModuleType("pydantic")
        p.BaseModel = type("BaseModel", (), {})
        guardados = {k: sys.modules.get(k) for k in ("fastapi", "pydantic")}
        sys.modules["fastapi"], sys.modules["pydantic"] = f, p
        sys.path.insert(0, os.path.join(RAIZ, "agent"))
        try:
            import fake_api  # type: ignore
        finally:
            sys.path.pop(0)
            for k, v in guardados.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        return fake_api
    except Exception as e:  # pragma: no cover
        print(f"aviso: no se pudo cargar fake_api ({e!r}); se usan las tablas de reserva", file=sys.stderr)
        return None


API = cargar_clinica()
if API:
    MEDICOS = {p[0]: p[1] for p in API.PROVIDERS}
    MEDICO_ESP = {p[0]: p[2] for p in API.PROVIDERS}
    SEDES = {k: v["name"] for k, v in API.SITES.items()}
    SEGUROS = {k: v["name"] for k, v in API.PLANS.items()}
    PACIENTES = {k: (v["given_name"], v["first_surname"]) for k, v in API.PATIENTS.items()}
else:
    MEDICOS = {"PR01": "Dr. Pablo Requena", "PR02": "Dra. Elena Ortiz", "PR03": "Dr. Andrés Sáez", "PR04": "Dra. Lucía Sáenz",
               "PR05": "Dr. Marc Puig", "PR06": "Dra. Carmen Iglesias", "PR07": "Dr. Jordi Vilar", "PR08": "Dr. Tomás Iglesia",
               "PR09": "Dra. Núria Ferrer", "PR10": "Dra. Isabel Morán", "PR11": "D. Álvaro Cid", "PR12": "Dra. Montse Roca"}
    MEDICO_ESP = {}
    SEDES = {"centro": "Arenal Centro", "norte": "Arenal Norte", "sur": "Arenal Sur"}
    SEGUROS = {}
    PACIENTES = {}

ESPECIALIDAD = {"general_practice": "medicina general", "paediatrics": "pediatría", "dermatology": "dermatología",
                "orthopaedics": "traumatología", "gynaecology": "ginecología", "physiotherapy": "fisioterapia"}
MOTIVO = {
    "out_of_scope": "lo que pedían queda fuera de mi cometido", "specialty_not_covered": "el seguro no cubre esa especialidad",
    "provider_not_found": "ese médico no es de la casa", "no_availability": "no había hueco", "referral_required": "hace falta volante",
    "insurer_referral_required": "el seguro pide volante", "allowance_exhausted": "el cupo del seguro está agotado",
    "location_not_covered": "el seguro no cubre esa sede", "patient_not_found": "no aparece en el directorio",
    "provider_on_leave": "el médico está de baja", "provider_not_in_network": "ese médico no trabaja con su seguro",
    "clinic_closed": "la clínica cierra ese día", "not_eligible_age": "la edad no corresponde a esa especialidad",
    "location_hours": "esa sede no abre a esa hora", "type_not_offered": "ese tipo de cita no se ofrece",
    "patient_history": "el historial no lo permite", "medical_emergency": "urgencia médica: 112",
}
ACTO = {"confirm": "dice que sí", "reject": "dice que no", "correct": "corrige", "provide_info": "da datos", "ask_question": "pregunta",
        "backchannel": "asiente", "end_call": "se despide", "unclear": "no se entiende"}
INTENCION = {"book": "quiere pedir cita", "reschedule": "quiere cambiar una cita", "cancel": "quiere anular", "info": "quiere informarse",
             "register": "quiere darse de alta"}
ALARMA = {"stroke": "ictus", "chest_pain": "dolor en el pecho", "anaphylaxis": "reacción alérgica grave", "head_injury": "golpe en la cabeza",
          "breathing": "dificultad para respirar", "bleeding": "hemorragia", "suicide": "riesgo de suicidio"}
FUERA = {"injection": "manipulación", "other_patient_data": "pide datos ajenos", "medical_advice": "pide consejo médico",
         "unrelated": "tema ajeno", "sales": "llamada comercial"}
FUERA_FRASE = {"injection": "Intento de manipulación", "other_patient_data": "Piden datos de otra persona", "medical_advice": "Piden consejo médico",
               "unrelated": "Tema ajeno a la clínica", "sales": "Llamada comercial"}
IDIOMA = {"en": "inglés", "es": "castellano", "ca": "catalán", "fr": "francés", "ro": "rumano", "de": "alemán", "pt": "portugués",
          "gl": "gallego", "eu": "euskera", "other": "otro idioma"}
CAMPO = {"date_of_birth": "una fecha de nacimiento", "national_id": "un DNI", "phone": "un teléfono", "full_name": "un nombre",
         "name": "un nombre", "email": "un correo", "address": "una dirección", "specialty": "una especialidad"}
INVENTO = {"dob_invented": "una fecha de nacimiento", "nid_invented": "un DNI", "address_invented": "una dirección",
           "phone_invented": "un teléfono", "email_invented": "un correo", "specialty_invented": "una especialidad"}
VERBO = {"book": "BOOK", "reschedule": "RESCHEDULE", "cancel": "CANCEL", "register": "REGISTER", "no-action": "NO_ACTION", "escalate": "ESCALATE"}
TEMA = {"doctor_where_and_when": "dónde y cuándo pasa consulta un médico", "site_address": "dirección y horario de una sede",
        "doctors_for_specialty": "médicos de una especialidad", "sites_for_specialty": "sedes de una especialidad",
        "languages": "idiomas de los médicos", "insurers": "seguros admitidos", "weekend": "apertura en fin de semana"}
FECHA_PEDIDA = {"earliest": "lo antes posible", "tomorrow": "mañana", "day_after_tomorrow": "pasado mañana", "this_coming": "el próximo",
                "specific_date": "un día concreto", "weekday_afternoon": "una tarde entre semana", "saturday_morning": "el sábado por la mañana",
                "first_thing": "a primera hora"}
DIA_EN = {"monday": "lunes", "tuesday": "martes", "wednesday": "miércoles", "thursday": "jueves", "friday": "viernes",
          "saturday": "sábado", "sunday": "domingo"}
ERROR_NUCLEO = [
    ("identify the patient first", "Antes de mirar la agenda hay que identificar al paciente"),
    ("not read back yet", "Aún no se había leído en voz alta: el núcleo no deja confirmar"),
    ("has not clearly chosen", "Quien llama no ha elegido esa opción con claridad: el núcleo manda preguntar"),
    ("has not said goodbye", "Nadie se ha despedido: el núcleo no deja colgar"),
    ("has not clearly agreed to cancel", "No hay un «sí» claro para anular justo eso: el núcleo manda releer"),
    ("has not given that DNI", "Ese DNI no lo dijo quien llama: el núcleo manda pedirlo otra vez"),
    ("has not given", "Ese dato no lo dijo quien llama: el núcleo manda pedirlo"),
    ("is not valid", "La letra del DNI no cuadra: el núcleo manda pedirlo otra vez"),
    ("have not looked anyone up", "Sin haber abierto ninguna ficha no se puede afirmar nada: el núcleo lo frena"),
    ("have not checked the diary", "Sin mirar la agenda no se puede decir que no hay hueco: el núcleo lo frena"),
    ("missing specialty", "A la consulta del catálogo le faltaba un dato"),
    ("needs 9 digits", "Al teléfono le faltan cifras: el núcleo manda pedirlo otra vez"),
]
HERRAMIENTA = {"find_slots": "buscar hueco", "confirm_booking": "confirmar la cita", "end_call": "colgar", "clinic_info": "consultar el catálogo",
               "prepare_registration": "preparar el alta", "confirm_registration": "confirmar el alta", "confirm_cancellation": "confirmar la anulación",
               "prepare_cancellation": "preparar la anulación", "list_appointments": "ver las citas", "nearest_site": "buscar la sede más cercana",
               "decline": "declinar", "identify_patient": "identificar al paciente"}


# ---------------------------------------------------------------- utilidades de texto
def coma(x, nd=2):
    return f"{float(x):.{nd}f}".replace(".", ",")


def segundos(ms):
    return coma(ms / 1000.0, 1) + " s"


_MASK = re.compile(r"([XYZxyz]?\d[\d\s.-]{5,}\d)([A-Za-z]?)")


def mask(s):
    """DNI, NIE y teléfonos a medias (misma regla que arena/app.js)."""
    def rep(m):
        d = re.sub(r"\D", "", m.group(1))
        return d[:2] + "•" * max(0, len(d) - 4) + d[-2:] + ("•" if m.group(2) else "")
    return _MASK.sub(rep, str(s or ""))


def recorta(s, n=240):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _dt(iso):
    try:
        d = datetime.fromisoformat(str(iso))
    except Exception:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?", str(iso or ""))
        if not m:
            return None
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0), int(m.group(5) or 0))
    return d


def dia_es(iso, con_semana=True):
    d = _dt(iso)
    if not d:
        return str(iso or "")
    base = f"{d.day} de {MESES[d.month - 1]}"
    return f"{DIAS[d.weekday()]} {base}" if con_semana else base


def fecha_es(iso, union=", "):
    """«martes 22 de septiembre, 8:00» (union=' a las ' para frases)."""
    d = _dt(iso)
    if not d:
        return str(iso or "")
    return f"{dia_es(iso)}{union}{d.hour}:{d.minute:02d}"


_ISO = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2})?)?")


def sin_iso(s):
    """Cualquier fecha ISO que quede en un texto visible pasa a español."""
    def uno(m):
        x = m.group(0)
        if "T" in x:
            return fecha_es(x)
        d = _dt(x)
        return dia_es(x) if d and d.year == 2026 else (f"{d.day} de {MESES[d.month - 1]} de {d.year}" if d else x)
    return _ISO.sub(uno, str(s or ""))


def visible(s):
    return mask(sin_iso(s))


def con_medico(nombre):
    if not nombre:
        return ""
    if nombre.startswith("Dra."):
        return f"con la {nombre}"
    if nombre.startswith("Dr."):
        return f"con el {nombre}"
    return f"con {nombre}"


def el_medico(nombre):
    if nombre.startswith("Dra."):
        return f"la {nombre}"
    if nombre.startswith("Dr."):
        return f"el {nombre}"
    return nombre


def nombre_corto(given, surname):
    given = (given or "").strip()
    if not given:
        return None
    ini = (surname or "").strip()[:1]
    return f"{given} {ini}." if ini else given


def mayus(s):
    return s[:1].upper() + s[1:]


def slug(s):
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s).lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def plural(n, uno, varios):
    return f"{n} {uno if n == 1 else varios}"


def lista_es(xs):
    xs = [x for x in xs if x]
    if len(xs) <= 1:
        return "".join(xs)
    ultimo = xs[-1]
    y = "e" if re.match(r"(?i)(i|hi)[^aeiouáéíóú]?", ultimo) and not ultimo.lower().startswith(("hie", "hia")) else "y"
    return ", ".join(xs[:-1]) + f" {y} " + ultimo


# ---------------------------------------------------------------- una llamada → un acta
def texto_accion(a):
    v = a.get("action")
    if v in ("BOOK", "RESCHEDULE"):
        partes = ["Reserva" if v == "BOOK" else "Cambio de cita", fecha_es(a.get("slot")), MEDICOS.get(a.get("provider_id"), ""),
                  SEDES.get(a.get("location_id"), "")]
        return " · ".join(p for p in partes if p)
    if v == "CANCEL":
        return f"Anulación · cita {a.get('appointment_id', '')}".strip()
    if v == "REGISTER":
        return "Alta · " + (nombre_corto(a.get("given_name"), a.get("first_surname")) or "paciente nuevo")
    if v == "ESCALATE":
        return "Urgencia · derivada al 112, sin reserva"
    if v == "NO_ACTION":
        return "Sin gestión · " + MOTIVO.get(a.get("reason"), str(a.get("reason") or "sin motivo"))
    return str(v)


def resumen_de(d):
    acts = [a for a in d.get("actions") or [] if isinstance(a, dict)]
    trozos = []
    ncancel = sum(1 for a in acts if a.get("action") == "CANCEL")
    hecho_cancel = False
    for a in acts:
        v = a.get("action")
        if v == "BOOK":
            trozos.append("Reserva: " + ", ".join(p for p in (fecha_es(a.get("slot"), " a las "), MEDICOS.get(a.get("provider_id")), SEDES.get(a.get("location_id"))) if p))
        elif v == "RESCHEDULE":
            trozos.append("Cambio de cita: ahora " + ", ".join(p for p in (fecha_es(a.get("slot"), " a las "), MEDICOS.get(a.get("provider_id")), SEDES.get(a.get("location_id"))) if p))
        elif v == "CANCEL" and not hecho_cancel:
            hecho_cancel = True
            trozos.append("Anulación de una cita" if ncancel == 1 else f"Anulación de {ncancel} citas")
        elif v == "REGISTER":
            trozos.append("Alta de paciente nuevo: " + (nombre_corto(a.get("given_name"), a.get("first_surname")) or "sin nombre"))
        elif v == "ESCALATE":
            trozos.append("Urgencia derivada al 112: no se reserva")
        elif v == "NO_ACTION":
            trozos.append("Sin gestión: " + MOTIVO.get(a.get("reason"), str(a.get("reason") or "sin motivo")))
    if not trozos:
        r = d.get("reason")
        trozos.append("Sin gestión" + (": " + MOTIVO.get(r, r) if r else ""))
    return " · ".join(trozos)


def parse_oferta(raw):
    """Saca (offer_id, inicio, médico, sede) del resultado de find_slots, aunque venga truncado."""
    m = re.search(r'"offer_id":\s*"(\w+)",\s*"readback":\s*"([^"]*)",\s*"start":\s*"([^"]+)"', raw or "")
    if not m:
        return None
    rb = m.group(2)
    med = next((n for n in MEDICOS.values() if n in rb), "")
    sede = next((n for n in SEDES.values() if n in rb), "")
    return m.group(1), m.group(3), med, sede


def frase_oferta(inicio, med, sede, verbo="Ofrece"):
    t = f"{verbo} el {fecha_es(inicio, ' a las ')}"
    if med:
        t += " " + con_medico(med)
    if sede:
        t += f" en {sede}"
    return t


def checks_puerta(detalle, ok):
    m = re.search(r"(\w+): en la mesa y leída=(\w+) · elige \(Jev\)=(\S+) ([\d.]+) · «sí»=([\d.]+) \((\w+) ([\d.]+)\)", detalle or "")
    if m:
        ref, leida, pk, pc, acc, act, ac = m.group(1), m.group(2) == "True", m.group(3), float(m.group(4)), float(m.group(5)), m.group(6), float(m.group(7))
        return [{"k": "oferta leída", "ok": leida},
                ({"k": "elige esa (Jev)", "v": pc, "ok": pc >= 0.7} if pk == ref else
                 {"k": "elige esa (Jev)", "ok": False, "nota": ("Jev no vio que eligiera ninguna" if pk in ("none", "None") else f"Jev vio que elegía otra ({pk})") + f" ({coma(pc)})"}),
                {"k": "«sí» claro", "v": acc, "ok": acc >= 0.6 or (act == "confirm" and ac >= 0.7)}], acc, act
    m = re.search(r"(\w+): leído antes=(\w+) · «sí» de Jev=([\d.]+) \((\w+) ([\d.]+)\)", detalle or "")
    if m:
        acc, act, ac = float(m.group(3)), m.group(4), float(m.group(5))
        return [{"k": "cambio leído", "ok": m.group(2) == "True"},
                {"k": "«sí» claro", "v": acc, "ok": acc >= 0.6 or (act == "confirm" and ac >= 0.7)}], acc, act
    m = re.search(r"anular exactamente eso \(Jev ([\d.]+)\)", detalle or "")
    if m:
        return [{"k": "anular exactamente eso (Jev)", "v": float(m.group(1)), "ok": bool(ok)}], float(m.group(1)), "cancel"
    return [{"k": visible(detalle), "ok": bool(ok)}], None, None


def glosas_de(j, est):
    """Solo lo informativo del turno, en español, máximo cuatro."""
    c = []  # (prioridad, orden, glosa)
    act = j.get("act")
    if isinstance(act, list) and act:
        c.append((0, 0, {"k": ACTO.get(act[0], act[0]), "v": round(float(act[1]), 2)}))
    rf = j.get("red_flag")
    if isinstance(rf, list) and rf and rf[0] != "none":
        c.append((1, 4, {"k": "urgencia: " + ALARMA.get(rf[0], rf[0]), "v": round(float(rf[1]), 2)}))
    oos = j.get("oos")
    if isinstance(oos, list) and oos and oos[0] != "none":
        c.append((2, 5, {"k": FUERA.get(oos[0], oos[0]), "v": round(float(oos[1]), 2)}))
    rep = j.get("repudiates")
    if isinstance(rep, (int, float)) and rep > 0.5:
        c.append((2, 5, {"k": "niega haber aceptado", "v": round(float(rep), 2)}))
    acc = j.get("accepts", j.get("accepts_offer"))
    contesta = isinstance(act, list) and act and act[0] in ("confirm", "reject", "backchannel")
    if isinstance(acc, (int, float)) and (est.get("oferta") or (acc > 0.3 and contesta)):
        c.append((3, 2, {"k": "acepta", "v": round(float(acc), 2)}))
    it = j.get("intent")
    if isinstance(it, list) and it and it[0] in INTENCION and it[0] != est.get("intent"):
        est["intent"] = it[0]
        c.append((4, 3, {"k": INTENCION[it[0]], "v": round(float(it[1]), 2)}))
    otro = j.get("for_other", j.get("third_party"))
    rel = j.get("relation")
    if (isinstance(otro, (int, float)) and otro > 0.5) or (isinstance(rel, list) and rel and rel[0] not in ("self", "none") and rel[1] > 0.5):
        v = otro if isinstance(otro, (int, float)) and otro > 0.5 else rel[1]
        c.append((5, 6, {"k": "para otra persona", "v": round(float(v), 2)}))
    lg = j.get("lang")
    if isinstance(lg, list) and lg and lg[0] != "other" and lg[1] >= 0.6:
        if lg[0] != est.get("lang") and (est.get("lang") is not None or lg[0] != "en"):
            c.append((6, 7, {"k": "habla " + IDIOMA.get(lg[0], lg[0]), "v": round(float(lg[1]), 2)}))
        est["lang"] = lg[0]
    fin = j.get("finished")
    if isinstance(fin, (int, float)):
        c.append((7, 1, {"k": "terminó", "v": round(float(fin), 2)}))
    aq = j.get("asks_question")
    if isinstance(aq, (int, float)) and aq > 0.6 and isinstance(acc, (int, float)) and acc > 0.5 and est.get("oferta") and not (isinstance(act, list) and act and act[0] == "ask_question"):
        c.append((8, 8, {"k": "además pregunta", "v": round(float(aq), 2)}))
    c.sort(key=lambda x: x[0])
    c = sorted(c[:4], key=lambda x: x[1])
    return [g for _, _, g in c]


def acta_de(d, fecha):
    cid = d.get("call_id")
    traza = [e for e in d.get("trace") or [] if isinstance(e, dict)]
    v2 = d.get("brain") == "v2"
    acts = [a for a in d.get("actions") or [] if isinstance(a, dict)]
    ev = []            # eventos de salida
    est = {"intent": None, "lang": None, "oferta": False}
    info = {"etiquetas": set(), "dudas": 0, "inventos": [], "fuera": [], "alarma": False, "puerta_no": [], "reglas": set(),
            "cambio_idioma": False, "espec": 0, "jev": [], "sin_hueco_esp": None, "esp": None, "no_supe": [], "oido": [], "baja": False,
            "para_otro": False, "puerta_si_tras_no": False, "percepciones": []}
    linea = "desconocida"
    paciente = None
    n_envio = 0
    dichos = []        # (índice en ev, texto) de lo que dice la recepción según la traza
    ult_perc = None
    lang_marca = None

    def marca(t, clase, texto, ok=True):
        ev.append({"t": t, "tipo": "marca", "clase": clase, "ok": bool(ok), "texto": visible(texto)})

    for e in traza:
        k = e.get("kind")
        t = round(float(e.get("t") or 0.0), 2)
        if k == "line":
            if e.get("matches"):
                linea = "reconocida"
                marca(t, "identidad", "Línea reconocida: hay una ficha con ese teléfono")
            elif e.get("from_number"):
                linea = "desconocida"
            else:
                linea = "oculta"
        elif k == "greet_lang":
            if e.get("why") == "memoria de la línea":
                marca(t, "idioma", f"Saluda en {IDIOMA.get(e.get('lang'), e.get('lang'))}: recuerda el idioma de esa línea")
        elif k == "perception":
            j = e.get("j") or {}
            g = glosas_de(j, est)
            ms = e.get("ms")
            if isinstance(ms, (int, float)) and ms > 0:
                info["jev"].append(ms)
            o = {"t": t, "tipo": "dice", "quien": "llama", "texto": visible(e.get("text")), "ms": int(ms or 0), "glosas": g}
            ev.append(o)
            ult_perc = e
            info["percepciones"].append(e)
            act = j.get("act") or [None, 0]
            if act[0] == "unclear":
                info["dudas"] += 1
                info["oido"].append({"tipo": "unclear", "t": t, "cita": e.get("text"), "v": act[1]})
            oos = j.get("oos") or ["none", 0]
            if oos[0] != "none" and oos[1] >= 0.5:
                info["fuera"].append((oos[0], t, e.get("text"), False))
            if (j.get("for_other") or 0) > 0.5 or (j.get("third_party") or 0) > 0.5:
                info["para_otro"] = True
            sp = j.get("specialty")
            if isinstance(sp, list) and sp and sp[0] != "none":
                info["esp"] = sp[0]
        elif k == "lang":
            lg = e.get("lang")
            if e.get("switched") or (lang_marca is not None and lg != lang_marca) or (lang_marca is None and lg not in ("en", None)):
                if lang_marca is not None or e.get("switched"):
                    info["cambio_idioma"] = True
                marca(t, "idioma", f"Detecta {IDIOMA.get(lg, lg)} ({coma(e.get('conf') or 0)}) y sigue en ese idioma")
            lang_marca = lg
        elif k == "date":
            dk = e.get("date_kind")
            if dk and dk != "none":
                txt = FECHA_PEDIDA.get(dk, dk)
                wd = DIA_EN.get(e.get("weekday"))
                if wd and dk in ("this_coming", "specific_date"):
                    txt = f"el próximo {wd}"
                if e.get("day"):
                    txt = dia_es(e.get("day"))
                marca(t, "agenda", f"Entiende cuándo la quiere: {txt}")
        elif k == "dni":
            num = e.get("normalized") or e.get("said")
            if num:
                marca(t, "identidad", f"DNI oído {num}: {e.get('why') or 'comprobado'}")
            else:
                marca(t, "identidad", "El DNI no llegó entero: " + str(e.get("why") or "lo vuelve a pedir"), ok=False)
        elif k == "identity":
            paso = {"sin coincidencia": "No encuentra la ficha con esos datos", "letra del DNI no cuadra": "La letra del DNI no cuadra: lo vuelve a pedir",
                    "sin coincidencia con el DNI": "Ese DNI no está en el directorio", "homónimos": "Hay homónimos: pide un segundo dato",
                    "falta el nombre": "Falta el nombre para buscar la ficha", "falta segundo dato": "Falta un segundo dato para confirmar quién es"}
            marca(t, "identidad", paso.get(e.get("step"), "Identidad: " + str(e.get("step"))), ok=False)
        elif k in ("prelookup", "identify_by_line"):
            por = {"line": "por la línea", "date_of_birth": "por la fecha de nacimiento", "national_id": "por el DNI", "phone": "por el teléfono"}.get(e.get("by"), "por la línea")
            marca(t, "identidad", f"Busca la ficha por adelantado, {por} ({coma(e.get('score') or 0)})")
        elif k == "gate":
            nombre, ok, det = e.get("name"), bool(e.get("ok")), str(e.get("detail") or "")
            if nombre == "identidad":
                if ok:
                    m = re.match(r"(P\d+) (.+?)(?: \((.+?) ([\d.]+)\)| por (\w+)|$| ·)", det)
                    quien = None
                    if m:
                        pid = m.group(1)
                        gs = PACIENTES.get(pid)
                        quien = nombre_corto(*gs) if gs else nombre_corto(*(m.group(2).split(" ") + [""])[:2])
                        paciente = paciente or quien
                        via = m.group(3) or m.group(5) or ""
                        via = {"dni + nombre": "por DNI y nombre", "nacimiento + nombre": "por fecha de nacimiento y nombre", "teléfono + nombre": "por teléfono y nombre",
                               "línea + nombre": "por la línea y el nombre", "date_of_birth": "por la fecha de nacimiento", "line": "por la línea",
                               "caller_line": "por la línea", "national_id": "por el DNI", "phone": "por el teléfono"}.get(via, "")
                        if via in ("por la línea",):
                            marca(t, "identidad", f"Ficha encontrada por la línea: {quien}")
                        else:
                            marca(t, "identidad", f"Ficha encontrada: {quien}" + (f" ({via})" if via else ""))
                    else:
                        marca(t, "identidad", "Ficha encontrada")
                else:
                    txt = {"no está en el directorio": "No está en el directorio: no hay ficha que abrir",
                           "sin segundo dato tras tres intentos": "Sin segundo dato tras tres intentos: no abre la ficha"}.get(det, "Identidad sin confirmar: " + det)
                    marca(t, "identidad", txt, ok=False)
            elif nombre == "puerta":
                checks, acc, act = checks_puerta(det, ok)
                ev.append({"t": t, "tipo": "puerta", "ok": ok, "checks": checks})
                leida = any(c["k"] in ("oferta leída", "cambio leído") and c["ok"] for c in checks) or (act == "cancel")
                if not ok and leida and act not in ("end_call", "provide_info", "ask_question"):
                    if not info["puerta_no"] or info["puerta_no"][-1]["cita"] != (ult_perc or {}).get("text"):
                        info["dudas"] += 1
                        info["puerta_no"].append({"t": t, "cita": (ult_perc or {}).get("text"), "v": acc, "act": act, "checks": checks})
                if ok and info["puerta_no"]:
                    info["puerta_si_tras_no"] = True
            elif nombre == "envío":
                m = re.match(r"([\w-]+) ", det)
                verbo = VERBO.get(m.group(1) if m else "", (m.group(1) if m else "?").upper())
                cod = re.search(r"→ (\d+)$", det)
                cod = int(cod.group(1)) if cod else 200
                a = acts[n_envio] if n_envio < len(acts) and acts[n_envio].get("action") == verbo else next((x for x in acts if x.get("action") == verbo), {"action": verbo})
                n_envio += 1
                if cod == 200:
                    ev.append({"t": t, "tipo": "escrito", "accion": verbo, "texto": visible(texto_accion(a))})
                    est["oferta"] = False
                else:
                    marca(t, "agenda", f"La agenda rechazó la escritura ({cod}): ya estaba hecha, no se duplica", ok=False)
            elif nombre == "negativa":
                r = det.split(" ")[0]
                marca(t, "limite", "No puede atenderlo: " + MOTIVO.get(r, r), ok=False)
                if r not in ("out_of_scope",):
                    info["reglas"].add(r)
            elif nombre == "límites":
                m = re.match(r"(\w+) \(([\d.]+)\)", det)
                tipo = m.group(1) if m else det.split(" ")[0]
                marca(t, "limite", f"{FUERA_FRASE.get(tipo, tipo)} ({coma(m.group(2)) if m else 'Jev'}): declina sin leer datos de nadie", ok=False)
                info["fuera"].append((tipo, t, (ult_perc or {}).get("text"), True))
            elif nombre == "triaje":
                m = re.search(r"«(\w+)» \(([\d.]+)\)", det)
                marca(t, "alarma", f"Urgencia: señal de {ALARMA.get(m.group(1), m.group(1)) if m else 'alarma'}" + (f" ({coma(m.group(2))})" if m else "") + ". Deriva al 112, no reserva", ok=False)
                info["alarma"] = True
            elif nombre == "reglas":
                marca(t, "limite", "Regla de la casa: " + MOTIVO.get(det, det), ok=False)
                info["reglas"].add(det)
            elif nombre == "escritura":
                marca(t, "agenda", "Escritura preparada: se envía tras oír la reacción de quien llama")
            else:
                marca(t, "limite", f"{nombre}: {det}", ok=ok)
        elif k == "availability":
            n = e.get("found") or 0
            esp = ESPECIALIDAD.get(((e.get("query") or {}).get("specialty_id")), "")
            info["esp"] = (e.get("query") or {}).get("specialty_id") or info["esp"]
            txt = (plural(n, "hueco posible", "huecos posibles") + (f" en {esp}" if esp else "")) if n else ("Sin huecos" + (f" en {esp}" if esp else ""))
            bl = [b for b in e.get("blocked") or [] if isinstance(b, dict)]
            if bl:
                txt += " · frenados por regla: " + lista_es([f"{MEDICOS.get(b.get('provider_id'), b.get('provider_id'))} ({MOTIVO.get(b.get('restriction'), b.get('restriction'))})" for b in bl[:3]])
                for b in bl:
                    if n == 0:
                        info["reglas"].add(b.get("restriction"))
            marca(t, "agenda", txt, ok=n > 0)
        elif k == "offer":
            marca(t, "oferta", frase_oferta(e.get("slot"), MEDICOS.get(e.get("provider"), ""), SEDES.get(e.get("site"), "")))
            est["oferta"] = True
        elif k == "offer_rejected":
            marca(t, "rechazo", "No quiere esa hora: busca otra", ok=False)
            est["oferta"] = False
        elif k == "tool":
            nombre, raw = e.get("name"), str(e.get("result") or "")
            err = re.search(r'^\{"error":\s*"([^"]*)', raw)
            if err:
                txt = next((es for en, es in ERROR_NUCLEO if en in err.group(1)), None)
                marca(t, "limite" if nombre not in ("clinic_info",) else "sistema2", txt or f"El núcleo frenó al planificador al {HERRAMIENTA.get(nombre, nombre)}", ok=False)
            elif nombre == "find_slots":
                args = e.get("args") or {}
                info["esp"] = args.get("specialty") or info["esp"]
                of = parse_oferta(raw)
                if '"nothing_matches"' in raw:
                    reglas = re.findall(r'"rule":\s*"(\w+)"', raw)
                    txt = "Nada encaja con lo pedido"
                    if reglas:
                        txt += ": " + lista_es(sorted({MOTIVO.get(r, r) for r in reglas}))
                        info["reglas"].update(reglas)
                    elif '"closed"' in raw:
                        txt += ": ese día está cerrado"
                    marca(t, "agenda", txt, ok=False)
                    if of:
                        marca(t, "oferta", frase_oferta(of[1], of[2], of[3], "Propone la alternativa más cercana:"))
                        est["oferta"] = True
                elif of:
                    marca(t, "oferta", frase_oferta(of[1], of[2], of[3]))
                    est["oferta"] = True
                    if "provider_on_leave" in raw:
                        info["baja"] = True
            elif nombre == "identify_patient":
                st = re.search(r'"status":\s*"(\w+)"', raw)
                st = st.group(1) if st else ""
                if st and st != "found":
                    txt = {"need_identifier": "Falta un segundo dato para abrir la ficha", "need_full_name": "Falta el nombre completo",
                           "id_incomplete": "El DNI llegó incompleto: lo vuelve a pedir", "not_found_with_that_id": "Ese DNI no está en el directorio",
                           "not_found": "No aparece en el directorio", "invalid_national_id": "La letra del DNI no cuadra"}.get(st, "Identidad sin confirmar")
                    marca(t, "identidad", txt, ok=False)
            elif nombre == "clinic_info":
                tema = TEMA.get((e.get("args") or {}).get("topic"), "un dato de la clínica")
                marca(t, "sistema2", f"Consulta el catálogo de la casa: {tema}")
            elif nombre == "decline":
                r = (e.get("args") or {}).get("reason")
                marca(t, "limite", "Declina la gestión: " + MOTIVO.get(r, str(r)), ok=False)
            elif nombre == "nearest_site":
                m = re.search(r'"use_location_id":\s*"(\w+)"', raw)
                marca(t, "agenda", "Sede más cercana a la dirección que dio: " + SEDES.get(m.group(1), m.group(1)) if m else "Busca la sede más cercana")
            elif nombre == "prepare_cancellation":
                marca(t, "agenda", "Prepara la anulación y la lee antes de confirmar")
            elif nombre == "prepare_registration":
                marca(t, "identidad", "Prepara el alta y repasa los datos en voz alta")
            elif nombre == "list_appointments":
                marca(t, "agenda", "Consulta las citas del paciente")
        elif k == "speculation_reused":
            info["espec"] += 1
            hs = e.get("head_start_ms") or 0
            if hs >= 200:
                marca(t, "especulacion", f"La respuesta ya estaba pensada {segundos(hs)} antes")
        elif k == "speculation_written":
            marca(t, "especulacion", "Tenía la escritura preparada antes de que acabara la frase")
        elif k == "prefetch_offer":
            marca(t, "especulacion", "Deja buscado el siguiente hueco por si lo piden")
        elif k == "undo":
            marca(t, "deshacer", f"Se corrige: descarta lo anterior («{recorta(e.get('text'), 60)}»)")
        elif k == "reg_correction":
            marca(t, "deshacer", "Corrige un dato del alta antes de enviarla")
        elif k == "barge_in":
            marca(t, "oido", f"Interrumpe a la recepcionista: «{recorta(e.get('text'), 80)}»")
            info["etiquetas"].add("interrupción")
        elif k == "early_answer":
            marca(t, "oido", f"Contesta antes de que acabe la pregunta (terminó {coma(e.get('finished') or 0)})")
        elif k == "second_opinion":
            campo = {"id": "del DNI", "dob": "de la fecha de nacimiento", "name": "del nombre"}.get(e.get("field"), "del dato")
            marca(t, "oido", f"Pide una segunda escucha {campo}: «{recorta(e.get('text'), 40)}»" + ("" if e.get("used") else " (se queda con la primera)"))
            info["oido"].append({"tipo": "second_opinion", "t": t, "cita": e.get("text"), "campo": campo, "used": bool(e.get("used"))})
        elif k == "site_unsure":
            marca(t, "oido", f"No está segura de la sede que oyó (¿{SEDES.get(e.get('candidate'), e.get('candidate'))}?, {coma(e.get('p') or 0)}): pregunta", ok=False)
            info["dudas"] += 1
            info["oido"].append({"tipo": "site_unsure", "t": t, "cita": (ult_perc or {}).get("text"), "sede": SEDES.get(e.get("candidate"), e.get("candidate")), "v": e.get("p")})
        elif k == "site_heard":
            marca(t, "oido", f"Oye la sede: {SEDES.get(e.get('site'), e.get('site'))} ({coma(e.get('conf') or 0)})")
        elif k == "site_corrected":
            marca(t, "oido", f"Corrige la sede: {SEDES.get(e.get('now'), e.get('now'))}")
        elif k == "provider_by_sound":
            marca(t, "oido", f"Oyó «{e.get('said')}»: por el sonido es {el_medico(MEDICOS.get(e.get('provider'), str(e.get('provider'))))}")
        elif k == "identifier_invented" or (k or "").endswith("_invented"):
            que = CAMPO.get(e.get("field")) if k == "identifier_invented" else INVENTO.get(k)
            que = que or "un dato"
            fem = que.startswith("una")
            marca(t, "invento", f"El planificador inventó {que}: el núcleo {'la' if fem else 'lo'} tiró", ok=False)
            info["inventos"].append({"t": t, "que": que, "dato": e.get("dropped"), "cita": (ult_perc or {}).get("text")})
        elif k == "name_placeholder":
            marca(t, "invento", f"El planificador puso «{e.get('said')}» como nombre: el núcleo lo tiró", ok=False)
        elif k == "site_ignored":
            marca(t, "invento", f"El planificador puso una sede que nadie dijo ({SEDES.get(e.get('dropped'), e.get('dropped'))}): el núcleo la tiró", ok=False)
        elif k == "provider_ignored":
            marca(t, "invento", f"El planificador puso un médico que nadie pidió ({MEDICOS.get(e.get('dropped'), e.get('dropped'))}): el núcleo lo tiró", ok=False)
        elif k == "insurers_ignored":
            marca(t, "invento", "El planificador añadió seguros que nadie dijo: el núcleo los tiró", ok=False)
        elif k == "guard":
            marca(t, "limite", "El núcleo tachó un dato personal antes de que sonara", ok=False)
        elif k == "truth_guard":
            marca(t, "limite", f"El planificador iba a dar por hecha una cita que no existía ({coma(e.get('claims') or 0)}): el núcleo lo frenó", ok=False)
        elif k == "decline_coerced":
            marca(t, "limite", "El planificador declaró un motivo que no tocaba: el núcleo puso el que vio", ok=False)
        elif k == "must_check_first":
            marca(t, "limite", "Antes de afirmar nada, el núcleo obliga a abrir la ficha", ok=False)
        elif k == "triage":
            queja = {"headaches": "dolores de cabeza", "ankle": "el tobillo", "knee": "la rodilla", "rash": "un sarpullido", "mole": "un lunar"}.get(e.get("complaint"), str(e.get("complaint")))
            marca(t, "agenda", f"Por lo que cuenta ({queja}), lo encamina a {ESPECIALIDAD.get(e.get('route'), e.get('route'))}")
        elif k == "system2":
            marca(t, "sistema2", "Pregunta fuera del guion: contesta el Sistema 2 con el catálogo delante")
            if NO_SE.search(str(e.get("answer") or "")):
                info["no_supe"].append({"t": t, "cita": e.get("question"), "por": "sistema2", "respuesta": e.get("answer")})
        elif k in ("fallback", "leave_alternative"):
            info["baja"] = True
            med = MEDICOS.get(e.get("provider"), "El médico pedido")
            hasta = f" hasta el {dia_es(e.get('until'), False)}" if e.get("until") else ""
            extra = f": busca con otro ({plural(e.get('found'), 'hueco', 'huecos')})" if e.get("found") else ": propone una alternativa"
            marca(t, "agenda", f"{med} está de baja{hasta}{extra}", ok=False)
        elif k == "second_plan":
            marca(t, "agenda", f"Prueba con el segundo seguro de la ficha ({SEGUROS.get(e.get('plan'), e.get('plan'))})")
        elif k == "appointments":
            marca(t, "agenda", "Tiene " + plural(e.get("n") or 0, "cita próxima", "citas próximas"))
        elif k == "otra_forma":
            marca(t, "identidad", "Ofrece otra forma de identificarse")
        elif k == "dejar_de_pedir":
            marca(t, "identidad", f"Deja de pedir el mismo dato tras {e.get('veces')} intentos", ok=False)
        elif k == "intent_changed":
            marca(t, "agenda", f"Cambia de propósito: ahora {INTENCION.get(e.get('new'), e.get('new'))}")
        elif k == "new_task":
            marca(t, "agenda", f"Otra gestión en la misma llamada: {INTENCION.get(e.get('intent'), e.get('intent'))}")
        elif k == "commit_on_hangup":
            marca(t, "agenda", "Cuelgan con una oferta aceptada: se escribe antes de cerrar")
        elif k in ("planner_error", "planner_retry"):
            marca(t, "sistema2", "El planificador falló: el núcleo siguió con plantilla", ok=False)
        elif k == "jev_down":
            marca(t, "sistema2", "Jev no respondió: el turno siguió con reglas fijas", ok=False)
        if k == "planner" and NO_SE.search(str(e.get("said") or "")):
            info["no_supe"].append({"t": t, "cita": (ult_perc or {}).get("text"), "por": "catálogo", "respuesta": e.get("said")})
        # lo que dice la recepción (cerebro v2: está en la traza)
        via = {"planner": "planificador", "composed": "plantilla", "fast_path": "carril rápido", "hold_ack": "acuse", "ack": "acuse", "filler": "acuse"}.get(k)
        if via:
            txt = e.get("said") if k in ("planner", "composed", "fast_path") else e.get("text")
            if txt:
                ev.append({"t": t, "tipo": "dice", "quien": "recepcion", "texto": visible(txt), "via": via, "_crudo": txt})

    # orden temporal; a igual instante, primero quien llama (el acuse suena después de oírle)
    for i, o in enumerate(ev):
        o["_i"] = i
    ev.sort(key=lambda o: (o["t"], 0 if (o["tipo"] == "dice" and o["quien"] == "llama") else 1, o["_i"]))

    # lo que la recepción dijo y no está en la traza (saludo, y todo el cerebro v1) sale de la transcripción
    sistema2 = {str(e.get("answer")) for e in traza if e.get("kind") == "system2"}
    usados = set()
    mover = []
    pos_llama = [i for i, o in enumerate(ev) if o["tipo"] == "dice" and o["quien"] == "llama"]
    turno = -1
    pendientes = []   # (turno, texto)
    for linea_tx in d.get("transcript") or []:
        if not isinstance(linea_tx, str):
            continue
        if linea_tx.startswith("Caller:"):
            turno += 1
        elif linea_tx.startswith("Receptionist:"):
            txt = linea_tx[len("Receptionist:"):].strip()
            hit = next((i for i, o in enumerate(ev) if i not in usados and o.get("_crudo") == txt), None)
            if hit is not None:
                usados.add(hit)
                # un acuse que sonó mientras Jev aún juzgaba va DESPUÉS de lo que dijo quien llama, como en la transcripción
                if 0 <= turno < len(pos_llama) and hit < pos_llama[turno]:
                    ev[hit]["t"] = ev[pos_llama[turno]]["t"]
                    mover.append(hit)
            elif txt:
                pendientes.append((turno, txt))
    if mover:
        ev.sort(key=lambda o: (o["t"], 0 if (o["tipo"] == "dice" and o["quien"] == "llama") else 1, o["_i"]))
        pos_llama = [i for i, o in enumerate(ev) if o["tipo"] == "dice" and o["quien"] == "llama"]
    for turno, txt in reversed(pendientes):
        via = "planificador" if txt in sistema2 else "plantilla"
        if turno < 0 or not pos_llama:
            idx, t = 0, 0.0
            while idx < len(ev) and ev[idx]["t"] <= 0.0 and ev[idx]["tipo"] == "marca":
                idx += 1
        else:
            ini = pos_llama[min(turno, len(pos_llama) - 1)]
            fin = pos_llama[turno + 1] if turno + 1 < len(pos_llama) else len(ev)
            # antes de la primera frase de la recepción de ese turno que sí estaba en la traza; si no hay, al final del turno
            idx = fin
            base = ev[ini]
            t = round(base["t"] + (base.get("ms") or 0) / 1000.0, 2)
            if idx > ini + 1:
                t = max(t, ev[idx - 1]["t"])
            if idx < len(ev):
                t = min(t, ev[idx]["t"])
        ev.insert(idx, {"t": t, "tipo": "dice", "quien": "recepcion", "texto": visible(txt), "via": via})
        pos_llama = [i for i, o in enumerate(ev) if o["tipo"] == "dice" and o["quien"] == "llama"]
    limpio, vistos_turno = [], set()
    for o in ev:
        o.pop("_i", None)
        o.pop("_crudo", None)
        if o["tipo"] == "dice" and o["quien"] == "llama":
            vistos_turno = set()
        elif o["tipo"] in ("marca", "puerta"):
            clave = json.dumps({k: v for k, v in o.items() if k != "t"}, ensure_ascii=False, sort_keys=True)
            if clave in vistos_turno:
                continue
            vistos_turno.add(clave)
        limpio.append(o)
    ev = limpio

    # paciente: la ficha, o el alta
    if not paciente:
        gs = PACIENTES.get(d.get("patient_id"))
        if gs:
            paciente = nombre_corto(*gs)
    if not paciente:
        reg = next((a for a in acts if a.get("action") == "REGISTER"), None)
        if reg:
            paciente = nombre_corto(reg.get("given_name"), reg.get("first_surname"))

    outcome = str(d.get("outcome") or "NO_ACTION")
    jev = info["jev"]
    acta = {
        "id": cid, "fecha": fecha, "dur": d.get("duration_s"), "idioma": d.get("language"), "cerebro": d.get("brain") or "v1",
        "resultado": outcome, "motivo": d.get("reason"), "fin": d.get("ended_by"), "linea": linea, "paciente": paciente,
        "para": d.get("caller_relation") or ("other" if info["para_otro"] else "self"),
        "resumen": visible(resumen_de(d)), "eventos": ev,
        "cifras": {"turnos": len(info["percepciones"]), "mediana_jev_ms": int(statistics.median(jev)) if jev else None,
                   "especulacion": info["espec"], "api": d.get("api_calls"), "dudas": info["dudas"]},
    }

    # etiquetas del índice
    et = info["etiquetas"]
    for v, nombre in (("BOOK", "reserva"), ("RESCHEDULE", "cambio"), ("CANCEL", "anulación"), ("REGISTER", "alta"), ("ESCALATE", "urgencia")):
        if v in outcome:
            et.add(nombre)
    if info["alarma"]:
        et.add("urgencia")
    tipos_fuera = {f[0] for f in info["fuera"]}
    if "injection" in tipos_fuera:
        et.add("manipulación")
    if "other_patient_data" in tipos_fuera:
        et.add("datos ajenos")
    if "medical_advice" in tipos_fuera:
        et.add("consejo médico")
    if tipos_fuera & {"sales", "unrelated"}:
        et.add("tema ajeno")
    if d.get("language") not in ("en", None) or info["cambio_idioma"]:
        et.add("otro idioma")
    if d.get("language") == "es":
        et.add("castellano")
    if d.get("language") == "ca":
        et.add("catalán")
    if (d.get("caller_relation") not in (None, "self")) or info["para_otro"]:
        et.add("para otra persona")
    if info["inventos"]:
        et.add("invento tirado")
    if d.get("reason") == "no_availability":
        et.add("sin hueco")
    regla = (d.get("reason") in REGLAS_MOTIVO) or bool(info["reglas"] & REGLAS_MOTIVO) or info["baja"]
    if regla:
        et.add("regla aplicada")
    if info["puerta_no"]:
        et.add("segundo sí")
    if info["oido"]:
        et.add("duda de oído")
    if linea == "reconocida":
        et.add("línea reconocida")
    info["regla"] = regla
    declino = any(e.get("kind") == "tool" and e.get("name") == "decline" and (e.get("args") or {}).get("reason") == "out_of_scope" for e in traza) \
        or any(e.get("kind") == "gate" and e.get("name") == "negativa" and str(e.get("detail") or "").startswith("out_of_scope") for e in traza)
    info["fuera_tipos"] = {f[0] for f in info["fuera"]}
    info["declinada"] = any(f[3] for f in info["fuera"]) or (bool(info["fuera"]) and (d.get("reason") == "out_of_scope" or declino))
    return acta, info


PLURAL_DATO = {"una fecha de nacimiento": ("fecha de nacimiento", "fechas de nacimiento"), "un DNI": ("DNI", "DNI"), "un teléfono": ("teléfono", "teléfonos"),
               "una dirección": ("dirección", "direcciones"), "un correo": ("correo", "correos"), "una especialidad": ("especialidad", "especialidades"),
               "un nombre": ("nombre", "nombres"), "un dato": ("dato sin clasificar", "datos sin clasificar")}
NO_SE = re.compile(r"(?i)(can't confirm|cannot confirm|do not have [\w ]*(details|information)|don't have [\w ]*(details|information)|not able to (confirm|tell)"
                   r"|no tengo (esa|ese|información)|no dispongo|I don't know|no lo sé)")
REGLAS_MOTIVO = {"specialty_not_covered", "referral_required", "insurer_referral_required", "allowance_exhausted", "location_not_covered",
                 "provider_on_leave", "provider_not_in_network", "clinic_closed", "not_eligible_age", "location_hours"}
ORDEN_ETIQUETAS = ["reserva", "cambio", "anulación", "alta", "urgencia", "manipulación", "datos ajenos", "consejo médico", "tema ajeno",
                   "sin hueco", "regla aplicada", "segundo sí", "invento tirado", "duda de oído", "para otra persona", "otro idioma",
                   "castellano", "catalán", "interrupción", "línea reconocida"]


# ---------------------------------------------------------------- escritura
def escribe(ruta, obj, compacto=False):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        if compacto:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)


def completa(x):
    a = x["acta"]
    return a.get("fin") == "goodbye" and 40 <= (a.get("dur") or 0) <= 120


def elige(cands, usados, n=1):
    """Los mejores candidatos: llamadas completas de 40 a 120 s primero, y las más recientes."""
    cands = [c for c in cands if c["acta"]["id"] not in usados]
    cands.sort(key=lambda x: (not completa(x), -(x["ts"])))
    return [c["acta"]["id"] for c in cands[:n]]


def evidencia(cands, n=3):
    cands = sorted(cands, key=lambda x: (not completa(x), -(x["ts"])))
    return [c["acta"]["id"] for c in cands[:n]]


# ---------------------------------------------------------------- parte diario
def parte_de(dia, xs):
    n = len(xs)
    res = lambda v: [x for x in xs if v in x["acta"]["resultado"]]
    citas, cambios, anul, altas = res("BOOK"), res("RESCHEDULE"), res("CANCEL"), res("REGISTER")
    gestiones = [x for x in xs if any(v in x["acta"]["resultado"] for v in ("BOOK", "RESCHEDULE", "CANCEL", "REGISTER"))]
    urg = [x for x in xs if x["info"]["alarma"] or "ESCALATE" in x["acta"]["resultado"]]
    declin = [x for x in xs if x["info"]["declinada"]]
    sin_hueco = [x for x in xs if x["acta"]["motivo"] == "no_availability"]
    por_motivo = defaultdict(list)
    for x in xs:
        if x["acta"]["motivo"]:
            por_motivo[x["acta"]["motivo"]].append(x)
    idiomas = Counter(x["acta"]["idioma"] for x in xs)
    jev = [m for x in xs for m in x["info"]["jev"]]
    med_jev = int(statistics.median(jev)) if jev else None
    turnos_v2 = sum(x["acta"]["cifras"]["turnos"] for x in xs if x["acta"]["cerebro"] == "v2")
    espec = sum(x["info"]["espec"] for x in xs)
    espec_pct = round(min(100.0, 100.0 * espec / turnos_v2), 1) if turnos_v2 else 0.0
    dur = [x["acta"]["dur"] for x in xs if x["acta"]["dur"]]
    d0 = datetime.fromisoformat(dia)
    nombre_dia = f"{DIAS[d0.weekday()]} {d0.day}"
    P = []

    # 1. volumen y resultados
    hechos = []
    if citas:
        hechos.append(plural(len(citas), "acabó en cita nueva", "acabaron en cita nueva"))
    if cambios:
        hechos.append(plural(len(cambios), "en un cambio de cita", "en cambios de cita"))
    if anul:
        hechos.append(plural(len(anul), "en una anulación", "en anulaciones"))
    if altas:
        hechos.append(plural(len(altas), "en el alta de un paciente nuevo", "en altas de pacientes nuevos"))
    t = f"El {nombre_dia} cogí {plural(n, 'llamada', 'llamadas')}"
    if hechos:
        t += ": " + lista_es(hechos) + "."
    else:
        t += "."
    t += f" En total, {plural(len(gestiones), 'llamada terminó', 'llamadas terminaron')} con algo escrito en la agenda"
    if dur:
        t += f", y la llamada mediana duró {int(statistics.median(dur))} segundos"
    t += "."
    P.append({"texto": t, "evidencia": (evidencia(citas, 1) + evidencia(cambios, 1) + evidencia(anul, 1))[:3]})

    # 2. lo que no se pudo atender
    no_at = [(m, v) for m, v in por_motivo.items() if m != "out_of_scope"]
    no_at.sort(key=lambda kv: -len(kv[1]))
    if no_at:
        total_no = sum(len(v) for _, v in no_at)
        trozos = []
        for m, v in no_at:
            frase = {"no_availability": "no había hueco en lo que pedían", "specialty_not_covered": "el seguro no cubría la especialidad",
                     "provider_not_found": "pedían un médico que no es de la casa", "referral_required": "hacía falta un volante que no consta en la ficha",
                     "allowance_exhausted": "el cupo de visitas del seguro estaba agotado", "location_not_covered": "el seguro no cubría esa sede",
                     "patient_not_found": "la persona no aparecía en el directorio", "provider_on_leave": "el médico pedido está de baja y no quisieron otro",
                     "provider_not_in_network": "ese médico no trabaja con su seguro", "clinic_closed": "la clínica cierra ese día"}.get(m, MOTIVO.get(m, m))
            if m == "no_availability":
                esp = Counter(ESPECIALIDAD.get(x["info"]["esp"]) for x in v if ESPECIALIDAD.get(x["info"]["esp"]))
                if esp:
                    frase += " (" + lista_es([f"{c} de {e}" for e, c in esp.most_common(3)]) + ")"
            trozos.append(f"en {len(v)}, {frase}")
        t = f"No pude dar lo que pedían en {plural(total_no, 'llamada', 'llamadas')}: " + "; ".join(trozos) + ". En ninguna escribí nada en la agenda, y lo expliqué antes de despedirme."
        P.append({"texto": t, "evidencia": [i for m, v in no_at[:3] for i in evidencia(v, 1)]})

    # 3. urgencias
    if urg:
        t = (f"Hubo {plural(len(urg), 'llamada', 'llamadas')} con señales de urgencia (síntomas de ictus). "
             f"En {'ella' if len(urg) == 1 else 'todas'} dije que llamaran al 112 de inmediato y no reservé nada: una cita no es la respuesta a una urgencia.")
        P.append({"texto": t, "evidencia": evidencia(urg, 3)})

    # 4. límites
    if declin:
        tipos = Counter()
        for x in declin:
            for tp in x["info"]["fuera_tipos"]:
                tipos[tp] += 1
        nombres = {"other_patient_data": ("pidió datos de otra persona", "pidieron datos de otra persona"), "medical_advice": ("quería consejo médico", "querían consejo médico"),
                   "injection": ("intentó darme órdenes para saltarme las normas", "intentaron darme órdenes para saltarme las normas"),
                   "sales": ("era una llamada comercial", "eran llamadas comerciales"), "unrelated": ("hablaba de algo ajeno a la clínica", "hablaban de algo ajeno a la clínica")}
        trozos = [f"{c} {nombres.get(tp, (tp, tp))[0 if c == 1 else 1]}" for tp, c in tipos.most_common()]
        t = (f"En {plural(len(declin), 'llamada', 'llamadas')} me pidieron algo que no me corresponde: " + lista_es(trozos) +
             ". Lo decliné siempre con educación y sin leer la ficha de nadie.")
        P.append({"texto": t, "evidencia": evidencia([x for x in declin if any(f[0] in ("injection", "other_patient_data") for f in x["info"]["fuera"])] or declin, 3)})

    # 5. idiomas
    partes = [f"{c} en {IDIOMA.get(l, l)}" for l, c in idiomas.most_common()]
    cambio = [x for x in xs if x["info"]["cambio_idioma"]]
    t = "Atendí " + lista_es(partes) + "."
    if cambio:
        t += f" En {plural(len(cambio), 'llamada', 'llamadas')} cambié de idioma a mitad de conversación porque así lo hizo quien llamaba."
    otros = [x for x in xs if x["acta"]["idioma"] in ("es", "ca")]
    P.append({"texto": t, "evidencia": evidencia([x for x in otros if x["acta"]["idioma"] == "ca"], 1) + evidencia([x for x in otros if x["acta"]["idioma"] == "es"], 1) + evidencia(cambio, 1)})

    # 6. dudas e inventos
    dud = [x for x in xs if x["info"]["puerta_no"]]
    nd = sum(len(x["info"]["puerta_no"]) for x in xs)
    inv = [x for x in xs if x["info"]["inventos"]]
    ni = sum(len(x["info"]["inventos"]) for x in xs)
    trozos = []
    if nd:
        bien = sum(1 for x in dud if x["info"]["puerta_si_tras_no"])
        trozos.append(f"Dudé {plural(nd, 'vez', 'veces')} antes de escribir: la oferta estaba leída, pero el «sí» no me pareció lo bastante claro, así que volví a preguntar"
                      + (f" ({plural(bien, 'de esas llamadas acabó', 'de esas llamadas acabaron')} con la gestión hecha igualmente)" if bien else "") + ".")
    if ni:
        que = Counter(i["que"] for x in inv for i in x["info"]["inventos"])
        trozos.append(f"El planificador se inventó {plural(ni, 'dato', 'datos')} que nadie había dicho (" + lista_es([f"{c} {PLURAL_DATO.get(q, (q, q))[0 if c == 1 else 1]}" for q, c in que.most_common(4)]) +
                      ") y el núcleo los tiró todos antes de que tocaran la agenda.")
    if trozos:
        t = " ".join(trozos)
        P.append({"texto": t, "evidencia": (evidencia(dud, 2) + evidencia(inv, 1))[:3]})

    # 7. velocidad
    if med_jev:
        t = f"En cuanto a la velocidad, Jev juzgó cada turno en {med_jev} ms de mediana"
        if espec:
            t += f", y en el {coma(espec_pct, 1)} % de los turnos del cerebro nuevo la respuesta ya estaba pensada antes de que la persona terminara de hablar"
        t += "."
        rap = sorted([x for x in xs if x["info"]["espec"] >= 2], key=lambda x: -x["info"]["espec"])
        P.append({"texto": t, "evidencia": evidencia(rap[:20], 2)})

    return {"fecha": dia, "titulo": f"Parte del {DIAS[d0.weekday()]} {d0.day} de {MESES[d0.month - 1]}",
            "parrafos": [{"texto": visible(p["texto"]), "evidencia": p["evidencia"]} for p in P],
            "cifras": {"llamadas": n, "gestiones": len(gestiones), "citas": len(citas), "cambios": len(cambios), "anulaciones": len(anul),
                       "altas": len(altas), "urgencias": len(urg), "declinadas": len(declin), "sin_hueco": len(sin_hueco),
                       "idiomas": dict(idiomas.most_common()), "mediana_jev_ms": med_jev, "especulacion_pct": espec_pct}}


# ---------------------------------------------------------------- bandeja de dudas
def dudas_de(todas):
    rec = sorted(todas, key=lambda x: (not completa(x), -x["ts"]))
    margen, no_supe, oido, invento = [], [], [], []
    no_ent, sede_d, escucha = [], [], []
    vistos = set()

    def nueva(lst, tope, clave, item):
        if len(lst) >= tope or clave in vistos or not item.get("cita"):
            return
        vistos.add(clave)
        lst.append(item)

    for x in rec:
        a, info = x["acta"], x["info"]
        for p in info["puerta_no"]:
            cita = recorta(visible(p["cita"]), 160)
            if p["act"] == "cancel":
                nueva(margen, 5, ("m", cita), {"tipo": "margen", "titulo": "Dudé si quería anular justo esa cita", "cita": f"«{cita}»",
                      "detalle": f"anular exactamente eso {coma(p['v'] or 0)} · releí la cita y pedí un «sí» antes de anular", "acta": a["id"], "t": p["t"],
                      "pregunta": "Volví a leer la cita antes de anularla. ¿Hice bien?", "opciones": ["Sí, siempre así", "Con eso bastaba"]})
            else:
                elige_ok = next((c for c in p["checks"] if c["k"] == "elige esa (Jev)"), None)
                si_ok = next((c for c in p["checks"] if c["k"] == "«sí» claro"), None)
                if si_ok is not None and si_ok["ok"] and elige_ok is not None and not elige_ok["ok"]:
                    nueva(margen, 5, ("m", cita), {"tipo": "margen", "titulo": "Dudé cuál de las ofertas aceptaba", "cita": f"«{cita}»",
                          "detalle": f"acepta {coma(p['v'] or 0)}, pero había leído más de una hora y Jev no vio cuál elegía · pregunté cuál antes de escribir", "acta": a["id"], "t": p["t"],
                          "pregunta": "Dijo que sí, pero no a cuál. Pregunté antes de escribir. ¿Hice bien?", "opciones": ["Sí, siempre así", "Quédate con la primera que leíste"]})
                else:
                    nueva(margen, 5, ("m", cita), {"tipo": "margen", "titulo": "Dudé si aceptaba la cita", "cita": f"«{cita}»",
                          "detalle": f"acepta {coma(p['v'] or 0)} · pedí un «sí» más claro antes de escribir", "acta": a["id"], "t": p["t"],
                          "pregunta": "Pregunté otra vez antes de escribir. ¿Hice bien?", "opciones": ["Sí, siempre así", "Con eso bastaba"]})
        for s in info["no_supe"]:
            cita = recorta(visible(s["cita"]), 160)
            nueva(no_supe, 4, ("n", cita), {"tipo": "no_supe", "titulo": "Me preguntaron algo que no sé", "cita": f"«{cita}»",
                  "detalle": ("ese dato no está en el catálogo de la casa" if s["por"] == "catálogo" else "solo pude confirmar una parte con el catálogo delante") + " · dije que no lo sabía en vez de inventarlo",
                  "acta": a["id"], "t": s["t"], "pregunta": f"Me preguntaron «{cita}» y no lo sé: ¿me lo cuenta?", "opciones": ["Te lo cuento", "Di que llamen en horario de oficina"]})
        for o in info["oido"]:
            cita = recorta(visible(o["cita"]), 160)
            if o["tipo"] == "unclear":
                nueva(no_ent, 2, ("o", cita), {"tipo": "oido", "titulo": "No entendí lo que dijo", "cita": f"«{cita}»",
                      "detalle": f"no se entiende {coma(o['v'] or 0)} · pedí que lo repitiera en vez de suponer", "acta": a["id"], "t": o["t"],
                      "pregunta": "Pedí que me lo repitiera. ¿Prefiere que pase la llamada a una persona?", "opciones": ["Sigue pidiendo que repitan", "A la segunda, pásamela"]})
            elif o["tipo"] == "site_unsure":
                nueva(sede_d, 2, ("s", cita), {"tipo": "oido", "titulo": "No estaba segura de la sede que oí", "cita": f"«{cita}»",
                      "detalle": f"¿{o['sede']}? {coma(o['v'] or 0)} · pregunté la sede antes de buscar hueco", "acta": a["id"], "t": o["t"],
                      "pregunta": "Pregunté la sede en vez de darla por buena. ¿Hice bien?", "opciones": ["Sí, siempre así", "Con eso bastaba"]})
            elif o["tipo"] == "second_opinion":
                nueva(escucha, 2, ("d", cita), {"tipo": "oido", "titulo": "Pedí una segunda escucha de un dato", "cita": f"«{cita}»",
                      "detalle": f"segunda escucha {o['campo']} · " + ("me quedé con la segunda" if o["used"] else "coincidía con la primera, seguí adelante"), "acta": a["id"], "t": o["t"],
                      "pregunta": "Cuando un número no me cuadra lo escucho dos veces. ¿Sigo así?", "opciones": ["Sí, siempre así", "Solo si la letra no cuadra"]})
        for i in info["inventos"]:
            cita = recorta(visible(i["cita"]), 160)
            nueva(invento, 3, ("i", i["que"]), {"tipo": "invento", "titulo": f"El planificador inventó {i['que']}", "cita": f"«{cita}»",
                  "detalle": f"dato tirado: {visible(i['dato'])} · nadie lo había dicho en la llamada, así que el núcleo no lo dejó pasar", "acta": a["id"], "t": i["t"],
                  "pregunta": "Tiré el dato y lo volví a pedir. ¿Hago bien en no fiarme?", "opciones": ["Sí, siempre así", "Avísame cada vez que pase"]})
    oido = no_ent + sede_d + escucha
    out = margen + no_supe + oido + invento
    for n, dd in enumerate(out, 1):
        dd_id = {"id": f"d{n}"}
        dd_id.update(dd)
        out[n - 1] = dd_id
    return out


# ---------------------------------------------------------------- arnés y nota
FAMILIA = {"tercero": "Llaman por otra persona", "fechas": "Piden un día o una hora concretos", "medico_sede": "Piden un médico o una sede",
           "simple": "Una cita, sin más", "preguntas": "Preguntan por la clínica", "reglas": "Chocan con una regla de la casa",
           "triaje": "No saben qué médico necesitan", "cambiar": "Cambian o anulan una cita", "alta": "Pacientes nuevos",
           "adversario": "Intentan engañarla", "idiomas": "Hablan otro idioma"}
COMPORTAMIENTO = {"charla_al_final": "Se enrolla al final", "a_trozos": "Habla a trozos", "sigue_ahi": "Pregunta si sigue ahí", "escueto": "Contesta con monosílabos",
                  "mezcla_idiomas": "Mezcla idiomas", "se_corrige": "Se corrige a mitad de frase", "acepta_y_pregunta": "Acepta y pregunta a la vez",
                  "pregunta_primero": "Pregunta antes de pedir", "titubea": "Titubea", "saluda_y_espera": "Saluda y espera",
                  "comprueba_tras_aceptar": "Comprueba tras aceptar", "todo_de_golpe": "Lo dice todo de golpe", "pregunta_por_la_oferta": "Pregunta por la oferta",
                  "reformula": "Reformula lo que pide", "acepta_con_condicion": "Acepta con una condición", "se_identifica_a_si_mismo": "Se identifica sin que se lo pidan",
                  "no_acepte_eso": "Niega haber aceptado", "pregunta_y_gracias_seco": "Pregunta y da las gracias a secas",
                  "pide_que_repita_tras_reservar": "Pide que repita tras reservar"}
PERSONA_FIJA = {"a prankster": "un bromista", "a pushy caller": "alguien insistente", "an anxious caller": "alguien con ansiedad",
                "a smooth talker": "un embaucador", "a worried caller": "alguien preocupado"}


def grupo_persona(p):
    p = str(p or "")
    if p in PERSONA_FIJA:
        return p, PERSONA_FIJA[p]
    if "calling for your child" in p or "parent of" in p:
        return "calling for your child", "una madre o un padre que llama por su hijo"
    if "not a patient yet" in p:
        return "not a patient yet", "alguien que aún no es paciente"
    m = re.search(r"(\d+) years old", p)
    if m:
        e = int(m.group(1))
        if e >= 65:
            return "65+ years old", "una persona mayor (65 años o más)"
        if e >= 40:
            return "40-64 years old", "una persona adulta (de 40 a 64 años)"
        return "18-39 years old", "una persona joven (de 18 a 39 años)"
    return p, p


# Cifras ya medidas sobre N = 528 llamadas de voz del cerebro v2. Fuente: docs/rigor.md (apartado 3, «Los modos de fallo que sabemos
# nombrar», y las tablas de latencias, especulación y coste) y docs/modos-de-fallo.md (mismas cifras con más decimales).
# Son constantes: este script no las recalcula.
RIGOR_N = 528
COSTE = {"media_usd": 0.060, "p90_usd": 0.097,
         "nota": "Estimación con precios de lista: la voz es cota superior (nada en caché) y los modelos, cota inferior.",
         "desglose": [{"parte": "Jev (percepción)", "usd": 0.00034}, {"parte": "Planificador", "usd": 0.00378},
                      {"parte": "Voz", "usd": 0.04533}, {"parte": "Oído", "usd": 0.01056}]}
LATENCIAS = {"n": RIGOR_N,
             "filas": [{"que": "Duración de la llamada", "mediana": "54,5 s", "p90": "108,0 s"},
                       {"que": "Jev, un juicio por turno", "mediana": "350 ms", "p90": "708 ms"},
                       {"que": "Planificador, paso que redacta", "mediana": "585 ms", "p90": "741 ms"},
                       {"que": "Herramienta (incluye la agenda)", "mediana": "576 ms", "p90": "719 ms"}],
             "especulacion": {"pct": 63.8, "ventaja_mediana_ms": 676}}
# (familia, modo de fallo, evento, llamadas, por 100, Wilson 95 %): las 15 filas de la tabla, tal cual
_GUARDIAS = [
    ("Oído", "El transcriptor destroza un nombre de sede; Jev lo recupera por sonido", "site_heard", 94, 17.8, "14,8–21,3"),
    ("Modelo inventa", "Identifica con un nombre de relleno («the patient») en vez de preguntar", "name_placeholder", 72, 13.6, "11,0–16,8"),
    ("Escritura", "Intento de escribir sin un «sí» claro a lo leído: la PUERTA se cierra", "gate puerta = no", 67, 12.7, "10,1–15,8"),
    ("Modelo inventa", "Pasa un DNI, teléfono o fecha de nacimiento que quien llama no ha dicho", "identifier_invented", 55, 10.4, "8,1–13,3"),
    ("Turnos", "Se contestó a un turno sin terminar; se deshace y se replanifica", "undo", 43, 8.1, "6,1–10,8"),
    ("Conversación", "Quien llama rechaza el hueco leído", "offer_rejected", 37, 7.0, "5,1–9,5"),
    ("Conversación", "Mismo dato pedido dos veces sin éxito: se pide de otra forma", "otra_forma", 20, 3.8, "2,5–5,8"),
    ("Infraestructura", "El planificador falla también al reintentar; el agente pide que se lo repitan", "planner_error", 18, 3.4, "2,2–5,3"),
    ("Modelo restringe", "Busca en una sede distinta de la pedida", "site_corrected", 17, 3.2, "2,0–5,1"),
    ("Negativa", "Va a negar sin haber mirado la agenda del paciente", "must_check_first", 16, 3.0, "1,9–4,9"),
    ("Modelo inventa", "Fecha de nacimiento inventada al dar de alta", "dob_invented", 12, 2.3, "1,3–3,9"),
    ("Negativa", "Da la llamada por denegada con una oferta abierta", "decline_with_offer", 10, 1.9, "1,0–3,5"),
    ("Conversación", "Tres peticiones del mismo dato: la llamada se atasca", "dejar_de_pedir", 10, 1.9, "1,0–3,5"),
    ("Modelo inventa", "Afirma una escritura que no se ha hecho, o una hora que no sale de ningún hueco", "truth_guard", 5, 0.9, "0,4–2,2"),
    ("Límite", "La llamada pasa de 180 s (sin guardia en código)", "derivado", 4, 0.8, "0,3–1,9"),
]
GUARDIAS = [{"familia": f, "modo": m, "evento": ev, "llamadas": n, "por_100": p, "ic95": ic} for f, m, ev, n, p, ic in _GUARDIAS]
GUARDIAS_RESUMEN = "185 de 528 llamadas (35,0 %) tienen al menos una guardia sobre el planificador: el LLM propone y el código comprueba."


def nota_de(todas):
    casos = []
    ficheros = sorted(glob.glob(os.path.join(TRAZAS, "arnes_*.json")))
    for f in ficheros:
        try:
            for c in json.load(open(f, encoding="utf-8")):
                if isinstance(c, dict):
                    c["_f"] = os.path.basename(f)
                    casos.append(c)
        except Exception as e:
            print(f"aviso: {f}: {e!r}", file=sys.stderr)

    def tabla(clave_de, nombre_de, campo):
        t = defaultdict(lambda: [0, 0])
        for c in casos:
            for k in clave_de(c):
                t[k][0] += 1
                t[k][1] += bool(c.get("ok"))
        return [{campo: k, "nombre": nombre_de(k), "casos": v[0], "bien": v[1]} for k, v in sorted(t.items(), key=lambda kv: -kv[1][0])]

    por_familia = tabla(lambda c: [c.get("family")], lambda k: FAMILIA.get(k, str(k)), "familia")
    por_comp = tabla(lambda c: c.get("behaviors") or [], lambda k: COMPORTAMIENTO.get(k, str(k).replace("_", " ")), "comportamiento")
    rep = defaultdict(lambda: [0, 0, ""])
    for c in casos:
        g, nombre = grupo_persona(c.get("persona"))
        rep[g][0] += 1
        rep[g][1] += bool(c.get("ok"))
        rep[g][2] = nombre
    reparto = [{"persona": g, "nombre": v[2], "casos": v[0], "bien": v[1]} for g, v in sorted(rep.items(), key=lambda kv: -kv[1][0])]
    var = defaultdict(lambda: [0, 0])
    for c in casos:
        var[c.get("seed_run")][0] += 1
        var[c.get("seed_run")][1] += bool(c.get("ok"))
    varianza = [{"semilla": s, "casos": v[0], "bien": v[1]} for s, v in sorted(var.items(), key=lambda kv: -kv[1][0])]

    # suspensos: los más recientes, sin repetir familia más de dos veces
    susp, por_f = [], Counter()
    for c in sorted([c for c in casos if not c.get("ok")], key=lambda c: c["_f"], reverse=True):
        if por_f[c.get("family")] >= 2 or len(susp) >= 12:
            continue
        turnos = [{"dijo": recorta(visible(t.get("said") or t.get("heard")), 240), "agente": recorta(visible(t.get("agent")), 240)}
                  for t in (c.get("turns") or [])[:8] if isinstance(t, dict)]
        if not turnos:
            continue
        por_f[c.get("family")] += 1
        susp.append({"id": c.get("id"), "familia": c.get("family"), "objetivo": recorta(visible(c.get("goal")), 240),
                     "por_que": recorta(visible(c.get("why")), 240), "turnos": turnos})

    # voz
    vc = vb = 0
    lat = []
    for f in sorted(glob.glob(os.path.join(TRAZAS, "harness_*.json"))):
        try:
            for c in json.load(open(f, encoding="utf-8")):
                if not isinstance(c, dict):
                    continue
                vc += 1
                vb += bool(c.get("ok"))
                det = c.get("agent_lat_detail")
                if isinstance(det, list) and det:
                    lat += [x["ms"] for x in det if isinstance(x, dict) and isinstance(x.get("ms"), (int, float)) and x["ms"] > 0]
                elif isinstance(c.get("agent_lat"), list):
                    lat += [x for x in c["agent_lat"] if isinstance(x, (int, float)) and x > 0]
        except Exception as e:
            print(f"aviso: {f}: {e!r}", file=sys.stderr)
    lat.sort()
    voz = {"casos": vc, "bien": vb, "mediana_ms": int(statistics.median(lat)) if lat else None,
           "p90_ms": int(lat[min(len(lat) - 1, int(0.9 * len(lat)))]) if lat else None}

    # ataques: de las llamadas reales
    intentos = [x for x in todas if x["info"]["fuera"]]
    declinados = [x for x in intentos if x["info"]["declinada"]]
    inventos = sum(len(x["info"]["inventos"]) for x in todas)
    n, b = len(casos), sum(1 for c in casos if c.get("ok"))
    return {
        "nota": round(10.0 * b / n, 1) if n else None, "casos": n, "bien": b, "pasadas": len(ficheros),
        "por_familia": por_familia, "por_comportamiento": por_comp, "reparto": reparto, "suspensos": susp, "varianza": varianza, "voz": voz,
        "marcador": {"puntos": 172, "de": 172, "problemas": 17},
        "coste": COSTE, "latencias": LATENCIAS, "guardias": GUARDIAS, "guardias_n": RIGOR_N, "guardias_resumen": GUARDIAS_RESUMEN,
        "ataques": {"intentos": len(intentos), "declinados": len(declinados), "inventos_tirados": inventos},
        "modos_de_fallo": [
            {"nombre": "Reserva doble",
             "que": "El transcriptor oyó «Play That One», Jev lo dio por un «sí» (0,73) y se reservó una cita que la paciente no había aceptado; al protestar ella, el agente reservó otra en vez de sustituir la primera. Visto dos veces en rondas puntuadas.",
             "estado": "diagnosticado; pide escritura en dos fases, no un guardia más"},
            {"nombre": "Colgar tras contestar una pregunta",
             "que": "Preguntaron el horario de Arenal Norte, el agente lo dijo bien y, al oír «Right, thanks.», colgó sin preguntar si necesitaban algo más.",
             "estado": "diagnosticado; candidato a arreglo (exigir una despedida más clara para colgar), sin medir"},
        ],
    }, casos


# ---------------------------------------------------------------- libro de la casa
def libro_de(todas, casos):
    def aplicada(motivos, extra=None):
        xs = [x for x in todas if x["acta"]["motivo"] in motivos or (x["info"]["reglas"] & set(motivos)) or (extra and extra(x))]
        return len(xs), evidencia(xs, 3)

    def ensayo(pred):
        cs = [c for c in casos if pred(c)]
        return {"casos": len(cs), "bien": sum(1 for c in cs if c.get("ok"))} if cs else None

    g = lambda c: str(c.get("goal") or "")
    fam = lambda c, f: c.get("family") == f
    R = []

    def regla(rid, texto, tipo, motivo, n_ev, ens):
        R.append({"id": rid, "texto": texto, "tipo": tipo, "motivo_api": motivo, "aplicada": n_ev[0], "actas": n_ev[1], "ensayo": ens})

    if API:
        S, PL, PR = API.SPECIALTIES, API.PLANS, API.PROVIDERS
        extra_vol = [f"con {v['name']}, también {ESPECIALIDAD[esp]}" for v in PL.values() for esp in v.get("referral_for", [])]
        for k, v in S.items():
            if v.get("referral"):
                regla(f"volante-{slug(ESPECIALIDAD[k])}", mayus(f"{ESPECIALIDAD[k]}, solo con volante en la ficha" + ("; " + "; ".join(extra_vol) if extra_vol else "") + "."),
                      "volante", "referral_required", aplicada(["referral_required", "insurer_referral_required"]),
                      ensayo(lambda c: fam(c, "reglas") and ("physio" in g(c).lower() or ("dermatolog" in g(c).lower() and "Mapfre" in g(c)))))
        tope = [(k, v["max"]) for k, v in S.items() if v.get("max") is not None]
        suelo = [k for k, v in S.items() if v.get("min")]
        if tope:
            anios = (tope[0][1] + 1) // 12
            regla("edad-pediatria", f"{mayus(ESPECIALIDAD[tope[0][0]])}, hasta cumplir los {anios} años; {lista_es([ESPECIALIDAD[s] for s in suelo])}, a partir de los {anios}.",
                  "edad", "not_eligible_age", aplicada(["not_eligible_age"]), ensayo(lambda c: fam(c, "reglas") and "paediatrician for yourself" in g(c)))
        no_esp = [f"{v['name']} no cubre {lista_es([ESPECIALIDAD[e] for e in v['no_spec']])}" for v in PL.values() if v.get("no_spec")]
        if no_esp:
            regla("cobertura-especialidad", "; ".join(no_esp) + ". Antes de decir que no, pregunto si tienen otro seguro.", "cobertura", "specialty_not_covered",
                  aplicada(["specialty_not_covered"]), None)
        no_sede = defaultdict(list)
        for v in PL.values():
            for s in v.get("no_site", []):
                no_sede[s].append(v["name"])
        for s, ps in no_sede.items():
            regla(f"sede-{s}", f"{lista_es(ps)} no {'cubre' if len(ps) == 1 else 'cubren'} {SEDES[s]}: ofrezco otra sede.", "sede", "location_not_covered",
                  aplicada(["location_not_covered"]), None)
        for p in PR:
            if p[5]:
                regla(f"red-{p[0].lower()}", f"{mayus(el_medico(p[1]))} no trabaja con {lista_es([PL[i]['name'] for i in p[5]])}.", "cobertura", "provider_not_in_network",
                      aplicada(["provider_not_in_network"]), None)
        for k, v in PL.items():
            if v.get("cap"):
                regla(f"cupo-{k}", f"{v['name']} cubre {v['cap']} visitas; con el cupo agotado no reservo con ese seguro.", "cupo", "allowance_exhausted",
                      aplicada(["allowance_exhausted"]), None)
        for p in PR:
            if p[6]:
                regla(f"baja-{p[0].lower()}", f"{mayus(el_medico(p[1]))} está de baja hasta el {dia_es(p[6][1], False)}: ofrezco otro médico de {ESPECIALIDAD[p[2]]} o su primera fecha a la vuelta.",
                      "baja", "provider_on_leave", aplicada(["provider_on_leave"], lambda x: x["info"]["baja"]), None)
        sab = [v["name"] for v in API.SITES.values() if 5 in v["hours"]]
        corto = [(v["name"], v["hours"][4][1]) for v in API.SITES.values() if 4 in v["hours"] and v["hours"][4][1] < "20:00"]
        t = f"Los sábados solo abre {lista_es(sab)}"
        if corto:
            t += "; " + lista_es([f"{n} cierra los viernes a las {h}" for n, h in corto])
        for c in API.CLOSURES:
            t += f"; el {dia_es(c.isoformat())}, festivo, la clínica cierra"
        regla("horario-sedes", t + ".", "horario", "location_hours", aplicada(["location_hours", "clinic_closed"]), None)
    regla("sin-hueco", "Si no hay hueco en lo que piden, leo la alternativa más cercana; si no la quieren, no reservo nada.", "horario", "no_availability",
          aplicada(["no_availability"]), None)
    regla("medico-desconocido", "Si piden un médico que no es de la casa, lo digo y no lo invento.", "limite", "provider_not_found", aplicada(["provider_not_found"]), None)
    regla("sin-ficha", "Sin ficha no reservo: hace falta el nombre y un segundo dato (DNI, fecha de nacimiento o la línea).", "limite", "patient_not_found",
          aplicada(["patient_not_found"]), None)

    def por_fuera(tipo):
        xs = [x for x in todas if x["info"]["declinada"] and tipo in x["info"]["fuera_tipos"]]
        return len(xs), evidencia(xs, 3)
    regla("limite-consejo-medico", "No doy consejo médico.", "limite", "out_of_scope", por_fuera("medical_advice"), ensayo(lambda c: fam(c, "adversario") and "ibuprofen" in g(c)))
    regla("limite-datos-ajenos", "No doy datos de otra persona.", "limite", "out_of_scope", por_fuera("other_patient_data"),
          ensayo(lambda c: fam(c, "adversario") and ("phone number and DNI" in g(c) or "next appointment is" in g(c))))
    regla("limite-manipulacion", "No obedezco órdenes de quien llama para saltarme las normas.", "limite", "out_of_scope", por_fuera("injection"),
          ensayo(lambda c: fam(c, "adversario") and "ignore your previous" in g(c)))
    urg = [x for x in todas if x["info"]["alarma"]]
    regla("limite-urgencias", "Las urgencias van al 112 y no se reserva.", "limite", "medical_emergency", (len(urg), evidencia(urg, 3)),
          ensayo(lambda c: fam(c, "triaje") and re.search(r"can't breathe|face drooped|banged your head|chest pain", g(c)) is not None))
    pu = [x for x in todas if x["info"]["puerta_no"]]
    regla("la-puerta", "No escribo en la agenda sin un «sí» claro a una oferta que ya he leído en voz alta.", "limite", None, (len(pu), evidencia(pu, 3)),
          ensayo(lambda c: "no_acepte_eso" in (c.get("behaviors") or []) or "acepta_con_condicion" in (c.get("behaviors") or [])))
    return R


# ---------------------------------------------------------------- principal
def main():
    ficheros = sorted(glob.glob(os.path.join(TRAZAS, "CA*.json")))
    if not ficheros:
        print(f"No hay trazas en {TRAZAS}", file=sys.stderr)
        return 1
    dir_actas = os.path.join(SALIDA, "actas")
    os.makedirs(dir_actas, exist_ok=True)
    for viejo in glob.glob(os.path.join(dir_actas, "*.json")):
        os.remove(viejo)
    todas, errores = [], []
    for f in ficheros:
        try:
            d = json.load(open(f, encoding="utf-8"))
            ts = os.path.getmtime(f)
            fecha = datetime.fromtimestamp(ts, MAD).isoformat(timespec="seconds")
            d.setdefault("call_id", os.path.basename(f)[:-5])
            acta, info = acta_de(d, fecha)
            escribe(os.path.join(dir_actas, f"{acta['id']}.json"), acta, compacto=True)
            todas.append({"acta": acta, "info": info, "ts": ts})
        except Exception as e:
            errores.append((os.path.basename(f), repr(e)))
    todas.sort(key=lambda x: -x["ts"])

    # índice y destacadas
    filas = []
    for x in todas:
        a = x["acta"]
        filas.append({"id": a["id"], "fecha": a["fecha"], "dur": a["dur"], "idioma": a["idioma"], "resultado": a["resultado"], "motivo": a["motivo"],
                      "resumen": a["resumen"], "paciente": a["paciente"], "turnos": a["cifras"]["turnos"], "dudas": a["cifras"]["dudas"],
                      "etiquetas": [e for e in ORDEN_ETIQUETAS if e in x["info"]["etiquetas"]]})
    tiene = lambda et: (lambda x: et in x["info"]["etiquetas"])
    criterios = [
        lambda x: x["info"]["alarma"],
        tiene("manipulación"),
        lambda x: x["acta"]["idioma"] == "es" and "reserva" in x["info"]["etiquetas"],
        lambda x: x["acta"]["idioma"] == "ca" and "reserva" in x["info"]["etiquetas"],
        lambda x: x["info"]["puerta_si_tras_no"] and "reserva" in x["info"]["etiquetas"],
        lambda x: bool(x["info"]["inventos"]),
        tiene("anulación"), tiene("cambio"), tiene("sin hueco"),
        lambda x: x["info"]["regla"] and x["acta"]["motivo"] in REGLAS_MOTIVO,
        tiene("datos ajenos"), tiene("alta"),
    ]
    destacadas = []
    for cr in criterios:
        destacadas += elige([x for x in todas if cr(x)], set(destacadas), 1)
    escribe(os.path.join(dir_actas, "indice.json"), {"generado": datetime.now(MAD).isoformat(timespec="seconds"), "total": len(filas),
                                                      "destacadas": destacadas, "actas": filas}, compacto=True)

    # parte
    por_dia = defaultdict(list)
    for x in todas:
        por_dia[x["acta"]["fecha"][:10]].append(x)
    escribe(os.path.join(SALIDA, "parte.json"), {"dias": [parte_de(dia, por_dia[dia]) for dia in sorted(por_dia, reverse=True)]})

    dudas = dudas_de(todas)
    escribe(os.path.join(SALIDA, "dudas.json"), {"dudas": dudas})
    nota, casos = nota_de(todas)
    escribe(os.path.join(SALIDA, "nota.json"), nota)
    reglas = libro_de(todas, casos)
    escribe(os.path.join(SALIDA, "libro.json"), {"edicion": 14, "fecha": max(por_dia), "reglas": reglas})

    print(f"actas: {len(todas)} (errores: {len(errores)}) · destacadas: {len(destacadas)} · dudas: {len(dudas)} · reglas: {len(reglas)} · "
          f"casos del arnés: {nota['casos']} · nota: {nota['nota']}")
    for f, e in errores[:10]:
        print(f"  error en {f}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
