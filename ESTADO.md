# Estado del agente (20-09-2026, 01:20)

Todo corre en el NAS (`pop-os`, por Tailscale: `joseluis@100.107.233.6`) y no depende de ningún portátil. La sesión
de usuario tiene *linger*, así que los servicios siguen aunque nadie inicie sesión.

## Qué hay en marcha

| Servicio | Puerto | Qué es |
|---|---|---|
| `prosper-agent` | 7860 | **El agente que llama Prosper.** `AGENT=v2` (planificador con herramientas), `Restart=always` |
| `prosper-tunnel` | (sin puerto) | Túnel de Cloudflare a 7860: `wss://battle-kelly-certificates-impossible.trycloudflare.com/ws`, que es el endpoint registrado en el panel |
| `prosper-agent-local` | 7861 | La misma versión contra la clínica simulada, para la arena y los arneses |
| `prosper-tunnel-local` | (sin puerto) | Túnel a 7861 (`tracker-oxygen-alabama-steal`), el que usa la web de la arena |
| `prosper-fake-api` | 8770 | Clínica simulada con la misma API |
| `puntuadas` | (sin puerto) | Lanza rondas PUNTUADAS solo, hasta las 05:40 |
| `prosper-arena-v2` y `prosper-v2` | 7880 y 7862 | Instancias de prueba desde el worktree `~/Dev/prosper-v2` (no hacen falta para competir) |

La web de la arena (`https://prosper.joseluissaorin.com`) está en Cloudflare y sirve exactamente lo que hay en
`arena/` del repositorio.

## Las rondas puntuadas, solas

`agent/puntuadas.py` (copiado a `/tmp/puntuadas3.py`, que es lo que ejecuta el servicio `puntuadas`; vivía solo en
`/tmp` y se habría perdido en el primer reinicio). El panel ya **no tiene ronda completa**: cada ronda puntuada es de
un problema y el total del equipo es la suma de la **mejor** ronda de cada uno. Por eso el guion:

1. mira qué problema tiene más puntos por ganar y lanza ese;
2. respeta la espera entre rondas que impone el panel;
3. antes de cada ronda comprueba `/health` del agente y lo reinicia si no responde;
3 bis. **lo que falta lo dice el panel, no una estimación nuestra.** Prosper acredita por CASO y guarda el mejor de
   cada uno; el máximo por ronda se queda corto y hace creer que quedan puntos donde no los hay. A las 03:05 del
   20-09 el lanzador llevaba cinco rondas y cuarenta minutos en `nearest_site` creyendo que faltaban 9 cuando ya
   estaba 4/4 acreditado —cero puntos—, mientras `change_and_cancel` y `no_slot_free` (+8 cada uno) seguían sin
   lanzarse ni una vez. Ahora usa `progress` del panel: `(credited_of - credited) × peso`;
4. para solo a las 05:40, antes de que se congele el marcador (06:00).

Para verlo: `journalctl --user -u puntuadas -f`. Para pararlo: `systemctl --user stop puntuadas`.

## Cómo está el agente

- **Cerebro v2** (`agent/conv.py`): el Sistema 2 planifica con herramientas tipadas sobre un núcleo determinista, y
  Jev vigila cada turno (urgencias, manipulación, fin de turno y la puerta del «sí» antes de escribir).
- **Sistema 2 por Groq** (`gpt-oss-120b` vía OpenRouter, 0,34-0,59 s por paso), con Gemini de respaldo automático si
  OpenRouter falla, y al revés si Google vuelve a denegar el proyecto.
- **Voz:** ElevenLabs (µ-law de 8 kHz directo, primer audio en ~150 ms). **Oído:** Gemini en directo, con Scribe de
  ElevenLabs como alternativa (`EARS=scribe`).
- **Latencia medida en voz real:** 608-874 ms de mediana del fin de voz a la primera palabra.
- **Réplica local** (`agent/replica.py`, los 77 casos publicados contra la API real sin enviar nada): 26/26 en los
  problemas principales de la última pasada.

## Nunca reiniciar a ciegas

El 20-09 a las 00:16 un despliegue cayó entre el lanzamiento de la ronda puntuada de `nearest_site` y su
primera llamada. `active_calls` era 0, así que la guarda de entonces dejó pasar el reinicio: los cuatro casos
se perdieron sin dejar ni traza local y la ronda se quedó colgada en `running`. **Doce puntos.** Entre dos
llamadas de la misma ronda también hay hueco, así que mirar `active_calls` no basta: hay que mirar la ronda.

```bash
python3 panel.py reiniciar     # espera a que no haya ronda NI llamada, y entonces reinicia
```

Lo usa también `panel.py nas --reiniciar` cuando el agente es local. Si se reinicia a mano, comprobar antes
`ronda activa` en `panel.py estado`.

## Dos defectos vistos en rondas puntuadas que el arnés de texto NO reproduce

Los dos están diagnosticados con su traza; ninguno tiene arreglo enviado, y el motivo es el mismo: **el arnés de
texto no los produce**, así que cualquier guardia que se les ponga solo se puede medir por lo que estorba.

**1. Reserva doble** — el defecto más caro que queda, y **no tiene arreglo con un guardia**.

Visto dos veces en rondas puntuadas: `noise` (20-09 02:01, `calls/eee5b071-8012-580b-8c98-73aeaf853a55.json`) y
`no_slot_free` (20-09 03:09, `calls/3aa57bb5-285d-5f0f-9aa5-21e8205cb6b7.json`). El segundo enseña el mecanismo
entero: el transcriptor oyó «**Play That One**», Jev lo dio por confirmación (`confirm` 0,86 · «sí» 0,73), se
reservó una cita que la paciente no había aceptado, ella dijo «Oh, no. I didn't agree to that time» y el agente
reservó **otra** en vez de sustituirla. Dos `BOOK` donde se esperaba uno: cero.

**Por qué no se arregla con un guardia.** La escritura ya salió a la API de Prosper: no se puede retirar. Así que
el único arreglo posible es no escribir la primera, y eso es apretar la puerta. Se probó, con reproducción
(`no_acepte_eso`, que lo saca 14 veces de 20):

| | acierto | dobles |
|---|---|---|
| referencia | 3/20 | 14 |
| exigir una palabra de asentimiento, salvo que Jev lo vea clarísimo | 2/20 | 10 |
| exigirla siempre | 1/20 | 11 |

Baja el defecto pero **no sube el acierto**: la primera reserva sigue saliendo y la corrección crea la segunda.
Esto pide **escritura de verdad en dos fases** —poder sustituir una reserva hecha en la misma llamada— no un
guardia más. Es trabajo de diseño, no de madrugada.

Antes se habían probado además cuatro guardias sobre la SEGUNDA reserva, todos peores que no hacer nada
(27/32, 28/32, 23/32 y 29/32 frente a 31/32).

**2. Colgar tras contestar una pregunta** (`the_questions`, 20-09 02:23, señal `agent_silence` ·
`calls/e6031651-3035-5830-832f-a18aada2f209.json`). El llamante preguntó el horario de Arenal Norte, el agente lo
dijo **bien** (09:00–19:00, comprobado contra la API) y, al oír «Right, thanks.», llamó a `end_call` sin preguntar
si necesitaba algo más. La llamada acabó en `NO_ACTION(out_of_scope)` a los 55 s. Los turnos del agente fueron de
0,5-0,6 s, así que `agent_silence` no es latencia nuestra. Candidato a arreglo: exigir `says_goodbye` más alto para
colgar cuando no se ha hecho nada en la llamada — sin medir, y con riesgo de no colgar nunca.

## Si algo va mal

```bash
ssh joseluis@100.107.233.6
journalctl --user -u prosper-agent -n 50      # qué ha hecho el agente
curl -s 127.0.0.1:7860/health                 # ¿vivo?
systemctl --user restart prosper-agent        # reiniciar (espera a que no haya llamada activa si puedes)
journalctl --user -u prosper-tunnel | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1
```

Si el túnel cambia de URL (pasa si se reinicia), hay que actualizar el endpoint en *Settings* del panel de Prosper;
si no, Prosper llama a una dirección muerta.

## Claves

En `~/.claude/.secrets/` del NAS: `prosper.env`, `gemini.env`, `typesafe.env`, `elevenlabs.env`, `openrouter.env`.
Ninguna va al repositorio.
