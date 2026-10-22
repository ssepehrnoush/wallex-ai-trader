"""Public, read-only copy of the control room for GitHub Pages (docs/app/): the same page, a snapshot of the live
run, and the answers the AI generated just now for the suggested questions. No server, no model and no controls
on the public page; free questions work only in the local version, which keeps the AI key on the machine.

  python ui/export_static.py --live-dir ../wallex-bot/runs/live_main
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ui import server as S  # noqa: E402
from ui.data import snapshot  # noqa: E402

QUESTIONS = ["چرا امروز معامله نکرد؟", "ریسک الان چطور است؟", "هوش مصنوعی آخرین بار چه تصمیمی گرفت و با کدام ابزارها؟",
             "کدام بازار به خرید نزدیک‌تر است؟", "اگر ربات یک ماه پیش روشن بود چه می‌شد؟", "ربات چطور تصمیم می‌گیرد بخرد؟"]

ap = argparse.ArgumentParser()
ap.add_argument("--live-dir", required=True)
ap.add_argument("--out", default=str(ROOT / "docs" / "app"))
a = ap.parse_args()
out = Path(a.out)
(out / "fonts").mkdir(parents=True, exist_ok=True)
(out / "img").mkdir(parents=True, exist_ok=True)
app = S.App(SimpleNamespace(runs=str(ROOT / "runs"), live_dir=a.live_dir))
snap = snapshot(app.runs["live_main"])
answers = {}
for q in QUESTIONS:
    r = app.ask("live_main", q)
    answers[q] = {"page": r["page"], "model": r["model"], "latency_s": r["latency_s"], "t": datetime.now(timezone.utc).isoformat()}
    print(f"{r['latency_s']:>5}s {r['model']}  {q}  ->  {[b['type'] for b in r['page']['blocks']]}", flush=True)
(out / "data.json").write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "snapshot": snap, "answers": answers},
                                          ensure_ascii=False), encoding="utf-8")
shutil.copy(S.STATIC / "index.html", out / "index.html")
shutil.copy(S.STATIC / "fonts" / "Vazirmatn.woff2", out / "fonts" / "Vazirmatn.woff2")
shutil.copy(S.STATIC / "fonts" / "Vazirmatn-OFL.txt", out / "fonts" / "Vazirmatn-OFL.txt")
for img in ("month_equity.png", "ideas.png"):
    shutil.copy(S.IMAGES / img, out / "img" / img)
print("written", out)
