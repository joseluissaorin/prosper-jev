# Rigor: cómo sabemos que funciona, cuánto varía, qué falla y qué cuesta

Todas las cifras de este documento salen de `agent/modos.py` sobre **N = 528 llamadas de voz reales del cerebro v2** (informes de `agent/calls/` copiados del servidor; ficheros fechados entre el 19-09-2026 21:36 y el 20-09-2026 08:04). Son llamadas de todo el desarrollo (arnés de voz de Prosper, rondas de práctica y pruebas propias) con versiones distintas del agente: describen el periodo, no la versión de hoy. La tabla completa está en [`modos-de-fallo.md`](modos-de-fallo.md) y se regenera con:

```
python agent/modos.py <carpeta de informes> --brain v2 --md > docs/modos-de-fallo.md
```

Donde una cifra no es una medida sino una estimación, se dice.

## 1. Los arneses: qué mide cada uno

| Script | Qué mide | Contra qué | Repeticiones |
|---|---|---|---|
| `agent/replica.py` | Los casos PUBLICADOS por Prosper con el guion exacto de su llamante (un LLM lo interpreta). Compara lo que el agente habría enviado con la respuesta aceptada de hoy, con las reglas de normalización del marcador; estima la duración en voz (límite: 180 s) y busca fugas de datos protegidos. | API real de la clínica, solo lectura, sin enviar (`SUBMIT=0`) | `--rep N` |
| `agent/eval.py` | Casos propios con la respuesta esperada calculada por un oráculo independiente del agente (la agenda de la clínica simulada). Latencia por turno desglosada. | Clínica simulada (`agent/fake_api.py`) | `--rep N` |
| `agent/arnes.py` | Arnés combinatorio: casos GENERADOS (paciente × especialidad × médico × sede × fecha × seguro × modo de identificarse) × una matriz de comportamientos del llamante × un canal de oído que estropea el texto como el transcriptor. Invariantes en cada turno y fallos agrupados por (invariante × estado × acto). | Clínica simulada | `--rep N` (nuevo) y `--seed` |
| `agent/voice_harness.py` + `agent/voice_cases.py` | 50 llamadas de VOZ por el protocolo de Twilio Media Streams, 10 a la vez, con voces pregrabadas y ruido a 5 dB. | Servidor de voz real + clínica simulada | no |
| `agent/voz_test.py` | Una llamada de voz con guion; mide, turno a turno, el tiempo desde que quien llama calla hasta que el agente empieza a hablar; guarda el audio. | Servidor de voz | `--rep N` |
| `agent/harness.py` | Imitación local del arnés de Prosper (µ-law de 8 kHz, tramas de 20 ms a ritmo real). | Servidor de voz | no |
| `agent/modos.py` | Taxonomía de modos de fallo, latencias y gasto a partir de los informes de llamadas reales. Sin red. | Informes JSON | no aplica |
| `agent/pruebas_rigor.py` | Comprobaciones sin red de la propia instrumentación: coste por llamada aislado entre llamadas concurrentes, cortacircuitos, Wilson y percentiles contra valores conocidos. | Dobles locales | no aplica |

Los arneses de texto gastan créditos de Jev y del planificador; los de voz, además, de voz y oído. Ninguno de ellos se ha ejecutado para escribir este documento.

## 2. Varianza entre repeticiones

El planificador es un LLM a temperatura 0,2 y Jev devuelve probabilidades: el mismo caso no sale igual dos veces. Un «17 de 20» de una tirada no distingue un defecto (falla siempre) de la varianza (falla una de cada cinco). Antes, `--rep N` aplanaba las repeticiones en un total. Ahora `agent/estadistica.py` agrega, y los tres arneses de texto imprimen al final:

- por caso, **k/N**;
- la tasa global con su **intervalo de Wilson al 95 %** (no el normal, que con pocas pruebas y tasas cerca de 0 o 1 se sale de [0, 1]);
- los casos **INESTABLES** (0 < k < N) aparte de los que **fallan SIEMPRE** (k = 0): unos piden bajar la varianza (una guardia, una plantilla), los otros son un defecto reproducible;
- la tasa de cada repetición, su mínimo, su máximo y su desviación típica;
- la **latencia por turno**: mediana y p90 globales, y la mediana y el p90 de cada repetición con su recorrido.

Advertencia que el propio informe imprime: Wilson trata los ensayos como independientes, y las repeticiones de un mismo caso no lo son del todo; el intervalo real es algo más ancho.

Comprobado sin red (`agent/pruebas_rigor.py`): Wilson para 8/10 da [49,0 %, 94,3 %], para 0/10 da [0, 27,8 %] y para 10/10 da [72,2 %, 100 %], los valores de referencia. **Todavía no hay una tirada `--rep N` hecha con este agregado**: cuesta créditos y no se ha lanzado. El comando es `AGENT=v2 python agent/replica.py --rep 5`.

## 3. Los modos de fallo que sabemos nombrar

Cada guardia del código deja un evento con nombre en la traza de la llamada. Un evento de guardia **no es una llamada fallida**: es un defecto del modelo, del oído o de la conversación que el código vio y paró. En 528 llamadas (tasa = llamadas afectadas por cada 100; entre paréntesis, Wilson 95 %):

| Familia | Modo de fallo | Evento | Llamadas | Por 100 |
|---|---|---|---|---|
| Oído | El transcriptor destroza un nombre de sede; Jev lo recupera por sonido | `site_heard` | 94 | 17,8 (14,8–21,3) |
| Modelo inventa | Identifica con un nombre de relleno («the patient») en vez de preguntar | `name_placeholder` | 72 | 13,6 (11,0–16,8) |
| Escritura | Intento de escribir sin un «sí» claro a lo leído: la PUERTA se cierra | `gate` puerta = no | 67 | 12,7 (10,1–15,8) |
| Modelo inventa | Pasa un DNI, teléfono o fecha de nacimiento que quien llama no ha dicho | `identifier_invented` | 55 | 10,4 (8,1–13,3) |
| Turnos | Se contestó a un turno sin terminar; se deshace y se replanifica | `undo` | 43 | 8,1 (6,1–10,8) |
| Conversación | Quien llama rechaza el hueco leído | `offer_rejected` | 37 | 7,0 (5,1–9,5) |
| Conversación | Mismo dato pedido dos veces sin éxito: se pide de otra forma | `otra_forma` | 20 | 3,8 (2,5–5,8) |
| Infraestructura | El planificador falla también al reintentar; el agente pide que se lo repitan | `planner_error` | 18 | 3,4 (2,2–5,3) |
| Modelo restringe | Busca en una sede distinta de la pedida | `site_corrected` | 17 | 3,2 (2,0–5,1) |
| Negativa | Va a negar sin haber mirado la agenda del paciente | `must_check_first` | 16 | 3,0 (1,9–4,9) |
| Modelo inventa | Fecha de nacimiento inventada al dar de alta | `dob_invented` | 12 | 2,3 (1,3–3,9) |
| Negativa | Da la llamada por denegada con una oferta abierta | `decline_with_offer` | 10 | 1,9 (1,0–3,5) |
| Conversación | Tres peticiones del mismo dato: la llamada se atasca | `dejar_de_pedir` | 10 | 1,9 (1,0–3,5) |
| Modelo inventa | Afirma una escritura que no se ha hecho, o una hora que no sale de ningún hueco | `truth_guard` | 5 | 0,9 (0,4–2,2) |
| Límite | La llamada pasa de 180 s (sin guardia en código) | derivado | 4 | 0,8 (0,3–1,9) |

- **185 de 528 llamadas (35,0 %)** tienen al menos una guardia sobre el planificador (inventa, restringe o motivo de la negativa). Es el argumento del diseño: el LLM propone y el código comprueba.
- **207 de 528 (39,2 %)** tienen al menos una herramienta que devuelve error al planificador. Los más frecuentes: `find_slots` sin paciente identificado (80 llamadas), `confirm_booking` sin haber leído antes la oferta (40), `end_call` sin que quien llama se haya despedido (29), `confirm_booking` sin elección clara (21). Quien llama no oye ninguno de ellos.
- De `planner_error` hay dos causas con nombre en las trazas de ejemplo: un 400 de Gemini por `thought_signature` ausente y un `UnboundLocalError` en `llm_step` que el código actual ya no tiene.
- Catalogados que **no aparecen** en estas llamadas: `jev_down`, `colgar_pronto`, `escritura_retirada`, `specialty_corrected`, `date_from_ignored`, `decline_kept`, `circuit_open`, `circuit_closed` (los dos últimos se añaden con este cambio).

Cómo acaban: BOOK 283 (53,6 %), NO_ACTION 192 (36,4 %), RESCHEDULE 13, REGISTER 13, CANCEL 10 (más 6 dobles), ESCALATE 8; ninguna llamada sin declarar. Cierra el agente con despedida en 318 (60,2 %), el arnés con `stop` en 197 (37,3 %) y se corta la conexión en 13 (2,5 %). Idioma: inglés 499, español 16, catalán 13.

Lo que la tabla **no** dice: si la acción declarada era la correcta. Eso lo juzga el marcador de Prosper y, en local, la réplica.

## 4. Lo que cuesta una llamada, en segundos

| Medida (N = 528 llamadas) | n | Mediana | p90 | p99 |
|---|---|---|---|---|
| Duración de la llamada | 528 | 54,5 s | 108,0 s | 171,9 s (máx. 211,6 s) |
| Jev, un juicio por turno | 2 406 | 350 ms | 708 ms | 819 ms |
| Planificador, paso que redacta | 1 267 | 585 ms | 741 ms | 1 233 ms |
| Planificador, paso con herramientas | 536 | 602 ms | 740 ms | 1 089 ms |
| Herramienta (incluye la API de la clínica) | 1 426 | 576 ms | 719 ms | 1 188 ms |

**Especulación:** de 2 406 turnos, 1 534 (63,8 %; Wilson 61,8–65,7) se contestaron con el plan ya hecho sobre un parcial. Ventaja (`head_start_ms`): mediana 676 ms, p90 1 899 ms. Se dio por buena por texto idéntico en 1 296, por «solo cortesía de más» en 129 y porque Jev dijo que el definitivo no pedía nada nuevo en 108.

Estas latencias son de los pasos del cerebro. El tiempo «fin de voz → primera palabra» lo emite el servidor como evento `latency`, pero **no se guarda en el informe de la llamada**: no se puede agregar a posteriori. Es una laguna conocida.

## 5. Lo que cuesta una llamada, en dinero

### Lo que se mide desde ahora

`Conv.report()` incluye un bloque `cost` (`agent/coste.py`): peticiones y tokens de Jev; peticiones y tokens de entrada y salida del planificador por modelo (se recoge el `usage` de OpenRouter y el `usage_metadata` de Gemini, que antes se tiraban); lecturas y escrituras en la API de la clínica; caracteres de voz sintetizados por fuente (ElevenLabs o Gemini) y caracteres servidos de la caché; segundos de oído. De ahí, USD por componente, USD total, segundos y USD por minuto.

- El medidor es por llamada (`contextvars`): hay hasta 10 llamadas concurrentes en el mismo bucle y no se mezclan. Las sombras de la especulación comparten el medidor de su llamada: lo que gasta una especulación descartada también se ha pagado y se cuenta.
- `api_calls` era `len(API.log)`, el contador de **todo el proceso**, no el de la llamada. En los 528 informes ese campo está mal y no se usa aquí. Corregido.
- El audio del oído se aproxima como duración × sesiones de transcripción abiertas (dos); no se cuentan los bytes enviados.
- No entran: la precarga de frases fijas al arrancar (coste fijo del despliegue) ni la telefonía.
- Medir nunca lanza hacia la llamada (comprobado con datos absurdos en `pruebas_rigor.py`).

Precios de lista usados (USD; **por verificar** antes de citarlos; fuente y fecha en `coste.py`):

| Componente | Precio | Fuente (consultada el 20-09-2026) |
|---|---|---|
| Jev | 0,042 por 1M tokens de entrada | constante previa del proyecto (`demo/server.py`); no verificada de nuevo |
| Planificador, gpt-oss-120b en Groq vía OpenRouter | 0,15 entrada / 0,60 salida por 1M | API pública de OpenRouter, `/models/openai/gpt-oss-120b/endpoints` |
| Planificador de respaldo, Gemini 3.5 Flash-Lite | 0,30 / 2,50 por 1M | ai.google.dev/gemini-api/docs/pricing |
| Voz, ElevenLabs plan Creator | 0,10 por 1K caracteres (22 USD / 220 000) | elevenlabs.io/pricing/api |
| Voz de respaldo, Gemini | ~0,021 por 1K caracteres (0,018 USD/min a 14 car./s) | estimación a partir del precio por minuto |
| Oído, Gemini Live | 0,005 por minuto de audio de entrada | ai.google.dev; el modelo de transcripción concreto no tiene precio propio publicado |

### Lo que se puede decir de las 528 llamadas ya hechas

Los informes antiguos **no tienen recuentos de tokens**. Tienen la duración, la transcripción y la traza. Con eso, por llamada:

| Recuento en la traza | Mediana | p90 | Media |
|---|---|---|---|
| Juicios de Jev registrados (`perception`) | 4 | 7 | 4,6 |
| Pasos del planificador registrados (`planner` + herramientas que él pide) | 4 | 10 | 5,1 |
| Caracteres dichos por el agente | 432 | 713 | 453,3 |
| Duración | 54,5 s | 108,0 s | 63,4 s |

Fórmula:

```
USD ≈ juicios × tok_Jev × 0,042/1e6
    + pasos × (tok_entrada × 0,15 + tok_salida × 0,60)/1e6
    + caracteres_sintetizados × 0,10/1e3
    + (duración/60) × sesiones_de_oído × 0,005
```

**Estimación, no medida.** Supuestos: 1 800 tokens por juicio de Jev y 4 700 de entrada por paso del planificador (contando caracteres sin red: 7 051 de preguntas de Jev; 11 296 de prompt de sistema + 7 267 de herramientas con el catálogo de la clínica simulada; ~4 caracteres por token; sin la conversación acumulada), 60 tokens de salida por paso, dos sesiones de oído:

| Caso | Jev | Planificador | Voz (cota superior: nada en caché) | Oído | Total |
|---|---|---|---|---|---|
| Llamada media | 0,00034 | 0,00378 | 0,04533 | 0,01056 | **0,060 USD** |
| Llamada p90 (cada recuento en su p90) | 0,00053 | 0,00741 | 0,07129 | 0,01800 | **0,097 USD** |

Lectura honrada de esa tabla:

- **La voz domina y es una cota superior**: supone que cada carácter se sintetiza. Las frases fijas y las plantillas están en caché de disco; cuánto ahorra no se sabía hasta hoy (el bloque `cost` lo separa en `tts_chars_synthesised` y `tts_chars_from_cache`).
- **Jev y el planificador son una cota inferior**: los juicios de Jev sobre parciales y los pasos de las especulaciones descartadas no dejan evento en la traza. El documento de diseño hablaba de «unas 50 peticiones» de Jev por llamada; la traza registra 4,6. El valor real está entre ambos y lo dará el bloque `cost`. Aun multiplicando Jev por diez, sigue siendo el componente más barato.
- Orden de magnitud: **céntimos de dólar por llamada**, con la voz y el oído por delante de los dos modelos de lenguaje.

## 6. Cortacircuitos de OpenRouter

`_OR["or_down"]` era un cerrojo de proceso que no se reponía: un solo timeout de OpenRouter en una llamada pasaba TODAS las llamadas al camino de Gemini (más lento) hasta reiniciar el servidor. Ahora es un cortacircuitos con plazo: 60 s para OpenRouter (`CIRCUITO_S`), 300 s para la denegación de Google (`CIRCUITO_DENEGADO_S`); pasado el plazo, la siguiente petición prueba de nuevo y, si va bien, cierra. Cada cambio deja `circuit_open` / `circuit_closed` en la traza de la primera llamada que pasa, y `modos.py` los cuenta. Comprobado sin red con un OpenRouter falso: abre al fallar, no se le llama mientras está abierto, reprueba al vencer el plazo y cierra.

## 7. Límites conocidos

- Las 528 llamadas mezclan versiones del agente y son en su mayoría del arnés (94,5 % en inglés): las tasas no son las de un tráfico real ni las de la versión de hoy. `--desde` acota por fecha de fichero; desde las 04:00 del 20-09 solo hay 5 llamadas v2, demasiado pocas para una tasa.
- La taxonomía solo ve lo que alguna guardia caza. Un fallo silencioso (una reserva correcta en forma pero equivocada en fondo) no deja evento: lo ven la réplica y el marcador.
- No hay todavía ninguna tirada con `--rep N` agregada con el método nuevo, ni ningún informe con bloque `cost` medido: la instrumentación está comprobada con dobles locales, no con servicios reales.
- Los precios son de lista, sin descuentos ni cuotas gratuitas, y el del oído es una aproximación por modelo vecino.
- Wilson supone independencia entre ensayos; entre repeticiones del mismo caso no la hay del todo.
- La latencia percibida (fin de voz → primera palabra) no se persiste en el informe de llamada.
- El cerebro v1 (`agent/brain.py`) no lleva medidor de coste y su `api_calls` sigue siendo el del proceso.
- `API.log` (el registro de peticiones del proceso) crece sin límite mientras el servidor vive; no se ha tocado.
