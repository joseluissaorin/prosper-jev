# Pruebas en el panel de Prosper (`panel.py`)

El panel de Prosper (https://hackspain.getprosperapp.com/leaderboard) tiene dos maneras de llamar a nuestro agente:

- **Práctica** (`POST /api/problems/{problema}/runs` con un `case_id` publicado). No puntúa. Enseña enseguida la respuesta esperada, los campos que fallaron, la transcripción y la grabación. Es lo que hace `panel.py`.
- **Ronda puntuada** (`POST /api/problems/{problema}/scored-runs`). Usa casos privados y suma al marcador. **`panel.py` no la lanza nunca**: se lanza a mano desde la web, y solo cuando José Luis lo decide.

Prosper llama siempre al endpoint que figura en *Settings* del panel: el túnel de Cloudflare del NAS (`wss://….trycloudflare.com/ws`), que acaba en el agente real, `prosper-agent` en el puerto 7860 de pop-os.

## Antes de lanzar nada: ¿el NAS corre el código nuevo?

Uvicorn **no recarga solo**. Un `git pull` en el NAS deja el código nuevo en disco, pero el agente sigue sirviendo el viejo hasta que se reinicia. Pasó el 19-09-2026: el agente llevaba en marcha desde las 16:56 y los seis commits de la tarde (capa de jugadas, lectura determinista…) no estaban en las llamadas de Prosper.

```bash
python3 panel.py nas               # compara el HEAD del NAS con el local y la hora de arranque con la del commit
python3 panel.py nas --reiniciar   # si corre código viejo, espera a que no haya llamada en curso y lo reinicia
```

El reinicio espera a `active_calls == 0` en `/health` del agente, no a que se vacíe la cola: la cola de prácticas es de todo el equipo y otra sesión puede tenerla siempre llena. Reiniciar a mitad de llamada corta esa llamada.

Si el NAS no tiene el mismo commit que el Mac, primero hay que llevarlo allí (`git push` aquí y `git pull` en `~/Dev/prosper-jev` del NAS).

## Uso

Desde `agent/`, con cualquier `python3` (solo usa la biblioteca estándar):

```bash
python3 panel.py problemas --casos                 # los 14 problemas con su peso (1-5) y sus casos publicados
python3 panel.py estado                            # endpoint, cola, aciertos, últimas rondas y estado del NAS

python3 panel.py lanzar --dificiles --seguir       # los problemas de peso 4 (difficult_caller y adversarial)
python3 panel.py lanzar third_party triage         # todos los casos de esos problemas
python3 panel.py lanzar --caso difficult_caller-ac2ac0d27f0d --seguir
python3 panel.py lanzar noise --reiniciar          # reinicia antes el agente si corre código viejo

python3 panel.py seguir RUN_ID RUN_ID              # espera a que acaben rondas ya lanzadas y las enseña
python3 panel.py ver --problema adversarial -n 4   # las últimas rondas de un problema, con transcripción
python3 panel.py ver 9f5379b4 --breve              # por id (vale el prefijo de 8): resultado y campos que fallan
python3 panel.py cancelar RUN_ID
```

**Prosper admite una sola práctica por equipo en cola o en curso** (si no, responde 409). Por eso `lanzar` las encadena: lanza una, espera a que acabe y lanza la siguiente; si otra sesión del equipo tiene la suya en marcha, espera turno. Ojo: `lanzar` se queda esperando hasta la última aunque no lleve `--seguir`; `--seguir` solo añade enseñar cada caso al acabar. Cada práctica tarda de dos a cuatro minutos, más lo que haya en la cola global (`public_wait` en `estado` es la posición en esa cola, compartida con los demás equipos).

Para dejarlo corriendo y leerlo luego:

```bash
python3 -u panel.py lanzar --dificiles --seguir > calls/panel/dificiles-$(date +%H%M).log 2>&1 &
```

## Qué se guarda y dónde

- `agent/calls/panel/<run_id>.json`: la ronda entera tal como la devuelve Prosper (campos, transcripción con tiempos, atribución del fallo). `agent/calls/` está en `.gitignore`, porque lleva datos de pacientes (simulados, pero con forma real).
- Las credenciales están en `~/.claude/.secrets/prosper.env`: `PROSPER_DASHBOARD_EMAIL`, `PROSPER_DASHBOARD_PASSWORD` y `PROSPER_API_KEY` (la clave `X-Api-Key` de la API de la clínica). Vinieron en el correo de Lluc Santamaria (Prosper) del 19-09-2026, asunto «HAckspain», en jlsf2005@gmail.com. Nunca van al repositorio.

## Cómo leer un resultado

```
━━ difficult_caller · completed · 18:40:12 · <run_id>

 ✘ The Difficult Caller · interrupts: failed (agent)
   ≠ actions[0].slot: esperado '2026-09-21T11:45:00+02:00' · enviado '2026-09-22T09:00:00+02:00'
     actions[0].patient_id: esperado 'P00012' · enviado 'P00012'

     2.9s AGENTE Good afternoon. Clinica Renal.
     7.7s LLAMA  Hello.
```

- `≠` marca un campo que no coincide. El marcador es literal: basta un campo mal para perder el caso.
- Entre paréntesis va la atribución de Prosper (`agent`, `harness`…) y los códigos de señal, si los hay.
- Los casos publicados pueden aceptar varias respuestas; `panel.py problemas --casos` resume cada caso.

## Los problemas por peso (19-09-2026)

| Peso | Problemas |
|---|---|
| 4 | `difficult_caller` (13), `adversarial` (14) |
| 3 | `the_rules` (6), `third_party` (9), `triage` (10), `languages` (11), `noise` (12) |
| 2 | `doctor_and_site`, `the_new_patient`, `when_exactly`, `no_slot_free`, `change_and_cancel` |
| 1 | `simple_booking` |
| 0 | `switchboard` (sin puntos) |

`--dificiles` toma los de peso 4 (constante `HARD_WEIGHT` en `panel.py`).

## La API del panel, por si hay que ampliarlo

Todo cuelga de `https://hackspain.getprosperapp.com/leaderboard/api` con la cookie de sesión de `POST /session {email, password}`. Se sacó del JavaScript del propio panel.

| Llamada | Para qué |
|---|---|
| `GET /problems` | lista con `id`, `number`, `weight`, `examples` |
| `GET /problems/{id}` | enunciado y casos publicados con sus respuestas aceptadas |
| `POST /problems/{id}/runs {case_id[, endpoint, headers]}` | práctica; `endpoint` opcional para llamar a otro sitio sin tocar Settings |
| `GET /problems/{id}/submissions` | intentos de ese problema |
| `GET /teams/{team_id}` | estadísticas, cola (`eligibility`), endpoint y todas las rondas con transcripción (tarda: hasta 30 s) |
| `POST /runs/{run_id}/cancel` | cancelar |
| `POST /problems/{id}/scored-runs` | **ronda puntuada: no usar desde scripts** |
