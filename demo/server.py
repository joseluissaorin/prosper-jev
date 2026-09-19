"""Servidor de la demo: una llamada por WebSocket.

    ../.venv/bin/uvicorn server:app --port 8765     (desde demo/)

El navegador manda audio PCM de 16 kHz y recibe la voz del agente (PCM de 24 kHz) y los eventos del panel.

Turnos de palabra (lo aprendido en las pruebas de voz):
- Los parciales del transcriptor van ~1 s por detrás del audio: responder con el parcial al callarse es inseguro.
  Se responde con el definitivo (llega 0,2-1 s después), y el oído es redundante (dos sesiones, gana la primera)
  porque a veces una sesión pierde o retrasa un definitivo.
- Una pausa no es el final del turno: si la persona vuelve a hablar poco después de que respondamos, se deshace
  la respuesta (se restaura el estado) y lo nuevo se une al mismo turno.
- Un turno que empezó antes de nuestra última respuesta no puede contestarla (un «sí» que cerraba la frase
  anterior nunca confirma una lectura que no se ha oído).
"""
from __future__ import annotations

import asyncio
import copy
import difflib
import json
import logging
import re
import time
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import clinic
import nlg
import system2
from jev import JEV, JevError, choice
from policy import Call
from sense import LANG, perceive
from voice import MOUTH, Ears, Vad, fixed_phrases, make_vad

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("llamada")
HERE = Path(__file__).parent
CALLS = HERE / "calls"
CALLS.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app):
    await asyncio.gather(JEV.warm(), MOUTH.warm(), return_exceptions=True)
    asyncio.create_task(MOUTH.prewarm(fixed_phrases()))
    yield


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def no_cache(request, call_next):
    """La demo cambia a menudo: que el navegador no guarde en caché ni la página ni los estáticos."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-store"
    return resp
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/calls")
async def calls():
    out = []
    for f in sorted(CALLS.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:50]:
        if f.name.startswith("sim_"):
            continue
        r = json.loads(f.read_text())
        out.append({k: r.get(k) for k in ("call_id", "outcome", "reason", "language", "duration_s", "patient_id")})
    return out


@app.get("/api/calls/{cid}")
async def call_detail(cid: str):
    f = CALLS / f"{cid}.json"
    return JSONResponse(json.loads(f.read_text())) if f.exists() else JSONResponse({"error": "no existe"}, 404)


@app.get("/api/clinic")
async def clinic_view():
    return {
        "today": clinic.TODAY.isoformat(),
        "doctors": [{"id": d.id, "name": d.short, "service": nlg.service_name("es", d.service), "site": clinic.SITES[d.site]["name"],
                     "full": d.full} for d in clinic.DOCTORS.values()],
        "patients": [{"id": p.id, "name": p.full_name, "dob": p.dob.isoformat(), "age": p.age()} for p in clinic.PATIENTS.values()],
        "appointments": [{"id": a.id, "patient": clinic.PATIENTS[a.patient_id].full_name, "doctor": clinic.DOCTORS[a.doctor_id].short,
                          "start": a.start.isoformat(timespec="minutes"), "status": a.status}
                         for a in sorted(clinic.APPOINTMENTS.values(), key=lambda a: a.start)],
        "stats": {"jev_calls": JEV.calls, "jev_tokens": JEV.tokens, "jev_cost_usd": round(JEV.tokens * 0.042 / 1e6, 5)},
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    vc = VoiceCall(ws)
    try:
        await vc.run()
    except WebSocketDisconnect:
        pass
    finally:
        await vc.close()


def _fold(s: str) -> str:
    return clinic.fold(s)


def _k(t: str) -> str:
    """Clave para reutilizar el juicio de Jev: sin mayúsculas, tildes ni puntuación."""
    return " ".join("".join(c if c.isalnum() else " " for c in clinic.fold(t)).split())


_GLUE = re.compile(r"([a-záéíóúüñç.?!,])([A-ZÁÉÍÓÚÜÑÇ¿¡])")


def _latin(w: str) -> bool:
    letters = [c for c in w if c.isalpha()]
    return not letters or any(unicodedata.name(c, "").startswith("LATIN") for c in letters)


def _unglue(t: str) -> str:
    """El STT a veces pega frases («GómezNací»): se separan. Y con ruido alucina palabras en otros alfabetos
    (árabe, cirílico): todas las lenguas de la clínica usan el latino, así que esas se quitan."""
    t = " ".join(w for w in t.split() if _latin(w))
    return _GLUE.sub(r"\1 \2", t).strip()


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _k(a), _k(b)).ratio()


def _compact(p) -> dict:
    out = {}
    for k, a in p.raw.items():
        if a["type"] == "noul":
            out[k] = {"t": "n", "p": a["noul"]}
        elif a["type"] == "choice":
            top = sorted(a["probabilities"].items(), key=lambda x: -x[1])[:4]
            out[k] = {"t": "c", "c": a["choice"], "conf": a["confidence"], "top": top}
    return out


class VoiceCall:
    """Una llamada. Las subclases pueden cambiar el cerebro (self.call) y la percepción (self.perceive)."""
    RESPOND_WAIT_S = 1.1    # si Jev dice que la frase está a medias, se espera como mucho esto
    UNDO_WINDOW_S = 1.2     # si vuelve a hablar antes de esto, la respuesta se deshace
    WATCHDOG_S = 3.0        # si ninguna sesión devuelve el definitivo, se usa el parcial

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.call: Call | None = None
        self.tts = True
        self.vad = make_vad()
        self.ring: list[bytes] = []      # últimos ~400 ms de audio (se mandan al abrir el turno)
        self.turn_open = False
        self.close_task: asyncio.Task | None = None
        self.vad_end_t = 0.0
        self.run_t = 0.0
        self.closed_t = 0.0
        self.turn_text = False
        self.turn_pcm: list[bytes] = []
        self.act_audio: dict[int, list[bytes]] = {}
        self.noisy = False               # voz de fondo continua: el turno lo marca el texto, no el detector
        self.interim_t = 0.0
        self.ears: Ears | None = None
        self.segments: list[str] = []
        self.interim = ""
        self.spec: dict[str, object] = {}
        self.spec_busy = False
        self.spec_next: str | None = None
        self.speak_task: asyncio.Task | None = None
        self.speaking_until = 0.0
        self.agent_text = ""
        self.t_speech_end: float | None = None
        self.respond_timer: asyncio.TimerHandle | None = None
        self.first_turn = True
        self.first_act: int | None = None
        self.probe_lang: tuple | None = None
        self.probe_busy = False
        self.answered_text = ""
        self.answered_act = -1           # intervención del transcriptor del texto contestado
        self.final_act = -1              # intervención del último definitivo aceptado
        self.turn_t0: float | None = None
        self.last_response_t = 0.0
        self.undo: tuple | None = None
        self.undo_requested = False
        self.continuation = False
        self.act_start_t = 0.0
        self.lock = asyncio.Lock()
        self.finalized = False
        self.bg: set[asyncio.Task] = set()

    # ------------------------------------------------------------ utilidades

    async def perceive(self, text: str, spec: bool = False):
        return await perceive(self.call.s, text)

    async def emit(self, typ: str, **kw):
        try:
            await self.ws.send_text(json.dumps({"type": typ, **kw}, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001
            pass

    def spawn(self, coro):
        t = asyncio.create_task(coro)
        self.bg.add(t)

        def done(task):
            self.bg.discard(task)
            if not task.cancelled() and task.exception() is not None:
                e = task.exception()
                log.error("tarea de la llamada falló: %r", e, exc_info=e)
                asyncio.ensure_future(self.emit("log", msg=f"ERROR en {coro.__qualname__}: {e!r}"[:300]))
        t.add_done_callback(done)
        return t

    @property
    def agent_speaking(self) -> bool:
        return time.time() < self.speaking_until

    def is_echo(self, text: str) -> bool:
        if not self.agent_speaking or not self.agent_text or len(text) < 6:
            return False
        a, b = _k(self.agent_text), _k(text)
        return b in a or difflib.SequenceMatcher(None, a[: len(b) + 20], b).ratio() > 0.6

    @staticmethod
    def contained(a: str, b: str) -> bool:
        fa, fb = _k(a), _k(b)
        return bool(fa) and (fa in fb or difflib.SequenceMatcher(None, fa, fb[-len(fa) - 10:]).ratio() > 0.8)

    # ------------------------------------------------------------ bucle principal

    async def run(self):
        while True:
            msg = await self.ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                try:
                    await self.on_audio(msg["bytes"])
                except Exception as e:  # noqa: BLE001
                    log.exception("audio: %s", e)
                    await self.emit("log", msg=f"error en el audio: {e}")
            elif msg.get("text"):
                m = json.loads(msg["text"])
                t = m.get("type")
                if t == "start":
                    await self.start(m.get("lang", "auto"), m.get("tts", True))
                elif t == "text" and self.call:
                    await self.on_typed(m.get("text", ""))
                elif t == "hangup":
                    await self.finalize("hangup")
                elif t == "tts":
                    self.tts = bool(m.get("on"))

    async def start(self, lang: str, tts: bool):
        self.tts = tts
        fixed = lang if lang in nlg.LANGS else None
        self.call = Call(lang=fixed)
        await self.emit("call_started", call_id=self.call.s.call_id, lang=lang)
        # Sin idioma fijado: una sesión sin pista y otra con pista gallega (sin pista, el gallego puede salir
        # traducido al español). Con idioma fijado: dos sesiones con la misma pista, por redundancia.
        langs = [[nlg.STT_CODES[fixed]]] * 2 if fixed else [[], ["gl-ES"]]
        self.ears = Ears(self.on_interim, self.on_final, langs)
        try:
            await self.ears.start()
        except Exception as e:  # noqa: BLE001
            await self.emit("log", msg=f"sin transcripción de voz ({e}): solo texto")
            self.ears = None
        await self.speak_outs(self.call.opening())
        await self.emit("state", state=self.call.snapshot())

    async def close(self):
        await self.finalize("disconnect")
        if self.ears:
            await self.ears.close()
        for t in list(self.bg):
            t.cancel()

    # ------------------------------------------------------------ audio entrante

    async def on_audio(self, pcm: bytes):
        """Un turno = UNA intervención del transcriptor, con los silencios incluidos. Las pausas a mitad de frase
        no la cortan (cortar en cada pausa hacía que el transcriptor perdiera o duplicara texto). El turno lo
        cierra turn_watch: silencio + texto estable + Jev dice que la frase está terminada."""
        if not self.ears or self.finalized:
            return
        events = self.vad.feed(pcm)
        started = False
        for kind, _ in events:
            if kind == "start":
                self.act_start_t = time.time()
                self.run_t = time.perf_counter()         # inicio de este tramo de voz sin pausas
                if not self.turn_open:
                    started = await self.open_turn()
                await self.emit("vad", state="speech", agent_speaking=self.agent_speaking)
            elif kind == "end":
                self.t_speech_end = self.vad_end_t = time.perf_counter()
                self.noisy = False                       # hay silencios de verdad: no es ruido continuo
                await self.emit("vad", state="silence")
        # Con ruido de voz continuo (una tele) el detector nunca calla: el turno se reabre solo y es el texto
        # el que dice si alguien habla.
        if not self.turn_open and self.noisy and self.vad.speaking and time.perf_counter() - self.closed_t > 0.3:
            self.act_start_t = time.time()
            started = await self.open_turn()
        if self.turn_open and not started:
            self.turn_pcm.append(pcm)
            await self.ears.push(pcm)
        self.ring = (self.ring + [pcm])[-20:]

    async def open_turn(self) -> bool:
        if self.turn_t0 is None:
            self.turn_t0 = time.time()
        if self.respond_timer:
            self.respond_timer.cancel()
            self.respond_timer = None
        self.turn_open = True
        self.turn_text = False           # ¿ha llegado ya algún parcial de ESTE turno?
        pre = b"".join(self.ring)
        act = await self.ears.activity_start(pre)
        # el audio de cada intervención, por si hay que volver a transcribirla (cifras)
        self.turn_pcm = [pre]
        self.act_audio[act] = self.turn_pcm
        for old in [a for a in self.act_audio if a < act - 6]:
            del self.act_audio[old]
        if self.first_act is None:
            self.first_act = act
        self.close_task = self.spawn(self.turn_watch(act))
        return True

    async def turn_watch(self, act: int):
        """Decide cuándo se cierra el turno. Normal: silencio del detector + texto estable + «terminada» de Jev.
        Ruido de voz de fondo: el detector sigue oyendo voz, pero el texto ya no cambia."""
        t_open = time.perf_counter()
        while self.turn_open and self.ears and self.ears.act == act:
            await asyncio.sleep(0.05)
            now = time.perf_counter()
            speaking = self.vad.speaking
            silence = 0.0 if speaking else now - self.vad_end_t
            stable = now - self.interim_t if self.interim else 0.0
            full = " ".join(self.segments + [self.interim]).strip()
            p = self.spec.get(_k(full)) if full else None
            fin = p.finished if p is not None else None
            why = None
            if not speaking:
                if (silence >= 1.3
                        or (silence >= 0.3 and stable >= 0.3 and fin is not None and fin >= 0.8)
                        or (silence >= 0.7 and stable >= 0.5 and (fin is None or fin >= 0.4))):
                    why = f"silencio {silence:.2f} s"
            # voz de fondo: el detector lleva ≥3 s oyendo voz SIN una sola pausa (una persona hace pausas; una tele no)
            # y el texto no cambia. Con menos, es el retraso del transcriptor en una frase normal.
            elif self.turn_text and self.interim and now - self.run_t >= 3.0 and \
                    ((stable >= 1.5 and fin is not None and fin >= 0.7) or stable >= 3.0):
                why = "el detector oye voz de fondo pero el texto no cambia"
                self.noisy = True
            elif now - t_open >= 25:
                why = "turno de más de 25 s"
                self.noisy = True
            if why:
                self.turn_open = False
                self.closed_t = now
                await self.ears.activity_end()
                self.spawn(self.watchdog(act))
                await self.emit("log", msg=f"turno cerrado: {why}, texto estable {stable:.2f} s, terminada {fin}")
                return

    def caller_talking(self) -> bool:
        """¿Está hablando quien llama? Con ruido de fondo el detector no sirve: cuenta el texto nuevo."""
        if self.noisy:
            return self.turn_open and bool(self.interim) and time.time() - self.act_start_t > 0.3
        return self.vad.speaking and time.time() - self.act_start_t > 0.3

    async def on_interim(self, tag: str, text: str):
        text = _unglue(text)
        if not text:
            return
        if not self.turn_open and self.ears and self.ears.act in self.ears.texts:
            return      # parcial atrasado de una intervención que ya tiene su definitivo
        if self.is_echo(text):
            return
        if self.first_turn and tag == "o1" and self.call and not self.call.s.lang_locked:
            self.spawn(self.probe_check(text))
        # deshacer solo con palabras de verdad (no con ruido) de una intervención empezada tras la respuesta
        if self.undo and self.act_start_t > self.undo[2] and not self.noisy:
            await self.maybe_undo()
        if text != self.interim:
            self.interim_t = time.perf_counter()
        if self.turn_open:
            self.turn_text = True
        self.interim = text
        full = " ".join(self.segments + [text]).strip()
        await self.emit("partial", text=full)
        self.spawn(self.speculate(full))

    async def on_final(self, tag: str, text: str, lag_ms, act: int, duplicate: bool = False):
        text = _unglue(text)
        if not text:
            return
        if duplicate:
            await self.emit("log", msg=f"sesión {tag}: definitivo redundante ({lag_ms} ms)")
            if self.first_turn and tag == "o1":
                self.spawn(self.probe_check(text))
            return
        # Un definitivo tardío de lo ya respondido se ignora; pero si es de una intervención POSTERIOR, es que
        # quien llama lo ha repetido (p. ej. porque se lo pedimos) y hay que contestarle.
        same = act < 0 or act <= self.answered_act
        if self.answered_text and same and self.contained(text, self.answered_text):
            await self.emit("log", msg=f"definitivo que ya estaba respondido: «{text}»")
            return
        if self.answered_text and same and _k(self.answered_text) and _k(self.answered_text) in _k(text) and _k(text) != _k(self.answered_text):
            self.continuation = True
            self.segments = []
            await self.emit("log", msg=f"el definitivo completa lo respondido: «{text}»")
        if self.is_echo(text):
            await self.emit("log", msg=f"eco descartado: «{text}»")
            return
        if self.segments and self.contained(text, self.segments[-1]):
            return
        if self.segments and _k(self.segments[-1]) and _k(self.segments[-1]) in _k(text):
            self.segments[-1] = text.strip()      # el definitivo ya incluye lo anterior: se sustituye
        else:
            self.segments.append(text.strip())
        self.interim = ""
        self.final_act = max(self.final_act, act)
        full = " ".join(self.segments).strip()
        await self.emit("final", text=full, stt_ms=lag_ms, session=tag)
        self.spawn(self.endpoint(full, typed=False))

    async def watchdog(self, act: int):
        await asyncio.sleep(self.WATCHDOG_S)
        if not self.ears or act in self.ears.texts or self.turn_open or not self.interim or act != self.ears.act:
            return
        await self.emit("log", msg=f"ninguna sesión devolvió el definitivo en {self.WATCHDOG_S:.0f} s: se usa el último parcial")
        await self.on_final("vigilante", self.interim, None, act)

    async def on_typed(self, text: str):
        self.turn_t0 = time.time()
        self.t_speech_end = time.perf_counter()
        self.segments = [text.strip()]
        await self.emit("final", text=text, stt_ms=None, typed=True)
        await self.endpoint(text, typed=True)

    # ------------------------------------------------------------ Sistema 1 especulativo

    async def speculate(self, full: str):
        """Jev sobre el parcial; si la frase parece terminada, prepara ya la voz de la respuesta probable."""
        if self.spec_busy:
            self.spec_next = full
            return
        self.spec_busy = True
        try:
            while full:
                if _k(full) not in self.spec and self.call:
                    try:
                        p = await self.perceive(full, spec=True)
                    except JevError:
                        full, self.spec_next = self.spec_next, None
                        continue
                    self.spec[_k(full)] = p
                    await self.emit("perception", phase="parcial", text=full, ms=p.ms, hedged=p.hedged, j=_compact(p))
                    await self.maybe_barge(full, p)
                    if p.finished >= 0.85 and self.tts and not self.agent_speaking:
                        try:
                            outs = self.merge_says(await self.call.handle(full, p, dry=True))
                            for o in outs:
                                if o["kind"] == "say":
                                    MOUTH.render(o["text"])
                        except Exception as e:  # noqa: BLE001
                            log.info("preparación especulativa falló: %s", e)
                full, self.spec_next = self.spec_next, None
        finally:
            self.spec_busy = False

    async def probe_check(self, text: str):
        """Mientras habla, Jev mira si la sesión con pista gallega oye gallego (para no esperar después)."""
        if self.probe_busy or not text or len(text.split()) < 3:
            return
        self.probe_busy = True
        try:
            r = await JEV.ask({"text": text}, {"lang": choice("Which language is `text` written in?", LANG)})
            a = r["answers"]["lang"]
            self.probe_lang = (a["choice"], a["confidence"], text)
        finally:
            self.probe_busy = False

    async def maybe_barge(self, full: str, p):
        """Interrupción: un «ajá» no corta al agente; una corrección, una negativa o una urgencia, sí.
        Solo cuenta si la persona está hablando AHORA y empezó después de que el agente arrancara: los parciales
        atrasados de la frase anterior llegan cuando el agente ya contesta y no son una interrupción."""
        if not self.agent_speaking or not self.speak_task or self.speak_task.done():
            return
        if not (self.turn_open if self.noisy else self.vad.speaking) or getattr(self, "act_start_t", 0) < getattr(self, "agent_start_t", 0) + 0.15:
            return
        act, c = p.act
        if p.n("emergency") >= 0.5 or (act not in ("backchannel", "unclear", None) and c >= 0.6 and len(full.split()) >= 2):
            self.speak_task.cancel()
            self.speaking_until = 0
            await self.emit("stop_audio", reason=f"interrupción ({act} {c:.2f})")
            if self.call:
                self.call._log("barge_in", act=act, conf=c, text=full)

    async def maybe_undo(self):
        """Vuelve a hablar justo después de que respondiéramos: era una pausa. Se corta la voz, se restaura
        el estado y lo nuevo se une al mismo turno."""
        if self.lock.locked():
            self.undo_requested = True
            return
        if not self.undo or not self.call:
            return
        state, text, t, prev_resp_t = self.undo
        self.undo = None
        if self.act_start_t - t > self.UNDO_WINDOW_S or self.call.s.ended:
            return
        self.last_response_t = prev_resp_t
        if self.speak_task and not self.speak_task.done():
            self.speak_task.cancel()
        self.speaking_until = 0
        await self.emit("stop_audio", reason="seguía hablando: se deshace la respuesta")
        self.call.s = state
        self.segments = [text]
        self.answered_text = ""
        self.call._log("undo", text=text)
        await self.emit("state", state=self.call.snapshot())

    # ------------------------------------------------------------ fin de turno y respuesta

    async def endpoint(self, full: str, typed: bool):
        if not self.call or self.finalized:
            return
        p = self.spec.get(_k(full))
        if p is None:
            try:
                p = await self.perceive(full)
            except JevError as e:
                # Sin Sistema 1 no se decide nada: se pide que lo repita y el estado no se toca.
                await self.emit("log", msg=f"Jev no responde ({e}): se pide que lo repita, sin tocar el estado")
                self.segments = []
                self.call._log("jev_unavailable", text=full)
                self.speak_task = self.spawn(self.speak_outs([{"kind": "say", "text": nlg.say("ask_repeat", self.call.s.lang), "act": "ask_repeat"}]))
                return
            self.spec[_k(full)] = p
            await self.emit("perception", phase="definitivo", text=full, ms=p.ms, hedged=p.hedged, j=_compact(p))
        else:
            await self.emit("log", msg="se reutiliza el juicio del parcial (0 ms)")
        act, c = p.act
        greeting = getattr(__import__(type(self.call).__module__), "is_greeting", lambda t: False)(full) if self.call else False
        if not typed and act == "backchannel" and c >= 0.6 and not greeting:
            self.segments = []
            await self.emit("log", msg="muletilla: no se responde")
            return
        if not typed and p.finished < 0.35:
            await self.emit("log", msg=f"frase a medias (terminada {p.finished:.2f}): se espera")
            loop = asyncio.get_running_loop()
            self.respond_timer = loop.call_later(self.RESPOND_WAIT_S, lambda: self.spawn(self.respond(full, p)))
            return
        await self.respond(full, p, typed)

    async def decide_language(self, p, full):
        """Primer turno sin idioma fijado. Gallego solo si la sesión con pista gallega lo oye como gallego Y su
        texto difiere del de la sesión sin pista (con español, las dos coinciden)."""
        self.first_turn = False
        lg, lc = p.c("lang")
        texts = self.ears.texts.get(self.first_act, {}) if self.ears else {}
        for _ in range(6):
            if len(texts) >= 2 and not self.probe_busy:
                break
            await asyncio.sleep(0.05)
            texts = self.ears.texts.get(self.first_act, {}) if self.ears else {}
        auto_t, gl_t = texts.get("o0"), texts.get("o1")
        if lg == "es" and gl_t:
            if not self.probe_lang or _k(self.probe_lang[2]) != _k(gl_t):
                await self.probe_check(gl_t)
            pl, pc, _ = self.probe_lang or (None, 0, "")
            differ = auto_t is None or _sim(auto_t, gl_t) < 0.85
            if pl == "gl" and pc >= 0.6 and differ:
                await self.emit("log", msg=f"gallego: la sesión con pista gallega lo oye como gallego ({pc:.2f}) y difiere de la otra")
                full = gl_t
                self.call.s.lang, self.call.s.lang_locked = "gl", True
                p = await self.perceive(full)
                await self.emit("perception", phase="definitivo", text=full, ms=p.ms, hedged=p.hedged, j=_compact(p))
                lg = "gl"
        if lg in nlg.STT_CODES and self.ears:
            # a partir del siguiente turno, las dos sesiones con la pista exacta
            self.ears.set_langs([[nlg.STT_CODES[lg]]] * 2)
        return p, full

    async def respond(self, full: str, p, typed: bool = False):
        async with self.lock:
            if not self.call or self.call.s.ended:
                return
            if not typed and self.caller_talking():
                await self.emit("log", msg="sigue hablando: se espera al final del turno")
                self.spawn(self.answer_later(full, p, self.final_act))
                return
            # Un turno que empezó antes de la última respuesta (o que la completa) no puede contestarla.
            if not typed and ((self.turn_t0 and self.turn_t0 < self.last_response_t) or self.continuation):
                self.continuation = False
                if self.undo:
                    state, prev_text, _, prev_resp_t = self.undo
                    self.undo = None
                    self.last_response_t = prev_resp_t
                    if self.speak_task and not self.speak_task.done():
                        self.speak_task.cancel()
                    self.speaking_until = 0
                    await self.emit("stop_audio", reason="continuación de la frase anterior: se deshace y se une")
                    self.call.s = state
                    full = full if _k(prev_text) in _k(full) else f"{prev_text} {full}"
                    p = await self.perceive(full)
                    await self.emit("perception", phase="unido", text=full, ms=p.ms, hedged=p.hedged, j=_compact(p))
                    self.call._log("merged_turn", text=full)
                else:
                    # sin nada que deshacer: se procesa, pero no puede confirmar nada (no oyó la pregunta)
                    await self.emit("log", msg=f"turno empezado antes de la última respuesta: se procesa sin poder confirmar «{full}»")
                    self.call.no_confirm = True
            if self.first_turn and not self.call.s.lang_locked:
                p, full = await self.decide_language(p, full)
            self.segments = []
            self.respond_timer = None
            # audio de lo que se contesta (las intervenciones desde la última respuesta), para una segunda opinión
            self.call.audio = b"".join(b"".join(self.act_audio.get(a, [])) for a in range(self.answered_act + 1, self.final_act + 1))
            self.answered_text = full
            self.answered_act = self.final_act
            self.undo_requested = False
            t0 = time.perf_counter()
            before = copy.deepcopy(self.call.s)
            try:
                outs = await self.call.handle(full, p)
            except Exception as e:  # noqa: BLE001
                # un fallo nuestro nunca deja la línea muda: se restaura el estado y se pide que lo repita
                log.exception("la política falló: %s", e)
                await self.emit("log", msg=f"ERROR en la política ({e!r})"[:300])
                self.call.s = before
                outs = [{"kind": "say", "text": nlg.say("ask_repeat", getattr(self.call.s, "lang", None) or "en"), "act": "ask_repeat"}]
            # Antes de pedir que repita, se vuelve a transcribir el audio del turno (Flash-Lite): si sale otra cosa,
            # el turno se procesa con eso. Solo en los caminos de «no le he entendido»; ahorra una vuelta entera.
            if not typed and self.reasks(outs, before) and hasattr(self.call, "second_opinion") and not getattr(self.call, "so_used", True):
                alt, ms = await self.call.second_opinion()
                if alt and _k(alt) != _k(full):
                    await self.emit("log", msg=f"segunda opinión ({ms} ms): «{alt}» en vez de «{full}»")
                    self.call.s = before
                    p2 = await self.perceive(alt)
                    await self.emit("perception", phase="segunda opinión", text=alt, ms=p2.ms, hedged=p2.hedged, j=_compact(p2))
                    try:
                        outs = await self.call.handle(alt, p2)
                        full = alt
                    except Exception as e:  # noqa: BLE001
                        log.exception("la política falló con la segunda opinión: %s", e)
                        self.call.s = before
                        outs = [{"kind": "say", "text": nlg.say("ask_repeat", getattr(self.call.s, "lang", None) or "en"), "act": "ask_repeat"}]
            if self.undo_requested and not typed:
                self.call.s = before
                self.segments, self.answered_text, self.undo_requested = [full], "", False
                await self.emit("log", msg="volvió a hablar mientras respondíamos: se une al turno")
                return
            prev_resp_t, self.last_response_t = self.last_response_t, time.time()
            self.turn_t0 = None
            wrote = len(self.call.s.actions) > len(before.actions)
            self.undo = None if wrote or self.call.s.ended else (before, full, time.time(), prev_resp_t)
            for o in outs:
                if o["kind"] == "event":
                    await self.emit("trace", event=o["event"])
            await self.emit("state", state=self.call.snapshot())
            await self.emit("latency", stage="política", ms=round((time.perf_counter() - t0) * 1000))
            if self.speak_task and not self.speak_task.done():
                self.speak_task.cancel()
                await self.emit("stop_audio", reason="nueva respuesta")
            self.speak_task = self.spawn(self.speak_outs(outs))

    async def answer_later(self, full: str, p, act: int):
        """Guardia de «sigue hablando»: si el resto del turno no trae texto nuevo (una «H» suelta que el
        reconocedor no devuelve), se contesta lo que ya había. Nunca debe quedarse la línea muda."""
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 15:
            await asyncio.sleep(0.1)
            if self.finalized or self.final_act > act or self.answered_act >= act:
                return          # llegó un definitivo nuevo (lo contesta el camino normal) o ya se contestó
            if not self.turn_open and time.perf_counter() - self.closed_t > 1.2:
                full2 = " ".join(self.segments).strip() or full
                await self.emit("log", msg=f"el resto del turno no trajo texto: se contesta «{full2}»")
                p2 = p if _k(full2) == _k(full) else await self.perceive(full2)
                await self.respond(full2, p2)
                return

    REASK = {"ask_second_id", "repeat_id", "repeat", "id_letter_bad", "reg_phone_groups", "ask_repeat"}

    def reasks(self, outs: list[dict], before) -> bool:
        """¿La respuesta es volver a pedir lo mismo (no se entendió o no validó)?"""
        acts = {o.get("act") for o in outs if o.get("kind") == "say"}
        same_q = bool(getattr(before, "pending", None)) and getattr(before, "pending", "") == getattr(self.call.s, "pending", "") \
            and str(before.pending).startswith("reg_") and f"{before.pending}" in acts
        return bool(acts & self.REASK) or same_q

    # ------------------------------------------------------------ voz del agente

    @staticmethod
    def merge_says(outs: list[dict]) -> list[dict]:
        merged: list[dict] = []
        for o in outs:
            if o["kind"] == "event":
                continue
            if o["kind"] == "say" and merged and merged[-1]["kind"] == "say":
                merged[-1] = {**merged[-1], "text": merged[-1]["text"] + " " + o["text"], "act": merged[-1]["act"] + "+" + o["act"]}
            else:
                merged.append(o)
        return merged

    async def speak_outs(self, outs: list[dict]):
        outs = self.merge_says(outs)
        try:
            for o in outs:
                if o["kind"] == "say":
                    await self.speak(o["text"], o.get("act", ""))
                elif o["kind"] == "s2":
                    r = await system2.answer(o["question"], o["lang"])
                    await self.emit("system2", **{k: v for k, v in r.items() if k != "guard"}, guard=r.get("guard"))
                    text = r["reply"] if r["ok"] else nlg.say("s2_fallback", o["lang"])
                    await self.speak(text, "system2" if r["ok"] else "s2_fallback", source="sistema 2")
                    await self.speak_outs(o["then"])
                elif o["kind"] == "end":
                    wait = max(0.0, self.speaking_until - time.time())
                    await asyncio.sleep(wait + 0.3)
                    await self.finalize("goodbye")
        except asyncio.CancelledError:
            pass

    async def speak(self, text: str, act: str, source: str = "plantilla"):
        if not self.call:
            return
        self.call.spoken(text)
        self.agent_text = text
        self.agent_start_t = time.time()
        if not self.tts:
            await self.emit("agent", text=text, act=act, source=source, audio=False)
            return
        r = MOUTH.render(text)
        await self.emit("agent", text=text, act=act, source=source, cached=r.cached or r.done, audio=True)
        first, sent = True, 0
        async for chunk in r.stream():
            if first:
                first = False
                if self.t_speech_end:
                    await self.emit("latency", stage="fin de voz → primera palabra",
                                    ms=round((time.perf_counter() - self.t_speech_end) * 1000))
                    self.t_speech_end = None
            now = time.time()
            self.speaking_until = max(self.speaking_until, now) + len(chunk) / 48000
            sent += len(chunk)
            await self.ws.send_bytes(chunk)
        if not sent:
            await self.emit("tts_fallback", text=text)

    async def finalize(self, why: str):
        if self.finalized or not self.call:
            return
        self.finalized = True
        await clinic.release_holds(self.call.s.call_id)
        rep = await self.call.report()
        rep["ended_by"] = why
        (CALLS / f"{rep['call_id']}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        await self.emit("report", report={k: v for k, v in rep.items() if k != "trace"})
        await self.emit("state", state=self.call.snapshot())
