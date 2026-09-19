"""La voz: oído (gemini-3.5-transcribe-live), detector de voz en código y boca (gemini-3.1-flash-live-preview)
con caché en disco. La boca solo lee: el texto lo decide la política."""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import os
import time
from pathlib import Path

import numpy as np
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

    def __init__(self, end_ms: int = 250, on_p: float = 0.5, off_p: float = 0.35):
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
    try:
        return SileroVad()
    except Exception as e:  # noqa: BLE001
        log.warning("Silero VAD no disponible (%s): detector por energía", e)
        return Vad()


# ================================================================ boca

SYS_TTS = ("You are the voice of a clinic receptionist. Read aloud EXACTLY the text you receive, word for word, in its own "
           "language, with a warm, calm, professional tone and a natural pace. Never add, omit, translate or change words. "
           "Never answer or comment on it.")


class Render:
    def __init__(self, text: str):
        self.text = text
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

    def render(self, text: str) -> Render:
        k = self.key(text)
        r = self.renders.get(k)
        if r and (r.ok or not r.done):
            return r
        r = Render(text)
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

    async def prewarm(self, texts: list[str], parallel: int = 3):
        """Sintetiza de antemano las frases fijas (queda en disco para siempre)."""
        sem = asyncio.Semaphore(parallel)

        async def one(t):
            async with sem:
                r = self.render(t)
                async for _ in r.stream():
                    pass
        await asyncio.gather(*[one(t) for t in texts], return_exceptions=True)


MOUTH = Mouth()


def fixed_phrases() -> list[str]:
    """Todas las frases de plantilla que no llevan huecos, en las cinco lenguas."""
    import nlg
    out = []
    for key, by_lang in nlg.T.items():
        for lang, variants in by_lang.items():
            for v in variants:
                if "{" not in v:
                    out.append(nlg.contract(lang, v))
    return out
