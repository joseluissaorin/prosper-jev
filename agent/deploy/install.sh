#!/bin/sh
# Instala (o actualiza) los servicios de usuario del agente en esta máquina. Sin root.
set -e
cd "$(dirname "$0")"
mkdir -p ~/.config/systemd/user
cp prosper-agent.service prosper-fake-api.service prosper-tunnel.service ~/.config/systemd/user/
test -f ~/.config/prosper-agent.env || printf 'PROSPER_API_BASE_URL=http://127.0.0.1:8770\n' > ~/.config/prosper-agent.env
chmod 600 ~/.config/prosper-agent.env
systemctl --user daemon-reload
systemctl --user enable --now prosper-fake-api prosper-agent prosper-tunnel
sleep 6
journalctl --user -u prosper-tunnel --no-pager -n 50 | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1
