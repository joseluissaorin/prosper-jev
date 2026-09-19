"""Prueba de voz de extremo a extremo, sin micrófono: se hace pasar por el navegador.

Sintetiza a quien llama con otra voz (Gemini TTS, en caché), le manda el audio al servidor a ritmo real
y mide cuánto tarda el agente en empezar a hablar desde que la persona se calla.

    ../.venv/bin/python e2e.py [escenario]     (con el servidor en marcha en :8765)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np
import websockets
from google.genai import types

from system2 import CLIENT

AUDIO = Path(__file__).parent / "cache" / "caller"
AUDIO.mkdir(parents=True, exist_ok=True)

SCENARIOS = {
    "reserva": ("auto", [
        "Hola, buenos días. Quería pedir cita con el médico de cabecera.",
        "Laura Ruiz Gómez. Nací el catorce de septiembre de mil novecientos setenta y ocho.",
        "El jueves que viene por la mañana, si puede ser.",
        "La primera me va bien.",
        "Sí, perfecto.",
        "No, nada más, gracias. Adiós."]),
    "gallego": ("auto", [
        "Bos días, quería pedir unha cita co médico de cabeceira.",
        "Uxía Castro Rey. Nacín o dezasete de febreiro de mil novecentos oitenta e oito.",
        "O venres pola mañá.",
        "A primeira.",
        "Si, perfecto.",
        "Nada máis, grazas."]),
    "urgencia": ("auto", [
        "Hola, quería pedir cita... es que llevo media hora con un dolor muy fuerte en el pecho que me baja por el brazo."]),
}


def caller_audio(text: str, voice: str = "Puck") -> bytes:
    k = hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:16]
    f = AUDIO / f"{k}.pcm"
    if f.exists():
        return f.read_bytes()
    cfg = types.GenerateContentConfig(response_modalities=["AUDIO"], speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))))
    r = CLIENT.models.generate_content(model="gemini-3.1-flash-tts-preview", contents=f"Say naturally, like a person on the phone: {text}", config=cfg)
    pcm24 = np.frombuffer(r.candidates[0].content.parts[0].inline_data.data, dtype=np.int16).astype(np.float32)
    n = int(len(pcm24) * 16000 / 24000)
    pcm16 = np.interp(np.linspace(0, len(pcm24) - 1, n), np.arange(len(pcm24)), pcm24).astype(np.int16).tobytes()
    f.write_bytes(pcm16)
    return pcm16


async def run(name: str):
    lang, lines = SCENARIOS[name]
    print(f"Preparando el audio de quien llama ({len(lines)} frases)…")
    audios = [caller_audio(t) for t in lines]
    async with websockets.connect("ws://127.0.0.1:8765/ws", max_size=None) as ws:
        state = {"until": 0.0, "agent_events": 0, "first_audio": None, "report": None, "ended": False}
        lat, events = [], []

        async def rx():
            async for m in ws:
                now = time.time()
                if isinstance(m, bytes):
                    if state["first_audio"] is None:
                        state["first_audio"] = now
                    state["until"] = max(state["until"], now) + len(m) / 48000
                    continue
                e = json.loads(m)
                events.append(e)
                t = e["type"]
                if t == "agent":
                    state["agent_events"] += 1
                    print(f"   🟢 {e['text']}   [{e['source']}{' · caché' if e.get('cached') else ' · en vivo'}]")
                elif t == "final":
                    print(f"   🔵 {e['text']}   [STT definitivo {e.get('stt_ms')} ms]")
                elif t == "latency":
                    print(f"      ⏱ {e['stage']}: {e['ms']} ms")
                elif t == "perception":
                    print(f"      Jev ({e['phase']}) {e['ms']} ms{' · duplicada' if e['hedged'] else ''}")
                elif t == "trace" and e["event"]["kind"] == "gate":
                    g = e["event"]
                    print(f"      {'✓' if g['ok'] else '✗'} {g['name']}: {g['detail']}")
                elif t in ("log", "stop_audio"):
                    print(f"      · {e.get('msg') or e.get('reason')}")
                elif t == "partial" and "--v" in sys.argv:
                    print(f"      … {e['text']}")
                elif t == "vad" and "--v" in sys.argv:
                    print(f"      [vad {e['state']}]")
                elif t == "report":
                    state["report"] = e["report"]
                    state["ended"] = True

        rxt = asyncio.create_task(rx())
        await ws.send(json.dumps({"type": "start", "lang": lang, "tts": True}))

        async def wait_agent_done(prev_events):
            t0 = time.time()
            while time.time() - t0 < 20:
                if state["agent_events"] > prev_events and time.time() > state["until"] + 0.5:
                    return
                if state["ended"]:
                    return
                await asyncio.sleep(0.05)

        async def send_pcm(pcm: bytes):
            chunk = 1280  # 40 ms
            t0 = time.perf_counter()
            for i, off in enumerate(range(0, len(pcm), chunk)):
                await ws.send(pcm[off:off + chunk])
                await asyncio.sleep(max(0, t0 + (i + 1) * 0.04 - time.perf_counter()))

        await wait_agent_done(0)
        for text, pcm in zip(lines, audios):
            if state["ended"]:
                break
            prev = state["agent_events"]
            state["first_audio"] = None
            # quitar el silencio final del audio sintetizado para medir desde la última palabra
            x = np.frombuffer(pcm, dtype=np.int16)
            voiced = np.where(np.abs(x) > 700)[0]
            pcm = x[: voiced[-1] + 1600].tobytes() if len(voiced) else pcm
            await send_pcm(pcm)
            t_end = time.time()
            # silencio de fondo mientras espera la respuesta
            silence = asyncio.create_task(send_pcm(b"\x00" * 32000 * 3))
            while state["first_audio"] is None and time.time() - t_end < 8 and not state["ended"]:
                await asyncio.sleep(0.01)
            if state["first_audio"]:
                lat.append((state["first_audio"] - t_end) * 1000)
                print(f"      ⏱ respuesta percibida (última palabra → voz del agente): {lat[-1]:.0f} ms")
            await silence
            await wait_agent_done(prev)
        if not state["ended"]:
            await ws.send(json.dumps({"type": "hangup"}))
            for _ in range(60):
                if state["report"]:
                    break
                await asyncio.sleep(0.1)
        rxt.cancel()
        r = state["report"] or {}
        print(f"\nResultado: {r.get('outcome')} {r.get('reason') or ''} · idioma {r.get('language')} · auditoría {r.get('audit', {}).get('consistent')}")
        if lat:
            print(f"Respuesta percibida: mediana {statistics.median(lat):.0f} ms · mín {min(lat):.0f} · máx {max(lat):.0f}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(run(args[0] if args else "reserva"))
