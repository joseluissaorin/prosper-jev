import os, time, json, http.client, statistics
K=[l.split("=",1)[1].strip() for l in open(os.path.expanduser("~/.claude/.secrets/gemini.env")) if l.startswith("GEMINI_API_KEY=")][0]
SYS="Eres la recepcionista de una clínica en Madrid. Responde en una o dos frases cortas, en el idioma de quien llama, sin inventar datos: si no sabes algo, dilo."
Q="Oiga, ¿y el parking de la sede de Chamberí es gratis para pacientes?"
def run(model, thinking=None):
    c=http.client.HTTPSConnection("generativelanguage.googleapis.com")
    out=[]
    for i in range(5):
        body={"systemInstruction":{"parts":[{"text":SYS}]},"contents":[{"role":"user","parts":[{"text":Q}]}],"generationConfig":{"maxOutputTokens":80}}
        if thinking is not None: body["generationConfig"]["thinkingConfig"]={"thinkingBudget":thinking}
        t=time.perf_counter()
        c.request("POST",f"/v1beta/models/{model}:streamGenerateContent?alt=sse&key={K}",json.dumps(body),{"Content-Type":"application/json"})
        r=c.getresponse(); first=None; txt=""
        for line in r:
            line=line.decode().strip()
            if line.startswith("data:"):
                d=json.loads(line[5:])
                p=d.get("candidates",[{}])[0].get("content",{}).get("parts",[])
                s="".join(x.get("text","") for x in p)
                if s and first is None: first=(time.perf_counter()-t)*1000
                txt+=s
        total=(time.perf_counter()-t)*1000
        if r.status!=200: print(model,r.status); return
        out.append((first or total,total))
    f=[o[0] for o in out]; tt=[o[1] for o in out]
    print(f"{model:28} think={thinking}  1.er token p50={statistics.median(f):4.0f} ms  total p50={statistics.median(tt):4.0f} ms  « {txt.strip()[:110]} »")
if __name__ == "__main__":
    for m,th in [("gemini-3.5-flash-lite",0),("gemini-3.1-flash-lite",0),("gemini-3.8-flash",0),("gemini-3.8-flash",None),("gemini-flash-latest",0)]:
        try: run(m,th)
        except Exception as e: print(m,"ERROR",e)
