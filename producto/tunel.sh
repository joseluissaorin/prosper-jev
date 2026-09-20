#!/bin/sh
# Si el túnel de la instancia local del agente cambia de nombre (pasa cuando se reinicia cloudflared en el NAS),
# la llamada desde la web deja de contestar. Esto lee el nombre nuevo, lo pone en wrangler.jsonc y vuelve a desplegar.
set -e
cd "$(dirname "$0")"
NUEVO=$(ssh -o ConnectTimeout=8 joseluis@100.107.233.6 "journalctl --user -u prosper-tunnel-local --no-pager | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1")
[ -n "$NUEVO" ] || { echo "No he podido leer el túnel del NAS."; exit 1; }
ACTUAL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' wrangler.jsonc | head -1)
if [ "$NUEVO" = "$ACTUAL" ]; then echo "El túnel no ha cambiado: $ACTUAL"; else
  sed -i '' "s#$ACTUAL#$NUEVO#" wrangler.jsonc && echo "Túnel nuevo: $NUEVO" && npx wrangler deploy | tail -3
fi
curl -s https://digame.joseluissaorin.com/api/estado; echo
