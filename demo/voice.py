"""La voz: oído, detector de voz en código y boca con caché en disco. La boca solo lee: el texto lo decide la política.

Oído (EARS): gemini (gemini-3.5-transcribe-live, dos sesiones redundantes; por defecto) o scribe (Scribe v2 Realtime
de ElevenLabs, una sesión con commit manual).
Boca (MOUTH): elevenlabs (TTS en streaming, µ-law de 8 kHz directo para Twilio; por defecto) o gemini
(gemini-3.1-flash-live-preview). Si ElevenLabs falla o tarda más de ELEVEN_TTFB_S en dar el primer byte, habla Gemini."""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import os
import base64
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
import numpy as np
import websockets
from google import genai
from google.genai import types

import clinic
from system2 import _key

log = logging.getLogger("voz")
CLIENT = genai.Client(api_key=_key())
STT_MODEL = os.environ.get("STT_MODEL", "gemini-3.5-transcribe-live")
TTS_MODEL = os.environ.get("TTS_MODEL", "gemini-3.1-flash-live-preview")
VOICE = os.environ.get("VOICE", "Kore")
FALLBACK_TTS_MODEL = os.environ.get("FALLBACK_TTS_MODEL", "gemini-3.1-flash-tts-preview")
CACHE = Path(__file__).parent / "cache" / "tts"
CACHE.mkdir(parents=True, exist_ok=True)

VOCAB = sorted({d.surname for d in clinic.DOCTORS.values()} | {s["name"] for s in clinic.SITES.values()}
               | {p.surname1 for p in clinic.PATIENTS.values()} | {p.surname2 for p in clinic.PATIENTS.values()}
               | {"Prosper", "cardiología", "pediatría", "traumatología", "ginecología", "médico de cabecera"})

# ================================================================ oído

class Stt:
    """Una sesión de transcripción con inicio y fin de intervención marcados por nosotros."""

    def __init__(self, on_interim, on_final, langs: list[str] | None = None, tag: str = "main"):
        self.on_interim, self.on_final = on_interim, on_final
        self.langs = langs or []
        self.tag = tag
        self.acts: list[int] = []      # intervenciones cerradas que esperan su definitivo, en orden
        self.session = None
        self._cm = None
        self._rx: asyncio.Task | None = None
        self.ready = asyncio.Event()
        self.in_activity = False
        self.t_end: float | None = None
        self.cur_act = -1              # intervención a la que pertenecen los parciales que llegan

    def _config(self):
        kw = {"custom_vocabulary": VOCAB} if VOCAB else {}
        if self.langs:
            kw["language_codes"] = self.langs
        return types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(**kw),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)))

    async def start(self):
        last = None
        for attempt in range(3):
            try:
                self._cm = CLIENT.aio.live.connect(model=STT_MODEL, config=self._config())
                self.session = await asyncio.wait_for(self._cm.__aenter__(), timeout=6)
                self._rx = asyncio.create_task(self._receive())
                self.ready.set()
                return
            except Exception as e:  # noqa: BLE001
                last = e
                log.warning("STT %s: no conecta (intento %d): %s", self.tag, attempt + 1, e)
                await asyncio.sleep(0.3 * (attempt + 1))
        raise RuntimeError(f"STT {self.tag} no conecta: {last}")

    async def _receive(self):
        try:
            while True:
                async for r in self.session.receive():
                    sc = r.server_content
                    if not sc:
                        continue
                    if sc.interim_input_transcription and sc.interim_input_transcription.text:
                        await self.on_interim(self.tag, sc.interim_input_transcription.text, self.cur_act)
                    if sc.input_transcription and sc.input_transcription.text:
                        lag = round((time.perf_counter() - self.t_end) * 1000) if self.t_end else None
                        act = self.acts.pop(0) if self.acts else -1
                        await self.on_final(self.tag, sc.input_transcription.text, lag, act)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("STT %s cerrado: %s", self.tag, e)

    async def _send(self, **kw):
        """Envía; si la conexión se ha caído, reconecta (y reabre la intervención en curso) y reintenta una vez."""
        if os.environ.get("STT_DEBUG"):
            k = next(iter(kw))
            log.warning("STT %s → %s %s", self.tag, k, len(kw[k].data) if k == "audio" else "")
        try:
            await self.session.send_realtime_input(**kw)
        except Exception as e:  # noqa: BLE001
            log.warning("STT %s: conexión caída (%s); se reconecta", self.tag, e)
            await self.restart()
            if self.in_activity and "activity_start" not in kw:
                await self.session.send_realtime_input(activity_start=types.ActivityStart())
            await self.session.send_realtime_input(**kw)

    async def restart(self):
        old_cm, old_rx = self._cm, self._rx
        self.ready.clear()
        await self.start()
        if old_rx:
            old_rx.cancel()
        if old_cm:
            asyncio.create_task(_aexit(old_cm))

    async def activity_start(self, preroll: bytes = b"", act: int = -1):
        await self.ready.wait()
        self.cur_act = act
        self.in_activity = True
        self.pushed = 0
        await self._send(activity_start=types.ActivityStart())
        if preroll:
            await self.push(preroll)

    async def push(self, pcm: bytes):
        if self.session and self.in_activity and pcm:
            self.pushed = getattr(self, "pushed", 0) + len(pcm)
            await self._send(audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000"))

    async def activity_end(self, act: int = -1):
        if self.session and self.in_activity:
            if not getattr(self, "pushed", 0):
                # cerrar una intervención vacía tumba la sesión (1007): se manda antes un poco de silencio
                await self.push(bytes(3200))
            self.in_activity = False
            self.t_end = time.perf_counter()
            self.acts.append(act)
            await self._send(activity_end=types.ActivityEnd())

    async def close(self):
        if self._rx:
            self._rx.cancel()
        if self._cm:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass


class Ears:
    """Transcripción redundante: varias sesiones oyen lo mismo y gana el primer definitivo de cada
    intervención (si una pierde un definitivo, la otra lo tiene). El idioma se cambia entre turnos."""

    def __init__(self, on_interim, on_final, langs_list: list[list[str]]):
        self.on_interim, self.on_final = on_interim, on_final
        self.sessions = [Stt(self._interim, self._final, langs=l, tag=f"o{i}") for i, l in enumerate(langs_list)]
        self.next: list[Stt] | None = None
        self.act = 0
        self.in_activity = False
        self.texts: dict[int, dict[str, str]] = {}   # intervención → {sesión: definitivo}
        self.interims: dict[str, str] = {}
        self.primary: dict[int, str] = {}              # intervención → sesión cuyos parciales cuentan
        self.gen = 0

    async def start(self):
        res = await asyncio.gather(*[s.start() for s in self.sessions], return_exceptions=True)
        ok = [s for s, r in zip(self.sessions, res) if not isinstance(r, Exception)]
        if not ok:
            raise RuntimeError("ninguna sesión de transcripción conecta")
        self.sessions = ok

    def set_langs(self, langs_list: list[list[str]]):
        """Prepara sesiones nuevas (p. ej. con la pista del idioma fijado); se cambian al empezar la siguiente intervención."""
        nxt = [Stt(self._interim, self._final, langs=l, tag=f"o{i}g{self.gen + 1}") for i, l in enumerate(langs_list)]
        self.gen += 1
        self.next = nxt

        async def go():
            await asyncio.gather(*[s.start() for s in nxt], return_exceptions=True)
        asyncio.create_task(go())

    async def activity_start(self, preroll: bytes) -> int:
        if self.next and all(s.ready.is_set() for s in self.next):
            old, self.sessions, self.next = self.sessions, [s for s in self.next if s.session], None

            async def later():
                await asyncio.sleep(4)   # deja llegar los definitivos pendientes
                for s in old:
                    await s.close()
            asyncio.create_task(later())
        self.act += 1
        self.in_activity = True
        await asyncio.gather(*[s.activity_start(preroll, self.act) for s in self.sessions], return_exceptions=True)
        return self.act

    async def push(self, pcm: bytes):
        await asyncio.gather(*[s.push(pcm) for s in self.sessions], return_exceptions=True)

    async def activity_end(self):
        self.in_activity = False
        await asyncio.gather(*[s.activity_end(self.act) for s in self.sessions], return_exceptions=True)

    async def _interim(self, tag, text, act=-1):
        """Solo pasan los parciales de la intervención en curso, sin definitivo aún, y de UNA sesión (la primera
        que habla en cada intervención): las dos sesiones dan parciales distintos y, alternándose, el texto no
        parecía estable nunca."""
        self.interims[tag] = text
        if act != self.act or act in self.texts:
            return
        if self.primary.setdefault(act, tag) != tag:
            return
        await self.on_interim(tag, text)

    async def _final(self, tag, text, lag, act):
        seen = act in self.texts
        self.texts.setdefault(act, {})[tag] = text
        await self.on_final(tag, text, lag, act, duplicate=seen)

    async def close(self):
        for s in self.sessions + (self.next or []):
            await s.close()


SCRIBE_MODEL = os.environ.get("SCRIBE_MODEL", "scribe_v2_realtime")
# Lengua principal y secundarias: sin ellas, una frase corta en castellano puede salir en portugués.
SCRIBE_LANGS = [x for x in os.environ.get("SCRIBE_LANGS", "en,es,ca").split(",") if x]


class ScribeEars:
    """Oído con Scribe v2 Realtime de ElevenLabs. Misma interfaz que Ears: activity_start(preroll) → intervención,
    push(pcm16k), activity_end(), close(), set_langs(), y los mismos avisos on_interim(tag, text) y
    on_final(tag, text, lag_ms, act, duplicate=). Una sola sesión WebSocket por llamada; cada intervención se cierra
    con un commit manual y su definitivo llega en orden. La lengua la detecta Scribe en cada commit (dentro de
    SCRIBE_LANGS), así que si quien llama cambia de lengua a mitad no hace falta reconectar.

    Trampas medidas: (1) Scribe cierra la sesión si pasan ~15 s sin audio, así que entre intervenciones se manda un
    poco de silencio cada 4 s; (2) un commit con menos de 0,3 s de audio se ignora (commit_throttled), así que se
    rellena con silencio."""

    TAG = "s0"
    MIN_COMMIT_BYTES = 16000 * 2 * 35 // 100      # 0,35 s a 16 kHz

    def __init__(self, on_interim, on_final, langs_list: list[list[str]] | None = None):
        self.on_interim, self.on_final = on_interim, on_final
        self.act = 0
        self.in_activity = False
        self.texts: dict[int, dict[str, str]] = {}
        self.pending: list[tuple[int, float]] = []     # (intervención, instante del commit), en orden
        self.ws = None
        self.ready = asyncio.Event()
        self.pushed = 0
        self.last_send = 0.0
        self.closed = False
        self.langs = list(SCRIBE_LANGS)
        self._rx: asyncio.Task | None = None
        self._ka: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._reconnecting: asyncio.Task | None = None

    def _url(self) -> str:
        q = [("model_id", SCRIBE_MODEL), ("audio_format", "pcm_16000"), ("commit_strategy", "manual"),
             ("filter_background_audio", "true")]
        if self.langs:
            q.append(("language_code", self.langs[0]))
            q += [("secondary_languages", l) for l in self.langs[1:]]
        q += [("keyterms", w) for w in VOCAB[:50] if len(w) <= 20]
        return "wss://api.elevenlabs.io/v1/speech-to-text/realtime?" + urlencode(q)

    async def start(self):
        last = None
        for attempt in range(3):
            try:
                self.ws = await asyncio.wait_for(websockets.connect(
                    self._url(), additional_headers={"xi-api-key": _eleven_key()}, max_size=None, ping_interval=10), timeout=6)
                self._rx = asyncio.create_task(self._receive(self.ws))
                self.last_send = time.time()
                if self._ka is None:
                    self._ka = asyncio.create_task(self._keepalive())
                self.ready.set()
                return
            except Exception as e:  # noqa: BLE001
                last = e
                log.warning("Scribe: no conecta (intento %d): %s", attempt + 1, e)
                await asyncio.sleep(0.3 * (attempt + 1))
        raise RuntimeError(f"Scribe no conecta: {last}")

    async def _receive(self, ws):
        try:
            async for raw in ws:
                m = json.loads(raw)
                typ = m.get("message_type")
                if typ == "partial_transcript":
                    text = (m.get("text") or "").strip()
                    if text and self.in_activity and self.act not in self.texts:
                        await self.on_interim(self.TAG, text)
                elif typ == "committed_transcript":
                    act, t_commit = self.pending.pop(0) if self.pending else (-1, None)
                    text = (m.get("text") or "").strip()
                    if not text:
                        continue
                    lag = round((time.perf_counter() - t_commit) * 1000) if t_commit else None
                    seen = act in self.texts
                    self.texts.setdefault(act, {})[self.TAG] = text
                    await self.on_final(self.TAG, text, lag, act, duplicate=seen)
                elif typ == "commit_throttled":
                    if self.pending:
                        self.pending.pop(0)
                    log.warning("Scribe: commit ignorado: %s", m.get("error"))
                elif typ not in ("session_started", "committed_transcript_with_timestamps", "committed_transcript_entities"):
                    log.warning("Scribe: %s: %s", typ, m.get("error") or m)
        except asyncio.CancelledError:
            return
        except Exception as e:  # noqa: BLE001
            log.warning("Scribe: sesión cerrada: %s", e)
        if not self.closed and ws is self.ws:
            # los definitivos pendientes de esta sesión ya no llegarán: el vigilante de la llamada usará el parcial
            self.pending.clear()
            self._reconnect_soon()

    def _reconnect_soon(self):
        if self._reconnecting is None or self._reconnecting.done():
            self.ready.clear()
            self._reconnecting = asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        old = self.ws
        try:
            await self.start()
        except Exception as e:  # noqa: BLE001
            log.warning("Scribe: no se pudo reconectar: %s", e)
        if old is not None and old is not self.ws:
            asyncio.create_task(old.close())

    async def _send(self, pcm: bytes, commit: bool = False):
        msg = json.dumps({"message_type": "input_audio_chunk", "audio_base_64": base64.b64encode(pcm).decode(),
                          "commit": commit, "sample_rate": 16000})
        async with self._lock:
            if not self.ready.is_set():
                await asyncio.wait_for(self.ready.wait(), timeout=6)
            try:
                await self.ws.send(msg)
            except Exception as e:  # noqa: BLE001
                log.warning("Scribe: envío fallido (%s); se reconecta", e)
                self._reconnect_soon()
                await asyncio.wait_for(self._reconnecting, timeout=8)
                await self.ws.send(msg)
            self.last_send = time.time()

    async def _keepalive(self):
        """Scribe cierra la sesión tras ~15 s sin audio: entre intervenciones se le manda un poco de silencio."""
        while not self.closed:
            await asyncio.sleep(4)
            if not self.in_activity and time.time() - self.last_send > 3.5 and self.ready.is_set():
                try:
                    await self._send(bytes(640))
                except Exception as e:  # noqa: BLE001
                    log.warning("Scribe: silencio de mantenimiento: %s", e)

    def set_langs(self, langs_list: list[list[str]]):
        """Scribe detecta la lengua en cada commit dentro de SCRIBE_LANGS: no hace falta reconectar."""

    async def start_act(self):
        self.act += 1
        self.in_activity = True
        self.pushed = 0
        return self.act

    async def activity_start(self, preroll: bytes) -> int:
        act = await self.start_act()
        if preroll:
            await self.push(preroll)
        return act

    async def push(self, pcm: bytes):
        if self.in_activity and pcm:
            self.pushed += len(pcm)
            await self._send(pcm)

    async def activity_end(self):
        if not self.in_activity:
            return
        pad = max(640, self.MIN_COMMIT_BYTES - self.pushed)
        self.in_activity = False
        self.pending.append((self.act, time.perf_counter()))
        try:
            await self._send(bytes(pad), commit=True)
        except Exception as e:  # noqa: BLE001
            log.warning("Scribe: commit fallido: %s", e)

    async def close(self):
        self.closed = True
        for t in (self._rx, self._ka, self._reconnecting):
            if t:
                t.cancel()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass


EARS_KIND = os.environ.get("EARS", "gemini")


def make_ears(on_interim, on_final, langs_list: list[list[str]]):
    """El oído que toque según EARS=gemini|scribe (misma interfaz)."""
    if EARS_KIND == "scribe" and _eleven_key():
        return ScribeEars(on_interim, on_final, langs_list)
    return Ears(on_interim, on_final, langs_list)


async def _aexit(cm):
    try:
        await cm.__aexit__(None, None, None)
    except Exception:  # noqa: BLE001
        pass


class Vad:
    """Detector de voz por energía, con suelo de ruido adaptativo. Trabaja en tramas de 20 ms a 16 kHz."""
    FRAME = 320

    def __init__(self, end_ms: int = 280):
        self.noise = 150.0
        self.speaking = False
        self.voiced_run = 0
        self.silence_ms = 0
        self.end_ms = end_ms
        self.buf = b""
        self.preroll: list[bytes] = []

    def feed(self, pcm: bytes):
        """Devuelve una lista de eventos: ('start', preroll) | ('audio', bytes) | ('end', None) | ('silence', ms)."""
        self.buf += pcm
        ev = []
        while len(self.buf) >= self.FRAME * 2:
            fr, self.buf = self.buf[:self.FRAME * 2], self.buf[self.FRAME * 2:]
            x = np.frombuffer(fr, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(x * x)) + 1e-6)
            voiced = rms > max(self.noise * 3.2, 420.0)
            if not self.speaking:
                self.preroll = (self.preroll + [fr])[-15:]  # 300 ms
                if voiced:
                    self.voiced_run += 1
                    if self.voiced_run >= 4:
                        self.speaking, self.silence_ms = True, 0
                        ev.append(("start", b"".join(self.preroll)))
                        self.preroll = []
                else:
                    self.voiced_run = 0
                    self.noise = 0.95 * self.noise + 0.05 * rms
            else:
                ev.append(("audio", fr))
                if voiced:
                    self.silence_ms = 0
                else:
                    self.silence_ms += 20
                    if self.silence_ms >= self.end_ms:
                        self.speaking, self.voiced_run = False, 0
                        ev.append(("end", None))
        return ev

class SileroVad:
    """Detector de voz neuronal (Silero VAD, ONNX): distingue la voz del ruido de fondo, cosa que la energía
    no hace a 5 dB de relación señal/ruido. Misma interfaz que Vad: feed() → ('start', preroll) | ('end', None)."""
    MODEL = Path(os.environ.get("SILERO_VAD", str(Path.home() / ".cache/silero/silero_vad.onnx")))
    _sess = None

    # 180 ms: el detector neuronal distingue voz de ruido de sobra, y cada milisegundo aquí es un milisegundo de
    # silencio que oye quien llama. Una pausa a mitad de frase no cierra el turno igualmente: eso lo deciden el
    # texto estable y el «¿ha terminado?» de Jev, no el detector.
    def __init__(self, end_ms: int = 180, on_p: float = 0.5, off_p: float = 0.35):
        import onnxruntime as ort
        if SileroVad._sess is None:
            so = ort.SessionOptions()
            so.intra_op_num_threads, so.inter_op_num_threads = 1, 1
            SileroVad._sess = ort.InferenceSession(str(self.MODEL), sess_options=so, providers=["CPUExecutionProvider"])
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.ctx = np.zeros(64, dtype=np.float32)
        self.buf = np.zeros(0, dtype=np.float32)
        self.speaking = False
        self.on_run = 0
        self.silence_ms = 0
        self.end_ms, self.on_p, self.off_p = end_ms, on_p, off_p
        self.last_p = 0.0

    def _prob(self, chunk: np.ndarray) -> float:
        x = np.concatenate([self.ctx, chunk])[None, :].astype(np.float32)
        out, self.state = SileroVad._sess.run(None, {"input": x, "state": self.state, "sr": np.array(16000, dtype=np.int64)})
        self.ctx = chunk[-64:]
        return float(out[0][0])

    def feed(self, pcm: bytes):
        self.buf = np.concatenate([self.buf, np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0])
        ev = []
        while len(self.buf) >= 512:
            chunk, self.buf = self.buf[:512], self.buf[512:]
            p = self._prob(chunk)
            self.last_p = p
            if not self.speaking:
                self.on_run = self.on_run + 1 if p >= self.on_p else 0
                if self.on_run >= 2:
                    self.speaking, self.silence_ms = True, 0
                    ev.append(("start", b""))
            else:
                if p < self.off_p:
                    self.silence_ms += 32
                    if self.silence_ms >= self.end_ms:
                        self.speaking, self.on_run = False, 0
                        ev.append(("end", None))
                else:
                    self.silence_ms = 0
        return ev


def make_vad():
    # 140 ms de silencio para dar el turno por terminado (antes 180): el turno se cierra por SENTIDO, y deshacer y
    # unir recoge el error si quien llama seguía hablando. Ajustable con VAD_END_MS para poder comparar.
    end_ms = int(os.environ.get("VAD_END_MS", "140"))
    try:
        return SileroVad(end_ms=end_ms)
    except Exception as e:  # noqa: BLE001
        log.warning("Silero VAD no disponible (%s): detector por energía", e)
        return Vad()


# ================================================================ boca

SYS_TTS = ("You are the voice of a clinic receptionist. Read aloud EXACTLY the text you receive, word for word, in its own "
           "language, with a warm, calm, professional tone and a natural pace. Never add, omit, translate or change words. "
           "Never answer or comment on it.")


class Render:
    """Una frase que se está sintetizando (o ya sintetizada). `fmt` dice qué llevan los trozos: «pcm24» (PCM de 16 bits
    a 24 kHz) o «ulaw8» (µ-law a 8 kHz, lo que habla Twilio). Se fija antes del primer trozo y no cambia."""

    def __init__(self, text: str, fmt: str = "pcm24"):
        self.text = text
        self.fmt = fmt
        self.source = ""
        self.chunks: list[bytes] = []
        self.done = False
        self.ok = True
        self.heard = ""
        self.t0 = time.perf_counter()
        self.first_ms: int | None = None
        self.cached = False
        self._cond = asyncio.Condition()

    async def add(self, b: bytes):
        async with self._cond:
            if self.first_ms is None:
                self.first_ms = round((time.perf_counter() - self.t0) * 1000)
            self.chunks.append(b)
            self._cond.notify_all()

    async def finish(self, ok: bool = True):
        async with self._cond:
            self.done, self.ok = True, ok
            self._cond.notify_all()

    async def stream(self):
        i = 0
        while True:
            async with self._cond:
                while i >= len(self.chunks) and not self.done:
                    await self._cond.wait()
                if i >= len(self.chunks) and self.done:
                    return
                batch = self.chunks[i:]
            i += len(batch)
            for b in batch:
                yield b


class Mouth:
    def __init__(self, pool_size: int = 2, max_parallel: int = 4):
        self.renders: dict[str, Render] = {}
        self.pool: asyncio.Queue = asyncio.Queue()
        self.pool_size = pool_size
        self.sem = asyncio.Semaphore(max_parallel)
        self.enabled = True

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha1(f"{TTS_MODEL}|{VOICE}|{text}".encode()).hexdigest()[:20]

    async def _connect(self):
        cfg = types.LiveConnectConfig(
            response_modalities=["AUDIO"], system_instruction=SYS_TTS,
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE))),
            output_audio_transcription=types.AudioTranscriptionConfig())
        cm = CLIENT.aio.live.connect(model=TTS_MODEL, config=cfg)
        s = await cm.__aenter__()
        return cm, s, time.time()

    async def warm(self):
        """Deja sesiones abiertas para no pagar la conexión en la primera frase."""
        async def one():
            try:
                await self.pool.put(await self._connect())
            except Exception as e:  # noqa: BLE001
                log.warning("boca: no se pudo abrir sesión: %s", e)
        await asyncio.gather(*[one() for _ in range(self.pool_size)])

    async def _take(self):
        while not self.pool.empty():
            cm, s, t = self.pool.get_nowait()
            if time.time() - t < 480:
                return cm, s
            asyncio.create_task(self._close(cm))
        cm, s, _ = await self._connect()
        return cm, s

    async def _close(self, cm):
        try:
            await cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass

    def render(self, text: str, lang: str | None = None, fmt: str = "pcm24", slow: bool = False) -> Render:
        """Gemini siempre da PCM de 24 kHz: `lang`, `fmt` y `slow` se aceptan por compatibilidad (quien escucha mira r.fmt)."""
        k = self.key(text)
        r = self.renders.get(k)
        if r and (r.ok or not r.done):
            return r
        r = Render(text)
        r.source = "gemini"
        self.renders[k] = r
        f = CACHE / f"{k}.pcm"
        if f.exists():
            r.chunks, r.done, r.cached, r.first_ms = [f.read_bytes()], True, True, 0
            return r
        asyncio.create_task(self._render(k, r))
        return r

    async def _render(self, k: str, r: Render):
        """Sesión del pool → si falla ANTES de sonar, sesión nueva → si también, TTS no en directo. Una sesión
        del pool puede llevar minutos abierta y el servidor la aborta (1008): nunca debe quedar la línea muda."""
        async with self.sem:
            for attempt in ("pool", "nueva", "tts"):
                try:
                    if attempt == "tts":
                        await self._render_tts(r)
                    else:
                        await self._render_live(k, r, fresh=attempt == "nueva")
                    return
                except Exception as e:  # noqa: BLE001
                    log.warning("boca (%s): error: %s", attempt, e)
                    if r.chunks:          # ya ha empezado a sonar: no se puede empezar de nuevo
                        await r.finish(ok=True)
                        return
            await r.finish(ok=False)

    async def _render_live(self, k: str, r: Render, fresh: bool):
        cm = None
        try:
            if fresh:
                cm, s, _ = await asyncio.wait_for(self._connect(), timeout=5)
            else:
                cm, s = await asyncio.wait_for(self._take(), timeout=5)
            asyncio.create_task(self._refill())
            await s.send_client_content(turns=types.Content(role="user", parts=[types.Part(text=r.text)]), turn_complete=True)
            async for m in s.receive():
                sc = m.server_content
                if m.data:
                    await r.add(m.data)
                if sc and sc.output_transcription and sc.output_transcription.text:
                    r.heard += sc.output_transcription.text
                if sc and sc.turn_complete:
                    break
            if not r.chunks:
                raise RuntimeError("la sesión terminó sin audio")
            sim = difflib.SequenceMatcher(None, clinic.fold(r.text), clinic.fold(r.heard)).ratio() if r.heard else 1.0
            r.fidelity = round(sim, 2)
            # La transcripción de control a veces se corta aunque el audio esté entero: se mira también
            # la duración frente a la esperada (~14 caracteres por segundo).
            dur = sum(len(c) for c in r.chunks) / 48000
            expected = len(r.text) / 14
            r.duration = round(dur, 2)
            good = sim >= 0.75 or dur >= 0.75 * expected
            if good:
                (CACHE / f"{k}.pcm").write_bytes(b"".join(r.chunks))
                with open(CACHE / "index.jsonl", "a") as f:
                    f.write(json.dumps({"k": k, "text": r.text, "fidelity": r.fidelity}, ensure_ascii=False) + "\n")
            else:
                log.warning("boca: lectura poco fiel (%.2f, %.1fs de %.1fs): %r → %r", sim, dur, expected, r.text, r.heard)
            await r.finish(ok=True)
            if not good and not getattr(r, "retried", False):
                # se vuelve a sintetizar en segundo plano para la caché; esta vez ya ha sonado
                self.renders.pop(k, None)
                again = Render(r.text)
                again.retried = True
                self.renders[k] = again
                asyncio.create_task(self._render(k, again))
        finally:
            if cm:
                asyncio.create_task(self._close(cm))

    async def _render_tts(self, r: Render):
        """Último recurso: TTS no en directo (tarda más en empezar, pero suena). PCM a 24 kHz como la boca."""
        cfg = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE))))
        res = await asyncio.wait_for(CLIENT.aio.models.generate_content(
            model=FALLBACK_TTS_MODEL, contents=f"Read aloud exactly, in a warm professional tone: {r.text}", config=cfg), timeout=12)
        data = b"".join(p.inline_data.data for p in res.candidates[0].content.parts if p.inline_data and p.inline_data.data)
        if not data:
            raise RuntimeError("TTS sin audio")
        for i in range(0, len(data), 9600):
            await r.add(data[i:i + 9600])
        await r.finish(ok=True)

    async def _refill(self):
        if self.pool.qsize() < self.pool_size:
            try:
                await self.pool.put(await self._connect())
            except Exception:  # noqa: BLE001
                pass

    async def prewarm(self, texts: list, parallel: int = 3, fmt: str = "pcm24"):
        """Sintetiza de antemano las frases fijas (queda en disco para siempre). Cada elemento: texto o (texto, lengua)."""
        await _prewarm(self, texts, parallel, fmt)


async def _prewarm(mouth, texts: list, parallel: int, fmt: str):
    sem = asyncio.Semaphore(parallel)

    async def one(item):
        t, lang = item if isinstance(item, tuple) else (item, None)
        async with sem:
            r = mouth.render(t, lang=lang, fmt=fmt)
            async for _ in r.stream():
                pass
    await asyncio.gather(*[one(t) for t in texts], return_exceptions=True)


# ================================================================ boca de ElevenLabs

def _eleven_key() -> str:
    if os.environ.get("ELEVENLABS_API_KEY"):
        return os.environ["ELEVENLABS_API_KEY"]
    f = Path.home() / ".claude/.secrets/elevenlabs.env"
    if f.exists():
        for line in f.read_text().splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


ELEVEN_API = "https://api.elevenlabs.io"
# eleven_v3_conversational: tan rápido como flash_v2_5 (mediana ~150 ms al primer byte), más fiel leyendo cifras
# deletreadas, y el único con catalán y gallego. Sin euskera: el euskera lo lee Gemini.
ELEVEN_MODEL = os.environ.get("ELEVEN_MODEL", "eleven_v3_conversational")
ELEVEN_MODEL_V3 = "eleven_v3_conversational"
ELEVEN_MODEL_SLOW = os.environ.get("ELEVEN_MODEL_SLOW", "eleven_flash_v2_5")
ELEVEN_SLOW_SPEED = float(os.environ.get("ELEVEN_SLOW_SPEED", "0.85"))
# Ninguna voz suena nativa a la vez en inglés y en castellano: Alice (británica, de serie) para el inglés y
# Llanos Aguilar (peninsular, de la biblioteca, añadida a la cuenta) para castellano, catalán y gallego.
ELEVEN_VOICES = {
    "en": os.environ.get("ELEVEN_VOICE_EN", "Xb7hH8MSUJpSbSDYk0k2"),
    "es": os.environ.get("ELEVEN_VOICE_ES", "PksrhvpHrGUgesnsmLTX"),
}
ELEVEN_VOICES["ca"] = os.environ.get("ELEVEN_VOICE_CA", ELEVEN_VOICES["es"])
ELEVEN_VOICES["gl"] = os.environ.get("ELEVEN_VOICE_GL", ELEVEN_VOICES["es"])
ELEVEN_TTFB_S = float(os.environ.get("ELEVEN_TTFB_S", "1.2"))
ELEVEN_FORMATS = {"ulaw8": ("ulaw_8000", 8000), "pcm24": ("pcm_24000", 48000)}   # formato → (output_format, bytes/s)


class ElevenMouth:
    """Boca con el TTS en streaming de ElevenLabs. Misma interfaz que Mouth: render(text, lang, fmt) → Render, warm(),
    prewarm(). La conexión HTTP se mantiene abierta (la primera petición en frío tarda ~1 s; en caliente, ~150 ms).
    Si ElevenLabs falla o no da el primer byte en ELEVEN_TTFB_S, se arranca Gemini y habla el primero que suene.
    Todo lo sintetizado entero queda en disco: la cuenta tiene un cupo de caracteres al mes."""

    def __init__(self, fallback: Mouth, max_parallel: int = int(os.environ.get("ELEVEN_CONCURRENCY", "5"))):
        self.fallback = fallback
        self.renders: dict[str, Render] = {}
        self.sem = asyncio.Semaphore(max_parallel)
        self.max_parallel = max_parallel
        self.key_ = _eleven_key()
        self.enabled = bool(self.key_)
        self.http: httpx.AsyncClient | None = None
        self._keep: asyncio.Task | None = None
        self.last_use = 0.0

    def _client(self) -> httpx.AsyncClient:
        if self.http is None:
            self.http = httpx.AsyncClient(
                base_url=ELEVEN_API, headers={"xi-api-key": self.key_},
                timeout=httpx.Timeout(20.0, connect=5.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=8, keepalive_expiry=120))
        return self.http

    @staticmethod
    def voice_model(lang: str | None) -> tuple[str, str]:
        voice = ELEVEN_VOICES.get(lang or "en", ELEVEN_VOICES["en"])
        model = ELEVEN_MODEL if lang in (None, "en", "es") or "v3" in ELEVEN_MODEL else ELEVEN_MODEL_V3
        return voice, model

    @staticmethod
    def key(text: str, lang: str | None, fmt: str) -> str:
        voice, model = ElevenMouth.voice_model(lang)
        return hashlib.sha1(f"el|{model}|{voice}|{lang or ''}|{fmt}|{text}".encode()).hexdigest()[:20]

    async def warm(self):
        """Abre tantas conexiones como peticiones a la vez admite la cuenta (en paralelo, para que queden todas en el
        pool) y las mantiene vivas: con varias llamadas a la vez, abrir una conexión nueva costaba ~1 s."""
        await asyncio.gather(self.fallback.warm(), self._ping(self.max_parallel), return_exceptions=True)
        if self._keep is None:
            self._keep = asyncio.create_task(self._keepalive())

    async def _ping(self, n: int = 1):
        if not self.enabled:
            return
        cli = self._client()

        async def one():
            try:
                await cli.get("/v1/user/subscription", timeout=5)   # la respuesta más pequeña
            except Exception as e:  # noqa: BLE001
                log.warning("boca ElevenLabs: no se pudo abrir conexión: %s", e)
        await asyncio.gather(*[one() for _ in range(n)])

    async def _keepalive(self):
        while True:
            await asyncio.sleep(20)
            if time.time() - self.last_use > 15:
                await self._ping(self.max_parallel)

    def render(self, text: str, lang: str | None = None, fmt: str = "pcm24", live: bool = True, slow: bool = False) -> Render:
        """live=False (precarga): sin carrera con Gemini y con reintentos; lo que importa es que quede en disco.
        slow=True: voz pausada para quien oye mal o llama con ruido (lo pide la nota de su ficha)."""
        if not self.enabled or lang not in (None, "en", "es", "ca", "gl") or fmt not in ELEVEN_FORMATS:
            return self.fallback.render(text)
        k = self.key(text, lang, fmt) + ("-lento" if slow else "")
        r = self.renders.get(k)
        if r and (r.ok or not r.done):
            return r
        r = Render(text, fmt)
        r.slow = slow
        self.renders[k] = r
        f = CACHE / f"{k}.{'ulaw' if fmt == 'ulaw8' else 'pcm'}"
        if f.exists():
            r.chunks, r.done, r.cached, r.first_ms, r.source = [f.read_bytes()], True, True, 0, "elevenlabs"
            return r
        asyncio.create_task(self._render(k, r, lang, fmt, f, live))
        return r

    async def _render(self, k: str, r: Render, lang: str | None, fmt: str, f: Path, live: bool = True):
        """ElevenLabs primero; si a los ELEVEN_TTFB_S no ha sonado (o ha fallado), también Gemini, y gana el primero
        que da audio. El otro se cancela. Solo se guarda en disco lo de ElevenLabs que ha llegado entero."""
        win: dict[str, str | None] = {"w": None}
        first = asyncio.Event()

        def sink(name: str, rfmt: str):
            async def add(b: bytes):
                if win["w"] is None:
                    win["w"], r.fmt, r.source = name, rfmt, name
                    first.set()
                if win["w"] == name and b:
                    await r.add(b)
            return add

        if not live:
            for attempt in range(3):
                try:
                    await self._eleven(r.text, lang, fmt, sink("elevenlabs", fmt), slow=getattr(r, "slow", False))
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("boca ElevenLabs (precarga, intento %d): %s", attempt + 1, e)
                    if r.chunks:
                        break
                    await asyncio.sleep(1 + attempt)
            else:
                await r.finish(ok=False)
                self.renders.pop(k, None)
                return
            self._save(k, r, lang, fmt, f)
            await r.finish(ok=True)
            return
        tasks = {"elevenlabs": asyncio.create_task(self._eleven(r.text, lang, fmt, sink("elevenlabs", fmt), slow=getattr(r, "slow", False)))}
        fw = asyncio.create_task(first.wait())
        try:
            await asyncio.wait([tasks["elevenlabs"], fw], timeout=ELEVEN_TTFB_S, return_when=asyncio.FIRST_COMPLETED)
            if win["w"] is None:
                el = tasks["elevenlabs"]
                why = f"error: {el.exception()!r}" if el.done() and not el.cancelled() and el.exception() else                     ("sin audio" if el.done() else f"sin audio en {ELEVEN_TTFB_S:.1f} s")
                log.warning("boca ElevenLabs (%s): suena Gemini: %r", why, r.text[:60])
                tasks["gemini"] = asyncio.create_task(self._gemini(r.text, sink("gemini", "pcm24")))
                while win["w"] is None and any(not t.done() for t in tasks.values()):
                    await asyncio.wait([fw, *[t for t in tasks.values() if not t.done()]], return_when=asyncio.FIRST_COMPLETED)
            if win["w"] is None:
                await r.finish(ok=False)
                return
            for name, t in tasks.items():
                if name != win["w"]:
                    t.cancel()
            try:
                complete = await tasks[win["w"]]
            except Exception as e:  # noqa: BLE001
                log.warning("boca (%s) cortada a mitad: %s", win["w"], e)
                complete = False
            if win["w"] == "elevenlabs" and complete:
                self._save(k, r, lang, fmt, f)
            elif win["w"] == "gemini":
                self.renders.pop(k, None)     # la próxima vez se vuelve a pedir a ElevenLabs
            await r.finish(ok=True)
        finally:
            fw.cancel()
            if not r.done:
                await r.finish(ok=bool(r.chunks))

    def _save(self, k: str, r: Render, lang: str | None, fmt: str, f: Path):
        """A disco solo si la duración es razonable para el texto (~14 caracteres por segundo)."""
        data = b"".join(r.chunks)
        r.duration = round(len(data) / ELEVEN_FORMATS[fmt][1], 2)
        expected = len(r.text) / 14
        if 0.4 * expected <= r.duration <= 3 * expected + 2:
            f.write_bytes(data)
            with open(CACHE / "index.jsonl", "a") as fh:
                fh.write(json.dumps({"k": k, "text": r.text, "lang": lang, "fmt": fmt, "src": "elevenlabs"}, ensure_ascii=False) + "\n")
        else:
            log.warning("boca ElevenLabs: duración rara (%.1f s para %.1f s esperados), no se guarda: %r", r.duration, expected, r.text)

    async def _eleven(self, text: str, lang: str | None, fmt: str, add, slow: bool = False) -> bool:
        voice, model = self.voice_model(lang)
        if slow:
            # medido el 20-09-2026: eleven_v3_conversational ignora `speed` (misma duración a 0,75 que a 1,0);
            # flash_v2_5 la respeta (+30 % de duración a 0,75). Para hablar despacio, la misma voz con ese modelo.
            model = ELEVEN_MODEL_SLOW
        params = {"output_format": ELEVEN_FORMATS[fmt][0]}
        if "v3" not in model:
            params["optimize_streaming_latency"] = "3"
        body = {"text": text, "model_id": model}
        if lang:
            body["language_code"] = lang
        if slow:
            body["voice_settings"] = {"speed": ELEVEN_SLOW_SPEED}
        async with self.sem:
            self.last_use = time.time()
            async with self._client().stream("POST", f"/v1/text-to-speech/{voice}/stream", params=params, json=body) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}: {(await resp.aread())[:200]!r}")
                odd = b""
                async for chunk in resp.aiter_bytes():
                    if fmt == "pcm24":           # PCM de 16 bits: nunca partir una muestra entre dos trozos
                        chunk, odd = odd + chunk, b""
                        if len(chunk) % 2:
                            chunk, odd = chunk[:-1], chunk[-1:]
                    await add(chunk)
            self.last_use = time.time()
        return True

    async def _gemini(self, text: str, add) -> bool:
        g = self.fallback.render(text)
        async for c in g.stream():
            await add(c)
        return False   # lo de Gemini ya lo guarda su propia caché

    async def prewarm(self, texts: list, parallel: int = 3, fmt: str = "pcm24"):
        """Precarga sin carrera con Gemini: lo que suene en directo después debe ser la voz de ElevenLabs."""
        sem = asyncio.Semaphore(parallel)

        async def one(item):
            t, lang = item if isinstance(item, tuple) else (item, None)
            async with sem:
                r = self.render(t, lang=lang, fmt=fmt, live=False)
                async for _ in r.stream():
                    pass
        await asyncio.gather(*[one(t) for t in texts], return_exceptions=True)


GEMINI_MOUTH = Mouth()
MOUTH_KIND = os.environ.get("MOUTH", "elevenlabs")
MOUTH = ElevenMouth(GEMINI_MOUTH) if MOUTH_KIND == "elevenlabs" and _eleven_key() else GEMINI_MOUTH


def fixed_phrases() -> list[tuple[str, str]]:
    """Todas las frases de plantilla que no llevan huecos, en las cinco lenguas, con su lengua (la boca elige voz por lengua)."""
    import nlg
    out = []
    for key, by_lang in nlg.T.items():
        for lang, variants in by_lang.items():
            for v in variants:
                if "{" not in v:
                    out.append((nlg.contract(lang, v), lang))
    return out
