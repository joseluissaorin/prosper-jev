"""Una llamada de voz de prueba contra el agente, sin depender de Google.

Habla el mismo protocolo que Prosper (Twilio Media Streams): sintetiza a quien llama con ElevenLabs en µ-law de
8 kHz, manda las tramas de 20 ms a ritmo real y mide, turno a turno, cuánto tarda el agente en empezar a hablar
desde que quien llama calla. Sirve para medir latencia y para oír la llamada (guarda el audio de las dos voces).

    ../.venv/bin/python voz_test.py --ws ws://127.0.0.1:7880/ws
    ../.venv/bin/python voz_test.py --ws wss://…/ws --guion cambio --wav /tmp/llamada.wav
    ../.venv/bin/python voz_test.py --ws ws://127.0.0.1:7880/ws --linea +34612345678 --rep 3

Los guiones van contra la clínica SIMULADA (los pacientes de prueba de la arena).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import statistics
import sys
import time
import uuid
import wave
from pathlib import Path

import httpx
import websockets

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "demo"))

import ulaw  # noqa: E402

VOICE = os.environ.get("CALLER_VOICE", "EXAVITQu4vr4xnSDxMaL")      # una voz distinta de la del agente
MODEL = os.environ.get("CALLER_TTS_MODEL", "eleven_v3_conversational")
CACHE = HERE.parent / "demo" / "cache" / "voz_test"

GUIONES = {
    "cita": [("Hola, buenas. Quería pedir cita con el médico de cabecera.", "es"),
             ("Soy Marta Ruiz Navarro, nací el doce de abril del ochenta y siete.", "es"),
             ("Uf, no sé... ¿tiene alguna otra opción?", "es"),
             ("Vale, pues la primera que me dijo.", "es"),
             ("Sí, perfecto. Gracias, adiós.", "es")],
    "cambio": [("Hello, I'd like to change one of my appointments, please.", "en"),
               ("Laura Ruiz Gómez, born the fourteenth of September 1978.", "en"),
               ("The first one, please.", "en"),
               ("Yes, that's right. Thank you, goodbye.", "en")],
    "ingles": [("Hi, I need to see a GP as soon as possible, please.", "en"),
               ("Mario García López, my DNI is 39958838 H.", "en"),
               ("Yes, that works. Thank you, bye.", "en")],
}


def _key() -> str:
    if os.environ.get("ELEVENLABS_API_KEY"):
        return os.environ["ELEVENLABS_API_KEY"]
    for line in (Path.home() / ".claude/.secrets/elevenlabs.env").read_text().splitlines():
        if line.startswith("ELEVENLABS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("falta ELEVENLABS_API_KEY")


async def say(text: str, lang: str) -> bytes:
    """La frase en µ-law de 8 kHz, guardada en disco para no pagarla dos veces."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{abs(hash((text, lang, VOICE, MODEL)))}.ulaw"
    if f.exists():
        return f.read_bytes()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}/stream",
                         params={"output_format": "ulaw_8000"}, headers={"xi-api-key": _key()},
                         json={"text": text, "model_id": MODEL, "language_code": lang})
        r.raise_for_status()
        audio = r.content
    f.write_bytes(audio)
    return audio


class Llamada:
    """Una llamada: manda las frases y escucha lo que contesta el agente."""

    def __init__(self, ws, call_id: str, linea: str | None):
        self.ws, self.call_id, self.linea = ws, call_id, linea
        self.agent = bytearray()
        self.last_in = 0.0            # cuándo llegó la última trama del agente
        self.first_in = 0.0           # cuándo llegó la primera desde que callamos
        self.listening = False
        self.turnos: list[float] = []

    async def recv(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if m.get("event") == "media":
                b = base64.b64decode(m["media"]["payload"])
                self.agent += b
                now = time.perf_counter()
                if self.listening and not self.first_in:
                    self.first_in = now
                self.last_in = now

    async def decir(self, texto: str, lang: str):
        audio = await say(texto, lang)
        self.listening, self.first_in = False, 0.0
        t0 = time.perf_counter()
        for i in range(0, len(audio), 160):
            frame = audio[i:i + 160]
            await self.ws.send(json.dumps({"event": "media", "streamSid": self.call_id,
                                           "media": {"payload": base64.b64encode(frame).decode()}}))
            t0 += 0.02
            await asyncio.sleep(max(0.0, t0 - time.perf_counter()))
        fin = time.perf_counter()
        self.listening = True
        # esperar a que el agente empiece y termine de hablar (700 ms sin tramas)
        while time.perf_counter() - fin < 25:
            await asyncio.sleep(0.05)
            if self.first_in and time.perf_counter() - self.last_in > 0.7:
                break
        if self.first_in:
            self.turnos.append((self.first_in - fin) * 1000)
            print(f"   fin de voz → primera palabra: {(self.first_in - fin) * 1000:.0f} ms")
        else:
            print("   (el agente no contestó en 25 s)")
        await asyncio.sleep(0.3)


async def una(ws_url: str, guion: str, linea: str | None, wav: str | None) -> list[float]:
    call_id = str(uuid.uuid4())
    async with websockets.connect(ws_url, max_size=None, ping_interval=None) as ws:
        await ws.send(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
        params = {"call_id": call_id} | ({"from_number": linea} if linea else {})
        await ws.send(json.dumps({"event": "start", "start": {"streamSid": call_id, "callSid": call_id, "customParameters": params}}))
        c = Llamada(ws, call_id, linea)
        rx = asyncio.create_task(c.recv())
        t0 = time.perf_counter()
        await asyncio.sleep(0.2)
        while not c.first_in and time.perf_counter() - t0 < 12:      # el saludo del agente
            c.listening = True
            await asyncio.sleep(0.05)
        while c.first_in and time.perf_counter() - c.last_in < 0.7:
            await asyncio.sleep(0.05)
        for texto, lang in GUIONES[guion]:
            print(f"   ►  {texto}")
            await c.decir(texto, lang)
        await ws.send(json.dumps({"event": "stop", "streamSid": call_id}))
        rx.cancel()
        dur = time.perf_counter() - t0
        if wav:
            with wave.open(wav, "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
                w.writeframes(ulaw.ulaw_to_pcm16(bytes(c.agent)))
            print(f"   audio del agente en {wav}")
        print(f"   llamada de {dur:.0f} s")
        return c.turnos


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", default="ws://127.0.0.1:7880/ws")
    ap.add_argument("--guion", default="cita", choices=list(GUIONES))
    ap.add_argument("--linea", default=None, help="número de quien llama (como el caller id de Prosper)")
    ap.add_argument("--wav", default=None)
    ap.add_argument("--rep", type=int, default=1)
    a = ap.parse_args()
    todos: list[float] = []
    for i in range(a.rep):
        print(f"── llamada {i + 1} · {a.guion} · {a.ws}")
        todos += await una(a.ws, a.guion, a.linea, a.wav if i == 0 else None)
    if todos:
        todos.sort()
        print(f"\nfin de voz → primera palabra: mediana {statistics.median(todos):.0f} ms · "
              f"p90 {todos[int(len(todos) * 0.9)]:.0f} ms · máx {max(todos):.0f} ms · {len(todos)} turnos")


if __name__ == "__main__":
    asyncio.run(main())
