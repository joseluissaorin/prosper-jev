"""Rondas PUNTUADAS de Prosper, en bucle y sin vigilancia, hasta que se congela el marcador.

El panel no tiene ronda completa: cada ronda puntuada es de un problema y el total del equipo es la suma de la MEJOR
ronda de cada uno. Así que esto va lanzando siempre el problema al que le faltan más puntos, respeta la espera entre
rondas, comprueba que el agente está sano antes de cada una (y lo reinicia si no lo está) y para sola a la hora fijada.

    systemd-run --user --unit=puntuadas python3 /tmp/puntuadas3.py           # hasta las 05:40
    python3 /tmp/puntuadas3.py --hasta 04:30
"""
import json, os, subprocess, sys, time, urllib.request, http.cookiejar
from datetime import datetime, timedelta

env = {l.split('=', 1)[0].strip(): l.split('=', 1)[1].strip()
       for l in open(os.path.expanduser('~/.claude/.secrets/prosper.env')) if '=' in l}
B = "https://hackspain.getprosperapp.com/leaderboard/api"
TEAM = "fb8dea80-bb20-435b-bf4c-06192d3f3cc5"
HASTA = sys.argv[sys.argv.index("--hasta") + 1] if "--hasta" in sys.argv else "05:40"
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def log(m):
    print(f"{datetime.now():%H:%M:%S} {m}", flush=True)


def req(path, body=None, t=180):
    r = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None,
                               method="POST" if body is not None else "GET", headers={"Content-Type": "application/json"})
    for _ in range(4):
        try:
            return json.loads(op.open(r, timeout=t).read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                login()
                continue
            return {"_status": e.code, "_body": e.read().decode()[:200]}
        except Exception:
            time.sleep(5)
    return {}


def login():
    try:
        req("/session", {"email": env["PROSPER_DASHBOARD_EMAIL"], "password": env["PROSPER_DASHBOARD_PASSWORD"]})
    except Exception as e:  # noqa: BLE001
        log(f"login: {e}")


def agente_sano() -> bool:
    try:
        out = subprocess.run(["curl", "-s", "-m", "8", "127.0.0.1:7860/health"], capture_output=True, text=True).stdout
        return '"ok":true' in out
    except Exception:  # noqa: BLE001
        return False


def revivir():
    log("el agente no responde: reiniciando prosper-agent y el túnel")
    subprocess.run(["systemctl", "--user", "restart", "prosper-agent"], check=False)
    time.sleep(15)
    if not agente_sano():
        subprocess.run(["systemctl", "--user", "restart", "prosper-tunnel"], check=False)
        time.sleep(15)


def mejor_por_problema(t):
    best = {}
    for r in t.get("runs", []):
        if r.get("public") or r.get("state") != "completed":
            continue
        pts = sum(c.get("points") or 0 for c in r.get("cases", []))
        best[r.get("problem_id")] = max(best.get(r.get("problem_id"), 0), pts)
    return best


def fin_del_plazo() -> datetime:
    h, m = (int(x) for x in HASTA.split(":"))
    ahora = datetime.now()
    fin = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
    return fin + timedelta(days=1) if fin <= ahora else fin


def main():
    fin = fin_del_plazo()
    log(f"rondas puntuadas hasta las {fin:%H:%M}")
    login()
    while datetime.now() < fin:
        t = req(f"/teams/{TEAM}")
        if not t:
            time.sleep(30)
            continue
        probs = [p for p in req("/problems").get("problems", []) if p.get("weight")]
        best = mejor_por_problema(t)
        # Lo que falta lo dice el PANEL, no una estimación nuestra: Prosper acredita por CASO y se queda con el
        # mejor de cada uno, así que el máximo por ronda se queda corto y hace creer que aún hay puntos donde no
        # los hay. A las 03:05 del 20-09 el lanzador llevaba cinco rondas y cuarenta minutos en `nearest_site`
        # creyendo que faltaban 9, cuando ya estaba 4/4 acreditado: cero puntos, mientras `change_and_cancel` y
        # `no_slot_free` (+8 cada uno) seguían sin lanzarse ni una vez.
        prog = {g["problem_id"]: g for g in t.get("progress", [])}

        def falta(p):
            g = prog.get(p["id"])
            if g and g.get("credited_of"):
                return (g["credited_of"] - g["credited"]) * p["weight"]
            return p["weight"] * 4 - best.get(p["id"], 0)
        pendientes = sorted([p for p in probs if falta(p) > 0], key=lambda p: (-falta(p), -p["weight"]))
        resumen = ", ".join("%s+%d" % (x["id"], falta(x)) for x in pendientes[:6])
        log(f"total {t.get('stats', {}).get('best_points')} · puesto {t.get('stats', {}).get('rank')} · pendientes {resumen}")
        if not pendientes:
            log("todo al máximo; se vuelve a intentar en 10 min por si abren problemas nuevos")
            time.sleep(600)
            continue
        el = t.get("eligibility", {})
        if el.get("active_run"):
            time.sleep(20)
            continue
        w = el.get("private_wait") or 0
        if w:
            time.sleep(min(w, 120) + 3)
            continue
        if not agente_sano():
            revivir()
        p = pendientes[0]
        r = req(f"/problems/{p['id']}/scored-runs", {})
        rid = r.get("run_id") or (r.get("run") or {}).get("run_id")
        if not rid:
            log(f"✗ {p['id']}: no se pudo lanzar ({json.dumps(r)[:120]})")
            time.sleep(60)
            continue
        log(f"→ {p['id']} (peso {p['weight']}, faltan {falta(p):.0f}) {rid[:8]}")
        t0 = time.time()
        while time.time() - t0 < 900:
            time.sleep(20)
            t = req(f"/teams/{TEAM}")
            run = next((x for x in t.get("runs", []) if x["run_id"] == rid), None)
            if run and run["state"] in ("completed", "failed", "cancelled", "voided"):
                pts = sum(c.get("points") or 0 for c in run.get("cases", []))
                ok = sum(1 for c in run.get("cases", []) if c.get("status") == "passed")
                señales = [c.get("signal_codes") for c in run.get("cases", []) if c.get("status") != "passed"]
                log(f"✓ {p['id']:18s} {ok}/{len(run.get('cases', []))} casos · {pts:.0f}/{p['weight'] * 4} puntos · "
                    f"total {t.get('stats', {}).get('best_points')} · puesto {t.get('stats', {}).get('rank')}"
                    + (f" · fallos {señales}" if señales else ""))
                break
    log("== se acabó el plazo")


if __name__ == "__main__":
    main()
