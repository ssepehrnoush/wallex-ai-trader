#!/bin/sh
# Container entry on Liara. Liara has an Iranian IP, so Binance, OKX and OpenAI do not answer from here. An SSH tunnel to
# the team's server outside Iran gives a local SOCKS proxy for exactly those three hosts (the server's authorized_keys
# allows nothing else). Wallex, its MCP and BoxAPI (.ir) always go direct.
# Env: TUNNEL_KEY_B64, TUNNEL_HOST, TUNNEL_PORT, TUNNEL_USER (all optional: without them everything goes direct).
set -e
if [ -n "$TUNNEL_KEY_B64" ] && [ -n "$TUNNEL_HOST" ]; then
  umask 077
  echo "$TUNNEL_KEY_B64" | base64 -d > /tmp/tunnel_key
  (
    while true; do
      ssh -N -D 127.0.0.1:1080 -i /tmp/tunnel_key -p "${TUNNEL_PORT:-22}" \
        -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/tmp/known_hosts \
        -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
        "${TUNNEL_USER:-wxtunnel}@${TUNNEL_HOST}" || true
      echo "tunnel dropped; reconnecting in 15 s"
      sleep 15
    done
  ) &
  export HTTPS_PROXY="socks5h://127.0.0.1:1080" HTTP_PROXY="socks5h://127.0.0.1:1080"
  export NO_PROXY="localhost,127.0.0.1,::1,.ir"
fi
exec "$@"
