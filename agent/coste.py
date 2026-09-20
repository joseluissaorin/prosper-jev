"""Lo que cuesta UNA llamada, en peticiones, tokens, caracteres, segundos y dinero.

Cada `Conv` lleva un `Coste`. Quien gasta (el cliente de Jev, el planificador, el cliente de la API de la clínica, la
boca) no conoce la llamada: anota en el medidor ACTUAL, una `contextvars.ContextVar`. Hay hasta 10 llamadas a la vez en
un mismo bucle de eventos y cada una vive en sus propias tareas; una tarea hereda el contexto de quien la crea, así
que las sombras de la especulación, las fichas pedidas por adelantado y la voz prerrenderizada se apuntan a la llamada
que las lanzó. Las sombras comparten además el MISMO objeto `Coste` que su llamada (`shadow.coste = self.coste`): lo
que gasta una especulación descartada también se ha pagado.

Regla de la casa: medir nunca puede tirar una llamada. Todo lo que anota va envuelto y calla si falla.

Lo que NO entra: la precarga de frases fijas al arrancar el servidor (no hay llamada en curso: es coste fijo del
despliegue, no de la llamada) y la telefonía de Twilio (la paga el arnés del jurado, no nosotros).
"""
from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------- precios
# PRECIOS DE LISTA, en USD, para VERIFICAR antes de citarlos: cambian sin aviso y no incluyen descuentos, créditos
# gratuitos ni impuestos. Fuente y fecha de cada uno al lado. Unidades: *_mtok = por millón de tokens;
# *_kchar = por mil caracteres; *_min = por minuto de audio.
PRECIOS = {
    # Jev (TypeSafe, Sistema 1): 0,042 USD/1M tokens de entrada. Fuente: la constante que ya usaba demo/server.py
    # (panel «jev_cost_usd»), tomada de la documentación de TypeSafe; sin verificar de nuevo el 20-09-2026.
    "jev_mtok": 0.042,
    # Planificador por modelo: (entrada, salida) por millón de tokens.
    "planificador_mtok": {
        # OpenRouter, openai/gpt-oss-120b servido por Groq. Fuente: API pública de OpenRouter
        # (GET /api/v1/models/openai/gpt-oss-120b/endpoints), consultada el 20-09-2026. Cerebras: 0,35 / 0,75.
        "openai/gpt-oss-120b": (0.15, 0.60),
        # Gemini 3.5 Flash-Lite, nivel de pago. Fuente: ai.google.dev/gemini-api/docs/pricing, 20-09-2026
        # (el mismo precio que publica OpenRouter para google/gemini-3.5-flash-lite).
        "gemini-3.5-flash-lite": (0.30, 2.50),
    },
    "planificador_mtok_desconocido": (0.30, 2.50),      # modelo sin precio en la tabla: se cobra como el más caro
    # ElevenLabs, plan Creator: 22 USD/mes con 220 000 caracteres → 0,10 USD por mil caracteres efectivos si se
    # consume la cuota entera (los modelos Flash/Turbo descuentan la mitad: 0,05). Fuente: elevenlabs.io/pricing/api,
    # 20-09-2026. Se usa el precio alto.
    "tts_kchar": {"elevenlabs": 0.10,
                  # Boca de respaldo (Gemini Live/TTS): se factura por tokens de audio de salida, 0,018 USD/min según
                  # ai.google.dev/gemini-api/docs/pricing (20-09-2026); a ~14 caracteres por segundo hablados (la cifra
                  # que usa demo/voice.py) son 840 caracteres por minuto → ~0,021 USD por mil caracteres. ESTIMACIÓN.
                  "gemini": 0.021},
    # Oído (Gemini Live): entrada de audio 0,005 USD/min (3,00 USD/1M tokens a 25 tokens por segundo). Fuente:
    # ai.google.dev/gemini-api/docs/pricing, 20-09-2026, para el modelo de audio nativo de la Live API; el modelo de
    # transcripción que usamos (STT_MODEL) no tiene precio propio publicado: se toma este como aproximación.
    "stt_min": 0.005,
}

ACTUAL: contextvars.ContextVar = contextvars.ContextVar("coste_actual", default=None)


@dataclass
class Coste:
    inicio: float = field(default_factory=time.time)
    jev_peticiones: int = 0
    jev_tokens: int = 0
    jev_sin_uso: int = 0                       # respuestas de Jev que no traían `usage` (sus tokens no se han contado)
    planificador: dict = field(default_factory=dict)   # modelo → {peticiones, entrada, salida, sin_uso}
    api_lecturas: int = 0
    api_escrituras: int = 0
    tts_sintetizados: dict = field(default_factory=dict)   # fuente → caracteres pedidos a un servicio de pago
    tts_cache_chars: int = 0                   # caracteres hablados que ya estaban en disco: no cuestan
    tts_hablados_chars: int = 0
    stt_sesiones: int = 0

    # ---- anotar (nunca lanzan)
    def jev(self, tokens) -> None:
        try:
            self.jev_peticiones += 1
            if tokens is None:
                self.jev_sin_uso += 1
            else:
                self.jev_tokens += int(tokens)
        except Exception:  # noqa: BLE001
            pass

    def paso(self, modelo: str, entrada, salida) -> None:
        try:
            m = self.planificador.setdefault(str(modelo), {"peticiones": 0, "entrada": 0, "salida": 0, "sin_uso": 0})
            m["peticiones"] += 1
            if entrada is None and salida is None:
                m["sin_uso"] += 1
            m["entrada"] += int(entrada or 0)
            m["salida"] += int(salida or 0)
        except Exception:  # noqa: BLE001
            pass

    def api(self, metodo: str) -> None:
        try:
            if str(metodo).upper() == "GET":
                self.api_lecturas += 1
            else:
                self.api_escrituras += 1
        except Exception:  # noqa: BLE001
            pass

    def tts(self, chars, fuente: str) -> None:
        try:
            self.tts_sintetizados[str(fuente)] = self.tts_sintetizados.get(str(fuente), 0) + int(chars)
        except Exception:  # noqa: BLE001
            pass

    def hablado(self, chars, cached: bool) -> None:
        try:
            self.tts_hablados_chars += int(chars)
            if cached:
                self.tts_cache_chars += int(chars)
        except Exception:  # noqa: BLE001
            pass

    # ---- resumen
    def resumen(self, duracion_s: float | None = None) -> dict:
        """El bloque `cost` del informe de la llamada: recuentos, USD por componente, USD total y segundos."""
        try:
            dur = float(duracion_s if duracion_s is not None else time.time() - self.inicio)
            usd_plan = 0.0
            for modelo, m in self.planificador.items():
                pe, ps = PRECIOS["planificador_mtok"].get(modelo, PRECIOS["planificador_mtok_desconocido"])
                usd_plan += m["entrada"] * pe / 1e6 + m["salida"] * ps / 1e6
            usd_tts = sum(n * PRECIOS["tts_kchar"].get(f, PRECIOS["tts_kchar"]["elevenlabs"]) / 1e3
                          for f, n in self.tts_sintetizados.items())
            stt_s = dur * self.stt_sesiones
            usd = {"jev": self.jev_tokens * PRECIOS["jev_mtok"] / 1e6, "planner": usd_plan, "tts": usd_tts,
                   "stt": stt_s / 60 * PRECIOS["stt_min"], "clinic_api": 0.0}
            return {
                "counts": {"jev_requests": self.jev_peticiones, "jev_tokens": self.jev_tokens, "jev_no_usage": self.jev_sin_uso,
                           "planner": {k: dict(v) for k, v in self.planificador.items()},
                           "planner_requests": sum(m["peticiones"] for m in self.planificador.values()),
                           "api_reads": self.api_lecturas, "api_writes": self.api_escrituras,
                           "tts_chars_synthesised": dict(self.tts_sintetizados),
                           "tts_chars_spoken": self.tts_hablados_chars, "tts_chars_from_cache": self.tts_cache_chars,
                           "stt_sessions": self.stt_sesiones, "stt_audio_s": round(stt_s, 1)},
                "usd": {k: round(v, 6) for k, v in usd.items()},
                "usd_total": round(sum(usd.values()), 6),
                "seconds": round(dur, 1),
                "usd_per_minute": round(sum(usd.values()) / dur * 60, 6) if dur > 0 else None,
                "notes": "Precios de lista (agent/coste.py, PRECIOS), por verificar. El audio del oído se aproxima como "
                         "duración de la llamada × sesiones de transcripción abiertas. La telefonía no se incluye.",
            }
        except Exception as e:  # noqa: BLE001
            return {"error": repr(e)[:200]}


def actual() -> Coste | None:
    try:
        return ACTUAL.get()
    except Exception:  # noqa: BLE001
        return None


def usar(c: Coste | None) -> None:
    """Fija el medidor de la tarea en curso (y de las que cree a partir de ahora)."""
    try:
        if c is not None and ACTUAL.get() is not c:
            ACTUAL.set(c)
    except Exception:  # noqa: BLE001
        pass


# ---- atajos para quien gasta sin conocer la llamada: si no hay llamada en curso, no hacen nada

def anotar_jev(tokens) -> None:
    c = actual()
    if c is not None:
        c.jev(tokens)


def anotar_paso(modelo: str, entrada, salida) -> None:
    c = actual()
    if c is not None:
        c.paso(modelo, entrada, salida)


def anotar_api(metodo: str) -> None:
    c = actual()
    if c is not None:
        c.api(metodo)


def anotar_tts(chars, fuente: str) -> None:
    c = actual()
    if c is not None:
        c.tts(chars, fuente)


def uso_openrouter(js: dict) -> tuple:
    """(entrada, salida) del `usage` de una respuesta de OpenRouter; (None, None) si no lo trae."""
    try:
        u = js.get("usage") or {}
        if not u:
            return None, None
        return int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    except Exception:  # noqa: BLE001
        return None, None


def uso_gemini(r) -> tuple:
    """(entrada, salida) del `usage_metadata` de Gemini; los tokens de razonamiento se facturan como salida."""
    try:
        u = getattr(r, "usage_metadata", None)
        if u is None:
            return None, None
        ent = int(getattr(u, "prompt_token_count", 0) or 0)
        sal = int(getattr(u, "candidates_token_count", 0) or 0) + int(getattr(u, "thoughts_token_count", 0) or 0)
        return ent, sal
    except Exception:  # noqa: BLE001
        return None, None
