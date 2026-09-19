"""Frases del agente (inglés por defecto; castellano y catalán para el problema 11).
Fechas, horas y nombres se componen en código."""
from __future__ import annotations

import itertools
from datetime import datetime

WD = {"en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
      "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
      "ca": ["dilluns", "dimarts", "dimecres", "dijous", "divendres", "dissabte", "diumenge"]}
MO = {"en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
      "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
      "ca": ["gener", "febrer", "març", "abril", "maig", "juny", "juliol", "agost", "setembre", "octubre", "novembre", "desembre"]}


def when(lang: str, dt: datetime) -> str:
    h = f"{dt.hour}:{dt.minute:02d}"
    if lang == "es":
        return f"el {WD['es'][dt.weekday()]} {dt.day} de {MO['es'][dt.month - 1]} a las {h}"
    if lang == "ca":
        m = MO["ca"][dt.month - 1]
        return f"{WD['ca'][dt.weekday()]} {dt.day} {'d’' if m[0] in 'aeiou' else 'de '}{m} a les {h}"
    ampm = f"{dt.hour % 12 or 12}{':' + f'{dt.minute:02d}' if dt.minute else ''} {'am' if dt.hour < 12 else 'pm'}"
    return f"{WD['en'][dt.weekday()]} the {_ord(dt.day)} of {MO['en'][dt.month - 1]} at {ampm}"


def day_name(lang: str, dt) -> str:
    if lang == "es":
        return f"El {WD['es'][dt.weekday()]} {dt.day}"
    if lang == "ca":
        return f"El {WD['ca'][dt.weekday()]} {dt.day}"
    return f"On {WD['en'][dt.weekday()]} the {_ord(dt.day)}"


def _ord(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def spell(s: str) -> str:
    """Para leer un DNI o un correo carácter a carácter."""
    return " ".join(s)


T = {
    "greet": {"en": ["Good {daypart}, {clinic}, you're through to reception. How can I help you today?"],
              "es": ["{clinic}, {saludo_es}. ¿En qué puedo ayudarle?"], "ca": ["{clinic}, {saludo_ca}. En què el puc ajudar?"]},
    "ask_need": {"en": ["What can I do for you today?"], "es": ["Dígame, ¿en qué puedo ayudarle?"], "ca": ["Digui’m, en què el puc ajudar?"]},
    "ask_identity": {"en": ["Could I take the patient's full name, and either their DNI or NIE, or the phone number on their file?"],
                     "es": ["¿Me dice el nombre completo del paciente y su DNI o NIE, o el teléfono de su ficha?"],
                     "ca": ["Em pot dir el nom complet del pacient i el seu DNI o NIE, o el telèfon de la fitxa?"]},
    "ask_identity_self": {"en": ["Could I take your full name, and either your DNI or NIE, or the phone number on your file?"],
                          "es": ["¿Me dice su nombre completo y su DNI o NIE, o el teléfono de su ficha?"],
                          "ca": ["Em pot dir el seu nom complet i el DNI o NIE, o el telèfon de la fitxa?"]},
    "ask_patient_identity": {"en": ["And who is the appointment for? Their full name and date of birth, please."],
                             "es": ["¿Y para quién es la cita? Su nombre completo y su fecha de nacimiento, por favor."],
                             "ca": ["I per a qui és la cita? El nom complet i la data de naixement, si us plau."]},
    "ask_second_id": {"en": ["Thanks. And to be sure I have the right record, could I have the DNI, or the date of birth?"],
                      "es": ["Gracias. Para asegurarme de que es la ficha correcta, ¿me da el DNI o la fecha de nacimiento?"],
                      "ca": ["Gràcies. Per assegurar-me que és la fitxa correcta, em dona el DNI o la data de naixement?"]},
    "ask_dob": {"en": ["I have more than one {name} on file. Could you give me the date of birth, or the DNI?"],
                "es": ["Tengo más de una ficha a nombre de {name}. ¿Me da la fecha de nacimiento o el DNI?"],
                "ca": ["Tinc més d’una fitxa a nom de {name}. Em dona la data de naixement o el DNI?"]},
    "repeat_id": {"en": ["I can't find that number. Could you read me the DNI again, digit by digit, with the letter at the end?"],
                  "es": ["No encuentro ese número. ¿Me repite el DNI, cifra a cifra, con la letra al final?"],
                  "ca": ["No trobo aquest número. Em repeteix el DNI, xifra a xifra, amb la lletra al final?"]},
    "not_found": {"en": ["I'm sorry, I can't find a record with those details. Would you like me to register you as a new patient?"],
                  "es": ["Lo siento, no encuentro ninguna ficha con esos datos. ¿Quiere que le dé de alta como paciente nuevo?"],
                  "ca": ["Ho sento, no trobo cap fitxa amb aquestes dades. Vol que el doni d’alta com a pacient nou?"]},
    "identified": {"en": ["Thank you, {first}, I have your record here."], "es": ["Gracias, {first}, ya tengo su ficha."], "ca": ["Gràcies, {first}, ja tinc la seva fitxa."]},
    "identified_other": {"en": ["Thank you, I have {first}'s record here."], "es": ["Gracias, ya tengo la ficha de {first}."], "ca": ["Gràcies, ja tinc la fitxa."]},
    "ask_specialty": {"en": ["Which kind of appointment is it: a GP, or one of our specialists?"],
                      "es": ["¿Qué tipo de cita necesita: médico de familia o algún especialista?"],
                      "ca": ["Quin tipus de cita necessita: metge de família o algun especialista?"]},
    "ask_which_provider": {"en": ["Just to check, do you mean {a} or {b}?"], "es": ["Para confirmar, ¿se refiere a {a} o a {b}?"], "ca": ["Per confirmar, es refereix a {a} o a {b}?"]},
    "offer": {"en": ["The earliest I have {constraint}is {when} with {provider} at {site}. Shall I book that for you?"],
              "es": ["Lo primero que tengo {constraint}es {when} con {provider} en {site}. ¿Se la reservo?"],
              "ca": ["El primer que tinc {constraint}és {when} amb {provider} a {site}. L’hi reservo?"]},
    "offer_closed": {"en": ["{day} we're closed{where}. The earliest after that {constraint}is {when} with {provider} at {site}. Would that work?"],
                     "es": ["{day} estamos cerrados{where}. Lo primero después {constraint}es {when} con {provider} en {site}. ¿Le va bien?"],
                     "ca": ["{day} tenim tancat{where}. El primer després {constraint}és {when} amb {provider} a {site}. Li va bé?"]},
    "offer_fallback": {"en": ["{reason} The earliest with another {specialty} at {site} is {when} with {provider}. Would you like that instead?"],
                       "es": ["{reason} Lo primero con otro profesional de {specialty} en {site} es {when} con {provider}. ¿Le va bien?"],
                       "ca": ["{reason} El primer amb un altre professional de {specialty} a {site} és {when} amb {provider}. Li va bé?"]},
    "booked": {"en": ["That's booked: {when} with {provider} at {site}. Is there anything else I can help with?"],
               "es": ["Queda reservada: {when} con {provider} en {site}. ¿Algo más?"], "ca": ["Queda reservada: {when} amb {provider} a {site}. Alguna cosa més?"]},
    "rescheduled": {"en": ["Done, your appointment is now {when} with {provider} at {site}. Anything else?"],
                    "es": ["Hecho, su cita queda {when} con {provider} en {site}. ¿Algo más?"], "ca": ["Fet, la cita queda {when} amb {provider} a {site}. Alguna cosa més?"]},
    "cancelled": {"en": ["That appointment is cancelled. Anything else I can do?"], "es": ["La cita queda anulada. ¿Algo más?"], "ca": ["La cita queda anul·lada. Alguna cosa més?"]},
    "which_appt": {"en": ["I can see {options}. Which one do you mean?"], "es": ["Veo {options}. ¿Cuál de ellas?"], "ca": ["Veig {options}. Quina d’elles?"]},
    "confirm_cancel": {"en": ["So that's the {appt}. Shall I cancel it?"], "es": ["Entonces es la {appt}. ¿La anulo?"], "ca": ["Doncs és la {appt}. L’anul·lo?"]},
    "no_upcoming": {"en": ["I can't see any upcoming appointments on that record."], "es": ["No veo ninguna cita pendiente en esa ficha."], "ca": ["No veig cap cita pendent en aquesta fitxa."]},
    "refuse": {"en": ["I'm sorry, I can't book that: {why} Is there anything else I can help with?"],
               "es": ["Lo siento, no puedo reservarla: {why} ¿Le ayudo con algo más?"], "ca": ["Ho sento, no la puc reservar: {why} El puc ajudar amb res més?"]},
    "ask_other_plan": {"en": ["Your {plan} plan doesn't cover {what}. Do you have any other insurance I could use?"],
                       "es": ["Su seguro {plan} no cubre {what}. ¿Tiene algún otro seguro?"], "ca": ["La seva assegurança {plan} no cobreix {what}. En té alguna altra?"]},
    "ask_which_plan": {"en": ["Which insurer is that?"], "es": ["¿Qué aseguradora es?"], "ca": ["Quina asseguradora és?"]},
    "no_slots": {"en": ["I'm sorry, there's nothing free {constraint}in the diary."], "es": ["Lo siento, no hay nada libre {constraint}en la agenda."],
                 "ca": ["Ho sento, no hi ha res lliure {constraint}a l’agenda."]},
    "emergency": {"en": ["What you're describing needs urgent attention, not an appointment. Please hang up and call one one two now, or go to the nearest emergency department. I'm flagging this call for our medical team."],
                  "es": ["Lo que me describe necesita atención urgente, no una cita. Cuelgue y llame ahora al uno uno dos, o vaya a urgencias. Dejo el aviso a nuestro equipo médico."],
                  "ca": ["El que em descriu necessita atenció urgent, no una cita. Pengi i truqui ara al u u dos, o vagi a urgències. Deixo l’avís al nostre equip mèdic."]},
    "decline": {"en": ["I'm sorry, I can't help with that. I can only help with appointments, and I can't share anything about other patients or give medical advice."],
                "es": ["Lo siento, en eso no puedo ayudarle. Solo gestiono citas, y no puedo dar datos de otros pacientes ni consejo médico."],
                "ca": ["Ho sento, amb això no el puc ajudar. Només gestiono cites, i no puc donar dades d’altres pacients ni consell mèdic."]},
    # cada negativa dice QUÉ no se puede hacer (no la lista entera) y, si hay una salida, la ofrece
    "decline_other_patient": {"en": ["I'm sorry, I can't share anything about another patient, not even whether they're on our list or have an appointment. They're very welcome to call us themselves."],
                              "es": ["Lo siento, no puedo dar ningún dato de otro paciente, ni siquiera si está en nuestras fichas o si tiene cita. La propia persona puede llamarnos cuando quiera."],
                              "ca": ["Ho sento, no puc donar cap dada d’un altre pacient, ni tan sols si és a les nostres fitxes o si té cita. La mateixa persona ens pot trucar quan vulgui."]},
    "decline_medical": {"en": ["I'm afraid I can't give medical advice over the phone, but one of our doctors can see you. Would you like me to book you an appointment?"],
                        "es": ["Lo siento, por teléfono no puedo dar consejo médico, pero uno de nuestros médicos puede verle. ¿Quiere que le dé cita?"],
                        "ca": ["Ho sento, per telèfon no puc donar consell mèdic, però un dels nostres metges el pot visitar. Vol que li doni hora?"]},
    "decline_sales": {"en": ["Thanks for calling, but this line is only for patients' appointments, so I can't take sales calls or pass on anyone's contact details."],
                      "es": ["Gracias por llamar, pero esta línea es solo para las citas de los pacientes: no atiendo llamadas comerciales ni puedo dar datos de contacto del personal."],
                      "ca": ["Gràcies per trucar, però aquesta línia és només per a les cites dels pacients: no atenc trucades comercials ni puc donar dades de contacte del personal."]},
    "decline_injection": {"en": ["I'm sorry, I can't do that. On this line I can only help with appointments."],
                          "es": ["Lo siento, eso no puedo hacerlo. En esta línea solo gestiono citas."],
                          "ca": ["Ho sento, això no ho puc fer. En aquesta línia només gestiono cites."]},
    "decline_unrelated": {"en": ["I'm sorry, that's not something I can help with. I can book, change or cancel appointments, or answer questions about the clinic."],
                          "es": ["Lo siento, con eso no puedo ayudarle. Puedo dar, cambiar o anular citas, o resolver dudas sobre la clínica."],
                          "ca": ["Ho sento, amb això no el puc ajudar. Puc donar, canviar o anul·lar cites, o resoldre dubtes sobre la clínica."]},
    # si insiste en lo mismo: corto, sin repetir el discurso entero
    "decline_again": {"en": ["I understand, but I'm really not able to help with that on this line."],
                      "es": ["Le entiendo, pero de verdad que eso no puedo hacerlo por esta línea."],
                      "ca": ["L’entenc, però de debò que això no ho puc fer per aquesta línia."]},
    "decline_again_other_patient": {"en": ["I understand, but I really can't share anything about other patients, whoever is asking."],
                                    "es": ["Le entiendo, pero no puedo dar datos de otros pacientes, lo pida quien lo pida."],
                                    "ca": ["L’entenc, però no puc donar dades d’altres pacients, ho demani qui ho demani."]},
    "decline_again_medical": {"en": ["I understand it's uncomfortable, but only a doctor can tell you that. I can book you in with one, if you like."],
                              "es": ["Entiendo que es molesto, pero eso solo puede decírselo un médico. Si quiere, le doy cita con uno."],
                              "ca": ["Entenc que és molest, però això només l’hi pot dir un metge. Si vol, li dono hora amb un."]},
    "greet_back": {"en": ["Hi there, yes. What can I do for you?"], "es": ["Hola, sí, dígame. ¿En qué puedo ayudarle?"], "ca": ["Hola, sí, digui’m. En què el puc ajudar?"]},
    "still_here": {"en": ["Yes, I'm still here."], "es": ["Sí, sigo aquí."], "ca": ["Sí, encara hi soc."]},
    "id_incomplete": {"en": ["Sorry, I only caught part of the DNI. Could you read me the whole number, with the letter at the end?"],
                      "es": ["Perdone, solo he cogido una parte del DNI. ¿Me dice el número entero, con la letra al final?"],
                      "ca": ["Perdoni, només he agafat una part del DNI. Em diu el número sencer, amb la lletra al final?"]},
    "repeat": {"en": ["Sorry, the line broke up a little. Could you say that again?"], "es": ["Perdone, se ha cortado un poco. ¿Me lo repite?"], "ca": ["Perdoni, s’ha tallat una mica. M’ho repeteix?"]},
    "anything_else": {"en": ["Is there anything else I can help with?"], "es": ["¿Puedo ayudarle en algo más?"], "ca": ["El puc ajudar amb res més?"]},
    "goodbye": {"en": ["Thank you for calling. Take care, goodbye."], "es": ["Gracias por llamar. Que vaya bien, adiós."], "ca": ["Gràcies per trucar. Que vagi bé, adéu."]},
    "hold": {"en": ["One moment while I check."], "es": ["Un momento, que lo miro."], "ca": ["Un moment, que ho miro."]},
    "reg_intro": {"en": ["Of course, I'll register you. Let's go step by step."], "es": ["Claro, le doy de alta. Vamos paso a paso."], "ca": ["És clar, el dono d’alta. Anem pas a pas."]},
    "reg_ask": {"en": {"given_name": "What's your first name?", "surnames": "And your two surnames?", "national_id": "Your DNI or NIE, with the letter?",
                       "date_of_birth": "Your date of birth?", "phone": "A contact phone number?", "email": "And an email address? Please spell it for me.",
                       "insurer": "And which insurer are you with, or are you private?"},
                "es": {"given_name": "¿Su nombre?", "surnames": "¿Y sus dos apellidos?", "national_id": "¿Su DNI o NIE, con la letra?", "date_of_birth": "¿Su fecha de nacimiento?",
                       "phone": "¿Un teléfono de contacto?", "email": "¿Y un correo electrónico? Deletréemelo, por favor.", "insurer": "¿Y con qué aseguradora está, o es privado?"},
                "ca": {"given_name": "El seu nom?", "surnames": "I els dos cognoms?", "national_id": "El DNI o NIE, amb la lletra?", "date_of_birth": "La data de naixement?",
                       "phone": "Un telèfon de contacte?", "email": "I un correu electrònic? Me’l lletreja, si us plau?", "insurer": "I amb quina asseguradora és, o és privat?"}},
    "reg_readback": {"en": ["Let me read that back: {summary}. Is all of that correct?"], "es": ["Le leo los datos: {summary}. ¿Es todo correcto?"], "ca": ["Li llegeixo les dades: {summary}. És tot correcte?"]},
    "reg_fix": {"en": ["No problem. What needs correcting?"], "es": ["Sin problema. ¿Qué hay que corregir?"], "ca": ["Cap problema. Què cal corregir?"]},
    "reg_done": {"en": ["You're registered with us now. Would you like to book an appointment while you're on the line?"],
                 "es": ["Ya está dado de alta. ¿Quiere pedir una cita ahora?"], "ca": ["Ja està donat d’alta. Vol demanar una cita ara?"]},
    "reg_phone_groups": {"en": ["Sorry, I didn't catch all nine digits. Could you say the number slowly, in groups of three?"],
                         "es": ["Perdone, no he cogido las nueve cifras. ¿Me dice el número despacio, de tres en tres?"],
                         "ca": ["Perdoni, no he agafat les nou xifres. Me’l diu a poc a poc, de tres en tres?"]},
    "reoffer": {"en": ["That's {when} with {provider} at {site}. Shall I book it for you?"],
                "es": ["Sería {when} con {provider} en {site}. ¿Se la reservo?"],
                "ca": ["Seria {when} amb {provider} a {site}. Li reservo?"]},
    "id_letter_bad": {"en": ["I think I misheard the DNI, because the letter doesn't match the numbers. Could you read it again?"],
                      "es": ["Creo que he oído mal el DNI: la letra no cuadra con los números. ¿Me lo repite?"],
                      "ca": ["Crec que he sentit malament el DNI: la lletra no quadra amb els números. M’ho repeteix?"]},
}
_rot = itertools.count()


def say(key: str, lang: str, **kw) -> str:
    v = T[key].get(lang) or T[key]["en"]
    return v[next(_rot) % len(v)].format(**kw)


def reg_ask(field: str, lang: str) -> str:
    return (T["reg_ask"].get(lang) or T["reg_ask"]["en"])[field]
