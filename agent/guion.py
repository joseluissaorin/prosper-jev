"""Llamadas con GUION FIJO contra la clínica real, en solo lectura y sin enviar nada (SUBMIT=0).

Los arneses sortean lo que dice quien llama; aquí lo que dice es siempre lo mismo, así que lo único que cambia entre
dos pasadas es el agente. Sirve para oír (en texto) cómo trata a una persona con una ficha concreta: si usa su nombre,
si le ofrece su médico de siempre, si la nota le cambia el trato, si a un «no» pregunta qué no encaja.

    ../.venv/bin/python guion.py                 # todos los guiones
    ../.venv/bin/python guion.py habitual sorda  # solo esos
    ../.venv/bin/python guion.py --rep 5 habitual   # varianza: la misma llamada cinco veces
"""
from __future__ import annotations

import os

os.environ["SUBMIT"] = "0"
os.environ.pop("PROSPER_API_BASE_URL", None)
from pathlib import Path  # noqa: E402

_ENV = Path.home() / ".config/prosper-agent.env"
if _ENV.exists():
    for _l in _ENV.read_text().splitlines():
        if "=" in _l and not _l.startswith("#"):
            _k, _v = _l.split("=", 1)
            if not _k.startswith("PROSPER_") and _k.strip() != "SUBMIT":
                os.environ.setdefault(_k.strip(), _v.strip())

import asyncio  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "demo"))
from conv import Conv  # noqa: E402
import say as S  # noqa: E402

# nombre → (línea desde la que llama, frases de quien llama, lo que hay que ver en la llamada)
GUIONES = {
    "habitual": ("+34778059454", ["Hola, buenos días.", "Quería pedir cita con el médico de cabecera.", "Soy Andrea Sanz Ruiz.",
                                  "Mejor con mi médico de siempre.", "Sí, esa me va bien.", "No, nada más, gracias."],
                 "siete visitas con el Dr. Sáez: se le ofrece lo primero Y su médico; no se pregunta si ha venido antes"),
    "sorda": ("+34776484864", ["Hola.", "Quiero una cita de medicina general.", "Concepción Ramos Álvarez.", "¿Cómo dice?", "Sí, vale.",
                               "Nada más, adiós."],
              "oye mal: voz pausada, frases cortas, la fecha dos veces; tiene visitas en tres sedes"),
    "apellido": ("+34688285587", ["Hello.", "I'd like a general practice appointment, please.", "Oliver Baker.", "No.", "Later in the week, in the afternoon.",
                                  "Yes, that's fine.", "No, that's all."],
                 "prefiere el apellido (Mr Baker); a un «no» a secas se pregunta qué no encaja"),
    "ya_tiene": ("+34684453253", ["Buenas tardes.", "Quería coger una cita con medicina general.", "Elena Rubio Suárez.", "Ah, no, es otra cosa, una nueva.",
                                  "Sí.", "Nada más."],
                 "ya tiene una cita: se menciona antes de dar otra; regular de la Dra. Ortiz"),
    "pariente": ("+34711482334", ["Hello.", "I need to move my mother's appointment.", "She's Ella Wood Smith.", "I'm her son, Peter.",
                                  "Any day after the fourteenth of October.", "Yes please.", "That's all, bye."],
                 "un pariente suele hablar por ella: se aclara con quién se habla"),
}


async def una(nombre: str, rep: int = 0) -> dict:
    frm, frases, _ = GUIONES[nombre]
    b = Conv(call_id=f"guion-{nombre}-{uuid.uuid4().hex[:6]}", from_number=frm)
    dichos: list[str] = []
    b.on_early = lambda t: (dichos.append(t), b.spoken(t))
    await b.begin()
    for o in b.opening():
        b.spoken(o["text"])
        print(f"  AGENTE  {o['text']}")
    ms = []
    for f in frases:
        print(f"  LLAMA   {f}")
        t0 = time.perf_counter()
        p = await b.perceive(f)
        dichos.clear()
        outs = await b.handle(f, p)
        ms.append(round((time.perf_counter() - t0) * 1000))
        for d in dichos:
            print(f"  AGENTE  ({d})")
        for o in outs:
            if o["kind"] == "say":
                b.spoken(o["text"])
                oido = S.hablado(o["text"], b.s.lang)
                print(f"  AGENTE  {o['text']}   [{ms[-1]} ms]" + (f"\n          suena: {oido}" if oido != o["text"] else ""))
            if o["kind"] == "end":
                break
        if b.s.ended:
            break
    await b.finalize()
    kinds = [e["kind"] for e in b.s.trace]
    rep_ = b.report() if hasattr(b, "report") else {}
    print(f"  · trato {b.s.trato} · médico de siempre ofrecido: {'usual_doctor_option' in kinds} · preguntó qué cambiar: {'ask_what_to_change' in kinds}"
          f" · acciones {[a.get('action') for a in rep_.get('actions', [])]} · motivo {rep_.get('reason')}")
    return {"guion": nombre, "rep": rep, "ms": ms, "trato": b.s.trato, "kinds": kinds, "transcript": b.s.history, "trace": b.s.trace}


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rep = int(sys.argv[sys.argv.index("--rep") + 1]) if "--rep" in sys.argv else 1
    args = [a for a in args if not a.isdigit()]
    res = []
    for n in args or list(GUIONES):
        for r in range(rep):
            print(f"\n━━ {n}" + (f" ({r + 1}/{rep})" if rep > 1 else "") + f" · {GUIONES[n][2]}")
            try:
                res.append(await una(n, r))
            except Exception as e:  # noqa: BLE001
                print(f"  ERROR {e!r}")
    out = Path(__file__).parent / "calls" / f"guion_{time.strftime('%m%d_%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, default=str))
    print(f"\n→ {out}")


if __name__ == "__main__":
    asyncio.run(main())
