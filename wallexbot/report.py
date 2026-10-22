"""Camera-ready HTML dashboard of one run (live, shadow or test), in Persian, right to left.

    python -m wallexbot.report runs/<run> [--out file.html]      default: dashboard/<run>.html

Strictly read-only towards the run folder: the live bot appends to it while we read, so a partial
last line is skipped and nothing is ever written there. No network: one file, inline CSS and SVG."""
from __future__ import annotations

import argparse
import html
import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ROOT, load_config

TEH = timezone(timedelta(hours=3, minutes=30))
DEADLINE = datetime(2026, 10, 23, 20, 20, tzinfo=timezone.utc)
TICK_HOURS = 6.0
MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
COIN = {"BTC": "بیت‌کوین", "ETH": "اتریوم", "SOL": "سولانا", "XRP": "ریپل", "DOGE": "دوج‌کوین", "PEPE": "پپه",
        "BNB": "بی‌ان‌بی", "ARB": "آربیتروم", "USDT": "تتر"}
EXIT_FA = {"target": ("هدف (فاصله بسته شد)", "good"), "stop": ("حد ضرر", "bad"),
           "time": ("سقف زمان نگه‌داری", "warn"), "flatten": ("تسویه پایان مسابقه", "mute")}
MODE_FA = {"normal": ("عادی", "good"), "defensive": ("دفاعی", "warn"), "halt": ("توقف کامل", "bad")}
EVENT_FA = {"blocked": "مسدود توسط مدیر ریسک", "skip": "رد شد: افت سریع قیمت جهانی", "entry_unfilled": "سفارش خرید پر نشد",
            "exit_unfilled": "سفارش فروش پر نشد", "feed_gap": "قطعی داده قیمت", "dust_left": "باقی‌مانده ناچیز رها شد",
            "adopted_holding": "دارایی جامانده تحویل گرفته شد", "reconciled": "هم‌سان‌سازی با موجودی",
            "position_resized": "اصلاح اندازه موقعیت", "stray_order_cancelled": "لغو سفارش ناشناس",
            "window_closed": "پایان پنجره مسابقه", "loop_error": "خطای حلقه اصلی", "symbol_error": "خطای یک بازار",
            "place_error": "خطای ثبت سفارش", "cancel_error": "خطای لغو سفارش", "order_status_error": "خطای وضعیت سفارش",
            "balance_error": "خطای خواندن موجودی", "stray_check_error": "خطای بررسی سفارش‌های باز",
            "policy_review_error": "خطای بازبینی هوش مصنوعی"}
NOTABLE = {"blocked", "skip", "entry_unfilled", "exit_unfilled", "feed_gap", "dust_left", "adopted_holding", "reconciled",
           "position_resized", "stray_order_cancelled", "window_closed"}


# -- reading (never opens anything in the run folder for writing) ----------------------------------
def _events(path: Path) -> list[dict]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    out = []
    for line in raw.split(b"\n"):
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass            # the live bot may be half way through writing this line
    return out


def _state(path: Path) -> dict:
    for _ in range(4):           # rewritten every minute: retry if caught mid-write
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (ValueError, OSError):
            time.sleep(0.25)
    return {}


def _ticks(path: Path, hours: float = TICK_HOURS) -> dict[str, list[tuple]]:
    """Last `hours` of ticks.csv per symbol as (t, bid, ask, buy_dev), reading backwards from the end."""
    try:
        size = path.stat().st_size
    except OSError:
        return {}
    chunk, rows = 2 << 20, []
    with open(path, "rb") as f:
        while True:
            start = max(0, size - chunk)
            f.seek(start)
            data = f.read(size - start)
            lines = data.split(b"\n")
            if not data.endswith(b"\n"):
                lines = lines[:-1]          # partial last line (still being written)
            if start > 0:
                lines = lines[1:]           # partial first line (we seeked into it)
            rows = []
            for ln in lines:
                c = ln.split(b",")
                if len(c) != 10:
                    continue
                try:
                    rows.append((float(c[0]), c[1].decode(), float(c[2]), float(c[3]), float(c[8])))
                except ValueError:
                    continue                # header
            if start == 0 or (rows and rows[-1][0] - rows[0][0] >= hours * 3600):
                break
            chunk *= 2
    if not rows:
        return {}
    cut = rows[-1][0] - hours * 3600
    out: dict[str, list[tuple]] = {}
    for t, s, bid, ask, bd in rows:
        if t >= cut:
            out.setdefault(s, []).append((t, bid, ask, bd))
    return out


# -- formatting -----------------------------------------------------------------------------------
# U+2013 and U+2014 become a plain hyphen: the page uses plain hyphens, also in quoted model and log text
_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-"})


def _e(x) -> str:
    return html.escape(str(x).translate(_DASHES), quote=True)


def _n(text, cls: str = "") -> str:
    """A number as an isolated left-to-right run, so a minus sign never jumps to the wrong side."""
    return f'<span class="n {cls}">{_e(text)}</span>'


def _pct(v: float, d: int = 2, sign: bool = True) -> str:
    s = f"{abs(v):.{d}f}%"
    r = round(v, d)
    return ("+" + s if sign else s) if r > 0 else ("−" + s if r < 0 else s)


def _px(v: float) -> str:
    v = float(v)
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}" if abs(v) >= 1 else f"{v:.6g}"


def _utc(x) -> datetime:
    if isinstance(x, (int, float)):
        return datetime.fromtimestamp(x, timezone.utc)
    d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    dm = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + dm[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    return (jy, 1 + days // 31, 1 + days % 31) if days < 186 else (jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30)


def _day(x, year: bool = False) -> str:
    d = _utc(x).astimezone(TEH)
    jy, jm, jd = _jalali(d.year, d.month, d.day)
    return f"{jd} {MONTHS[jm - 1]}" + (f" {jy}" if year else "")


def _hm(x) -> str:
    return _utc(x).astimezone(TEH).strftime("%H:%M")


def _when(x, year: bool = False) -> str:
    return f"{_day(x, year)}، {_hm(x)}"


def _dur(sec: float) -> str:
    sec = max(0, int(sec))
    if sec < 60:
        return "کمتر از یک دقیقه"
    d, r = divmod(sec, 86400)
    h, m = r // 3600, (r % 3600) // 60
    parts = ([f"{d} روز"] if d else []) + ([f"{h} ساعت"] if h else []) + ([f"{m} دقیقه"] if m or not (d or h) else [])
    return " و ".join(parts[:2])


def _badge(text: str, kind: str) -> str:
    return f'<span class="bd {kind}"><i></i>{_e(text)}</span>'


def _official(call: dict) -> bool:
    """Official Wallex MCP call (older records have no "server" key: tell by the tool name)."""
    return call.get("server") == "wallex-official" or str(call.get("tool", "")).startswith("wallex_")


# -- inline SVG plot: the SVG stretches, the labels are HTML so they stay readable on a phone -----
def _xticks(t0: float, t1: float, maxn: int = 6) -> list[float]:
    span = t1 - t0
    if span <= 0:
        return [t0]
    for step in (300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400):
        if span / step <= maxn:
            break
    off = 12600                                    # Tehran is UTC+3:30: ticks on round local times
    first = ((int(t0) + off) // step + 1) * step - off
    return list(range(first, int(t1) + 1, step))


def _plot(segs: list[list[tuple]], lo: float, hi: float, *, h: int = 200, t0=None, t1=None, lines=(), zone=None,
          area_to=None, fmt=str, ticks: str = "auto", mark_min: bool = False, cls: str = "") -> str:
    """segs: lists of (t, v) drawn as one broken line. lines: (value, css class, label) reference lines.
    zone: wash below this value. area_to: fill between the line and this value."""
    pts = [p for s in segs for p in s]
    if not pts:
        return '<div class="empty">هنوز داده‌ای برای رسم نیست.</div>'
    t0 = pts[0][0] if t0 is None else t0
    t1 = pts[-1][0] if t1 is None else t1
    st, sv = (t1 - t0) or 1.0, (hi - lo) or 1.0
    X = lambda t: 2 + (t - t0) / st * 96 if t1 > t0 else 50.0
    Y = lambda v: 7 + (hi - v) / sv * 86
    svg = []
    if zone is not None:
        y = Y(zone) * 10
        svg.append(f'<rect x="0" y="{y:.1f}" width="1000" height="{max(0, 1000 - y):.1f}" class="zone"/>')
    for v, c, _ in lines:
        svg.append(f'<line x1="0" x2="1000" y1="{Y(v) * 10:.1f}" y2="{Y(v) * 10:.1f}" class="{c}" vector-effect="non-scaling-stroke"/>')
    d = " ".join("M" + " L".join(f"{X(t) * 10:.1f},{Y(v) * 10:.1f}" for t, v in s) for s in segs if s)
    if area_to is not None:
        for s in segs:
            if s:
                b = Y(area_to) * 10
                svg.append(f'<path class="area" d="M{X(s[0][0]) * 10:.1f},{b:.1f} L' +
                           " L".join(f"{X(t) * 10:.1f},{Y(v) * 10:.1f}" for t, v in s) + f' L{X(s[-1][0]) * 10:.1f},{b:.1f} Z"/>')
    svg.append(f'<path class="ln" d="{d}" vector-effect="non-scaling-stroke"/>')
    lab = [f'<span class="yl {c}" style="top:{Y(v):.2f}%">{lb}</span>' for v, c, lb in lines if lb]
    last = pts[-1]
    lab += [f'<i class="dot pt" style="left:{X(s[0][0]):.2f}%;top:{Y(s[0][1]):.2f}%"></i>' for s in segs if len(s) == 1 and s[0] != last]
    lab.append(f'<i class="dot" style="left:{X(last[0]):.2f}%;top:{Y(last[1]):.2f}%"></i>')
    if mark_min:
        m = min(pts, key=lambda p: p[1])
        lab.append(f'<i class="dot ring" style="left:{X(m[0]):.2f}%;top:{Y(m[1]):.2f}%"></i>')
    if ticks == "ends":
        xs = [(t0, "s"), (t1, "e")] if _hm(t0) != _hm(t1) else [(t1, "e")]
    else:
        tk = _xticks(t0, t1)
        xs = [(t, "s" if X(t) < 10 else "e" if X(t) > 90 else "") for t in tk]
    multi = t1 - t0 > 20 * 3600
    xl, prev = [], None
    for t, a in xs:
        dname = _day(t)
        txt = f"{dname} {_hm(t)}" if multi and dname != prev else _hm(t)
        prev = dname
        xl.append(f'<span class="{a}" style="left:{X(t):.2f}%">{_e(txt)}</span>')
    step = max(1, len(pts) // 400)
    hov = [[round(X(t), 2), round(Y(v), 2), f"{_when(t)} · {fmt(v)}"] for t, v in pts[::step]]
    if hov and hov[-1][0] != round(X(last[0]), 2):
        hov.append([round(X(last[0]), 2), round(Y(last[1]), 2), f"{_when(last[0])} · {fmt(last[1])}"])
    return (f'<div class="plot {cls}" style="height:{h}px" data-h="{_e(json.dumps(hov, ensure_ascii=False))}">'
            f'<svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">{"".join(svg)}</svg>{"".join(lab)}</div>'
            f'<div class="xa">{"".join(xl)}</div>')


def _segments(pts: list[tuple], gap: float) -> list[list[tuple]]:
    segs, cur = [], []
    for p in pts:
        if cur and p[0] - cur[-1][0] > gap:
            segs.append(cur)
            cur = []
        cur.append(p)
    return segs + ([cur] if cur else [])


def _thin(pts: list[tuple], n: int = 1200) -> list[tuple]:
    """Keep the shape (each bucket's min and max) of a long series."""
    if len(pts) <= n:
        return pts
    k, out = len(pts) / (n / 2), []
    for i in range(int(n / 2)):
        b = pts[int(i * k):int((i + 1) * k)]
        if b:
            lo, hi = min(b, key=lambda p: p[1]), max(b, key=lambda p: p[1])
            out += sorted({lo, hi})
    return out


# -- sections ----------------------------------------------------------------------------------
def _section(id_: str, title: str, body: str, intro: str = "") -> str:
    return (f'<section id="{id_}"><h2>{title}</h2>' + (f'<p class="intro">{intro}</p>' if intro else "") + body + "</section>")


def _tile(label: str, value: str, sub: str = "", cls: str = "") -> str:
    return f'<div class="k {cls}"><div class="kl">{label}</div><div class="kv">{value}</div><div class="ks">{sub}</div></div>'


def _split_bar(off: int, eng: int, big: bool = False) -> str:
    tot = off + eng
    if not tot:
        return ""
    a, b = off / tot * 100, eng / tot * 100
    return (f'<div class="split{" big" if big else ""}" role="img" aria-label="MCP رسمی والکس {off}، MCP موتور ربات {eng}">'
            + (f'<i class="s1" style="width:{a:.2f}%" title="MCP رسمی والکس: {off}"></i>' if off else "")
            + (f'<i class="s2" style="width:{b:.2f}%" title="MCP موتور ربات: {eng}"></i>' if eng else "") + "</div>")


def _legend(off: int, eng: int) -> str:
    return (f'<div class="lg"><span><i class="sw s1"></i>MCP رسمی والکس {_n(off)}</span>'
            f'<span><i class="sw s2"></i>MCP موتور ربات {_n(eng)}</span></div>')


def _pol_cells(p: dict, prev: dict | None) -> str:
    """Policy cells of one review, as logged; a cell that differs from the previous review is highlighted.
    entry_dev is shown as logged: the supervisor had already clamped it to the fee floor of the code running at
    that time, which can be lower than today's (live_main: 0.6% before the 17:51:56 UTC start). Applying today's
    floor here would show old reviews at a threshold they never used."""
    eff = lambda q: {"enabled": bool(q.get("enabled", True)), "entry_dev": round(float(q.get("entry_dev", 0)), 6),
                     "stop_pct": float(q.get("stop_pct", 0)), "max_hold_min": float(q.get("max_hold_min", 0)),
                     "size_factor": float(q.get("size_factor", 0))}
    show = lambda k, v: (_badge("فعال", "good") if v else _badge("خاموش", "mute")) if k == "enabled" else         _n(_pct(-v * 100)) if k == "entry_dev" else _n(_pct(v * 100, sign=False)) if k == "stop_pct" else _n(f"{v:g}")
    new, old = eff(p), (eff(prev) if prev else None)
    out = ""
    for k, v in new.items():
        if old is None or old[k] == v:
            out += f"<td>{show(k, v)}</td>"
            continue
        was = re.sub(r"<[^>]+>", "", show(k, old[k]))
        mark = "تغییر" if k == "enabled" else "▲" if v > old[k] else "▼"
        out += f'<td class="chg" title="قبلی: {_e(html.unescape(was))}">{show(k, v)}<sup>{mark}</sup></td>'
    return out


def _code_history(starts: list[dict], reviews: list[dict], last_t, live: bool) -> str:
    """One header line: every start event (Tehran time). Each start loads the code and config on disk at that
    moment, so the starts are the versions of the running code; the AI model of each stretch is its first review's."""
    if not starts:
        return ""
    ts = [_utc(e["t"]) for e in starts]
    items, prev_day = [], None
    for i, t in enumerate(ts):
        nxt = ts[i + 1] if i + 1 < len(ts) else None
        model = next((str(r["analyst_model"]) for r in reviews if r.get("analyst_model") and r.get("t")
                      and _utc(r["t"]) >= t and (nxt is None or _utc(r["t"]) < nxt)), "")
        model = re.sub(r"\s*via MCP.*$", "", model).split("/")[-1]
        day = _day(t)
        label = (f"{_e(day)} " if day != prev_day else "") + _n(_hm(t))
        prev_day = day
        items.append(f'<li{" class=last" if nxt is None else ""}>{label}' + (f"<small>{_e(model)}</small>" if model else "") + "</li>")
    since = (f"<span>از آخرین شروع {_e(_dur((_utc(last_t) - ts[-1]).total_seconds()))} بی‌وقفه</span>"
             if last_t and _utc(last_t) > ts[-1] else "")
    restarts = f"، {_n(len(ts) - 1)} بار راه‌اندازی دوباره" if len(ts) > 1 else ""
    return (f'<div class="hist"><span><b>{"تاریخچه کد زنده" if live else "تاریخچه اجرا"}</b>: {_n(len(ts))} شروع{restarts}'
            f' (به وقت تهران، هر شروع کد و تنظیمات همان لحظه را بار می‌کند)</span><ol>{"".join(items)}</ol>{since}</div>')


def build(run_dir: Path, out: Path | None = None) -> Path:
    run_dir = Path(run_dir).resolve()
    out = Path(out).resolve() if out else (ROOT / "dashboard" / f"{run_dir.name}.html").resolve()
    if out.parent == run_dir or run_dir in out.parents:
        raise SystemExit(f"refusing to write inside the run folder {run_dir}")
    try:
        cfg = load_config().raw
    except Exception:
        cfg = {}
    deadline = _utc(cfg.get("window", {}).get("end_utc", DEADLINE.isoformat()))
    risk = cfg.get("risk", {})
    taker = {"TMN": cfg.get("fees", {}).get("taker", 0.003), "USDT": cfg.get("fees_usdt", {}).get("taker", 0.0013)}
    floor = lambda s: 2 * taker["TMN" if s.endswith("TMN") else "USDT"] + 0.001
    now = datetime.now(timezone.utc)

    ev = _events(run_dir / "decisions.jsonl")
    st = _state(run_dir / "state.json")
    ticks = _ticks(run_dir / "ticks.csv")
    by = lambda name: [e for e in ev if e.get("event") == name]
    starts, eqs, reviews, exits, entries = by("start"), by("equity"), by("policy_review"), by("exit"), by("entry")
    errors = [e for e in ev if str(e.get("event", "")).endswith("error")]
    mode = st.get("mode") or (starts[-1].get("mode") if starts else "shadow")
    is_test = run_dir.name.lower().startswith(("test", "smoke"))
    last_t = st.get("t") or (ev[-1]["t"] if ev else None)
    first_t = ev[0]["t"] if ev else None

    # account
    eq_pts = [(_utc(e["t"]).timestamp(), float(e["equity_tmn"])) for e in eqs if e.get("equity_tmn")]
    equity = float(st.get("equity_tmn") or (eq_pts[-1][1] if eq_pts else 0))
    start_cap = float(cfg.get("live", {}).get("start_capital") or 0) if mode == "live" else 0.0
    start_cap = start_cap or (eq_pts[0][1] if eq_pts else equity)
    ret = float(st.get("return_pct", (equity / start_cap - 1) * 100 if start_cap else 0))
    dd_pts = [(_utc(e["t"]).timestamp(), -float(e.get("drawdown_pct", 0))) for e in eqs if e.get("equity_tmn")]
    peak, mdd = start_cap, 0.0
    for _, v in eq_pts:
        peak = max(peak, v)
        mdd = max(mdd, (1 - v / peak) * 100 if peak else 0)
    mdd = max([mdd] + [-v for _, v in dd_pts])

    # trades: each exit matched to the latest entry (or adopted holding) of the same symbol before it
    opened, trades = {}, []
    for e in ev:
        k = e.get("event")
        if k in ("entry", "adopted_holding", "reconciled"):
            opened[e.get("symbol")] = e
        elif k == "exit":
            trades.append((e, opened.get(e.get("symbol"))))
    wins = [x for x, _ in trades if x.get("pnl_tmn", 0) > 0]
    pnl_sum = sum(x.get("pnl_tmn", 0) for x, _ in trades)
    positions = st.get("positions") or {}

    # AI tool calls
    calls = [c for r in reviews for c in (r.get("analyst_tool_calls") or []) if isinstance(c, dict) and c.get("tool")]
    n_off = sum(1 for c in calls if _official(c))
    n_eng = len(calls) - n_off

    # ---------------------------------------------------------------- header
    left = (deadline - now).total_seconds()
    stale = (now - _utc(last_t)).total_seconds() if last_t else None
    badge = (_badge("آزمایشی · صرافی ساختگی", "warn") if is_test else _badge("زنده · پول واقعی", "live") if mode == "live"
             else _badge("سایه · معامله کاغذی", "mute"))
    hist = _code_history(starts, reviews, last_t, live=mode == "live" and not is_test)
    head = f"""<header class="hd">
<div class="top"><span class="brand">هکاتون Agentic AI استارکوچ · چالش والکس</span>
<button id="theme" type="button" hidden aria-label="تغییر حالت روشن و تیره">روشن / تیره</button></div>
<div class="hero"><div class="ttl"><h1>ربات آربیتراژ قیمت منصفانه والکس</h1><div class="mode">{badge}</div>
<p class="lead">وقتی قیمت خرید یک کوین در والکس به‌وضوح ارزان‌تر از قیمت منصفانه جهانی باشد می‌خرد، و وقتی این فاصله بسته شد می‌فروشد.</p>
<p class="formula">قیمت منصفانه = قیمت جهانی کوین (بایننس) × نرخ تتر به تومان در والکس</p>
<div class="meta"><span>آخرین داده: <b>{_e(_when(last_t)) if last_t else 'ندارد'}</b>{f' ({_e(_dur(stale))} پیش)' if stale is not None else ''}</span>
<span>مدت اجرا: <b>{_e(_dur((_utc(last_t) - _utc(first_t)).total_seconds())) if first_t else 'ندارد'}</b></span>
<span>پوشه اجرا: <b>{_n(run_dir.name)}</b></span></div>{hist}</div>
<div class="clock"><div class="cl">{'تا پایان مسابقه' if left > 0 else 'مسابقه'}</div><div class="cv">{_e(_dur(left)) if left > 0 else 'تمام شد'}</div>
<div class="cs">پایان: {_e(_when(deadline, True))} به وقت تهران<br>توقف خرید تازه {risk.get('no_new_entries_min_before_end', 150)} دقیقه و فروش همه {risk.get('flatten_min_before_end', 75)} دقیقه پیش از پایان</div></div></div>
{'<div class="banner warn">این صفحه از یک اجرای آزمایشی آفلاین با صرافی ساختگی ساخته شده است. هیچ‌کدام از معامله‌های آن واقعی نیست.</div>' if is_test else ''}
{f'<div class="banner bad">آخرین داده {_e(_dur(stale))} پیش ثبت شده است. ربات را بررسی کنید.</div>' if mode == 'live' and not is_test and stale and stale > 300 else ''}
<nav><a href="#perf">عملکرد</a><a href="#trades">معامله‌ها</a><a href="#pos">موقعیت‌های باز</a><a href="#markets">بازارها</a><a href="#ai">هوش مصنوعی</a><a href="#log">رویدادها</a></nav>
</header>"""

    # ---------------------------------------------------------------- KPI tiles
    last_rev = reviews[-1]["t"] if reviews else None
    err_kinds = Counter(e["event"] for e in errors).most_common(2)
    kpis = "".join([
        _tile("ارزش حساب", f"{_n(f'{equity:,.0f}')}<small>تومان</small>", f"سرمایه اولیه {_n(f'{start_cap:,.0f}')}", "hero"),
        _tile("بازده", _n(_pct(ret), "pos" if ret > 0 else "neg" if ret < 0 else ""),
              f"{'▲ ' if pnl_sum > 0 else '▼ ' if pnl_sum < 0 else ''}سود و زیان معامله‌ها {_n(f'{pnl_sum:+,.0f}' if pnl_sum else '0')} تومان"),
        _tile("بیشترین افت", _n(_pct(-mdd) if mdd else "0.00%", "neg" if mdd > 0.005 else ""),
              f"توقف اضطراری در افت {_n(_pct(risk.get('kill_switch_dd', 0.03) * 100, 0, False))}"),
        _tile("معامله‌های بسته‌شده", _n(len(trades)),
              f"میانگین بازده {_n(_pct(sum(x.get('ret_pct', 0) for x, _ in trades) / len(trades), 3))}" if trades else "هنوز معامله‌ای بسته نشده"),
        _tile("نرخ برد", _n(f"{len(wins) / len(trades) * 100:.0f}%") if trades else "ندارد",
              f"{_n(len(wins))} برد از {_n(len(trades))}" if trades else "پس از اولین معامله"),
        _tile("موقعیت باز", f"{_n(len(positions))}<small>از {risk.get('max_positions', 3)}</small>",
              "، ".join(positions) if positions else "پول نقد، منتظر فرصت"),
        _tile("بازبینی هوش مصنوعی", _n(len(reviews)),
              f"هر {cfg.get('fairvalue', {}).get('review_minutes', 30)} دقیقه · آخرین {_n(_hm(last_rev))}" if last_rev else "هنوز بازبینی نشده"),
        _tile("فراخوانی ابزار MCP", _n(len(calls)),
              f"رسمی والکس {_n(n_off)} · موتور ربات {_n(n_eng)}{_split_bar(n_off, n_eng)}"),
        _tile("خطاها", _n(len(errors), "neg" if errors else ""),
              "، ".join(f"{_e(EVENT_FA.get(k, k))} {_n(c)}" for k, c in err_kinds) if errors else "بدون خطا"),
    ])

    # ---------------------------------------------------------------- equity and drawdown
    if eq_pts:
        vals = [v for _, v in eq_pts] + [start_cap]
        lo, hi = min(vals), max(vals)
        pad = (hi - lo) * 0.12 or max(1.0, abs(hi) * 0.002)
        money = lambda v: f"{v:,.0f} تومان"
        eq_lines = [(start_cap, "ref", f"سرمایه اولیه {_n(format(start_cap, ',.0f'))}")]
        dmin, dmax = min(v for _, v in eq_pts), max(v for _, v in eq_pts)
        if dmax != start_cap:
            eq_lines.append((dmax, "grid", f"بیشینه {_n(format(dmax, ',.0f'))}"))
        if dmin != start_cap and dmin != dmax:
            eq_lines.append((dmin, "grid", f"کمینه {_n(format(dmin, ',.0f'))}"))
        if dmin == dmax:
            eq_lines[0] = (start_cap, "ref", eq_lines[0][2] + ("" if dmin == start_cap else f" · ثابت {_n(format(dmin, ',.0f'))}"))
        eq_svg = _plot(_segments(_thin(eq_pts), 300), lo - pad, hi + pad, h=230, lines=eq_lines, fmt=money, cls="eq")
        kill = risk.get("kill_switch_dd", 0.03) * 100
        dlo = -max(mdd, kill) * 1.12
        dd_lines = [(0, "grid", _n("0%")), (-kill, "bad", f"توقف اضطراری {_n(_pct(-kill, 1))}")]
        if mdd > 0.005:
            dd_lines.append((-mdd, "grid", f"بیشترین افت {_n(_pct(-mdd))}"))
        dd_svg = _plot(_segments(_thin(dd_pts), 300), dlo, max(0.05 * kill, 0.05), h=150, lines=dd_lines,
                       fmt=lambda v: _pct(v), area_to=0, cls="dd")
    else:
        eq_svg = dd_svg = '<div class="empty">هنوز هیچ ارزش‌گذاری حسابی ثبت نشده است.</div>'
    perf = (f'<div class="card"><h3>ارزش حساب (تومان)</h3>{eq_svg}</div>'
            f'<div class="card"><h3>افت از اوج (%)</h3>{dd_svg}</div>')

    # ---------------------------------------------------------------- trades
    rows = []
    for x, op in reversed(trades):
        t_exit = _utc(x["t"]).timestamp()
        t_in = _utc(op["t"]).timestamp() if op else t_exit - x.get("held_s", 0)
        why = (op.get("why_fa") if op and op.get("event") == "entry" else
               "دارایی جامانده در حساب که ربات پس از راه‌اندازی دوباره تحویل گرفت" if op else "")
        rfa, rk = EXIT_FA.get(x.get("reason"), (x.get("reason", ""), "mute"))
        r = x.get("ret_pct", 0)
        rows.append(f"<tr><td><b>{_n(_hm(t_in))}</b><small>{_e(_day(t_in))}</small></td><td><b>{_e(x.get('symbol'))}</b></td>"
                    f"<td>{_n(_px(x.get('entry', 0)))}</td><td>{_n(_px(x.get('exit', 0)))}</td>"
                    f"<td>{_n(_pct(x.get('entry_dev_pct', op.get('buy_dev_pct', 0) if op else 0)))}</td><td>{_badge(rfa, rk)}</td>"
                    f"<td>{_n(format(x.get('held_s', 0) / 60, '.1f'))}</td><td>{_n(_pct(r, 3), 'pos' if r > 0 else 'neg' if r < 0 else '')}</td>"
                    f"<td>{_n(format(x.get('pnl_tmn', 0), '+,.0f'), 'pos' if x.get('pnl_tmn', 0) > 0 else 'neg' if x.get('pnl_tmn', 0) < 0 else '')}</td>"
                    f"<td class='why'>{_e(why)}</td></tr>")
    if trades:
        mix = Counter(x.get("reason") for x, _ in trades)
        mean = sum(x.get("ret_pct", 0) for x, _ in trades) / len(trades)
        chips = "".join(f'<span class="chip">{_badge(EXIT_FA.get(k, (k, "mute"))[0], EXIT_FA.get(k, (k, "mute"))[1])} {_n(c)}</span>'
                        for k, c in mix.most_common())
        tfoot = (f"<tfoot><tr><td colspan='7'>جمع {_n(len(trades))} معامله · برد {_n(len(wins))}</td>"
                 f"<td>{_n(_pct(mean, 3), 'pos' if mean > 0 else 'neg')}</td><td>{_n(f'{pnl_sum:+,.0f}', 'pos' if pnl_sum > 0 else 'neg')}</td><td></td></tr></tfoot>")
        trades_html = (f'<div class="mix">دلیل خروج: {chips}</div><div class="scroll"><table class="tt"><thead><tr><th>زمان ورود</th><th>بازار</th>'
                       f'<th>قیمت ورود</th><th>قیمت خروج</th><th>انحراف ورود</th><th>دلیل خروج</th><th>مدت (دقیقه)</th><th>بازده</th>'
                       f'<th>سود و زیان (تومان)</th><th>دلیل ورود</th></tr></thead><tbody>{"".join(rows)}</tbody>{tfoot}</table></div>')
    else:
        trades_html = ('<div class="empty big">هنوز معامله‌ای بسته نشده است. ربات فقط وقتی می‌خرد که ارزانی والکس از کارمزد رفت و برگشت '
                       'و یک حاشیه امن بیشتر باشد؛ بخش «بازارها» نشان می‌دهد هر بازار چقدر به این آستانه نزدیک شده است.</div>')

    # ---------------------------------------------------------------- open positions
    pols = st.get("policies") or (reviews[-1].get("policies") if reviews else None) or {}
    if not pols and starts:
        dp = starts[-1].get("policy", {})
        pols = {s: dp.get("TMN" if s.endswith("TMN") else "USDT", {}) for s in starts[-1].get("symbols", [])}
    prows = []
    st_t = _utc(last_t).timestamp() if last_t else time.time()
    for s, p in positions.items():
        pol = pols.get(s, {})
        bid = ticks.get(s, [(0, 0, 0, 0)])[-1][1] or None
        ch = (bid / p["entry"] - 1) * 100 if bid and p.get("entry") else None
        opened_t = float(p.get("opened", st_t))
        stop = p.get("entry", 0) * (1 - pol.get("stop_pct", 0.009))
        prows.append(f"<tr><td><b>{_e(s)}</b></td><td><b>{_n(_hm(opened_t))}</b><small>{_e(_dur(st_t - opened_t))} پیش</small></td>"
                     f"<td>{_n(_px(p.get('entry', 0)))}</td><td>{_n(format(p.get('qty', 0), '.8g'))}</td><td>{_n(format(p.get('cost_q', 0), ',.0f'))}</td>"
                     f"<td>{_n(_pct(p.get('entry_dev', 0) * 100))}</td><td>{_n(_px(bid)) if bid else 'ندارد'}</td>"
                     f"<td>{_n(_pct(ch, 3), 'pos' if ch > 0 else 'neg') if ch is not None else 'ندارد'}</td><td>{_n(_px(stop))}</td>"
                     f"<td>{_n(_hm(opened_t + pol.get('max_hold_min', 25) * 60))}</td></tr>")
    pos_html = (f'<div class="scroll"><table><thead><tr><th>بازار</th><th>زمان ورود</th><th>قیمت ورود</th><th>مقدار</th><th>هزینه خرید</th>'
                f'<th>انحراف ورود</th><th>آخرین قیمت خریدار</th><th>تغییر از ورود</th><th>قیمت حد ضرر</th><th>خروج اجباری</th></tr></thead>'
                f'<tbody>{"".join(prows)}</tbody></table></div>' if positions else
                '<div class="empty">هیچ موقعیت بازی نیست؛ همه سرمایه نقد است و منتظر فرصت بعدی.</div>')

    # ---------------------------------------------------------------- markets
    base = st.get("baselines_pct") or {}
    cards, near = [], []          # near: (gap to threshold, symbol, lowest tick, threshold, enabled now)
    span_txt = ""
    is_on = lambda s: bool(pols[s].get("enabled", True))
    order = sorted(pols, key=lambda s: not is_on(s))      # enabled markets first; config order kept inside each group
    for s in order:
        p = pols[s]
        on = is_on(s)
        thr = max(float(p.get("entry_dev", 0)), floor(s))
        rows_s = ticks.get(s, [])
        mins: dict[int, tuple] = {}
        for t, bid, ask, bd in rows_s:
            m = int(t // 60)
            if m not in mins or bd < mins[m][1]:
                mins[m] = (m * 60 + 30, bd)
        series = [(t, max(min(v * 100, 1.5), -5.0)) for t, v in sorted(mins.values())]
        stats = ""
        if rows_s:
            lo_r = min(rows_s, key=lambda r: r[3])
            lt = rows_s[-1]
            gap = (lo_r[3] + thr) * 100
            below = len({int(t // 60) for t, _, _, bd in rows_s if bd <= -thr})
            near.append((gap, s, lo_r, thr, on))
            span_txt = _dur(rows_s[-1][0] - rows_s[0][0])
            lo_v = min(min(v for _, v in series), -thr * 100) - 0.12
            hi_v = max(max(v for _, v in series), 0.15) + 0.08
            chart = _plot([series], lo_v, hi_v, h=120, zone=-thr * 100, ticks="ends", mark_min=True, cls="mini",
                          lines=[(0, "grid", _n("0%")), (-thr * 100, "thr", f"آستانه خرید {_n(_pct(-thr * 100))}")], fmt=lambda v: _pct(v))
            stats = (f'<dl><div><dt>نزدیک‌ترین لحظه</dt><dd>{_n(_pct(lo_r[3] * 100))}<small>{_e(_when(lo_r[0]))}</small></dd></div>'
                     f'<div><dt>فاصله تا آستانه</dt><dd>{_n(_pct(max(gap, 0), sign=False)) if gap > 0 else "رسید"}'
                     f'<small>{f"{below} دقیقه زیر آستانه" if below else "هنوز نرسیده"}</small></dd></div>'
                     f'<div><dt>انحراف فعلی</dt><dd>{_n(_pct(lt[3] * 100))}<small>اسپرد {_n(_pct((lt[2] / lt[1] - 1) * 100 if lt[1] else 0, sign=False))}</small></dd></div>'
                     f'<div><dt>پریمیوم عادی</dt><dd>{_n(_pct(base[s])) if s in base else "ندارد"}<small>میانه ۳ ساعت</small></dd></div></dl>')
        else:
            chart = '<div class="empty">در این اجرا تیکی برای این بازار ثبت نشده است.</div>'
        coin = s[:-3] if s.endswith("TMN") else s[:-4]
        quote = "تومان" if s.endswith("TMN") else "تتر"
        cards.append(f'<article class="mk {"on" if on else "off"}"><div class="mh"><div><b>{_e(s)}</b><span>{_e(COIN.get(coin, coin))} / {quote}</span></div>'
                     f'{_badge("فعال", "good") if on else _badge("خاموش", "mute")}</div>'
                     + ("" if on else '<div class="offnote">در سیاست فعلی خاموش است: ربات در این بازار خرید تازه نمی‌کند، '
                                      'حتی اگر انحراف به آستانه برسد.</div>')
                     + f'{chart}{stats}'
                     f'<div class="pol">آستانه {_n(_pct(-thr * 100))} · حد ضرر {_n(_pct(p.get("stop_pct", 0) * 100, sign=False))} · '
                     f'نگه‌داری تا {_n(format(p.get("max_hold_min", 0), "g"))} دقیقه · ضریب اندازه {_n(format(p.get("size_factor", 0), "g"))}</div>'
                     f'<p class="why">{_e(p.get("reason", ""))}</p></article>')
    gap_txt = lambda g: "رسید" if g <= 0 else f"فاصله {_n(_pct(g, sign=False))}"
    near_on = sorted(n for n in near if n[4])
    near_off = sorted(n for n in near if not n[4])
    callout = ""
    if near:
        span = f"{_e(span_txt)} اخیر"
        if near_on:
            hit = [n for n in near_on if n[0] <= 0]
            g, s0, r0, thr0, _ = near_on[0]
            body = ((f'در {span} انحراف خرید {_n(len(hit))} بازار از {_n(len(near_on))} بازار فعال به آستانه ورود رسید.' if hit else
                     f'در {span} انحراف خرید هیچ‌کدام از {_n(len(near_on))} بازار فعال به آستانه ورود نرسید.')
                    + f' نزدیک‌ترین بازار فعال <b>{_e(s0)}</b> بود با {_n(_pct(r0[3] * 100))} در {_e(_when(r0[0]))} (آستانه {_n(_pct(-thr0 * 100))}).'
                    + '<ol class="rank">' + "".join(f'<li><b>{_e(s)}</b> کمترین {_n(_pct(r[3] * 100))} · {gap_txt(g)}</li>'
                                                    for g, s, r, _, _ in near_on) + '</ol>')
        else:
            body = 'در سیاست فعلی هیچ بازاری فعال نیست، پس ربات اصلاً خرید تازه نمی‌کند.'
        off = ""
        if near_off:
            off = (f'<div class="offl">{_badge("خاموش", "mute")} این بازارها در سیاست فعلی خاموش‌اند و در رتبه‌بندی بالا حساب نشده‌اند: '
                   + "".join(f'<span class="it"><b>{_e(s)}</b> کمترین {_n(_pct(r[3] * 100))} · '
                             f'{"رسید، ولی خاموش است" if g <= 0 else gap_txt(g)}</span>' for g, s, r, _, _ in near_off) + '</div>')
        callout = (f'<div class="callout"><b>{"چرا هنوز خریدی انجام نشده؟" if not entries else "فرصت‌ها در برابر آستانه"}</b> '
                   + body + off
                   + '<p class="fl">کف آستانه همیشه کارمزد رفت و برگشت به‌علاوه ۰٫۱ درصد حاشیه است، تا هیچ خریدی از اول ضررده نباشد.</p></div>')
    on_syms = [s for s in order if is_on(s)]
    off_syms = [s for s in order if not is_on(s)]
    onoff = (f'<div class="onoff"><span>{_badge(f"فعال {len(on_syms)}", "good")} {"، ".join(_n(s) for s in on_syms) or "هیچ"}</span>'
             f'<span>{_badge(f"خاموش {len(off_syms)}", "mute")} {"، ".join(_n(s) for s in off_syms) or "هیچ"}</span></div>')
    legend = ('<div class="lg"><span><i class="sw line"></i>کمترین انحراف خرید در هر دقیقه</span><span><i class="sw zone"></i>ناحیه خرید (زیر آستانه)</span>'
              '<span><i class="sw ring"></i>نزدیک‌ترین لحظه</span></div>')
    markets_html = (callout + onoff + legend + f'<div class="mks">{"".join(cards)}</div>' if cards else
                    '<div class="empty">هنوز سیاستی برای بازارها ثبت نشده است.</div>')

    # ---------------------------------------------------------------- AI reviews
    tool_n = Counter((("o" if _official(c) else "e"), c["tool"]) for c in calls)
    mx = max(tool_n.values()) if tool_n else 1
    bars = "".join(f'<div class="br"><span class="bn">{_e(t)}</span><span class="bt"><i class="{"s1" if k == "o" else "s2"}" '
                   f'style="width:{n / mx * 100:.1f}%"></i></span><span class="bc">{_n(n)}</span></div>' for (k, t), n in tool_n.most_common())
    ai_top = (f'<div class="card aisum"><div class="aik"><div><b>{_n(len(reviews))}</b>بازبینی سیاست</div><div><b>{_n(len(calls))}</b>فراخوانی ابزار</div>'
              f'<div><b>{_n(len(tool_n))}</b>ابزار متفاوت</div></div>{_split_bar(n_off, n_eng, True)}{_legend(n_off, n_eng)}'
              f'<div class="two"><p><i class="sw s1"></i><b>MCP رسمی والکس</b> (api.wallex.ir/mcp): دفتر سفارش، معاملات اخیر و کندل‌ها مستقیم از خود والکس. '
              f'فقط چهار ابزار عمومی و فقط‌خواندنی در اختیار مدل است و هیچ کلید API فرستاده نمی‌شود.</p>'
              f'<p><i class="sw s2"></i><b>MCP موتور ربات</b> (stdio): قیمت منصفانه جهانی، آمار پریمیوم، بازپخش آستانه روی تیک‌های زنده، '
              f'بک‌تست و وضعیت ریسک. همه فقط‌خواندنی.</p></div>'
              + (f'<h3>ابزارهایی که ایجنت صدا زد</h3><div class="bars">{bars}</div>' if bars else "") + "</div>")
    revs, prev_pol = [], None
    rev_html = []
    for r in reviews:
        rev_html.append((r, prev_pol))
        prev_pol = r.get("policies") or prev_pol
    for i, (r, prevp) in enumerate(reversed(rev_html)):
        tr = [c for c in (r.get("analyst_tool_calls") or []) if isinstance(c, dict)]
        tc = [c for c in tr if c.get("tool")]
        o = sum(1 for c in tc if _official(c))
        mname, mk = MODE_FA.get(r.get("risk_mode"), (r.get("risk_mode") or "نامشخص", "mute"))
        rp = r.get("policies") or {}
        n_on = sum(1 for p in rp.values() if p.get("enabled"))
        cnt = Counter((_official(c), c["tool"]) for c in tc)
        def chips(off):
            return "".join(f'<span class="tchip {"s1" if off else "s2"}">{_e(t)} <b>×{n}</b></span>' for (k, t), n in cnt.most_common() if k == off)
        seq = "".join(f'<li><span class="tchip {"s1" if _official(c) else "s2"}">{_e(c["tool"])}</span>'
                      f'<code>{_e(json.dumps(c.get("args") or {}, ensure_ascii=False)[:160])}</code>'
                      f'<small>{_e(str(c.get("result", ""))[:220])}</small></li>' for c in tc)
        errs = [c.get("agent_error") for c in tr if c.get("agent_error")]
        prow = "".join(f'<tr{"" if p.get("enabled", True) else " class=offr"}><td><b>{_e(s)}</b></td>{_pol_cells(p, (prevp or {}).get(s))}'
                       f'<td class="why">{_e(p.get("reason", ""))}</td></tr>' for s, p in rp.items())
        body = (f'<p class="sum">{_e(r.get("summary_fa") or "")}</p>'
                + (f'<p class="en" dir="ltr">{_e(r["analyst_summary"])}</p>' if r.get("analyst_summary") else "")
                + (f'<div class="banner bad">بازبینی ناموفق بود؛ سیاست‌ها با اندازه کوچک‌تر حفظ شد: <span dir="ltr">{_e(r["error"])}</span></div>' if r.get("error") else "")
                + (f'<div class="banner warn">ایجنت ابزارمحور خطا داد و تحلیل تک‌مرحله‌ای جایگزین شد: <span dir="ltr">{_e(errs[0])}</span></div>' if errs else "")
                + f'<div class="rm"><span>مدل تحلیلگر: <code dir="ltr">{_e(r.get("analyst_model") or "ثبت نشده")}</code></span></div>'
                + (f'{_split_bar(o, len(tc) - o)}{_legend(o, len(tc) - o)}<div class="tg"><h4>MCP رسمی والکس</h4>{chips(True) or "<span class=mutetxt>فراخوانی نشد</span>"}'
                   f'<h4>MCP موتور ربات</h4>{chips(False) or "<span class=mutetxt>فراخوانی نشد</span>"}</div>'
                   f'<details class="seq"><summary>ترتیب فراخوانی‌ها ({_n(len(tc))})</summary><ol>{seq}</ol></details>' if tc else "")
                + (f'<h4>سیاست نتیجه برای هر بازار</h4><div class="scroll"><table class="pt"><thead><tr><th>بازار</th><th>وضعیت</th><th>آستانه</th><th>حد ضرر</th>'
                   f'<th>نگه‌داری (دقیقه)</th><th>ضریب اندازه</th><th>دلیل</th></tr></thead><tbody>{prow}</tbody></table></div>' if rp else ""))
        revs.append(f'<details class="rev"{" open" if i == 0 else ""}><summary><span class="rt">{_e(_when(r["t"]))}</span>{_badge(mname, mk)}'
                    f'<span class="rs">{_n(len(tc))} ابزار: والکس {_n(o)} · موتور {_n(len(tc) - o)}</span>'
                    f'<span class="rs">{_n(n_on)} از {_n(len(rp))} بازار فعال</span></summary><div class="rb">{body}</div></details>')
    ai_html = ai_top + ("".join(revs) if revs else '<div class="empty">هنوز بازبینی هوش مصنوعی انجام نشده است.</div>')

    # ---------------------------------------------------------------- events
    def detail(e):
        k = e.get("event")
        why = e.get("why")
        why = "; ".join(map(str, why)) if isinstance(why, list) else why
        bits = []
        if "buy_dev" in e:
            bits.append(f"انحراف خرید {_n(_pct(float(e['buy_dev']) * 100))}")
        if why:
            bits.append(f'<span dir="ltr">{_e(why)}</span>' if k != "skip" else "قیمت جهانی در ۶۰ ثانیه اخیر تند افت کرده بود")
        if k == "feed_gap":
            bits.append(f"والکس {_n(e.get('wallex', 0))} بازار · منبع جهانی {_n(e.get('global_source', ''))} · {_n(e.get('prices', 0))} قیمت")
        if "error" in e:
            bits.append(f'<span dir="ltr">{_e(str(e["error"])[:220])}</span>')
        if k in ("entry_unfilled",):
            bits.append(f"قیمت فروشنده {_n(_px(e.get('ask', 0)))}")
        if k in ("dust_left", "adopted_holding", "position_resized", "reconciled"):
            bits.append(f"مقدار {_n(format(e.get('qty', 0), '.8g'))}" + (f" · ارزش {_n(format(e.get('value', 0), ','))} تومان" if "value" in e else ""))
        return " · ".join(bits)
    notable = [e for e in ev if e.get("event") in NOTABLE or str(e.get("event", "")).endswith("error")][-50:]
    erows = "".join(f'<tr><td><b>{_n(_hm(e["t"]))}</b><small>{_e(_day(e["t"]))}</small></td>'
                    f'<td>{_badge(EVENT_FA.get(e["event"], e["event"]), "bad" if e["event"].endswith("error") else "warn")}</td>'
                    f'<td>{_e(e.get("symbol", ""))}</td><td class="why">{detail(e)}</td></tr>' for e in reversed(notable))
    srows, warm = [], None
    for e in ev:
        k = e.get("event")
        if k == "warm_start":
            warm = e
            continue
        if k not in ("start", "stop", "window_closed"):
            continue
        txt = (f"شروع در حالت {'زنده' if e.get('mode') == 'live' else 'سایه'} با {_n(len(e.get('symbols', [])))} بازار"
               + (f"؛ خط پایه از {_n(format(warm.get('ticks', 0), ','))} تیک قبلی گرم شد" if warm else "") if k == "start" else
               f"توقف ({_n(e.get('trades', 0))} معامله)" if k == "stop" else "پایان پنجره مسابقه")
        warm = None
        srows.append(f'<li><b>{_e(_when(e["t"]))}</b> {txt}</li>')
    n_restart = max(0, len(starts) - 1)
    log_html = (f'<details open class="card"><summary><h3>هشدارها، خطاها و ردشده‌ها{" (آخرین " + _n(len(notable)) + ")" if notable else ""}</h3></summary>'
                + (f'<div class="scroll"><table><thead><tr><th>زمان</th><th>رویداد</th><th>بازار</th><th>جزئیات</th></tr></thead><tbody>{erows}</tbody></table></div>'
                   if notable else '<div class="empty">هیچ هشدار، خطا یا معامله ردشده‌ای ثبت نشده است.</div>') + "</details>"
                + f'<details class="card"{" open" if len(srows) <= 12 else ""}><summary><h3>شروع‌ها و راه‌اندازی‌های دوباره ({_n(len(starts))} شروع'
                + (f'، {_n(n_restart)} بار راه‌اندازی دوباره' if n_restart else "") + ')</h3></summary>'
                + (f'<ul class="runs">{"".join(srows[-40:])}</ul>' if srows else '<div class="empty">رویداد شروعی در این اجرا ثبت نشده است.</div>') + "</details>")

    # trade agent (trader.py): the model decides each entry once a start says it is on
    decs = by("trade_decision")
    agent_on = bool(decs) or any(s.get("trade_agent") for s in starts[-1:])
    act_fa = {"buy": "خرید شد", "skip": "خرید نشد", "expired": "جواب دیر رسید", "candidate gone": "فرصت تا جواب گذشته بود"}
    drows = "".join(
        f'<tr><td><b>{_n(_hm(d["t"]))}</b><small>{_e(_day(d["t"]))}</small></td><td dir="ltr">{_e(d.get("symbol", ""))}</td>'
        f'<td>{"بخر" if d.get("decision") == "buy" else "نخر"}</td><td>{_e(act_fa.get(d.get("action"), d.get("action", "")))}</td>'
        f'<td>{_n(_pct(d["buy_dev_pct_asked"], 2, False)) if d.get("buy_dev_pct_asked") is not None else "-"}</td>'
        f'<td>{_n(format(d["latency_s"], ".1f")) if d.get("latency_s") is not None else "-"}</td>'
        f'<td>{_e(d.get("reason_fa") or d.get("error") or "")}</td></tr>' for d in reversed(decs[-60:]))
    n_buy = sum(1 for d in decs if d.get("action") == "buy")
    agent_html = ((f'<div class="mix">{_n(len(decs))} فرصت به مدل رسید؛ مدل {_n(sum(1 for d in decs if d.get("decision") == "buy"))} بار گفت بخر و '
                   f'{_n(n_buy)} خرید انجام شد.</div><div class="scroll"><table><thead><tr><th>زمان</th><th>بازار</th><th>تصمیم مدل</th>'
                   f'<th>نتیجه</th><th>ارزانی</th><th>زمان پاسخ (ثانیه)</th><th>دلیل مدل</th></tr></thead><tbody>{drows}</tbody></table></div>')
                  if decs else '<div class="empty">عامل معامله روشن است؛ هنوز فرصتی به آستانه نرسیده که مدل درباره‌اش تصمیم بگیرد.</div>')
    role = ("<b>مدل زبانی سیاست هر بازار را تعیین می‌کند و هر خرید را هم خودش تصمیم می‌گیرد؛ سفارش را کد ثبت می‌کند</b>"
            if agent_on else "<b>مدل زبانی فقط سیاست هر بازار را تعیین می‌کند؛ همه سفارش‌ها را کد ثبت می‌کند</b>")
    foot = (f'<footer><p>ساخته‌شده در {_e(_when(now, True))} به وقت تهران · منبع: <span dir="ltr">runs/{_e(run_dir.name)}</span> · '
            f'{_n(len(ev))} رویداد در لاگ تصمیم‌ها</p><p>{role}، '
            f'در چارچوب قوانین سخت ریسک که هوش مصنوعی نمی‌تواند دورشان بزند.</p></footer>')

    title = f"داشبورد ربات والکس · {run_dir.name}"
    page = (f'<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{_e(title)}</title>'
            f'<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;700;800&display=swap" rel="stylesheet">'
            f'<style>{CSS}</style></head><body><div class="w">{head}'
            f'<div class="kpis">{kpis}</div>'
            + _section("perf", "عملکرد حساب", f'<div class="perf">{perf}</div>')
            + _section("trades", "معامله‌ها", trades_html, "جدیدترین در بالا. بازده و سود پس از کسر هر دو کارمزد است.")
            + _section("pos", "موقعیت‌های باز", pos_html)
            + _section("markets", "بازارها و فاصله تا آستانه خرید", markets_html,
                       f"سیاست فعلی هر بازار و {span_txt or 'ساعت‌های'} اخیر انحراف قیمت خرید والکس از قیمت منصفانه، پس از کم کردن پریمیوم عادی همان بازار. "
                       "ربات فقط در بازارهای فعال، و فقط وقتی می‌خرد که خط وارد ناحیه سبز شود. بازارهای خاموش آخر فهرست و کم‌رنگ آمده‌اند.")
            + _section("ai", "نقش هوش مصنوعی: بازبینی سیاست با دو سرور MCP", ai_html,
                       "ایجنت تحلیلگر با دو سرور MCP داده جمع می‌کند و برای هر بازار سیاست پیشنهاد می‌دهد؛ افسر ریسک فقط می‌تواند سخت‌گیرتر کند "
                       "و کد هر عدد را به بازه مجاز می‌بندد. "
                       + ("هر خرید را عامل معامله (بخش بعد) تصمیم می‌گیرد و خروج‌ها با موتور است." if agent_on
                          else "ورود و خروج در چند ثانیه را موتور انجام می‌دهد، نه مدل زبانی."))
            + (_section("agent", "عامل معامله: مدل هر خرید را تصمیم می‌گیرد", agent_html,
                        "موتور هر ۲٫۵ ثانیه فرصت پیدا می‌کند و یک پرونده از همان لحظه برای مدل می‌فرستد؛ مدل می‌گوید بخرد یا نه و چرا. "
                        "خرید فقط با جواب «بخر» تازه و وقتی فرصت هنوز هست انجام می‌شود. جدیدترین در بالا.") if agent_on else "")
            + _section("log", "رویدادها", log_html)
            + f"{foot}</div><script>{JS}</script></body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(page, encoding="utf-8")
    tmp.replace(out)               # atomic: an open browser tab never reloads half a file
    return out


CSS = """
:root{--bg:#f3f5f4;--card:#fff;--card2:#f7f9f8;--fg:#111816;--fg2:#3a4541;--mut:#66726d;--line:#e1e7e4;--grid:#d9e0dc;
--acc:#0b7a5c;--accw:rgba(11,122,92,.11);--pos:#0a7a3c;--neg:#c0392b;--negw:rgba(192,57,43,.10);--warn:#9a5b00;--warnw:rgba(250,178,25,.18);
--live:#d03b3b;--s1:#2a78d6;--s2:#eb6834;--ln:#36413d;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0b100e;--card:#141b18;--card2:#18201d;--fg:#e8eeeb;--fg2:#c4cdc9;
--mut:#93a09a;--line:#25302b;--grid:#2c3833;--acc:#3fc79c;--accw:rgba(63,199,156,.13);--pos:#3dd07f;--neg:#f2867d;--negw:rgba(242,134,125,.13);
--warn:#f0b343;--warnw:rgba(240,179,67,.14);--live:#ff6b6b;--s1:#3987e5;--s2:#d95926;--ln:#c9d3cf;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0b100e;--card:#141b18;--card2:#18201d;--fg:#e8eeeb;--fg2:#c4cdc9;--mut:#93a09a;--line:#25302b;--grid:#2c3833;
--acc:#3fc79c;--accw:rgba(63,199,156,.13);--pos:#3dd07f;--neg:#f2867d;--negw:rgba(242,134,125,.13);--warn:#f0b343;--warnw:rgba(240,179,67,.14);
--live:#ff6b6b;--s1:#3987e5;--s2:#d95926;--ln:#c9d3cf;color-scheme:dark}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font-family:Vazirmatn,Tahoma,"Segoe UI",sans-serif;font-size:16px;line-height:1.7;direction:rtl}
.w{max-width:1200px;margin:0 auto;padding:20px max(16px,env(safe-area-inset-right)) 48px max(16px,env(safe-area-inset-left));display:flex;flex-direction:column;gap:26px}
.n{direction:ltr;unicode-bidi:isolate;display:inline-block}
.pos{color:var(--pos)}.neg{color:var(--neg)}
h1{font-size:clamp(1.55rem,4.2vw,2.5rem);line-height:1.3;margin:0;font-weight:800;letter-spacing:-.01em}
h2{font-size:clamp(1.25rem,2.6vw,1.6rem);margin:0 0 6px;font-weight:800}
h3{font-size:1.05rem;margin:0 0 10px;font-weight:700}
h4{font-size:.92rem;margin:14px 0 6px;color:var(--fg2)}
section{display:flex;flex-direction:column;gap:12px;scroll-margin-top:12px}
.intro{margin:0;color:var(--mut);max-width:80ch}
.card,.k,.mk,.rev,.callout,.empty{background:var(--card);border:1px solid var(--line);border-radius:14px}
.card{padding:16px 18px}
.hd{display:flex;flex-direction:column;gap:14px}
.top{display:flex;justify-content:space-between;align-items:center;gap:10px;color:var(--mut);font-size:.9rem}
#theme{white-space:nowrap;font:inherit;font-size:.85rem;color:var(--fg2);background:var(--card);border:1px solid var(--line);border-radius:999px;padding:4px 14px;cursor:pointer}
.hero{display:grid;grid-template-columns:1fr auto;gap:18px;align-items:stretch}
.ttl{display:flex;flex-direction:column;gap:8px;min-width:0}
.mode{display:flex}
.lead{margin:0;font-size:1.08rem;color:var(--fg2);max-width:70ch}
.formula{margin:0;font-size:.9rem;color:var(--mut)}
.meta{display:flex;flex-wrap:wrap;gap:6px 20px;font-size:.9rem;color:var(--mut)}.meta b{color:var(--fg)}
.clock{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 20px;min-width:250px;display:flex;flex-direction:column;justify-content:center}
.cl{color:var(--mut);font-size:.9rem}.cv{font-size:clamp(1.5rem,3.4vw,2.1rem);font-weight:800;line-height:1.35}.cs{color:var(--mut);font-size:.8rem;line-height:1.6;margin-top:4px}
nav{display:flex;flex-wrap:wrap;gap:8px}
nav a{color:var(--fg2);text-decoration:none;font-size:.88rem;border:1px solid var(--line);background:var(--card);border-radius:999px;padding:3px 13px}
nav a:hover{border-color:var(--acc);color:var(--acc)}
.banner{border-radius:12px;padding:10px 14px;font-size:.95rem;overflow-wrap:anywhere}
.banner.warn{background:var(--warnw);border:1px solid var(--warn)}.banner.bad{background:var(--negw);border:1px solid var(--neg)}
.bd{display:inline-flex;align-items:center;gap:6px;font-size:.85rem;font-weight:700;border-radius:999px;padding:2px 11px;border:1px solid var(--line);background:var(--card2);white-space:nowrap}
.bd i{width:8px;height:8px;border-radius:50%;background:var(--mut);flex:none}
.bd.good i{background:var(--pos)}.bd.bad i{background:var(--neg)}.bd.warn i{background:#e09a00}.bd.mute i{background:var(--mut)}
.bd.live{border-color:var(--live);color:var(--live);font-size:.95rem;padding:3px 14px}.bd.live i{background:var(--live);animation:pulse 1.6s ease-in-out infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(208,59,59,.55)}50%{box-shadow:0 0 0 7px rgba(208,59,59,0)}}
@media (prefers-reduced-motion:reduce){.bd.live i{animation:none}}
.kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
@media (min-width:620px){.kpis{grid-template-columns:repeat(4,minmax(0,1fr))}.k.hero{grid-column:span 4}}
@media (min-width:1000px){.kpis{grid-template-columns:repeat(6,minmax(0,1fr))}.k.hero{grid-column:span 2;grid-row:span 2;justify-content:center}}
.k{padding:14px 16px;display:flex;flex-direction:column;gap:2px;min-width:0}
.k.hero{grid-column:span 2;background:linear-gradient(135deg,var(--accw),transparent 70%),var(--card)}
.kl{color:var(--mut);font-size:.88rem}
.kv{font-size:clamp(1.6rem,3.3vw,2.25rem);font-weight:800;line-height:1.25}.k.hero .kv{font-size:clamp(2rem,4.6vw,3rem)}
.kv small{font-size:.9rem;font-weight:500;color:var(--mut);margin-inline-start:6px}
.ks{color:var(--mut);font-size:.8rem;line-height:1.6}
.perf{display:grid;gap:12px}
.plot{position:relative;direction:ltr;touch-action:pan-y}
.plot svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.plot .ln{fill:none;stroke:var(--acc);stroke-width:2.2;stroke-linejoin:round;stroke-linecap:round}
.plot.dd .ln{stroke:var(--neg)}.plot.mini .ln{stroke:var(--ln);stroke-width:1.6}
.plot .area{fill:var(--accw);stroke:none}.plot.dd .area{fill:var(--negw)}
.plot .zone{fill:var(--accw)}
.plot line{stroke:var(--grid);stroke-width:1}.plot line.ref{stroke:var(--mut)}.plot line.bad{stroke:var(--neg)}.plot line.thr{stroke:var(--acc);stroke-width:1.5}
.yl{position:absolute;left:4px;transform:translateY(-100%);font-size:.75rem;color:var(--mut);background:var(--card);padding:0 4px;border-radius:4px;line-height:1.5;white-space:nowrap;direction:rtl;pointer-events:none}
.yl.thr{color:var(--acc);font-weight:700}.yl.bad{color:var(--neg)}
.dot{position:absolute;width:10px;height:10px;margin:-5px 0 0 -5px;border-radius:50%;background:var(--acc);box-shadow:0 0 0 2px var(--card);pointer-events:none}
.dot.pt{width:6px;height:6px;margin:-3px 0 0 -3px;box-shadow:none}.dd .dot{background:var(--neg)}.mini .dot{background:var(--ln);width:8px;height:8px;margin:-4px 0 0 -4px}
.dot.ring{background:var(--card);box-shadow:0 0 0 2px var(--neg);width:9px;height:9px;margin:-4.5px 0 0 -4.5px}
.xa{position:relative;height:22px;direction:ltr;font-size:.75rem;color:var(--mut)}
.xa span{position:absolute;top:2px;transform:translateX(-50%);white-space:nowrap;direction:rtl}.xa span.s{transform:none}.xa span.e{transform:translateX(-100%)}
.plot .vl{position:absolute;top:0;bottom:0;width:1px;background:var(--mut);display:none;pointer-events:none}
.plot .hd2{position:absolute;width:10px;height:10px;margin:-5px 0 0 -5px;border-radius:50%;background:var(--fg);box-shadow:0 0 0 2px var(--card);display:none;pointer-events:none}
.plot .tip{position:absolute;top:-6px;transform:translate(-50%,-100%);background:var(--fg);color:var(--bg);font-size:.78rem;padding:3px 9px;border-radius:6px;white-space:nowrap;display:none;direction:rtl;pointer-events:none;z-index:2}
.plot.on .vl,.plot.on .hd2,.plot.on .tip{display:block}
.empty{padding:18px;color:var(--mut);text-align:center}.empty.big{font-size:1.02rem;line-height:1.9}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:14px;background:var(--card)}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th{font-weight:700;color:var(--mut);font-size:.8rem;background:var(--card2);white-space:nowrap}
td,th{padding:9px 10px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top}
tbody tr:last-child td{border-bottom:none}
td .n{font-variant-numeric:tabular-nums;white-space:nowrap}
td small{display:block;color:var(--mut);font-size:.75rem}
tfoot td{font-weight:700;background:var(--card2);border-top:1px solid var(--line);border-bottom:none}
td.why{min-width:220px;color:var(--fg2);font-size:.85rem;overflow-wrap:anywhere}
.tt td:nth-child(2){white-space:nowrap}
.mix{display:flex;flex-wrap:wrap;align-items:center;gap:8px;color:var(--mut);font-size:.9rem}
.chip{display:inline-flex;align-items:center;gap:6px}
.callout{padding:14px 16px;border-inline-start:4px solid var(--acc);line-height:1.9}
.lg{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:.85rem;color:var(--mut);align-items:center}
.lg span{display:inline-flex;align-items:center;gap:6px}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;flex:none;vertical-align:middle}
.sw.s1{background:var(--s1)}.sw.s2{background:var(--s2)}.sw.line{height:2px;background:var(--ln)}.sw.zone{background:var(--accw);border:1px solid var(--acc)}
.sw.ring{border-radius:50%;width:10px;height:10px;border:2px solid var(--neg)}
.mks{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.mk{padding:14px 14px 12px;display:flex;flex-direction:column;gap:8px;min-width:0}
.mk.on{border-inline-start:4px solid var(--pos)}
.mk.off{background:var(--card2);border-style:dashed}.mk.off .plot,.mk.off .xa,.mk.off dl{opacity:.55;filter:grayscale(1)}
.mk.off .mh b{color:var(--mut)}
.offnote{font-size:.8rem;color:var(--fg2);background:var(--card);border:1px dashed var(--mut);border-radius:8px;padding:4px 10px}
.onoff{display:flex;flex-wrap:wrap;gap:8px 22px;align-items:center;font-size:.9rem;color:var(--fg2)}
.onoff>span{display:inline-flex;flex-wrap:wrap;align-items:center;gap:6px}
.callout .rank{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0;padding:0;list-style:none;counter-reset:r}
.callout .rank li{counter-increment:r;font-size:.85rem;background:var(--card2);border:1px solid var(--line);border-radius:8px;padding:1px 10px}
.callout .rank li::before{content:counter(r) ". ";color:var(--mut);font-weight:700}
.callout .offl{margin-top:8px;font-size:.85rem;color:var(--mut);display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px}
.callout .offl .it{white-space:nowrap;border:1px dashed var(--line);border-radius:8px;padding:0 8px}
.callout .fl{margin:6px 0 0;font-size:.85rem;color:var(--mut)}
.hist{display:flex;flex-wrap:wrap;align-items:center;gap:4px 10px;font-size:.85rem;color:var(--mut)}.hist b{color:var(--fg2)}
.hist ol{display:flex;flex-wrap:wrap;gap:6px;margin:0;padding:0;list-style:none}
.hist li{display:inline-flex;align-items:baseline;gap:6px;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:0 10px;white-space:nowrap;color:var(--fg2)}
.hist li small{font-size:.72rem;color:var(--mut);direction:ltr;unicode-bidi:isolate}
.hist li.last{border-color:var(--acc);color:var(--fg)}
.pt tr.offr td:not(.chg){background:var(--card2);color:var(--mut)}
.mh{display:flex;justify-content:space-between;align-items:center;gap:8px}
.mh b{font-size:1.15rem;margin-inline-end:8px}.mh span{color:var(--mut);font-size:.85rem}
.mk dl{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0}
.mk dl div{background:var(--card2);border-radius:10px;padding:6px 10px;min-width:0}
.mk dt{font-size:.75rem;color:var(--mut)}.mk dd{margin:0;font-weight:800;font-size:1.05rem}.mk dd small{display:block;font-weight:400;font-size:.72rem;color:var(--mut)}
.mk .pol{font-size:.8rem;color:var(--fg2)}
.mk .why{margin:0;font-size:.82rem;color:var(--mut);line-height:1.7}
.split{display:flex;gap:2px;height:8px;margin-top:6px;border-radius:4px;overflow:hidden}.split.big{height:16px;margin:12px 0 8px;border-radius:5px}
.split i{display:block;height:100%}.split .s1{background:var(--s1)}.split .s2{background:var(--s2)}
.aisum{display:flex;flex-direction:column;gap:6px}
.aik{display:flex;flex-wrap:wrap;gap:10px 34px}.aik div{color:var(--mut);font-size:.88rem}.aik b{display:block;color:var(--fg);font-size:2rem;font-weight:800;line-height:1.2}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:6px}.two p{margin:0;font-size:.88rem;color:var(--fg2);line-height:1.8}.two .sw{margin-inline-end:6px}
.bars{display:flex;flex-direction:column;gap:6px}
.br{display:grid;grid-template-columns:minmax(120px,240px) 1fr 36px;gap:10px;align-items:center}
.bn{direction:ltr;text-align:right;font-size:.8rem;font-family:ui-monospace,Consolas,monospace;overflow-wrap:anywhere;color:var(--fg2)}
.bt{height:14px;display:block}.bt i{display:block;height:100%;border-radius:4px 0 0 4px;min-width:3px}.bt i.s1{background:var(--s1)}.bt i.s2{background:var(--s2)}
.bc{font-size:.85rem;font-weight:700}
.rev>summary{list-style:none;cursor:pointer;padding:12px 16px;display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px}
.rev>summary::-webkit-details-marker{display:none}
.rev>summary::before{content:"◂";color:var(--mut);transition:transform .15s}.rev[open]>summary::before{transform:rotate(-90deg)}
.rt{font-weight:800}.rs{color:var(--mut);font-size:.88rem}
.rb{padding:0 16px 16px;display:flex;flex-direction:column;gap:6px;min-width:0}
.sum{margin:0;font-size:1.05rem;line-height:1.9}
.en{margin:0;color:var(--mut);font-size:.85rem;text-align:left;line-height:1.6;font-family:"Segoe UI",system-ui,sans-serif}
.rm{font-size:.85rem;color:var(--mut)}
code{font-family:ui-monospace,Consolas,monospace;font-size:.8rem;overflow-wrap:anywhere}
.tg{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.tg h4{flex-basis:100%;margin:8px 0 0}
.tchip{display:inline-flex;align-items:center;gap:4px;direction:ltr;font-family:ui-monospace,Consolas,monospace;font-size:.78rem;border-radius:7px;padding:2px 8px;
border:1px solid var(--line);background:var(--card2);overflow-wrap:anywhere}
.tchip.s1{border-inline-start:4px solid var(--s1)}.tchip.s2{border-inline-start:4px solid var(--s2)}
.mutetxt{color:var(--mut);font-size:.85rem}
.seq{margin-top:6px}.seq summary{cursor:pointer;color:var(--fg2);font-size:.9rem}
.seq ol{margin:8px 0 0;padding-inline-start:22px;display:flex;flex-direction:column;gap:8px}
.seq li{font-size:.82rem}.seq code{display:block;color:var(--fg2);direction:ltr;text-align:left;margin-top:2px}
.seq small{display:block;color:var(--mut);direction:ltr;text-align:left;font-size:.72rem;overflow-wrap:anywhere;font-family:ui-monospace,Consolas,monospace}
.pt td.chg{background:var(--warnw);white-space:nowrap}.pt sup{font-size:.7rem;color:var(--warn);margin-inline-start:4px;font-weight:700}
details.card>summary{cursor:pointer;list-style:none}details.card>summary::-webkit-details-marker{display:none}
details.card>summary h3{display:inline;margin:0}details.card>summary::before{content:"◂ ";color:var(--mut)}details.card[open]>summary::before{content:"▾ "}
details.card[open]>summary{margin-bottom:10px}
.runs{margin:0;padding-inline-start:20px;font-size:.88rem;color:var(--fg2);display:flex;flex-direction:column;gap:3px}
footer{border-top:1px solid var(--line);padding-top:14px;color:var(--mut);font-size:.85rem}footer p{margin:4px 0}footer b{color:var(--fg2)}
@media (max-width:760px){.hero{grid-template-columns:1fr}.clock{min-width:0}.two{grid-template-columns:1fr}.br{grid-template-columns:minmax(0,1.3fr) 1fr 30px}
body{font-size:15px}.card{padding:14px}}
"""

JS = """
(function(){
var b=document.getElementById('theme');if(b){b.hidden=false;b.onclick=function(){var d=document.documentElement,t=d.getAttribute('data-theme');
var dark=t?t==='dark':matchMedia('(prefers-color-scheme: dark)').matches;d.setAttribute('data-theme',dark?'light':'dark');};}
document.querySelectorAll('.plot[data-h]').forEach(function(p){var pts=JSON.parse(p.getAttribute('data-h')||'[]');if(!pts.length)return;
var vl=document.createElement('i'),hd=document.createElement('i'),tip=document.createElement('div');vl.className='vl';hd.className='hd2';tip.className='tip';
p.appendChild(vl);p.appendChild(hd);p.appendChild(tip);
p.addEventListener('pointermove',function(e){var r=p.getBoundingClientRect(),x=(e.clientX-r.left)/r.width*100,b=pts[0];
for(var i=1;i<pts.length;i++){if(Math.abs(pts[i][0]-x)<Math.abs(b[0]-x))b=pts[i];}
vl.style.left=hd.style.left=b[0]+'%';hd.style.top=b[1]+'%';tip.textContent=b[2];tip.style.left=Math.min(Math.max(b[0],15),85)+'%';p.classList.add('on');});
p.addEventListener('pointerleave',function(){p.classList.remove('on');});});
})();
"""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wallexbot.report", description="HTML dashboard of a run (read-only towards the run folder)")
    ap.add_argument("run_dir")
    ap.add_argument("--out", default=None, help="output .html (default: dashboard/<run>.html)")
    a = ap.parse_args(argv)
    print(build(Path(a.run_dir), Path(a.out) if a.out else None))


if __name__ == "__main__":
    main()
