"""Offline test of the control-room UI (ui/server.py, ui/data.py): no model, no network, no real run touched.

Checks: the snapshot of the committed live run has the parts the page draws; the high-risk controls are locked on a
real-money run whatever the confirmation; on a paper (shadow) copy they need the typed confirmation, then STOP,
resume and FLATTEN work and are logged; the model's page is reduced to the known components; and a POST without
the per-process token is refused.

  python scripts/test_ui.py
"""
import json
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ui import server as S  # noqa: E402
from ui.data import snapshot  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("PASS " if cond else "FAIL ") + msg)
    fails += not cond


snap = snapshot(ROOT / "runs" / "live_main")
check(snap["kind"] == "live" and {"kpis", "equity", "brakes", "markets", "reviews", "decisions", "contest"} <= set(snap),
      "snapshot of the committed live run has every part the page draws")
check(snap["kpis"]["reviews"] > 0 and snap["kpis"]["tool_calls"] > 0 and len(snap["brakes"]) == 4,
      f"live snapshot counts AI reviews ({snap['kpis']['reviews']}) and tool calls ({snap['kpis']['tool_calls']})")

snap3 = snapshot(ROOT / "runs" / "shadow_v3")      # state.json keeps only the count of closed trades; the list comes from the log
check(snap3["kpis"]["trades"] == 1 and len(snap3["trades"]) == 1 and snap3["trades"][0]["symbol"] and snap3["trades"][0]["reason"],
      "a run with a closed trade: the snapshot counts it and lists it from the exit events")

tmp = Path(tempfile.mkdtemp())
for name in ("live_main", "shadow_v6"):
    shutil.copytree(ROOT / "runs" / name, tmp / name, ignore=shutil.ignore_patterns("ticks.csv", "STOP", "FLATTEN", "ui_controls.jsonl"))
app = S.App(SimpleNamespace(runs=str(tmp), live_dir=None))

code, out = app.control("live_main", "stop", S.CONFIRM)
check(code == 403 and not (tmp / "live_main" / "STOP").exists(), "real-money run: STOP refused even with the confirmation")
code, out = app.control("live_main", "flatten", S.CONFIRM)
check(code == 403 and not (tmp / "live_main" / "FLATTEN").exists(), "real-money run: FLATTEN refused even with the confirmation")
code, out = app.control("shadow_v6", "stop", "ok")
check(code == 400 and not (tmp / "shadow_v6" / "STOP").exists(), "shadow run: no action without the typed confirmation")
code, out = app.control("shadow_v6", "stop", "تایید")
check(code == 200 and (tmp / "shadow_v6" / "STOP").exists(), "shadow run: STOP works after the confirmation (spelling without hamza too)")
code, out = app.control("shadow_v6", "flatten", S.CONFIRM)
check(code == 200 and (tmp / "shadow_v6" / "FLATTEN").exists(), "shadow run: FLATTEN works after the confirmation")
code, out = app.control("shadow_v6", "resume", S.CONFIRM)
check(code == 200 and not (tmp / "shadow_v6" / "STOP").exists() and not (tmp / "shadow_v6" / "FLATTEN").exists(),
      "shadow run: resume clears both flags")
log = (tmp / "shadow_v6" / "ui_controls.jsonl").read_text(encoding="utf-8").splitlines()
check([json.loads(x)["action"] for x in log] == ["stop", "flatten", "resume"], "every control action is logged")

page = S.clean_page({"title": "t", "answer": "a", "blocks": [{"type": "kpis"}, {"type": "evil_script"}, "x", {"type": "brakes"}] + [{"type": "text", "text": "y" * 2000}] * 9})
check([b["type"] for b in page["blocks"]][:2] == ["kpis", "brakes"] and len(page["blocks"]) <= 7 and all(len(b.get("text", "")) <= 600 for b in page["blocks"]),
      "model page: unknown components dropped, count and text length capped")

srv = ThreadingHTTPServer(("127.0.0.1", 0), S.handler(app))
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}"
req = urllib.request.Request(url + "/api/control", data=json.dumps({"run": "shadow_v6", "action": "stop", "confirm": S.CONFIRM}).encode(),
                             headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=10)
    status = 200
except urllib.error.HTTPError as e:
    status = e.code
check(status == 403 and not (tmp / "shadow_v6" / "STOP").exists(), "a POST without the page's token is refused")
html = urllib.request.urlopen(url + "/", timeout=10).read().decode("utf-8")
check(S.TOKEN in html and "__UI_TOKEN__" not in html, "the page served by this process carries its token")
srv.shutdown()
shutil.rmtree(tmp, ignore_errors=True)

print("all passed" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
