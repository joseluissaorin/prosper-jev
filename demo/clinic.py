"""Clínica simulada: fichas, médicos, reglas y agenda.

Todo lo que es cálculo (edades, fechas, plazos, huecos, reglas numéricas) vive aquí,
en código. Jev nunca hace estas cuentas.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

TODAY = date.fromisoformat(os.environ["TODAY"]) if os.environ.get("TODAY") else date.today()
MAX_DAYS_AHEAD = 60
HOLD_SECONDS = 180

SITES = {
    "chamberi": {"name": "Chamberí", "address": "calle de Santa Engracia, 45"},
    "retiro": {"name": "Retiro", "address": "calle de O'Donnell, 22"},
}

# Servicios que ofrece la clínica. Los que no están aquí se rechazan por regla.
SERVICES = {
    "medicina_general": {"es": "medicina general", "ca": "medicina general", "gl": "medicina xeral", "eu": "medikuntza orokorra", "en": "general practice"},
    "pediatria": {"es": "pediatría", "ca": "pediatria", "gl": "pediatría", "eu": "pediatria", "en": "paediatrics"},
    "cardiologia": {"es": "cardiología", "ca": "cardiologia", "gl": "cardioloxía", "eu": "kardiologia", "en": "cardiology"},
    "traumatologia": {"es": "traumatología", "ca": "traumatologia", "gl": "traumatoloxía", "eu": "traumatologia", "en": "orthopaedics"},
    "ginecologia": {"es": "ginecología", "ca": "ginecologia", "gl": "xinecoloxía", "eu": "ginekologia", "en": "gynaecology"},
}
NOT_OFFERED = {
    "dermatologia": "Skin, moles, rashes, acne, dermatology",
    "psiquiatria": "Mental health, psychiatry, psychology, anxiety or depression treatment",
    "odontologia": "Teeth, dentist, dental care",
    "otro_no_ofrecido": "Any other specialty or service not listed (e.g. ophthalmology, physiotherapy, vaccines for travel)",
}

@dataclass
class Doctor:
    id: str
    name: str          # tal cual se dice: «la doctora Iglesias»
    short: str         # «Dra. Iglesias»
    title: str         # dra | dr
    surname: str
    service: str
    site: str
    full: bool = False  # agenda completamente llena

DOCTORS = {d.id: d for d in [
    Doctor("d_iglesias", "Carmen Iglesias", "Dra. Iglesias", "dra", "Iglesias", "medicina_general", "chamberi"),
    Doctor("d_perez", "Luis Pérez", "Dr. Pérez", "dr", "Pérez", "medicina_general", "retiro"),
    Doctor("d_navarro", "Elena Navarro", "Dra. Navarro", "dra", "Navarro", "pediatria", "chamberi"),
    Doctor("d_morales", "Andrés Morales", "Dr. Morales", "dr", "Morales", "cardiologia", "retiro"),
    Doctor("d_ortega", "Javier Ortega", "Dr. Ortega", "dr", "Ortega", "traumatologia", "chamberi", full=True),
    Doctor("d_sanz", "Lucía Sanz", "Dra. Sanz", "dra", "Sanz", "ginecologia", "retiro"),
]}

@dataclass
class Patient:
    id: str
    given: str
    surname1: str
    surname2: str
    dob: date
    phone: str
    gp_referral_cardiology: bool = False
    guardians: list[str] = field(default_factory=list)   # quién puede gestionar sus citas
    relatives: list[str] = field(default_factory=list)   # familiares autorizados (hijos de mayores)

    @property
    def full_name(self) -> str:
        return f"{self.given} {self.surname1} {self.surname2}".strip()

    @property
    def first(self) -> str:
        return self.given.split()[0]

    def age(self, on: date = TODAY) -> int:
        return on.year - self.dob.year - ((on.month, on.day) < (self.dob.month, self.dob.day))

PATIENTS: dict[str, Patient] = {p.id: p for p in [
    Patient("p001", "María", "García", "López", date(1984, 3, 12), "600114471"),
    Patient("p002", "María", "García", "López", date(1991, 11, 2), "611230915"),
    Patient("p003", "María José", "García", "Lozano", date(1984, 3, 21), "622904471"),
    Patient("p004", "Mario", "García", "López", date(1975, 7, 30), "633412208"),
    Patient("p010", "Antonio", "Ruiz", "Medina", date(1944, 5, 3), "644551020", gp_referral_cardiology=True, relatives=["p011"]),
    Patient("p011", "Laura", "Ruiz", "Gómez", date(1978, 9, 14), "655667788"),
    Patient("p020", "Pau", "Vidal", "Serra", date(2019, 4, 10), "666123450", guardians=["p021"]),
    Patient("p021", "Marta", "Serra", "Puig", date(1985, 1, 22), "666123450"),
    Patient("p030", "Iker", "Etxeberria", "Goikoetxea", date(1990, 6, 8), "677889900"),
    Patient("p031", "Uxía", "Castro", "Rey", date(1988, 2, 17), "688990011"),
    Patient("p040", "Ana", "López", "Fernández", date(1995, 10, 5), "699001122"),
    Patient("p050", "Carmen", "López", "Díaz", date(1960, 12, 1), "600998877"),
]}

@dataclass
class Appointment:
    id: str
    patient_id: str
    doctor_id: str
    start: datetime
    status: str = "booked"   # booked | cancelled

    @property
    def service(self) -> str:
        return DOCTORS[self.doctor_id].service

APPOINTMENTS: dict[str, Appointment] = {}
_BUSY: set[tuple[str, datetime]] = set()   # huecos ocupados por otras citas (relleno realista)
_HOLDS: dict[tuple[str, datetime], tuple[str, float]] = {}
_LOCK = asyncio.Lock()
_seq = 100


def _next_weekday(d: date, weekday: int, min_days: int = 1) -> date:
    d = d + timedelta(days=min_days)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def day_slots(d: date) -> list[datetime]:
    if d.weekday() >= 5:
        return []
    times = [(9, 0), (9, 30), (10, 0), (10, 30), (11, 0), (11, 30), (12, 0), (12, 30), (13, 0), (13, 30),
             (16, 0), (16, 30), (17, 0), (17, 30), (18, 0), (18, 30)]
    return [datetime(d.year, d.month, d.day, h, m) for h, m in times]


def _seed() -> None:
    global _seq
    rng = random.Random(7)
    for doc in DOCTORS.values():
        for i in range(1, MAX_DAYS_AHEAD + 1):
            for s in day_slots(TODAY + timedelta(days=i)):
                if doc.full or rng.random() < (0.55 if i < 10 else 0.35):
                    _BUSY.add((doc.id, s))
    tue = _next_weekday(TODAY, 1)
    thu = _next_weekday(TODAY, 3)
    seeds = [
        ("a001", "p010", "d_morales", datetime.combine(tue, datetime.min.time()).replace(hour=10)),
        ("a002", "p040", "d_iglesias", datetime.combine(tue, datetime.min.time()).replace(hour=10)),
        ("a003", "p040", "d_sanz", datetime.combine(thu, datetime.min.time()).replace(hour=17)),
        ("a004", "p001", "d_sanz", datetime.combine(tue + timedelta(days=14), datetime.min.time()).replace(hour=12)),
    ]
    for aid, pid, did, start in seeds:
        APPOINTMENTS[aid] = Appointment(aid, pid, did, start)
        _BUSY.discard((did, start))
    _seq = 100


_seed()


def reset() -> None:
    """Vuelve la clínica al estado inicial (para las pruebas)."""
    APPOINTMENTS.clear(); _BUSY.clear(); _HOLDS.clear()
    for pid in [p for p in PATIENTS if p.startswith("p9")]:
        del PATIENTS[pid]
    _seed()

# ---------------------------------------------------------------- búsqueda de pacientes

def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _tokens(s: str) -> list[str]:
    return [t for t in "".join(c if c.isalnum() else " " for c in fold(s)).split() if len(t) > 1]


def name_candidates(text: str) -> list[str]:
    """Fichas cuyo nombre aparece (al menos dos piezas) en lo dicho. Tolera errores leves del STT."""
    toks = set(_tokens(text))
    if not toks:
        return []
    out = []
    for p in PATIENTS.values():
        parts = _tokens(p.given) + _tokens(p.surname1) + _tokens(p.surname2)
        hits = sum(1 for part in parts if part in toks or any(_close(part, t) for t in toks))
        if hits >= 2:
            out.append((hits, p.id))
    out.sort(reverse=True)
    return [pid for _, pid in out]


def _close(a: str, b: str) -> bool:
    if len(a) < 5 or abs(len(a) - len(b)) > 1:
        return False
    diff = sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))
    return diff <= 1


def phone_digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


def distinguishing_field(pids: list[str]) -> str:
    """Qué dato separa mejor a las candidatas que quedan."""
    ps = [PATIENTS[p] for p in pids]
    options = {
        "dob": len({p.dob for p in ps}),
        "phone": len({p.phone[-4:] for p in ps}),
        "surname2": len({fold(p.surname2) for p in ps}),
    }
    return max(options, key=lambda k: (options[k], k == "dob"))


def new_patient(given: str, s1: str, s2: str, dob: date, phone: str) -> Patient:
    pid = f"p{900 + len([p for p in PATIENTS if p.startswith('p9')])}"
    p = Patient(pid, given, s1, s2, dob, phone)
    PATIENTS[pid] = p
    return p

# ---------------------------------------------------------------- reglas

RULES = {
    "not_offered": "The clinic does not offer this specialty or service.",
    "pediatrics_age": "Paediatrics only sees patients under 14.",
    "cardiology_referral": "Cardiology requires a referral from a GP on file.",
    "max_days_ahead": f"Appointments cannot be booked more than {MAX_DAYS_AHEAD} days ahead.",
    "one_per_specialty": "A patient may hold at most one future appointment per specialty.",
    "not_authorised": "Only the patient, a parent/guardian of a minor, or an authorised relative can manage appointments.",
}


def check_booking(patient: Patient, service: str, when: date | None = None, exclude_appt: str | None = None) -> str | None:
    """Devuelve el id de la regla que lo impide, o None. Todo en código."""
    if service in NOT_OFFERED or service not in SERVICES:
        return "not_offered"
    if service == "pediatria" and patient.age() >= 14:
        return "pediatrics_age"
    if service == "cardiologia" and not patient.gp_referral_cardiology:
        return "cardiology_referral"
    if when and (when - TODAY).days > MAX_DAYS_AHEAD:
        return "max_days_ahead"
    for a in future_appointments(patient.id):
        if a.service == service and a.id != exclude_appt:
            return "one_per_specialty"
    return None


def future_appointments(pid: str) -> list[Appointment]:
    now = datetime.combine(TODAY, datetime.min.time())
    return sorted([a for a in APPOINTMENTS.values() if a.patient_id == pid and a.status == "booked" and a.start > now],
                  key=lambda a: a.start)

# ---------------------------------------------------------------- huecos

PART_OF_DAY = {
    "first_thing": (9, 0, 9, 59),
    "morning": (9, 0, 13, 59),
    "late_morning": (11, 30, 13, 59),
    "midday": (12, 0, 14, 59),
    "afternoon": (16, 0, 18, 59),
    "evening": (18, 0, 18, 59),
    "any": (0, 0, 23, 59),
}


def _taken(doc_id: str, s: datetime, call_id: str | None) -> bool:
    if (doc_id, s) in _BUSY:
        return True
    if any(a.doctor_id == doc_id and a.start == s and a.status == "booked" for a in APPOINTMENTS.values()):
        return True
    h = _HOLDS.get((doc_id, s))
    return bool(h and h[1] > time.time() and h[0] != call_id)


def find_slots(service: str, *, doctor: str | None = None, site: str | None = None, day: date | None = None,
               part: str = "any", limit: int = 3, call_id: str | None = None) -> tuple[list[tuple[str, datetime]], bool]:
    """Huecos libres. Si el día pedido no tiene, devuelve los más cercanos y exact=False."""
    docs = [d for d in DOCTORS.values() if d.service == service and (not doctor or d.id == doctor) and (not site or d.site == site)]
    h0, m0, h1, m1 = PART_OF_DAY.get(part, PART_OF_DAY["any"])

    def ok_time(s: datetime) -> bool:
        return (h0, m0) <= (s.hour, s.minute) <= (h1, m1)

    def scan(days):
        found = []
        for d in days:
            for s in day_slots(d):
                if not ok_time(s):
                    continue
                for doc in docs:
                    if not _taken(doc.id, s, call_id):
                        found.append((doc.id, s))
            if part == "first_thing" and found:
                break
        return found

    if day:
        exact = scan([day])
        if exact:
            return _spread(exact, limit, part), True
        near = scan([day + timedelta(days=i) for i in range(1, MAX_DAYS_AHEAD) if (day + timedelta(days=i) - TODAY).days <= MAX_DAYS_AHEAD])
        return _spread(near, limit, part), False
    allf = scan([TODAY + timedelta(days=i) for i in range(1, MAX_DAYS_AHEAD + 1)])
    return _spread(allf, limit, part), True


def _spread(found, limit, part):
    """Primeros huecos, sin ofrecer tres seguidos del mismo día y médico."""
    found.sort(key=lambda x: x[1])
    if part == "first_thing":
        return found[:1]
    out, seen_days = [], {}
    for doc, s in found:
        k = (doc, s.date())
        if seen_days.get(k, 0) >= 2:
            continue
        seen_days[k] = seen_days.get(k, 0) + 1
        out.append((doc, s))
        if len(out) == limit:
            break
    return out

# ---------------------------------------------------------------- escritura en dos fases

async def hold(doc_id: str, s: datetime, call_id: str) -> bool:
    async with _LOCK:
        if _taken(doc_id, s, call_id):
            return False
        for k in [k for k, v in _HOLDS.items() if v[0] == call_id]:
            del _HOLDS[k]
        _HOLDS[(doc_id, s)] = (call_id, time.time() + HOLD_SECONDS)
        return True


OFFER_SECONDS = 45


def soft_hold(slots: list[tuple[str, datetime]], call_id: str) -> None:
    """Aparta un momento los huecos ofrecidos, para que otra llamada simultánea oiga opciones distintas."""
    now = time.time()
    for k in [k for k, v in _HOLDS.items() if v[0] == call_id and v[1] - now <= OFFER_SECONDS]:
        del _HOLDS[k]
    for k in slots:
        if not _taken(k[0], k[1], call_id):
            _HOLDS[k] = (call_id, now + OFFER_SECONDS)


async def release_holds(call_id: str) -> None:
    async with _LOCK:
        for k in [k for k, v in _HOLDS.items() if v[0] == call_id]:
            del _HOLDS[k]


async def commit_booking(pid: str, doc_id: str, s: datetime, call_id: str, idem: str) -> Appointment | None:
    global _seq
    async with _LOCK:
        for a in APPOINTMENTS.values():
            if getattr(a, "_idem", None) == idem:
                return a
        h = _HOLDS.get((doc_id, s))
        if not h or h[0] != call_id or h[1] < time.time():
            return None
        _seq += 1
        a = Appointment(f"a{_seq}", pid, doc_id, s)
        a._idem = idem  # type: ignore[attr-defined]
        APPOINTMENTS[a.id] = a
        del _HOLDS[(doc_id, s)]
        return a


async def commit_move(appt_id: str, doc_id: str, s: datetime, call_id: str) -> Appointment | None:
    async with _LOCK:
        a = APPOINTMENTS.get(appt_id)
        h = _HOLDS.get((doc_id, s))
        if not a or a.status != "booked" or not h or h[0] != call_id:
            return None
        a.doctor_id, a.start = doc_id, s
        del _HOLDS[(doc_id, s)]
        return a


async def commit_cancel(appt_id: str) -> Appointment | None:
    async with _LOCK:
        a = APPOINTMENTS.get(appt_id)
        if not a or a.status != "booked":
            return None
        a.status = "cancelled"
        return a

# ---------------------------------------------------------------- información general (para el Sistema 2)

FAQ = {
    "hours": "Monday to Friday, 9:00 to 14:00 and 16:00 to 19:00. Closed on weekends and public holidays.",
    "sites": {k: f"{v['name']}: {v['address']}, Madrid" for k, v in SITES.items()},
    "parking": "Chamberí has no parking; there is a public car park (Santa Engracia) 200 m away, not free. Retiro has 6 free parking spaces for patients, first come first served.",
    "public_transport": "Chamberí: metro Iglesia (line 1). Retiro: metro O'Donnell (line 6).",
    "what_to_bring": "ID card (DNI/NIE), health insurance card, and any previous reports.",
    "insurance": "The clinic works with private patients and the main insurers; the exact coverage must be checked with the insurer.",
    "prices": "Prices are not given over the phone; the front desk can send the price list by email.",
    "results": "Test results are given in person by the doctor or through the patient portal, never over the phone.",
}
