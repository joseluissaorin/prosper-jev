"""µ-law a 8 kHz (el formato de Twilio Media Streams) ↔ PCM de 16 bits, y remuestreo, en numpy.
(audioop ya no existe en Python 3.13)."""
from __future__ import annotations

import numpy as np

_BIAS, _CLIP = 0x84, 32635


def _build_decode() -> np.ndarray:
    u = ~np.arange(256, dtype=np.int32) & 0xFF
    sign = u & 0x80
    exp = (u >> 4) & 0x07
    man = u & 0x0F
    mag = (((man << 3) + _BIAS) << exp) - _BIAS
    return np.where(sign != 0, -mag, mag).astype(np.int16)


_DEC = _build_decode()


def ulaw_to_pcm16(b: bytes) -> np.ndarray:
    return _DEC[np.frombuffer(b, dtype=np.uint8)]


def pcm16_to_ulaw(x: np.ndarray) -> bytes:
    x = x.astype(np.int32)
    sign = np.where(x < 0, 0x80, 0)
    x = np.minimum(np.abs(x), _CLIP) + _BIAS
    exp = np.floor(np.log2(np.maximum(x, 1))).astype(np.int32) - 7
    exp = np.clip(exp, 0, 7)
    man = (x >> (exp + 3)) & 0x0F
    return (~(sign | (exp << 4) | man) & 0xFF).astype(np.uint8).tobytes()


def up_8k_to_16k(x: np.ndarray) -> bytes:
    """8 kHz → 16 kHz por interpolación lineal (suficiente para el transcriptor)."""
    n = len(x)
    if n == 0:
        return b""
    y = np.empty(2 * n, dtype=np.float32)
    y[0::2] = x
    y[1::2] = np.append((x[:-1].astype(np.float32) + x[1:]) / 2, x[-1])
    return y.astype(np.int16).tobytes()


def down_24k_to_8k(pcm24: bytes) -> np.ndarray:
    """24 kHz → 8 kHz: paso bajo simple (media de 3 muestras con solape) y diezmado."""
    x = np.frombuffer(pcm24, dtype=np.int16).astype(np.float32)
    if len(x) < 3:
        return np.zeros(0, dtype=np.int16)
    k = np.array([0.25, 0.5, 0.25], dtype=np.float32)
    y = np.convolve(x, k, mode="same")[::3]
    return np.clip(y, -32768, 32767).astype(np.int16)
