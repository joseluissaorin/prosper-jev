"""Cliente de la API de la clínica de Prosper (El Turno). Mismo cliente para la API real y para la falsa local.

    PROSPER_API_BASE_URL   (por defecto la real; http://127.0.0.1:8770 para la falsa)
    PROSPER_API_KEY        o ~/.claude/.secrets/prosper.env (PLATFORM_API_KEY=pk-…)
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

MADRID = ZoneInfo("Europe/Madrid")
BASE = os.environ.get("PROSPER_API_BASE_URL", "https://hackspain.getprosperapp.com").rstrip("/")


def _key() -> str:
    for k in ("PROSPER_API_KEY", "PLATFORM_API_KEY"):
        if os.environ.get(k):
            return os.environ[k]
    f = Path.home() / ".claude/.secrets/prosper.env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and line.split("=", 1)[0].strip() in ("PLATFORM_API_KEY", "PROSPER_API_KEY"):
                return line.split("=", 1)[1].strip()
    return "pk-local"


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"{status}: {body[:300]}")
        self.status, self.body = status, body


class Prosper:
    def __init__(self, base: str = BASE, key: str | None = None):
        self.base = base
        self.c = httpx.AsyncClient(base_url=base, timeout=httpx.Timeout(8.0, connect=4.0),
                                   headers={"X-Api-Key": key or _key()},
                                   limits=httpx.Limits(max_keepalive_connections=20, max_connections=40))
        self._clinic: dict | None = None
        self.log: list[dict] = []          # cada petición, para la traza y el panel
        # Caché corta de lecturas con deduplicación: la especulación sobre los parciales ya pide los mismos huecos
        # (o la misma ficha) mientras la persona habla; el turno definitivo los reutiliza en vez de repetir el viaje.
        self._cache: dict = {}
        self.CACHE_S = float(os.environ.get("PROSPER_CACHE_S", "20"))

    async def _get(self, path: str, params=None) -> dict:
        key = (path, tuple(sorted(params.items())) if isinstance(params, dict) else tuple(params or ()))
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.CACHE_S:
            try:
                return await asyncio.shield(hit[1])
            except Exception:  # noqa: BLE001
                self._cache.pop(key, None)
        task = asyncio.ensure_future(self._get_net(path, params))
        self._cache[key] = (time.time(), task)
        if len(self._cache) > 500:
            for k in [k for k, v in self._cache.items() if time.time() - v[0] > self.CACHE_S]:
                self._cache.pop(k, None)
        try:
            return await asyncio.shield(task)
        except Exception:
            self._cache.pop(key, None)
            raise

    async def _get_net(self, path: str, params=None) -> dict:
        t0 = time.perf_counter()
        for attempt in range(3):
            try:
                r = await self.c.get(path, params=params)
                break
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))
        ms = round((time.perf_counter() - t0) * 1000)
        self.log.append({"t": time.time(), "method": "GET", "path": path, "params": params, "status": r.status_code, "ms": ms})
        if r.status_code != 200:
            raise ApiError(r.status_code, r.text)
        return r.json()

    async def clinic(self) -> dict:
        if self._clinic is None:
            self._clinic = await self._get("/api/v1/clinic")
        return self._clinic

    async def directory(self, *, name=None, national_id=None, phone=None, date_of_birth=None) -> list[dict]:
        params = {k: v for k, v in {"name": name, "national_id": national_id, "phone": phone, "date_of_birth": date_of_birth}.items() if v}
        if not params:
            return []
        return (await self._get("/api/v1/directory", params))["matches"]

    async def appointments(self, patient_id: str, when: str = "upcoming") -> list[dict]:
        return (await self._get(f"/api/v1/patients/{patient_id}/appointments", {"when": when}))["appointments"]

    async def availability(self, date_from: date, date_to: date, *, provider_id=None, specialty_id=None, location_id=None,
                           patient_id=None, insurers: list[str] | None = None) -> dict:
        params = [("date_from", date_from.isoformat()), ("date_to", date_to.isoformat())]
        for k, v in (("provider_id", provider_id), ("specialty_id", specialty_id), ("location_id", location_id), ("patient_id", patient_id)):
            if v:
                params.append((k, v))
        for ins in insurers or []:
            params.append(("insurer", ins))
        return await self._get("/api/v1/availability", params)

    async def availability_span(self, first: date, last: date, **kw) -> dict:
        """Disponibilidad en un tramo de cualquier longitud (la API admite 14 días por petición)."""
        cal = (await self.clinic())["calendar"]
        start, end = max(first, date.fromisoformat(cal["starts"])), min(last, date.fromisoformat(cal["ends"]))
        span = cal.get("max_span_days", 14)
        chunks, d = [], start
        while d <= end:
            chunks.append((d, min(end, d + timedelta(days=span - 1))))
            d += timedelta(days=span)
        res = await asyncio.gather(*[self.availability(a, b, **kw) for a, b in chunks]) if chunks else []
        out = {"slots": [], "blocked": [], "providers": [], "appointment_type": None}
        seen_b, seen_p = set(), set()
        for r in res:
            out["slots"] += r.get("slots", [])
            out["appointment_type"] = out["appointment_type"] or r.get("appointment_type")
            for b in r.get("blocked", []):
                k = (b["provider_id"], b["restriction"])
                if k not in seen_b:
                    seen_b.add(k)
                    out["blocked"].append(b)
            for p in r.get("providers", []):
                if p["id"] not in seen_p:
                    seen_p.add(p["id"])
                    out["providers"].append(p)
        out["slots"].sort(key=lambda s: s["start_time"])
        return out

    async def submit(self, action: str, body: dict) -> dict:
        """POST /api/v1/submit/<action>. Reintenta en errores de red; 409 (ya aceptado) cuenta como éxito."""
        t0 = time.perf_counter()
        last = None
        for attempt in range(4):
            try:
                r = await self.c.post(f"/api/v1/submit/{action}", json=body)
            except httpx.TransportError as e:
                last = e
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            ms = round((time.perf_counter() - t0) * 1000)
            self.log.append({"t": time.time(), "method": "POST", "path": f"/submit/{action}", "body": body, "status": r.status_code, "ms": ms})
            if r.status_code in (200, 409):
                return {"status": r.status_code, **(r.json() if r.headers.get("content-type", "").startswith("application/json") else {})}
            if r.status_code >= 500 and attempt < 3:
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            raise ApiError(r.status_code, r.text)
        raise ApiError(0, str(last))

    async def close(self):
        await self.c.aclose()

# ---------------------------------------------------------------- utilidades del dominio

DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def dni_letter(digits: str) -> str:
    return DNI_LETTERS[int(digits) % 23]


def normalize_national_id(raw: str) -> tuple[str | None, str]:
    """Devuelve (id normalizado o None, diagnóstico). Si falta la letra, la deduce de los dígitos.
    Si la letra dicha no cuadra con los dígitos, devuelve None: hay que pedir que lo repita."""
    s = "".join(c for c in raw.upper() if c.isalnum())
    if not s:
        return None, "vacío"
    nie = s[0] in "XYZ"
    body = s[1:] if nie else s
    digits = "".join(c for c in body if c.isdigit())
    letters = [c for c in body if c.isalpha()]
    need = 7 if nie else 8
    if len(digits) == 7 and letters:
        # NIE con la letra inicial perdida o mal oída («Y, 1234567, X» → «1234567X» o «X1234567X»): si solo un
        # prefijo cuadra con la letra de control, es ese. La lectura final lo confirma con quien llama.
        fits = [p for p in "XYZ" if dni_letter(str("XYZ".index(p)) + digits) == letters[-1]]
        if len(fits) == 1 and not (nie and fits[0] == s[0]):
            return fits[0] + digits + letters[-1], f"prefijo del NIE deducido: {fits[0]}"
    if len(digits) != need:
        return None, f"{len(digits)} dígitos (hacen falta {need})"
    num = str("XYZ".index(s[0])) + digits if nie else digits
    good = dni_letter(num)
    if letters and letters[-1] != good:
        return None, f"la letra {letters[-1]} no cuadra con los dígitos (sería {good})"
    return (s[0] if nie else "") + digits + good, "ok" if letters else f"letra deducida: {good}"


def madrid_iso(dt: datetime) -> str:
    return dt.astimezone(MADRID).isoformat(timespec="seconds")


def parse_slot(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(MADRID)
