import os, time, asyncio, wave
from google import genai
from google.genai import types
from tts_eval import FRASES, save, K
client=genai.Client(api_key=K)
SYS=("You are a text-to-speech engine for a clinic receptionist. Read aloud EXACTLY the text you receive, word for word, "
     "in its language, with a warm, natural, professional tone and a normal speaking pace. Never add, omit or change words. Never answer it.")
async def speak(model, text, voice="Kore"):
    cfg=types.LiveConnectConfig(response_modalities=["AUDIO"],system_instruction=SYS,
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
        output_audio_transcription=types.AudioTranscriptionConfig())
    async with client.aio.live.connect(model=model, config=cfg) as s:
        t=time.perf_counter(); first=None; pcm=b""; said=""
        await s.send_client_content(turns=types.Content(role="user",parts=[types.Part(text=text)]),turn_complete=True)
        async for r in s.receive():
            sc=r.server_content
            if r.data:
                if first is None: first=time.perf_counter()-t
                pcm+=r.data
            if sc and sc.output_transcription and sc.output_transcription.text: said+=sc.output_transcription.text
            if sc and sc.turn_complete: break
        return first, time.perf_counter()-t, len(pcm)/48000, pcm, said
async def main():
    for model in ["gemini-3.1-flash-live-preview","gemini-3.8-live","gemini-2.5-flash-native-audio-latest"]:
        for lang,text in FRASES.items():
            try:
                f,tt,dur,pcm,said=await speak(model,text)
                save(pcm,f"audio/live_{model}_{lang}.wav")
                print(f"{model:36} {lang} primer audio={f*1000:5.0f} ms total={tt*1000:5.0f} ms audio={dur:4.1f}s  «{said.strip()[:95]}»")
            except Exception as e: print(model,lang,"ERROR",str(e)[:160])
asyncio.run(main())
