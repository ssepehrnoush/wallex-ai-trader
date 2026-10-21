"""Render the RESULTS.md charts as plain SVG (no matplotlib) into docs/img/.

Reads docs/results_stats.json, which scripts/results_stats.py writes from recorded files only.

  python scripts/results_stats.py --cutoff 2026-10-21T18:45:00
  python scripts/make_results_charts.py

Charts:
  docs/img/threshold_sweep.svg   net % per trade by entry threshold (two backtest variants)
  docs/img/per_market_0p7.svg    7-day backtest at 0.7%, per market (two variants)
  docs/img/live_min_dev.svg      live run: deepest buy_dev per market vs the entry threshold
"""
import json
import sys
from html import escape
from pathlib import Path

WT = Path(__file__).resolve().parent.parent
STATS = WT / "docs" / "results_stats.json"
OUT = WT / "docs" / "img"

# light palette (validated: categorical slots 1-2 pass every check on a white surface)
BG = "#ffffff"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
S1 = "#2a78d6"   # blue
S2 = "#eb6834"   # orange
FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif'
TMN8 = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]


def text(x, y, s, size=13, fill=INK2, anchor="start", weight="normal"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}">{escape(str(s))}</text>')


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'font-family=\'{FONT}\' role="img" aria-label="{escape(title)}">\n'
            f'<title>{escape(title)}</title>\n<rect width="{w}" height="{h}" fill="{BG}"/>\n'
            + "\n".join(body) + "\n</svg>\n")


def vbar(x, w, y_base, y_val, color, tip, r=4):
    """Vertical bar: square at the baseline, 4px rounded at the data end."""
    h = abs(y_val - y_base)
    if h < 0.5:
        return f'<rect x="{x:.1f}" y="{y_base - 0.5:.1f}" width="{w:.1f}" height="1" fill="{color}"><title>{escape(tip)}</title></rect>'
    r = min(r, h, w / 2)
    if y_val < y_base:      # positive, grows up
        d = (f"M{x:.1f},{y_base:.1f} V{y_val + r:.1f} Q{x:.1f},{y_val:.1f} {x + r:.1f},{y_val:.1f} "
             f"H{x + w - r:.1f} Q{x + w:.1f},{y_val:.1f} {x + w:.1f},{y_val + r:.1f} V{y_base:.1f} Z")
    else:                   # negative, grows down
        d = (f"M{x:.1f},{y_base:.1f} V{y_val - r:.1f} Q{x:.1f},{y_val:.1f} {x + r:.1f},{y_val:.1f} "
             f"H{x + w - r:.1f} Q{x + w:.1f},{y_val:.1f} {x + w:.1f},{y_val - r:.1f} V{y_base:.1f} Z")
    return f'<path d="{d}" fill="{color}"><title>{escape(tip)}</title></path>'


def hbar(y, h, x_base, x_val, color, tip, r=4):
    """Horizontal bar: square at the baseline, rounded at the data end."""
    w = abs(x_val - x_base)
    if w < 0.5:
        return f'<rect x="{x_base - 0.5:.1f}" y="{y:.1f}" width="1" height="{h:.1f}" fill="{color}"><title>{escape(tip)}</title></rect>'
    r = min(r, w, h / 2)
    if x_val > x_base:
        d = (f"M{x_base:.1f},{y:.1f} H{x_val - r:.1f} Q{x_val:.1f},{y:.1f} {x_val:.1f},{y + r:.1f} "
             f"V{y + h - r:.1f} Q{x_val:.1f},{y + h:.1f} {x_val - r:.1f},{y + h:.1f} H{x_base:.1f} Z")
    else:
        d = (f"M{x_base:.1f},{y:.1f} H{x_val + r:.1f} Q{x_val:.1f},{y:.1f} {x_val:.1f},{y + r:.1f} "
             f"V{y + h - r:.1f} Q{x_val:.1f},{y + h:.1f} {x_val + r:.1f},{y + h:.1f} H{x_base:.1f} Z")
    return f'<path d="{d}" fill="{color}"><title>{escape(tip)}</title></path>'


def legend(x, y, items):
    out, cx = [], x
    for label, color in items:
        out.append(f'<rect x="{cx}" y="{y - 10}" width="12" height="12" rx="3" fill="{color}"/>')
        out.append(text(cx + 18, y, label, 13, INK2))
        cx += 18 + 7.2 * len(label) + 26
    return out


def sgn(v, nd=2):
    return f"{v:+.{nd}f}%"


# -- chart 1 ----------------------------------------------------------------------------------
def chart_sweep(b):
    s8 = b["sweep_8_live_rule"]
    s5 = {k: v["tmn_majors"] for k, v in b["sweep_majors_existing"].items()}
    ths = ["0.3", "0.4", "0.5", "0.6", "0.7", "0.8"]
    W, H = 820, 486
    L, R, T, B = 70, 30, 104, 94
    pw, ph = W - L - R, H - T - B
    lo, hi = -0.30, 0.50
    y = lambda v: T + (hi - v) / (hi - lo) * ph
    body = [text(L, 30, "Entry threshold sweep, 7-day backtest on 1-minute candles", 17, INK, weight="600"),
            text(L, 52, "Mean net % per trade after real fees (two taker legs = 0.6%). n = number of trades.", 13, INK2)]
    body += legend(L, 80, [("8 TMN markets, live exit rule, today's tick spreads", S1),
                           ("5 major TMN markets, existing backtest (fv_sweep)", S2)])
    gw = pw / len(ths)
    cx7 = L + gw * (ths.index("0.7") + 0.5)
    body.append(f'<rect x="{cx7 - gw / 2 + 4:.1f}" y="{T - 6}" width="{gw - 8:.1f}" height="{ph + 12}" rx="6" fill="#f3f2ee"/>')
    body.append(text(cx7, T + ph - 4, "fee floor", 12, INK2, "middle", "600"))
    for v in [-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5]:
        yy = y(v)
        body.append(f'<line x1="{L}" x2="{W - R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{AXIS if v == 0 else GRID}" stroke-width="1"/>')
        body.append(text(L - 8, yy + 4, f"{v:+.1f}%" if v else "0", 12, MUTED, "end"))
    bw, gap = 24, 2
    for i, th in enumerate(ths):
        cx = L + gw * (i + 0.5)
        for j, (ser, col, name) in enumerate([(s8, S1, "8 markets, live rule"), (s5, S2, "5 majors, existing")]):
            x0 = cx - bw - gap / 2 if j == 0 else cx + gap / 2
            if th not in ser:
                body.append(text(x0 + bw / 2, y(0) - 6, "-", 12, MUTED, "middle"))
                continue
            st = ser[th]
            m = st["mean_pct"]
            tip = f"{name} at {th}%: n={st['n']}, mean {m:+.3f}%, sum {st['sum_pct']:+.2f}%, win {st['win_pct']}%"
            yv = y(max(lo, m))
            body.append(vbar(x0, bw, y(0), yv, col, tip))
            ly = yv - 6 if m >= 0 else yv + 14
            body.append(text(x0 + bw / 2, ly, f"{m:+.2f}", 11, INK, "middle"))
        # trade counts under the axis
        n8 = s8.get(th, {}).get("n")
        n5 = s5.get(th, {}).get("n")
        body.append(text(cx, H - B + 22, f"{th}%", 13, INK, "middle", "600"))
        body.append(text(cx, H - B + 40, f"n={n8 if n8 is not None else '-'} / {n5 if n5 is not None else '-'}", 12, MUTED, "middle"))
    body.append(text(L, H - 26, "x axis: entry threshold (buy_dev below fair). n under each threshold: blue / orange.", 11, MUTED))
    body.append(text(L, H - 10, "Sources: research_ultra/sizing_depth/verify_independent/v_trades_base_ff_*.csv and "
                     "runs/backtests/fv_sweep_*/trades.csv", 11, MUTED))
    return svg(W, H, body, "Entry threshold sweep, mean net % per trade")


# -- chart 2 ----------------------------------------------------------------------------------
def chart_markets(b):
    A = b["at_0.7_existing_method"]["per_market"]
    Bm = b["at_0.7_live_rule"]["per_market"]
    W = 820
    L, R, T = 110, 150, 104
    row = 46
    H = T + row * len(TMN8) + 76
    lo, hi = -0.2, 1.0
    pw = W - L - R
    x = lambda v: L + (v - lo) / (hi - lo) * pw
    a_all, b_all = b["at_0.7_existing_method"]["all"], b["at_0.7_live_rule"]["all"]
    body = [text(20, 30, "7-day backtest at the live 0.7% threshold, per market", 17, INK, weight="600"),
            text(20, 52, f"Mean net % per trade. Existing method: {a_all['n']} trades, mean {a_all['mean_pct']:+.3f}%. "
                         f"Live exit rule: {b_all['n']} trades, mean {b_all['mean_pct']:+.3f}%.", 13, INK2)]
    body += legend(20, 80, [("existing method (shadow_v1 spreads, original exit)", S2),
                            ("live exit rule, today's tick spreads", S1)])
    for v in [-0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
        xx = x(v)
        body.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{T - 6}" y2="{T + row * len(TMN8)}" stroke="{AXIS if v == 0 else GRID}" stroke-width="1"/>')
        body.append(text(xx, T + row * len(TMN8) + 18, f"{v:+.1f}%" if v else "0", 12, MUTED, "middle"))
    bh, gap = 16, 2
    for i, s in enumerate(TMN8):
        yc = T + row * i + row / 2
        body.append(text(L - 10, yc + 4, s, 13, INK, "end", "600"))
        for j, (d, col, name) in enumerate([(A, S2, "existing method"), (Bm, S1, "live exit rule")]):
            y0 = yc - bh - gap / 2 if j == 0 else yc + gap / 2
            st = d.get(s, {"n": 0})
            if not st.get("n"):
                body.append(text(x(0) + 6, y0 + 12, "0 trades", 12, MUTED))
                continue
            m = st["mean_pct"]
            tip = f"{s}, {name}: n={st['n']}, mean {m:+.3f}%, sum {st['sum_pct']:+.2f}%, win {st['win_pct']}%"
            body.append(hbar(y0, bh, x(0), x(m), col, tip))
            body.append(text(max(x(m), x(0)) + 6, y0 + 12, f"{m:+.2f}% (n={st['n']})", 12, INK))
    body.append(text(20, H - 28, "Sources: research_ultra/sizing_depth/out/bt_trades_orig_sv1_ff.csv and bt_trades_live_tick_ff.csv.", 11, MUTED))
    body.append(text(20, H - 12, "BTC, SOL, XRP, DOGE of the existing method match runs/backtests/fv_sweep_0.007_0.004 exactly.", 11, MUTED))
    return svg(W, H, body, "Per-market 7-day backtest at 0.7%")


# -- chart 3 ----------------------------------------------------------------------------------
def chart_live(lv, snap):
    bs = lv["by_symbol"]
    pol = (lv.get("state") or {}).get("policies", {})
    W = 820
    L, R, T = 110, 40, 112
    row = 36
    H = T + row * len(TMN8) + 70
    lo, hi = -1.0, 0.0
    pw = W - L - R
    x = lambda v: L + (v - lo) / (hi - lo) * pw
    first = lv["first_tick_utc"][11:16]
    body = [text(20, 30, "Live run: how close each market came to a buy", 17, INK, weight="600"),
            text(20, 52, f"Deepest buy_dev on 2.5 s ticks, {lv['first_tick_utc'][:10]} {first} to {snap[11:16]} UTC "
                         f"({lv['tick_minutes'] / 60:.1f} h). A buy needs buy_dev at or below the threshold.", 13, INK2),
            text(20, 72, f"Ticks at or below -0.7%: 0 in every market. Trades: {lv['state']['trades'] if lv.get('state') else 0}. "
                         f"Equity {lv['equity_last']:,} TMN.", 13, INK2)]
    for v in [-1.0, -0.8, -0.6, -0.4, -0.2, 0.0]:
        xx = x(v)
        body.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{T - 8}" y2="{T + row * len(TMN8)}" stroke="{AXIS if v == 0 else GRID}" stroke-width="1"/>')
        body.append(text(xx, T + row * len(TMN8) + 18, f"{v:.1f}%" if v else "0", 12, MUTED, "middle"))
    # threshold reference lines
    for v in (-0.7, -0.8):
        xx = x(v)
        body.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{T - 14}" y2="{T + row * len(TMN8)}" stroke="{INK2}" stroke-width="1.5"/>')
    body.append(text(x(-0.7) + 5, T - 16, "entry floor -0.7%", 12, INK, "start", "600"))
    body.append(text(x(-0.8) - 5, T - 16, "-0.8% (AI policy for ETH, PEPE, ARB)", 12, INK, "end", "600"))
    bh = 18
    for i, s in enumerate(TMN8):
        yc = T + row * i + row / 2
        st = bs.get(s, {})
        m = st.get("min_buy_dev_pct")
        en = pol.get(s, {}).get("enabled", True)
        body.append(text(L - 10, yc + 4, s, 13, INK, "end", "600"))
        if m is None:
            continue
        tip = f"{s}: deepest buy_dev {m:.3f}% at {st['min_at_utc']} UTC; {st['ticks']} ticks; median spread {st['median_spread_pct']}%"
        body.append(hbar(yc - bh / 2, bh, x(0), x(m), S1, tip))
        note = "" if en else " (now disabled by AI)"
        body.append(text(x(m) - 6, yc + 4, f"{m:.2f}%{note}", 12, INK, "end"))
    body.append(text(20, H - 14, f"Source: runs/live_main/ticks.csv and state.json (snapshot {snap} UTC), via scripts/results_stats.py",
                     11, MUTED))
    return svg(W, H, body, "Live run, deepest buy_dev per market")


def main():
    if not STATS.exists():
        sys.exit("run scripts/results_stats.py first")
    d = json.loads(STATS.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    b = d["backtests"]
    files = {"threshold_sweep.svg": chart_sweep(b),
             "per_market_0p7.svg": chart_markets(b),
             "live_min_dev.svg": chart_live(d["runs"]["live_main"], d["live_snapshot_utc"])}
    for name, s in files.items():
        (OUT / name).write_text(s, encoding="utf-8")
        print("wrote", OUT / name)


if __name__ == "__main__":
    main()
