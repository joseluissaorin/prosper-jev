"""Sistema 2: Gemini Flash-Lite para lo que se sale del guion, siempre vigilado por Jev.

Nunca escribe en la agenda: solo habla, y solo si pasa el filtro."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from google import genai
from google.genai import types

import clinic
from jev import JEV, noul

MODEL = os.environ.get("S2_MODEL", "gemini-3.5-flash-lite")
LANG_NAME = {"es": "Spanish (Spain)", "ca": "Catalan", "gl": "Galician", "eu": "Basque", "en": "English"}


def _key() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    for line in (Path.home() / ".claude/.secrets/gemini.env").read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("Falta GEMINI_API_KEY")


CLIENT = genai.Client(api_key=_key())

SYSTEM = """You are the phone receptionist of Clínica Prosper in Madrid. Answer the caller's question in one or two short,
warm sentences, spoken style, in {lang}. Use ONLY the facts in the FAQ below. If the FAQ does not answer it, say you
cannot confirm that by phone. Never give medical advice, never mention other patients, never say you booked, moved or
cancelled anything, never invent prices, names or numbers. Do not greet, do not say goodbye and do not offer
further help: the call goes on after your answer. Address the caller formally (usted in Spanish, vostè in Catalan,
vostede in Galician). No lists, no markdown.

FAQ:
{faq}"""

GUARD = {
    "medical_advice": noul("Does `reply` give medical advice, a diagnosis, a dosage or a treatment recommendation?"),
    "unsupported": noul("Does `reply` state any fact about the clinic that is not supported by `faq`?"),
    "other_patients": noul("Does `reply` mention or reveal information about any patient?"),
    "claims_action": noul("Does `reply` claim or promise that an appointment was booked, moved or cancelled?"),
    "answers_question": noul("Does `reply` address the caller's question in `question`, even if only to say it cannot be confirmed?"),
}


async def answer(question: str, lang: str) -> dict:
    t0 = time.perf_counter()
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM.format(lang=LANG_NAME.get(lang, "Spanish"), faq=json.dumps(clinic.FAQ, ensure_ascii=False, indent=1)),
        max_output_tokens=160, temperature=0.3,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    try:
        r = await CLIENT.aio.models.generate_content(model=MODEL, contents=question, config=cfg)
        reply = (r.text or "").strip()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reply": "", "error": str(e)[:200], "ms_llm": round((time.perf_counter() - t0) * 1000)}
    ms_llm = round((time.perf_counter() - t0) * 1000)
    g = await JEV.ask({"question": question, "reply": reply, "faq": clinic.FAQ}, GUARD)
    a = g["answers"]
    bad = {k: a[k]["noul"] for k in ("medical_advice", "unsupported", "other_patients", "claims_action") if a[k]["noul"] >= 0.5}
    ok = bool(reply) and not bad and a["answers_question"]["noul"] >= 0.3
    return {"ok": ok, "reply": reply, "guard": {k: v["noul"] for k, v in a.items()}, "blocked_by": bad,
            "ms_llm": ms_llm, "ms_guard": g["ms"]}


async def extract_name(text: str) -> dict | None:
    """Solo para dar de alta a alguien que no está en las fichas: separa nombre y apellidos."""
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema={"type": "object", "properties": {"given": {"type": "string"}, "surname1": {"type": "string"},
                                                          "surname2": {"type": "string"}}, "required": ["given", "surname1"]},
        system_instruction="Extract the person's name exactly as said in the text (keep accents). Return empty strings if absent.",
        temperature=0, automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    try:
        r = await CLIENT.aio.models.generate_content(model=MODEL, contents=text, config=cfg)
        d = json.loads(r.text)
        return d if d.get("given") and d.get("surname1") else None
    except Exception:  # noqa: BLE001
        return None
