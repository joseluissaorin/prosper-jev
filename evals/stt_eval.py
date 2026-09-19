import os, time, asyncio, wave, numpy as np, sys
from google import genai
from google.genai import types
from tts_eval import FRASES, K
client=genai.Client(api_key=K)
def load16k(path):
    with wave.open(path) as w: rate=w.getframerate(); x=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).astype(np.float32)
    n=int(len(x)*16000/rate); y=np.interp(np.linspace(0,len(x)-1,n),np.arange(len(x)),x)
    return y.astype(np.int16).tobytes()
async def transcribe(pcm, langs):
    cfg=types.LiveConnectConfig(response_modalities=["TEXT"],input_audio_transcription=types.AudioTranscriptionConfig(language_codes=langs))
    async with client.aio.live.connect(model="gemini-3.5-transcribe-live", config=cfg) as s:
        events=[]; t0=time.perf_counter(); state={"end":None}
        async def sender():
            chunk=3200  # 100 ms
            for i in range(0,len(pcm),chunk):
                await s.send_realtime_input(audio=types.Blob(data=pcm[i:i+chunk],mime_type="audio/pcm;rate=16000"))
                await asyncio.sleep(0.1)
            state["end"]=time.perf_counter()
            silence=b"\x00"*3200
            for _ in range(15):
                await s.send_realtime_input(audio=types.Blob(data=silence,mime_type="audio/pcm;rate=16000")); await asyncio.sleep(0.1)
            await s.send_realtime_input(audio_stream_end=True)
        task=asyncio.create_task(sender())
        final=""
        try:
            async with asyncio.timeout(20):
                async for r in s.receive():
                    sc=r.server_content
                    if not sc: continue
                    now=time.perf_counter()
                    it=getattr(sc,"interim_input_transcription",None)
                    if it and it.text: events.append(("i",now-t0,it.text))
                    if sc.input_transcription and sc.input_transcription.text:
                        events.append(("f",now-t0,sc.input_transcription.text)); final+=sc.input_transcription.text
                        if state["end"] and now>state["end"]: break
        except TimeoutError: pass
        task.cancel()
        dur=len(pcm)/32000
        interims=[e for e in events if e[0]=="i"]; finals=[e for e in events if e[0]=="f"]
        lastf=finals[-1][1]-dur if finals else None
        firsti=interims[0][1] if interims else None
        return dur, len(interims), firsti, lastf, final.strip(), interims[-1][2] if interims else ""
async def main():
    src=sys.argv[1] if len(sys.argv)>1 else "gemini-3.1-flash-tts"
    for lang in FRASES:
        pcm=load16k(f"audio/{src}_{lang}.wav")
        for langs in ([],):
            try:
                dur,ni,fi,lf,final,lasti=await transcribe(pcm,langs)
                print(f"{lang} audio={dur:.1f}s parciales={ni:3d} 1.er parcial={fi if fi is None else round(fi,2)}s  final tras fin de audio={lf if lf is None else round(lf,2)}s")
                print(f"    FINAL « {final[:140]} »")
                if not final: print(f"    ÚLTIMO PARCIAL « {lasti[:140]} »")
            except Exception as e: print(lang,"ERROR",repr(e)[:300])
if __name__ == "__main__":
    asyncio.run(main())
