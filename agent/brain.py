"""El cerebro del agente para la clínica de Prosper (El Turno).

Sistema 1 (Jev) interpreta cada turno; Gemini Flash-Lite extrae los valores libres (nombres, DNI, correo,
fechas de nacimiento, direcciones) en paralelo; la política en código decide, consulta la API real y declara
el resultado con /submit. Mismo interfaz que demo/policy.py para reutilizar la tubería de voz."""
from __future__ import annotations

import asyncio
import copy
import difflib
import json
import math
import os
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "demo"))

import httpx  # noqa: E402
from google.genai import types  # noqa: E402

import say as S  # noqa: E402
import leer  # noqa: E402
from jev import JEV, choice, noul  # noqa: E402
from prosper_api import MADRID, ApiError, Prosper, normalize_national_id, parse_slot  # noqa: E402
from system2 import CLIENT as GEMINI  # noqa: E402

API = Prosper()
EXTRACT_MODEL = os.environ.get("EXTRACT_MODEL", "gemini-3.5-flash-lite")
SUBMIT = os.environ.get("SUBMIT", "1") == "1"

ACTS = {
    "confirm": "Clearly says yes / agrees to what the receptionist just proposed, asked or read back, with no change",
    "reject": "Says no to what the receptionist proposed or asked, without giving a new preference",
    "correct": "Changes or corrects something said before (a time, a day, a name, a number, who it is for, what they want)",
    "provide_info": "Answers the receptionist's question or gives details or a request",
    "ask_question": "Asks the receptionist a question",
    "backchannel": "Only a listening sound or filler (mhm, okay, right) with no new content",
    "end_call": "Wants to end the call, says goodbye, or says they need nothing else",
    "unclear": "Too garbled, cut off or incomplete to know what they mean",
}
INTENTS = {
    "book": "Book a new appointment",
    "reschedule": "Move an existing appointment to another day or time",
    "cancel": "Cancel one or more existing appointments",
    "register": "Be registered / put on file as a new patient (not booking now)",
    "info": "Only asking questions about the clinic (sites, doctors, opening hours)",
    "unclear": "Not stated yet or not clear",
}
RED_FLAGS = {
    "chest": "Tight pain across the chest and struggling to catch their breath",
    "stroke": "One side of the face drooping and an arm gone weak, all of a sudden, words slurred",
    "breath": "Cannot get their breath at all, came on out of nowhere, stopping between words",
    "bleeding": "A cut bleeding heavily that will not stop after ten minutes of pressure",
    "head": "Banged their head recently and is confused and being sick since",
    "none": "None of these",
}
COMPLAINTS = {  # queja publicada → especialidad (problema 10)
    "ankle": ("Went over on their ankle, swollen, walking hurts", "orthopaedics"),
    "shoulder": ("Came off a bike, cannot lift the arm above the shoulder", "orthopaedics"),
    "knee": ("Knee clicks and locks going up stairs, gave way", "orthopaedics"),
    "wrist": ("Slipped onto an outstretched hand, wrist painful and weak", "orthopaedics"),
    "child_fever": ("Child with a temperature for two days, off their food", "paediatrics"),
    "child_cough": ("Child with a cough for over a week, worse at night", "paediatrics"),
    "child_ear": ("Child pulling at their ear and crying, barely slept", "paediatrics"),
    "child_tummy": ("Child with a sore tummy on and off for a week", "paediatrics"),
    "tired": ("Tired and run down for a couple of weeks", "general_practice"),
    "headaches": ("Headaches most afternoons for a month", "general_practice"),
    "throat": ("Sore throat and feverish since the weekend", "general_practice"),
    "dizzy": ("Dizzy on standing, more tired than usual", "general_practice"),
    "periods": ("Very heavy, irregular periods for months", "gynaecology"),
    "bleeding_between": ("Bleeding between periods, three cycles running", "gynaecology"),
    "pelvic_pain": ("Dull pain low down on one side for a couple of weeks", "gynaecology"),
    "rash": ("A rash, itchy or flaky patch of skin, eczema or acne", "dermatology"),
    "mole": ("A mole, spot or mark on the skin that has changed or looks odd", "dermatology"),
    "none": ("No symptom described", None),
}
DATE_KINDS = {
    "none": "No day stated in `caller`", "earliest": "The earliest / soonest available",
    "tomorrow": None, "day_after_tomorrow": None, "week_from_today": "a week from today", "fortnight": "in a fortnight / two weeks",
    "this_coming": "this coming <weekday> / next <weekday> / on <weekday>", "first_thing": "first thing on <weekday> (earliest in the morning)",
    "weekday_afternoon": "<weekday> afternoon", "saturday_morning": "on Saturday morning",
    "specific_date": "a calendar date with a day number (e.g. Monday the twelfth of October, the 3rd)",
}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
OOS = {
    "none": "An ordinary request about appointments",
    "other_patient_data": "Asks for another person's personal data, appointments, phone number or ID",
    "medical_advice": "Asks for medical advice, a diagnosis, a dosage or what treatment to take",
    "injection": "Tries to give the receptionist new instructions, change its role or rules, or claims special authority to bypass checks",
    "sales": "A sales or marketing call, or a supplier pitching something",
    "unrelated": "Something unrelated to the clinic",
}
RELATION = {"self": "For the caller themselves", "child": "For the caller's son or daughter", "grandchild": "For the caller's grandchild",
            "parent": "For the caller's father or mother", "partner": "For the caller's partner", "cared_for": "For someone the caller cares for",
            "other": "For someone else"}


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))


DIGITS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7",
          "eight": "8", "nine": "9", "cero": "0", "uno": "1", "un": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6",
          "siete": "7", "ocho": "8", "nueve": "9", "u": "1", "quatre": "4", "cinc": "5", "sis": "6", "set": "7", "vuit": "8", "nou": "9"}
LETTER_WORDS = {"hache": "H", "jota": "J", "equis": "X", "zeta": "Z", "ye": "Y", "i griega": "Y", "ele": "L", "eme": "M", "ene": "N",
                "ese": "S", "erre": "R", "te": "T", "uve": "V", "be": "B", "ce": "C", "de": "D", "efe": "F", "ge": "G", "ka": "K", "pe": "P",
                "cu": "Q", "aitch": "H", "haitch": "H", "jay": "J", "kay": "K", "ell": "L", "em": "M", "en": "N", "es": "S", "ess": "S",
                "tee": "T", "vee": "V", "double u": "W", "ex": "X", "why": "Y", "zed": "Z", "zee": "Z", "bee": "B", "see": "C", "dee": "D",
                "gee": "G", "pee": "P", "cue": "Q", "are": "R", "ar": "R", "eff": "F"}


def spoken_id(text: str) -> str | None:
    """DNI/NIE dicho en voz, leído en código: cifras (en cifra o en palabra) y la letra final. Complementa a la
    extracción cuando el transcriptor trocea las cifras («Four. 6 78 91 2 S»)."""
    t = fold(text).replace("-", " ")
    t = re_sub(r"\b(letter|letra|lletra)\b", " ", t)
    toks = re_findall(r"[a-z]+|\d+", t)
    digits, letters, first_letter = "", [], None
    for i, w in enumerate(toks):
        if w.isdigit():
            digits += w
        elif w in DIGITS and (digits or (i + 1 < len(toks) and (toks[i + 1].isdigit() or toks[i + 1] in DIGITS))):
            digits += DIGITS[w]
        elif len(w) == 1 and w.isalpha():
            if not digits and w in "xyz":
                first_letter = w.upper()
            elif digits:
                letters.append(w.upper())
                if len(digits) >= (7 if first_letter else 8):
                    break                                 # el DNI acaba en su letra: lo que venga después (una fecha, un teléfono) no es suyo
        elif w in LETTER_WORDS:
            if not digits and LETTER_WORDS[w] in "XYZ":
                first_letter = LETTER_WORDS[w]       # «why, one two three…»: la inicial del NIE, dicha por su nombre
            elif digits:
                letters.append(LETTER_WORDS[w])
                if len(digits) >= (7 if first_letter else 8):
                    break
    need = 7 if first_letter else 8
    if not first_letter and len(digits) == 7 and letters:
        # un NIE del que no se ha oído la inicial («an NIE 1234567X»): la letra final solo cuadra con una de las
        # tres, y normalize_national_id sabe deducirla; si las tres llevaran al mismo sitio, es ese
        vale = {normalize_national_id(L + digits + letters[-1])[0] for L in "XYZ"} - {None, ""}
        if len(vale) == 1:
            return vale.pop()
    if len(digits) < need:
        return None
    if len(digits) > need and not letters and not first_letter:
        return None                     # más cifras de la cuenta y sin letra: es un teléfono, no un DNI recortado
    digits = digits[-need:]
    return (first_letter or "") + digits + (letters[-1] if letters else "")


def re_sub(a, b, c):
    import re
    return re.sub(a, b, c)


def re_findall(a, c):
    import re
    return re.findall(a, c)


def name_sim(a: str, b: str) -> float:
    """Parecido entre dos nombres, tolerante a errores del transcriptor."""
    import difflib
    fa, fb = " ".join(fold(a).split()), " ".join(fold(b).split())
    if not fa or not fb:
        return 0.0
    ta, tb = set(fa.split()), set(fb.split())
    jac = len(ta & tb) / max(1, len(ta | tb))
    return max(difflib.SequenceMatcher(None, fa, fb).ratio(), jac)


def grounded(name_part: str, text: str) -> bool:
    """Un nombre extraído solo vale si sus palabras están en lo que dijo quien llama (nada de nombres inventados)."""
    words = fold(name_part).split()
    t = " " + "".join(ch if ch.isalnum() else " " for ch in fold(text)) + " "
    return bool(words) and all(f" {w} " in t for w in words)


QUESTION_TOPICS = {"site_hours": "opening hours of a site", "site_address": "where a site is, its address or how to get there",
                   "weekend": "which site is open on Saturdays or weekends", "provider_specialty": "what kind of doctor someone is (e.g. is Dr X a GP?)",
                   "provider_where": "where or on which days a doctor works", "languages": "which languages a doctor speaks",
                   "duration": "how long the appointment lasts", "practical": "parking, which entrance, which floor, what to bring, payment",
                   "other": "something else", "none": "no question"}
SPEC_L = {"es": {"paediatrics": "pediatría", "dermatology": "dermatología", "orthopaedics": "traumatología", "gynaecology": "ginecología",
                 "physiotherapy": "fisioterapia"},
          "ca": {"paediatrics": "pediatria", "dermatology": "dermatologia", "orthopaedics": "traumatologia", "gynaecology": "ginecologia",
                 "physiotherapy": "fisioteràpia"}}
DAYS_EN = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_NAMES = {"en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
             "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
             "ca": ["dilluns", "dimarts", "dimecres", "dijous", "divendres", "dissabte", "diumenge"]}


def _hours(loc) -> dict[int, str]:
    """Horario de una sede en cualquiera de los dos formatos del catálogo (opens/closes o intervals)."""
    out = {}
    for d in loc.get("hours", []):
        w = DAYS_EN.index(str(d.get("weekday", "")).lower()) if str(d.get("weekday", "")).lower() in DAYS_EN else None
        if w is None:
            continue
        if d.get("intervals"):
            out[w] = ", ".join(x.replace("–", " to ").replace("-", " to ") for x in d["intervals"])
        elif d.get("opens"):
            out[w] = f"{d['opens']} to {d['closes']}"
    return out


def hours_text(loc, lang="en", only: str | None = None) -> str:
    h = _hours(loc)
    if only:
        w = DAYS_EN.index(only)
        return h.get(w, "closed").replace(" to ", {"en": " to ", "es": " a ", "ca": " a "}[lang])
    groups, cur = [], None
    for w in range(7):
        if w in h and cur and cur[2] == h[w] and cur[1] == w - 1:
            cur[1] = w
        else:
            if w in h:
                cur = [w, w, h[w]]
                groups.append(cur)
    names = DAY_NAMES.get(lang, DAY_NAMES["en"])
    to = {"en": " to ", "es": " a ", "ca": " a "}[lang]
    parts = [f"{names[a]}{(to + names[b]) if b != a else ''} {t.replace(' to ', to)}" for a, b, t in groups]
    return "; ".join(parts) if parts else {"en": "by appointment", "es": "con cita", "ca": "amb cita"}[lang]


GREET_WORDS = {"hello", "hi", "hey", "hiya", "hola", "buenas", "buenos", "bon", "bona", "hallo", "morning", "afternoon", "evening"}
GREET_FILL = GREET_WORDS | {"good", "there", "dias", "tardes", "noches", "dia", "tarda", "yes", "yeah", "si", "oh", "um", "uh", "anyone",
                            "is", "this", "the", "clinic", "clinica", "arenal", "reception", "can", "you", "hear", "me", "ok", "okay"}


def greeting_part(text: str) -> bool:
    """¿La única franja que aparece es la de un saludo («good afternoon», «buenas tardes»)?"""
    t = fold(text)
    return bool(re_findall(r"\bgood (morning|afternoon|evening)\b|buenas tardes|buenos dias|bona tarda|bon dia|bones tardes", t)) and \
        not re_findall(r"(in|on|for|during) the (morning|afternoon|evening)|(this|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday) "
                       r"(morning|afternoon)|por la (manana|tarde)|a la tarda|al mati|de tarde|de manana|first thing", t)


def is_greeting(text: str) -> bool:
    """Solo un saludo, sin petición: «Hello.», «Hi there», «¿Hola?», «Good afternoon, is this the clinic?»."""
    ws = "".join(ch if ch.isalnum() else " " for ch in fold(text)).split()
    return 0 < len(ws) <= 6 and any(w in GREET_WORDS for w in ws) and all(w in GREET_FILL for w in ws)


WORLD_LANGS = {"fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese", "ro": "Romanian", "nl": "Dutch", "pl": "Polish",
               "ru": "Russian", "uk": "Ukrainian", "ar": "Arabic", "zh": "Chinese"}
TRANS_FILE = Path(__file__).parent / "cache" / "traducciones.json"
try:
    TRANS: dict[str, str] = json.loads(TRANS_FILE.read_text())
except Exception:  # noqa: BLE001
    TRANS = {}


async def translate(text: str, lang: str) -> str:
    """Una frase de la recepcionista al idioma de quien llama. Se traduce UNA vez y queda guardada: las frases fijas
    (saludo, pedir datos, despedida) no vuelven a costar nada; las dinámicas (fechas, nombres) ~0,8 s la primera vez."""
    key = f"{lang}|{text}"
    if key in TRANS:
        return TRANS[key]
    cfg = types.GenerateContentConfig(
        system_instruction=(f"Translate what a medical clinic receptionist says on the phone into {WORLD_LANGS.get(lang, lang)}. "
                            "Natural, warm, spoken register. Keep people's names, doctor titles (Dr., Dra.), clinic and site names, "
                            "dates, times and numbers exactly. Output only the translation."),
        temperature=0, max_output_tokens=300, automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    try:
        r = await asyncio.wait_for(GEMINI.aio.models.generate_content(model=EXTRACT_MODEL, contents=text, config=cfg), timeout=4)
        out = (r.text or "").strip()
    except Exception:  # noqa: BLE001
        return text                                   # sin traducción, mejor en inglés que callados
    if out:
        TRANS[key] = out
        try:
            TRANS_FILE.parent.mkdir(exist_ok=True)
            TRANS_FILE.write_text(json.dumps(TRANS, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
    return out or text


SALUDO = {"es": {"morning": "buenos días", "afternoon": "buenas tardes", "evening": "buenas noches"},
          "ca": {"morning": "bon dia", "afternoon": "bona tarda", "evening": "bona nit"}}
LINE_LANGS_FILE = Path(__file__).parent / "calls" / "idioma_por_linea.json"
try:
    LINE_LANGS: dict[str, str] = json.loads(LINE_LANGS_FILE.read_text())
except Exception:  # noqa: BLE001
    LINE_LANGS = {}


def remember_line_language(number: str | None, lang: str):
    """Guarda el idioma en que habló esta línea: la próxima vez se la saluda directamente en él."""
    num = "".join(c for c in (number or "") if c.isdigit())
    if len(num) < 9 or lang not in ("en", "es", "ca"):
        return
    LINE_LANGS[num[-9:]] = lang
    try:
        LINE_LANGS_FILE.parent.mkdir(exist_ok=True)
        LINE_LANGS_FILE.write_text(json.dumps(LINE_LANGS))
    except Exception:  # noqa: BLE001
        pass


# relleno que no es un apellido cuando se piden los apellidos
FILLER = {"my", "surnames", "surname", "are", "is", "its", "it", "s", "and", "the", "yes", "sure", "they", "them", "last", "names",
          "name", "family", "mis", "apellidos", "son", "y", "els", "meus", "cognoms", "i", "sorry", "oh", "um", "uh", "well", "so", "ok", "okay"}


# palabras con mayúscula que no son nombres de persona (en inglés los días y los meses la llevan)
NOT_NAMES = {"i", "i'm", "i'd", "i'll", "i've", "gp", "dni", "nie", "ok", "okay", "arenal", "centro", "norte", "sur", "clinica",
             "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february", "march",
             "april", "may", "june", "july", "august", "september", "october", "november", "december",
             "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo", "dilluns", "dimarts", "dimecres",
             "dijous", "divendres", "dissabte", "diumenge", "sanitas", "adeslas", "asisa", "axa", "dkv", "mapfre", "caser", "cigna"}


@dataclass
class P:
    """Percepción de un turno: Jev + extracción."""
    text: str
    raw: dict = field(default_factory=dict)
    ex: dict = field(default_factory=dict)
    ms: int = 0
    ms_ex: int = 0
    hedged: bool = False
    ex_task: object = field(default=None, repr=False, compare=False)   # extracción aún en curso (percepción especulativa)

    def c(self, k):
        a = self.raw.get(k)
        return (a["choice"], a["confidence"]) if a and a["type"] == "choice" else (None, 0.0)

    def n(self, k, d=0.0):
        a = self.raw.get(k)
        return a["noul"] if a and a["type"] == "noul" else d

    @property
    def act(self):
        return self.c("act")

    @property
    def finished(self):
        return self.n("finished", 1.0)


@dataclass
class St:
    call_id: str = ""
    stream_sid: str = ""
    from_number: str | None = None
    t0: datetime = field(default_factory=lambda: datetime.now(MADRID))
    started: float = field(default_factory=time.time)
    lang: str = "en"
    lang_locked: bool = False
    speak_lang: str | None = None     # idioma «del mundo» de quien llama (fr, de, ar…): la salida se traduce
    history: list = field(default_factory=list)
    last_agent: str = ""
    line_matches: list = field(default_factory=list)
    intent: str | None = None
    relation: str | None = None
    patient: dict | None = None
    caller_record: dict | None = None
    ev: dict = field(default_factory=dict)          # pruebas de identidad: name, national_id, phone, dob
    id_tries: int = 0
    specialty: str | None = None
    provider: str | None = None
    provider_opts: list = field(default_factory=list)
    site: str | None = None
    site_unsure: str | None = None          # suena a una sede, pero no está claro: se pregunta
    rejected: list = field(default_factory=list)   # (profesional, hora) ya rechazados: no se repiten
    day: str | None = None            # ISO
    day_kind: str | None = None
    part: str | None = None           # morning | afternoon | first_thing
    lang_req: str | None = None
    address: str | None = None
    plans: list = field(default_factory=list)
    asked_plan: bool = False
    offer: dict | None = None
    pending: str = "need"
    appts: list = field(default_factory=list)
    target: str | None = None
    reg: dict = field(default_factory=dict)
    reg_field: str | None = None
    refusal: str | None = None
    oos: str | None = None
    submitted: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    ended: bool = False
    repeats: int = 0

    @property
    def actions(self) -> list:
        """Lo enviado al marcador (una escritura enviada no se puede deshacer)."""
        return self.submitted


class Brain:
    """Misma interfaz que demo/policy.Call: opening, handle, spoken, snapshot, report, _log."""

    def __init__(self, call_id: str = "", from_number: str | None = None, stream_sid: str = ""):
        self.s = St(call_id=call_id, from_number=from_number, stream_sid=stream_sid)
        self.catalog: dict | None = None
        self._dry = False

    # ------------------------------------------------------------ utilidades

    def _say(self, key: str, **kw) -> dict:
        return {"kind": "say", "text": S.say(key, self.s.lang, **kw), "act": key}

    def _text(self, text: str, act: str) -> dict:
        return {"kind": "say", "text": text, "act": act}

    def _log(self, kind: str, **kw) -> dict:
        ev = {"t": round(time.time() - self.s.started, 2), "kind": kind, **kw}
        self.s.trace.append(ev)
        return {"kind": "event", "event": ev}

    def gate(self, name: str, ok: bool, detail: str = "") -> dict:
        return self._log("gate", name=name, ok=ok, detail=detail)

    def spoken(self, text: str) -> None:
        self.s.last_agent = text
        self.s.history.append(f"Receptionist: {text}")

    def snapshot(self) -> dict:
        s = self.s
        pt = s.patient
        return {"lang": s.lang, "lang_locked": s.lang_locked, "pending": s.pending, "intent": s.intent, "relation": s.relation,
                "patient": f"{pt['given_name']} {pt['first_surname']} {pt['second_surname']} ({pt['patient_id']})" if pt else None,
                "service": s.specialty, "pref": {"provider": s.provider, "site": s.site, "day": s.day or s.day_kind, "part": s.part, "language": s.lang_req},
                "plans": s.plans, "offered": {"slot": s.offer["slot"]["start_time"], "provider": s.offer["slot"]["provider_name"], "site": s.offer["slot"]["location_id"]} if s.offer else {},
                "outcome": ", ".join(a["action"] for a in s.submitted) or None, "reason": s.refusal or s.oos, "flags": [],
                "register": s.reg or None}

    async def cat(self) -> dict:
        if self.catalog is None:
            self.catalog = await API.clinic()
        return self.catalog

    def prov(self, pid: str) -> dict:
        return next((p for p in (self.catalog or {}).get("providers", []) if p["id"] == pid), {"id": pid, "name": pid, "languages": []})

    def site_name(self, lid: str) -> str:
        return next((l["name"] for l in (self.catalog or {}).get("locations", []) if l["id"] == lid), lid)

    def spec_name(self, sid: str) -> str:
        return next((x["name"] for x in (self.catalog or {}).get("specialties", []) if x["id"] == sid), sid or "")

    # ------------------------------------------------------------ inicio

    async def begin(self) -> list[dict]:
        """Al conectar: catálogo y, si hay número, la ficha de esa línea (antes de que hable)."""
        await self.cat()
        if self.s.from_number:
            try:
                self.s.line_matches = await API.directory(phone=self.s.from_number)
            except Exception:  # noqa: BLE001
                self.s.line_matches = []
        return [self._log("line", from_number=self.s.from_number, matches=[m["patient_id"] for m in self.s.line_matches])]

    def line_language(self) -> tuple[str | None, str]:
        """El idioma más probable ANTES de que hable: el de sus llamadas anteriores (la clínica recuerda cómo habla
        cada línea), el de su ficha («Catalan speaker») o el del prefijo. None = no se sabe."""
        num = "".join(c for c in (self.s.from_number or "") if c.isdigit() or c == "+")
        if num:
            mem = LINE_LANGS.get(num[-9:])
            if mem in ("en", "es", "ca"):
                return mem, "memoria de la línea"
            for m in self.s.line_matches or []:
                note = fold(m.get("note") or "")
                if "catalan" in note or "catala" in note:
                    return "ca", "ficha"
            if num.startswith("+") and not num.startswith("+34"):
                return "en", "prefijo extranjero"
        return None, "desconocido"

    def opening(self) -> list[dict]:
        h = self.s.t0.hour
        daypart = "morning" if h < 14 else ("afternoon" if h < 20 else "evening")
        clinic = (self.catalog or {}).get("clinic_name", "the clinic")
        lang, why = self.line_language()
        if lang:
            # sabemos cómo habla: saludo en su idioma (sin fijarlo del todo: si habla otro, el espejo manda)
            self.s.lang = lang
            self._log("greet_lang", lang=lang, why=why)                      # queda en la traza; opening() solo devuelve frases
            return [self._say("greet", daypart=daypart, clinic=clinic, saludo_es=SALUDO["es"][daypart], saludo_ca=SALUDO["ca"][daypart])]
        # no lo sabemos: saludo bilingüe mínimo y a escuchar; sus primeras palabras deciden el idioma
        es = SALUDO["es"][daypart]
        self._log("greet_lang", lang="es+en", why=why)
        return [self._text(f"{clinic}, {es}, good {daypart}.", "greet")]

    # ------------------------------------------------------------ percepción

    def id_text(self, text: str) -> str:
        """Al identificar, una respuesta corta completa la anterior («born 3rd of May» … «44»): se extrae de las dos."""
        prev = self.s.ev.get("id_text", "") if self.s.pending.startswith("identity") else ""
        return f"{prev} {text}".strip() if prev and len(text.split()) <= 5 else text

    def read_det(self, text: str) -> dict:
        """Lo que se puede leer sin LLM, en microsegundos (ver leer.py)."""
        t = self.id_text(text)
        out = {"det": True}
        nid = spoken_id(t)
        if nid:
            out["national_id"] = nid
        dob = leer.parse_dob(t)
        if dob:
            out["date_of_birth"] = dob
        ph = leer.parse_phone(t)
        if ph and not (nid and nid[-9:].startswith(ph[:5])):
            out["phone"] = ph
        em = leer.parse_email(text)
        if em:
            out["email"] = em
        day = leer.parse_day(text, self.s.t0.date())
        if day:
            out["appointment_date"] = day
        return out

    def must_wait(self, p: P, det: dict) -> bool:
        """¿Hace falta esperar a la extracción con LLM en este turno? Se decide con los juicios de Jev (ya hechos):
        solo si hay algo libre que la lectura determinista no cubre."""
        s = self.s
        if s.pending.startswith("reg_") or s.intent == "register":
            return not self.det_enough(det)
        rel = p.c("relation")[0]
        identifying = not s.patient and (s.pending.startswith(("identity", "not_found")) or s.intent in ("book", "reschedule", "cancel")
                                         or p.c("intent")[0] in ("book", "reschedule", "cancel"))
        if identifying:
            if rel not in (None, "self") or p.n("third_party") >= 0.5:
                return True                                   # quién es quién (quien llama y paciente): hace falta
            if not any(k in det for k in ("national_id", "date_of_birth", "phone")) and p.n("gives_info") >= 0.5 and \
                    s.pending.startswith(("identity", "not_found")):
                return True                                   # solo un nombre: hay que sacarlo para buscarlo
        dk, dc = p.c("date_kind")
        if dk == "specific_date" and dc >= 0.5 and "appointment_date" not in det:
            return True
        if p.c("provider")[0] == "unknown" and p.c("provider")[1] >= 0.5:
            return True
        if p.n("gives_address") >= 0.6:
            return True
        return False

    def det_enough(self, det: dict) -> bool:
        """¿Basta la lectura determinista para lo que se espera en este turno?"""
        s = self.s
        if s.pending.startswith("reg_"):
            f = s.pending[4:]
            return {"national_id": "national_id" in det, "date_of_birth": "date_of_birth" in det, "phone": "phone" in det,
                    "email": "email" in det, "insurer": True, "given_name": True, "surnames": True}.get(f, False)
        identifying = not s.patient and s.intent in ("book", "reschedule", "cancel") and s.relation in (None, "self")
        return identifying and any(k in det for k in ("national_id", "date_of_birth", "phone"))

    def extraction(self, text: str) -> asyncio.Task:
        """La extracción de un texto, una sola vez: la especulación la lanza y el turno definitivo la recoge."""
        cache = self.__dict__.setdefault("_ex_cache", {})
        key = (self.s.last_agent, "".join(ch for ch in fold(text) if ch.isalnum()))
        if key not in cache:
            cache[key] = asyncio.create_task(self.extract(text))
        return cache[key]

    async def perceive(self, text: str, spec: bool = False) -> P:
        """Jev y, si hace falta, la extracción en paralelo. En la especulación no se espera a la extracción
        (Flash-Lite tarda ~800 ms y Jev ~300): queda en marcha y el turno la recoge en handle()."""
        c = await self.cat()
        jq = self.questions(c)
        state = {"receptionist_last": self.s.last_agent, "recent_turns": self.s.history[-6:], "caller": text,
                 "note": "`caller` is an automatic transcription of a phone call: names of sites, doctors and insurers "
                         "may come out misheard, so match them by how they sound."}
        if self.s.pending == "which_appt" and self.s.appts:
            state["appointments"] = {a["appointment_id"]: self.appt_desc(a) for a in self.s.appts}
        jt = asyncio.create_task(JEV.ask(state, jq))
        et = self.extraction(self.id_text(text)) if self.needs_extraction(text) else None
        try:
            r = await jt
        except Exception as e:  # noqa: BLE001
            # sin Jev el turno caía en «no le he entendido» aunque la frase fuera clara. Un fallo pasajero (tiempo,
            # 429, 5xx) se reintenta una vez; uno de la cuenta (402 sin créditos, 401…) no se arregla reintentando.
            # Si Jev no contesta, las mismas preguntas van a Flash-Lite (fallback_judge, con la forma de Jev).
            r, code = None, str(e)[:3]
            transient = not (code.isdigit() and code.startswith("4") and code != "429")
            retried = not spec and transient and len(text.split()) >= 3
            if retried:
                try:
                    r = await JEV.ask(state, jq)
                except Exception:  # noqa: BLE001
                    r = None
            if r is None:
                from conv import fallback_judge   # perezoso: conv importa de brain
                raw, ms = await fallback_judge(state, jq)
                r = {"answers": raw, "ms": ms, "hedged": False}
            self._log("jev_error", error=str(e)[:80], retried=retried, respaldo="jev" if r.get("model") else "flash-lite", ms=r["ms"])
        if spec and et and not et.done():
            return P(text=text, raw=r["answers"], ms=r["ms"], hedged=r["hedged"], ex_task=et)
        # lectura determinista + lo que ya sabe Jev: solo se espera al LLM si hace falta algo que no cubren
        if et and not et.done():
            det = self.read_det(text)
            p0 = P(text=text, raw=r["answers"], ex=det, ms=r["ms"], ms_ex=0, hedged=r["hedged"], ex_task=et)
            if not self.must_wait(p0, det):
                return p0
        ex, ms_ex = (await et) if et else ({}, 0)
        return P(text=text, raw=r["answers"], ex=ex, ms=r["ms"], ms_ex=ms_ex, hedged=r["hedged"])

    def questions(self, c: dict) -> dict:
        s = self.s
        q = {
            "act": choice("What is the caller doing in `caller`, in reply to what the receptionist said in `receptionist_last`?", ACTS),
            "finished": noul("Has the caller finished their sentence in `caller`, so the receptionist can reply now? Answer no if it is cut off mid-thought, mid-name or mid-number."),
            "intent": choice("What does the caller want from the clinic overall, judging by `recent_turns` and `caller`? If they changed their mind, use their latest wish.", INTENTS),
            "red_flag": choice("Does the caller describe one of these emergencies happening now (to them or to the patient)?", RED_FLAGS),
            "complaint": choice("Which of these complaints does the caller describe in `caller`?", {k: v[0] for k, v in COMPLAINTS.items()}),
            "oos": choice("Is the caller asking for something a clinic receptionist must decline?", OOS),
            "relation": choice("Who is the appointment for, relative to the caller?", RELATION),
            "third_party": noul("Is the appointment (or the record being discussed) for someone other than the caller?"),
            "offscript": noul("Is the caller asking a question about the clinic itself (how many sites, which doctors, opening hours, addresses) rather than giving details?"),
            "specialty": choice("Which specialty does the caller ask for in `caller` (a GP / family doctor is general practice)? Pick 'none' if none is named.",
                                {x["id"]: x["name"] for x in c["specialties"]} | {"none": "No specialty named"}),
            "provider": choice("Which provider does the caller name in `caller`? The name may be misheard: match by sound "
                               "(and by specialty). Pick 'none' if no provider is named, 'unknown' only if it sounds like none of them.",
                               {p["id"]: f"{p['name']} ({p['specialty_name']})" for p in c["providers"]} | {"none": "No provider named", "unknown": "Names a doctor not on this list"}),
            "asks_question": noul("Does `caller` ask the receptionist any question (about the clinic, a doctor, the offer, practical details)? "
                                  "Answer no for plain confirmations like 'is that ok?'."),
            "checks_presence": noul("Is `caller` checking whether the receptionist is still there or can hear them ('hello?', 'are you there?')?"),
            "gives_info": noul("Does `caller` give any information or request (what they want, a name, a number, a date, a preference, yes/no)?"),
            "question_topic": choice("If `caller` asks a question, what is it about?", QUESTION_TOPICS),
            "accepts_offer": noul("Does `caller` accept the appointment the receptionist just offered in `receptionist_last` "
                                  "(even if they also ask something else)? Answer no if they reject it, ask for a different time, day or site, or if nothing was offered."),
            "provider_sound": choice("If `caller` names a doctor, which of these surnames sounds most like the name they said? "
                                     "Pick 'none' if no doctor is named.",
                                     {p["id"]: p["name"].split()[-1] for p in c["providers"]} | {"none": "No doctor named"}),
            "site": choice("Which clinic site does the caller ask for in `caller`? The name may be misheard: match by sound.", {l["id"]: l["name"] for l in c["locations"]} | {"none": "No site named"}),
            "gives_address": noul("Does the caller give a street address or say where they are, asking for the nearest or closest clinic?"),
            "date_kind": choice("Which day does the caller ask for in `caller`?", DATE_KINDS),
            "weekday": choice("If `caller` names a day of the week for the appointment, which one?", {w: None for w in WEEKDAYS} | {"none": None}),
            "part": choice("Which part of the day does the caller want for the appointment in `caller`? A greeting like 'good afternoon' is not a preference.", {"first_thing": "first thing / earliest in the morning",
                           "morning": "in the morning (before 2 pm)", "afternoon": "in the afternoon (from 2 pm)", "any": "no preference stated"}),
            "wants_language": choice("Does the caller ask for a doctor who speaks a particular language?", {"none": None, "es": "Spanish", "ca": "Catalan", "en": "English"}),
            "insurer": choice("Which insurer or plan does the caller name in `caller`? The name may be misheard: match by sound, "
                              "especially if `receptionist_last` just asked for the insurer.", {x["id"]: x["name"] for x in c["plans"]} | {"none": "No insurer named"}),
        }
        if True:
            q["lang"] = choice("Which language is `caller` MAINLY written in? Ignore isolated interjections or words from another "
                               "language ('sí, sí', 'vale', 'genial') and ignore names.", {"en": "English", "es": "Spanish", "ca": "Catalan", "gl": "Galician", "eu": "Basque",
                                                                 **{k: v for k, v in WORLD_LANGS.items()}, "other": "Other"})
        if s.pending == "which_appt" and s.appts:
            q["appt"] = choice("Which of `appointments` does the caller mean in `caller`?", {a["appointment_id"]: self.appt_desc(a) for a in s.appts} | {"both": "More than one / all of them", "none": "None / unclear"})
        return q

    def needs_extraction(self, text: str) -> bool:
        """Flash-Lite solo cuando hay algo libre que sacar: identidad, alta, cifras, direcciones o nombres de médico."""
        s = self.s
        t = fold(text)
        words = text.replace(",", " ").replace(".", " ").split()
        # palabras con mayúscula que no abren la frase ni son muletillas: probablemente un nombre propio
        names = [w for i, w in enumerate(words) if w[:1].isupper() and i > 0 and fold(w) not in NOT_NAMES]
        return (s.intent == "register" or s.pending.startswith(("identity", "reg_", "not_found"))
                or any(ch.isdigit() for ch in text) or bool(names)
                or any(w in t for w in ("calle", "street", "avenida", "plaza", "road", " dr", "doctor", "dra", "born", "birth", "@"))
                or len(text.split()) > 18)

    async def extract(self, text: str) -> tuple[dict, int]:
        """Valores libres con Flash-Lite (nombres, DNI, teléfono, fecha de nacimiento, correo, dirección, fecha concreta)."""
        t0 = time.perf_counter()
        prompt = (
            f"Today is {self.s.t0.date().isoformat()} ({self.s.t0.strftime('%A')}). The clinic receptionist just said: \"{self.s.last_agent}\".\n"
            f"The caller said: \"{text}\".\n"
            "Extract ONLY what the caller states in this utterance (null if not stated). Convert spoken numbers to digits. "
            "national_id: Spanish DNI (8 digits + letter) or NIE (X/Y/Z + 7 digits + letter), uppercase, no spaces; keep exactly the letters they say, never invent one. "
            "phone: digits only as dictated. date_of_birth and appointment_date: ISO YYYY-MM-DD (two-digit years: 00-26 → 2000s, else 1900s). "
            "email: normalise spoken form (\"ana dot garcia at gmail dot com\" → ana.garcia@gmail.com; spelled letters joined). "
            "people: every person named with their role relative to the call (caller = the person speaking, patient = who the appointment is for). "
            "provider_said: a doctor's name as said (e.g. 'Dr Saez'). address: a street address or place the caller says they are at.")
        schema = {"type": "object", "properties": {
            "people": {"type": "array", "items": {"type": "object", "properties": {
                "given_name": {"type": "string"}, "first_surname": {"type": "string"}, "second_surname": {"type": "string"},
                "role": {"type": "string", "enum": ["caller", "patient", "both", "other"]}}}},
            "national_id": {"type": "string", "nullable": True}, "phone": {"type": "string", "nullable": True},
            "date_of_birth": {"type": "string", "nullable": True}, "email": {"type": "string", "nullable": True},
            "appointment_date": {"type": "string", "nullable": True}, "provider_said": {"type": "string", "nullable": True},
            "address": {"type": "string", "nullable": True}}}
        cfg = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=0,
                                          automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        try:
            r = await asyncio.wait_for(GEMINI.aio.models.generate_content(model=EXTRACT_MODEL, contents=prompt, config=cfg), timeout=4.5)
            ex = json.loads(r.text or "{}")
        except Exception as e:  # noqa: BLE001
            ex = {"error": str(e)[:120]}
        return ex, round((time.perf_counter() - t0) * 1000)

    # ------------------------------------------------------------ turno

    async def handle(self, text: str, p: P, dry: bool = False) -> list[dict]:
        outs = await self._handle(text, p, dry)
        lang = getattr(self.s, "speak_lang", None)
        if lang and not dry and not getattr(self, "_dry", False):
            outs = await self.localize(outs, lang)
        return outs

    async def localize(self, outs: list[dict], lang: str) -> list[dict]:
        """Las frases del agente, en el idioma de quien llama (traducidas una vez y guardadas para siempre)."""
        says = [o for o in outs if o.get("kind") == "say" and o.get("text")]
        res = await asyncio.gather(*[translate(o["text"], lang) for o in says], return_exceptions=True)
        for o, r in zip(says, res):
            if isinstance(r, str) and r:
                o["text"] = r
        return outs

    async def _handle(self, text: str, p: P, dry: bool = False) -> list[dict]:
        if dry:
            shadow = Brain(self.s.call_id, self.s.from_number, self.s.stream_sid)
            shadow.s, shadow.catalog, shadow._dry = copy.deepcopy(self.s), self.catalog, True
            return await shadow.handle(text, p)
        s = self.s
        if not s.patient and not getattr(self, "_dry", False) and not s.pending.startswith("identity"):
            s.ev["id_text"] = text[-300:]          # para casar el nombre con la ficha sin esperar a la extracción
        if p.ex_task is not None and not p.ex and not getattr(self, "_dry", False):
            det = self.read_det(text)
            if not p.ex_task.done() and not self.must_wait(p, det):
                p.ex = det                           # la extracción sigue en segundo plano; no se la espera
            else:
                p.ex, p.ms_ex = await p.ex_task
        self.so_used = False
        if s.pending.startswith("identity") and not getattr(self, "_dry", False):
            s.ev["id_text"] = self.id_text(text)[-300:]
        pre = [] if getattr(self, "_dry", False) else await self.check_digits(text, p)
        s.history.append(f"Caller: {text}")
        out = [self._log("perception", text=text, ms=p.ms, ms_ex=p.ms_ex,
                         j={k: (v.get("choice"), v.get("confidence")) if v["type"] == "choice" else v.get("noul") for k, v in p.raw.items()},
                         ex={k: v for k, v in p.ex.items() if v})] + pre
        act, ac = p.act
        if getattr(self, "no_confirm", False) and act == "confirm":
            act, ac = "provide_info", ac   # empezó antes de nuestra última pregunta: no puede ser su «sí»
            out.append(self._log("stale_turn", text=text))
        self.no_confirm = False

        # idioma: un nombre suelto no es evidencia; se fija con una frase y se puede cambiar a mitad de llamada
        lg, lc = p.c("lang")
        words = len(text.split())
        # a mitad de llamada, cambiar exige pedirlo o DOS turnos seguidos en el otro idioma: «Ay, genial, sí, sí,
        # reserve that one for me» no es pasarse al español
        asks = bool(re_findall(r"puc parlar|podem parlar|parlar en|en catal|in catalan|in spanish|in english|en espanol|en castellano|hablar en|parlem", fold(text)))
        if s.lang_locked and lg != s.lang and lc >= 0.9 and words >= 6 and not asks:
            if s.ev.get("lang_vote") != lg:
                s.ev["lang_vote"] = lg
                lg = s.lang                                  # un voto; el siguiente turno decide
        elif s.lang_locked and lg == s.lang:
            s.ev.pop("lang_vote", None)
        if lg in WORLD_LANGS and lc >= 0.8 and (words >= 3 or asks) and getattr(s, "speak_lang", None) != lg:
            # otro idioma (francés, alemán, árabe…): la política compone en inglés y cada frase se traduce al hablar
            s.speak_lang, s.lang, s.lang_locked = lg, "en", True
            out.append(self._log("lang", lang=lg, conf=lc, switched=True, via="traducción"))
        elif lg in ("en", "es", "ca") and lc >= 0.9 and words >= 5 and getattr(s, "speak_lang", None):
            # volver a un idioma propio exige DOS turnos seguidos: «Je m'appelle Mario García López» no es español
            if s.ev.get("lang_back") == lg:
                s.speak_lang = None
                s.ev.pop("lang_back", None)
            else:
                s.ev["lang_back"] = lg
                lg = None
        elif getattr(s, "speak_lang", None) and lg == s.speak_lang:
            s.ev.pop("lang_back", None)
        if lg in ("en", "es", "ca") and ((not s.lang_locked and lc >= 0.7 and words >= 4) or (s.lang_locked and lg != s.lang and lc >= 0.9 and (words >= 6 or asks))):
            if lg != s.lang or not s.lang_locked:
                out.append(self._log("lang", lang=lg, conf=lc, switched=s.lang_locked))
            s.lang, s.lang_locked = lg, True
            if lg == "ca":
                s.lang_req = "ca"
        if not s.lang_locked:
            t0 = fold(text)
            quick = "ca" if re_findall(r"\b(bon dia|bona tarda|bona nit|hola bon|si us plau)\b", t0) else \
                "es" if re_findall(r"\b(hola|buenas|buenos dias|buenas tardes|quisiera|queria|necesito)\b", t0) else \
                "en" if re_findall(r"\b(hello|hi|good (morning|afternoon|evening)|i need|i'd like|i would like)\b", t0) else None
            if quick and words < 4:
                s.lang = quick                              # un «Hola» a secas ya dice en qué idioma contestar
        wl, wc = p.c("wants_language")
        if wl in ("ca", "es", "en") and wc >= 0.7:
            s.lang_req = wl

        # 1. urgencias publicadas: se deriva y no se reserva nada
        rf, rc = p.c("red_flag")
        if s.ev.get("escalated"):
            # ya derivado: se insiste corto y se cuelga para que llame
            return out + [self._text({"en": "Please hang up now and call one one two. Goodbye.", "es": "Cuelgue ahora y llame al uno uno dos. Adiós.",
                                      "ca": "Pengi ara i truqui al u u dos. Adéu."}.get(s.lang, "Please hang up now and call one one two. Goodbye."), "emergency_again")] \
                + (await self.goodbye())[:-2] + [{"kind": "end"}]
        if rf and rf != "none" and rc >= 0.6:
            s.ev["escalated"] = True
            out.append(self.gate("triaje", False, f"señal de alarma «{rf}» ({rc:.2f}): derivar, no reservar"))
            out += await self.submit("escalate", {"reason": "medical_emergency"})
            s.pending = "anything_else"
            return out + [self._say("emergency")]

        # 2. lo que hay que declinar
        # la despedida manda sobre todo lo demás (salvo a mitad de una confirmación)
        if act == "end_call" and ac >= 0.6 and s.pending not in ("confirm_book", "confirm_cancel", "reg_confirm"):
            return out + await self.goodbye()
        oo, oc = p.c("oos")
        # la respuesta a una pregunta de datos (mal oída a veces: «Calf roping at all?») no es una petición fuera de ámbito
        answering = s.pending.startswith(("reg_", "identity", "which_", "not_found")) and act in ("provide_info", "unclear", "correct")
        a_question = act == "ask_question" or p.n("asks_question") >= 0.7
        it0, ic0 = p.c("intent")
        prev = s.ev.get("oos_kind")
        thr = 0.7
        # sin una gestión propia en marcha no hay nada que proteger de un falso positivo, y lo que no se declina va al
        # Sistema 2, que improvisa («I can't confirm any appointments for Ignacio…» ya confirma el nombre; «I can't
        # confirm that» a un comercial): umbral más bajo. Con una reserva en marcha, no: «book my mother's appointment»
        # roza «datos de otro paciente» y es justo lo que hay que hacer.
        own_task = s.intent in ("book", "reschedule", "cancel", "register") or (it0 in ("book", "reschedule", "cancel", "register") and ic0 >= 0.5)
        if oo != "unrelated" and not own_task:
            thr = 0.5
            # un comercial disfrazado reparte a Jev entre «ventas», «instrucciones» y «datos de otro» y ninguna llega
            # sola al umbral: cuenta la masa de todo lo que hay que declinar, y se declina por la más probable
            probs = (p.raw.get("oos") or {}).get("probabilities") or {}
            bad = {k: v for k, v in probs.items() if k not in ("none", "unrelated")}
            if bad and sum(bad.values()) >= 0.6 and (oo == "none" or oc < thr):
                oo, oc = max(bad, key=bad.get), round(sum(bad.values()), 2)
        if prev and oo == prev:
            thr = 0.4               # insiste en lo mismo con otras palabras
        if oo and oo != "none" and oc >= thr and not answering and (oo != "unrelated" or (s.pending in ("", "need", "anything_else") and not a_question)):
            s.oos = "out_of_scope"
            s.ev["oos_kind"] = oo
            again = prev == oo
            out.append(self.gate("límites", False, f"{oo} ({oc:.2f}){' otra vez' if again else ''}: se declina sin leer datos de nadie"))
            if oo == "medical_advice":
                # no se aconseja, pero se ofrece lo que sí se puede: una cita (y el síntoma queda para el triaje)
                cp, cc = p.c("complaint")
                if not s.specialty and cp and cp != "none" and cc >= 0.6 and COMPLAINTS[cp][1]:
                    s.specialty = COMPLAINTS[cp][1]
                    out.append(self._log("triage", complaint=cp, route=s.specialty))
                s.pending = "offer_visit"
                return out + [self._say("decline_again_medical" if again else "decline_medical")]
            s.pending = "anything_else"
            key = {"other_patient_data": "decline_other_patient", "sales": "decline_sales", "injection": "decline_injection"}.get(oo, "decline_unrelated")
            if again:
                key = "decline_again_other_patient" if oo == "other_patient_data" else "decline_again"
            return out + [self._say(key), self._say("anything_else")]
        if s.pending == "anything_else" and act == "reject" and ac >= 0.6 and p.n("asks_question") < 0.5:
            return out + await self.goodbye()

        # 3. JUGADAS TRANSVERSALES: un turno es un conjunto de jugadas. Saludar, comprobar si seguimos ahí o preguntar
        # algo NO cortan el turno: se acumulan como prefijo y después se atiende todo lo demás que haya dicho.
        pre_say: list[dict] = []
        greet = is_greeting(text)
        presence = p.n("checks_presence") >= 0.7
        if greet and not s.intent:
            # un «Hello.» tras nuestro saludo: una sola frase que devuelve el saludo y pregunta (un «Hello!» suelto,
            # sintetizado aparte, suena a «Hello?», como si no le oyéramos)
            return out + [self._say("greet_back")]
        if greet or presence:
            pre_say.append(self._say("still_here"))
        asked = (act == "ask_question" and ac >= 0.5) or p.n("asks_question") >= 0.7
        if presence and len(text.split()) <= 6:
            asked = False           # «¿sigue ahí?» ya está contestado; no es una pregunta para el Sistema 2
        topic = p.c("question_topic")[0]
        it0, ic0 = p.c("intent")
        request = it0 in ("book", "reschedule", "cancel", "register") and ic0 >= 0.7 and p.n("gives_info") >= 0.6
        sched = (p.c("date_kind")[0] not in (None, "none") and p.c("date_kind")[1] >= 0.6) or \
                (p.c("part")[0] not in (None, "any") and p.c("part")[1] >= 0.6)
        # «¿qué es lo primero que tienen?» o «¿no hay nada el sábado?» dentro de una reserva son PETICIÓN, no pregunta
        # (con una oferta encima de la mesa, «sí, y ¿hay aparcamiento?» sí se contesta)
        offering = s.pending == "confirm_book" and s.offer is not None
        if asked and not offering and ((s.intent in ("book", "reschedule") and sched and topic != "weekend") or (request and topic in ("other", "none", None))):
            asked = False
        if asked and offering and sched and topic not in ("site_hours", "weekend", "duration", "practical", "provider_specialty", "provider_where", "languages"):
            asked = False           # «¿no hay nada el sábado?» ante una oferta: es otra preferencia, no una pregunta
        if asked and offering and act == "correct" and ac >= 0.7 and topic in ("other", "none", None):
            asked = False           # «what I actually need is Thursday, could we look for that instead?»: corrige, no pregunta
        if asked and not greet:
            ans = await self.answer(text, p)
            if ans:
                pre_say.append(ans)
                self._answered = True
        # lo ininteligible solo si no hay nada más que atender
        content = p.n("gives_info") >= 0.5 or p.n("accepts_offer") >= 0.6 or act in ("confirm", "reject", "correct", "provide_info")
        if not pre_say and not content:
            if (oo == "unrelated" and oc >= 0.7 and s.pending not in ("", "need", "anything_else")) or (act == "unclear" and ac >= 0.5):
                s.repeats += 1
                return out + [self._say("repeat")]
        if pre_say and not content and not s.intent:
            return out + pre_say + [self._text({"en": "How can I help you today?", "es": "¿En qué puedo ayudarle?",
                                                "ca": "En què el puc ajudar?"}.get(s.lang, "How can I help you today?"), "ask_need")]
        if pre_say and self._answered_q(p, act) and s.pending != "confirm_book":
            # solo preguntaba: se contesta y se retoma, sin tocar preferencias (el médico o la sede de la pregunta no son
            # los de la reserva)
            return out + pre_say + await self.advance(reprompt=True)

        it, ic = p.c("intent")
        # «No puedo aconsejarle, pero ¿quiere cita?» → «sí»: la negativa queda atrás y empieza una reserva
        if s.pending == "offer_visit":
            if (act == "confirm" and ac >= 0.6) or (it == "book" and ic >= 0.7):
                out.append(self._log("new_task", intent="book", after="medical_advice"))
                s.intent, s.pending, s.oos = "book", "", None
                s.ev.pop("oos_kind", None)
            elif act == "reject" and ac >= 0.6:
                s.pending = "anything_else"
                return out + pre_say + [self._say("anything_else")]
        # «¿Le doy de alta?» → «sí»: se empieza el alta; con datos nuevos, otro intento de identificarle
        if s.pending == "not_found":
            if (act == "confirm" and ac >= 0.6) or (it == "register" and ic >= 0.7):
                s.intent, s.pending, s.refusal = "register", "", None
            elif act == "reject" and ac >= 0.6:
                s.pending = "anything_else"
                return out + pre_say + [self._say("anything_else")]
            elif p.n("gives_info") >= 0.6:
                s.pending, s.id_tries = "identity", 2
        # tras «¿algo más?», una petición nueva abre una tarea nueva (sin arrastrar las preferencias de la anterior)
        if s.pending == "anything_else" and it in ("book", "reschedule", "cancel", "register") and ic >= 0.7 \
                and act not in ("reject", "end_call") and (s.submitted or s.refusal or s.oos):
            out.append(self._log("new_task", intent=it))
            s.intent, s.pending, s.relation = it, "", None
            s.specialty = s.provider = s.site = s.site_unsure = s.day = s.day_kind = s.part = s.offer = s.target = None
            s.provider_opts, s.rejected, s.asked_plan, s.refusal, s.oos = [], [], False, None, None

        # 6. intención
        if it in ("book", "reschedule", "cancel", "register", "info") and ic >= 0.7:
            if s.intent in (None, "info") or (it != s.intent and ic >= 0.85 and act in ("correct", "provide_info")):
                if s.intent and s.intent != it:
                    out.append(self._log("intent_changed", old=s.intent, new=it))
                    s.offer, s.target = None, None
                s.intent = it

        # 7. absorber datos (aceptaciones, preferencias, identidad, alta…) y componer: prefijo + resultado + siguiente jugada
        self._answered = bool(pre_say)
        body = await self.absorb(text, p, act, ac)
        says = [o for o in body if o.get("kind") == "say"]
        logs = [o for o in body if o.get("kind") != "say"]
        if offering and getattr(self, "_changed", None):
            # había una oferta y el turno la ha cambiado (otro día, franja o sede): lo que el Sistema 2 haya improvisado
            # sobre la «pregunta» sobra, la respuesta es la oferta nueva (sin oferta, «¿qué días pasa consulta allí?»
            # también toca la sede y ahí la respuesta es lo único que hay)
            pre_say = [o for o in pre_say if o.get("act") != "question"]
        if getattr(self, "_stop", False):
            self._stop = False
            return out + logs + pre_say + says
        nxt = await self.advance(reprompt=bool(pre_say))
        return out + logs + pre_say + says + nxt

    def _answered_q(self, p: P, act: str) -> bool:
        return act == "ask_question" and p.n("gives_info") < 0.6 and not (p.c("intent")[0] in ("book", "reschedule", "cancel") and p.c("intent")[1] >= 0.85 and not self.s.intent)

    def offer_violates(self) -> bool:
        """¿Lo que ha pedido en ESTE turno (otro día, otra franja, otra sede) contradice la oferta abierta?
        Repetir lo mismo al aceptar («Monday at nine is fine») no la contradice; «el sábado» sí."""
        s, o = self.s, (self.s.offer or {}).get("slot")
        if not o or not self._changed:
            return False
        dt = parse_slot(o["start_time"])
        if "day" in self._changed and s.day and dt.date().isoformat() != s.day:
            return True
        if "part" in self._changed and s.part and ((s.part in ("morning", "first_thing") and dt.hour >= 14) or (s.part == "afternoon" and dt.hour < 14)):
            return True
        if "site" in self._changed and s.site and o["location_id"] != s.site:
            return True
        return False

    async def absorb(self, text: str, p: P, act: str, ac: float) -> list[dict]:
        s, out, ex = self.s, [], p.ex
        self._changed = set()
        # para quién
        rel, rc = p.c("relation")
        if s.relation is None and s.intent in ("book", "reschedule", "cancel"):
            s.relation = rel if (p.n("third_party") >= 0.6 and rel and rel != "self" and rc >= 0.5) else ("self" if p.n("third_party") < 0.5 else None)
        # pruebas de identidad
        if not s.patient or s.pending.startswith("identity"):
            people = ex.get("people") or []
            want_role = "patient" if s.relation not in (None, "self") else None
            chosen = None
            for person in people:
                person = {k: v for k, v in person.items() if k == "role" or (v and grounded(v, text))}
                if not person.get("first_surname") and not person.get("given_name"):
                    continue
                if self.is_doctor_name(person, text):
                    continue
                if want_role and person.get("role") in ("patient", "other"):
                    chosen = person
                elif not want_role or chosen is None:
                    chosen = chosen or person
            if chosen:
                nm = " ".join(x for x in (chosen.get("given_name"), chosen.get("first_surname"), chosen.get("second_surname")) if x)
                if want_role and chosen.get("role") == "caller":
                    s.ev["caller_name"] = nm
                else:
                    s.ev["name"] = nm
            said_id = ex.get("national_id")
            coded = spoken_id(text)
            if coded and (not said_id or len("".join(c for c in said_id if c.isdigit())) < len("".join(c for c in coded if c.isdigit()))):
                said_id = coded
            if said_id:
                nid, why = normalize_national_id(said_id)
                out.append(self._log("dni", said=said_id, normalized=nid, why=why))
                if nid:
                    s.ev["national_id"] = nid
                    s.ev.pop("bad_id", None)
                else:
                    s.ev["bad_id"] = why
            if ex.get("phone") and len("".join(c for c in ex["phone"] if c.isdigit())) >= 9:
                s.ev["phone"] = ex["phone"]
            if ex.get("date_of_birth"):
                s.ev["dob"] = ex["date_of_birth"]
        # especialidad, profesional, sede, fecha, franja
        sp, spc = p.c("specialty")
        if sp and sp != "none" and spc >= 0.6:
            s.specialty = sp
        cp, cc = p.c("complaint")
        if not s.specialty and cp and cp != "none" and cc >= 0.6 and COMPLAINTS[cp][1]:
            s.specialty = COMPLAINTS[cp][1]
            out.append(self._log("triage", complaint=cp, route=s.specialty))
        pv, pc = p.c("provider")
        said = (ex.get("provider_said") or "").strip()
        if pv and pv not in ("none", "unknown") and pc >= 0.6:
            twins = self.near_twins(pv, said)
            if twins and not s.specialty:
                s.provider_opts = twins
            else:
                s.provider = pv
                s.specialty = s.specialty or self.prov(pv).get("specialty_id")
        elif pv == "unknown" and self.sounds_like(p):
            s.provider = self.sounds_like(p)
            out.append(self._log("provider_by_sound", said=said, provider=s.provider))
        elif pv == "unknown" and pc >= 0.6 and said and self.names_a_doctor(text, said):
            s.provider = "unknown"
            s.ev["provider_said"] = said
        st, stc = p.c("site")
        probs = {k: v for k, v in ((p.raw.get("site") or {}).get("probabilities") or {}).items() if k != "none"}
        best = max(probs.items(), key=lambda kv: kv[1], default=(None, 0.0))
        if s.pending == "which_site" and best[0] and best[1] >= 0.35:
            s.site, s.site_unsure = best[0], None       # contestando a «¿qué sede?»: la más probable
        elif st and st != "none" and stc >= 0.6:
            if st != s.site:
                self._changed.add("site")
            s.site, s.site_unsure = st, None
        elif best[0] and best[1] >= 0.3 and best[0] != s.site and s.pending != "confirm_book" and self.mentions_site(text):
            # suena a una sede pero no está claro («R&L SORE», «it has to be sir»): se preguntará
            s.site_unsure = best[0]
            out.append(self._log("site_unsure", candidate=best[0], p=round(best[1], 2)))
        if s.pending == "which_site" and not s.site:
            s.site_unsure = None                         # «me da igual»: sin sede
        if p.n("gives_address") >= 0.6 and ex.get("address"):
            s.address = ex["address"]
        dk, dc = p.c("date_kind")
        probs = (p.raw.get("date_kind") or {}).get("probabilities", {})
        wk_kinds = ("this_coming", "first_thing", "weekday_afternoon")
        if dk in wk_kinds and dc < 0.55 and sum(probs.get(k, 0) for k in wk_kinds) >= 0.6:
            dc = 0.6   # Jev duda entre formas de decir el mismo día de la semana: el día está claro
        if dk and dk != "none" and dc >= 0.55:
            wd, _ = p.c("weekday")
            day, part = self.resolve_day(dk, wd, ex.get("appointment_date"))
            if greeting_part(text):
                part = None
            if day or dk == "earliest":
                if (day.isoformat() if day else None) != s.day:
                    self._changed.add("day")
                s.day_kind, s.day = dk, day.isoformat() if day else None
                if part:
                    if part != s.part:
                        self._changed.add("part")
                    s.part = part
                out.append(self._log("date", date_kind=dk, weekday=wd, day=s.day, part=s.part))
        pt, ptc = p.c("part")
        if greeting_part(text):
            pt = None      # «Good afternoon» es un saludo, no «por la tarde»
        if pt and pt != "any" and ptc >= 0.6:
            if pt != s.part:
                self._changed.add("part")
            s.part = pt
        # segundo seguro
        ins, ic = p.c("insurer")
        if ins and ins != "none" and ic >= 0.6 and s.pending in ("other_plan", "which_plan") and ins not in s.plans:
            s.plans.append(ins)
            s.offer = None
            out.append(self._log("second_plan", plan=ins))
        if s.pending in ("other_plan", "which_plan") and act == "confirm" and ac >= 0.6 and not (ins and ins != "none" and ic >= 0.6):
            s.pending = "which_plan"
            out.append(self._say("ask_which_plan"))
            self._stop = True
            return out
        # «no, solo ese» (aunque diga algo más, p. ej. una sede): se rechaza por la cobertura, sin volver a buscar
        new_plan = ins and ins != "none" and ic >= 0.6 and ins in s.plans[1:]
        if s.pending == "other_plan" and not new_plan and ((act == "reject" and ac >= 0.6) or act not in ("confirm", "question")):
            out += await self.refuse(s.refusal or "specialty_not_covered")
            self._stop = True
            return out
        # cita concreta
        if s.pending == "which_appt":
            ap, apc = p.c("appt")
            if ap and ap in {a["appointment_id"] for a in s.appts} and apc >= 0.6:
                s.target = ap
            elif ap == "both" and apc >= 0.6 and s.intent == "cancel":
                s.target = ",".join(a["appointment_id"] for a in s.appts)
        # alta: campos
        if s.intent == "register":
            out += self.absorb_register(p)
        # confirmaciones
        if s.pending == "confirm_book" and s.offer:
            accepts = p.n("accepts_offer")
            # la aceptación manda cuando Jev la ve clara («I mean Dr. Sáez, the GP. Yes, please book Monday at 9» sale
            # como «corrección», pero acepta con 0,93); una confirmación tibia vale si además acepta
            accepted = (accepts >= 0.85 or (act == "confirm" and ac >= 0.8) or (act == "confirm" and ac >= 0.6 and accepts >= 0.6)) \
                and not self.offer_violates()          # «sí, resérveme lo del sábado» ante una oferta del lunes: es otra preferencia
            if act == "ask_question" and ac >= 0.6 and accepts < 0.6:
                # una pregunta sobre la oferta («¿el Dr. Sáez es el de cabecera?»): ya contestada en el prefijo; se repregunta corto
                if not getattr(self, "_answered", False):
                    out.append(self._text(await self.answer_question(f"{text}\n(The receptionist had just offered: {s.last_agent})"), "question"))
                out.append(self._text({"en": "Shall I book it for you?", "es": "¿Se la reservo?", "ca": "Li reservo?"}.get(s.lang, "Shall I book it for you?"), "offer_again"))
                self._stop = True
            elif accepted:
                out += await self.commit_offer()     # «sí, y ¿por qué entrada?»: la pregunta ya va en el prefijo
                self._stop = True
            elif act == "reject" or (act == "correct" and accepts < 0.5) or self.offer_violates() or (accepts < 0.3 and (p.c("date_kind")[0] not in (None, "none") or p.c("part")[0] not in (None, "any"))):
                sl = s.offer["slot"]
                s.rejected = getattr(s, "rejected", []) + [(sl["provider_id"], sl["start_time"])]
                s.offer = None
                out.append(self._log("offer_rejected", act=act))
        elif s.pending == "confirm_cancel" and s.target:
            if act == "confirm" and ac >= 0.8:
                for aid in s.target.split(","):
                    out += await self.submit("cancel", {"appointment_id": aid})
                s.appts = [a for a in s.appts if a["appointment_id"] not in s.target.split(",")]
                s.target = None
                s.pending = "anything_else"
                out.append(self._say("cancelled"))
                self._stop = True
            elif act in ("reject", "correct"):
                s.target = None
        elif s.pending in ("reg_confirm", "reg_fix"):
            if act == "confirm" and ac >= 0.8 and s.pending == "reg_confirm":
                out += await self.submit("register", self.register_body())
                s.pending = "anything_else"
                out.append(self._text({"en": "You're registered with us now. We'll have your details on file whenever you need us. Is there anything else?",
                                       "es": "Ya está dado de alta. ¿Algo más?", "ca": "Ja està donat d’alta. Alguna cosa més?"}[s.lang], "reg_done"))
                self._stop = True
            else:
                # «No, el correo es…»: se corrige lo que diga y se vuelve a leer; si no dice qué, se pregunta
                changed = self.apply_reg_corrections(p)
                out.append(self._log("reg_correction", changed=changed))
                if not changed:
                    s.pending = "reg_fix"
                    out.append(self._say("reg_fix"))
                    self._stop = True
        return out

    def apply_reg_corrections(self, p: P) -> list[str]:
        s, ex, r = self.s, p.ex, self.s.reg
        flat = "".join(ch for ch in fold(p.text) if ch.isalnum())
        heard = lambda v: bool(v) and "".join(ch for ch in fold(v) if ch.isalnum()) in flat   # dicho o deletreado
        changed = []
        people = [x for x in ex.get("people") or [] if x.get("role") in ("caller", "both", "patient", None)]
        if people:
            for k in ("given_name", "first_surname", "second_surname"):
                v = (people[0].get(k) or "").strip()
                if v and v.lower() != "null" and heard(v) and v != r.get(k):
                    r[k] = v
                    changed.append(k)
        if ex.get("national_id"):
            nid, _ = normalize_national_id(ex["national_id"])
            if nid and nid != r.get("national_id"):
                r["national_id"] = nid
                changed.append("national_id")
        ph = "".join(c for c in (ex.get("phone") or "") if c.isdigit())
        if len(ph) >= 9 and ph != r.get("phone"):
            r["phone"] = ph
            changed.append("phone")
        if ex.get("email") and "@" in ex["email"]:
            em = self.email_like_name(ex["email"].strip().lower().replace(" ", ""))
            if em != r.get("email"):
                r["email"] = em
                changed.append("email")
        if ex.get("date_of_birth") and ex["date_of_birth"] != r.get("date_of_birth"):
            r["date_of_birth"] = ex["date_of_birth"]
            changed.append("date_of_birth")
        ins, ic = p.c("insurer")
        if ins and ins != "none" and ic >= 0.6 and ins != r.get("insurer"):
            r["insurer"] = ins
            changed.append("insurer")
        return changed

    # ------------------------------------------------------------ siguiente paso

    async def advance(self, reprompt: bool = False) -> list[dict]:
        s = self.s
        if s.pending == "anything_else" and (not reprompt or s.submitted):
            return [self._say("anything_else")]
        if s.intent is None or s.intent == "info":
            s.pending = "need"
            return [self._say("ask_need")]
        if s.intent == "register":
            return self.next_register()
        # identificar al paciente
        if not s.patient:
            r = await self.identify()
            if r is not None:
                return r
        out = []
        if s.intent == "book":
            return out + await self.book_flow()
        if s.intent in ("cancel", "reschedule"):
            return out + await self.change_flow()
        return [self._say("anything_else")]

    # ------------------------------------------------------------ identidad

    def is_doctor_name(self, person: dict, text: str) -> bool:
        """«Doctor Saez», «Doctora Iglesias»: es un profesional, no quien llama ni el paciente."""
        surs = {fold(p["name"]).replace(".", " ").split()[-1] for p in (self.catalog or {}).get("providers", [])}
        parts = [fold(person.get(k) or "") for k in ("given_name", "first_surname", "second_surname")]
        t = fold(text)
        for part in parts:
            if part and part.split()[-1] in surs and re_findall(r"(doctor|doctora|dr|dra|doctor a)\.?\s+" + re_sub(r"\W", ".", part), t):
                return True
        return False

    def pick_by_name(self, ms: list[dict], name: str | None):
        if not ms:
            return None, 0.0, 0.0
        if not name and self.s.ev.get("id_text"):
            # sin extracción: ¿qué ficha aparece en lo que ha dicho? (proporción de su nombre y apellidos presentes)
            scored = sorted(((leer.name_score(self.s.ev["id_text"], f"{m['given_name']} {m['first_surname']} {m['second_surname']}"), m) for m in ms),
                            key=lambda x: -x[0])
            if scored[0][0] >= 0.34:
                return scored[0][1], scored[0][0], (scored[1][0] if len(scored) > 1 else 0.0)
        if not name:
            return (ms[0], 1.0, 0.0) if len(ms) == 1 else (None, 0.0, 0.0)
        scored = sorted(((name_sim(name, f"{m['given_name']} {m['first_surname']} {m['second_surname']}"), m) for m in ms), key=lambda x: -x[0])
        return scored[0][1], scored[0][0], (scored[1][0] if len(scored) > 1 else 0.0)

    async def identify(self) -> list[dict] | None:
        """Primero el dato exacto (DNI, fecha de nacimiento, teléfono dicho o la línea); el nombre confirma con
        tolerancia, porque el transcriptor a veces lo destroza («Pau Vidal Serra» → «Pablo Vilar Sáenz»)."""
        s, ev = self.s, self.s.ev
        other = s.relation not in (None, "self")
        if ev.get("bad_id"):
            why = ev.pop("bad_id")
            # «…my DNI is 947» y el turno se corta a mitad del dictado: no es una letra que no cuadra, es un número a
            # medias. Si el nombre y otro dato (la línea, la fecha, el teléfono) ya bastan, se sigue sin pedir nada.
            partial = "dígitos" in why
            if partial and ev.get("name") and (s.line_matches or ev.get("dob") or ev.get("phone")):
                self._log("identity", step=f"DNI a medias ({why}): se prueba con el nombre y lo demás")
            else:
                s.id_tries += 1
                s.pending = "identity"
                if s.id_tries <= 2:
                    return [self._log("identity", step="DNI a medias" if partial else "letra del DNI no cuadra"),
                            self._say("id_incomplete" if partial else "id_letter_bad")]
        name = ev.get("name")
        probes = []
        if ev.get("national_id"):
            probes.append(("dni", {"national_id": ev["national_id"]}))
        if ev.get("dob"):
            probes.append(("nacimiento", {"date_of_birth": ev["dob"]}))
        if ev.get("phone"):
            probes.append(("teléfono", {"phone": ev["phone"]}))
        if s.from_number:
            probes.append(("línea", {"phone": s.from_number}))
        if not name and not probes:
            s.pending = "identity"
            return [self._say("ask_patient_identity" if other else "ask_identity_self")]
        found, why, missed_dni, ambiguous = None, "", False, False
        for label, q in probes:
            ms = await API.directory(**q)
            if other and ev.get("caller_name"):
                ms = [m for m in ms if name_sim(ev["caller_name"], f"{m['given_name']} {m['first_surname']} {m['second_surname']}") < 0.8] or ms
            best, score, second = self.pick_by_name(ms, name)
            if not ms:
                missed_dni = missed_dni or label == "dni"
                continue
            if label == "línea" and not name:
                continue   # la línea es solo una pista: sin nombre no identifica
            if best and ((len(ms) == 1 and (score >= 0.35 or label == "dni")) or (score >= 0.55 and score - second >= 0.15)):
                found, why = best, f"{label} + nombre {score:.2f}"
                break
            if len(ms) > 1:
                ambiguous = True
        if not found and name and not probes:
            s.id_tries += 1
            if s.id_tries > 3:
                s.refusal, s.pending = "patient_not_found", "not_found"
                return [self.gate("identidad", False, "sin segundo dato tras tres intentos"), self._say("not_found")]
            ms = await API.directory(name=name)
            s.pending = "identity_second"
            if len(ms) > 1:
                return [self._log("identity", step="homónimos", n=len(ms)), self._say("ask_dob", name=name)]
            return [self._log("identity", step="falta segundo dato", n=len(ms)), self._say("ask_second_id")]
        if not found:
            s.id_tries += 1
            if missed_dni and s.id_tries <= 2:
                ev.pop("national_id", None)
                s.pending = "identity"
                return [self._log("identity", step="sin coincidencia con el DNI"), self._say("repeat_id")]
            if not name:
                s.pending = "identity"
                return [self._log("identity", step="falta el nombre"), self._say("ask_patient_identity" if other else "ask_identity_self")]
            if s.id_tries <= 2 and not (ev.get("dob") and ev.get("national_id")):
                s.pending = "identity_second"
                key = "ask_dob" if ambiguous else "ask_second_id"
                return [self._log("identity", step="sin coincidencia" if not ambiguous else "varias fichas"), self._say(key, name=name)]
            s.refusal = "patient_not_found"
            s.pending = "not_found"
            return [self.gate("identidad", False, "no está en el directorio"), self._say("not_found")]
        s.patient = found
        s.plans = [found["insurer"]] + [x for x in s.plans if x != found["insurer"]]
        s.pending = "identified"
        first = found["given_name"]
        g = self.gate("identidad", True, f"{found['patient_id']} {first} {found['first_surname']} ({why}) · {found.get('note', '')[:80]}")
        self._greeted = [g, self._say("identified_other" if other else "identified", first=first)]
        return None

    # ------------------------------------------------------------ reservar

    async def book_flow(self) -> list[dict]:
        s = self.s
        out = getattr(self, "_greeted", [])
        self._greeted = []
        if s.provider_opts and not s.provider:
            a, b = (self.prov(x)["name"] for x in s.provider_opts[:2])
            s.pending = "provider_which"
            return out + [self._say("ask_which_provider", a=f"{a} ({self.spec_name(self.prov(s.provider_opts[0])['specialty_id'])})",
                                    b=f"{b} ({self.spec_name(self.prov(s.provider_opts[1])['specialty_id'])})")]
        if s.provider == "unknown":
            s.refusal = "provider_not_found"
            s.pending = "anything_else"
            out += await self.submit("no-action", {"reason": "provider_not_found"})
            return out + [self._text({"en": f"I'm sorry, we don't have a {s.ev.get('provider_said', 'doctor by that name')} at the clinic. Is there anything else I can help with?",
                                      "es": "Lo siento, no tenemos a ese profesional en la clínica. ¿Algo más?",
                                      "ca": "Ho sento, no tenim aquest professional a la clínica. Alguna cosa més?"}[s.lang], "provider_not_found")]
        if not s.specialty and s.pending == "specialty":
            # ya se preguntó y no lo sabe («whichever can see me first»): médico de familia, que deriva si hace falta
            s.specialty = "general_practice"
            out.append(self._text({"en": "Then I'll look for a GP, who can refer you on if needed.", "es": "Entonces le busco médico de familia, que le derivará si hace falta.",
                                   "ca": "Doncs li busco metge de família, que el derivarà si cal."}.get(s.lang, "Then I'll look for a GP."), "default_gp"))
        if not s.specialty:
            s.pending = "specialty"
            return out + [self._say("ask_specialty")]
        if s.address and not s.site:
            site = await self.nearest_site(s.address, s.specialty)
            if site:
                s.site = site
                out.append(self._log("nearest_site", address=s.address, site=site))
        if s.offer and s.pending == "confirm_book":
            # se repite SOLO la oferta (antes se repetía la última intervención entera, saludo incluido)
            sl = s.offer["slot"]
            return out + [self._say("reoffer", when=S.when(s.lang, parse_slot(sl["start_time"])),
                                    provider=self.prov(sl["provider_id"])["name"], site=self.site_name(sl["location_id"]))]
        res = await self.search()
        out += res
        return out

    def near_twins(self, pv: str, said: str) -> list[str]:
        """Sáez/Sáenz, Iglesias/Iglesia: si el nombre dicho encaja con dos, hay que preguntar."""
        c = self.catalog or {}
        sur = lambda n: fold(n.replace("Dr.", "").replace("Dra.", "").replace("D.", "")).split()[-1]
        target = sur(self.prov(pv)["name"])
        said_f = fold(said).replace("doctor", "").replace("dra", "").replace("dr", "").split()
        said_sur = said_f[-1] if said_f else target
        twins = [p["id"] for p in c.get("providers", []) if p["id"] != pv and _close(sur(p["name"]), target)]
        if not twins:
            return []
        exact = [x for x in [pv] + twins if sur(self.prov(x)["name"]) == said_sur]
        if len(exact) == 1 and said_sur:
            return []  # lo dijo claramente
        return [pv] + twins

    async def search(self, exclude_appt: str | None = None) -> list[dict]:
        s = self.s
        if getattr(s, "site_unsure", None) and s.pending != "which_site":
            s.pending = "which_site"
            names = [l["name"] for l in (await self.cat())["locations"]]
            lst = ", ".join(names[:-1]) + (" or " if s.lang == "en" else " o ") + names[-1] if len(names) > 1 else names[0]
            return [self._text({"en": f"Which of our sites would suit you: {lst}?", "es": f"¿Qué sede le va mejor: {lst}?",
                                "ca": f"Quina seu li va millor: {lst}?"}.get(s.lang, f"Which of our sites would suit you: {lst}?"), "which_site")]
        tomorrow = s.t0.date() + timedelta(days=1)
        cal = (await self.cat())["calendar"]
        last = date.fromisoformat(cal["ends"])
        first = date.fromisoformat(s.day) if s.day else tomorrow
        first = max(first, tomorrow)
        pid = s.patient["patient_id"] if s.patient else None
        kw = dict(specialty_id=s.specialty, patient_id=pid, insurers=s.plans or None)
        if s.provider:
            kw["provider_id"] = s.provider
        if s.site:
            kw["location_id"] = s.site
        if s.provider and not getattr(self, "_leave_ok", False):
            lv = self.prov(s.provider).get("leave")
            if lv and date.fromisoformat(lv["end"]) >= first:
                return await self.leave_fallback(first, last, kw, lv)
        a = await API.availability_span(first, last, **kw)
        slots = self.filter_slots(a["slots"], first)
        out = [self._log("availability", query={k: v for k, v in kw.items() if v}, first=first.isoformat(), found=len(slots), blocked=a["blocked"],
                         type=(a.get("appointment_type") or {}).get("id"))]
        if slots:
            return out + self.make_offer(slots, a, first)
        # nada: ¿por qué?
        blocked = {b["provider_id"]: b["restriction"] for b in a["blocked"]}
        if s.provider and blocked.get(s.provider) in ("provider_on_leave", "provider_not_in_network", "location_hours"):
            why = blocked[s.provider]
            alt_kw = dict(kw)
            alt_kw.pop("provider_id", None)
            if why != "provider_not_in_network" and s.site:
                alt_kw["location_id"] = s.site
            alt = await API.availability_span(first, last, **alt_kw)
            alt_slots = [x for x in self.filter_slots(alt["slots"], first) if x["provider_id"] != s.provider]
            out.append(self._log("fallback", reason=why, found=len(alt_slots)))
            if alt_slots:
                s.refusal = why
                reason_txt = {"provider_on_leave": f"{self.prov(s.provider)['name']} is on leave at the moment.",
                              "provider_not_in_network": f"{self.prov(s.provider)['name']} doesn't take your insurance.",
                              "location_hours": f"{self.prov(s.provider)['name']} isn't at {self.site_name(s.site)} then."}[why]
                return out + self.make_offer(alt_slots, alt, first, fallback=reason_txt)
            return out + await self.refuse(why)
        reasons = [r for r in blocked.values()]
        if reasons:
            coverage = [r for r in reasons if r in ("specialty_not_covered", "location_not_covered", "insurer_referral_required", "allowance_exhausted", "provider_not_in_network")]
            if coverage and not s.asked_plan and len(set(reasons)) >= 1 and all(r in coverage for r in reasons):
                s.asked_plan, s.refusal, s.pending = True, coverage[0], "other_plan"
                plan = next((x["name"] for x in (await self.cat())["plans"] if x["id"] == (s.plans or ["?"])[0]), (s.plans or ["your"])[0])
                what = {"specialty_not_covered": self.spec_name(s.specialty).lower(), "location_not_covered": f"appointments at {self.site_name(s.site) if s.site else 'that site'}",
                        "insurer_referral_required": f"{self.spec_name(s.specialty).lower()} without a referral", "allowance_exhausted": "any more visits this year",
                        "provider_not_in_network": f"visits with {self.prov(s.provider)['name'] if s.provider else 'that doctor'}"}[coverage[0]]
                return out + [self._say("ask_other_plan", plan=plan, what=what)]
            prio = ["not_eligible_age", "referral_required", "specialty_not_covered", "insurer_referral_required", "allowance_exhausted",
                    "location_not_covered", "provider_not_in_network", "provider_on_leave", "location_hours", "type_not_offered", "patient_history"]
            why = next((r for r in prio if r in reasons), reasons[0])
            return out + await self.refuse(why)
        # agenda llena en lo pedido
        if s.day or s.part or s.site:
            return out + await self.refuse("no_availability")
        return out + await self.refuse("no_availability")

    async def leave_fallback(self, first: date, last: date, kw: dict, lv: dict) -> list[dict]:
        """Quien pide a un profesional de baja: se le mueve a otro de la misma especialidad y la misma sede."""
        s = self.s
        alt_kw = {k: v for k, v in kw.items() if k != "provider_id"}
        alt = await API.availability_span(first, last, **alt_kw)
        alt_slots = [x for x in self.filter_slots(alt["slots"], first) if x["provider_id"] != s.provider]
        out = [self._log("fallback", reason="provider_on_leave", until=lv["end"], found=len(alt_slots))]
        if not alt_slots:
            return out + await self.refuse("provider_on_leave")
        s.refusal = "provider_on_leave"
        end = date.fromisoformat(lv["end"])
        reason = {"en": f"{self.prov(s.provider)['name']} is on leave until the {S._ord(end.day)} of {S.MO['en'][end.month - 1]}.",
                  "es": f"{self.prov(s.provider)['name']} está de baja hasta el {end.day} de {S.MO['es'][end.month - 1]}.",
                  "ca": f"{self.prov(s.provider)['name']} està de baixa fins al {end.day} de {S.MO['ca'][end.month - 1]}."}[s.lang]
        return out + self.make_offer(alt_slots, alt, first, fallback=reason)

    def filter_slots(self, slots: list[dict], first: date) -> list[dict]:
        s = self.s
        out = []
        rejected = set(getattr(s, "rejected", []))
        for x in slots:
            dt = parse_slot(x["start_time"])
            if dt.date() < first or dt.date() <= s.t0.date():
                continue
            if (x["provider_id"], x["start_time"]) in rejected:
                continue                                  # lo ya rechazado no se vuelve a ofrecer
            if s.part in ("morning", "first_thing") and dt.hour >= 14:
                continue
            if s.part == "afternoon" and dt.hour < 14:
                continue
            if s.lang_req in ("ca", "en") and s.lang_req not in self.prov(x["provider_id"]).get("languages", []):
                continue
            out.append(x)
        return out

    def make_offer(self, slots: list[dict], a: dict, first: date, fallback: str | None = None) -> list[dict]:
        s = self.s
        # el día pedido (si lo hay) o el siguiente abierto que cumpla lo demás
        earliest = parse_slot(slots[0]["start_time"])
        tied = [x for x in slots if parse_slot(x["start_time"]) == earliest]
        # repartir la carga: entre empatados, el profesional con más huecos libres
        free = {}
        for x in slots:
            free[x["provider_id"]] = free.get(x["provider_id"], 0) + 1
        best = max(tied, key=lambda x: free.get(x["provider_id"], 0))
        plan = next((pl for pl in s.plans if pl in best.get("payable_with", [])), (best.get("payable_with") or s.plans or ["privado"])[0])
        s.offer = {"slot": best, "policy_id": plan, "type": best["appointment_type_id"]}
        s.pending = "confirm_book"
        dt = parse_slot(best["start_time"])
        cons = []
        if s.part in ("morning", "first_thing"):
            cons.append({"en": "in the morning ", "es": "por la mañana ", "ca": "al matí "}[s.lang])
        elif s.part == "afternoon":
            cons.append({"en": "in the afternoon ", "es": "por la tarde ", "ca": "a la tarda "}[s.lang])
        prov = best["provider_name"]
        site = self.site_name(best["location_id"])
        log = self._log("offer", slot=best["start_time"], provider=best["provider_id"], site=best["location_id"], type=best["appointment_type_id"],
                        policy=plan, tied=len(tied))
        if fallback:
            return [log, self._say("offer_fallback", reason=fallback, specialty=self.spec_name(s.specialty).lower(), site=site, when=S.when(s.lang, dt), provider=prov)]
        if s.day and dt.date().isoformat() != s.day:
            asked = date.fromisoformat(s.day)
            closed = asked in [date.fromisoformat(d) for d in (self.catalog or {}).get("calendar", {}).get("closure_days", [])] or asked.weekday() == 6
            where = "" if closed else ({"en": f" at {self.site_name(s.site)}", "es": f" en {self.site_name(s.site)}", "ca": f" a {self.site_name(s.site)}"}[s.lang] if s.site else "")
            if closed or s.site:
                return [log, self._say("offer_closed", day=S.day_name(s.lang, asked), where=where, constraint="".join(cons), when=S.when(s.lang, dt), provider=prov, site=site)]
        return [log, self._say("offer", constraint="".join(cons), when=S.when(s.lang, dt), provider=prov, site=site)]

    async def commit_offer(self) -> list[dict]:
        s = self.s
        o = s.offer
        x = o["slot"]
        if s.intent == "reschedule" and s.target:
            body = {"appointment_id": s.target, "provider_id": x["provider_id"], "location_id": x["location_id"], "slot": x["start_time"], "policy_id": o["policy_id"]}
            out = await self.submit("reschedule", body)
            key = "rescheduled"
        else:
            body = {"patient_id": s.patient["patient_id"], "provider_id": x["provider_id"], "location_id": x["location_id"],
                    "appointment_type_id": x["appointment_type_id"], "slot": x["start_time"], "policy_id": o["policy_id"]}
            out = await self.submit("book", body)
            key = "booked"
        s.offer, s.pending = None, "anything_else"
        return out + [self._say(key, when=S.when(s.lang, parse_slot(x["start_time"])), provider=x["provider_name"], site=self.site_name(x["location_id"]))]

    async def refuse(self, why: str) -> list[dict]:
        s = self.s
        s.refusal, s.pending = why, "anything_else"
        text = {
            "not_eligible_age": "that specialty isn't available for the patient's age.",
            "referral_required": "that specialty needs a referral, and there isn't one on the record.",
            "provider_not_in_network": "that provider doesn't accept the insurance on file.",
            "specialty_not_covered": "your insurance doesn't cover that specialty.",
            "location_not_covered": "your insurance doesn't cover that site.",
            "insurer_referral_required": "your insurer needs a referral for that specialty.",
            "allowance_exhausted": "your plan has used up its visits for this year.",
            "provider_on_leave": "that provider is on leave.",
            "location_hours": "that provider isn't at that site at those times.",
            "type_not_offered": "that kind of appointment isn't offered there.",
            "patient_history": "the patient's history doesn't allow that appointment.",
            "no_availability": "there's nothing free that matches in the diary.",
            "clinic_closed": "the clinic is closed then.",
            "patient_not_found": "I can't find the patient's record.",
        }.get(why, "that isn't possible.")
        out = [self.gate("reglas", False, why)]
        out += await self.submit("no-action", {"reason": why})
        return out + [self._say("refuse", why=text)]

    # ------------------------------------------------------------ cambiar y anular

    def appt_desc(self, a: dict) -> str:
        dt = parse_slot(a["start_time"])
        return f"{self.spec_name(self.prov(a['provider_id']).get('specialty_id', '')).lower()} with {self.prov(a['provider_id'])['name']} on {S.when('en', dt)}"

    async def change_flow(self) -> list[dict]:
        s = self.s
        out = getattr(self, "_greeted", [])
        self._greeted = []
        if not s.appts:
            s.appts = await API.appointments(s.patient["patient_id"], "upcoming")
            out.append(self._log("appointments", n=len(s.appts)))
            if not s.appts:
                s.pending = "anything_else"
                return out + [self._say("no_upcoming")]
        if not s.target:
            if len(s.appts) == 1:
                s.target = s.appts[0]["appointment_id"]
            else:
                s.pending = "which_appt"
                opts = [self.appt_desc(a) for a in s.appts]
                return out + [self._say("which_appt", options=", and ".join(opts))]
        if "," in s.target and s.intent == "cancel":
            s.pending = "confirm_cancel"
            descs = [self.appt_desc(x) for x in s.appts if x["appointment_id"] in s.target.split(",")]
            return out + [self._text({"en": f"So that's both: the {' and the '.join(descs)}. Shall I cancel them?",
                                      "es": f"Entonces las dos: {' y '.join(descs)}. ¿Las anulo?", "ca": f"Doncs les dues: {' i '.join(descs)}. Les anul·lo?"}[s.lang],
                                     "confirm_cancel")]
        a = next(x for x in s.appts if x["appointment_id"] == s.target)
        if s.intent == "cancel":
            s.pending = "confirm_cancel"
            return out + [self._say("confirm_cancel", appt=self.appt_desc(a))]
        # cambiar: misma especialidad (y mismo profesional si no dice otra cosa)
        s.specialty = s.specialty or self.prov(a["provider_id"]).get("specialty_id")
        if not (s.day or s.part or s.day_kind):
            s.pending = "when"
            return out + [self._text({"en": f"Sure, that's the {self.appt_desc(a)}. When would you like to move it to?",
                                      "es": "Claro. ¿A cuándo quiere cambiarla?", "ca": "És clar. A quan la vol canviar?"}[s.lang], "ask_when")]
        return out + await self.search(exclude_appt=a["appointment_id"])

    # ------------------------------------------------------------ alta (problema 4)

    REG_FIELDS = ["given_name", "surnames", "national_id", "date_of_birth", "phone", "email", "insurer"]

    # ------------------------------------------------------------ cifras: segunda opinión

    def names_a_doctor(self, text: str, said: str) -> bool:
        """¿De verdad nombra a un médico? Hace falta «doctor/doctora/Dr./Dra.» en lo dicho, y que no suene a sede:
        «a GP at O'Rainel Central» es Arenal Centro mal oído, no un profesional que no existe."""
        t = fold(text)
        doctor = bool(re_findall(r"\b(dr|dra|doctor|doctora|doc|metge|metgessa|medico|medica)\b", t))
        return doctor and not self.mentions_site(said)

    def mentions_site(self, text: str) -> bool:
        """¿Dice algo que suene a una sede («R&L SORE», «Arenal, sir»)? Sin esto, una pregunta cualquiera
        (la entrada, la planta) dispersaba la probabilidad de sede y se preguntaba «¿qué sede?» sin motivo."""
        toks = [t for l in (self.catalog or {}).get("locations", []) for t in fold(l["name"]).split()] or ["arenal", "centro", "norte", "sur"]
        ws = "".join(ch if ch.isalnum() else " " for ch in fold(text)).split()
        return any(difflib.SequenceMatcher(None, w, t).ratio() >= 0.6 for w in ws if w not in ("sure", "so", "sorry") for t in toks)

    def sounds_like(self, p: P) -> str | None:
        """Un médico «desconocido» que suena como uno de la clínica DE LA ESPECIALIDAD pedida («Dra. Glacius» de
        dermatología → Iglesias). Sin especialidad que lo respalde, no se adivina: puede no existir de verdad."""
        snd, sc = p.c("provider_sound")
        if snd and snd != "none" and sc >= 0.5 and self.s.specialty and self.prov(snd).get("specialty_id") == self.s.specialty:
            return snd
        return None

    def digits_kind(self, text: str) -> str | None:
        """¿Esperamos cifras en este turno? Teléfono en el alta; DNI/NIE (7-8 cifras) en el alta o al identificar."""
        s = self.s
        if s.pending == "reg_phone":
            return "phone"
        if s.pending == "reg_national_id":
            return "id" if 5 <= (sum(c.isdigit() for c in text) or len(self.spoken_digits(text))) <= 8 else None
        # al identificar, solo una tira seguida de cifras (una fecha, «14th of September 1978», no es un DNI)
        runs = [sum(c.isdigit() for c in r) for r in re_findall(r"\d[\d .-]*\d", text)]
        if (s.pending.startswith("identity") or not s.patient) and runs and 6 <= max(runs) <= 8:
            return "id"
        return None

    @staticmethod
    def spoken_digits(text: str) -> str:
        return "".join(DIGITS.get(w, w if w.isdigit() else "") for w in re_findall(r"[a-z]+|\d+", fold(text)))

    def digits_ok(self, kind: str, text: str, ex: dict) -> bool:
        if kind == "phone":
            return len("".join(c for c in (ex.get("phone") or "") if c.isdigit()) or self.spoken_digits(text)) >= 9
        cands = [x for x in (ex.get("national_id"), spoken_id(text)) if x]
        return any(normalize_national_id(x)[0] for x in cands)

    async def check_digits(self, text: str, p: P) -> list[dict]:
        """El transcriptor en directo comprime las cifras repetidas («dos dos dos» → «2»). Si lo que esperamos es un
        teléfono o un DNI y no valida, se vuelve a transcribir el audio del turno con Flash-Lite (≈1 s, solo entonces)."""
        kind = self.digits_kind(text)
        if not kind or self.digits_ok(kind, text, p.ex):
            return []
        alt, ms = await self.second_opinion()
        if not alt or not self.digits_ok(kind, alt, {}):
            return [self._log("second_opinion", field=kind, text=alt, ms=ms, used=False)]
        p.ex = dict(p.ex)
        if kind == "phone":
            p.ex["phone"] = self.spoken_digits(alt)
        else:
            p.ex["national_id"] = next(x for x in (spoken_id(alt),) if x and normalize_national_id(x)[0])
        p.text = alt
        return [self._log("second_opinion", field=kind, text=alt, ms=ms, used=True)]

    async def second_opinion(self) -> tuple[str | None, int]:
        self.so_used = True
        audio = getattr(self, "audio", b"")
        if len(audio) < 16000 * 2 * 0.3:
            return None, 0
        import io
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
            w.writeframes(audio)
        t0 = time.perf_counter()
        try:
            r = await asyncio.wait_for(GEMINI.aio.models.generate_content(
                model=EXTRACT_MODEL,
                contents=[types.Part.from_bytes(data=buf.getvalue(), mime_type="audio/wav"),
                          "Transcribe this phone-call audio exactly. Write every digit as a numeral and keep repeated digits "
                          "(e.g. 'two two two' -> 222). Keep spelled letters. No commentary."],
                config=types.GenerateContentConfig(temperature=0, automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))),
                timeout=3.5)
            return (r.text or "").strip() or None, round((time.perf_counter() - t0) * 1000)
        except Exception:  # noqa: BLE001
            return None, round((time.perf_counter() - t0) * 1000)

    def absorb_register(self, p: P) -> list[dict]:
        """Cada dato del alta se toma cuando es el que se ha preguntado (o si aún no se ha empezado y lo dice todo
        de una vez). Así un apellido no sale del DNI ni el seguro de otra frase."""
        s, ex, out = self.s, p.ex, []
        ask = s.pending[4:] if s.pending.startswith("reg_") else None
        fresh = ask is None
        text = p.text
        for person in ex.get("people") or []:
            person = {k: v for k, v in person.items() if k == "role" or (v and grounded(v, text))}
            if person.get("role") == "other":
                continue
            if person.get("given_name") and (ask == "given_name" or (fresh and not s.reg.get("given_name"))):
                s.reg["given_name"] = person["given_name"]
            if ask in ("surnames", "given_name") or fresh:
                if person.get("first_surname"):
                    s.reg["first_surname"] = person["first_surname"]
                if person.get("second_surname"):
                    s.reg["second_surname"] = person["second_surname"]
        if ask == "given_name" and not s.reg.get("given_name") and not ex.get("people"):
            g = re_sub(r"(?i)^(?:(?:yes|yeah|sure|ok|okay|hi|hello|so|well|um|uh)[,.]?\s+)*(?:my (?:first |given )?name is|it's|it is|i'm|i am|me llamo|soy|mi nombre es|em dic|el meu nom és)\s+", "",
                       text.strip().rstrip(".!")) if text else ""
            w = [x for x in re_findall(r"[A-Za-zÀ-ÿ'-]+", g) if fold(x) not in FILLER]
            if 1 <= len(w) <= 2:
                s.reg["given_name"] = " ".join(x[:1].upper() + x[1:] for x in w)
        if ask == "surnames" and not (s.reg.get("first_surname") and s.reg.get("second_surname")):
            # el reconocedor a veces lo da en minúscula («castro vidal»): valen las palabras que no son de relleno
            ws = [w.strip(".,?!") for w in text.split() if w.strip(".,?!").isalpha() and fold(w.strip(".,?!")) not in FILLER]
            if 2 <= len(ws) <= 3 and "?" not in text:
                s.reg["first_surname"], s.reg["second_surname"] = (w[:1].upper() + w[1:] for w in ws[-2:])
        if ask == "national_id" or fresh or ex.get("national_id"):
            said_id = ex.get("national_id")
            coded = spoken_id(text)
            if coded and (not said_id or len("".join(c for c in said_id if c.isdigit())) < len("".join(c for c in coded if c.isdigit()))):
                said_id = coded
            # solo cuando es lo preguntado: un teléfono de 9 cifras («633445566») también parece un DNI
            if said_id and (ask == "national_id" or (fresh and not s.reg.get("national_id"))):
                nid, why = normalize_national_id(said_id)
                out.append(self._log("dni", said=said_id, normalized=nid, why=why))
                if nid:
                    s.reg["national_id"] = nid
                    s.reg.pop("_bad_tries", None)
                else:
                    s.reg["_bad_tries"] = s.reg.get("_bad_tries", 0) + 1
                    raw = "".join(c for c in said_id.upper() if c.isalnum())
                    if s.reg["_bad_tries"] >= 2 and "letra" in why:
                        fixed, _ = normalize_national_id(raw[:-1])     # las cifras mandan; la lectura final lo confirma
                        if fixed:
                            s.reg["national_id"] = fixed
                            out.append(self._log("dni", derived=fixed))
                            return out
                    s.reg.pop("national_id", None)
                    s.reg["_bad_id"] = why
        if ex.get("date_of_birth") and (ask == "date_of_birth" or fresh or "born" in fold(text) or "naci" in fold(text)):
            s.reg["date_of_birth"] = ex["date_of_birth"]
        if ask == "phone":
            digits = "".join(c for c in (ex.get("phone") or "") if c.isdigit()) or "".join(DIGITS.get(w, w if w.isdigit() else "") for w in re_findall(r"[a-z]+|\d+", fold(text)))
            if len(digits) >= 9:
                s.reg["phone"] = digits
            else:
                s.reg["_phone_tries"] = s.reg.get("_phone_tries", 0) + 1
                if s.reg["_phone_tries"] >= 3 and len(digits) >= 6:
                    s.reg["phone"] = digits          # sin bucles: la lectura final lo deja corregir
        if ex.get("email") and (ask == "email" or "@" in ex["email"]):
            s.reg["email"] = self.email_like_name(ex["email"].strip().lower().replace(" ", ""))
        ins, ic = p.c("insurer")
        if ins and ins != "none" and ic >= 0.6 and ask == "insurer":
            s.reg["insurer"] = ins
        return out

    def email_like_name(self, email: str) -> str:
        """«alina.castro@…» de una Elena Castro: lo que se oyó mal es el nombre, que ya sabemos cómo se escribe.
        Solo con una palabra MUY parecida al nombre o a un apellido; la lectura final lo confirma."""
        if "@" not in email:
            return email
        local, dom = email.split("@", 1)
        names = [fold(self.s.reg.get(k) or "").replace(" ", "") for k in ("given_name", "first_surname", "second_surname")]
        parts = [x for x in re_findall(r"[a-z]+|[^a-z]+", local)]
        for i, w in enumerate(parts):
            if not w.isalpha() or len(w) < 4:
                continue
            for n in names:
                if n and w != n and len(w) == len(n) and difflib.SequenceMatcher(None, w, n).ratio() >= 0.6:
                    parts[i] = n
                    break
        return "".join(parts) + "@" + dom

    def next_register(self) -> list[dict]:
        s, r = self.s, self.s.reg
        why = r.pop("_bad_id", None)
        if why:
            s.pending = "reg_national_id"
            return [self._say("id_incomplete" if "dígitos" in why else "id_letter_bad")]
        r = {k: v for k, v in r.items()}
        missing = [f for f in self.REG_FIELDS if (f != "surnames" and not r.get(f)) or (f == "surnames" and not (r.get("first_surname") and r.get("second_surname")))]
        if missing:
            f = missing[0]
            if f == "phone" and s.pending == "reg_phone" and s.reg.get("_phone_tries", 0) >= 1:
                return [self._say("reg_phone_groups")]
            s.pending = f"reg_{f}"
            intro = [self._say("reg_intro")] if not getattr(self, "_reg_started", False) else []
            self._reg_started = True
            return intro + [self._text(S.reg_ask(f, s.lang), f"reg_{f}")]
        s.pending = "reg_confirm"
        summ = (f"{r['given_name']} {r['first_surname']} {r['second_surname']}, ID {S.spell(r['national_id'])}, born {r['date_of_birth']}, "
                f"phone {S.spell(''.join(c for c in r['phone'] if c.isdigit()))}, email {r['email'].replace('.', ' dot ').replace('@', ' at ')}, insurer {r['insurer']}")
        return [self._say("reg_readback", summary=summ)]

    def register_body(self) -> dict:
        r = {k: v for k, v in self.s.reg.items() if not k.startswith("_")}
        return {k: r[k] for k in ("given_name", "first_surname", "second_surname", "national_id", "date_of_birth", "phone", "email", "insurer")}

    # ------------------------------------------------------------ fechas (problema 5), en código

    def resolve_day(self, kind: str, weekday: str | None, iso: str | None) -> tuple[date | None, str | None]:
        d0 = self.s.t0.date()
        nxt = lambda w: d0 + timedelta(days=((w - d0.weekday() - 1) % 7) + 1)   # primer <w> estrictamente después de hoy
        if kind == "tomorrow":
            return d0 + timedelta(days=1), None
        if kind == "day_after_tomorrow":
            return d0 + timedelta(days=2), None
        if kind == "week_from_today":
            return d0 + timedelta(days=7), None
        if kind == "fortnight":
            return d0 + timedelta(days=14), None
        if kind == "saturday_morning":
            return nxt(5), "morning"
        if kind in ("this_coming", "first_thing", "weekday_afternoon") and weekday in WEEKDAYS:
            return nxt(WEEKDAYS.index(weekday)), {"first_thing": "first_thing", "weekday_afternoon": "afternoon"}.get(kind)
        if kind == "specific_date" and iso:
            try:
                return date.fromisoformat(iso), None
            except ValueError:
                return None, None
        return None, None

    # ------------------------------------------------------------ sede más cercana (problema 15)

    async def nearest_site(self, address: str, specialty: str | None) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=6, headers={"User-Agent": "prosper-jev-demo/0.1 (jlsf2005@gmail.com)"}) as c:
                r = await c.get("https://nominatim.openstreetmap.org/search", params={"q": address + ", Madrid, Spain", "format": "json", "limit": 1})
                hit = r.json()[0]
            lat, lon = float(hit["lat"]), float(hit["lon"])
        except Exception:  # noqa: BLE001
            return None
        cat = await self.cat()
        serving = {loc for p in cat["providers"] if not specialty or p["specialty_id"] == specialty for loc in _prov_locs(p, cat)}
        best = sorted((_hav(lat, lon, l["latitude"], l["longitude"]), l["id"]) for l in cat["locations"] if l["id"] in serving or not serving)
        return best[0][1] if best else None

    # ------------------------------------------------------------ Sistema 2: preguntas sobre la clínica

    async def answer(self, text: str, p: P) -> dict | None:
        """Banco de respuestas: lo que está en el catálogo se contesta con plantilla (0 ms, ya en la caché de voz).
        Solo lo que no está en el banco va al Sistema 2 (Flash-Lite con la guardia de Jev)."""
        s, cat = self.s, await self.cat()
        topic, tc = p.c("question_topic")
        lang = s.lang
        off = (s.offer or {}).get("slot") or {}
        site = p.c("site")[0] if p.c("site")[1] >= 0.5 and p.c("site")[0] not in (None, "none") else (s.site or off.get("location_id"))
        pv = p.c("provider")[0] if p.c("provider")[1] >= 0.5 and p.c("provider")[0] not in (None, "none", "unknown") else None
        pv = pv or (p.c("provider_sound")[0] if p.c("provider_sound")[1] >= 0.5 and p.c("provider_sound")[0] not in (None, "none") else None)
        pv = pv or s.provider if s.provider not in (None, "unknown") else pv
        pv = pv or off.get("provider_id")
        loc = next((l for l in cat["locations"] if l["id"] == site), None)
        prov = next((x for x in cat["providers"] if x["id"] == pv), None)
        T = lambda en, es, ca: self._text({"en": en, "es": es, "ca": ca}.get(lang, en), f"answer_{topic}")
        if tc < 0.5 or topic in (None, "none"):
            topic = "other"
        if topic == "site_hours" and loc:
            h = hours_text(loc, lang)
            return T(f"{loc['name']} is open {h}.", f"{loc['name']} abre {h}.", f"{loc['name']} obre {h}.")
        if topic == "site_address" and loc:
            return T(f"{loc['name']} is at {loc['address']}.", f"{loc['name']} está en {loc['address']}.", f"{loc['name']} és a {loc['address']}.")
        if topic == "weekend":
            sat = [l["name"] for l in cat["locations"] if any(str(d.get("weekday", "")).lower() == "saturday" for d in l.get("hours", []))]
            if sat:
                h = "; ".join(f"{l['name']} {hours_text(l, lang, only='saturday')}" for l in cat["locations"] if l["name"] in sat)
                return T(f"On Saturdays only {' and '.join(sat)} is open: {h}.", f"Los sábados solo abre {' y '.join(sat)}: {h}.",
                         f"Els dissabtes només obre {' i '.join(sat)}: {h}.")
            return T("None of our sites open at weekends.", "Ninguna sede abre los fines de semana.", "Cap seu obre el cap de setmana.")
        if topic == "provider_specialty" and prov:
            gp = prov["specialty_id"] == "general_practice"
            sp = self.spec_name(prov["specialty_id"]).lower()
            es, ca = SPEC_L["es"].get(prov["specialty_id"], sp), SPEC_L["ca"].get(prov["specialty_id"], sp)
            return T(f"{prov['name']} is one of our {'GPs' if gp else sp + ' doctors'}.",
                     f"{prov['name']} es {'médico de familia' if gp else 'de ' + es}.", f"{prov['name']} és {'metge de família' if gp else 'de ' + ca}.")
        if topic == "provider_where" and prov:
            where = " and ".join(prov.get("location_names") or [])
            return T(f"{prov['name']} sees patients at {where}.", f"{prov['name']} pasa consulta en {where}.", f"{prov['name']} passa consulta a {where}.")
        if topic == "languages":
            wl = p.c("wants_language")[0]
            if prov and wl in (None, "none"):
                names = {"es": "Spanish", "en": "English", "ca": "Catalan"}
                ls = " and ".join(names.get(x, x) for x in prov.get("languages", []))
                return T(f"{prov['name']} speaks {ls}.", f"{prov['name']} habla {ls}.", f"{prov['name']} parla {ls}.")
            if wl in ("ca", "es", "en"):
                who = [x["name"] for x in cat["providers"] if wl in x.get("languages", [])]
                return T(f"{', '.join(who[:4])} {'speak' if len(who) > 1 else 'speaks'} {'Catalan' if wl == 'ca' else 'Spanish' if wl == 'es' else 'English'}.",
                         f"Hablan {'catalán' if wl == 'ca' else 'español' if wl == 'es' else 'inglés'}: {', '.join(who[:4])}.",
                         f"Parlen {'català' if wl == 'ca' else 'castellà' if wl == 'es' else 'anglès'}: {', '.join(who[:4])}.")
        if topic == "duration" and s.offer:
            t = next((x for x in cat["appointment_types"] if x["id"] == s.offer.get("type")), None)
            if t:
                return T(f"It's a {t['duration_minutes']}-minute appointment.", f"Es una cita de {t['duration_minutes']} minutos.",
                         f"És una visita de {t['duration_minutes']} minuts.")
        if topic == "practical":
            return T("I'm afraid I don't have that information here, but the front desk at the site will help you on the day.",
                     "Eso no lo tengo aquí, pero en recepción de la sede le ayudarán ese día.",
                     "Això no ho tinc aquí, però a la recepció de la seu l’ajudaran aquell dia.")
        ans = await self.answer_question(text if not s.offer else f"{text}\n(The receptionist had just offered: {s.last_agent})")
        return self._text(ans, "question")

    async def answer_question(self, question: str) -> str:
        cat = await self.cat()
        facts = {"locations": [{"name": l["name"], "address": l["address"], "hours": l["hours"], "providers": l["provider_names"]} for l in cat["locations"]],
                 "providers": [{"name": p["name"], "specialty": p["specialty_name"], "languages": p["languages"], "sites": p["location_names"],
                                "on_leave": p.get("leave")} for p in cat["providers"]],
                 "closure_days": cat["calendar"]["closure_days"]}
        # el cruce especialidad × sede ya hecho: Flash-Lite, cruzándolo solo, decía que solo Centro y Norte ven niños
        # (el Dr. Ocaña también pasa consulta en Sur) o ponía a un pediatra entre los médicos de familia
        by_spec: dict = {}
        for pr in cat["providers"]:
            for site in pr["location_names"]:
                by_spec.setdefault(pr["specialty_name"], {}).setdefault(site, []).append(pr["name"])
        facts["specialty_by_site"] = by_spec
        cfg = types.GenerateContentConfig(
            system_instruction=("You are a clinic receptionist on the phone. Answer in one or two short spoken sentences, in "
                                + {"en": "English", "es": "Spanish", "ca": "Catalan"}[self.s.lang] +
                                ", using ONLY these facts. Never guess: if the facts don't say it, say you can't confirm. No lists or markdown. "
                                "Never say or hint whether a named person is or is not a patient, is on file, or has an appointment, and "
                                "never repeat that name back; only if the caller asks for another person's details or appointments, say you "
                                "can't share information about other patients (otherwise do not mention privacy at all). "
                                "Do not greet or say goodbye.\nFACTS:\n" + json.dumps(facts, ensure_ascii=False)),
            temperature=0, max_output_tokens=200, automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        try:
            r = await asyncio.wait_for(GEMINI.aio.models.generate_content(model=EXTRACT_MODEL, contents=question, config=cfg), timeout=6)
            ans = (r.text or "").strip()
            g = await JEV.ask({"facts": facts, "reply": ans}, {"unsupported": noul("Does `reply` state anything about the clinic that is not supported by `facts`?"),
                                                               "advice": noul("Does `reply` give medical advice?")})
            if g["answers"]["unsupported"]["noul"] < 0.5 and g["answers"]["advice"]["noul"] < 0.5 and ans:
                return ans
        except Exception:  # noqa: BLE001
            pass
        return {"en": "I'm sorry, I can't confirm that over the phone.", "es": "Lo siento, eso no se lo puedo confirmar.", "ca": "Ho sento, això no l’hi puc confirmar."}[self.s.lang]

    # ------------------------------------------------------------ declarar el resultado

    async def submit(self, action: str, body: dict) -> list[dict]:
        s = self.s
        full = {"call_id": s.call_id, **body}
        if self._dry:
            return [self._log("would_write", action=action)]
        if any(x["route"] == action and x["body"] == full for x in s.submitted):
            return []
        entry = {"route": action, "body": full, "action": action.upper().replace("-", "_"), "t": round(time.time() - s.started, 2)}
        try:
            r = await API.submit(action, full) if SUBMIT else {"status": "skipped"}
            entry["status"] = r.get("status")
        except ApiError as e:
            entry["status"], entry["error"] = e.status, e.body[:200]
        s.submitted.append(entry)
        return [self.gate("envío", entry.get("status") in (200, 409, "skipped"), f"{action} {json.dumps(body, ensure_ascii=False)[:160]} → {entry.get('status')}")]

    async def goodbye(self) -> list[dict]:
        self.s.ended = True
        out = await self.finalize()
        return out + [self._say("goodbye"), {"kind": "end"}]

    async def finalize(self) -> list[dict]:
        """Nunca se cuelga sin declarar algo: el silencio siempre es un error."""
        s = self.s
        if s.submitted or self._dry:
            return []
        if s.offer and s.pending == "confirm_book":
            return await self.commit_offer()   # si se cortó con una oferta aceptable sobre la mesa
        if s.intent == "register" and all(self.s.reg.get(k) for k in ("given_name", "first_surname", "second_surname", "national_id", "date_of_birth", "phone", "email", "insurer")):
            return await self.submit("register", self.register_body())
        # el motivo de fondo (cobertura, agenda…) manda sobre un «fuera de ámbito» posterior
        reason = s.refusal or s.oos or ("patient_not_found" if s.pending == "not_found" else "out_of_scope")
        return await self.submit("no-action", {"reason": reason})

    async def report(self) -> dict:
        if self.s.lang_locked and not self._dry:
            remember_line_language(self.s.from_number, self.s.lang)
        s = self.s
        return {"call_id": s.call_id, "language": getattr(s, "speak_lang", None) or s.lang, "patient_id": (s.patient or {}).get("patient_id"),
                "outcome": ", ".join(x["action"] for x in s.submitted) or "none", "reason": s.refusal or s.oos,
                "actions": [{**x["body"], "action": x["action"], "status": x.get("status")} for x in s.submitted],
                "caller_relation": s.relation or "self", "duration_s": round(time.time() - s.started, 1), "trace": s.trace,
                "api_calls": len(API.log)}


def _close(a: str, b: str) -> bool:
    if a == b:
        return False
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    lo, hi = sorted((a, b), key=len)
    return any(hi[:i] + hi[i + 1:] == lo for i in range(len(hi)))


def _prov_locs(p: dict, cat: dict) -> list[str]:
    names = {l["name"]: l["id"] for l in cat["locations"]}
    return [names.get(n, n) for n in p.get("location_names", [])] or [s["location_id"] for s in p.get("schedules", [])]


def _hav(la1, lo1, la2, lo2) -> float:
    r = math.radians
    dlat, dlon = r(la2 - la1), r(lo2 - lo1)
    h = math.sin(dlat / 2) ** 2 + math.cos(r(la1)) * math.cos(r(la2)) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))
