"""Estadística mínima para los arneses: cuánto de un resultado es el sistema y cuánto es la suerte de esa tirada.

El planificador es un modelo de lenguaje a temperatura 0,2 y Jev devuelve probabilidades: el mismo caso no sale igual
dos veces. Un «17 de 20» de una sola tirada no dice si el caso que falla falla siempre (un defecto) o una de cada
cinco (varianza). Con `--rep N` los arneses repiten la batería entera y aquí se agrega como se debe:

- por caso, k/N: cuántas de las N repeticiones pasó;
- la tasa global con su intervalo de Wilson al 95 % (no el normal: con pocas pruebas y tasas cerca de 0 o de 1 el
  normal se sale de [0, 1] y se queda corto);
- los casos INESTABLES (0 < k < N) aparte de los que fallan SIEMPRE (k = 0): se arreglan de forma distinta;
- la tasa de cada repetición y su recorrido (mínimo–máximo): la varianza entre tiradas, a la vista;
- latencia por turno: mediana y p90 de todo, y la mediana de cada repetición con su recorrido.

Sin dependencias: solo la biblioteca estándar.
"""
from __future__ import annotations

import math
from collections import defaultdict

Z95 = 1.959964


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Intervalo de Wilson para k aciertos de n. Sin datos, (0, 1): no se sabe nada."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    lo, hi = max(0.0, c - h), min(1.0, c + h)
    return (0.0 if k == 0 else lo), (1.0 if k == n else hi)


def percentil(xs, q: float):
    """Percentil q (0-100) con interpolación lineal; None si no hay datos."""
    v = sorted(x for x in xs if x is not None)
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    pos = (len(v) - 1) * q / 100
    i = int(math.floor(pos))
    j = min(i + 1, len(v) - 1)
    return v[i] + (v[j] - v[i]) * (pos - i)


def mediana(xs):
    return percentil(xs, 50)


def resumen_ms(xs) -> dict:
    v = [x for x in xs if x is not None]
    return {"n": len(v), "p50": percentil(v, 50), "p90": percentil(v, 90), "p99": percentil(v, 99), "max": max(v) if v else None}


def pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.1f} %".replace(".", ",")


def ms(x) -> str:
    return "—" if x is None else f"{x:.0f} ms"


class Repeticiones:
    """Acumula los resultados de N repeticiones de una misma batería y los resume."""

    def __init__(self) -> None:
        self.casos: dict = defaultdict(dict)          # nombre → {rep: ok}
        self.lat: dict = defaultdict(list)            # rep → [ms por turno]

    def caso(self, nombre: str, rep: int, ok: bool) -> None:
        self.casos[str(nombre)][rep] = bool(ok)

    def latencias(self, rep: int, valores) -> None:
        self.lat[rep] += [float(x) for x in valores if x is not None]

    def resumen(self) -> dict:
        reps = sorted({r for d in self.casos.values() for r in d} | set(self.lat))
        ensayos = sum(len(d) for d in self.casos.values())
        aciertos = sum(sum(d.values()) for d in self.casos.values())
        por_caso = [(n, sum(d.values()), len(d)) for n, d in self.casos.items()]
        por_rep = []
        for r in reps:
            oks = [d[r] for d in self.casos.values() if r in d]
            if oks:
                por_rep.append(sum(oks) / len(oks))
        todas = [x for r in reps for x in self.lat.get(r, [])]
        med = [mediana(self.lat[r]) for r in reps if self.lat.get(r)]
        p90 = [percentil(self.lat[r], 90) for r in reps if self.lat.get(r)]
        return {"n_reps": len(reps), "casos": len(self.casos), "ensayos": ensayos, "aciertos": aciertos,
                "tasa": aciertos / ensayos if ensayos else None, "wilson": wilson(aciertos, ensayos),
                "por_caso": por_caso, "por_rep": por_rep,
                "inestables": sorted([x for x in por_caso if 0 < x[1] < x[2]], key=lambda x: x[1] / x[2]),
                "siempre_fallan": [x for x in por_caso if x[1] == 0 and x[2] > 0],
                "siempre_pasan": sum(1 for x in por_caso if x[1] == x[2] and x[2] > 0),
                "latencia": {"n": len(todas), "p50": percentil(todas, 50), "p90": percentil(todas, 90),
                             "medianas_por_rep": med, "p90_por_rep": p90}}

    def informe(self, titulo: str = "VARIANZA ENTRE REPETICIONES") -> list[str]:
        g = self.resumen()
        if not g["ensayos"]:
            return []
        lo, hi = g["wilson"]
        out = ["", f"── {titulo} " + "─" * max(4, 96 - len(titulo)),
               f"  {g['casos']} casos × {g['n_reps']} repeticiones = {g['ensayos']} ensayos · pasan {g['aciertos']} "
               f"({pct(g['tasa'])}) · Wilson 95 %: [{pct(lo)}, {pct(hi)}]"]
        if g["n_reps"] > 1:
            out.append("  (Wilson trata los ensayos como independientes; las repeticiones de un mismo caso no lo son del todo: "
                       "el intervalo real es algo más ancho)")
        if len(g["por_rep"]) > 1:
            pr = g["por_rep"]
            media = sum(pr) / len(pr)
            desv = math.sqrt(sum((x - media) ** 2 for x in pr) / (len(pr) - 1))
            out.append("  por repetición: " + " · ".join(pct(x) for x in pr)
                       + f"   (mín. {pct(min(pr))}, máx. {pct(max(pr))}, desv. típica " + f"{100 * desv:.1f}".replace(".", ",") + " puntos)")
        else:
            out.append("  una sola repetición: no hay varianza que medir; use --rep N (N ≥ 3) para separar los casos inestables")
        out.append(f"  siempre pasan: {g['siempre_pasan']} · INESTABLES (0 < k < N): {len(g['inestables'])} · "
                   f"siempre fallan: {len(g['siempre_fallan'])}")
        if g["inestables"]:
            out.append("  INESTABLES (varianza: a veces sí, a veces no):")
            out += [f"    {k}/{n}  {nombre}" for nombre, k, n in g["inestables"]]
        if g["siempre_fallan"]:
            out.append("  SIEMPRE FALLAN (defecto reproducible):")
            out += [f"    {k}/{n}  {nombre}" for nombre, k, n in g["siempre_fallan"]]
        L = g["latencia"]
        if L["n"]:
            out.append(f"  latencia por turno (n={L['n']}): mediana {ms(L['p50'])} · p90 {ms(L['p90'])}")
            if len(L["medianas_por_rep"]) > 1:
                m, p = L["medianas_por_rep"], L["p90_por_rep"]
                out.append(f"    mediana de cada repetición: {' · '.join(ms(x) for x in m)}   (recorrido {ms(min(m))}–{ms(max(m))})")
                out.append(f"    p90 de cada repetición:     {' · '.join(ms(x) for x in p)}   (recorrido {ms(min(p))}–{ms(max(p))})")
        return out
