"""Control room for the Wallex bot: a local web app with a generative UI.

Ask a question in Persian ("why no trade today?", "how is the risk now?") and the AI (BoxAPI first, OpenAI as
fallback, the same client as the bot) answers by composing a page from a fixed set of components. The components
take their numbers from the run's own log and state, never from the model, so the page cannot show an invented
figure.

The bot is only read. The high-risk controls (stop buying, resume, sell everything) exist but are off by default:
they need the "control mode" switch, a typed confirmation, a per-process token, and they work only on paper
(shadow) runs. On a real-money run they are always locked, because the hackathon rules forbid manual trading.

  python ui/server.py --live-dir ../wallex-bot/runs/live_main          # then open http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import json
import secrets as pysecrets
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wallexbot.ai import AICommittee  # noqa: E402
from wallexbot.config import RUNS, load_config, load_secrets  # noqa: E402

from ui.data import kind_of, snapshot  # noqa: E402
from wallexbot.report import _state  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
IMAGES = ROOT / "docs" / "img"
TOKEN = pysecrets.token_urlsafe(24)          # handed only to the page this server serves; required for every POST
CONFIRM = "تأیید"
BLOCKS = {"kpis", "equity_chart", "brakes", "markets", "market_distance", "reviews", "review_tools", "trade_decisions",
          "trades", "restarts", "callout", "steps", "image", "text"}

SYSTEM = """You design one answer page for the control room of an algorithmic trading bot on the Iranian exchange Wallex.
The user asks in Persian. You get the bot's live snapshot and background facts. Reply with JSON only:
{"title": "<short Persian title>",
 "answer": "<2 to 4 plain Persian sentences that answer the question directly, citing numbers from the snapshot>",
 "blocks": [<2 to 6 components, most relevant first>]}
Components (the page fills every number from the real data; you only choose and order them):
 {"type":"kpis","keys":[subset of "equity","return","drawdown","trades","reviews","tool_calls","errors","decisions"]}
 {"type":"equity_chart"}
 {"type":"brakes"}                                   risk brakes and how close each one is
 {"type":"markets","symbols":[...]}                  market cards; [] = all, ordered by closeness to a buy
 {"type":"market_distance","symbols":[...]}          how far each market's deepest discount got vs its buy threshold
 {"type":"reviews","n":1-5}                          the AI policy reviews, newest first
 {"type":"review_tools","index":0}                   which MCP tools the AI agent called in that review
 {"type":"trade_decisions","n":1-20}                 the trade agent's buy or skip decisions
 {"type":"trades"}                                   closed trades
 {"type":"restarts"}                                 when the live process (re)started
 {"type":"image","src":"month_equity"|"ideas"}       month backtest chart, or the comparison of all ideas tested
 {"type":"callout","tone":"info"|"good"|"warn"|"bad","text":"<one Persian sentence>"}
 {"type":"steps","items":["<Persian step>", ...]}    up to 5 short steps, e.g. how a decision is made
 {"type":"text","text":"<short Persian paragraph>"}
Rules: answer only from the snapshot and the facts; if something is unknown, say so. Use Persian digits in text.
Never suggest manual trades or switching off the risk rules; the hackathon forbids manual trading."""

FACTS = [
    "Strategy: fair value = Binance price x Wallex USDT/TMN rate. Buy when the Wallex ask is at least the threshold "
    "below the market's usual premium (3-hour median); sell when the gap closes and both fees are covered.",
    "Fees: 0.3% per side (taker), 0.6% round trip; the code never buys below a 0.7% discount (2 x taker + 0.1%).",
    "AI level 1: every 30 minutes an analyst agent with two MCP servers (official Wallex MCP, 4 read-only tools; the "
    "engine's MCP) sets each market's policy; a risk officer may only tighten it. Level 2: the trade agent decides "
    "every buy (BUY or SKIP with a reason); answers older than 20 s are ignored. Orders are placed by code.",
    "Risk: 0.6% of capital per trade x AI size factor, max 3 positions and 70% of capital, daily loss limit 1.5%, "
    "kill switch at 3% drawdown, contest loss budget 0.4%, gain guard when ahead; no new buys 150 min before the end, "
    "everything sold 75 min before the end (contest ends Friday 1 Aban 23:50 Tehran).",
    "30-day backtest on a 5,000,000 toman account: buying at the signal minute +360,109 toman (+7.2%), one minute "
    "later -166,993 toman with the kill switch on day 4. Live order-book replay (16 h, all markets on): 3 entries, all losses.",
    "Rejected after measuring: 3 technical strategies (-0.64% per trade), trend following, following Binance jumps, "
    "local buying rush, maker orders, 5 improvement ideas; more markets (120 allowed) and dollar/USDT holding "
    "(Fridays the dollar falls 2 of 3 times) were checked too. So the team deliberately kept the bot as it is.",
    "In 22 hours of the live order book only 4 real discounts passed 0.7%, which is why the bot has traded little.",
]


def runs_index(runs_dir: Path, live_dir: Path | None) -> dict[str, Path]:
    out = {}
    for d in sorted(runs_dir.iterdir()) if runs_dir.exists() else []:
        if d.is_dir() and (d / "state.json").exists() and (d / "decisions.jsonl").exists() \
                and not d.name.startswith(("test", "smoke", "backtests", "ai_cache")):
            out[d.name] = d
    if live_dir:
        out["live_main"] = live_dir
    return out


def compact(snap: dict) -> dict:
    """What the model sees: the snapshot without the long series."""
    s = {k: v for k, v in snap.items() if k not in ("equity", "trades", "positions")}
    s["markets"] = [{k: v for k, v in m.items() if k != "spark"} for m in snap["markets"]]
    s["reviews"] = [{k: v for k, v in r.items() if k != "tools"} | {"top_tools": r["tools"][:6]} for r in snap["reviews"][:4]]
    s["equity_first_last"] = [snap["equity"][0], snap["equity"][-1]] if snap["equity"] else []
    return s


def clean_page(resp: dict) -> dict:
    blocks = []
    for b in resp.get("blocks") or []:
        if isinstance(b, dict) and b.get("type") in BLOCKS:
            for k in ("text",):
                if k in b:
                    b[k] = str(b[k])[:600]
            if b["type"] == "steps":
                b["items"] = [str(x)[:200] for x in (b.get("items") or [])][:5]
            blocks.append(b)
    return {"title": str(resp.get("title") or "")[:120], "answer": str(resp.get("answer") or "")[:900], "blocks": blocks[:7]}


class App:
    def __init__(self, a):
        self.cfg = load_config().raw
        self.runs = runs_index(Path(a.runs), Path(a.live_dir).resolve() if a.live_dir else None)
        cache = RUNS / "ai_cache_ui"
        cache.mkdir(parents=True, exist_ok=True)
        self.ai = AICommittee(self.cfg["ai"], load_secrets(), cache)
        self.lock = threading.Lock()

    def control_status(self, run: str) -> dict:
        d = self.runs[run]
        kind = kind_of(d, _state(d / "state.json"))
        locked = kind != "shadow"
        why = ("روی حساب پول واقعی (حساب هکاتون) همیشه قفل است: قانون چالش معاملهٔ دستی را ممنوع کرده و همهٔ تصمیم‌ها باید از الگوریتم باشد."
               if kind == "live" else "فقط روی اجرای سایه (بدون پول) کار می‌کند." if locked else
               "اجرای سایه است: با روشن کردن حالت کنترل و نوشتن «تأیید» کار می‌کند. هر فرمان در ui_controls.jsonl همین اجرا ثبت می‌شود.")
        return {"run": run, "kind": kind, "locked": locked, "why": why,
                "stop": (d / "STOP").exists(), "flatten": (d / "FLATTEN").exists()}

    def control(self, run: str, action: str, confirm: str) -> tuple[int, dict]:
        stt = self.control_status(run)
        if stt["locked"]:
            return 403, {"ok": False, "error": stt["why"]}
        norm = lambda x: x.replace("أ", "ا").replace("ي", "ی").replace("‌", "").strip()   # «تایید» counts too
        if norm(confirm) != norm(CONFIRM):
            return 400, {"ok": False, "error": "برای اجرای فرمان باید «تأیید» را بنویسید."}
        d = self.runs[run]
        if action == "stop":
            (d / "STOP").touch()
        elif action == "flatten":
            (d / "FLATTEN").touch()
        elif action == "resume":
            for f in ("STOP", "FLATTEN"):
                (d / f).unlink(missing_ok=True)
        else:
            return 400, {"ok": False, "error": "فرمان ناشناخته"}
        with open(d / "ui_controls.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(), "action": action, "by": "ui"}, ensure_ascii=False) + "\n")
        return 200, {"ok": True, **self.control_status(run)}

    def ask(self, run: str, question: str) -> dict:
        snap = snapshot(self.runs[run])
        payload = {"question": question[:400], "snapshot": compact(snap), "facts": FACTS}
        resp, model, latency = self.ai.ask_fast("ui_designer", SYSTEM, payload, timeout_s=75)
        page = clean_page(resp if isinstance(resp, dict) else {})
        if not page["blocks"]:
            page["blocks"] = [{"type": "kpis", "keys": ["equity", "return", "drawdown", "trades"]}, {"type": "brakes"}]
        return {"page": page, "model": model, "latency_s": round(latency, 1), "snapshot": snap}


def handler(app: App):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def run_arg(self, q):
            run = (q.get("run") or ["live_main"])[0]
            return run if run in app.runs else next(iter(app.runs), None)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__UI_TOKEN__", TOKEN)
                return self.send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if u.path.startswith("/fonts/") or u.path.startswith("/img/"):
                base = STATIC / "fonts" if u.path.startswith("/fonts/") else IMAGES
                f = (base / Path(u.path).name)
                if f.exists():
                    ct = "font/woff2" if f.suffix == ".woff2" else "image/png" if f.suffix == ".png" else "image/jpeg"
                    return self.send(200, f.read_bytes(), ct)
                return self.send(404, {"error": "not found"})
            if u.path == "/api/runs":
                out = []
                for name, d in app.runs.items():
                    st = _state(d / "state.json")
                    out.append({"run": name, "kind": kind_of(d, st), "updated": st.get("t")})
                return self.send(200, out)
            run = self.run_arg(q)
            if run is None:
                return self.send(404, {"error": "no runs"})
            if u.path == "/api/snapshot":
                return self.send(200, snapshot(app.runs[run]))
            if u.path == "/api/controls":
                return self.send(200, app.control_status(run))
            return self.send(404, {"error": "not found"})

        def do_POST(self):
            if self.headers.get("X-UI-Token") != TOKEN:
                return self.send(403, {"error": "bad token"})
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            run = body.get("run") if body.get("run") in app.runs else None
            if run is None:
                return self.send(404, {"error": "unknown run"})
            u = urlparse(self.path)
            if u.path == "/api/ask":
                try:
                    return self.send(200, app.ask(run, str(body.get("question") or "")))
                except Exception as e:      # the model or the network failed: the page says so
                    return self.send(502, {"error": f"هوش مصنوعی جواب نداد: {type(e).__name__}"})
            if u.path == "/api/control":
                code, out = app.control(run, str(body.get("action")), str(body.get("confirm") or ""))
                return self.send(code, out)
            return self.send(404, {"error": "not found"})
    return H


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--runs", default=str(RUNS), help="folder of runs (default: this repo's runs/)")
    ap.add_argument("--live-dir", default=None, help="the live bot's run folder, read only, shown as live_main")
    a = ap.parse_args(argv)
    app = App(a)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), handler(app))
    print(f"control room: http://127.0.0.1:{a.port}  runs: {', '.join(app.runs)}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
