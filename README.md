# Séneca — recepcionista de voz con Sistema 1 y Sistema 2

**Reto Prosper · HackSpain 2026 · 172 de 172 en el marcador automático.**

Un agente de voz que atiende las llamadas de citas de una clínica: averigua quién llama y qué necesita, busca en las fichas, encuentra disponibilidad real y reserva, mueve o anula. Y cuando no debe reservar —sin cobertura, sin huecos, con reglas que lo prohíben, con una urgencia— lo dice y lo declara. En seis lenguas, con interrupciones, con línea mala y con gente que intenta manipularlo.

Proyecto personal, al margen del equipo de HackSpain. Equipo **Séneca**, sexto por hora de desempate entre los seis equipos con pleno.

> La tesis: una recepcionista trabaja como Kahneman. El 95 % son juicios rápidos —¿me ha dicho que sí?, ¿ha terminado?, ¿esto es una urgencia?, ¿cuál de estas cuatro fichas es?— y solo a veces hay que pararse a pensar. Aquí el LLM es la excepción, no la regla.

---

## Lo que oye el jurado

- **Arranca a hablar en ~0,7 s.** Mediana de 668 ms del fin de voz a la primera palabra, p90 de 1,3 s, con **diez llamadas de voz a la vez** (170 turnos, 20-09-2026).
- **Se le puede interrumpir.** Una negativa, una corrección o un «no», «espere», «wait» cortan la voz en ~300 ms. Un «ajá» no corta. Lo que no llegó a sonar no cuenta como dicho.
- **Cambia de idea sin romperse.** Las ofertas siguen sobre la mesa («mejor la primera que me dijo») y una escritura espera un turno por si llega el «eso no era».
- **No inventa.** Todo hueco sale de la API. Una hora que no sale de ninguna herramienta se tacha antes de sonar (`truth_guard`); un DNI, un teléfono o una fecha que nadie ha dicho no se usan (`*_invented`).
- **Suena como una persona.** «A las cuatro y media de la tarde», no «a las dieciséis treinta». Trato de usted. Castellano, catalán, gallego, euskera, inglés y francés, con cambio de lengua a media llamada.
- **Conoce la ficha antes de preguntar.** Si el número que llama está en una ficha basta el nombre; después, «Gracias, señora Sanz». Su médico de siempre se ofrece, nunca se impone. La cita que ya tiene se nombra antes de dar otra. Las 2.900 notas de trato están cubiertas (`agent/ficha.py` lo comprueba: 2.900 de 2.900).

## El reparto: quién decide qué

```
llamada (Twilio Media Streams, µ-law 8 kHz, tramas de 20 ms)
        │
        ▼
┌── Sistema 1: Jev ──────────────────────────┐
│ cada parcial, una sola petición (~300 ms):  │
│ acepta, rechaza, corrige, da un dato,       │
│ pregunta, muletilla, se despide, idioma,    │
│ urgencia, manipulación, ¿ha terminado?,     │
│ sede y médico emparejados por sonido        │
└──────────────┬─────────────────────────────┘
               ▼
┌── Núcleo determinista en código ────────────┐
│ hechos, permisos, agenda, puerta de escritura│
│ fichas, huecos, reglas, edades, distancias, │
│ motivo que se declara al marcador           │
└──────────────┬─────────────────────────────┘
               ▼
┌── Sistema 2: planificador con herramientas ─┐
│ decide QUÉ hacer, el código dice CÓMO queda │
│ ~1,27 llamadas a Flash-Lite por turno       │
└──────────────┬─────────────────────────────┘
               ▼
   tres carriles, siempre hay algo que decir:
   1. especulación del Sistema 2 (0 ms, ya sintetizada)
   2. núcleo (~1 ms: el «sí», la despedida, la plantilla)
   3. acuse a los 350 ms («Un momento, lo miro», ya grabado)
```

Dos reglas de reparto que dan la precisión:

- **Los hechos y los permisos son del núcleo.** Si el planificador nombra una fecha que nadie dijo, un DNI que en realidad es el teléfono o una regla que ninguna herramienta confirmó, el código lo tira y se lo dice.
- **La redacción y el encadenado son del planificador.** Y si el núcleo tiene una jugada inequívoca, manda el núcleo.

En voz real el cerebro ya casi no cuenta: la especulación se reutiliza en el 64 % de los turnos (mediana de ventaja: 676 ms) y la política baja a 0–5 ms en el carril rápido. Lo que queda no es nuestro: el último parcial del transcriptor (~400 ms) más un Jev (~300 ms). El turno del agente está en ~500 ms; del micrófono al altavoz, en ~0,9–1,1 s. Bajar de ahí pide un transcriptor con parciales más rápidos, no más optimización nuestra.

## Los números

Arnés combinatorio, clínica simulada, misma semilla (202, 40 casos):

| | v1 (estados + Jev) | v2 antes | **v2 ahora (híbrido)** |
|---|---|---|---|
| Acierto | 33/40 | 28/40 | **37/40** |
| Mediana por turno | 357 ms | 1 516 ms | **430 ms** |
| p90 | 838 ms | 2 196 ms | 1 366 ms |
| Llamadas a Flash-Lite por turno | 0 | 2,04 | **1,27** |
| Turnos con especulación aprovechada | — | — | **71 %** |

Cuatro semillas (164 casos): **v1 152/164 (93 %) · v2 146–148/164 (89–90 %)**. En la réplica de los casos reales de Prosper, v2 hacía 72/77 contra 60/77 de v1. El híbrido empata donde perdía por catorce puntos y gana donde ya ganaba.

Réplica local contra la API real sin enviar nada (`agent/replica.py`): **26/26** en los problemas principales en la última pasada.

Coste por llamada (medido en el propio informe, bloque `cost`): una llamada de 63 s sale por **0,21 $ a precio de lista** —voz 0,16 $, planificador 0,036 $, oído 0,005 $, Jev 0,003 $—. Jev cuesta ~0,0015 $ por llamada aunque se lance sobre cada parcial. Orden de magnitud: **céntimos por llamada**, con la voz por delante de los dos modelos de lenguaje.

Rigor: ocho arneses, varianza entre repeticiones con intervalo de Wilson (`--rep N`), **528 llamadas de voz reales** analizadas con sus modos de fallo por nombre. El 35 % de las llamadas tiene al menos una guardia sobre el planificador. Eso es el argumento del diseño: el LLM propone y el código comprueba. Detalle completo en [`docs/rigor.md`](docs/rigor.md) y [`docs/modos-de-fallo.md`](docs/modos-de-fallo.md).

## Seguro por construcción, no por promesas

- **Solo dice frases revisadas**, salvo el Sistema 2, que va filtrado por Jev antes de sonar.
- **Permisos atados a la persona verificada.** No existe herramienta que devuelva datos de otra persona; la manipulación fracasa aunque no se detecte.
- **Escritura en dos fases.** Se aparta el hueco, se lee la cita en voz alta y solo se escribe con un «sí» claro a lo leído, sin correcciones y con la frase terminada. Cada escritura lleva clave anti-duplicados.
- **Las urgencias tienen prioridad sobre todo** y no pasan por el planificador: mensaje del 112 ya grabado en su idioma, sin reservar nada.
- **Si Jev cae, degradación conservadora**: confirmación explícita para todo y constancia en el registro.

## Mapa de la repo

| Ruta | Qué es |
|---|---|
| `sistema-1-sistema-2.md` | El diseño y todo lo medido: latencias de Jev y Gemini, arquitectura, presupuesto de reactividad, los 18 problemas, el híbrido |
| `agent/` | **El agente que llama Prosper.** Twilio Media Streams en `/ws`, consola en `/`, cliente de su API, clínica falsa con la misma API y arneses |
| `agent/conv.py` | El cerebro v2: planificador con herramientas sobre núcleo determinista |
| `agent/brain.py` | El cerebro v1: máquina de estados con Jev de sensor (3 ms de política) |
| `agent/server.py` | Servidor de voz: turnos, especulación, interrupciones, deshacer y unir, informe |
| `agent/panel.py` + `PANEL.md` | Prácticas contra el panel de Prosper (nunca puntuadas desde script) |
| `agent/replica.py` | Los casos publicados de Prosper contra la API real, sin enviar nada |
| `agent/arnes.py` | Arnés combinatorio: casos generados × comportamientos × oído estropeado |
| `agent/voice_harness.py` | 50 llamadas de voz reales, 10 a la vez, con ruido a 5 dB |
| `agent/ficha.py` | Las 2.900 notas de trato convertidas en conducta |
| `demo/` | La demo con clínica propia: voz en el navegador, panel con el Sistema 1 en directo, 18 escenarios en texto. Ver `demo/README.md` |
| `arena/` | La web pública (`https://prosper.joseluissaorin.com`): la llamada como materia en movimiento y un teléfono en el navegador |
| `docs/jurado.md` | Lo que ve el jurado, criterio por criterio, con cifras |
| `docs/rigor.md` · `docs/modos-de-fallo.md` | Cómo sabemos que funciona, cuánto varía, qué falla y qué cuesta |
| `evals/`, `*.py` en raíz | Primeras pruebas de latencia y calidad de Jev, Gemini STT y TTS |
| `ESTADO.md` | Cuaderno de bitácora operativo: servicios en el NAS, rondas puntuadas, defectos vistos en competición |

## Arrancar

Las claves se leen de `~/.claude/.secrets/` (`typesafe.env`, `gemini.env`, `prosper.env`, `elevenlabs.env`, `openrouter.env`) y nunca van al repositorio.

```bash
cd agent
AGENT=v2 ../.venv/bin/python arnes.py --n 40          # arnés combinatorio contra la clínica simulada
AGENT=v2 ../.venv/bin/python replica.py               # los casos publicados de Prosper, sin enviar nada
AGENT=v2 ../.venv/bin/python replica.py --rep 5       # lo mismo, con varianza y Wilson
SPEC=0 AGENT=v2 ../.venv/bin/python arnes.py --n 40   # sin especulación: el coste en serie
PAR=1 ../.venv/bin/python voice_harness.py --caso p1_dni_manana   # una llamada de voz de verdad
PAR=10 ../.venv/bin/python voice_harness.py p1 p5 p13 # doce llamadas con ruido, diez a la vez

python3 panel.py estado                               # endpoint, cola, aciertos, estado del NAS
python3 panel.py lanzar --dificiles --seguir          # prácticas de peso 4, una tras otra

cd ../demo
TODAY=2026-09-19 ../../.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8765
# abrir http://127.0.0.1:8765 → «Llamar»
```

Voz: ElevenLabs (µ-law de 8 kHz directo, primer audio en ~150 ms). Oído: Gemini en directo, con Scribe como alternativa (`EARS=scribe`). Sistema 2: `gpt-oss-120b` en Groq vía OpenRouter (~0,35 s por paso) con Gemini Flash-Lite de respaldo automático (~0,6 s).

## Lo que se enseña en directo

- **Consola en vivo** (`/` del agente): llamadas a la vez, una fila por llamada activa, cada turno con lo que oyó Jev y sus confianzas, cada herramienta con argumentos y milisegundos, salvaguardas resaltadas.
- **«¿Por qué dijo eso?»**: clic en cualquier frase → cadena causal completa (qué oyó, qué juzgó Jev, qué herramientas, qué salvaguarda, qué carril la escribió). Enlace directo: `#llamada=<id>&frase=<n>`.
- **Arena**: `https://prosper.joseluissaorin.com` sirve exactamente lo que hay en `arena/`.
- **Guiones fijos** (`agent/guion.py`): cinco llamadas de guion contra la clínica real, solo lectura, para enseñar el trato por ficha.

## Límites conocidos

Dos defectos vistos en rondas puntuadas, diagnosticados con su traza y pendientes de un rediseño —el arnés de texto no los reproduce, así que cualquier remedio parcial solo se puede medir por lo que estorba:

1. **Reserva doble.** El transcriptor oye «Play That One», Jev lo toma por confirmación y se reserva una cita no aceptada; ante la protesta, se reserva otra en vez de sustituir la primera. Endurecer la puerta reduce el defecto sin subir el acierto. La solución es una escritura en dos fases de verdad —poder sustituir una reserva hecha en la misma llamada—, no un guardia más.
2. **Cierre prematuro.** Tras contestar bien una pregunta («Right, thanks»), el agente cuelga sin ofrecer nada más. La vía es exigir más evidencia de despedida antes de colgar sin gestión, aún sin calibrar.

Detalle completo en `ESTADO.md` y `docs/modos-de-fallo.md`.

---

*Jev interpreta, el código decide, Gemini improvisa cuando nada encaja. La decisión ya está tomada cuando quien llama se calla.*
