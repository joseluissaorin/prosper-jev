"""Clínica falsa con la misma API que la de Prosper, para probar sin la clave del equipo.

Sembrada con lo que cuenta su documentación: tres sedes (Centro, Norte, Sur; solo Centro abre el sábado,
Sur cierra el viernes a mediodía), seis especialidades, doce profesionales (Requena de baja, Sáez/Sáenz,
Iglesias/Iglesia, D. Álvaro Cid), diez planes, once tipos de cita, festivo el 12 de octubre, homónimos,
ids que difieren en un dígito, segundos seguros ocultos y el vocabulario de restricciones.

    ../.venv/bin/uvicorn fake_api:app --port 8770
"""
from __future__ import annotations

import random
import unicodedata
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

MAD = ZoneInfo("Europe/Madrid")
CAL_START, CAL_END = date(2026, 9, 7), date(2026, 10, 16)
CLOSURES = [date(2026, 10, 12)]
LET = "TRWAGMYFPDXBNJZSQVHLCKE"


def dni(n: int) -> str:
    return f"{n:08d}{LET[n % 23]}"


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))


SITES = {
    "centro": {"name": "Arenal Centro", "address": "Calle de Atocha 34, 28012 Madrid", "lat": 40.4125, "lon": -3.7010,
               "hours": {0: ("08:00", "20:00"), 1: ("08:00", "20:00"), 2: ("08:00", "20:00"), 3: ("08:00", "20:00"), 4: ("08:00", "20:00"), 5: ("09:00", "14:00")}},
    "norte": {"name": "Arenal Norte", "address": "Calle de Bravo Murillo 300, 28020 Madrid", "lat": 40.4610, "lon": -3.6975,
              "hours": {0: ("08:00", "20:00"), 1: ("08:00", "20:00"), 2: ("08:00", "20:00"), 3: ("08:00", "20:00"), 4: ("08:00", "20:00")}},
    "sur": {"name": "Arenal Sur", "address": "Calle de Madrid 20, 28901 Getafe", "lat": 40.3070, "lon": -3.7320,
            "hours": {0: ("08:00", "20:00"), 1: ("08:00", "20:00"), 2: ("08:00", "20:00"), 3: ("08:00", "20:00"), 4: ("08:00", "14:00")}},
}
SPECIALTIES = {
    "general_practice": {"name": "General practice", "min": 168, "max": None, "referral": False},
    "paediatrics": {"name": "Paediatrics", "min": 0, "max": 167, "referral": False},
    "dermatology": {"name": "Dermatology", "min": 0, "max": None, "referral": False},
    "orthopaedics": {"name": "Orthopaedics", "min": 0, "max": None, "referral": False},
    "gynaecology": {"name": "Gynaecology", "min": 168, "max": None, "referral": False},
    "physiotherapy": {"name": "Physiotherapy", "min": 0, "max": None, "referral": True},
}
# (id, nombre, especialidad, idiomas, {sede: días}, rechaza planes, baja)
WD = [0, 1, 2, 3, 4]
PROVIDERS = [
    ("PR01", "Dr. Pablo Requena", "general_practice", ["es", "en"], {"centro": WD, "norte": [1, 3]}, [], ("2026-09-14", "2026-09-30", "sick leave")),
    ("PR02", "Dra. Elena Ortiz", "general_practice", ["es", "en"], {"centro": WD + [5]}, [], None),
    ("PR03", "Dr. Andrés Sáez", "general_practice", ["es", "en"], {"norte": [0, 1, 2], "sur": [3, 4]}, [], None),
    ("PR04", "Dra. Lucía Sáenz", "paediatrics", ["es", "en"], {"centro": [0, 2, 4], "sur": [1, 3]}, [], None),
    ("PR05", "Dr. Marc Puig", "paediatrics", ["es", "ca", "en"], {"norte": WD}, [], None),
    ("PR06", "Dra. Carmen Iglesias", "dermatology", ["es", "en"], {"centro": WD}, ["dkv"], None),
    ("PR07", "Dr. Jordi Vilar", "dermatology", ["es", "ca"], {"norte": WD}, [], None),
    ("PR08", "Dr. Tomás Iglesia", "orthopaedics", ["es", "en"], {"sur": WD, "centro": [5]}, [], None),
    ("PR09", "Dra. Núria Ferrer", "orthopaedics", ["es", "ca", "en"], {"norte": WD}, [], None),
    ("PR10", "Dra. Isabel Morán", "gynaecology", ["es", "en"], {"centro": WD}, [], None),
    ("PR11", "D. Álvaro Cid", "physiotherapy", ["es"], {"sur": WD}, [], None),
    ("PR12", "Dra. Montse Roca", "general_practice", ["es", "ca"], {"sur": [0, 1, 2, 3]}, [], None),
]
PLANS = {
    "sanitas": {"name": "Sanitas", "no_spec": [], "no_site": []},
    "adeslas": {"name": "Adeslas", "no_spec": ["gynaecology"], "no_site": []},
    "dkv": {"name": "DKV", "no_spec": [], "no_site": []},
    "asisa": {"name": "ASISA", "no_spec": [], "no_site": ["sur"]},
    "mapfre": {"name": "Mapfre", "no_spec": [], "no_site": [], "referral_for": ["dermatology"]},
    "caser": {"name": "Caser", "no_spec": [], "no_site": [], "cap": 3},
    "cigna": {"name": "Cigna", "no_spec": ["physiotherapy"], "no_site": []},
    "axa": {"name": "AXA", "no_spec": [], "no_site": ["norte"]},
    "nueva_mutua": {"name": "Nueva Mutua", "no_spec": ["dermatology"], "no_site": []},
    "privado": {"name": "Privado", "no_spec": [], "no_site": []},
}
TYPES = {
    "first_visit": ("First visit", 30, "new", None), "review": ("Review", 15, "returning", None),
    "paediatric_first_visit": ("Paediatric first visit", 30, "new", "paediatrics"), "paediatric_review": ("Paediatric review", 20, "returning", "paediatrics"),
    "dermatology_first_visit": ("Dermatology first visit", 30, "new", "dermatology"), "dermatology_review": ("Dermatology review", 15, "returning", "dermatology"),
    "orthopaedic_first_visit": ("Orthopaedic first visit", 30, "new", "orthopaedics"), "orthopaedic_review": ("Orthopaedic review", 20, "returning", "orthopaedics"),
    "gynaecology_review": ("Gynaecology review", 30, "returning", "gynaecology"),
    "physio_assessment": ("Physiotherapy assessment", 45, "new", "physiotherapy"), "physio_session": ("Physiotherapy session", 30, "returning", "physiotherapy"),
}
RESTRICTIONS = ["not_eligible_age", "referral_required", "provider_not_in_network", "specialty_not_covered", "location_not_covered",
                "insurer_referral_required", "allowance_exhausted", "provider_on_leave", "location_hours", "type_not_offered", "patient_history"]

rng = random.Random(42)
PATIENTS: dict[str, dict] = {}
SECOND_PLAN: dict[str, str] = {}
USED: dict[str, int] = {}


def _p(pid, given, s1, s2, dob, phone, sex, visited, ins, refs=(), note="", second=None, used=0, nid=None):
    PATIENTS[pid] = {"patient_id": pid, "given_name": given, "first_surname": s1, "second_surname": s2,
                     "national_id": nid or dni(rng.randint(10_000_000, 79_999_999)), "date_of_birth": dob, "phone": phone, "sex": sex,
                     "has_visited_before": visited, "insurer": ins, "referrals": list(refs), "note": note}
    if second:
        SECOND_PLAN[pid] = second
    USED[pid] = used


_p("P00001", "Marta", "Ruiz", "Navarro", "1987-04-12", "+34612345678", "F", True, "sanitas", note="Seen eleven times, mostly by Dra. Elena Ortiz at Arenal Centro. Last in March 2025.")
_p("P00002", "María", "García", "López", "1984-03-12", "+34600114471", "F", True, "adeslas", note="Two visits in 2025, dermatology.")
_p("P00003", "María", "García", "López", "1991-11-02", "+34611230915", "F", False, "dkv", note="New to the clinic.")
_p("P00004", "María José", "García", "Lozano", "1984-03-21", "+34622904471", "F", True, "mapfre")
_p("P00005", "Mario", "García", "López", "1975-07-30", "+34633412208", "M", True, "sanitas")
_p("P00006", "Antonio", "Ruiz", "Medina", "1944-05-03", "+34644551020", "M", True, "sanitas", refs=["physiotherapy"],
   note="Hard of hearing: speak slowly. Usually calls through his daughter Laura.")
_p("P00007", "Laura", "Ruiz", "Gómez", "1978-09-14", "+34655667788", "F", True, "axa")
_p("P00008", "Pau", "Vidal", "Serra", "2019-04-10", "+34666123450", "M", True, "sanitas", note="Child. Mother: Marta Serra.")
_p("P00009", "Marta", "Serra", "Puig", "1985-01-22", "+34666123451", "F", True, "sanitas")
_p("P00010", "Jordi", "Puig", "Vila", "1969-02-02", "+34677111222", "M", True, "adeslas", second="sanitas", note="Catalan speaker.")
_p("P00011", "Lucía", "Fernández", "Ortega", "1992-06-30", "+34688222333", "F", True, "caser", used=3)
_p("P00012", "Diego", "Martín", "Soto", "2001-12-05", "+34699333444", "M", False, "asisa")
_p("P00013", "Carmen", "López", "Díaz", "1960-12-01", "+34600998877", "F", True, "dkv")
_p("P00014", "Sergio", "Navas", "Prieto", "1990-01-01", "+34611000111", "M", True, "cigna", second="dkv")
PATIENTS["P00015"] = dict(PATIENTS["P00013"], patient_id="P00015", given_name="Carmen", first_surname="López", second_surname="Díez",
                          national_id=PATIENTS["P00013"]["national_id"][:7] + str((int(PATIENTS["P00013"]["national_id"][7]) + 1) % 10), phone="+34600998876")
PATIENTS["P00015"]["national_id"] = PATIENTS["P00015"]["national_id"][:8] + LET[int(PATIENTS["P00015"]["national_id"][:8]) % 23]
USED["P00015"] = 0

# Agenda: ocupación de 40 % a 72 % según profesional; citas próximas y pasadas
BUSY: set[tuple[str, str, datetime]] = set()
APPTS: dict[str, dict] = {}


def _slots(pid: str, loc: str, d: date):
    _, _, _, _, sched, _, _ = next(p for p in PROVIDERS if p[0] == pid)
    if d.weekday() not in sched.get(loc, []) or d in CLOSURES:
        return []
    h = SITES[loc]["hours"].get(d.weekday())
    if not h:
        return []
    a, b = (datetime.combine(d, time.fromisoformat(x), MAD) for x in h)
    out, t = [], a
    while t + timedelta(minutes=15) <= b:
        out.append(t)
        t += timedelta(minutes=15)
    return out


for i, p in enumerate(PROVIDERS):
    occ = 0.40 + 0.32 * ((i * 7) % 12) / 11
    if p[2] == "gynaecology":
        occ = 0.9
    d = CAL_START
    while d <= CAL_END:
        for loc in p[4]:
            for s in _slots(p[0], loc, d):
                if rng.random() < occ:
                    BUSY.add((p[0], loc, s))
        d += timedelta(days=1)

_seq = 0


def _appt(pid, prov, loc, typ, start):
    global _seq
    _seq += 1
    aid = f"A{_seq:05d}"
    APPTS[aid] = {"appointment_id": aid, "patient_id": pid, "provider_id": prov, "location_id": loc, "appointment_type_id": typ,
                  "start_time": start.isoformat(), "duration_minutes": TYPES[typ][1]}
    BUSY.discard((prov, loc, start))


_appt("P00001", "PR02", "centro", "review", datetime(2026, 9, 29, 10, 0, tzinfo=MAD))
_appt("P00006", "PR11", "sur", "physio_session", datetime(2026, 9, 24, 11, 0, tzinfo=MAD))
_appt("P00008", "PR04", "centro", "paediatric_review", datetime(2026, 9, 30, 17, 0, tzinfo=MAD))
_appt("P00007", "PR03", "norte", "review", datetime(2026, 9, 28, 9, 30, tzinfo=MAD))
_appt("P00007", "PR09", "norte", "orthopaedic_review", datetime(2026, 10, 2, 12, 0, tzinfo=MAD))
_appt("P00001", "PR02", "centro", "review", datetime(2025, 3, 11, 9, 0, tzinfo=MAD))
_appt("P00006", "PR01", "centro", "review", datetime(2025, 11, 4, 10, 15, tzinfo=MAD))

app = FastAPI(title="Prosper (falsa)")
SUBMISSIONS: dict[str, list] = {}


def _auth(key):
    if not key:
        raise HTTPException(403, "Invalid API key")


@app.get("/api/v1/health")
def health():
    return {"status": "healthy"}


def _ref(i):
    return {"id": i, "name": PLANS[i]["name"]}


@app.get("/api/v1/clinic")
def clinic(x_api_key: str | None = Header(None)):
    _auth(x_api_key)
    days = lambda hours: [{"weekday": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][k], "opens": v[0], "closes": v[1]} for k, v in hours.items()]
    return {
        "clinic_name": "Clínica Arenal", "patient_count": len(PATIENTS),
        "calendar": {"starts": CAL_START.isoformat(), "ends": CAL_END.isoformat(), "max_span_days": 14, "slot_minutes": 15,
                     "closure_days": [d.isoformat() for d in CLOSURES], "appointment_count": len(APPTS)},
        "restrictions": [{"id": r, "title": r.replace("_", " "), "explanation": ""} for r in RESTRICTIONS],
        "providers": [{"id": p[0], "name": p[1], "specialty_id": p[2], "specialty_name": SPECIALTIES[p[2]]["name"], "languages": p[3],
                       "appointment_type_names": [], "location_names": [SITES[l]["name"] for l in p[4]],
                       "schedules": [{"location_id": l, "location_name": SITES[l]["name"],
                                      "days": [{"weekday": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][w], "opens": SITES[l]["hours"].get(w, ("", ""))[0], "closes": SITES[l]["hours"].get(w, ("", ""))[1]} for w in ds]}
                                     for l, ds in p[4].items()],
                       "accepted_insurers": [_ref(i) for i in PLANS if i not in p[5]], "refused_insurers": [_ref(i) for i in p[5]],
                       "leave": {"start": p[6][0], "end": p[6][1], "reason": p[6][2]} if p[6] else None} for p in PROVIDERS],
        "specialties": [{"id": k, "name": v["name"], "min_age_months": v["min"], "max_age_months": v["max"], "referral_required": v["referral"],
                         "provider_names": [p[1] for p in PROVIDERS if p[2] == k],
                         "covered_by": [_ref(i) for i, pl in PLANS.items() if k not in pl["no_spec"]],
                         "not_covered_by": [_ref(i) for i, pl in PLANS.items() if k in pl["no_spec"]]} for k, v in SPECIALTIES.items()],
        "appointment_types": [{"id": k, "name": v[0], "duration_minutes": v[1], "new_patient_requirement": v[2], "guidance": "",
                               "provider_names": [], "specialty_id": v[3], "specialty_name": SPECIALTIES[v[3]]["name"] if v[3] else None} for k, v in TYPES.items()],
        "locations": [{"id": k, "name": v["name"], "address": v["address"], "latitude": v["lat"], "longitude": v["lon"], "hours": days(v["hours"]),
                       "provider_names": [p[1] for p in PROVIDERS if k in p[4]],
                       "covered_by": [_ref(i) for i, pl in PLANS.items() if k not in pl["no_site"]],
                       "not_covered_by": [_ref(i) for i, pl in PLANS.items() if k in pl["no_site"]]} for k, v in SITES.items()],
        "plans": [{"id": k, "name": v["name"], "covered_specialty_names": [SPECIALTIES[s]["name"] for s in SPECIALTIES if s not in v["no_spec"]],
                   "uncovered_specialty_names": [SPECIALTIES[s]["name"] for s in v["no_spec"]],
                   "covered_location_names": [SITES[s]["name"] for s in SITES if s not in v["no_site"]],
                   "uncovered_location_names": [SITES[s]["name"] for s in v["no_site"]],
                   "accepted_by": [p[1] for p in PROVIDERS if k not in p[5]], "refused_by": [p[1] for p in PROVIDERS if k in p[5]],
                   "holders": sum(1 for x in PATIENTS.values() if x["insurer"] == k)} for k, v in PLANS.items()],
    }


def _norm_id(s):
    return "".join(c for c in (s or "").upper() if c.isalnum())


def _norm_phone(s):
    d = "".join(c for c in (s or "") if c.isdigit())
    return d[-9:]


@app.get("/api/v1/directory")
def directory(name: str | None = None, national_id: str | None = None, phone: str | None = None, date_of_birth: str | None = None,
              x_api_key: str | None = Header(None)):
    _auth(x_api_key)
    out = []
    for p in PATIENTS.values():
        matched = []
        if national_id:
            if _norm_id(national_id) != _norm_id(p["national_id"]):
                continue
            matched.append("national_id")
        if phone:
            if _norm_phone(phone) != _norm_phone(p["phone"]):
                continue
            matched.append("phone")
        if date_of_birth:
            if date_of_birth != p["date_of_birth"]:
                continue
            matched.append("date_of_birth")
        score = 1.0
        if name:
            toks = set(fold(name).replace(",", " ").split())
            parts = fold(f"{p['given_name']} {p['first_surname']} {p['second_surname']}").split()
            hits = sum(1 for t in parts if t in toks)
            score = hits / max(1, len(parts))
            if hits == 0 or (hits < 2 and len(toks) >= 2):
                continue
            matched.append("name")
        out.append({**p, "match_score": round(score, 2), "matched_fields": matched})
    out.sort(key=lambda m: -m["match_score"])
    return {"matches": out[:10]}


@app.get("/api/v1/patients/{patient_id}/appointments")
def appointments(patient_id: str, when: str = "upcoming", x_api_key: str | None = Header(None)):
    _auth(x_api_key)
    if patient_id not in PATIENTS:
        raise HTTPException(404, "Not found")
    now = datetime.now(MAD)
    xs = [a for a in APPTS.values() if a["patient_id"] == patient_id]
    if when == "upcoming":
        xs = [a for a in xs if datetime.fromisoformat(a["start_time"]) > now]
    elif when == "past":
        xs = [a for a in xs if datetime.fromisoformat(a["start_time"]) <= now]
    return {"appointments": sorted(xs, key=lambda a: a["start_time"])}


def _age_months(dob: str, on: date) -> int:
    b = date.fromisoformat(dob)
    return (on.year - b.year) * 12 + on.month - b.month - (on.day < b.day)


def _type_for(spec: str, p: dict | None) -> str:
    visited = bool(p and p["has_visited_before"])
    own = [k for k, v in TYPES.items() if v[3] == spec]
    new = next((k for k in own if TYPES[k][2] == "new"), "first_visit")
    ret = next((k for k in own if TYPES[k][2] == "returning"), "review")
    return ret if visited else new


@app.get("/api/v1/availability")
def availability(date_from: date, date_to: date, provider_id: str | None = None, specialty_id: str | None = None,
                 location_id: str | None = None, patient_id: str | None = None, insurer: list[str] | None = Query(None),
                 x_api_key: str | None = Header(None)):
    _auth(x_api_key)
    if date_from < CAL_START or date_to > CAL_END or (date_to - date_from).days > 13 or date_to < date_from:
        raise HTTPException(422, "window out of range or longer than 14 days")
    p = PATIENTS.get(patient_id) if patient_id else None
    if patient_id and not p:
        raise HTTPException(404, "patient not found")
    plans = insurer or ([p["insurer"]] if p else [])
    provs = [x for x in PROVIDERS if (not provider_id or x[0] == provider_id) and (not specialty_id or x[2] == specialty_id)
             and (not location_id or location_id in x[4])]
    spec = specialty_id or (provs[0][2] if provs else None)
    typ = _type_for(spec, p) if spec else "first_visit"
    slots, blocked = [], []
    for pr in provs:
        reason = None
        sp = SPECIALTIES[pr[2]]
        if p:
            am = _age_months(p["date_of_birth"], date_from)
            if am < sp["min"] or (sp["max"] is not None and am > sp["max"]):
                reason = "not_eligible_age"
            elif sp["referral"] and pr[2] not in p["referrals"]:
                reason = "referral_required"
        payable = []
        if not reason and plans:
            for pl in plans:
                P = PLANS[pl]
                if pr[2] in P["no_spec"]:
                    reason = reason or "specialty_not_covered"; continue
                if pl in pr[5]:
                    reason = reason or "provider_not_in_network"; continue
                if all(l in P["no_site"] for l in pr[4] if not location_id or l == location_id):
                    reason = reason or "location_not_covered"; continue
                if pr[2] in P.get("referral_for", []) and p and pr[2] not in p["referrals"]:
                    reason = reason or "insurer_referral_required"; continue
                if P.get("cap") and p and USED.get(p["patient_id"], 0) >= P["cap"]:
                    reason = reason or "allowance_exhausted"; continue
                payable.append(pl)
            if payable:
                reason = None
        elif not reason:
            payable = ["privado"]
        leave = pr[6]
        if not reason and leave and date.fromisoformat(leave[0]) <= date_from and date_to <= date.fromisoformat(leave[1]):
            reason = "provider_on_leave"
        if not reason and location_id and provider_id and not any(_slots(pr[0], location_id, date_from + timedelta(days=i)) for i in range((date_to - date_from).days + 1)):
            reason = "location_hours"
        if reason:
            blocked.append({"provider_id": pr[0], "restriction": reason})
            continue
        d = date_from
        while d <= date_to:
            if not (leave and date.fromisoformat(leave[0]) <= d <= date.fromisoformat(leave[1])):
                for loc in pr[4]:
                    if location_id and loc != location_id:
                        continue
                    if plans and all(loc in PLANS[pl]["no_site"] for pl in payable):
                        continue
                    for s in _slots(pr[0], loc, d):
                        if (pr[0], loc, s) not in BUSY and not any(a["provider_id"] == pr[0] and a["start_time"] == s.isoformat() for a in APPTS.values()):
                            slots.append({"provider_id": pr[0], "provider_name": pr[1], "specialty_id": pr[2], "location_id": loc,
                                          "appointment_type_id": _type_for(pr[2], p), "start_time": s.isoformat(),
                                          "duration_minutes": TYPES[_type_for(pr[2], p)][1],
                                          "payable_with": [x for x in payable if loc not in PLANS[x]["no_site"]] or payable})
            d += timedelta(days=1)
    slots.sort(key=lambda s: s["start_time"])
    t = TYPES[typ]
    return {"providers": [{"id": x[0], "name": x[1], "specialty_id": x[2], "languages": x[3], "accepted_insurers": [i for i in PLANS if i not in x[5]],
                           "locations": list(x[4]), "on_leave_until": x[6][1] if x[6] else None} for x in provs],
            "appointment_type": {"id": typ, "name": t[0], "duration_minutes": t[1], "new_patient_requirement": t[2], "guidance": ""},
            "slots": slots[:400], "blocked": blocked}


class Body(BaseModel):
    call_id: str
    model_config = {"extra": "allow"}


@app.post("/api/v1/submit/{action}")
def submit(action: str, body: Body, x_api_key: str | None = Header(None)):
    _auth(x_api_key)
    verb = {"register": "REGISTER", "book": "BOOK", "reschedule": "RESCHEDULE", "cancel": "CANCEL", "no-action": "NO_ACTION", "escalate": "ESCALATE"}.get(action)
    if not verb:
        raise HTTPException(404, "Not found")
    data = body.model_dump()
    cid = data.pop("call_id")
    if verb == "REGISTER":
        nid = _norm_id(data.get("national_id", ""))
        digits = nid[1:] if nid[:1] in "XYZ" else nid
        num = (str("XYZ".index(nid[0])) + digits[:-1]) if nid[:1] in "XYZ" else digits[:-1]
        if not num.isdigit() or LET[int(num) % 23] != nid[-1:]:
            raise HTTPException(422, "national_id check letter does not match")
        act = {"action": verb, "new_patient": data}
    else:
        act = {"action": verb, **data}
    rec = SUBMISSIONS.setdefault(cid, [])
    if act in rec:
        raise HTTPException(409, "identical action already accepted")
    rec.append(act)
    return {"call_id": cid, "received_at": datetime.utcnow().isoformat() + "Z", "record": {"actions": rec}}


@app.get("/api/v1/submissions")
def submissions(limit: int = 50, x_api_key: str | None = Header(None)):
    _auth(x_api_key)
    return {"records": [{"call_id": k, "record": {"actions": v}} for k, v in list(SUBMISSIONS.items())[-limit:]]}
