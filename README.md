# prosper-jev

Recepcionista de voz para el reto Prosper de HackSpain 2026, con **Sistema 1** (Jev, de TypeSafe) y **Sistema 2** (Gemini Flash-Lite). Proyecto personal.

- `sistema-1-sistema-2.md`: el diseño y todo lo medido.
- `demo/`: la demo con clínica propia (voz en el navegador, panel, 18 escenarios en texto). Ver `demo/README.md`.
- `agent/`: el agente para la clínica real de Prosper. Protocolo Twilio Media Streams en `/ws`, consola en `/`, cliente de su API, clínica falsa con la misma API y arnés local.
- `evals/`, `*.py` en la raíz: pruebas de latencia y de calidad de Jev, Gemini STT y TTS.

Dos cerebros, y el que se usa es el híbrido de los dos (`AGENT=v2`): el Sistema 2 decide qué hacer, el código
escribe la frase con plantillas y el turno se resuelve sobre el último parcial, antes de que quien llama se calle.
El reparto y todo lo medido están en `sistema-1-sistema-2.md`.

```bash
cd agent
AGENT=v2 ../.venv/bin/python arnes.py --n 40          # arnés combinatorio contra la clínica simulada
AGENT=v2 ../.venv/bin/python replica.py               # los casos publicados de Prosper, sin enviar nada
SPEC=0 AGENT=v2 ../.venv/bin/python arnes.py --n 40   # sin especulación: el coste en serie
PAR=1 ../.venv/bin/python voice_harness.py --caso p1_dni_manana   # una llamada de voz de verdad
```

Las claves se leen de `~/.claude/.secrets/` (`typesafe.env`, `gemini.env`, `prosper.env`) y nunca van al repositorio.
