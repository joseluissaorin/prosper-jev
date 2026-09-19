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

`/tmp/puntuadas3.py`, como servicio `puntuadas`. El panel ya **no tiene ronda completa**: cada ronda puntuada es de
un problema y el total del equipo es la suma de la **mejor** ronda de cada uno. Por eso el guion:

1. mira qué problema tiene más puntos por ganar y lanza ese;
2. respeta la espera entre rondas que impone el panel;
3. antes de cada ronda comprueba `/health` del agente y lo reinicia si no responde;
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
