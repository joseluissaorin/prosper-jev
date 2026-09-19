from probe import call, show
import time, statistics
# 1) latencia en serie
lat=[]
for i in range(8):
    _,ms=call({"transcript":"Quería pedir cita para el lunes por la mañana."},{"q":{"type":"noul","instructions":"The caller wants to book an appointment"},"r":{"type":"noul","instructions":"The caller mentions a specific doctor"}}); lat.append(ms)
print("latencias serie ms:",[round(x) for x in lat],"p50=",round(statistics.median(lat)))

# 2) desambiguar identidad entre 4 homónimos (lo que el llamante dijo, con errores de STT)
cands={"p1":{"name":"María García López","dob":"1984-03-12","phone_last4":"4471","site":"Chamberí"},
       "p2":{"name":"María García López","dob":"1991-11-02","phone_last4":"0915","site":"Retiro"},
       "p3":{"name":"María José García Lozano","dob":"1984-03-21","phone_last4":"4471","site":"Chamberí"},
       "p4":{"name":"Mario García López","dob":"1975-07-30","phone_last4":"2208","site":"Chamberí"}}
said="Soy María García López, nací el doce de marzo del ochenta y cuatro, y el móvil acaba en cuarenta y cuatro setenta y uno."
q={"who":{"type":"choice","instructions":"Which record in `candidates` is the caller, based only on what they said in `caller_said`? Pick 'none' if no record matches every detail they gave, 'ambiguous' if more than one record fits.",
   "criteria":{**{k:f"record {k}" for k in cands},"none":"No record matches","ambiguous":"More than one record fits what was said"}}}
q.update({f"match_{k}":{"type":"noul","instructions":f"Is everything the caller said in `caller_said` consistent with `candidates.{k}` (name, date of birth, phone ending)?"} for k in cands})
show("identidad_4_homonimos",call({"caller_said":said,"candidates":cands},q))

# 3) fecha vaga -> partes (el código resuelve)
dq={"anchor":{"type":"choice","instructions":"Which day does the caller ask for in `utterance`?","criteria":{"today":None,"tomorrow":None,"day_after":"the day after tomorrow","weekday":"a named day of the week","asap":"the soonest available, no specific day","none":"no day stated"}},
    "weekday":{"type":"choice","instructions":"If `utterance` names a day of the week, which one?","criteria":{d:None for d in ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]}|{"none":None}},
    "week":{"type":"choice","instructions":"If a weekday is named in `utterance`, which week? 'this' = this week, 'next' = 'next X' / 'X de la semana que viene', 'none' = bare weekday","criteria":{"this":None,"next":None,"none":None}},
    "part_of_day":{"type":"choice","instructions":"Which part of the day does the caller want in `utterance`?","criteria":{"first_thing":"the earliest slot of the day ('first thing', 'a primera hora')","morning":None,"midday":None,"afternoon":None,"evening":None,"any":"no preference stated"}}}
for u in ["A primera hora del lunes, si puede ser.","El jueves que viene por la tarde.","Lo antes posible, me da igual el día.","Next Thursday, late morning please."]:
    show("fecha: "+u,call({"utterance":u},dq))

# 4) reglas de la clínica: ¿se puede? y ¿por qué no?
rules=["Pediatrics only sees patients under 14.","Cardiology requires a referral from a GP on file.","Appointments cannot be booked more than 60 days ahead.","A patient may hold at most one future appointment per specialty.","The clinic does not offer dermatology, psychiatry or dental care."]
req={"request":"Book cardiology for Antonio Ruiz, 82","patient_record":{"age":82,"referrals":[],"future_appointments":[]}}
rq={"violated":{"type":"choice","instructions":"Which clinic rule in `rules`, if any, forbids fulfilling `request` for this `patient_record`?","criteria":{f"r{i}":r for i,r in enumerate(rules)}|{"none":"No rule forbids it"}}}
show("regla_cardio_sin_derivacion",call({"rules":rules,**req},rq))
show("regla_dermatologia",call({"rules":rules,"request":"Quiero cita con el dermatólogo para mirarme un lunar","patient_record":{"age":30,"referrals":[],"future_appointments":[]}},rq))

# 5) auditoría final: ¿el resultado declarado cuadra con la conversación y el log de herramientas?
audit_state={"transcript":["Caller: Quiero anular la cita del martes.","Agent: ¿La de las 10:00 con la Dra. Iglesias?","Caller: No, no, mejor pásamela al jueves por la mañana.","Agent: Hecho, le he movido la cita al jueves 24 a las 9:30."],
             "tool_log":[{"tool":"cancel_appointment","id":"A-881","ok":True}],
             "declared_outcome":"rescheduled"}
aq={"consistent":{"type":"noul","instructions":"Do the actions in `tool_log` actually carry out what the agent told the caller in the last line of `transcript`?"},
    "final_wish":{"type":"choice","instructions":"What did the caller finally want, per `transcript`?","criteria":{"book":None,"reschedule":None,"cancel":None,"nothing":None}}}
show("auditoria_log_vs_transcript",call(audit_state,aq))
