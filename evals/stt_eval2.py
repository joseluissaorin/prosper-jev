import time, asyncio, difflib, inspect
from google import genai
from google.genai import types
from tts_eval import FRASES, K
from stt_eval import load16k
client=genai.Client(api_key=K)
if __name__ == "__main__": print("campos AudioTranscriptionConfig:", list(types.AudioTranscriptionConfig.model_fields))
VOCAB=["Chamberí","Retiro","Iglesias","Pérez","Morales","cardiología","pediatría","dermatología"]
def norm(s): return "".join(c.lower() for c in s if c.isalnum() or c==" ").split()
async def run(pcm, langs, vocab):
    kw={"language_codes":langs}
    if vocab and "custom_vocabulary" in types.AudioTranscriptionConfig.model_fields: kw["custom_vocabulary"]=vocab
    cfg=types.LiveConnectConfig(response_modalities=["TEXT"],input_audio_transcription=types.AudioTranscriptionConfig(**kw))
    async with client.aio.live.connect(model="gemini-3.5-transcribe-live", config=cfg) as s:
        t0=time.perf_counter(); ev=[]; end={"t":None}
        async def sender():
            for i in range(0,len(pcm),3200):
                await s.send_realtime_input(audio=types.Blob(data=pcm[i:i+3200],mime_type="audio/pcm;rate=16000")); await asyncio.sleep(0.1)
            end["t"]=time.perf_counter()-t0
            for _ in range(25):
                await s.send_realtime_input(audio=types.Blob(data=b"\0"*3200,mime_type="audio/pcm;rate=16000")); await asyncio.sleep(0.1)
        task=asyncio.create_task(sender()); final=""
        try:
            async with asyncio.timeout(15):
                async for r in s.receive():
                    sc=r.server_content
                    if not sc: continue
                    now=time.perf_counter()-t0
                    if sc.interim_input_transcription and sc.interim_input_transcription.text: ev.append((now,sc.interim_input_transcription.text))
                    if sc.input_transcription and sc.input_transcription.text:
                        final+=sc.input_transcription.text
                        if end["t"] and now>end["t"]: fin_t=now; break
        except TimeoutError: fin_t=None
        task.cancel()
        # ¿cuándo contiene el parcial las 3 últimas palabras del final?
        tail=norm(final)[-3:]; t_tail=None
        for t,txt in ev:
            if norm(txt)[-3:]==tail: t_tail=t; break
        return final.strip(), (t_tail-end["t"]) if t_tail and end["t"] else None, (fin_t-end["t"]) if fin_t and end["t"] else None
async def main():
    hints={"es":["es-ES"],"ca":["ca-ES"],"gl":["gl-ES"],"eu":["eu-ES"],"en":["en-US"]}
    todas=["es-ES","ca-ES","gl-ES","eu-ES","en-US"]
    for lang,ref in FRASES.items():
        pcm=load16k(f"audio/gemini-3.1-flash-tts_{lang}.wav")
        for nombre,langs in [("pista exacta",hints[lang]),("5 pistas",todas)]:
            try:
                final,tp,tf=await run(pcm,langs,VOCAB)
                sim=difflib.SequenceMatcher(None," ".join(norm(ref))," ".join(norm(final))).ratio()
                print(f"{lang} {nombre:12} parcial completo tras fin={tp if tp is None else round(tp,2)}s final={tf if tf is None else round(tf,2)}s sim={sim:.2f} « {final[:105]} »")
            except Exception as e: print(lang,nombre,"ERROR",repr(e)[:250])
if __name__ == "__main__":
    asyncio.run(main())
