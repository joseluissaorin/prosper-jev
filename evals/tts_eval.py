import os, time, wave, json, statistics, asyncio
from google import genai
from google.genai import types
K=[l.split("=",1)[1].strip() for l in open(os.path.expanduser("~/.claude/.secrets/gemini.env")) if l.startswith("GEMINI_API_KEY=")][0]
client=genai.Client(api_key=K)
FRASES={
 "es":"Perfecto, María. Le he reservado el jueves veinticuatro a las nueve y media con la doctora Iglesias, en Chamberí.",
 "ca":"Perfecte, Maria. Li he reservat dijous vint-i-quatre a dos quarts de deu amb la doctora Iglesias, a Chamberí.",
 "gl":"Perfecto, María. Reserveille o xoves vinte e catro ás nove e media coa doutora Iglesias, en Chamberí.",
 "eu":"Ederki, María. Hitzordua hartu dizut ostegunean, hilaren hogeita lauan, bederatzi eta erdietan, Iglesias doktorearekin.",
 "en":"Perfect, María. I've booked you on Thursday the twenty-fourth at nine thirty with Doctor Iglesias, at the Chamberí site.",
}
def save(pcm,path,rate=24000):
    with wave.open(path,"wb") as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
def run(model, lang, text, voice="Kore", stream=True):
    cfg=types.GenerateContentConfig(response_modalities=["AUDIO"],speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))))
    t=time.perf_counter(); first=None; pcm=b""
    if stream:
        for ch in client.models.generate_content_stream(model=model,contents=text,config=cfg):
            for p in (ch.candidates[0].content.parts if ch.candidates and ch.candidates[0].content else []):
                if p.inline_data and p.inline_data.data:
                    if first is None: first=time.perf_counter()-t
                    pcm+=p.inline_data.data
    else:
        r=client.models.generate_content(model=model,contents=text,config=cfg)
        pcm=r.candidates[0].content.parts[0].inline_data.data; first=time.perf_counter()-t
    total=time.perf_counter()-t
    return first,total,len(pcm)/48000,pcm
if __name__ == "__main__":
    for model in ["gemini-3.1-flash-tts-preview","gemini-2.5-flash-preview-tts"]:
        for lang,text in FRASES.items():
            try:
                f,tt,dur,pcm=run(model,lang,text)
                save(pcm,f"audio/{model.split('-preview')[0]}_{lang}.wav")
                print(f"{model:30} {lang}  primer audio={f*1000:5.0f} ms  total={tt*1000:5.0f} ms  audio={dur:4.1f} s")
            except Exception as e: print(model,lang,"ERROR",str(e)[:200])
