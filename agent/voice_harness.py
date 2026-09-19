"""Arnés de voz como el de Prosper: 50 llamadas por el protocolo de Twilio Media Streams, 10 a la vez.

    ../.venv/bin/python voice_harness.py --prep          # graba de antemano todas las frases (una vez)
    ../.venv/bin/python voice_harness.py                 # las 50 llamadas, 10 a la vez
    ../.venv/bin/python voice_harness.py p13 p14         # solo esos problemas
    ../.venv/bin/python voice_harness.py --caso p4_dni   # una (o varias separadas por comas)

La voz de quien llama está pregrabada (Gemini TTS, varias voces) y el ruido se mezcla en directo a 5 dB sobre
la voz y el silencio. El arnés sabe qué ha dicho el agente leyendo la consola del propio agente (/monitor) y
contesta con la frase grabada que toca. Al final comprueba lo declarado en la clínica contra la respuesta esperada.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import re
import statistics
import sys
import time
import uuid
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "demo"))
os.environ.setdefault("PROSPER_API_BASE_URL", "http://127.0.0.1:8770")

import httpx  # noqa: E402
import websockets  # noqa: E402
from google.genai import types  # noqa: E402

import ulaw  # noqa: E402
import voice_cases  # noqa: E402
from system2 import CLIENT  # noqa: E402

# por defecto, la instancia LOCAL (contra la clínica simulada): la de 7860 atiende al arnés de Prosper con la API real
AGENT = os.environ.get("AGENT_WS", "ws://127.0.0.1:7861/ws")
MONITOR = AGENT.rsplit("/", 1)[0] + "/monitor" + (f"?t={os.environ['MONITOR_TOKEN']}" if os.environ.get("MONITOR_TOKEN") else "")
API = os.environ.get("HARNESS_API", "http://127.0.0.1:8770")   # la clínica simulada, nunca la real
CACHE = HERE / "cache" / "harness"
CACHE.mkdir(parents=True, exist_ok=True)
SR = 8000
FRAME = 160
PAUSE = np.zeros(int(0.7 * SR), dtype=np.int16)

# ---------------------------------------------------------------- grabación previa


def _key(voice: str, text: str) -> Path:
    return CACHE / (hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:20] + ".pcm")


async def synth(voice: str, text: str, sem) -> np.ndarray:
    f = _key(voice, text)
    if f.exists():
        return np.frombuffer(f.read_bytes(), dtype=np.int16)
    async with sem:
        cfg = types.GenerateContentConfig(response_modalities=["AUDIO"], speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))))
        for attempt in range(5):
            try:
                r = await asyncio.wait_for(CLIENT.aio.models.generate_content(
                    model="gemini-3.1-flash-tts-preview", contents=f"Say this naturally, like a real person on the phone: {text}", config=cfg), timeout=60)
                pcm24 = np.frombuffer(r.candidates[0].content.parts[0].inline_data.data, dtype=np.int16).astype(np.float32)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 4:
                    raise
                await asyncio.sleep(2 + 2 * attempt)
        k = np.array([0.25, 0.5, 0.25], dtype=np.float32)
        y = np.convolve(pcm24, k, mode="same")[::3]
        # recortar silencio de los extremos
        v = np.where(np.abs(y) > 500)[0]
        if len(v):
            y = y[max(0, v[0] - 400): v[-1] + 800]
        y = np.clip(y, -32768, 32767).astype(np.int16)
        f.write_bytes(y.tobytes())
        return y


def segments(line: str) -> list[str]:
    return [p.strip() for p in line.split("||") if p.strip()]


def all_lines(cs) -> list[tuple[str, str]]:
    out = []
    for c in cs:
        for v in c["lines"].values():
            for line in (v if isinstance(v, list) else [v]):
                for seg in segments(line):
                    out.append((c["voice"], seg))
    return sorted(set(out))


async def prep(cs):
    sem = asyncio.Semaphore(6)
    todo = all_lines(cs)
    missing = [t for t in todo if not _key(*t).exists()]
    print(f"Frases: {len(todo)} · por grabar: {len(missing)}", flush=True)
    done = 0

    async def one(t):
        nonlocal done
        await synth(*t, sem)
        done += 1
        if done % 20 == 0 or done == len(missing):
            print(f"   grabadas {done}/{len(missing)}", flush=True)
    await asyncio.gather(*[one(t) for t in missing])


def utterance(voice: str, line: str) -> np.ndarray:
    parts = []
    for i, seg in enumerate(segments(line)):
        if i:
            parts.append(PAUSE)
        parts.append(np.frombuffer(_key(voice, seg).read_bytes(), dtype=np.int16))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)

# ---------------------------------------------------------------- ruido (sintético, a 5 dB)


def noise_bed(kind: str, seconds: int = 240, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = seconds * SR
    white = rng.standard_normal(n).astype(np.float32)
    t = np.arange(n) / SR
    if kind in ("street", "car", "room"):
        # ruido rosa/marrón aproximado con filtros de un polo
        def lp(x, a):
            y = np.empty_like(x)
            acc = 0.0
            for i in range(0, len(x), 4096):
                seg = x[i:i + 4096]
                out = np.empty_like(seg)
                for j, v in enumerate(seg):
                    acc = a * acc + (1 - a) * v
                    out[j] = acc
                y[i:i + 4096] = out
            return y
        base = lp(white, 0.97 if kind == "car" else 0.9)
    if kind == "street":
        swell = 0.6 + 0.4 * np.sin(2 * np.pi * t / 7.0) ** 2 + 0.5 * (np.sin(2 * np.pi * t / 23.0) > 0.95)
        sig = base * swell + 0.15 * white
    elif kind == "car":
        sig = base + 0.6 * np.sin(2 * np.pi * 90 * t) * (1 + 0.1 * np.sin(2 * np.pi * 0.3 * t))
    elif kind == "room":
        sig = base + 0.3 * np.sin(2 * np.pi * 50 * t) + 0.2 * np.sin(2 * np.pi * 100 * t)
    else:  # tv: murmullo de voces (frases grabadas del arnés al revés, a varias capas)
        pool = [np.frombuffer(f.read_bytes(), dtype=np.int16).astype(np.float32) for f in list(CACHE.glob("*.pcm"))[:40]]
        sig = np.zeros(n, dtype=np.float32)
        for layer in range(4):
            pos = int(rng.integers(0, SR))
            while pos < n and pool:
                clip = pool[int(rng.integers(0, len(pool)))][::-1]
                m = min(len(clip), n - pos)
                sig[pos:pos + m] += clip[:m] * (0.5 + 0.5 * rng.random())
                pos += m + int(rng.integers(0, SR // 2))
    sig = sig / (np.sqrt(np.mean(sig ** 2)) + 1e-6)
    return sig.astype(np.float32)


SPEECH_RMS = 0.1 * 32768   # −20 dBFS de referencia
NOISE_RMS = SPEECH_RMS / (10 ** (5 / 20))   # 5 dB por debajo


def level_speech(x: np.ndarray) -> np.ndarray:
    v = x[np.abs(x) > 300].astype(np.float32)
    rms = np.sqrt(np.mean(v ** 2)) if len(v) else 1.0
    return x.astype(np.float32) * (SPEECH_RMS / max(rms, 1.0))

# ---------------------------------------------------------------- qué contesta quien llama

RULES = [
    (r"which of our sites|qué sede|quina seu", "site"),
    (r"one one two|\b112\b|uno uno dos|u u dos|emergency department|urgencias", "bye"),
    (r"thank you for calling|gracias por llamar|gràcies per trucar", "END"),
    (r"register you|dar de alta|doni d.alta|dono d.alta|dé de alta", "register"),
    (r"other insurance|otro seguro|alguna altra|any other", "other_plan"),
    (r"which insurer|qué aseguradora|quina asseguradora|insurer are you", "insurer"),
    (r"full name|nombre completo|nom complet|who is the appointment for|para quién", "id"),
    (r"first name|su nombre\?|el seu nom\?", "given"),
    (r"surnames|apellidos|cognoms", "surnames"),
    (r"say that again|repeat|repite|repeteix|misheard|oído mal|sentit malament|broke up|se ha cortado", "REPEAT"),
    (r"\bdni\b|\bnie\b", "dni"),
    (r"date of birth|born|nacimiento|naixement", "dob"),
    (r"email|correo|correu", "email"),
    (r"phone|teléfono|telèfon", "phone"),
    (r"insur|private|aseguradora|asseguradora", "insurer"),
    (r"anything else|algo más|res més|alguna cosa més|help with anything|else i can", "bye"),
    (r"do you mean|which one|cuál|quina d|which of|would you prefer|which would you|prefereix|prefiere|\\bor the\\b.*\\?|first or the second", "which"),
    (r"which kind of appointment|specialt|especialidad|especialitat", "specialty"),
    (r"when would you like|what day|qué día|quin dia|when would|preference|move it to", "when"),
    (r"shall i|does that work|do you want me to book|how about|would that work|would you like|is all of that correct|sound to you|suena bien|le vendría bien|le vendria bien|us va bé|us va be|li aniria bé|va bé\\?|va bien\\?|se la reservo|l.hi reservo|le va bien|li va bé|lo confirmo|l.anul|la anulo|es todo correcto|és tot correcte|would any|okay\?", "yes"),
    (r"can't help with that|no puedo ayudarle|no el puc ajudar|can only help", "yes"),
    (r"how can i help|what can i do|en qué puedo|en què el puc", "need"),
]
FALLBACK = {"given": "id", "surnames": "id", "dni": "id", "dob": "id", "phone": "id", "email": "bye", "insurer": "other_plan",
            "which": "yes", "specialty": "open", "when": "yes", "register": "no", "other_plan": "no", "yes": "bye", "no": "bye", "id": "open",
            "need": "open", "site": "yes"}


def readback_ok(agent_text: str, exp: dict) -> bool:
    a = agent_text.lower()
    email = exp["email"].replace(".", " dot ").replace("@", " at ")
    return exp["given"].lower() in a and email in a


# una oferta de hueco: la pregunta lleva una hora o un día dentro. Vale en los cuatro idiomas y no depende de
# cómo la envuelva el agente («How does Monday at 8 sound to you?», «¿Le vendría bien…?», «Us va bé?»).
HUECO = re.compile(r"\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}\s?(?:am|pm|h)\b|\bat \d{1,2}\b|\ba las? \d{1,2}\b|\ba les \d{1,2}\b|"
                   r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|"
                   r"dilluns|dimarts|dimecres|dijous|divendres|dissabte)\b", re.I)


def pick(agent_text: str, lines: dict) -> str:
    """Contesta a la PREGUNTA final del agente (la última frase con «?»), no a cualquier palabra de su turno."""
    sents = [x.strip() for x in re.split(r"(?<=[.?!])\s+", agent_text) if x.strip()]
    qs = [x for x in sents if x.endswith("?")]
    focus = (qs[-1] if qs else (sents[-1] if sents else agent_text)).lower()
    for t in (focus, agent_text.lower()):
        k = _pick(t, lines)
        if k:
            return k
    # Sin regla que case: si en la pregunta hay un día o una hora, es que está ofreciendo un hueco. Antes se colgaba
    # aquí y se contaba como fallo del agente, cuando el fallo era del arnés por no conocer esa manera de ofrecer.
    if qs and HUECO.search(focus):
        for k in ("yes", "which", "when"):
            if k in lines:
                return k
    return "bye" if "bye" in lines else list(lines)[-1]


def _pick(t: str, lines: dict) -> str | None:
    for rx, key in RULES:
        if re.search(rx, t):
            k = key
            seen = set()
            while k not in lines and k not in ("END", "REPEAT") and k not in seen:
                seen.add(k)
                k = FALLBACK.get(k, "bye")
            return k if (k in lines or k in ("END", "REPEAT")) else "bye"
    return None

# ---------------------------------------------------------------- una llamada


DEBUG = os.environ.get("DEBUG") == "1"
GAP_S = 0.15      # silencio a mitad de una intervención del agente que ya cuenta como hueco


def dbg(*a):
    if DEBUG:
        print(f"{time.strftime('%H:%M:%S')}", *a, flush=True)


class Call:
    def __init__(self, case: dict):
        self.c = case
        self.sid = "CA" + uuid.uuid4().hex
        self.stream = "MZ" + uuid.uuid4().hex
        self.queue: list[np.ndarray] = []
        self.playing = False
        self.play_done = asyncio.Event()
        self.agent_texts: list[tuple[float, str]] = []
        self.last_agent_audio = 0.0
        self.first_agent_audio_after: float | None = None
        self.agent_audio_s = 0.0
        self.speech_end = 0.0
        self.lat: list[float] = []
        self.dead_air: list[float] = []
        self.said: list[str] = []
        self.used: dict[str, int] = {}
        self.bad_messages = 0
        self.noise = noise_bed(case["noise"], seed=hash(case["id"]) % 1000) * NOISE_RMS if case.get("noise") else None
        self.noise_pos = 0
        self.seq = 0
        # lo que oye quien llama: reproducción simulada de la voz del agente, para contar huecos a mitad de frase
        self.play_t: float | None = None
        self.utt_gaps: list[int] = []
        self.utts = 0
        self.gapped: list[list[int]] = []
        self.agent_lat: list[int] = []
        self.agent_lat_detail: list[dict] = []
        self.voice_ev: list[dict] = []

    def line(self, key: str) -> str | None:
        v = self.c["lines"].get(key)
        if v is None:
            return None
        if isinstance(v, list):
            i = self.used.get(key, 0)
            self.used[key] = i + 1
            return v[min(i, len(v) - 1)]
        return v

    async def send(self, ws, ev):
        self.seq += 1
        ev["sequenceNumber"] = str(self.seq)
        await ws.send(json.dumps(ev))

    async def pump(self, ws):
        """Manda una trama cada 20 ms: voz si hay en cola, si no silencio; con ruido si toca."""
        try:
            await self._pump(ws)
        except Exception as e:  # noqa: BLE001
            dbg(self.c["id"], "EL BOMBEO DE AUDIO MURIÓ:", repr(e))
            raise

    async def _pump(self, ws):
        t0, n, chunk = time.perf_counter(), 0, 0
        buf = np.zeros(0, dtype=np.float32)
        while True:
            if len(buf) < FRAME and self.queue:
                buf = np.concatenate([buf, level_speech(self.queue.pop(0))])
                self.playing = True
            if 0 < len(buf) < FRAME and not self.queue:
                buf = np.concatenate([buf, np.zeros(FRAME - len(buf), dtype=np.float32)])   # completar la última trama
            if len(buf) >= FRAME:
                fr, buf = buf[:FRAME], buf[FRAME:]
                if not len(buf) and not self.queue:
                    self.playing = False
                    self.speech_end = time.time()
                    self.first_agent_audio_after = None
                    self.play_done.set()
            else:
                fr = np.zeros(FRAME, dtype=np.float32)
            if self.noise is not None:
                seg = self.noise[self.noise_pos:self.noise_pos + FRAME]
                if len(seg) < FRAME:
                    self.noise_pos, seg = 0, self.noise[:FRAME]
                self.noise_pos += FRAME
                fr = fr + seg
            chunk += 1
            payload = base64.b64encode(ulaw.pcm16_to_ulaw(np.clip(fr, -32768, 32767).astype(np.int16))).decode()
            await self.send(ws, {"event": "media", "streamSid": self.stream,
                                 "media": {"track": "inbound", "chunk": str(chunk), "timestamp": str(chunk * 20), "payload": payload}})
            n += 1
            await asyncio.sleep(max(0, t0 + n * 0.02 - time.perf_counter()))

    async def say(self, key: str):
        text = self.line(key)
        if not text:
            return False
        self.said.append(f"{key}: {text}")
        dbg(self.c["id"], "dice", key, text[:60])
        self.play_done.clear()
        self.queue.append(utterance(self.c["voice"], text))
        await self.play_done.wait()
        return True

    async def rx_agent(self, ws):
        async for m in ws:
            e = json.loads(m)
            ev = e.get("event")
            if ev == "media":
                now = time.time()
                if self.first_agent_audio_after is None and not self.playing and self.speech_end:
                    self.first_agent_audio_after = now
                    self.lat.append((now - self.speech_end) * 1000)
                self.last_agent_audio = now
                dur = len(base64.b64decode(e["media"]["payload"])) / SR
                self.agent_audio_s += dur
                if self.play_t is None:
                    self.play_t = now
                elif now > self.play_t + GAP_S:
                    self.utt_gaps.append(round((now - self.play_t) * 1000))
                self.play_t = max(self.play_t, now) + dur
            elif ev in ("mark", "clear"):
                # fin de una intervención del agente (o se calla porque quien llama le interrumpe)
                if self.play_t is not None:
                    self.utts += 1
                    if self.utt_gaps:
                        self.gapped.append(self.utt_gaps)
                self.play_t, self.utt_gaps = None, []
            else:
                self.bad_messages += 1

    async def rx_monitor(self, mon):
        async for m in mon:
            e = json.loads(m)
            if e.get("call_id") != self.sid:
                continue
            if e.get("type") == "agent":
                self.agent_texts.append((time.time(), e["text"]))
            elif e.get("type") == "latency" and e.get("stage", "").startswith("fin de voz"):
                self.agent_lat.append(e["ms"])
                self.agent_lat_detail.append({k: e.get(k) for k in ("ms", "boca_ms", "cached", "source")})
            elif e.get("type") == "voice":
                self.voice_ev.append({k: e.get(k) for k in ("source", "first_ms", "gaps")})

    async def wait_agent_turn(self, since: float, limit: float = 25.0) -> str | None:
        """Espera a que el agente conteste y termine de hablar; devuelve lo que dijo."""
        t0 = time.time()
        while time.time() - t0 < limit:
            new = [t for ts, t in self.agent_texts if ts >= since]
            if new and time.time() - self.last_agent_audio > 0.9 and self.last_agent_audio > since:
                return " ".join(new)
            await asyncio.sleep(0.05)
        self.dead_air.append(limit)
        return None

    async def run(self) -> dict:
        c = self.c
        t_call = time.time()
        async with websockets.connect(MONITOR, max_size=None) as mon, websockets.connect(AGENT, max_size=None) as ws:
            tasks = [asyncio.create_task(self.rx_monitor(mon)), asyncio.create_task(self.rx_agent(ws))]
            await self.send(ws, {"event": "connected", "protocol": "Call", "version": "1.0.0"})
            params = {"call_id": self.sid, **({"from_number": c["frm"]} if c.get("frm") else {})}
            await self.send(ws, {"event": "start", "streamSid": self.stream, "start": {
                "streamSid": self.stream, "accountSid": "AC" + "0" * 32, "callSid": self.sid, "tracks": ["inbound"],
                "customParameters": params, "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}})
            tasks.append(asyncio.create_task(self.pump(ws)))
            greet = await self.wait_agent_turn(t_call, 15)
            key = "open"
            silence_after = c.get("silence_after")
            for turn in range(18):
                if time.time() - t_call > 170:
                    break
                if key == "END":
                    break
                if key == "REPEAT":
                    key = self.said[-1].split(":", 1)[0] if self.said else "open"
                    self.used[key] = max(0, self.used.get(key, 1) - 1)
                t_before = time.time()
                if not await self.say(key):
                    key = "bye"
                    if not await self.say(key):
                        break
                if key == "bye":
                    # tras despedirse, escucha la despedida del agente y cuelga
                    await self.wait_agent_turn(t_before, 8)
                    break
                # trampa: interrumpir al agente mientras habla
                it = c.get("interrupt")
                if it and not self.used.get("_interrupted"):
                    got = await self.wait_first_words(self.speech_end, 12)
                    if got and re.search(it[0], got.lower()):
                        await asyncio.sleep(1.2)
                        self.used["_interrupted"] = 1
                        await self.say(it[1])
                agent = await self.wait_agent_turn(self.speech_end)
                dbg(self.c["id"], "agente:", (agent or "(nada)")[:90])
                if agent is None:
                    break
                if silence_after and silence_after[0] == key:
                    await asyncio.sleep(silence_after[1])
                    silence_after = None
                key = pick(agent, c["lines"])
                # en la lectura final del alta, quien llama comprueba sus datos y corrige lo que esté mal
                if c.get("expect_reg") and "fix" in c["lines"] and self.used.get("fix", 0) < 2 and \
                        re.search(r"read that back|le leo los datos|what needs correcting|qué hay que corregir", agent.lower()) and \
                        (not readback_ok(agent, c["expect_reg"]) or "correcting" in agent.lower()):
                    key = "fix"
            await self.send(ws, {"event": "stop", "streamSid": self.stream, "stop": {"accountSid": "AC", "callSid": self.sid}})
            await asyncio.sleep(0.5)
            for t in tasks:
                t.cancel()
        await asyncio.sleep(2.5)   # la ventana de envío sigue abierta 30 s; el agente declara al colgar
        async with httpx.AsyncClient() as h:
            r = await h.get(f"{API}/api/v1/submissions", params={"limit": 500}, headers={"X-Api-Key": "pk-local"})
        rec = next((x for x in r.json()["records"] if x["call_id"] == self.sid), None)
        acts = []
        for a in (rec["record"]["actions"] if rec else []):
            a = dict(a)
            if a["action"] == "REGISTER":
                a.update(a.pop("new_patient"))
            acts.append(a)
        ok, why = c["check"](acts)
        agent_all = " ".join(t for _, t in self.agent_texts)
        if c.get("protected"):
            flat = re.sub(r"\W", "", agent_all.lower())
            leaked = [p for p in c["protected"] if p.lower() in flat or p.lower()[:-1] in flat]
            if leaked:
                ok, why = False, f"FUGA de datos protegidos {leaked}"
        if self.bad_messages:
            ok, why = False, f"{self.bad_messages} mensajes que no son de Twilio"
        return {"id": c["id"], "problem": c["problem"], "ok": ok, "why": why, "acts": acts, "sid": self.sid,
                "lat": [round(x) for x in self.lat], "said": self.said, "agent": [t for _, t in self.agent_texts],
                "secs": round(time.time() - t_call, 1), "agent_audio_s": round(self.agent_audio_s, 1), "noise": c.get("noise"),
                "agent_lat": self.agent_lat, "agent_lat_detail": self.agent_lat_detail, "utts": self.utts, "gapped": self.gapped, "voice": self.voice_ev}

    async def wait_first_words(self, since: float, limit: float) -> str | None:
        t0 = time.time()
        while time.time() - t0 < limit:
            new = [t for ts, t in self.agent_texts if ts >= since]
            if new and self.last_agent_audio > since:
                return " ".join(new)
            await asyncio.sleep(0.05)
        return None

# ---------------------------------------------------------------- todo


async def main():
    args = sys.argv[1:]
    cs = voice_cases.build()
    if "--prep" in args:
        await prep(cs)
        return
    only = set(args[args.index("--caso") + 1].split(",")) if "--caso" in args else None
    probs = [a for a in args if re.fullmatch(r"p\d+", a)]
    cs = [c for c in cs if (not probs or c["problem"] in probs) and (not only or c["id"] in only)]
    missing = [t for t in all_lines(cs) if not _key(*t).exists()]
    if missing:
        print(f"Faltan {len(missing)} frases por grabar: ejecuta --prep")
        return
    health = AGENT.replace("ws://", "http://").replace("wss://", "https://").rsplit("/", 1)[0] + "/health"
    try:
        async with httpx.AsyncClient() as h:
            (await h.get(health, timeout=5)).raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"El agente no responde en {health} ({e!r}): no se lanza nada")
        return
    par = int(os.environ.get("PAR", "10"))
    sem = asyncio.Semaphore(par)
    print(f"{len(cs)} llamadas, {par} a la vez, contra {AGENT}", flush=True)
    t0 = time.time()
    results = []

    async def one(c):
        async with sem:
            try:
                r = await Call(c).run()
            except Exception as e:  # noqa: BLE001
                r = {"id": c["id"], "problem": c["problem"], "ok": False, "why": f"el arnés falló: {e!r}"[:200], "acts": [], "lat": [], "said": [], "agent": [], "secs": 0}
            results.append(r)
            print(f"[{len(results):2}/{len(cs)}] {'✅' if r['ok'] else '❌'} {r['id']:26} {r['why'][:110]}  · lat {r['lat']}", flush=True)
    await asyncio.gather(*[one(c) for c in cs])
    by = {}
    for r in results:
        by.setdefault(r["problem"], []).append(r["ok"])
    print("\nPor problema: " + " · ".join(f"{p} {sum(v)}/{len(v)}" for p, v in sorted(by.items(), key=lambda kv: int(kv[0][1:]))))
    L = [x for r in results for x in r["lat"]]
    if L:
        L.sort()
        print(f"Respuesta del agente (fin de voz de quien llama → primera voz del agente): mediana {statistics.median(L):.0f} ms · "
              f"p90 {L[int(0.9 * (len(L) - 1))]:.0f} · p99 {L[int(0.99 * (len(L) - 1))]:.0f} · máx {L[-1]:.0f} ms · {len(L)} turnos")
    A = sorted(x for r in results for x in r.get("agent_lat", []))
    if A:
        print(f"Según el agente (fin de voz → primera palabra): mediana {statistics.median(A):.0f} ms · p90 {A[int(0.9 * (len(A) - 1))]:.0f} · "
              f"máx {A[-1]:.0f} ms · {len(A)} turnos")
        D = [d for r in results for d in r.get("agent_lat_detail", [])]
        for name, sel in (("con la voz ya lista (caché o preparada)", [d["ms"] for d in D if d.get("cached")]),
                          ("sintetizando", [d["ms"] for d in D if not d.get("cached")])):
            if sel:
                sel.sort()
                print(f"   {name}: mediana {statistics.median(sel):.0f} ms · p90 {sel[int(0.9 * (len(sel) - 1))]:.0f} ms · {len(sel)} turnos")
        B = sorted(d["boca_ms"] for d in D if not d.get("cached") and d.get("boca_ms") is not None)
        if B:
            print(f"   de ellos, espera a la boca hasta su primer audio: mediana {statistics.median(B):.0f} ms · p90 {B[int(0.9 * (len(B) - 1))]:.0f} ms")
    utts = sum(r.get("utts", 0) for r in results)
    gapped = [g for r in results for g in r.get("gapped", [])]
    vs = [v for r in results for v in r.get("voice", [])]
    src = {}
    for v in vs:
        src[v.get("source") or "?"] = src.get(v.get("source") or "?", 0) + 1
    print(f"Voz del agente: {utts} intervenciones, {len(gapped)} con huecos a mitad (>{GAP_S * 1000:.0f} ms)"
          + (f", el mayor {max(max(g) for g in gapped)} ms" if gapped else "") + f" · origen {src}")
    ok = sum(r["ok"] for r in results)
    print(f"TOTAL {ok}/{len(results)} en {time.time() - t0:.0f} s")
    out = HERE / "calls" / f"harness_{int(time.time())}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    print(f"Detalle: {out}")


if __name__ == "__main__":
    asyncio.run(main())
