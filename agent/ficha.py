"""La ficha antes de preguntar: qué dice la nota de un paciente sobre CÓMO tratarle, y qué cuenta su historial.

Las notas de la clínica no son texto libre: son una gramática cerrada (medido el 20-09-2026 sobre las 2.900 fichas:
un resumen del historial más una pauta de trato, de unas 45 distintas). Cada pauta se convierte aquí en dos cosas:

- un CÓDIGO que cambia lo que hace el código (hablar más despacio, leer el DNI agrupado o cifra a cifra, repetir la
  cita antes de colgar, aguantar un silencio sin dar la línea por caída…), porque eso un planificador no lo puede hacer;
- una INSTRUCCIÓN corta para el planificador, que es quien redacta.

Una nota nunca es una preferencia de agenda: nada de aquí toca qué hueco se ofrece. Lo que pida quien llama manda.

    python3 ficha.py /tmp/patients.json      # cobertura: qué notas no casan con ninguna pauta
"""
from __future__ import annotations

import re
from collections import Counter

# (patrón sobre la nota, código, instrucción para el planificador)
PAUTAS: list[tuple[str, str, str]] = [
    (r"hard of hearing", "despacio",
     "hard of hearing: short sentences, one piece of information at a time, and say the date and time TWICE when you confirm"),
    (r"speakerphone|somewhere busy|child is audible", "ruido",
     "noisy line on their side: keep sentences short and repeat the date and time once more when you confirm"),
    (r"mishears numbers", "cifra_a_cifra", "say any identifier or phone number back digit by digit, slowly"),
    (r"run of digits", "agrupado", "when you say their identifier back, group the digits in pairs"),
    (r"repeats numbers back wrong", "confirmar_dos", "if they repeat a number back wrong, correct it gently and confirm it a second time"),
    (r"weekday wrong", "dia_y_fecha", "always say the weekday AND the date together, and if they name a weekday that does not match the date, point it out kindly"),
    (r"morning or afternoon", "hora_exacta", "they blur times into 'morning' or 'afternoon': confirm the exact hour explicitly"),
    (r"read the appointment back before ending|date and time again at the end", "repaso_final",
     "before saying goodbye, read the full appointment back once (day, date, time, doctor, site) without being asked"),
    (r"writes it down", "pausa_fecha", "they write it down: after the date and time, pause and ask if they have got it"),
    (r"goes quiet while they check", "silencio_ok", "a long silence is them checking their diary, not a dropped line: wait, do not ask if they are still there"),
    (r"slow to settle", "sin_prisa", "give them a moment at the start: greet warmly and let them explain before your first question"),
    (r"addressed by surname", "apellido", "address them by title and surname (Sr./Sra. + first surname), never by first name"),
    (r"relative often speaks", "quien_habla", "a relative often speaks for them: check politely who you are talking to before discussing the record"),
    (r"names whoever they are calling about|books for a partner", "quien_paciente", "settle early who the appointment is for: themselves or someone else"),
    (r"gives their own name first; the patient is the child|parent is always the caller|parent rings and may raise a sibling", "menor",
     "the patient is a child and a parent calls: keep clear which child each detail belongs to, and identify the CHILD as the patient"),
    (r"parent has the child's details", "menor_datos", "the parent has the child's details to hand but maybe not their own: ask for the child's"),
    (r"grandparent", "abuelo", "a grandparent may be calling and may not know the insurer: ask, and do not assume"),
    (r"already has an appointment on the books|something ahead of them on the diary", "cita_previa",
     "they already have an appointment coming up: mention it before booking another, they may mean that one"),
    (r"answers before the question is finished", "oyo_entera", "they answer before you finish: check they heard the whole question"),
    (r"talks over the top", "de_uno_en_uno", "they talk over you: confirm each detail on its own, one at a time"),
    (r"corrects themselves mid-sentence", "ultima_respuesta", "they correct themselves mid-sentence: take the LAST answer, not the first"),
    (r"volunteers nothing", "preguntar_todo", "they volunteer nothing: ask for each detail directly, one question at a time"),
    (r"keep the call going", "reconducir", "they like to chat: be warm but steer back to what is being booked"),
    (r"rings from work", "breve", "they ring from work: keep it short and confirm once"),
    (r"spells their surname", "deletreo", "they spell their surname unprompted: take the spelling and use it"),
    (r"does not use email", "sin_correo", "they do not use email: confirm everything on the call itself and do not offer anything by email"),
    (r"confirmation by text", "sms", "they will ask for a text confirmation: be honest that you confirm on the call, and offer to repeat the details"),
    (r"reference number", "referencia", "they will ask for a reference number: there is none; reassure them the booking is in the system under their name"),
    (r"soonest before anything", "lo_primero", "they want to know what is available soonest: lead with the earliest slot"),
    (r"what it costs", "coste", "they will ask what it costs under their policy: say only what the record shows is covered; never quote a price you do not have"),
    (r"can be moved once", "se_puede_cambiar", "they will ask whether an appointment can be moved later: yes, by ringing reception"),
    (r"outside working hours", "fuera_horario", "they will ask to be seen outside opening hours: check the real hours with clinic_info, never invent them"),
    (r"what to bring|needs to bring anything|entrance and which floor|parking", "logistica",
     "they will ask about logistics (what to bring, entrance, parking): answer only what clinic_info gives you; if it is not there, say reception will tell them on arrival"),
]
_RX = [(re.compile(p, re.I), code, tip) for p, code, tip in PAUTAS]
LENTO = {"despacio", "ruido", "cifra_a_cifra"}          # códigos que piden una voz más pausada


def trato(note: str) -> list[tuple[str, str]]:
    """Las pautas de trato que pide la nota: [(código, instrucción)]."""
    return [(code, tip) for rx, code, tip in _RX if rx.search(note or "")]


def historial(appts: list[dict], now_iso: str, prov_name, site_name) -> dict:
    """Lo que el historial cuenta sin preguntar: cuántas veces ha venido, con quién suele ir, a qué sede y qué tiene
    por delante. `prov_name` y `site_name` traducen ids a nombres. Las citas traen `start_time` en ISO con zona."""
    past = [a for a in appts if a["start_time"][:16] < now_iso[:16]]
    nxt = [a for a in appts if a["start_time"][:16] >= now_iso[:16]]
    out: dict = {"visits": len(past)}
    if past:
        docs, sites = Counter(a["provider_id"] for a in past), Counter(a["location_id"] for a in past)
        doc, n = docs.most_common(1)[0]
        if len(past) >= 2 and n * 2 > len(past):
            out["usual_doctor"] = {"provider_id": doc, "name": prov_name(doc), "visits_with_them": n}
        elif len(docs) > 1:
            out["usual_doctor"] = "none: seen by several doctors"
        out["last_seen_by"] = prov_name(past[-1]["provider_id"])
        out["last_visit"] = past[-1]["start_time"][:7]
        out["usual_site"] = site_name(sites.most_common(1)[0][0]) if len(sites) == 1 else "several sites: if they say 'the usual place', ask which"
    if nxt:
        out["upcoming"] = len(nxt)
    return out


def para_planificador(note: str, hist: dict) -> dict:
    """Lo que ve el planificador de la ficha: hechos del historial y cómo tratar a esta persona."""
    out = dict(hist)
    tips = [tip for _, tip in trato(note)]
    if tips:
        out["how_to_treat_them"] = tips
    if hist.get("visits", 0) >= 1:
        out["never_ask"] = "whether they have been here before (they have), nor offer a first visit"
    return out


if __name__ == "__main__":
    import json
    import sys
    pats = json.load(open(sys.argv[1]))
    sin, por = Counter(), Counter()
    for p in pats:
        hits = trato(p.get("note") or "")
        for c, _ in hits:
            por[c] += 1
        if not hits:
            # lo que queda al quitar las frases de historial: si hay algo, es una pauta sin cubrir
            resto = [s for s in re.split(r"(?<=\.)\s+", p.get("note") or "") if not re.search(
                r"visit|seen|chart|since|clinic|referral|history|regular|attend|Arenal|on file|coming here|goes back|ahead|saw them|"
                r"^(Dra?\.? )?[A-ZÁÉÍÓÚ][\wáéíóúñ]+( [A-ZÁÉÍÓÚ][\wáéíóúñ]+){1,2}\.$", s, re.I)]
            for s in resto:
                sin[s] += 1
    print(f"{len(pats)} fichas · con pauta de trato: {sum(1 for p in pats if trato(p.get('note') or ''))}")
    for c, n in por.most_common():
        print(f"  {n:4d}  {c}")
    print("frases sin pauta:", dict(sin.most_common(15)) or "ninguna")
