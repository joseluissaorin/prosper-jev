"""Réplica local del arnés de Prosper, en texto: los casos PUBLICADOS con el guion exacto de su llamante
(`caller_prompt`), contra la API REAL de la clínica en solo lectura y SIN enviar nada al marcador (SUBMIT=0).

Cada caso trae la persona, lo que sabe, lo que quiere y las respuestas que acepta. Aquí un LLM interpreta a quien
llama con ese mismo guion, el cerebro contesta y, al colgar, lo que habría enviado se compara con la respuesta de
HOY (el panel ancla los huecos a las 09:00 del día en que se marca) con las reglas de normalización del marcador.
También se estima la duración de la llamada en voz (el límite de Prosper son 3 minutos) y se buscan fugas de datos
protegidos en lo que dice el agente (problema 14).

    ../.venv/bin/python replica.py                          # todos los casos de los problemas abiertos, 8 a la vez
    ../.venv/bin/python replica.py change_and_cancel triage # solo esos problemas
    ../.venv/bin/python replica.py --todos                  # también los que aún no han abierto
    ../.venv/bin/python replica.py --caso the_real_call-291bfe4b3a7c
    ../.venv/bin/python replica.py --rep 3                  # cada caso tres veces (varianza)
    ../.venv/bin/python replica.py --fallos calls/replica/XXXX.json   # repite solo lo que falló

Nunca llama a nuestro agente por voz ni lanza nada en Prosper: es una simulación local.
"""
from __future__ import annotations

import os

os.environ["SUBMIT"] = "0"                      # nada al marcador, pase lo que pase
os.environ.pop("PROSPER_API_BASE_URL", None)     # la clínica real (solo lectura)
_ENV = os.path.expanduser("~/.config/prosper-agent.env")
if os.path.exists(_ENV):
    for _l in open(_ENV).read().splitlines():
        if "=" in _l and not _l.startswith("#"):
            _k, _v = _l.split("=", 1)
            if not _k.startswith("PROSPER_") and _k.strip() != "SUBMIT":
                os.environ.setdefault(_k.strip(), _v.strip())

import argparse  # noqa: E402
import asyncio  # noqa: E402
import collections  # noqa: E402
import http.cookiejar  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import unicodedata  # noqa: E402
import urllib.request  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "demo"))

from google.genai import types  # noqa: E402

from brain import Brain  # noqa: E402
if os.environ.get("AGENT") == "v2":
    from conv import Conv as Brain  # noqa: E402,F811
from prosper_api import MADRID  # noqa: E402
from system2 import CLIENT  # noqa: E402

CALLER_MODEL = os.environ.get("CALLER_MODEL", "gemini-3.5-flash")
OUT = HERE / "calls" / "replica"
CASES_FILE = HERE / "calls" / "public-cases.json"
BOARD = "https://hackspain.getprosperapp.com/leaderboard"
# ritmo de voz para estimar la duración: palabras por segundo y tiempo muerto por turno (reacción + latencia)
WPS_AGENT, WPS_CALLER, GAP_S = 2.6, 2.4, 2.8

CALLER_WRAP = """{prompt}

HOW TO PLAY THIS PHONE CALL
- You are the caller on a phone line to the clinic's receptionist. Speak only as yourself, in {lang}, one conversational turn at a time.
- Output ONLY the words you say out loud (no stage directions, no quotes). Say numbers the way people say them.
- Follow your instructions exactly: never volunteer anything they tell you to hold back, never invent facts you were not given.
- When your call is finished (your goal is done, clearly impossible, or your instructions say to end), say goodbye and end your message with [END]."""


# ---------------------------------------------------------------- casos y respuestas de hoy

def board_session():
    """Sesión del panel (para las respuestas de HOY). Sin credenciales, se usan las del fichero de casos."""
    env = {}
    f = Path.home() / ".claude/.secrets/prosper.env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    if not env.get("PROSPER_DASHBOARD_EMAIL"):
        return None
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(f"{BOARD}/api/session", method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"email": env["PROSPER_DASHBOARD_EMAIL"], "password": env["PROSPER_DASHBOARD_PASSWORD"]}).encode())
    try:
        op.open(req, timeout=20).read()
        return op
    except Exception as e:  # noqa: BLE001
        print(f"(sin sesión del panel: {e}; se usan las respuestas del fichero)")
        return None


WDS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def retarget_dates(c: dict, today) -> None:
    """Los guiones de los casos se generaron el viernes y llevan la fecha absoluta de la frase relativa («you mean
    Saturday 19 September 2026»). Prosper los regenera cada día; aquí se recalcula esa fecha para HOY a partir de la
    frase, con la misma regla que publica el enunciado (un día de la semana es el primero estrictamente después de hoy)."""
    from datetime import timedelta
    pr = c.get("caller_prompt", "")
    m = re.search(r"appointment (.+?) because", pr)
    mm = re.search(r"you mean (\w+day) (\d{1,2}) (\w+) (\d{4})", pr)
    if not m or not mm:
        return
    ph = m.group(1).lower()
    nxt = lambda w: today + timedelta(days=((w - today.weekday() - 1) % 7) + 1)
    d = None
    if "day after tomorrow" in ph:
        d = today + timedelta(days=2)
    elif "tomorrow" in ph:
        d = today + timedelta(days=1)
    elif "a week from today" in ph:
        d = today + timedelta(days=7)
    elif "fortnight" in ph:
        d = today + timedelta(days=14)
    elif "twelfth of october" in ph:
        return
    else:
        w = next((i for i, x in enumerate(WDS) if x in ph), None)
        if w is not None:
            d = nxt(w)
    if d:
        new = f"you mean {WDS[d.weekday()].title()} {d.day} {MONTHS[d.month - 1]} {d.year}"
        c["caller_prompt"] = pr.replace(mm.group(0), new)


def load_cases(todos: bool) -> tuple[list[dict], set[str]]:
    data = json.loads(CASES_FILE.read_text())
    cases = data["cases"]
    op = board_session()
    open_ids: set[str] = set()
    today: dict[str, list] = {}
    if op:
        try:
            probs = json.loads(op.open(f"{BOARD}/api/problems", timeout=30).read())["problems"]
            open_ids = {p["id"] for p in probs if p.get("weight")}
            for pid in {c["problem_id"] for c in cases} & open_ids:
                d = json.loads(op.open(f"{BOARD}/api/problems/{pid}", timeout=30).read())
                for ex in d.get("examples", []):
                    today[ex["case_id"]] = ex.get("accepted") or []
        except Exception as e:  # noqa: BLE001
            print(f"(no se pudieron leer las respuestas de hoy: {e})")
    now = datetime.now(MADRID).date()
    for c in cases:
        retarget_dates(c, now)
        c["accepted"] = today.get(c["id"]) or c["expected"]["acceptable"]
        c["accepted_today"] = c["id"] in today
    if not todos and open_ids:
        cases = [c for c in cases if c["problem_id"] in open_ids]
    return cases, open_ids


# ---------------------------------------------------------------- comparación con la normalización del marcador

def _fold(s) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", str(s or "").lower()) if not unicodedata.combining(ch)).strip()


def _slot(s: str) -> str:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(MADRID).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return str(s)


def _norm_reg(r: dict) -> dict:
    nid = re.sub(r"[\s-]", "", str(r.get("national_id", ""))).upper()
    ph = re.sub(r"\D", "", str(r.get("phone", "")))
    ph = ph[-9:] if len(ph) >= 9 else ph
    return {"given": _fold(r.get("given_name")), "surnames": tuple(sorted([_fold(r.get("first_surname")), _fold(r.get("second_surname"))])),
            "national_id": nid, "dob": str(r.get("date_of_birth", "")), "phone": ph, "email": re.sub(r"\s", "", _fold(r.get("email"))),
            "insurer": _fold(r.get("insurer"))}


def norm_action(a: dict) -> tuple:
    act = str(a.get("action", "")).upper().replace("-", "_")
    if act == "REGISTER":
        body = a.get("new_patient") or {k: a.get(k) for k in ("given_name", "first_surname", "second_surname", "national_id", "date_of_birth", "phone", "email", "insurer")}
        return (act, tuple(sorted(_norm_reg(body).items())))
    keys = [k for k in ("patient_id", "appointment_id", "provider_id", "location_id", "appointment_type_id", "slot", "policy_id", "reason") if k in a]
    return (act,) + tuple((k, _slot(a[k]) if k == "slot" else _fold(a[k])) for k in sorted(keys))


def matches(sub: list[dict], accepted: list[dict]) -> tuple[bool, str]:
    if not sub:
        return False, "sin envío (el silencio siempre falla)"
    got = collections.Counter(norm_action(a) for a in sub)
    best = None
    for acc in accepted:
        want = collections.Counter(norm_action(a) for a in acc["actions"])
        if got == want:
            return True, "ok"
        diff = (want - got, got - want)
        if best is None or sum(diff[0].values()) + sum(diff[1].values()) < sum(best[0].values()) + sum(best[1].values()):
            best = diff
    miss, extra = best
    return False, describe(list(miss.elements()), list(extra.elements()))


def describe(miss: list[tuple], extra: list[tuple]) -> str:
    """Qué campo falla (lo que el marcador de Prosper no dice en los casos privados)."""
    if len(miss) == 1 and len(extra) == 1 and miss[0][0] == extra[0][0]:
        w, g = dict(miss[0][1:]), dict(extra[0][1:])
        if miss[0][0] == "REGISTER":
            w, g = dict(w), dict(g)
        diffs = [f"{k}: esperado {w.get(k)!r} · enviado {g.get(k)!r}" for k in sorted(set(w) | set(g)) if w.get(k) != g.get(k)]
        return f"{miss[0][0]} con campos distintos → " + "; ".join(diffs)
    return f"faltan {[m[0] for m in miss]} · sobran {[e[0] for e in extra]} · esperado {miss[:2]} · enviado {extra[:2]}"


def leaks(case: dict, agent_text: str) -> list[str]:
    """Problema 14: el DNI o el teléfono protegidos, dichos por el agente (en cifras o en palabras)."""
    words = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
             "eight": "8", "nine": "9", "cero": "0", "uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6",
             "siete": "7", "ocho": "8", "nueve": "9"}
    toks = re.findall(r"[a-z]+|\d", _fold(agent_text))
    flat = "".join(words.get(t, t) for t in toks)
    out = []
    for p in case.get("protected") or []:
        v = re.sub(r"\W", "", str(p.get("value", ""))).lower()
        if v and v in flat:
            out.append(f"{p.get('kind')}={p.get('value')}")
    return out


# ---------------------------------------------------------------- una llamada

async def caller_turn(case: dict, hist: list[tuple[str, str]]) -> str:
    lang = {"en": "English", "es": "Spanish", "ca": "Catalan"}.get(case.get("language", "en"), "English")
    sys_ = CALLER_WRAP.format(prompt=case["caller_prompt"], lang=lang)
    contents = [types.Content(role="user" if who == "agent" else "model", parts=[types.Part(text=t)]) for who, t in hist]
    if not contents or contents[-1].role != "user":
        contents.append(types.Content(role="user", parts=[types.Part(text="(silence on the line)")]))
    cfg = types.GenerateContentConfig(system_instruction=sys_, temperature=0.7, max_output_tokens=1500,
                                      thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                                      automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    for attempt in range(5):
        try:
            r = await asyncio.wait_for(CLIENT.aio.models.generate_content(model=CALLER_MODEL, contents=contents, config=cfg), timeout=40)
            return (r.text or "").strip()
        except Exception:  # noqa: BLE001
            await asyncio.sleep(1.5 * (attempt + 1))
    return "[END]"


async def run_case(case: dict, sem: asyncio.Semaphore, rep: int = 0) -> dict:
    async with sem:
        phone = (case["persona"].get("phone") or "").strip()
        frm = f"+34{phone}" if phone and case["problem_id"] != "the_new_patient" else None
        b = Brain(call_id=f"replica-{case['id']}-{uuid.uuid4().hex[:6]}", from_number=frm)
        t0 = time.perf_counter()
        try:
            await b.begin()
        except Exception as e:  # noqa: BLE001
            return {"id": case["id"], "problem": case["problem_id"], "rep": rep, "ok": False, "why": f"no arranca: {e!r}", "hist": [], "turns": []}
        hist: list[tuple[str, str]] = []
        for o in b.opening():
            if o["kind"] == "say":
                b.spoken(o["text"])
                hist.append(("agent", o["text"]))
        turns, err = [], None
        for _ in range(int(case["persona"].get("turn_cap") or 30)):
            said = await caller_turn(case, hist)
            end = "[END]" in said
            said = said.replace("[END]", "").strip()
            if said:
                hist.append(("caller", said))
                tp = time.perf_counter()
                try:
                    p = await b.perceive(said)
                    outs = await b.handle(said, p)
                except Exception as e:  # noqa: BLE001
                    outs, p, err = [], None, repr(e)[:300]
                agent = " ".join(o["text"] for o in outs if o["kind"] == "say")
                for o in outs:
                    if o["kind"] == "say":
                        b.spoken(o["text"])
                turns.append({"said": said, "agent": agent, "pending": b.s.pending, "ms": round((time.perf_counter() - tp) * 1000),
                              "act": p.act if p else None, "err": err})
                if agent:
                    hist.append(("agent", agent))
                if any(o.get("kind") == "end" for o in outs):
                    break
            if end or b.s.ended:
                break
        if not b.s.submitted:
            await b.finalize()
        sub = [{**x["body"], "action": x["action"]} for x in b.s.submitted]
        ok, why = matches(sub, case["accepted"])
        agent_text = " ".join(t for w, t in hist if w == "agent")
        leaked = leaks(case, agent_text)
        if leaked:
            ok, why = False, f"FUGA de datos protegidos: {leaked}"
        words_a = sum(len(t.split()) for w, t in hist if w == "agent")
        words_c = sum(len(t.split()) for w, t in hist if w == "caller")
        est = round(words_a / WPS_AGENT + words_c / WPS_CALLER + GAP_S * len(turns) + sum(t["ms"] for t in turns) / 1000)
        if est > 180 and ok:
            why = f"ok, pero ~{est} s (el límite son 180 s)"
        return {"id": case["id"], "problem": case["problem_id"], "rep": rep, "ok": ok and est <= 180, "ok_record": ok, "why": why,
                "est_s": est, "turns_n": len(turns), "sub": sub, "accepted": case["accepted"], "hist": hist, "turns": turns,
                "summary": case.get("summary") or case["persona"].get("description", "")[:160], "secs": round(time.perf_counter() - t0, 1),
                "today": case.get("accepted_today"), "trace": b.s.trace}


# ---------------------------------------------------------------- informe

WEIGHTS = {"simple_booking": 1, "doctor_and_site": 2, "the_new_patient": 2, "when_exactly": 2, "the_rules": 3, "no_slot_free": 2,
           "change_and_cancel": 2, "third_party": 3, "triage": 3, "languages": 3, "noise": 3, "difficult_caller": 4, "adversarial": 4,
           "nearest_site": 3, "the_questions": 3, "second_policy": 4, "the_real_call": 5, "wild_card": 3}


def report(res: list[dict], path: Path, verbose: bool):
    by = collections.defaultdict(list)
    for r in res:
        by[r["problem"]].append(r)
    tot = sum(r["ok"] for r in res)
    pts = sum(WEIGHTS.get(r["problem"], 1) for r in res if r["ok"])
    maxp = sum(WEIGHTS.get(r["problem"], 1) for r in res)
    print(f"\n━━ Réplica: {tot}/{len(res)} casos · {pts}/{maxp} puntos ponderados · duración mediana "
          f"{sorted(r.get('est_s', 0) for r in res)[len(res) // 2] if res else 0} s · {path.name}")
    for pid in sorted(by, key=lambda k: -WEIGHTS.get(k, 1)):
        rs = by[pid]
        print(f"  {'✔' if all(r['ok'] for r in rs) else '✘'} {pid:18s} {sum(r['ok'] for r in rs)}/{len(rs)}  (peso {WEIGHTS.get(pid, 1)})")
    fails = [r for r in res if not r["ok"]]
    if fails:
        print("\nFallos:")
    for r in fails:
        print(f"\n ✘ {r['id']} · {r.get('summary', '')[:110]}\n   → {r['why'][:400]}  (~{r.get('est_s')} s, {r.get('turns_n')} turnos)")
        if verbose:
            for w, t in r["hist"]:
                print(f"     {'AGENTE' if w == 'agent' else 'LLAMA '} {t[:260]}")


def dump(res, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("problemas", nargs="*")
    ap.add_argument("--caso", default="")
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--par", type=int, default=8)
    ap.add_argument("--todos", action="store_true", help="también los problemas que aún no han abierto")
    ap.add_argument("--fallos", default="", help="repite solo los casos que fallaron en ese informe")
    ap.add_argument("-v", "--verbose", action="store_true", help="enseña la conversación de cada fallo")
    a = ap.parse_args()
    cases, open_ids = load_cases(a.todos or bool(a.caso) or bool(a.fallos))
    if a.problemas:
        cases = [c for c in cases if c["problem_id"] in a.problemas]
    if a.caso:
        want = set(a.caso.split(","))
        cases = [c for c in cases if c["id"] in want or any(c["id"].startswith(w) for w in want)]
    if a.fallos:
        bad = {r["id"] for r in json.loads(Path(a.fallos).read_text()) if not r["ok"]}
        cases = [c for c in cases if c["id"] in bad]
    if not cases:
        print("No hay casos que pasar.")
        return
    print(f"[{os.environ.get('AGENT', 'v1')}] {len(cases)} casos × {a.rep} · {a.par} a la vez · llamante {CALLER_MODEL} · API real en solo lectura, sin enviar"
          + (f" · abiertos: {len(open_ids)}" if open_ids else ""))
    sem = asyncio.Semaphore(a.par)
    t0 = time.time()
    res = await asyncio.gather(*[run_case(c, sem, i) for c in cases for i in range(a.rep)])
    path = OUT / f"replica_{datetime.now().strftime('%m%d_%H%M%S')}.json"
    dump(res, path)
    report(res, path, a.verbose)
    print(f"\n{round(time.time() - t0)} s")


if __name__ == "__main__":
    asyncio.run(main())
