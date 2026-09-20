"""Lectura determinista de lo que dice quien llama: fecha de nacimiento, teléfono, correo y nombre contra una ficha.
Instantánea (microsegundos) y sin LLM: saca la extracción con Flash-Lite (~800 ms) del camino crítico en los turnos
de identidad y de alta, que hay en todas las llamadas. La extracción sigue en segundo plano como respaldo.

    python leer.py        # pruebas
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))


# ---------------------------------------------------------------- números en palabra (en, es, ca)

UNITS = {
    # inglés
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
    # español
    "cero": 0, "uno": 1, "una": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintiun": 21, "veintidos": 22,
    "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80,
    "noventa": 90,
    # catalán
    "u": 1, "quatre": 4, "cinc": 5, "sis": 6, "set": 7, "vuit": 8, "nou": 9, "deu": 10, "onze": 11, "dotze": 12,
    "tretze": 13, "catorze": 14, "quinze": 15, "setze": 16, "disset": 17, "divuit": 18, "dinou": 19, "vint": 20,
    "trenta": 30, "quaranta": 40, "cinquanta": 50, "seixanta": 60, "setanta": 70, "vuitanta": 80, "noranta": 90,
}
ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9,
       "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16,
       "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20, "thirtieth": 30, "primero": 1, "primer": 1}
MONTHS = {}
for i, names in enumerate([("january", "jan", "enero", "gener"), ("february", "feb", "febrero", "febrer"), ("march", "mar", "marzo", "marc"),
                           ("april", "apr", "abril"), ("may", "mayo", "maig"), ("june", "jun", "junio", "juny"),
                           ("july", "jul", "julio", "juliol"), ("august", "aug", "agosto", "agost"),
                           ("september", "sep", "sept", "septiembre", "setiembre", "setembre"), ("october", "oct", "octubre"),
                           ("november", "nov", "noviembre", "novembre"), ("december", "dec", "diciembre", "desembre")], 1):
    for n in names:
        MONTHS[n] = i
SMALL = {k: v for k, v in UNITS.items() if v < 10}


def words_to_numbers(text: str) -> str:
    """«nineteen eighty-four» → «1984», «the third» → «3», «cuarenta y cuatro» → «44», «seixanta-nou» → «69»."""
    t = fold(text)
    t = re.sub(r"(\d+)(st|nd|rd|th|º|ª|o|a)\b", r"\1", t)
    iso = {}                                                  # una fecha ISO («2017-05-12») no se toca
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", lambda m: iso.setdefault(f"isofecha{chr(97 + len(iso))}", m.group(0)) and f"isofecha{chr(96 + len(iso))}", t)
    t = t.replace("-", " ")
    # el año dicho entero: «mil novecientos ochenta y siete», «dos mil diecisiete», «two thousand and seventeen», «mil nou-cents…»
    t = re.sub(r"\bmil (novecientos|nou cents)\b", "1900", t)
    t = re.sub(r"\b(dos mil|two thousand)( and)?\b", "2000", t)
    toks = re.findall(r"[a-z]+|\d+|[^\sa-z\d]", t)
    out, i = [], 0
    while i < len(toks):
        w = toks[i]
        if w in ORD:
            out.append(str(ORD[w]))
            i += 1
            continue
        if w in ("twenty", "thirty") and i + 1 < len(toks) and toks[i + 1] in ORD:   # twenty-first
            out.append(str(UNITS[w] + ORD[toks[i + 1]]))
            i += 2
            continue
        if w in UNITS and not (w in ("u", "un", "una", "set", "sis", "nou", "oh") and not (i + 1 < len(toks) and (toks[i + 1] in UNITS or toks[i + 1].isdigit()))):
            n = UNITS[w]
            j = i + 1
            if n >= 20 and n % 10 == 0 and j < len(toks):             # forty four / cuarenta y cuatro / quaranta i quatre
                if toks[j] in ("y", "i") and j + 1 < len(toks) and toks[j + 1] in SMALL:
                    n += UNITS[toks[j + 1]]
                    j += 2
                elif toks[j] in SMALL:
                    n += UNITS[toks[j]]
                    j += 1
            out.append(str(n))
            i = j
            continue
        out.append(w)
        i += 1
    s = " ".join(out)
    s = re.sub(r"\s*([/-])\s*", r"\1", s)                    # 12 / 03 / 1984 → 12/03/1984
    # 19 84 → 1984 (año en dos grupos). Si detrás viene otro par, el año es el de la derecha: «october 18 19 93» es el 18 de
    # octubre de 1993, no «1819 93» (así se leía, y la fecha de nacimiento dicha sin comas se descartaba como inventada)
    s = re.sub(r"\b(1[0-9]) ([0-9]{2})\b(?! [0-9]{2}\b)", r"\1\2", s)
    s = re.sub(r"\b(20) (0[0-9]|1[0-9]|2[0-6])\b", r"\1\2", s)  # 20 19 → 2019
    s = re.sub(r"\b(2000|1900) ([0-9]{1,2})\b", lambda m: str(int(m.group(1)) + int(m.group(2))), s)   # two thousand five, mil novecientos 87
    for k, v in iso.items():
        s = s.replace(k, v)
    return s


def _year(y: str) -> int | None:
    n = int(y)
    if len(y) == 4 and 1900 <= n <= 2026:
        return n
    if len(y) <= 2:
        return 2000 + n if n <= 26 else 1900 + n
    return None


def parse_dob(text: str) -> str | None:
    """Fecha de nacimiento dicha de cualquier forma habitual → ISO, o None."""
    t = words_to_numbers(text)
    t = re.sub(r"['’]", " ", t)                               # «d'abril»
    t = re.sub(r"\b(of|de|del|d)\b", " ", t)
    t = re.sub(r"[,.]", " ", t)
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        return _iso(y, mo, d)
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    mon = "|".join(sorted(MONTHS, key=len, reverse=True))
    m = re.search(rf"\b(\d{{1,2}})\s+(?:the\s+)?({mon})\s+(?:in\s+)?(\d{{2,4}})\b", t) or \
        re.search(rf"\b({mon})\s+(?:the\s+)?(\d{{1,2}})\s+(?:in\s+)?(\d{{2,4}})\b", t)
    if m:
        a, b, y = m.groups()
        d, mo = (int(a), MONTHS[b]) if a.isdigit() else (int(b), MONTHS[a])
        return _iso(_year(y), mo, d)
    return None


def dob_dicha(text: str, iso: str) -> bool:
    """¿Están en lo dicho el día, el mes y el año de esta fecha, aunque la persona se haya corregido por el camino?
    «el trece de julio de dos mil veintidós… no, perdón, dos mil veintitrés» dice 2023-07-13, pero la lectura de corrido
    se queda con la primera versión. Aquí basta con que las tres piezas se hayan dicho."""
    try:
        y, mo, d = (int(x) for x in iso.split("-"))
    except Exception:  # noqa: BLE001
        return False
    t = words_to_numbers(text)
    nums = {int(n) for n in re.findall(r"\d+", t)}
    mes = any(re.search(rf"\b{name}\b", t) for name, n in MONTHS.items() if n == mo) or bool(re.search(rf"[/-]0?{mo}[/-]", t))
    return mes and d in nums and (y in nums or y % 100 in nums)


def parse_day(text: str, today: date) -> str | None:
    """Fecha de cita sin año («Monday the 12th of October», «el 3 de octubre», «October 2nd») → la próxima en el
    calendario, en ISO. Solo con mes explícito: «el 3» a secas lo decide la extracción."""
    t = words_to_numbers(text)
    t = re.sub(r"\b(of|de|del|d)\b", " ", t)
    t = re.sub(r"[,.]", " ", t)
    mon = "|".join(sorted(MONTHS, key=len, reverse=True))
    m = re.search(rf"\b(\d{{1,2}})\s+(?:the\s+)?({mon})\b(?!\s+\d{{2,4}})", t) or re.search(rf"\b({mon})\s+(?:the\s+)?(\d{{1,2}})\b(?!\s+\d{{2,4}})", t)
    if not m:
        return None
    a, b = m.groups()
    d, mo = (int(a), MONTHS[b]) if a.isdigit() else (int(b), MONTHS[a])
    for y in (today.year, today.year + 1):
        iso = _iso(y, mo, d)
        if iso and iso >= today.isoformat():
            return iso
    return None


def _iso(y, mo, d):
    try:
        return date(y, mo, d).isoformat() if y else None
    except (ValueError, TypeError):
        return None


DIGIT_WORDS = {k: str(v) for k, v in UNITS.items() if v < 10}
DIGIT_WORDS.update({"for": "4", "to": "2", "too": "2"})


def spoken_digits(text: str) -> str:
    """Todas las cifras del turno, dichas en cifra o en palabra, en orden («six one one, 222 333» → «611222333»)."""
    t = fold(text).replace("-", " ")
    t = re.sub(r"\+34|\b0034\b", " ", t)
    out = []
    toks = re.findall(r"[a-z]+|\d+", t)
    for i, w in enumerate(toks):
        if w.isdigit():
            out.append(w)
        elif w in ("double", "doble") and i + 1 < len(toks) and toks[i + 1] in DIGIT_WORDS:
            out.append(DIGIT_WORDS[toks[i + 1]])            # «double six» → 6 (la otra la añade el siguiente token)
        elif w in ("triple",) and i + 1 < len(toks) and toks[i + 1] in DIGIT_WORDS:
            out.append(DIGIT_WORDS[toks[i + 1]] * 2)
        elif w in DIGIT_WORDS and w not in ("to", "too", "for", "u", "un", "una", "set", "sis", "nou") or \
                (w in ("to", "too", "for", "u", "set", "sis", "nou") and (out or (i + 1 < len(toks) and (toks[i + 1].isdigit() or toks[i + 1] in DIGIT_WORDS)))):
            out.append(DIGIT_WORDS.get(w, ""))
    return "".join(out)


def parse_phone(text: str) -> str | None:
    d = spoken_digits(text)
    m = re.search(r"[6789]\d{8}", d)
    return m.group(0) if m and len(d) <= 12 else None


STOP_BEFORE = {"is", "es", "email", "it", "its", "that", "my", "correo", "address", "adress", "mail", "e", "the"}
TLDS = {"com", "es", "org", "net", "cat", "eu", "co", "uk", "io", "info", "edu", "gob", "fr", "de", "it", "pt"}


def parse_email(text: str) -> str | None:
    """«e l e n a dot castro at gmail dot com» → elena.castro@gmail.com; también si ya viene con @. Si lo dice y
    luego lo deletrea, vale la primera versión completa."""
    t = fold(text).strip()
    m = re.search(r"[a-z0-9_.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", t)
    if m:
        return m.group(0).rstrip(".")
    t = re.sub(r"[’']s\b", " is", t)                          # «it's», «that's»
    t = re.sub(r"\b(dot|punto|punt)\b", " . ", t)
    t = re.sub(r"\b(at|arroba)\b", " @ ", t)
    t = re.sub(r"\b(underscore|guion bajo)\b", " _ ", t)
    toks = re.findall(r"[a-z0-9]+|[.@_]|-", t)
    # letras deletreadas (b-e-a-t-r-i-z, «e, l, e, n, a», «a n d r e i p») → una palabra
    merged: list[str] = []
    for i, w in enumerate(toks):
        if w == "-":
            if merged and len(merged[-1]) >= 1 and merged[-1] not in ".@_" and i + 1 < len(toks) and len(toks[i + 1]) == 1:
                continue                                   # guion entre letras deletreadas
            merged.append("-")
            continue
        if len(w) == 1 and w.isalnum() and merged and merged[-1] not in ".@_-" and (len(merged[-1]) == 1 or getattr(parse_email, "_run", False)):
            merged[-1] += w
            parse_email._run = True
            continue
        parse_email._run = len(w) == 1 and w.isalnum()
        merged.append(w)
    parse_email._run = False
    if "@" not in merged:
        return None
    i = merged.index("@")
    local: list[str] = []
    j = i - 1
    while j >= 0 and merged[j] not in STOP_BEFORE and (merged[j] in "._-" or merged[j].isalnum()):
        local.insert(0, merged[j])
        j -= 1
    dom, k = [], i + 1
    if k < len(merged) and merged[k].isalnum():
        dom.append(merged[k])
        k += 1
        while k + 1 < len(merged) and merged[k] == "." and merged[k + 1].isalnum() and (merged[k + 1] in TLDS or len(merged[k + 1]) <= 3):
            dom += [".", merged[k + 1]]
            k += 2
            if dom[-1] in TLDS and not (k + 1 < len(merged) and merged[k] == "." and merged[k + 1] in TLDS):
                break
    lp = "".join(local).strip("._-")
    if not lp or len(dom) < 3:
        return None
    return f"{lp}@{''.join(dom)}"


def name_score(text: str, full_name: str) -> float:
    """¿Cuánto de la ficha (nombre y apellidos) aparece en lo dicho? Tolerante a errores del transcriptor.
    Es la proporción de palabras de la ficha que tienen una parecida en el texto."""
    import difflib
    tw = re.findall(r"[a-z]+", fold(text))
    nw = [w for w in re.findall(r"[a-z]+", fold(full_name)) if len(w) > 1]
    if not tw or not nw:
        return 0.0
    hit = 0.0
    for w in nw:
        best = max(difflib.SequenceMatcher(None, w, x).ratio() for x in tw)
        hit += 1.0 if best >= 0.85 else (0.5 if best >= 0.7 else 0.0)
    return hit / len(nw)


def _sonido(w: str) -> str:
    """Esqueleto sonoro de una palabra: sin vocales repetidas ni letras mudas, para comparar oídos distintos."""
    w = fold(w)
    w = re.sub(r"h", "", w)
    w = re.sub(r"(ph|f)", "f", w)
    w = re.sub(r"(k|q|c(?![ei]))", "k", w)
    w = re.sub(r"c[ei]", "s", w)
    w = re.sub(r"z", "s", w)
    w = re.sub(r"(v|b)", "b", w)
    w = re.sub(r"(y|j|ll)", "i", w)
    w = re.sub(r"(.)\1+", r"\1", w)
    return w


def sounds_match(said: str, name: str) -> float:
    """Parecido de sonido entre lo que se oyó y un nombre conocido (aseguradora, especialidad, sede).
    «Sunita» → Sanitas 0,73; «a de eslas» → Adeslas. Sirve para no preguntar tres veces lo mismo."""
    import difflib
    a, b = _sonido(said), _sonido(name)
    if not a or not b:
        return 0.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if a.startswith(b[:3]) or b.startswith(a[:3]):
        r = max(r, 0.6 + 0.4 * r)
    return r


def best_match(said: str, options: dict[str, str], floor: float = 0.62) -> tuple[str | None, float]:
    """La opción que mejor suena como lo dicho, comparando también palabra a palabra («tengo Sunita» → sanitas).
    Devuelve (id, parecido) o (None, 0.0) si ninguna llega al suelo o hay empate técnico entre dos."""
    palabras = [w for w in re.findall(r"[a-z]+", fold(said)) if len(w) > 2]
    puntos = []
    for k, v in options.items():
        s = sounds_match(said, v)
        for w in palabras:
            s = max(s, sounds_match(w, v))
        puntos.append((s, k))
    puntos.sort(reverse=True)
    if not puntos or puntos[0][0] < floor:
        return None, 0.0
    if len(puntos) > 1 and puntos[0][0] - puntos[1][0] < 0.06:
        return None, 0.0                # dos suenan igual de bien: que lo pregunte, no que lo invente
    return puntos[1 - 1][1], round(puntos[0][0], 2)


if __name__ == "__main__":
    casos_dob = {
        "Laura Ruiz Gomez born the 14th of September 1978.": "1978-09-14",
        "Antonio Ruiz Medina, born 3rd of May 44": "1944-05-03",
        "born the third of May nineteen forty-four": "1944-05-03",
        "Lucia Fernandez Ortega, born 30th of June 92.": "1992-06-30",
        "The twelfth of April, nineteen eighty-seven.": "1987-04-12",
        "May 3rd, 1944": "1944-05-03",
        "el dos de febrero del sesenta y nueve": "1969-02-02",
        "El dos de febrer del mil nou-cents seixanta-nou.": None,     # «mil nou-cents» no se intenta: lo hará la extracción
        "Jordi Puig Vila, dos de febrer del seixanta-nou.": "1969-02-02",
        "12/03/1984": "1984-03-12",
        "born 10th of April 2019": "2019-04-10",
        "tenth of April twenty nineteen": "2019-04-10",
        "fourth of February, nineteen ninety-three": "1993-02-04",
        "August seventh, nineteen sixty-eight": "1968-08-07",
        "I'd like an appointment on Monday": None,
    }
    ok = 0
    for t, want in casos_dob.items():
        got = parse_dob(t)
        ok += got == want
        print(("✅" if got == want else "❌"), repr(t), "→", got, "" if got == want else f"(esperado {want})")
    for t, want in {"633445566": "633445566", "six one one, two two two, three three three": "611222333",
                    "my phone is 612-345-678": "612345678", "+34 655 66 77 88": "655667788", "six double one two two two three three three": "611222333"}.items():
        got = parse_phone(t)
        ok += got == want
        print(("✅" if got == want else "❌"), repr(t), "→", got)
    for t, want in {"elena dot castro at gmail dot com": "elena.castro@gmail.com", "a n d r e i p, at outlook dot e s.": "andreip@outlook.es",
                    "It's elena.castro@gmail.com": "elena.castro@gmail.com", "e, l, e, n, a, dot castro, at gmail dot com.": "elena.castro@gmail.com",
                    "It is beatriz dot okafor at hotmail dot com. That is b-e-a-t-r-i-z dot o-k-a-f-o-r at hotmail dot com.": "beatriz.okafor@hotmail.com",
                    "My email is tomas dot prieto at hotmail dot com, t-o-m-a-s": "tomas.prieto@hotmail.com",
                    "andrei p at outlook dot es": "andreip@outlook.es", "grace.walsh@gmail.com.": "grace.walsh@gmail.com",
                    "it's laia dot costa at outlook dot co dot uk": "laia.costa@outlook.co.uk"}.items():
        got = parse_email(t)
        ok += got == want
        print(("✅" if got == want else "❌"), repr(t), "→", got)
    print(name_score("Mario Garcia Lopez. My DNI is 39958838.", "Mario García López"), name_score("It's Marta Ruiz Navarro.", "Marta Serra Puig"))
    PLANES = {"sanitas": "Sanitas", "adeslas": "Adeslas", "dkv": "DKV", "axa": "AXA", "caser": "Caser", "asisa": "Asisa", "mapfre": "Mapfre"}
    for t, want in {"Sunita": "sanitas", "a de eslas": "adeslas", "Map free": "mapfre", "Assisa": "asisa",
                    "I have no insurance": None, "Mutua Madrileña": None}.items():
        got = best_match(t, PLANES)[0]
        ok += got == want
        print(("✅" if got == want else "❌"), repr(t), "→", got)
    hoy = date(2026, 9, 19)
    for t, want in {"first thing on Monday the 12th of October": "2026-10-12", "el 3 de octubre por la tarde": "2026-10-03",
                    "October 2nd please": "2026-10-02", "on the 12th": None, "born 3rd of May 1944": None}.items():
        got = parse_day(t, hoy)
        print(("✅" if got == want else "❌"), repr(t), "→", got)
