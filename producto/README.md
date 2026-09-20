# Dígame: el producto

La recepcionista de voz de este repositorio, empaquetada como producto: **https://digame.joseluissaorin.com**
(también en `https://digame.jlsf2005.workers.dev`). La tesis, en una frase: no se vende una IA que coge el teléfono,
se vende la primera que **se gana la autonomía con pruebas**; y esas pruebas son el producto, la interfaz y el precio.

## Qué hay

| Ruta | Qué es |
|---|---|
| `/` | La portada. El botón **Llamar** convierte la página en la llamada: micrófono a µ-law de 8 kHz por `/llamada`, y el acta se compone en directo con lo que llega por `/monitor` (los juicios de Jev como glosas al margen, la puerta como sello). Al colgar se entrega el acta (imprimible y con enlace guardado). Sin micrófono, `/?grabada` reproduce una llamada real con sus tiempos. Más abajo: **Sistema 1 y Sistema 2** (la clave del diseño: el tríptico Sistema 1 percibe · el núcleo decide · Sistema 2 redacta, el cronograma de un turno y un banco de pruebas donde Jev juzga EN DIRECTO la frase que escriba el visitante y el núcleo enseña qué haría con esos juicios), los cuatro niveles, la propuesta por WhatsApp (nivel 1) y la orden «mañana no viene la doctora» (nivel 3), el gemelo desde una web, «intente romperla», la nota, el precio y el código de desvío. |
| `/mesa` | No hay *dashboard*: el parte en prosa, las dudas, el archivo de actas, el libro de la casa (con el rito de dictar una regla) y la nota del examen con costes, latencias, guardias y suspensos. |
| `/acta?id=…` | Un acta: edición crítica de una llamada. `?v=…` para las guardadas desde la web (KV, 30 días) y `?mia=1` para la propia. |
| `/auditoria` | La auditoría del teléfono que recibe una clínica tras su semana de escucha (ejemplo con la clínica de pruebas). |
| `/sello.svg` | El sello «Esta clínica contesta siempre» con la nota del examen. |

Todo lo que se enseña sale de datos reales: 962 llamadas con su traza y 3 118 casos del arnés (78 pasadas).

## Piezas

- `public/`: el sitio estático (HTML, CSS y JS sin dependencias ni compilación). `css/digame.css` es el sistema de diseño
  (papel, tinta, terracota y azul; Newsreader y monoespaciada). `js/acta.js` pinta un acta (entera, en directo o reproducida),
  `js/llamada.js` es la llamada desde el navegador, `js/arena.js` la arena de fondo, `js/sistemas.js` el banco de pruebas del Sistema 1, `js/portada.js` y `js/mesa.js` el resto.
- `construir.py`: convierte las trazas (`agent/calls/*.json` y los `arnes_*.json`) en `public/datos/`: un acta por llamada,
  el índice, el parte (prosa escrita por código a partir de lo contado, con sus actas de evidencia), las dudas, el libro de la
  casa y la nota. `TRAZAS=/ruta python3 producto/construir.py`; por defecto lee `/tmp/digame_trazas`
  (`rsync -az joseluis@100.107.233.6:Dev/prosper-jev/agent/calls/ /tmp/digame_trazas/`). No llama a ningún modelo.
- `src/worker.js`: el Worker. `/llamada` y `/monitor?call=` son proxies WebSocket a la instancia LOCAL del agente
  (clínica simulada) con los tokens guardados como secretos, el monitor filtrado por llamada, corte a los 4 minutos, tres
  líneas como máximo y cupo por IP. `/api/gemelo` lee la web de una clínica y compone su gemelo (OpenRouter si hay saldo;
  si no, Workers AI con `qwen3-30b`), sin fiarse del modelo: teléfono, dirección, equipo y precios solo valen si están en la
  web. `/api/percibe` es el Sistema 1 en directo: manda a Jev (TypeSafe) las mismas preguntas que el agente y devuelve etiquetas y probabilidades; gasta la cuenta de TypeSafe del agente, así que lleva caché por frase, 40 frases por IP y día y 800 globales (unos 0,04 $ al día como mucho). `/api/saludo` es la voz (ElevenLabs, con caché en KV), `/api/acta` guarda actas y `/api/estado` dice si hay recepción.

## Desplegar

```bash
cd producto
npx wrangler deploy          # sitio + Worker, a workers.dev y a digame.joseluissaorin.com
./tunel.sh                   # si la llamada no contesta: el túnel del NAS ha cambiado de nombre
```

Secretos del Worker (`npx wrangler secret put …`): `CALL_TOKEN`, `MONITOR_TOKEN` (los de `~/.config/prosper-agent-local.env`
del NAS), `OPENROUTER_API_KEY`, `ELEVENLABS_API_KEY` y `TYPESAFE_API_KEY`. KV: `ACTAS`.

## Lo que es de verdad y lo que es demostración

De verdad: la llamada y su acta, las 962 actas, el parte, las dudas, el libro (reglas y recuentos), la nota, la auditoría,
el gemelo y su saludo. Demostración (se queda en el navegador y lo dice): contestar una duda, dictar una regla con su
ensayo, la conversación de WhatsApp y la orden de nivel 3. No hay telefonía real, cobros ni adaptadores de agenda.
La instancia de producción del agente (la que llama el panel de Prosper) no se toca desde aquí.
