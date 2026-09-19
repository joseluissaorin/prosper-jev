# prosper-jev

Recepcionista de voz para el reto Prosper de HackSpain 2026, con **Sistema 1** (Jev, de TypeSafe) y **Sistema 2** (Gemini Flash-Lite). Proyecto personal.

- `sistema-1-sistema-2.md`: el diseño y todo lo medido.
- `demo/`: la demo con clínica propia (voz en el navegador, panel, 18 escenarios en texto). Ver `demo/README.md`.
- `agent/`: el agente para la clínica real de Prosper. Protocolo Twilio Media Streams en `/ws`, consola en `/`, cliente de su API, clínica falsa con la misma API y arnés local.
- `evals/`, `*.py` en la raíz: pruebas de latencia y de calidad de Jev, Gemini STT y TTS.

Las claves se leen de `~/.claude/.secrets/` (`typesafe.env`, `gemini.env`, `prosper.env`) y nunca van al repositorio.
