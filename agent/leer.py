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
    t = t.replace("-", " ")
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
    s = re.sub(r"\b(1[0-9]) ([0-9]{2})\b", r"\1\2", s)          # 19 84 → 1984 (año en dos grupos)
    s = re.sub(r"\b(20) (0[0-9]|1[0-9]|2[0-6])\b", r"\1\2", s)  # 20 19 → 2019
    s = re.sub(r"\b(2000|1900) ([1-9])\b", lambda m: str(int(m.group(1)) + int(m.group(2))), s)   # two thousand five
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


def parse_email(text: str) -> str | None:
    """«e l e n a dot castro at gmail dot com» → elena.castro@gmail.com; también si ya viene con @."""
    t = fold(text).strip().rstrip(".")
    m = re.search(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", t)
    if m:
        return m.group(0)
    t = re.sub(r"\b(dot|punto|punt)\b", " . ", t)
    t = re.sub(r"\b(at|arroba)\b", " @ ", t)
    t = re.sub(r"\b(underscore|guion bajo)\b", " _ ", t)
    t = re.sub(r"\b(dash|hyphen|guion)\b", " - ", t)
    if "@" not in t:
        return None
    left, right = t.split("@", 1)
    right = re.sub(r"\b(it's|its|my|email|is|es|mi|correo)\b", " ", right)
    left = re.sub(r"^.*\b(is|es|email|correo|address)\b", " ", left)
    join = lambda s: re.sub(r"\s+", "", re.sub(r"[^\w.\-_\s]", " ", s))
    lpart, rpart = join(left), join(right.split(" and ")[0])
    if not lpart or "." not in rpart:
        return None
    return f"{lpart}@{rpart}"


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
                    "It's elena.castro@gmail.com": "elena.castro@gmail.com", "e, l, e, n, a, dot castro, at gmail dot com.": "elena.castro@gmail.com"}.items():
        got = parse_email(t)
        ok += got == want
        print(("✅" if got == want else "❌"), repr(t), "→", got)
    print(name_score("Mario Garcia Lopez. My DNI is 39958838.", "Mario García López"), name_score("It's Marta Ruiz Navarro.", "Marta Serra Puig"))
