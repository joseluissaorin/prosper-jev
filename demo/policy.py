"""La política: una máquina de estados en código que decide qué hace el agente.

Jev (sense.py) solo interpreta a quien llama. Aquí se decide el siguiente paso, se aplican
las reglas, se hacen los controles antes de escribir y se escribe en dos fases.
Cada decisión deja una traza para el panel y para el informe final."""
from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

import clinic
import nlg
import system2
from clinic import DOCTORS, PATIENTS
from jev import JEV, choice, noul
from sense import INTENTS, Perception, resolve_day, resolve_dob

T_ACT = 0.6          # confianza mínima para creerse el acto de quien llama
T_CONFIRM = 0.85     # un «sí» para escribir tiene que ser claro
T_EMERGENCY = 0.85   # por encima: 112 sin preguntar
T_EMERGENCY_CHECK = 0.5
T_MANIPULATION = 0.8


@dataclass
class Pref:
    doctor: str | None = None
    site: str | None = None
    day_mode: str = "none"      # none | asap | day
    day: date | None = None
    part: str = "any"

    def any(self) -> bool:
        return bool(self.doctor or self.site or self.day_mode != "none" or self.part != "any")


@dataclass
class CallState:
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    started: float = field(default_factory=time.time)
    lang: str = "es"
    lang_locked: bool = False
    history: list[str] = field(default_factory=list)
    last_agent: str = ""
    pending: str = "need"
    intent: str | None = None
    relation: str | None = None
    patient_id: str | None = None
    name_texts: list[str] = field(default_factory=list)
    dob: date | None = None
    dob_tries: int = 0
    phone4: str | None = None
    surname2_text: str | None = None
    dis_field: str | None = None
    dis_rounds: int = 0
    new_name: dict | None = None
    service: str | None = None
    pref: Pref = field(default_factory=Pref)
    asked_when: bool = False
    offered: dict = field(default_factory=dict)
    chosen: dict | None = None
    confirm_kind: str | None = None
    appt_choices: dict = field(default_factory=dict)
    target_appt: str | None = None
    emergency_checked: bool = False
    prev_pending: str | None = None
    repeats: int = 0
    outcome: str | None = None
    reason: str | None = None
    actions: list = field(default_factory=list)
    flags: set = field(default_factory=set)
    trace: list = field(default_factory=list)
    ended: bool = False


class Call:
    def __init__(self, lang: str | None = None):
        self.s = CallState()
        if lang:
            self.s.lang, self.s.lang_locked = lang, True

    # ---------------------------------------------------------------- utilidades

    def _say(self, key: str, **kw) -> dict:
        text = nlg.say(key, self.s.lang, **kw)
        return {"kind": "say", "text": text, "act": key}

    def _log(self, kind: str, **kw) -> dict:
        ev = {"t": round(time.time() - self.s.started, 2), "kind": kind, **kw}
        self.s.trace.append(ev)
        return {"kind": "event", "event": ev}

    def gate(self, name: str, ok: bool, detail: str = "") -> dict:
        return self._log("gate", name=name, ok=ok, detail=detail)

    def spoken(self, text: str) -> None:
        self.s.last_agent = text
        self.s.history.append(f"Receptionist: {text}")

    def opening(self) -> list[dict]:
        out = [self._say("greet")]
        self.s.pending = "need"
        return out

    def snapshot(self) -> dict:
        s = self.s
        p = PATIENTS.get(s.patient_id) if s.patient_id else None
        return {
            "lang": s.lang, "lang_locked": s.lang_locked, "pending": s.pending, "intent": s.intent,
            "relation": s.relation, "patient": p.full_name if p else None, "patient_id": s.patient_id,
            "candidates": [PATIENTS[c].full_name + f" ({PATIENTS[c].dob.isoformat()})" for c in self._candidates()] if not s.patient_id else [],
            "dob_heard": s.dob.isoformat() if s.dob else None, "service": s.service,
            "pref": {"doctor": s.pref.doctor, "site": s.pref.site, "day": s.pref.day.isoformat() if s.pref.day else s.pref.day_mode, "part": s.pref.part},
            "offered": {k: v["desc"] for k, v in s.offered.items()}, "chosen": s.chosen["desc"] if s.chosen else None,
            "target_appt": s.target_appt, "outcome": s.outcome, "reason": s.reason, "flags": sorted(s.flags),
        }

    # ---------------------------------------------------------------- turno de quien llama

    async def handle(self, text: str, p: Perception, dry: bool = False) -> list[dict]:
        """Procesa un turno. Con dry=True trabaja sobre una copia y no escribe nada (para preparar la voz)."""
        if dry:
            shadow = Call()
            shadow.s = copy.deepcopy(self.s)
            shadow._dry = True
            return await shadow.handle(text, p)
        s = self.s
        s.history.append(f"Caller: {text}")
        out: list[dict] = []
        act, act_c = p.act
        out.append(self._log("perception", text=text, act=act, act_c=act_c, ms=p.ms, hedged=p.hedged,
                             j={k: (v.get("choice"), v.get("confidence")) if v["type"] == "choice" else v.get("noul") for k, v in p.raw.items()
                                if k in ("act", "intent", "emergency", "urgent_today", "manipulation", "offscript", "third_party", "relation", "lang", "finished", "service", "slot")}))

        # idioma: se fija con el primer turno con contenido
        if not s.lang_locked:
            lg, lc = p.c("lang")
            if lg in nlg.LANGS and lc >= 0.7 and len(text.split()) >= 2:
                s.lang, s.lang_locked = lg, True
                out.append(self._log("lang_locked", lang=lg, conf=lc))

        # 1. seguridad antes que nada
        em = p.n("emergency")
        if s.pending == "emergency_check":
            if act == "confirm" or em >= T_EMERGENCY_CHECK:
                return out + self._emergency(em)
            s.pending = s.prev_pending or "need"
            out.append(self._say("ack"))
            return out + await self._advance()
        if em >= T_EMERGENCY:
            return out + self._emergency(em)
        if em >= T_EMERGENCY_CHECK and not s.emergency_checked:
            s.emergency_checked, s.prev_pending, s.pending = True, s.pending, "emergency_check"
            out.append(self.gate("triaje", False, f"posible urgencia {em:.2f}: se comprueba"))
            return out + [self._say("emergency_check")]
        it, ic = p.c("intent")
        if (p.n("urgent_today") >= 0.8 and it in ("medical_now", "book")) or (it == "medical_now" and ic >= 0.7):
            return out + self._urgent(p)

        # 2. manipulación: se rechaza y se vuelve a lo pendiente, sin tocar el estado
        if p.n("manipulation") >= T_MANIPULATION:
            s.flags.add("manipulation_declined")
            out.append(self.gate("manipulación", False, f"{p.n('manipulation'):.2f}"))
            out.append(self._say("decline_manipulation"))
            return out + await self._advance(reprompt=True)

        # 3. despedida
        if act == "end_call" and act_c >= T_ACT and s.pending != "confirm":
            return out + self._goodbye()
        if s.pending == "anything_else" and act == "reject" and act_c >= T_ACT:
            return out + self._goodbye()

        # 4. no se entiende
        if (act == "unclear" and act_c >= 0.5) or not text.strip():
            s.repeats += 1
            out.append(self._log("repeat", n=s.repeats))
            return out + [self._say("ask_repeat")]

        # 5. fuera de guion → Sistema 2 (con frase de espera) y se retoma lo pendiente
        if p.n("offscript") >= 0.6 and act in ("ask_question", "provide_info") and s.pending != "confirm":
            if s.intent is None and it == "info":
                s.intent = "info"
            then = await self._advance(reprompt=True, quiet=True)
            out.append(self._log("system2", question=text))
            s.flags.add("system2")
            return out + [self._say("filler"), {"kind": "s2", "question": text, "lang": s.lang, "then": then}]

        # 6. intención (se puede cambiar de opinión)
        if it in ("book", "reschedule", "cancel") and ic >= 0.7:
            if s.intent in (None, "info"):
                s.intent = it
                out.append(self._log("intent", intent=it, conf=ic))
            elif it != s.intent and ic >= 0.85 and (act in ("correct", "provide_info") or s.pending in ("anything_else", "need")):
                out.append(self._log("intent_changed", old=s.intent, new=it, conf=ic))
                self._switch_intent(it)

        # 7. para quién es
        rel, rc = p.c("relation")
        tp = p.n("third_party")
        if s.intent in ("book", "reschedule", "cancel"):
            if s.relation is None:
                s.relation = rel if (tp >= 0.7 and rel and rel != "self" and rc >= 0.5) else ("self" if tp < 0.5 else None)
            elif act == "correct" and tp >= 0.8 and rel and rel != s.relation and rel != "self":
                out.append(self._log("patient_changed", relation=rel))
                s.relation, s.patient_id, s.name_texts, s.dob = rel, None, [], None

        # 8. lo que corresponde a lo pendiente
        out += await self._absorb(text, p, act, act_c)
        if s.ended:
            return out
        if getattr(self, "_stop", False):
            self._stop = False
            return out
        return out + await self._advance()

    # ---------------------------------------------------------------- absorber datos del turno

    async def _absorb(self, text: str, p: Perception, act: str, act_c: float) -> list[dict]:
        s = self.s
        out: list[dict] = []

        # identidad (de cualquier turno hasta verificarla)
        if not s.patient_id:
            if p.n("gives_name") >= 0.6:
                s.name_texts.append(text)
            dob = resolve_dob(p)
            if dob:
                s.dob = dob
            digits = clinic.phone_digits(text)
            if s.dis_field == "phone" and len(digits) >= 4:
                s.phone4 = digits[-4:]
            if s.dis_field == "surname2":
                s.surname2_text = text
        if s.pending == "new_phone":
            digits = clinic.phone_digits(text)
            if len(digits) >= 9 and s.new_name and s.dob:
                n = s.new_name
                if getattr(self, "_dry", False):
                    s.patient_id = "p_new"
                else:
                    pt = clinic.new_patient(n["given"], n["surname1"], n.get("surname2", ""), s.dob, digits[-9:])
                    s.patient_id = pt.id
                    s.actions.append({"action": "patient_created", "patient_id": pt.id, "name": pt.full_name})
                    out.append(self.gate("alta de paciente", True, pt.full_name))
                s.flags.add("new_patient")
                s.pending = "service"
            else:
                out.append(self._say("ask_repeat"))
                self._stop = True
                return out

        # preferencias de la cita (fuera de las preguntas de identidad, para no confundir la fecha de nacimiento).
        # Si está eligiendo una de las opciones ofrecidas, «la primera» no es «a primera hora».
        sl0, slc0 = p.c("slot")
        picking = s.pending == "choose_slot" and sl0 in s.offered and slc0 >= 0.6 and act != "correct"
        if s.pending not in ("identity", "identity_dob", "disambiguate", "new_phone", "which_appt") and not picking:
            svc, sc = p.c("service")
            if svc and svc != "not_stated" and sc >= 0.6 and (not s.service or act == "correct"):
                s.service = svc
            doc, dc = p.c("doctor_pref")
            if doc and doc != "none" and dc >= 0.7:
                s.pref.doctor = doc
                s.service = s.service or DOCTORS[doc].service
            site, stc = p.c("site_pref")
            if site and site != "none" and stc >= 0.7:
                s.pref.site = site
            mode, day = resolve_day(p)
            changed = False
            if mode != "none" and (mode, day) != (s.pref.day_mode, s.pref.day):
                s.pref.day_mode, s.pref.day = mode, day
                changed = True
            part, pc = p.c("part_of_day")
            if part and part != "any" and pc >= 0.6 and part != s.pref.part:
                s.pref.part = part
                changed = True
            if s.pending == "confirm" and act == "confirm":
                changed = False
            if changed:
                out.append(self._log("pref", day=s.pref.day.isoformat() if s.pref.day else s.pref.day_mode, part=s.pref.part,
                                     doctor=s.pref.doctor, site=s.pref.site))
        else:
            changed = False

        # lo pendiente
        if s.pending == "choose_slot":
            sl, slc = p.c("slot")
            if len(s.offered) == 1 and act == "confirm" and act_c >= T_ACT and not changed:
                sl, slc = next(iter(s.offered)), 1.0
            if sl in s.offered and slc >= 0.6 and act not in ("reject",) and not changed:
                out += await self._choose(s.offered[sl])
                self._stop = True
            elif sl == "other_time" or changed or act == "correct":
                s.offered = {}
            elif act == "confirm":
                out.append(self._say("which_one"))
                self._stop = True
            elif sl == "none" or act == "reject":
                s.offered, s.asked_when = {}, False
                s.pref = Pref(doctor=s.pref.doctor, site=s.pref.site)
        elif s.pending == "confirm":
            if act == "confirm" and act_c >= T_CONFIRM and p.finished >= 0.5 and not changed:
                out += await self._commit()
                self._stop = True
            else:
                out.append(self.gate("confirmación", False, f"acto={act} ({act_c:.2f})"))
                if not getattr(self, "_dry", False):
                    await clinic.release_holds(s.call_id)
                if s.confirm_kind == "cancel" and s.intent == "cancel":
                    s.target_appt, s.confirm_kind = None, None
                    s.pending = "anything_else"
                    out.append(self._say("ack"))
                    out.append(self._say("anything_else"))
                    self._stop = True
                else:
                    s.chosen, s.offered, s.confirm_kind = None, {}, None
                    if not changed:
                        s.asked_when = False
                        s.pref = Pref(doctor=s.pref.doctor, site=s.pref.site)
        elif s.pending == "which_appt":
            ap, apc = p.c("appt")
            if ap in s.appt_choices and apc >= 0.6:
                s.target_appt = ap
                out.append(self._log("appt_selected", appt=ap))
        elif s.pending == "waitlist":
            if act == "confirm" and act_c >= T_ACT:
                s.flags.add("waitlisted")
                s.actions.append({"action": "waitlisted", "service": s.service, "doctor": s.pref.doctor})
                s.pending = "anything_else"
                out.append(self._say("waitlisted"))
                self._stop = True
            else:
                s.pending = "anything_else"
                out.append(self._say("ack"))
                out.append(self._say("anything_else"))
                self._stop = True
        elif s.pending == "anything_else":
            it, ic = p.c("intent")
            if it in ("book", "reschedule", "cancel") and ic >= 0.7:
                self._switch_intent(it)
        return out

    # ---------------------------------------------------------------- siguiente paso (determinista)

    async def _advance(self, reprompt: bool = False, quiet: bool = False) -> list[dict]:
        s = self.s
        out: list[dict] = []

        if s.pending == "anything_else":
            return [self._say("anything_else")]
        if s.intent in (None, "info"):
            s.pending = "need" if s.intent is None else "anything_else"
            return [self._say("ask_need" if s.intent is None else "anything_else")]

        if not s.patient_id:
            ident = await self._identity(quiet)
            if ident is not None:
                return ident
            p = PATIENTS[s.patient_id]
            out.append(self._say("greet_patient" if s.relation in (None, "self") else "greet_patient_other", first=p.first))

        p = PATIENTS[s.patient_id]
        if s.intent == "book":
            if not s.service or s.service == "not_stated":
                s.pending = "service"
                return out + [self._say("ask_service")]
            rule = clinic.check_booking(p, s.service, s.pref.day if s.pref.day_mode == "day" else None)
            out.append(self.gate("reglas", rule is None, rule or "ninguna regla lo impide"))
            if rule:
                return out + self._refuse(rule, p)
            if not s.pref.any() and not s.asked_when:
                s.asked_when, s.pending = True, "when"
                return out + [self._say("ask_when")]
            return out + self._offer(s.service)

        if s.intent in ("reschedule", "cancel"):
            appts = clinic.future_appointments(p.id)
            if not appts:
                s.pending = "anything_else"
                out.append(self._log("no_appointments"))
                return out + [self._say("no_appts")]
            if not s.target_appt:
                if len(appts) == 1:
                    s.target_appt = appts[0].id
                else:
                    s.appt_choices = {a.id: nlg.appt_phrase("en", a) for a in appts}
                    s.pending = "which_appt"
                    verb = {"reschedule": {"es": "cambiar", "ca": "canviar", "gl": "cambiar", "eu": "aldatu", "en": "move"},
                            "cancel": {"es": "anular", "ca": "anul·lar", "gl": "anular", "eu": "ezeztatu", "en": "cancel"}}[s.intent][s.lang]
                    return out + [self._say("which_appt", options=nlg.join_or(s.lang, [nlg.appt_phrase(s.lang, a) for a in appts]), verb=verb)]
            a = clinic.APPOINTMENTS[s.target_appt]
            if s.intent == "cancel":
                s.pending, s.confirm_kind = "confirm", "cancel"
                return out + [self._say("readback_cancel", appt=nlg.appt_phrase(s.lang, a))]
            s.service = a.service
            if not s.pref.any() and not s.asked_when:
                s.asked_when, s.pending = True, "when"
                return out + [self._say("ask_when")]
            return out + self._offer(a.service, exclude=a.id)
        return out + [self._say("anything_else")]

    async def _identity(self, quiet: bool) -> list[dict] | None:
        """Devuelve la pregunta siguiente, o None si la identidad ya está verificada."""
        s = self.s
        other = s.relation not in (None, "self")
        cands = self._candidates()
        if not s.name_texts:
            s.pending = "identity"
            return [self._say("ask_identity_other" if other else "ask_identity_self")]
        if not cands:
            if not s.dob:
                s.dob_tries += 1
                if s.dob_tries > 2:
                    return self._unresolved("dob_not_understood")
                s.pending = "identity_dob"
                return [self._say("ask_dob")]
            if s.intent == "book":
                if not getattr(self, "_dry", False):
                    s.new_name = await system2.extract_name(" ".join(s.name_texts))
                else:
                    s.new_name = {"given": "?", "surname1": "?"}
                if s.new_name:
                    s.pending = "new_phone"
                    return [self.gate("identidad", True, "sin ficha: alta nueva"), self._say("not_found_offer_new")]
            return self._unresolved("no_match")
        if len(cands) == 1 and s.dob:
            pid = cands[0]
            pt = PATIENTS[pid]
            auth = self._authorised(pt)
            gate = self.gate("identidad", True, f"{pt.full_name}, {pt.dob.isoformat()}")
            if not auth:
                s.patient_id = None
                s.outcome, s.reason, s.pending = "refused", "not_authorised", "anything_else"
                return [gate, self.gate("autorización", False, s.relation or ""), self._say("refuse_not_authorised")]
            s.patient_id = pid
            s.pending = "verified"
            return None
        if not s.dob:
            s.pending, s.dis_field = ("disambiguate", "dob") if len(cands) > 1 else ("identity_dob", None)
            return [self._say("disambiguate_dob" if len(cands) > 1 else "ask_dob")]
        s.dis_rounds += 1
        if s.dis_rounds > 3:
            return self._unresolved("ambiguous")
        f = clinic.distinguishing_field(cands)
        s.pending, s.dis_field = "disambiguate", f
        return [self._say({"dob": "disambiguate_dob", "phone": "disambiguate_phone", "surname2": "disambiguate_surname2"}[f])]

    def _candidates(self) -> list[str]:
        s = self.s
        cands = clinic.name_candidates(" ".join(s.name_texts)) if s.name_texts else []
        if s.dob:
            cands = [c for c in cands if PATIENTS[c].dob == s.dob]
        if s.phone4:
            cands = [c for c in cands if PATIENTS[c].phone.endswith(s.phone4)]
        if s.surname2_text:
            t = clinic.fold(s.surname2_text)
            cands = [c for c in cands if clinic.fold(PATIENTS[c].surname2) in t] or cands
        return cands

    def _authorised(self, pt) -> bool:
        rel = self.s.relation
        if rel in (None, "self"):
            return True
        if rel == "child":
            return pt.age() < 18
        if rel in ("parent", "partner", "other_relative"):
            return bool(pt.relatives)
        return False

    def _unresolved(self, why: str) -> list[dict]:
        s = self.s
        s.outcome, s.reason, s.pending = "identity_unresolved", why, "anything_else"
        return [self.gate("identidad", False, why), self._say("identity_unresolved")]

    # ---------------------------------------------------------------- ofrecer, elegir, escribir

    def _offer(self, service: str, exclude: str | None = None) -> list[dict]:
        s = self.s
        day = s.pref.day if s.pref.day_mode == "day" else None
        if day and (day - clinic.TODAY).days > clinic.MAX_DAYS_AHEAD:
            return self._refuse("max_days_ahead", PATIENTS[s.patient_id])
        slots, exact = clinic.find_slots(service, doctor=s.pref.doctor, site=s.pref.site, day=day, part=s.pref.part,
                                         call_id=s.call_id)
        if not slots and s.pref.part != "any":
            slots, exact = clinic.find_slots(service, doctor=s.pref.doctor, site=s.pref.site, day=day, call_id=s.call_id)
            exact = False
        out = [self._log("availability", service=service, found=len(slots), exact=exact)]
        if not slots:
            what = nlg.who_is_full(s.lang, DOCTORS[s.pref.doctor] if s.pref.doctor else None, service)
            s.outcome, s.reason, s.pending = "no_availability", service, "waitlist"
            return out + [self._say("no_availability", what=what)]
        s.offered = {f"s{i + 1}": {"doctor": d, "start": st.isoformat(), "desc": nlg.slot_phrase("en", d, st)}
                     for i, (d, st) in enumerate(slots)}
        if not getattr(self, "_dry", False):
            clinic.soft_hold(slots, s.call_id)
        s.pending = "choose_slot"
        options = nlg.offer_options(s.lang, slots)
        return out + [self._say(("offer_one" if len(slots) == 1 else "offer") if exact else "offer_alt", options=options)]

    async def _choose(self, slot: dict) -> list[dict]:
        s = self.s
        st = datetime.fromisoformat(slot["start"])
        dry = getattr(self, "_dry", False)
        ok = True if dry else await clinic.hold(slot["doctor"], st, s.call_id)
        out = [self.gate("reserva provisional", ok, slot["desc"])]
        if not ok:
            s.offered = {}
            return out + [self._say("hold_lost")] + self._offer(s.service)
        s.chosen = slot
        s.pending = "confirm"
        doc = DOCTORS[slot["doctor"]]
        p = PATIENTS.get(s.patient_id)
        if s.intent == "reschedule" and s.target_appt:
            s.confirm_kind = "move"
            old = clinic.APPOINTMENTS[s.target_appt]
            return out + [self._say("readback_move", old=nlg.when(s.lang, old.start), when=nlg.when(s.lang, st),
                                    doctor_with=nlg.doctor_with(s.lang, doc))]
        s.confirm_kind = "book"
        return out + [self._say("readback_book", patient=p.full_name if p else "", service=nlg.service_name(s.lang, doc.service),
                                doctor_with=nlg.doctor_with(s.lang, doc), when=nlg.when(s.lang, st), site=nlg.site_at(s.lang, doc.site))]

    async def _commit(self) -> list[dict]:
        s = self.s
        dry = getattr(self, "_dry", False)
        out = [self.gate("confirmación", True, "sí claro, frase terminada, sin correcciones")]
        p = PATIENTS[s.patient_id] if s.patient_id in PATIENTS else None
        if s.confirm_kind == "cancel":
            a = None if dry else await clinic.commit_cancel(s.target_appt)
            if a or dry:
                s.outcome, s.reason = "cancelled", None
                if a:
                    s.actions.append({"action": "cancelled", "appointment": _appt(a)})
                s.pending, s.target_appt, s.confirm_kind = "anything_else", None, None
                return out + [self.gate("escritura", True, "anulación"), self._say("done_cancel")]
            return out + [self.gate("escritura", False, "anulación")] + self._unresolved("write_failed")
        st = datetime.fromisoformat(s.chosen["start"])
        doc = DOCTORS[s.chosen["doctor"]]
        if s.confirm_kind == "move":
            prev = _appt(clinic.APPOINTMENTS[s.target_appt])
            a = None if dry else await clinic.commit_move(s.target_appt, doc.id, st, s.call_id)
            if a or dry:
                s.outcome, s.reason = "rescheduled", None
                if a:
                    s.actions.append({"action": "rescheduled", "appointment": _appt(a), "previous": prev})
                s.pending, s.chosen, s.target_appt, s.confirm_kind, s.offered = "anything_else", None, None, None, {}
                return out + [self.gate("escritura", True, "cambio"), self._say("done_move", when=nlg.when(s.lang, st))]
        else:
            rule = clinic.check_booking(p, doc.service, st.date())
            out.append(self.gate("reglas (otra vez, antes de escribir)", rule is None, rule or "ok"))
            if rule:
                return out + self._refuse(rule, p)
            a = None if dry else await clinic.commit_booking(p.id, doc.id, st, s.call_id, idem=f"{s.call_id}:{doc.id}:{st.isoformat()}")
            if a or dry:
                s.outcome, s.reason = "booked", None
                if a:
                    s.actions.append({"action": "booked", "appointment": _appt(a)})
                s.pending, s.chosen, s.confirm_kind, s.offered = "anything_else", None, None, {}
                return out + [self.gate("escritura", True, "reserva"),
                              self._say("done_book", first=p.first, when=nlg.when(s.lang, st), doctor_with=nlg.doctor_with(s.lang, doc))]
        s.chosen, s.offered = None, {}
        return out + [self.gate("escritura", False, "el hueco ya no estaba reservado"), self._say("hold_lost")] + self._offer(s.service)

    # ---------------------------------------------------------------- salidas especiales

    def _refuse(self, rule: str, p) -> list[dict]:
        s = self.s
        s.outcome, s.reason, s.pending = "refused", rule, "anything_else"
        kw = {}
        if rule == "one_per_specialty":
            a = next(a for a in clinic.future_appointments(p.id) if a.service == s.service)
            kw = {"service": nlg.service_name(s.lang, s.service), "when": nlg.when(s.lang, a.start)}
        return [self._log("refused", rule=rule), self._say(f"refuse_{rule}", **kw)]

    def _emergency(self, em: float) -> list[dict]:
        s = self.s
        s.outcome, s.reason, s.pending = "escalated", "emergency_112", "anything_else"
        s.flags.add("emergency")
        return [self.gate("triaje", False, f"urgencia {em:.2f}: 112, no se reserva"), self._say("emergency")]

    def _urgent(self, p: Perception) -> list[dict]:
        s = self.s
        s.outcome, s.reason, s.pending = "escalated", "urgent_care_today", "anything_else"
        s.flags.add("urgent_today")
        return [self.gate("triaje", False, f"debe verle un médico hoy ({p.n('urgent_today'):.2f})"), self._say("urgent_today")]

    def _goodbye(self) -> list[dict]:
        self.s.ended = True
        return [self._say("goodbye"), {"kind": "end"}]

    def _switch_intent(self, it: str) -> None:
        s = self.s
        s.intent = it
        s.pending = "need"
        s.service = None if it == "book" else s.service
        s.pref, s.asked_when, s.offered, s.chosen, s.target_appt, s.confirm_kind = Pref(), False, {}, None, None, None
        if s.outcome in ("refused", "no_availability"):
            s.outcome, s.reason = None, None

    # ---------------------------------------------------------------- informe final

    async def report(self) -> dict:
        s = self.s
        outcome, reason = s.outcome, s.reason
        # una escritura real prevalece sobre lo que pasara después (salvo una urgencia)
        writes = [a for a in s.actions if a.get("action") in ("booked", "rescheduled", "cancelled")]
        if writes and outcome != "escalated":
            outcome, reason = writes[-1]["action"], None
        if not outcome and "manipulation_declined" in s.flags:
            outcome, reason = "refused", "manipulation"
        if not outcome:
            outcome = "info_only" if s.intent == "info" or "system2" in s.flags else "no_action"
        r = {
            "call_id": s.call_id, "language": s.lang, "outcome": outcome, "reason": reason,
            "patient_id": s.patient_id, "caller_relation": s.relation or "self",
            "appointment": next((a.get("appointment") for a in reversed(s.actions) if "appointment" in a), None),
            "previous_appointment": next((a.get("previous") for a in reversed(s.actions) if a.get("action") == "rescheduled"), None),
            "actions": s.actions, "flags": sorted(s.flags), "duration_s": round(time.time() - s.started, 1),
        }
        # auditoría: ¿lo hecho cuadra con lo hablado?
        try:
            a = await JEV.ask({"transcript": s.history[-14:], "actions_executed": s.actions, "declared_outcome": outcome,
                               "declared_reason": reason},
                              {"consistent": noul("Do `actions_executed` and `declared_outcome` match what the receptionist told the caller in `transcript`?"),
                               "final_wish": choice("What did the caller finally want, per `transcript`?", INTENTS | {"nothing": "Nothing / hung up"})})
            r["audit"] = {"consistent": a["answers"]["consistent"]["noul"], "final_wish": a["answers"]["final_wish"]["choice"], "ms": a["ms"]}
        except Exception as e:  # noqa: BLE001
            r["audit"] = {"error": str(e)[:120]}
        r["trace"] = s.trace
        return r


def _appt(a) -> dict:
    d = DOCTORS[a.doctor_id]
    return {"id": a.id, "patient_id": a.patient_id, "doctor_id": d.id, "doctor": d.short, "service": d.service,
            "site": d.site, "start": a.start.isoformat(timespec="minutes"), "status": a.status}
