"""Frases del agente en cinco lenguas. El agente solo puede decir lo que está aquí
(más las respuestas vigiladas del Sistema 2). Fechas y horas se formatean en código."""
from __future__ import annotations

import itertools
from datetime import datetime

from clinic import DOCTORS, SERVICES, SITES, Appointment, Doctor

LANGS = ["es", "ca", "gl", "eu", "en"]
STT_CODES = {"es": "es-ES", "ca": "ca-ES", "gl": "gl-ES", "eu": "eu-ES", "en": "en-US"}

WD = {
    "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "ca": ["dilluns", "dimarts", "dimecres", "dijous", "divendres", "dissabte", "diumenge"],
    "gl": ["luns", "martes", "mércores", "xoves", "venres", "sábado", "domingo"],
    "eu": ["astelehenean", "asteartean", "asteazkenean", "ostegunean", "ostiralean", "larunbatean", "igandean"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}
MO = {
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "ca": ["gener", "febrer", "març", "abril", "maig", "juny", "juliol", "agost", "setembre", "octubre", "novembre", "desembre"],
    "gl": ["xaneiro", "febreiro", "marzo", "abril", "maio", "xuño", "xullo", "agosto", "setembro", "outubro", "novembro", "decembro"],
    "eu": ["urtarrilaren", "otsailaren", "martxoaren", "apirilaren", "maiatzaren", "ekainaren", "uztailaren", "abuztuaren", "irailaren", "urriaren", "azaroaren", "abenduaren"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
}


def when(lang: str, s: datetime) -> str:
    wd, d, mo, hm = s.weekday(), s.day, s.month - 1, f"{s.hour}:{s.minute:02d}"
    if lang == "es":
        return f"el {WD['es'][wd]} {d} de {MO['es'][mo]} a {'la' if s.hour == 1 else 'las'} {hm}"
    if lang == "ca":
        m = MO["ca"][mo]
        de = "d'" if m[0] in "aeiou" else "de "
        return f"{WD['ca'][wd]} {d} {de}{m} a les {hm}"
    if lang == "gl":
        return f"o {WD['gl'][wd]} {d} de {MO['gl'][mo]} ás {hm}"
    if lang == "eu":
        return f"{WD['eu'][wd]}, {MO['eu'][mo]} {d}an, {hm}etan"
    return f"on {WD['en'][wd]} {d} {MO['en'][mo]} at {hm}"


def doctor_with(lang: str, doc: Doctor) -> str:
    fem = doc.title == "dra"
    return {
        "es": f"con {'la doctora' if fem else 'el doctor'} {doc.surname}",
        "ca": f"amb {'la doctora' if fem else 'el doctor'} {doc.surname}",
        "gl": f"{'coa doutora' if fem else 'co doutor'} {doc.surname}",
        "eu": f"{doc.surname} doktorearekin",
        "en": f"with Doctor {doc.surname}",
    }[lang]


def site_at(lang: str, site: str) -> str:
    n = SITES[site]["name"]
    return {"es": f"en la sede de {n}", "ca": f"a la seu de {n}", "gl": f"na sede de {n}",
            "eu": f"{n}ko egoitzan", "en": f"at our {n} site"}[lang]


def who_is_full(lang: str, doc: Doctor | None, service: str) -> str:
    """Sujeto de «… no tiene ningún hueco libre»."""
    if doc:
        fem = doc.title == "dra"
        return {"es": f"{'la doctora' if fem else 'el doctor'} {doc.surname}", "ca": f"{'la doctora' if fem else 'el doctor'} {doc.surname}",
                "gl": f"{'a doutora' if fem else 'o doutor'} {doc.surname}", "eu": f"{doc.surname} doktoreak", "en": f"Doctor {doc.surname}"}[lang]
    sv = service_name(lang, service)
    return {"es": f"la agenda de {sv}", "ca": f"l'agenda de {sv}", "gl": f"a axenda de {sv}",
            "eu": f"{sv}ko agendak", "en": f"the {sv} diary"}[lang]


def service_name(lang: str, service: str) -> str:
    return SERVICES.get(service, {}).get(lang, service)


def slot_phrase(lang: str, doc_id: str, s: datetime, with_doctor: bool = True) -> str:
    base = when(lang, s)
    return f"{base} {doctor_with(lang, DOCTORS[doc_id])}" if with_doctor else base


def at_time(lang: str, s: datetime) -> str:
    hm = f"{s.hour}:{s.minute:02d}"
    return {"es": f"a {'la' if s.hour == 1 else 'las'} {hm}", "ca": f"a les {hm}", "gl": f"ás {hm}", "eu": f"{hm}etan", "en": f"at {hm}"}[lang]


def offer_options(lang: str, slots: list) -> str:
    """Las opciones sin repetir el día cuando coincide con la anterior."""
    items, prev = [], None
    for doc_id, s in slots:
        if prev and prev.date() == s.date():
            items.append(f"{at_time(lang, s)} {doctor_with(lang, DOCTORS[doc_id])}")
        else:
            ph = slot_phrase(lang, doc_id, s)
            items.append(ph[3:] if lang == "en" and ph.startswith("on ") else ph)
        prev = s
    return join_or(lang, items)


def join_or(lang: str, items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    conj = {"es": "o", "ca": "o", "gl": "ou", "eu": "edo", "en": "or"}[lang]
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


def appt_phrase(lang: str, a: Appointment) -> str:
    return f"{service_name(lang, a.service)} {when(lang, a.start)}"


T: dict[str, dict[str, list[str]]] = {
    "greet": {
        "es": ["Clínica Prosper, buenos días. ¿En qué puedo ayudarle?"],
        "ca": ["Clínica Prosper, bon dia. En què el puc ajudar?"],
        "gl": ["Clínica Prosper, bo día. En que podo axudarlle?"],
        "eu": ["Prosper klinika, egun on. Zertan lagun zaitzaket?"],
        "en": ["Prosper Clinic, good morning. How can I help you?"],
    },
    "ask_need": {
        "es": ["Dígame, ¿en qué puedo ayudarle?", "¿Qué necesita? ¿Pedir, cambiar o anular una cita?"],
        "ca": ["Digui'm, en què el puc ajudar?"],
        "gl": ["Dígame, en que podo axudarlle?"],
        "eu": ["Esan, zertan lagun zaitzaket?"],
        "en": ["Sure, what can I do for you?"],
    },
    "ask_identity_self": {
        "es": ["¿Me dice su nombre completo y su fecha de nacimiento, por favor?"],
        "ca": ["Em pot dir el seu nom complet i la data de naixement, si us plau?"],
        "gl": ["Pode dicirme o seu nome completo e a data de nacemento, por favor?"],
        "eu": ["Esango didazu zure izen-abizenak eta jaiotze-data, mesedez?"],
        "en": ["Could I have your full name and date of birth, please?"],
    },
    "ask_identity_other": {
        "es": ["De acuerdo. ¿Me dice el nombre completo y la fecha de nacimiento de la persona que vendría a la consulta?"],
        "ca": ["D'acord. Em pot dir el nom complet i la data de naixement de la persona que vindria a la consulta?"],
        "gl": ["De acordo. Pode dicirme o nome completo e a data de nacemento da persoa que viría á consulta?"],
        "eu": ["Ados. Esango didazu kontsultara etorriko den pertsonaren izen-abizenak eta jaiotze-data?"],
        "en": ["Of course. Could I have the full name and date of birth of the person who'll be coming in?"],
    },
    "ask_name": {
        "es": ["¿Y el nombre completo, por favor?"],
        "ca": ["I el nom complet, si us plau?"],
        "gl": ["E o nome completo, por favor?"],
        "eu": ["Eta izen-abizenak, mesedez?"],
        "en": ["And the full name, please?"],
    },
    "ask_dob": {
        "es": ["Gracias. ¿Y la fecha de nacimiento?"],
        "ca": ["Gràcies. I la data de naixement?"],
        "gl": ["Grazas. E a data de nacemento?"],
        "eu": ["Eskerrik asko. Eta jaiotze-data?"],
        "en": ["Thank you. And the date of birth?"],
    },
    "disambiguate_dob": {
        "es": ["Tengo varias fichas con ese nombre. ¿Me confirma la fecha de nacimiento?"],
        "ca": ["Tinc diverses fitxes amb aquest nom. Em confirma la data de naixement?"],
        "gl": ["Teño varias fichas con ese nome. Pode confirmarme a data de nacemento?"],
        "eu": ["Izen horrekin fitxa bat baino gehiago dauzkat. Jaiotze-data baieztatuko didazu?"],
        "en": ["I have more than one record with that name. Could you confirm the date of birth?"],
    },
    "disambiguate_phone": {
        "es": ["Tengo más de una ficha que encaja. ¿Me dice los cuatro últimos números del teléfono?"],
        "ca": ["Tinc més d'una fitxa que encaixa. Em diu les quatre últimes xifres del telèfon?"],
        "gl": ["Teño máis dunha ficha que encaixa. Pode dicirme os catro últimos números do teléfono?"],
        "eu": ["Fitxa bat baino gehiago datoz bat. Telefonoaren azken lau zenbakiak esango dizkidazu?"],
        "en": ["More than one record matches. Could you tell me the last four digits of the phone number?"],
    },
    "disambiguate_surname2": {
        "es": ["Tengo más de una ficha que encaja. ¿Me dice el segundo apellido?"],
        "ca": ["Tinc més d'una fitxa que encaixa. Em diu el segon cognom?"],
        "gl": ["Teño máis dunha ficha que encaixa. Pode dicirme o segundo apelido?"],
        "eu": ["Fitxa bat baino gehiago datoz bat. Bigarren abizena esango didazu?"],
        "en": ["More than one record matches. Could you tell me the second surname?"],
    },
    "not_found_offer_new": {
        "es": ["No encuentro ninguna ficha con esos datos, así que le doy de alta ahora. ¿Me dice un teléfono de contacto?"],
        "ca": ["No trobo cap fitxa amb aquestes dades, així que el dono d'alta ara. Em diu un telèfon de contacte?"],
        "gl": ["Non atopo ningunha ficha con eses datos, así que dou de alta agora. Pode dicirme un teléfono de contacto?"],
        "eu": ["Ez dut datu horiekin fitxarik aurkitzen; beraz, orain emango dizut alta. Harremanetarako telefono bat emango didazu?"],
        "en": ["I can't find a record with those details, so I'll register you now. Could I have a contact phone number?"],
    },
    "identity_unresolved": {
        "es": ["Lo siento, no consigo identificar la ficha con seguridad por teléfono. Le recomiendo pasar por recepción con su DNI y lo resolvemos allí."],
        "ca": ["Ho sento, no aconsegueixo identificar la fitxa amb seguretat per telèfon. Li recomano passar per recepció amb el DNI i ho resolem allà."],
        "gl": ["Síntoo, non consigo identificar a ficha con seguridade por teléfono. Recoméndolle pasar pola recepción co DNI e resolvémolo alí."],
        "eu": ["Sentitzen dut, ezin dut fitxa ziurtasunez identifikatu telefonoz. Harrerara NANarekin etortzea gomendatzen dizut, eta han konponduko dugu."],
        "en": ["I'm sorry, I can't safely identify the record over the phone. Please come to the front desk with your ID and we'll sort it out there."],
    },
    "greet_patient": {
        "es": ["Gracias, {first}."], "ca": ["Gràcies, {first}."], "gl": ["Grazas, {first}."],
        "eu": ["Eskerrik asko, {first}."], "en": ["Thank you, {first}."],
    },
    "greet_patient_other": {
        "es": ["Gracias, ya tengo la ficha de {first}."], "ca": ["Gràcies, ja tinc la fitxa."], "gl": ["Grazas, xa teño a ficha."],
        "eu": ["Eskerrik asko, fitxa aurkitu dut."], "en": ["Thanks, I've found {first}'s record."],
    },
    "ask_service": {
        "es": ["¿Para qué especialidad sería?"],
        "ca": ["Per a quina especialitat seria?"],
        "gl": ["Para que especialidade sería?"],
        "eu": ["Zein espezialitatetarako izango litzateke?"],
        "en": ["Which specialty would it be for?"],
    },
    "ask_when": {
        "es": ["¿Qué día y a qué hora le vendría bien?", "¿Tiene preferencia de día u hora?"],
        "ca": ["Quin dia i a quina hora li aniria bé?"],
        "gl": ["Que día e a que hora lle viría ben?"],
        "eu": ["Zein egun eta ordutan etorriko litzaizuke ondo?"],
        "en": ["What day and time would suit you?"],
    },
    "offer": {
        "es": ["Tengo {options}. ¿Cuál prefiere?"],
        "ca": ["Tinc {options}. Quina prefereix?"],
        "gl": ["Teño {options}. Cal prefire?"],
        "eu": ["Hauek dauzkat: {options}. Zein nahiago duzu?"],
        "en": ["I have {options}. Which would you prefer?"],
    },
    "offer_one": {
        "es": ["Tengo {options}. ¿Le va bien?"],
        "ca": ["Tinc {options}. Li va bé?"],
        "gl": ["Teño {options}. Vállelle ben?"],
        "eu": ["Hau daukat: {options}. Ondo datorkizu?"],
        "en": ["I have {options}. Does that work for you?"],
    },
    "which_one": {
        "es": ["¿Cuál de ellas le viene mejor?"], "ca": ["Quina li va millor?"], "gl": ["Cal delas lle vén mellor?"],
        "eu": ["Zein datorkizu hobeto?"], "en": ["Which one suits you best?"],
    },
    "offer_alt": {
        "es": ["Ese día no me queda hueco. Lo más cercano que tengo es {options}. ¿Le sirve alguno?"],
        "ca": ["Aquell dia no em queda cap forat. El més proper que tinc és {options}. Li va bé algun?"],
        "gl": ["Ese día non me queda oco. O máis próximo que teño é {options}. Válelle algún?"],
        "eu": ["Egun horretan ez dut hutsunerik. Hurbilena hau da: {options}. Ondo datorkizu?"],
        "en": ["There's nothing left that day. The closest I have is {options}. Would any of those work?"],
    },
    "readback_book": {
        "es": ["Le leo la cita: {patient}, {service}, {doctor_with}, {when}, {site}. ¿Se la confirmo?"],
        "ca": ["Li llegeixo la cita: {patient}, {service}, {doctor_with}, {when}, {site}. L'hi confirmo?"],
        "gl": ["Léolle a cita: {patient}, {service}, {doctor_with}, {when}, {site}. Confírmolla?"],
        "eu": ["Hitzordua irakurriko dizut: {patient}, {service}, {doctor_with}, {when}, {site}. Baieztatuko dut?"],
        "en": ["Let me read it back: {patient}, {service}, {doctor_with}, {when}, {site}. Shall I confirm it?"],
    },
    "readback_move": {
        "es": ["Entonces le cambio la cita de {old} a {when}, {doctor_with}. ¿Lo confirmo?"],
        "ca": ["Aleshores li canvio la cita de {old} a {when}, {doctor_with}. Ho confirmo?"],
        "gl": ["Entón cámbiolle a cita de {old} a {when}, {doctor_with}. Confírmoo?"],
        "eu": ["Orduan, {old} hitzordua {when} aldatuko dizut, {doctor_with}. Baieztatuko dut?"],
        "en": ["So I'll move your appointment from {old} to {when}, {doctor_with}. Shall I confirm?"],
    },
    "readback_cancel": {
        "es": ["Voy a anular su cita de {appt}. ¿Lo confirmo?"],
        "ca": ["Anul·laré la seva cita de {appt}. Ho confirmo?"],
        "gl": ["Vou anular a súa cita de {appt}. Confírmoo?"],
        "eu": ["{appt} hitzordua ezeztatuko dut. Baieztatuko dut?"],
        "en": ["I'm going to cancel your {appt} appointment. Shall I confirm?"],
    },
    "done_book": {
        "es": ["Hecho, {first}. Queda reservada {when}, {doctor_with}. ¿Necesita algo más?"],
        "ca": ["Fet, {first}. Queda reservada {when}, {doctor_with}. Necessita res més?"],
        "gl": ["Feito, {first}. Queda reservada {when}, {doctor_with}. Precisa algo máis?"],
        "eu": ["Eginda, {first}. Hitzordua hartuta dago: {when}, {doctor_with}. Beste zerbait behar duzu?"],
        "en": ["Done, {first}. You're booked {when}, {doctor_with}. Anything else?"],
    },
    "done_move": {
        "es": ["Hecho, ya está cambiada a {when}. ¿Necesita algo más?"],
        "ca": ["Fet, ja està canviada a {when}. Necessita res més?"],
        "gl": ["Feito, xa está cambiada a {when}. Precisa algo máis?"],
        "eu": ["Eginda, {when} aldatuta dago. Beste zerbait behar duzu?"],
        "en": ["Done, it's now {when}. Anything else?"],
    },
    "done_cancel": {
        "es": ["Hecho, la cita queda anulada. ¿Necesita algo más?"],
        "ca": ["Fet, la cita queda anul·lada. Necessita res més?"],
        "gl": ["Feito, a cita queda anulada. Precisa algo máis?"],
        "eu": ["Eginda, hitzordua ezeztatuta dago. Beste zerbait behar duzu?"],
        "en": ["Done, the appointment is cancelled. Anything else?"],
    },
    "which_appt": {
        "es": ["Veo que tiene estas citas: {options}. ¿Cuál quiere {verb}?"],
        "ca": ["Veig que té aquestes cites: {options}. Quina vol {verb}?"],
        "gl": ["Vexo que ten estas citas: {options}. Cal quere {verb}?"],
        "eu": ["Hitzordu hauek dituzu: {options}. Zein {verb} nahi duzu?"],
        "en": ["I can see these appointments: {options}. Which one would you like to {verb}?"],
    },
    "no_appts": {
        "es": ["No le veo ninguna cita pendiente. ¿Quiere pedir una nueva?"],
        "ca": ["No li veig cap cita pendent. Vol demanar-ne una de nova?"],
        "gl": ["Non lle vexo ningunha cita pendente. Quere pedir unha nova?"],
        "eu": ["Ez dut hitzordu zain ikusten. Berri bat eskatu nahi duzu?"],
        "en": ["I can't see any upcoming appointments. Would you like to book a new one?"],
    },
    "hold_lost": {
        "es": ["Vaya, ese hueco se acaba de ocupar. Le busco otro."],
        "ca": ["Vaja, aquest forat s'acaba d'ocupar. Li busco un altre."],
        "gl": ["Vaia, ese oco acaba de ocuparse. Búscolle outro."],
        "eu": ["Hutsune hori oraintxe bete da. Beste bat bilatuko dizut."],
        "en": ["Oh, that slot has just been taken. Let me find you another."],
    },
    "no_availability": {
        "es": ["Lo siento, {what} no tiene ningún hueco libre en los próximos dos meses, así que ahora no puedo darle cita. Si quiere, le apunto en lista de espera y le llamamos si se libera algo."],
        "ca": ["Ho sento, {what} no té cap forat lliure els pròxims dos mesos, així que ara no li puc donar cita. Si vol, l'apunto a la llista d'espera i el trucarem si s'allibera alguna cosa."],
        "gl": ["Síntoo, {what} non ten ningún oco libre nos próximos dous meses, así que agora non podo darlle cita. Se quere, apúntoo na lista de espera e chamámolo se se libera algo."],
        "eu": ["Sentitzen dut, {what} ez du hutsunerik hurrengo bi hilabeteetan; beraz, orain ezin dizut hitzordurik eman. Nahi baduzu, itxaron-zerrendan jarriko zaitut eta deituko dizugu zerbait askatzen bada."],
        "en": ["I'm sorry, {what} has nothing free in the next two months, so I can't book you in right now. If you like, I can put you on the waiting list and we'll call you if something opens up."],
    },
    "waitlisted": {
        "es": ["Perfecto, queda apuntado en la lista de espera. ¿Necesita algo más?"],
        "ca": ["Perfecte, queda apuntat a la llista d'espera. Necessita res més?"],
        "gl": ["Perfecto, queda apuntado na lista de espera. Precisa algo máis?"],
        "eu": ["Ederki, itxaron-zerrendan jarrita zaude. Beste zerbait behar duzu?"],
        "en": ["Great, you're on the waiting list. Anything else?"],
    },
    "refuse_not_offered": {
        "es": ["Lo siento, esa especialidad no la ofrecemos en la clínica, así que no puedo darle cita para eso. ¿Le ayudo con otra cosa?"],
        "ca": ["Ho sento, aquesta especialitat no l'oferim a la clínica, així que no li puc donar cita. El puc ajudar amb res més?"],
        "gl": ["Síntoo, esa especialidade non a ofrecemos na clínica, así que non podo darlle cita. Pódolle axudar con outra cousa?"],
        "eu": ["Sentitzen dut, espezialitate hori ez dugu klinikan; beraz, ezin dizut hitzordurik eman. Beste zerbaitetan lagun zaitzaket?"],
        "en": ["I'm sorry, we don't offer that specialty at the clinic, so I can't book it. Can I help with anything else?"],
    },
    "refuse_pediatrics_age": {
        "es": ["Lo siento, pediatría solo atiende a menores de catorce años, así que no puedo darle esa cita. Si quiere, le busco hueco en medicina general."],
        "ca": ["Ho sento, pediatria només atén menors de catorze anys, així que no li puc donar aquesta cita. Si vol, li busco forat a medicina general."],
        "gl": ["Síntoo, pediatría só atende a menores de catorce anos, así que non podo darlle esa cita. Se quere, búscolle oco en medicina xeral."],
        "eu": ["Sentitzen dut, pediatriak hamalau urtetik beherakoak bakarrik hartzen ditu; beraz, ezin dizut hitzordu hori eman. Nahi baduzu, medikuntza orokorrean bilatuko dizut hutsunea."],
        "en": ["I'm sorry, paediatrics only sees children under fourteen, so I can't book that. I can look for a general practice appointment instead if you like."],
    },
    "refuse_cardiology_referral": {
        "es": ["Para cardiología necesitamos un volante de su médico de cabecera y no me consta ninguno, así que ahora no puedo darle esa cita. Si quiere, le doy cita en medicina general para que se lo hagan."],
        "ca": ["Per a cardiologia necessitem un volant del metge de capçalera i no me'n consta cap, així que ara no li puc donar aquesta cita. Si vol, li dono cita a medicina general perquè l'hi facin."],
        "gl": ["Para cardioloxía precisamos un volante do seu médico de cabeceira e non me consta ningún, así que agora non podo darlle esa cita. Se quere, doulle cita en medicina xeral para que llo fagan."],
        "eu": ["Kardiologiarako familia-medikuaren bolantea behar dugu, eta ez daukat bat ere erregistratuta; beraz, orain ezin dizut hitzordu hori eman. Nahi baduzu, medikuntza orokorrean emango dizut hitzordua, bolantea egin diezazuten."],
        "en": ["Cardiology needs a referral from your GP and I don't have one on file, so I can't book it right now. I can book you with general practice to get the referral, if you like."],
    },
    "refuse_max_days_ahead": {
        "es": ["Solo puedo dar citas con hasta sesenta días de antelación. Si quiere, le busco lo más cercano dentro de ese plazo."],
        "ca": ["Només puc donar cites amb fins a seixanta dies d'antelació. Si vol, li busco el més proper dins d'aquest termini."],
        "gl": ["Só podo dar citas con ata sesenta días de antelación. Se quere, búscolle o máis próximo dentro dese prazo."],
        "eu": ["Gehienez hirurogei egun lehenago eman ditzaket hitzorduak. Nahi baduzu, epe horren barruan hurbilena bilatuko dizut."],
        "en": ["I can only book up to sixty days ahead. I can look for the closest slot within that window if you like."],
    },
    "refuse_one_per_specialty": {
        "es": ["Ya tiene una cita de {service} pendiente, {when}, y solo se puede tener una por especialidad. Si quiere, se la cambio de día."],
        "ca": ["Ja té una cita de {service} pendent, {when}, i només se'n pot tenir una per especialitat. Si vol, l'hi canvio de dia."],
        "gl": ["Xa ten unha cita de {service} pendente, {when}, e só se pode ter unha por especialidade. Se quere, cámbiolla de día."],
        "eu": ["Badaukazu {service} hitzordu bat zain, {when}, eta espezialitate bakoitzeko bat bakarrik izan daiteke. Nahi baduzu, egunez aldatuko dizut."],
        "en": ["You already have a {service} appointment {when}, and only one per specialty is allowed. I can move it to another day if you like."],
    },
    "refuse_not_authorised": {
        "es": ["Lo siento, solo puedo gestionar las citas del propio paciente, de sus hijos menores o de un familiar autorizado."],
        "ca": ["Ho sento, només puc gestionar les cites del mateix pacient, dels seus fills menors o d'un familiar autoritzat."],
        "gl": ["Síntoo, só podo xestionar as citas do propio paciente, dos seus fillos menores ou dun familiar autorizado."],
        "eu": ["Sentitzen dut, pazientearen beraren, haren seme-alaba adingabeen edo baimendutako senide baten hitzorduak bakarrik kudea ditzaket."],
        "en": ["I'm sorry, I can only manage appointments for the patient, their children under eighteen, or an authorised relative."],
    },
    "emergency": {
        "es": ["Por lo que me cuenta, esto puede ser una urgencia. Cuelgue y llame ahora mismo al uno uno dos. No espere a una cita."],
        "ca": ["Pel que m'explica, això pot ser una urgència. Pengi i truqui ara mateix al u u dos. No esperi a una cita."],
        "gl": ["Polo que me conta, isto pode ser unha urxencia. Colgue e chame agora mesmo ao un un dous. Non agarde a unha cita."],
        "eu": ["Kontatzen didazunagatik, larrialdi bat izan daiteke. Eskegi eta deitu orain bertan bat bat bira. Ez itxaron hitzordu baten zain."],
        "en": ["From what you're describing, this could be an emergency. Please hang up and call one one two right now. Don't wait for an appointment."],
    },
    "emergency_check": {
        "es": ["Antes de seguir: ¿tiene ahora mismo dolor en el pecho, dificultad para respirar o algo que le parezca grave?"],
        "ca": ["Abans de continuar: té ara mateix dolor al pit, dificultat per respirar o alguna cosa que li sembli greu?"],
        "gl": ["Antes de seguir: ten agora mesmo dor no peito, dificultade para respirar ou algo que lle pareza grave?"],
        "eu": ["Jarraitu aurretik: orain bertan bularreko minik, arnasteko zailtasunik edo larria iruditzen zaizun zerbait al duzu?"],
        "en": ["Before we go on: do you have chest pain, trouble breathing, or anything that feels serious right now?"],
    },
    "urgent_today": {
        "es": ["Por lo que me describe, debería verle un médico hoy mismo, y eso no puedo resolverlo con una cita. Acuda hoy a urgencias o a su centro de salud; si empeora, llame al uno uno dos."],
        "ca": ["Pel que m'explica, l'hauria de veure un metge avui mateix, i això no ho puc resoldre amb una cita. Vagi avui a urgències o al seu CAP; si empitjora, truqui al u u dos."],
        "gl": ["Polo que me describe, debería velo un médico hoxe mesmo, e iso non podo resolvelo cunha cita. Acuda hoxe a urxencias ou ao seu centro de saúde; se empeora, chame ao un un dous."],
        "eu": ["Deskribatzen didazunagatik, mediku batek gaur bertan ikusi beharko zintuzke, eta hori ezin dut hitzordu batekin konpondu. Joan gaur larrialdietara edo zure osasun-zentrora; okerrera egiten baduzu, deitu bat bat bira."],
        "en": ["From what you describe, you should be seen by a doctor today, and I can't solve that with an appointment. Please go to A and E or your health centre today; if it gets worse, call one one two."],
    },
    "decline_manipulation": {
        "es": ["Lo siento, no puedo dar información de otros pacientes ni saltarme los procedimientos de la clínica."],
        "ca": ["Ho sento, no puc donar informació d'altres pacients ni saltar-me els procediments de la clínica."],
        "gl": ["Síntoo, non podo dar información doutros pacientes nin saltarme os procedementos da clínica."],
        "eu": ["Sentitzen dut, ezin dut beste pazienteen informaziorik eman, ezta klinikaren prozedurak saltatu ere."],
        "en": ["I'm sorry, I can't share other patients' information or skip the clinic's procedures."],
    },
    "ask_repeat": {
        "es": ["Perdone, se ha cortado un poco. ¿Me lo puede repetir?", "Disculpe, no le he oído bien. ¿Me lo repite, por favor?"],
        "ca": ["Perdoni, s'ha tallat una mica. M'ho pot repetir?"],
        "gl": ["Perdoe, cortouse un pouco. Pode repetirmo?"],
        "eu": ["Barkatu, pixka bat moztu da. Errepikatuko didazu?"],
        "en": ["Sorry, the line cut out a little. Could you say that again?"],
    },
    "ack": {
        "es": ["De acuerdo."], "ca": ["D'acord."], "gl": ["De acordo."], "eu": ["Ados."], "en": ["All right."],
    },
    "anything_else": {
        "es": ["¿Puedo ayudarle en algo más?"], "ca": ["El puc ajudar amb res més?"], "gl": ["Pódolle axudar en algo máis?"],
        "eu": ["Beste zerbaitetan lagun zaitzaket?"], "en": ["Is there anything else I can help with?"],
    },
    "goodbye": {
        "es": ["Gracias por llamar. Que tenga un buen día."],
        "ca": ["Gràcies per trucar. Que tingui un bon dia."],
        "gl": ["Grazas por chamar. Que teña un bo día."],
        "eu": ["Eskerrik asko deitzeagatik. Egun ona izan."],
        "en": ["Thanks for calling. Have a good day."],
    },
    "filler": {
        "es": ["Déjeme mirarlo un momento."], "ca": ["Deixi'm mirar-ho un moment."], "gl": ["Déixeme miralo un momento."],
        "eu": ["Utzidazu une batez begiratzen."], "en": ["Let me check that for a moment."],
    },
    "s2_fallback": {
        "es": ["Eso no se lo puedo confirmar por teléfono, lo siento."],
        "ca": ["Això no l'hi puc confirmar per telèfon, ho sento."],
        "gl": ["Iso non llo podo confirmar por teléfono, síntoo."],
        "eu": ["Hori ezin dizut telefonoz baieztatu, sentitzen dut."],
        "en": ["I'm afraid I can't confirm that over the phone."],
    },
}

_rot = itertools.count()


CONTRACTIONS = {
    "es": [(" de el ", " del "), (" a el ", " al ")],
    "gl": [(" de o ", " do "), (" a o ", " ao "), (" en o ", " no "), (" con o ", " co ")],
}


def contract(lang: str, text: str) -> str:
    for a, b in CONTRACTIONS.get(lang, []):
        text = text.replace(a, b)
    return text


def say(key: str, lang: str, **kw) -> str:
    variants = T[key].get(lang) or T[key]["es"]
    return contract(lang, variants[next(_rot) % len(variants)].format(**kw))
