"""El panel de Prosper desde la terminal: lanzar prácticas (sin puntuar), seguirlas y leer las transcripciones.

Es lo mismo que los botones «Call» de https://hackspain.getprosperapp.com/leaderboard, sin navegador. Una práctica
llama al endpoint que figura en Settings (el túnel del NAS) con un caso publicado y enseña al momento la respuesta
esperada, los campos que fallaron y la transcripción. NUNCA lanza una ronda puntuada: eso se hace a mano en la web.
Guía completa en PANEL.md.

    python3 panel.py problemas                     # los 14 problemas, con su peso (1-5) y sus casos
    python3 panel.py estado                        # endpoint, cola, aciertos y si el NAS corre el último commit
    python3 panel.py lanzar --dificiles --seguir   # todos los casos de los problemas de peso 4, y espera a verlos
    python3 panel.py lanzar difficult_caller adversarial
    python3 panel.py lanzar --caso difficult_caller-ac2ac0d27f0d --seguir
    python3 panel.py seguir RUN_ID [RUN_ID…]       # espera a que acaben y enseña cada caso
    python3 panel.py ver --problema adversarial -n 4   # las últimas prácticas, con transcripción
    python3 panel.py ver RUN_ID --breve            # sin transcripción: solo resultado y campos que fallan
    python3 panel.py cancelar RUN_ID
    python3 panel.py nas --reiniciar               # reinicia el agente del NAS si corre código viejo (entre llamadas)

Credenciales en ~/.claude/.secrets/prosper.env (PROSPER_DASHBOARD_EMAIL, PROSPER_DASHBOARD_PASSWORD). Cada ronda se
guarda entera en agent/calls/panel/<run_id>.json (fuera del repositorio: lleva datos de pacientes simulados).
Solo biblioteca estándar: funciona con cualquier python3, en el Mac o en el NAS.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE = "https://hackspain.getprosperapp.com/leaderboard/api"
SECRETS = Path.home() / ".claude/.secrets/prosper.env"
HERE = Path(__file__).parent
SAVE = HERE / "calls/panel"
FINAL = {"completed", "failed", "cancelled", "voided"}   # estados terminales, los mismos que usa el panel
NAS = "joseluis@100.107.233.6"                           # pop-os por Tailscale
NAS_REPO = "~/Dev/prosper-jev"
HARD_WEIGHT = 4                                          # «difíciles» = peso ≥ 4 (difficult_caller, adversarial)


# ---------------------------------------------------------------- sesión

def _secrets() -> dict:
    if not SECRETS.exists():
        sys.exit(f"Falta {SECRETS} con PROSPER_DASHBOARD_EMAIL y PROSPER_DASHBOARD_PASSWORD.")
    env = {}
    for line in SECRETS.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


class Busy(Exception):
    """409: el equipo ya tiene una práctica en cola o en curso (Prosper admite una sola a la vez)."""


class Panel:
    def __init__(self):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        env = _secrets()
        s = self.req("POST", "/session", {"email": env["PROSPER_DASHBOARD_EMAIL"], "password": env["PROSPER_DASHBOARD_PASSWORD"]})
        self.team = s["viewer"]["team_id"]

    def req(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else (b"{}" if method == "POST" else None)
        r = urllib.request.Request(BASE + path, data=data, method=method, headers={"content-type": "application/json"})
        try:
            for attempt in range(3):   # /teams trae todas las transcripciones y a veces tarda; solo se reintenta lo que se lee
                try:
                    with self.op.open(r, timeout=90) as resp:
                        return json.loads(resp.read() or b"null")
                except (TimeoutError, urllib.error.URLError) as e:
                    if method != "GET" or isinstance(e, urllib.error.HTTPError) or attempt == 2:
                        raise
                    time.sleep(3)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            try:
                j = json.loads(msg)
                msg = j.get("message") or j.get("detail") or msg
            except (ValueError, AttributeError):
                pass
            if e.code == 409:
                raise Busy(msg) from None
            raise SystemExit(f"{method} {path} → {e.code}: {msg}")

    def problems(self) -> list[dict]:
        return self.req("GET", "/problems")["problems"]

    def problem(self, pid: str) -> dict:
        return self.req("GET", f"/problems/{pid}")

    def team_state(self) -> dict:
        return self.req("GET", f"/teams/{self.team}")

    def runs(self) -> list[dict]:
        return self.team_state()["runs"]


# ---------------------------------------------------------------- impresión

def _t(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")


def show_run(run: dict, brief: bool = False) -> None:
    head = f"━━ {run['problem_id']} · {run['state']} · {_t(run['started_at'])} · {run['run_id']}"
    if run.get("voided"):
        head += " · ANULADA"
    print(head)
    if run.get("explanation"):
        print(f"   {run['explanation']}")
    for c in run["cases"]:
        mark = {"passed": "✔", "failed": "✘"}.get(c["status"], "…")
        extra = [c["attribution"]] if c.get("attribution") not in (None, "none") else []
        extra += c.get("signal_codes") or []
        print(f"\n {mark} {c['label']}: {c['status']}" + (f" ({', '.join(extra)})" if extra else ""))
        for f in c.get("fields") or []:
            if not (brief and f["matched"]):
                print(f"   {'  ' if f['matched'] else '≠ '}{f['field']}: esperado {f['expected']!r} · enviado {f['submitted']!r}")
        if not brief:
            print()
            for u in c.get("transcript") or []:
                who = "AGENTE " if u["speaker"] == "Agent" else "LLAMA  "
                print(f"   {u['seconds']:6.1f}s {who}{u['text']}")
    print()


def save(run: dict) -> None:
    SAVE.mkdir(parents=True, exist_ok=True)
    (SAVE / f"{run['run_id']}.json").write_text(json.dumps(run, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- NAS

def nas_check(restart: bool = False, p: Panel | None = None) -> bool:
    """¿El agente del NAS (puerto 7860, el que llama Prosper) corre el último commit? Uvicorn no recarga solo."""
    cmd = (f"cd {NAS_REPO} && git log -1 --format='%h %ct' && "
           "systemctl --user show prosper-agent -p ActiveEnterTimestampMonotonic --value && "
           "systemctl --user show prosper-agent -p ActiveEnterTimestamp --value && cat /proc/uptime")
    try:
        out = subprocess.run(["ssh", "-o", "ConnectTimeout=8", NAS, cmd], capture_output=True, text=True, timeout=30).stdout.split("\n")
        head, commit_ts = out[0].split()
        started_ts = time.time() - float(out[3].split()[0]) + int(out[1]) / 1e6
        started_txt = out[2]
    except Exception as e:  # noqa: BLE001
        print(f"NAS: no se pudo comprobar ({e}).")
        return False
    local = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    fresh = started_ts >= int(commit_ts)
    print(f"NAS: HEAD {head} (local {local}) · agente arrancado {started_txt} · "
          + ("corre el último commit" if fresh else "CORRE CÓDIGO VIEJO: el commit es posterior al arranque"))
    if head != local:
        print("     el NAS no tiene el mismo commit que este repositorio: git pull allí (o push aquí).")
    if not fresh and restart:
        # la cola de prácticas es de todo el equipo y puede no vaciarse nunca: se reinicia en el hueco entre dos
        # llamadas (active_calls == 0 en /health), que es lo que tarda Prosper en marcar la siguiente
        script = ("for i in $(seq 1 600); do curl -s 127.0.0.1:7860/health | grep -q '\"active_calls\":0' && "
                  "systemctl --user restart prosper-agent && exit 0; sleep 1; done; exit 1")
        print("     esperando a que no haya ninguna llamada en curso para reiniciar…")
        if subprocess.run(["ssh", NAS, script], timeout=700).returncode != 0:
            print("     no reinicio: el agente no ha quedado libre en diez minutos.")
            return False
        time.sleep(4)
        print("     reiniciado prosper-agent.")
        return True
    return fresh


# ---------------------------------------------------------------- órdenes

def cmd_problemas(p: Panel, a) -> None:
    for pr in p.problems():
        print(f"{pr['number']:>2} · peso {pr['weight']} · {pr['examples']} casos · {pr['id']:<18} {pr['title']}")
        if a.casos:
            for e in p.problem(pr["id"])["examples"]:
                print(f"       {e['case_id']}  {e['caller']}: {e['summary'][:110]}")


def cmd_estado(p: Panel, a) -> None:
    d = p.team_state()
    st, el, it = d["stats"], d["eligibility"], d["integration"]
    print(f"Equipo {d['name']} · endpoint {it['endpoint']} · última llamada {_t(it['last_call_at']) if it.get('last_call_at') else '—'}")
    print(f"Cola: ronda activa {el['active_run']} · prácticas esperando {el['public_wait']} · puntuadas esperando {el['private_wait']}")
    print(f"Prácticas: {st['cases_passed']}/{st['cases_judged']} casos bien · campos que más fallan: "
          + ", ".join(f"{f['field']} ({f['failures']})" for f in st["field_failures"]))
    for r in d["runs"][:8]:
        cs = " ".join({"passed": "✔", "failed": "✘"}.get(c["status"], "…") for c in r["cases"])
        print(f"   {_t(r['started_at'])} {r['state']:<10} {r['problem_id']:<18} {cs}  {r['run_id']}")
    nas_check()


def cmd_lanzar(p: Panel, a) -> None:
    if a.caso:
        cases = [(c.rsplit("-", 1)[0], c) for c in a.caso]
    else:
        pids = list(a.problemas)
        if a.dificiles:
            pids += [pr["id"] for pr in p.problems() if pr["weight"] >= HARD_WEIGHT and pr["id"] not in pids]
        if not pids:
            sys.exit("¿Qué problemas? Nombra alguno o usa --dificiles.")
        cases = [(pid, e["case_id"]) for pid in pids for e in p.problem(pid)["examples"]]
    if not a.sin_comprobar and not nas_check(restart=a.reiniciar, p=p):
        print("     (lanzo igualmente; con --reiniciar se reinicia antes el agente entre dos llamadas)")
    # Prosper admite UNA práctica por equipo en cola o en curso: se lanzan de una en una, y si otra sesión tiene la
    # suya en marcha se espera turno (409) en vez de fallar
    ids = []
    for i, (pid, cid) in enumerate(cases, 1):
        t0 = time.time()
        while True:
            try:
                r = p.req("POST", f"/problems/{pid}/runs", {"case_id": cid})
                break
            except Busy:
                print(f"   [{int(time.time() - t0)} s] equipo ocupado con otra práctica; espero turno para {cid}", file=sys.stderr, flush=True)
                time.sleep(10)
        ids.append(r["run_id"])
        print(f"[{i}/{len(cases)}] lanzada {cid} → {r['run_id']}", flush=True)
        if a.seguir or i < len(cases):
            follow(p, [r["run_id"]], a.breve, quiet=not a.seguir)
    print(f"\n{len(ids)} prácticas: {' '.join(ids)}")


def follow(p: Panel, ids: list[str], brief: bool, quiet: bool = False) -> None:
    pending, t0 = list(ids), time.time()
    while pending:
        by_id = {r["run_id"]: r for r in p.runs()}
        for rid in list(pending):
            r = by_id.get(rid)
            if r and r["state"] in FINAL:
                save(r)
                if not quiet:
                    show_run(r, brief)
                pending.remove(rid)
        if pending:
            states = Counter(by_id.get(x, {}).get("state", "?") for x in pending)
            print(f"   [{int(time.time() - t0)} s] faltan {len(pending)}: "
                  + ", ".join(f"{k} {v}" for k, v in states.items()), file=sys.stderr, flush=True)
            time.sleep(15)


def cmd_seguir(p: Panel, a) -> None:
    follow(p, a.runs, a.breve)


def cmd_ver(p: Panel, a) -> None:
    runs = p.runs()
    if a.runs:
        runs = [r for r in runs if r["run_id"] in a.runs or r["run_id"][:8] in a.runs]
    else:
        if a.problema:
            runs = [r for r in runs if r["problem_id"] == a.problema]
        runs = runs[: a.n]
    for r in reversed(runs):
        if r["state"] in FINAL:
            save(r)
        show_run(r, a.breve)


def cmd_cancelar(p: Panel, a) -> None:
    for rid in a.runs:
        p.req("POST", f"/runs/{rid}/cancel")
        print(f"cancelada {rid}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Prácticas del panel de Prosper (nunca rondas puntuadas).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("problemas")
    s.add_argument("--casos", action="store_true", help="lista también los casos publicados")
    sub.add_parser("estado")
    s = sub.add_parser("lanzar")
    s.add_argument("problemas", nargs="*", help="ids de problema (p. ej. difficult_caller adversarial)")
    s.add_argument("--dificiles", action="store_true", help=f"añade los problemas de peso ≥ {HARD_WEIGHT}")
    s.add_argument("--caso", nargs="+", help="solo estos case_id")
    s.add_argument("--seguir", action="store_true", help="espera a que acaben y enseña cada caso")
    s.add_argument("--breve", action="store_true", help="sin transcripción")
    s.add_argument("--reiniciar", action="store_true", help="reinicia antes el agente del NAS si corre código viejo")
    s.add_argument("--sin-comprobar", action="store_true", help="no mira el NAS por SSH")
    s = sub.add_parser("seguir")
    s.add_argument("runs", nargs="+")
    s.add_argument("--breve", action="store_true")
    s = sub.add_parser("ver")
    s.add_argument("runs", nargs="*")
    s.add_argument("--problema")
    s.add_argument("-n", type=int, default=5)
    s.add_argument("--breve", action="store_true")
    s = sub.add_parser("cancelar")
    s.add_argument("runs", nargs="+")
    s = sub.add_parser("nas")
    s.add_argument("--reiniciar", action="store_true")
    a = ap.parse_args()
    if a.cmd == "nas":
        nas_check(restart=a.reiniciar)
        return
    p = Panel()
    {"problemas": cmd_problemas, "estado": cmd_estado, "lanzar": cmd_lanzar, "seguir": cmd_seguir,
     "ver": cmd_ver, "cancelar": cmd_cancelar}[a.cmd](p, a)


if __name__ == "__main__":
    main()
