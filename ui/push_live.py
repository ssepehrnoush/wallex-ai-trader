"""Send the live run's snapshot to the public control room on Liara once a minute.

Read-only towards the run: it computes the same snapshot the local control room shows (ui/data.py) and posts it,
gzipped, to <url>/api/push. Nothing in runs/ is written. The token is PUSH_TOKEN in agentic-hackathon/.env; the same
value is set on the Liara app by deploy/deploy_liara.py. liara.run is an Iranian host, so this goes direct and never
through the local VPN proxy.

  python ui/push_live.py --live-dir runs/live_main --url https://wallex-ai-trader-live.liara.run
"""
import argparse
import gzip
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values  # noqa: E402

from ui.data import snapshot  # noqa: E402
from wallexbot.config import ENV_FILE  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-dir", required=True)
    ap.add_argument("--url", default="https://wallex-ai-trader-live.liara.run")
    ap.add_argument("--every", type=int, default=60, help="seconds between pushes")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    token = os.environ.get("PUSH_TOKEN") or dotenv_values(ENV_FILE).get("PUSH_TOKEN") or ""
    if not token:
        sys.exit("PUSH_TOKEN missing in agentic-hackathon/.env")
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        try:
            body = gzip.compress(json.dumps(snapshot(Path(a.live_dir)), ensure_ascii=False).encode("utf-8"))
            req = urllib.request.Request(a.url.rstrip("/") + "/api/push", data=body, method="POST", headers={
                "Content-Type": "application/json", "Content-Encoding": "gzip", "X-Push-Token": token})
            with direct.open(req, timeout=30) as r:
                print(time.strftime("%H:%M:%S"), "pushed", r.status, f"{len(body) // 1024} KB", flush=True)
        except Exception as e:      # the network or the app is down: try again next minute, never stop
            print(time.strftime("%H:%M:%S"), "push failed:", e, flush=True)
        if a.once:
            return
        time.sleep(a.every)


if __name__ == "__main__":
    main()
