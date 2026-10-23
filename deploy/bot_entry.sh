#!/bin/sh
# The bot app's main process on Liara (after deploy/start.sh has opened the tunnel).
#   BOT_MODE   shadow (default: paper trading on the live order book) or live
#   BOT_RUN    run folder under runs/ (default shadow_liara)
#   BOT_LIVE_OK=yes  must also be set for live; without it the container refuses real orders and stays in shadow
#   PUSH_URL   optional: the public control room that should show this run (ui/push_live.py, needs PUSH_TOKEN)
# runs/ is the app's disk. On every start, files from seed_runs/ that the disk does not have yet are copied in
# (committed backtest summaries the AI reads, and recent ticks so the bot never starts on a cold baseline).
set -e
mkdir -p runs
cp -rn seed_runs/. runs/ 2>/dev/null || true
MODE="${BOT_MODE:-shadow}"
RUN="${BOT_RUN:-shadow_liara}"
if [ "$MODE" = "live" ] && [ "$BOT_LIVE_OK" != "yes" ]; then
  echo "BOT_MODE=live without BOT_LIVE_OK=yes: refusing real orders, running shadow instead"
  MODE=shadow
  RUN=shadow_liara
fi
mkdir -p "runs/$RUN"
python ui/server.py --public --live-dir "runs/$RUN" --host 0.0.0.0 --port 8000 &
if [ -n "$PUSH_URL" ]; then
  python ui/push_live.py --live-dir "runs/$RUN" --url "$PUSH_URL" &
fi
if [ "$MODE" = "live" ]; then
  exec python -m wallexbot live --i-understand-real-money --run "$RUN"
fi
exec python -m wallexbot shadow --run "$RUN"
