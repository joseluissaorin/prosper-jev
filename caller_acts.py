from keepalive import call
import concurrent.futures as cf
def Q(agent_said):
  return {
  "act":{"type":"choice","instructions":"What is the caller doing in `caller` in reply to what the receptionist said in `agent`?","criteria":{
     "confirm":"Clearly agrees / says yes to what the receptionist proposed or read back, with no change",
     "reject":"Says no to the proposal without giving a new preference",
     "correct":"Changes or corrects something (a time, day, name, detail), possibly after first saying yes",
     "provide_info":"Answers the question or gives requested details",
     "ask_question":"Asks the receptionist something",
     "backchannel":"Only a listening sound or filler (mhm, ajá, vale, sí sí) while the receptionist talks; no new content",
     "end_call":"Wants to hang up / says goodbye",
     "unclear":"Too garbled or cut off to know"}},
  "finished":{"type":"noul","instructions":"Has the caller finished their sentence in `caller`, so the receptionist can reply now (not cut off in the middle of a thought, a number, or a date)?"},
  }
cases=[
 ("¿Le va bien el jueves 24 a las 9:30 con la doctora Iglesias?","Sí, perfecto."),
 ("¿Le va bien el jueves 24 a las 9:30 con la doctora Iglesias?","Sí... bueno, no, mejor por la tarde."),
 ("¿Le va bien el jueves 24 a las 9:30 con la doctora Iglesias?","No, ese día no puedo."),
 ("Le leo la cita: jueves 24, a las 9:30, con la doctora Iglesias, en Chamberí...","ajá, sí"),
 ("¿Me dice su fecha de nacimiento?","El doce de marzo del"),
 ("¿Me dice su fecha de nacimiento?","El doce de marzo del ochenta y cuatro."),
 ("¿Me dice su nombre completo?","Pues mira, me llamo María José y"),
 ("¿En qué puedo ayudarle?","Quería pedir cita para"),
 ("¿Le va bien el jueves 24 a las 9:30?","Espera, espera, que no es para mí, es para mi hija."),
 ("¿Algo más?","No, nada más, gracias, adiós."),
 ("Val, li busco hora dijous al matí.","Sí, gràcies, perfecte."),
]
with cf.ThreadPoolExecutor(1) as ex:
  for ag,ca in cases:
    r,ms=call({"agent":ag,"caller":ca},Q(ag)); a=r["answers"]
    print(f"{ms:4.0f}ms  {a['act']['choice']:12} c={a['act']['confidence']:.2f}  fin={a['finished']['noul']:.2f}  « {ca} »")
