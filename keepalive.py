import http.client, json, os, time, statistics
K=open(os.path.expanduser("~/.claude/.secrets/typesafe.env")).read().split("=",1)[1].strip()
c=http.client.HTTPSConnection("api.typesafe.ai")
def call(state,q):
    b=json.dumps({"model":"jev-latest","state":state,"questions":q})
    t=time.perf_counter(); c.request("POST","/v1/systemone",b,{"Authorization":"Bearer "+K,"Content-Type":"application/json"})
    r=json.loads(c.getresponse().read()); return r,(time.perf_counter()-t)*1000
if __name__=="__main__":
  lat=[call({"t":"quería cita el lunes"},{"a":{"type":"noul","instructions":"The caller wants an appointment"}})[1] for _ in range(10)]
  print("keep-alive, 1 pregunta:",[round(x) for x in lat],"p50",round(statistics.median(lat)))
  q12={f"q{i}":{"type":"noul","instructions":f"Question number {i}: does the caller mention a weekday?"} for i in range(12)}
if __name__=="__main__":
  lat=[call({"t":"quería cita el lunes"},q12)[1] for _ in range(5)]
  print("keep-alive, 12 preguntas:",[round(x) for x in lat],"p50",round(statistics.median(lat)))
