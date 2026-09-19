# Prosper · recepción por voz con Sistema 1 y Sistema 2

Demo funcional del diseño de `../sistema-1-sistema-2.md`: un agente de voz que atiende las llamadas de citas de una clínica.

- **Sistema 1:** Jev interpreta cada fragmento de lo que dice quien llama.
- **La política:** una máquina de estados en código decide qué hace el agente.
- **La voz:** el agente responde con frases de plantilla, sintetizadas y guardadas en caché.
- **Sistema 2:** Gemini Flash-Lite contesta solo lo que se sale del guion, y Jev lo revisa antes de que suene.

Es un proyecto personal, al margen del equipo de HackSpain.

## Cómo arrancarla

```bash
cd demo
TODAY=2026-09-19 ../.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8765
# abrir http://127.0.0.1:8765 → «Llamar»
```

- **Micrófono:** mejor con auriculares, para que el micrófono no oiga al agente. También se puede escribir como si se hablara, con la caja de texto.
- **`TODAY`:** fija el «hoy» de la clínica. Sin ella se usa la fecha real.
- **Claves:** se leen de `~/.claude/.secrets/typesafe.env` (Jev) y `~/.claude/.secrets/gemini.env` (Gemini).
- **Frases fijas:** en el primer arranque se sintetizan las 171 frases fijas de las cinco lenguas (quedan en `cache/tts/`, unos 45 MB). A partir de ahí suenan al instante.

## Pruebas

```bash
../.venv/bin/python sim.py              # 18 escenarios en texto, uno por tipo de problema del reto
../.venv/bin/python sim.py --diez       # diez reservas a la vez, sin huecos repetidos
../.venv/bin/python e2e.py reserva      # voz de extremo a extremo sin micrófono (servidor en marcha)
../.venv/bin/python e2e.py gallego
../.venv/bin/python e2e.py urgencia
```

- **`sim.py`:** usa el mismo código de percepción y política que la voz. Imprime la conversación, los controles antes de escribir, el resultado y la auditoría.
- **`e2e.py`:** sintetiza a quien llama con otra voz, manda el audio a ritmo real y mide cuánto tarda el agente en empezar a hablar desde la última palabra.

## Piezas

| Fichero | Qué hace |
|---|---|
| `clinic.py` | Fichas, médicos, reglas y agenda. Todo el cálculo (edades, fechas, plazos, huecos) está aquí, en código. Escritura en dos fases y huecos apartados mientras se ofrecen. |
| `jev.py` | Cliente de Jev: conexión reutilizada, petición duplicada si tarda más de 450 ms y reintento. |
| `sense.py` | La batería de preguntas a Jev en cada fragmento (qué hace quien llama, si ha terminado, intención, urgencia, manipulación, idioma, para quién, fecha, franja, servicio, médico…). También la lectura de fechas en código. |
| `policy.py` | La máquina de estados: identidad, reglas, ofrecer, elegir, leer la cita, confirmar y escribir; urgencias, manipulación y despedida; informe final con auditoría de Jev. |
| `nlg.py` | Frases en español, catalán, gallego, euskera e inglés; fechas, horas y contracciones en código. |
| `system2.py` | Gemini Flash-Lite para las preguntas fuera de guion, con la batería de Jev que filtra la respuesta antes de que suene. |
| `voice.py` | Oído (`gemini-3.5-transcribe-live`, dos sesiones redundantes), detector de voz y boca (`gemini-3.1-flash-live-preview`) con caché en disco y control de fidelidad. |
| `server.py` | Orquesta cada llamada: turnos de palabra, Jev especulativo, voz preparada de antemano, interrupciones, deshacer y unir, informe. |
| `static/` | El panel: transcripción, Sistema 1 en directo, controles antes de escribir, traza, estado, latencias, informe y agenda. |

## Lo aprendido construyendo la demo

- **Jev no decide el siguiente paso; lo decide el código.** Como selector de pasos falla en todo lo que depende del estado. Como intérprete de quien llama acertó 11 de 11.
- **Las preguntas especulativas van siempre.** Si al anular solo se pregunta por la anulación, «no la anule, cámbiela al jueves» pierde el jueves.
- **Jev lee al pie de la letra.** «La primera» se leía como «a primera hora». Si se está eligiendo entre opciones, se da prioridad a la elección.
- **Los parciales del transcriptor van ~1 s por detrás del audio.** Responder con el parcial al callarse hizo que el agente contestara a «La primera» y tomara «me va bien» como un «sí» a una lectura no oída. Por eso ahora hay tres reglas: responder con el definitivo; deshacer y unir si la persona sigue hablando; y no dejar que un turno empezado antes de la última respuesta la conteste.
- **La transcripción en directo a veces pierde o retrasa un definitivo.** De ahí las dos sesiones redundantes, que gana la primera, y el vigilante que usa el parcial si no llega nada.
- **Sin pista de idioma, el gallego puede salir traducido al español.** Por eso el primer turno se escucha también con pista gallega. Solo se decide gallego si esa sesión lo oye como gallego y su texto difiere del de la otra.
- **La boca (el modelo Live) lee el texto al pie de la letra en las cinco lenguas.** La transcripción de control a veces se corta aunque el audio esté entero, así que la fidelidad también se mide por la duración del audio.
- **Nunca se escribe en la agenda sin un «sí» claro, a una lectura oída y sin correcciones.** Con diez llamadas a la vez, los huecos ofrecidos se apartan un momento para cada llamada.

## Pendiente

- Revisar las frases en euskera con alguien nativo, y las de catalán y gallego con más calma.
- Medir la latencia con una red estable. Estas pruebas se hicieron con pérdida de paquetes, y aun así los turnos normales van entre 0,7 y 1,1 s.
- Adaptar el informe final al esquema exacto del kit del reto cuando se tenga.
- Conectar la telefonía del kit (ahora la entrada es el navegador).
