"""El agente de Prosper: un WebSocket con el protocolo de Twilio Media Streams, y una consola en directo.

    cd agent && ../.venv/bin/uvicorn server:app --host 0.0.0.0 --port 7860
    wss://<túnel>/ws          ← lo que se registra en el panel de Prosper
    http://<host>:7860/       ← consola: llamadas en curso y pasadas, qué pensó el agente y por qué

Por el socket del arnés solo van mensajes de Twilio (media, mark, clear). Todo lo demás va al canal de la consola.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import base64
import json
import logging
import sys
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "demo"))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("demo_server", HERE.parent / "demo" / "server.py")
demo = importlib.util.module_from_spec(_spec)   # la tubería de voz de la demo: oído, turnos, deshacer, boca
_spec.loader.exec_module(demo)
import ulaw  # noqa: E402
from brain import API, Brain  # noqa: E402
from conv import Conv  # noqa: E402

AGENT = os.environ.get("AGENT", "v1")      # v1: máquina de estados (brain.py) · v2: planificador con herramientas (conv.py)
from jev import JEV  # noqa: E402
from voice import MOUTH, make_ears  # noqa: E402

log = logging.getLogger("prosper")
CALLS = HERE / "calls"
CALLS.mkdir(exist_ok=True)


class Hub:
    """Canal de la consola: eventos de todas las llamadas, en directo."""

    def __init__(self):
        self.subs: set[asyncio.Queue] = set()
        self.active: dict[str, dict] = {}
        self.recent: dict[str, list] = defaultdict(list)

    def publish(self, call_id: str, ev: dict):
        ev = {"call_id": call_id, "ts": time.time(), **ev}
        self.recent[call_id].append(ev)
        if len(self.recent[call_id]) > 800:
            self.recent[call_id] = self.recent[call_id][-800:]
        for q in list(self.subs):
            if q.qsize() < 2000:
                q.put_nowait(ev)


HUB = Hub()


def english_phrases() -> list[tuple[str, str]]:
    """Las frases fijas del agente con su lengua (la boca elige voz y modelo por lengua)."""
    import say as S
    out = []
    for key, by_lang in S.T.items():
        if key == "reg_ask":
            for lang, d in by_lang.items():
                out += [(v, lang) for v in d.values()]
            continue
        for lang, vs in by_lang.items():
            out += [(v, lang) for v in vs if "{" not in v]
    if AGENT == "v2":
        import conv as C
        out += [(v, lang) for d in C.ACK.values() for lang, v in d.items()]
        out += [(v, lang) for lang, v in C.SORRY.items() if lang in ("en", "es", "ca")]
        out += [(v, lang) for lang, v in C.EMERGENCY.items() if lang in ("en", "es", "ca")]
        out += [(C.GREET.format(dp=dp), "en") for dp in ("morning", "afternoon", "evening")]
    return out


async def prosper_vocabulary():
    """El transcriptor, sesgado hacia las especialidades de la clínica (no hacia los nombres de la demo)."""
    import voice
    # Solo especialidades: los nombres propios del catálogo sesgaban los de los pacientes («Pau Vidal Serra» →
    # «Pablo Vilar Sáenz», «Elena» → «Arenal») y las letras del DNI («S» → «ASISA»). Las sedes las encaja Jev.
    # Y ni eso: con murmullo de fondo el transcriptor «oía» «gynaecology». Sin vocabulario; Jev encaja lo demás.
    voice.VOCAB[:] = []


@asynccontextmanager
async def lifespan(app):
    await asyncio.gather(JEV.warm(), MOUTH.warm(), prosper_vocabulary(), return_exceptions=True)
    asyncio.create_task(MOUTH.prewarm(english_phrases(), parallel=3, fmt=TwilioCall.AUDIO_FMT))
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
async def console():
    return FileResponse(HERE / "static" / "console.html")


@app.get("/health")
async def health():
    return {"ok": True, "active_calls": len(HUB.active)}


MONITOR_TOKEN = os.environ.get("MONITOR_TOKEN", "")
CALL_TOKEN = os.environ.get("CALL_TOKEN", "")     # solo en la instancia local: llamadas desde la web de la arena


def from_internet(headers) -> bool:
    """Lo que entra por el túnel de Cloudflare trae cf-connecting-ip; lo del propio NAS (arneses, consola), no."""
    return bool(headers.get("cf-connecting-ip"))


def token_ok(expected: str, given: str | None) -> bool:
    return secrets.compare_digest((given or "").encode(), expected.encode())


@app.middleware("http")
async def api_token(request, call_next):
    """Con MONITOR_TOKEN, los datos de las llamadas (/api/…) desde internet solo con ?t=… (la salud y la llamada, abiertas)."""
    if MONITOR_TOKEN and request.url.path.startswith("/api/") and from_internet(request.headers) and \
            not token_ok(MONITOR_TOKEN, request.query_params.get("t")):
        return JSONResponse({"detail": "token"}, status_code=401)
    return await call_next(request)


@app.get("/api/calls")
async def calls():
    items = []
    for f in sorted(CALLS.glob("CA*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:100]:
        r = json.loads(f.read_text())
        if not isinstance(r, dict):
            continue
        items.append({k: r.get(k) for k in ("call_id", "outcome", "reason", "language", "duration_s", "patient_id", "from_number", "ended_by")})
    return {"active": list(HUB.active.values()), "past": items}


@app.get("/api/calls/{cid}")
async def call_detail(cid: str):
    f = CALLS / f"{cid}.json"
    if f.exists():
        return JSONResponse(json.loads(f.read_text()))
    return JSONResponse({"call_id": cid, "live": True, "events": HUB.recent.get(cid, [])})


@app.get("/api/active")
async def active():
    return list(HUB.active.values())


@app.get("/api/events/{cid}")
async def call_events(cid: str):
    return {"call_id": cid, "events": HUB.recent.get(cid, [])}


@app.websocket("/monitor")
async def monitor(ws: WebSocket):
    # con MONITOR_TOKEN, el monitor (transcripciones y nombres) solo con ?t=… correcto; /ws (la llamada) no cambia
    if MONITOR_TOKEN and from_internet(ws.headers) and not token_ok(MONITOR_TOKEN, ws.query_params.get("t")):
        await ws.close(code=4401)
        return
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue()
    HUB.subs.add(q)
    try:
        for cid, evs in list(HUB.recent.items())[-10:]:
            for ev in evs[-200:]:
                await ws.send_text(json.dumps(ev, ensure_ascii=False, default=str))
        while True:
            ev = await q.get()
            await ws.send_text(json.dumps(ev, ensure_ascii=False, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        HUB.subs.discard(q)


@app.websocket("/ws")
async def twilio(ws: WebSocket):
    # CALL_TOKEN (solo instancia local): la arena llama desde el navegador con ?t=…; Prosper llama a producción, sin él
    if CALL_TOKEN and from_internet(ws.headers) and not token_ok(CALL_TOKEN, ws.query_params.get("t")):
        await ws.close(code=4401)
        return
    await ws.accept()
    call = TwilioCall(ws)
    try:
        await call.run()
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.exception("llamada: %s", e)
    finally:
        await call.close()


class TwilioCall(demo.VoiceCall):
    """La misma orquestación de turnos que la demo, con el cerebro de Prosper y el cable de Twilio."""
    AUDIO_FMT = "ulaw8"      # la boca da µ-law de 8 kHz directamente: sin remuestrear

    def __init__(self, ws: WebSocket):
        super().__init__(ws)
        self.stream_sid = ""
        self.call_sid = ""
        self.send_lock = asyncio.Lock()
        self.first_turn = False          # sin sesión de sondeo gallego: aquí manda el inglés
        self.t_connect = time.time()

    # la percepción es la del cerebro de Prosper (Jev + extracción)
    async def perceive(self, text: str, spec: bool = False):
        return await self.call.perceive(text, spec=spec)

    async def decide_language(self, p, full):
        self.first_turn = False
        return p, full

    async def emit(self, typ: str, **kw):
        HUB.publish(self.call_sid or "?", {"type": typ, **kw})

    async def send_json(self, obj: dict):
        async with self.send_lock:
            await self.ws.send_text(json.dumps(obj))

    async def run(self):
        while True:
            msg = await self.ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            raw = msg.get("text") or (msg.get("bytes") or b"").decode("utf-8", "ignore")
            if not raw:
                continue
            m = json.loads(raw)
            ev = m.get("event")
            if ev == "start":
                await self.on_start(m["start"])
            elif ev == "media" and self.ears:
                try:
                    pcm8 = ulaw.ulaw_to_pcm16(base64.b64decode(m["media"]["payload"]))
                    await self.on_audio(ulaw.up_8k_to_16k(pcm8))
                except Exception as e:  # noqa: BLE001
                    log.warning("media: %s", e)
            elif ev == "stop":
                await self.emit("log", msg="el arnés colgó (stop)")
                await self.finalize("stop")
                return

    async def on_start(self, st: dict):
        self.stream_sid = st.get("streamSid", "")
        self.call_sid = st.get("callSid") or (st.get("customParameters") or {}).get("call_id", "")
        frm = (st.get("customParameters") or {}).get("from_number")
        self.call = (Conv if AGENT == "v2" else Brain)(call_id=self.call_sid, from_number=frm, stream_sid=self.stream_sid)
        if hasattr(self.call, "on_early"):
            self.call.on_early = self.early_ack
        HUB.active[self.call_sid] = {"call_id": self.call_sid, "from_number": frm, "started": time.time()}
        await self.emit("call_started", call_id=self.call_sid, from_number=frm)
        # el saludo sale ya; la ficha de la línea y el oído se preparan en paralelo
        self.ears = make_ears(self.on_interim, self.on_final, [[], []])
        begin = asyncio.create_task(self.call.begin())
        ears = asyncio.create_task(self.ears.start())
        try:
            await asyncio.wait_for(asyncio.shield(begin), timeout=2.5)
        except asyncio.TimeoutError:
            pass
        self.speak_task = self.spawn(self.speak_outs(self.call.opening()))
        try:
            for o in await begin:
                if o["kind"] == "event":
                    await self.emit("trace", event=o["event"])
            await ears
        except Exception as e:  # noqa: BLE001
            await self.emit("log", msg=f"arranque: {e}")
        await self.emit("state", state=self.call.snapshot())

    async def speak(self, text: str, act: str, source: str = "plantilla"):
        """La voz del agente en µ-law a 8 kHz, en tramas de 20 ms y a ritmo casi real (para poder callarse)."""
        if not self.call:
            return
        self.call.spoken(text)
        self.agent_text = text
        self.agent_start_t = time.time()
        r = self.render(text)
        await self.emit("agent", text=text, act=act, source=source, cached=r.cached or r.done, audio=True)
        first, buf, t_start, sent_s, wall0 = True, b"", None, 0.0, time.time()
        gaps: list[int] = []
        async for chunk in r.stream():
            if first:
                first = False
                if self.t_speech_end:
                    await self.emit("latency", stage="fin de voz → primera palabra", ms=round((time.perf_counter() - self.t_speech_end) * 1000),
                                    boca_ms=r.first_ms, cached=r.cached, source=r.source)
                    self.t_speech_end = None
            # la boca de ElevenLabs ya da µ-law de 8 kHz; la de Gemini (o su respaldo), PCM de 24 kHz
            buf += chunk if r.fmt == "ulaw8" else ulaw.pcm16_to_ulaw(ulaw.down_24k_to_8k(chunk))
            while len(buf) >= 160:
                frame, buf = buf[:160], buf[160:]
                if t_start is None:
                    t_start, wall0 = time.perf_counter(), time.time()
                # como mucho 400 ms por delante del tiempo real
                ahead = sent_s - (time.perf_counter() - t_start)
                if ahead > 0.4:
                    await asyncio.sleep(ahead - 0.4)
                elif ahead < -0.1:
                    # la boca se ha quedado atrás: quien llama oye un hueco a mitad de frase
                    gaps.append(round(-ahead * 1000))
                    t_start -= ahead
                await self.send_json({"event": "media", "streamSid": self.stream_sid, "media": {"payload": base64.b64encode(frame).decode()}})
                sent_s += 0.02
                self.speaking_until = wall0 + sent_s
        if buf:
            await self.send_json({"event": "media", "streamSid": self.stream_sid, "media": {"payload": base64.b64encode(buf).decode()}})
        await self.send_json({"event": "mark", "streamSid": self.stream_sid, "mark": {"name": act[:40] or "say"}})
        await self.emit("voice", source=r.source or ("caché" if r.cached else ""), first_ms=r.first_ms, gaps=gaps, text=text[:80])

    def early_ack(self, text: str):
        """Acuse del planificador («Un momento, lo miro») mientras trabajan las herramientas: suena ya, y la respuesta
        espera a que termine en vez de cortarlo."""
        self.ack_task = self.spawn(self.speak_outs([{"kind": "say", "text": text, "act": "ack"}]))

    async def speak_outs(self, outs: list[dict]):
        ack = getattr(self, "ack_task", None)
        if ack is not None and ack is not asyncio.current_task() and not ack.done():
            try:
                await asyncio.shield(ack)
            except Exception:  # noqa: BLE001
                pass
        await super().speak_outs(outs)

    async def maybe_barge(self, full: str, p):
        before = self.speak_task and not self.speak_task.done()
        await super().maybe_barge(full, p)
        if before and self.speak_task and self.speak_task.done():
            await self.send_json({"event": "clear", "streamSid": self.stream_sid})

    async def finalize(self, why: str):
        if self.finalized or not self.call:
            return
        self.finalized = True
        try:
            for o in await self.call.finalize():   # nunca se cuelga sin declarar algo
                if o["kind"] == "event":
                    await self.emit("trace", event=o["event"])
        except Exception as e:  # noqa: BLE001
            await self.emit("log", msg=f"no se pudo declarar al cerrar: {e}")
        rep = await self.call.report()
        rep.update({"ended_by": why, "from_number": self.call.s.from_number, "transcript": self.call.s.history})
        (CALLS / f"{self.call_sid or 'sin-id'}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        HUB.active.pop(self.call_sid, None)
        await self.emit("report", report={k: v for k, v in rep.items() if k != "trace"})

    async def close(self):
        await self.finalize("disconnect")
        if self.ears:
            await self.ears.close()
        for t in list(self.bg):
            t.cancel()
