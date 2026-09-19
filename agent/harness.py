"""Arnés local que imita al de Prosper: llama a nuestro WebSocket con el protocolo de Twilio Media Streams.

    ../.venv/bin/python harness.py [escenario] [--url ws://127.0.0.1:7860/ws]

Sintetiza a quien llama (Gemini TTS, en caché), manda µ-law de 8 kHz en tramas de 20 ms a ritmo real (con
silencio continuo entre frases, como Twilio), escucha al agente y al final mira qué declaró en la clínica."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import websockets

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "demo"))
os.chdir(HERE.parent / "demo")
from e2e import caller_audio  # noqa: E402  (voz de quien llama, en caché)
os.chdir(HERE)
import httpx  # noqa: E402

import ulaw  # noqa: E402

SCEN = {
    "simple": ("+34612345678", ["Hi, I'd like the earliest appointment with a GP, please.",
                                "It's Marta Ruiz Navarro, my phone is six one two, three four five, six seven eight.",
                                "Yes, please book it.", "No, that's all, thanks. Bye."]),
    "requena": (None, ["Hi, can I book with Doctor Requena at Arenal Centro, please?",
                       "Antonio Ruiz Medina, born the third of May, nineteen forty-four.",
                       "Yes, that works.", "That's all, thank you. Goodbye."]),
    "urgencia": (None, ["Hi, I need an appointment. I've got a tight pain across my chest and I'm struggling to catch my breath."]),
}


def to_ulaw_8k(pcm16k: bytes) -> bytes:
    x = np.frombuffer(pcm16k, dtype=np.int16).astype(np.float32)
    y = ((x[0::2][: len(x) // 2] + x[1::2][: len(x) // 2]) / 2).astype(np.int16)
    return ulaw.pcm16_to_ulaw(y)


async def run(name: str, url: str):
    frm, lines = SCEN[name]
    print(f"Preparando la voz de quien llama ({len(lines)} frases)…")
    audios = []
    for t in lines:
        for attempt in range(4):
            try:
                raw = caller_audio(t)
                break
            except Exception as e:  # noqa: BLE001
                print(f"   (síntesis de quien llama falló, reintento {attempt + 1}: {str(e)[:60]})")
                time.sleep(2)
        pcm = np.frombuffer(raw, dtype=np.int16)
        voiced = np.where(np.abs(pcm) > 700)[0]
        pcm = pcm[: voiced[-1] + 1600] if len(voiced) else pcm
        audios.append(to_ulaw_8k(pcm.tobytes()))
    call_sid, stream_sid = "CA" + uuid.uuid4().hex, "MZ" + uuid.uuid4().hex
    st = {"agent_bytes": 0, "last_agent": 0.0, "first_after": None, "marks": []}
    async with websockets.connect(url, max_size=None) as ws:
        async def rx():
            async for m in ws:
                e = json.loads(m)
                if e.get("event") == "media":
                    now = time.time()
                    st["agent_bytes"] += len(base64.b64decode(e["media"]["payload"]))
                    if st["first_after"] is None:
                        st["first_after"] = now
                    st["last_agent"] = now
                elif e.get("event") == "mark":
                    st["marks"].append(e["mark"]["name"])
                elif e.get("event") not in ("clear",):
                    print("   ⚠ mensaje inesperado del agente:", str(e)[:120])
        t_rx = asyncio.create_task(rx())
        seq = [0]

        async def send(ev: dict):
            seq[0] += 1
            ev["sequenceNumber"] = str(seq[0])
            await ws.send(json.dumps(ev))

        await send({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        await send({"event": "start", "streamSid": stream_sid, "start": {
            "streamSid": stream_sid, "accountSid": "AC" + "0" * 32, "callSid": call_sid, "tracks": ["inbound"],
            "customParameters": {"call_id": call_sid, **({"from_number": frm} if frm else {})},
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}})
        chunk, ts = [0], [0]

        async def stream(data: bytes):
            t0 = time.perf_counter()
            for i in range(0, len(data), 160):
                fr = data[i:i + 160].ljust(160, b"\xff")
                chunk[0] += 1
                ts[0] += 20
                await send({"event": "media", "streamSid": stream_sid, "media": {"track": "inbound", "chunk": str(chunk[0]),
                                                                                    "timestamp": str(ts[0]), "payload": base64.b64encode(fr).decode()}})
                await asyncio.sleep(max(0, t0 + (i // 160 + 1) * 0.02 - time.perf_counter()))

        async def silence_until(cond, limit=20.0):
            t0 = time.time()
            while not cond() and time.time() - t0 < limit:
                await stream(b"\xff" * 160 * 5)

        lat = []
        # saludo del agente
        await silence_until(lambda: st["first_after"] is not None and time.time() - st["last_agent"] > 0.9, 15)
        print(f"   🟢 (saludo, {st['agent_bytes'] / 8000:.1f} s de audio)")
        for text, audio in zip(lines, audios):
            print(f"   🔵 {text}")
            st["first_after"] = None
            await stream(audio)
            t_end = time.time()
            await silence_until(lambda: st["first_after"] is not None and time.time() - st["last_agent"] > 1.0, 20)
            if st["first_after"]:
                lat.append((st["first_after"] - t_end) * 1000)
                print(f"      ⏱ respuesta: {lat[-1]:.0f} ms · audio del agente acumulado {st['agent_bytes'] / 8000:.1f} s")
            else:
                print("      ⚠ el agente no contestó")
        await send({"event": "stop", "streamSid": stream_sid, "stop": {"accountSid": "AC", "callSid": call_sid}})
        await asyncio.sleep(1.5)
        t_rx.cancel()
    base = os.environ.get("PROSPER_API_BASE_URL", "http://127.0.0.1:8770")
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{base}/api/v1/submissions", params={"limit": 200}, headers={"X-Api-Key": "pk-local"})
    rec = next((x for x in r.json()["records"] if x["call_id"] == call_sid), None)
    print(f"\nDeclarado para {call_sid[:10]}…: {json.dumps(rec['record']['actions'] if rec else None, ensure_ascii=False)}")
    if lat:
        print(f"Respuesta: mediana {statistics.median(lat):.0f} ms · mín {min(lat):.0f} · máx {max(lat):.0f}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    url = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--url=")), "ws://127.0.0.1:7860/ws")
    asyncio.run(run(args[0] if args else "simple", url))
