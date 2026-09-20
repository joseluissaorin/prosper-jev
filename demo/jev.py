"""Cliente de Jev: conexión reutilizada, petición duplicada si tarda, y registro de cada juicio."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import httpx

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
# s: si no ha contestado, se lanza un duplicado. Medido desde España: el viaje a Oregón son ~200 ms de los ~300
# que tarda una petición, así que a los 0,25 s o ya viene de camino o se ha perdido; esperar 0,45 s no informa.
HEDGE_AFTER = float(os.environ.get("JEV_HEDGE_AFTER", "0.25"))
TIMEOUT = float(os.environ.get("JEV_TIMEOUT", "2.5"))


def _key() -> str:
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"]
    for line in (Path.home() / ".claude/.secrets/typesafe.env").read_text().splitlines():
        if line.startswith("TYPESAFE_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("Falta TYPESAFE_API_KEY")


try:                                   # el medidor por llamada vive en agent/coste.py; sin él (la demo sola) no se mide
    from coste import anotar_jev
except Exception:  # noqa: BLE001
    def anotar_jev(tokens) -> None:
        return None



class JevError(Exception):
    pass


class Jev:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            http2=False,
            timeout=httpx.Timeout(TIMEOUT, connect=2.0),
            limits=httpx.Limits(max_keepalive_connections=16, max_connections=32, keepalive_expiry=120),
            headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"},
        )
        self.tokens = 0
        self.calls = 0

    async def warm(self) -> None:
        """Abre conexiones de antemano para no pagar el saludo TLS en la primera pregunta."""
        q = {"w": {"type": "noul", "instructions": "Is this a greeting?"}}
        await asyncio.gather(*[self.ask("hola", q, hedge=False) for _ in range(3)], return_exceptions=True)

    async def _post(self, body: dict) -> dict:
        r = await self._client.post(URL, json=body)
        if r.status_code in (429, 529) or r.status_code >= 500:
            raise JevError(f"{r.status_code}")
        if r.status_code != 200:
            raise JevError(f"{r.status_code}: {r.text[:300]}")
        return r.json()

    async def ask(self, state, questions: dict, *, hedge: bool = True) -> dict:
        """Una petición con todas las preguntas. Devuelve {answers, ms, hedged, model}."""
        body = {"model": MODEL, "state": state, "questions": questions}
        t0 = time.perf_counter()
        first = asyncio.create_task(self._post(body))
        first.add_done_callback(_quiet)
        tasks = {first}
        hedged = False
        if hedge:
            done, _ = await asyncio.wait(tasks, timeout=HEDGE_AFTER)
            if not done:
                hedged = True
                dup = asyncio.create_task(self._post(body))
                dup.add_done_callback(_quiet)
                tasks.add(dup)
        result, last_exc = None, None
        pending = set(tasks)
        while pending and result is None:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=TIMEOUT)
            if not done:
                break
            for t in done:
                if t.exception() is None:
                    result = t.result()
                    break
                last_exc = t.exception()
        for t in pending:
            t.cancel()
        if result is None:
            # un reintento con espera corta si todo falló (429/529)
            try:
                await asyncio.sleep(0.2)
                result = await self._post(body)
            except Exception as e:  # noqa: BLE001
                raise JevError(str(last_exc or e)) from e
        ms = (time.perf_counter() - t0) * 1000
        self.calls += 1
        self.tokens += result.get("usage", {}).get("input_tokens", 0)
        try:                           # además del contador del proceso, el de la llamada en curso (contextvars)
            anotar_jev((result.get("usage") or {}).get("input_tokens"))
        except Exception:  # noqa: BLE001
            pass
        return {"answers": result["answers"], "ms": round(ms), "hedged": hedged, "model": result.get("model")}

    async def close(self) -> None:
        await self._client.aclose()


def _quiet(t: asyncio.Task) -> None:
    """Las peticiones duplicadas que pierden la carrera no deben dejar avisos."""
    if not t.cancelled():
        t.exception()


JEV = Jev()

# ---------------------------------------------------------------- ayudantes para construir preguntas

def noul(instructions, yes: str | None = None, no: str | None = None) -> dict:
    q = {"type": "noul", "instructions": instructions}
    if yes or no:
        q["criteria"] = {"true": yes or "", "false": no or ""}
    return q


def choice(instructions, options: dict) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions, levels: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": levels}
