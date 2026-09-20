"""Comprobaciones SIN RED de la instrumentación de rigor: no llaman a Jev, ni a OpenRouter, ni a Gemini, ni a la clínica.

    python agent/pruebas_rigor.py

1. Coste por llamada: una respuesta falsa de OpenRouter con `usage` pasa por `conv.or_chat` y acaba en el bloque
   `cost` de `Conv.report()`; dos llamadas concurrentes en el mismo bucle no se mezclan; lo que gasta una tarea hija
   (como la sombra de la especulación) se apunta a su llamada; `api_calls` es el de la llamada, no el del proceso.
2. Cortacircuitos de OpenRouter: un fallo lo abre, con él abierto no se llama a OpenRouter, pasado el plazo se prueba
   de nuevo y se cierra; los dos cambios quedan como eventos para la traza.
3. Estadística: el intervalo de Wilson contra valores conocidos, percentiles y el agregado de repeticiones.
4. Medir nunca lanza, ni con datos absurdos.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

for k in ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ.setdefault(k, "clave-falsa-para-pruebas")
os.environ["CIRCUITO_S"] = "0.3"
os.environ["PLANNER_BACKEND"] = "openrouter"
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "demo")]

import conv  # noqa: E402
import coste  # noqa: E402
import estadistica as E  # noqa: E402
from google.genai import types  # noqa: E402


class _Resp:
    status_code, text = 200, ""

    def __init__(self, js):
        self._js = js

    def json(self):
        return self._js


class _OpenRouterFalso:
    def __init__(self):
        self.fail, self.n = False, 0

    async def post(self, url, json=None, timeout=None):
        self.n += 1
        await asyncio.sleep(0.01)
        if self.fail:
            raise TimeoutError("simulado")
        return _Resp({"choices": [{"message": {"content": "hola"}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 50}})


class _GeminiFalso:
    """Gemini que falla sin red: `llm_step` lo intenta cuando OpenRouter está cortado."""
    class aio:  # noqa: N801
        class models:  # noqa: N801
            @staticmethod
            async def generate_content(**kw):
                raise RuntimeError("gemini simulado caído")


MSGS = [types.Content(role="user", parts=[types.Part(text="hi")])]


async def _llamada(n: int) -> conv.Conv:
    c = conv.Conv(call_id=f"c{n}")
    coste.usar(c.coste)
    for _ in range(n):
        await conv.llm_step("s", MSGS, None)
    sombra = conv.Conv(call_id="sombra")            # crear una sombra no puede robarle el contexto a la llamada
    sombra.coste = c.coste
    await asyncio.ensure_future(conv.llm_step("s", MSGS, None))       # tarea hija: hereda el medidor
    coste.anotar_api("GET")
    coste.anotar_api("POST")
    coste.anotar_jev(2000)
    coste.anotar_jev(None)
    coste.anotar_tts(100, "elevenlabs")
    c.coste.hablado(150, True)
    c.coste.stt_sesiones = 2
    return c


async def prueba_coste() -> None:
    a, b = await asyncio.gather(_llamada(1), _llamada(4))
    ia, ib = await a.report(), await b.report()
    ra, rb = ia["cost"], ib["cost"]
    assert ra["counts"]["planner_requests"] == 2 and rb["counts"]["planner_requests"] == 5, (ra, rb)
    assert rb["counts"]["planner"]["openai/gpt-oss-120b"] == {"peticiones": 5, "entrada": 5000, "salida": 250, "sin_uso": 0}
    assert abs(rb["usd"]["planner"] - (5000 * 0.15 + 250 * 0.60) / 1e6) < 1e-9
    assert ia["api_calls"] == 2 and ra["counts"]["api_reads"] == 1 and ra["counts"]["api_writes"] == 1
    assert ra["counts"]["jev_requests"] == 2 and ra["counts"]["jev_tokens"] == 2000 and ra["counts"]["jev_no_usage"] == 1
    assert abs(ra["usd"]["jev"] - 2000 * 0.042 / 1e6) < 1e-12
    assert abs(ra["usd"]["tts"] - 0.01) < 1e-9 and ra["counts"]["tts_chars_from_cache"] == 150
    assert abs(ra["usd_total"] - sum(ra["usd"].values())) < 1e-5
    assert coste.actual() is None, "fuera de una llamada no hay medidor"
    coste.anotar_paso("x", 1, 1)                     # y anotar sin llamada no hace nada ni falla
    print("1. coste por llamada, aislado entre llamadas concurrentes: bien")
    print("   ", rb["counts"]["planner"], "→", rb["usd"])


async def prueba_circuito(falso: _OpenRouterFalso) -> None:
    conv.circuit_events()
    falso.fail = True
    try:
        await conv.llm_step("s", MSGS, None, timeout=1)
    except Exception:  # noqa: BLE001
        pass                                         # Gemini también está caído aquí: el error sube, como antes
    assert conv._cortado("or_down")
    n0 = falso.n
    conv._OR["down"] = 0.0
    try:
        await conv.llm_step("s", MSGS, None, timeout=1)
    except Exception:  # noqa: BLE001
        pass
    # con el circuito abierto el primer intento se salta; solo queda el último recurso de `llm_step` si Gemini está cortado
    assert falso.n == n0, "con el circuito abierto no se llama a OpenRouter"
    await asyncio.sleep(0.35)
    falso.fail = False
    assert not conv._cortado("or_down")
    await conv.llm_step("s", MSGS, None)
    evs = conv.circuit_events()
    kinds = [k for k, _ in evs]
    assert kinds[0] == "circuit_open" and kinds[-1] == "circuit_closed" and conv._OR["or_down"] == 0.0, evs
    assert conv.circuit_events() == []
    print("2. cortacircuitos: abre, salta OpenRouter mientras dura, reprueba al cabo del plazo y cierra: bien")
    print("   ", evs[0], evs[-1])


def prueba_estadistica() -> None:
    lo, hi = E.wilson(8, 10)
    assert abs(lo - 0.4902) < 5e-4 and abs(hi - 0.9433) < 5e-4, (lo, hi)      # valores de referencia (Wilson 1927, z=1,96)
    lo, hi = E.wilson(0, 10)
    assert lo == 0.0 and abs(hi - 0.2775) < 5e-4, (lo, hi)
    lo, hi = E.wilson(10, 10)
    assert abs(lo - 0.7225) < 5e-4 and hi == 1.0, (lo, hi)
    assert E.wilson(0, 0) == (0.0, 1.0)
    assert E.percentil([1, 2, 3, 4, 5], 50) == 3 and E.percentil([1, 2, 3, 4], 50) == 2.5 and E.percentil([], 50) is None
    assert abs(E.percentil(list(range(1, 101)), 90) - 90.1) < 1e-9
    r = E.Repeticiones()
    for rep, (a, b, c) in enumerate([(True, True, False), (True, False, False), (True, True, False)]):
        r.caso("a", rep, a)
        r.caso("b", rep, b)
        r.caso("c", rep, c)
        r.latencias(rep, [100 * (rep + 1), 200 * (rep + 1)])
    g = r.resumen()
    assert g["n_reps"] == 3 and g["ensayos"] == 9 and g["aciertos"] == 5
    assert g["inestables"] == [("b", 2, 3)] and g["siempre_fallan"] == [("c", 0, 3)] and g["siempre_pasan"] == 1
    assert g["por_rep"] == [2 / 3, 1 / 3, 2 / 3]
    assert g["latencia"]["medianas_por_rep"] == [150, 300, 450]
    txt = "\n".join(r.informe())
    assert "INESTABLES" in txt and "Wilson" in txt
    print("3. estadística (Wilson, percentiles, repeticiones): bien")


def prueba_nunca_lanza() -> None:
    c = coste.Coste()
    c.paso(None, "x", object())
    c.tts("z", 1)
    c.jev("q")
    c.api(None)
    c.hablado(None, 1)
    c.planificador["roto"] = 3
    out = c.resumen()
    assert isinstance(out, dict)
    assert coste.uso_openrouter({"usage": "roto"}) == (None, None) and coste.uso_gemini(object()) == (None, None)
    print("4. medir con datos absurdos no lanza: bien →", out)


async def main() -> None:
    falso = _OpenRouterFalso()
    conv._OR["client"] = falso
    conv.GEMINI = _GeminiFalso()
    await prueba_coste()
    await prueba_circuito(falso)
    prueba_estadistica()
    prueba_nunca_lanza()
    print("TODO BIEN")


if __name__ == "__main__":
    asyncio.run(main())
