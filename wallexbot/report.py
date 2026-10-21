"""Static HTML report of a run (shadow or live): equity, drawdown, every trade with its reason,
and every AI policy review. `python -m wallexbot.report runs/<run>` writes runs/<run>/report.html."""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from .journal import read


def _svg_line(points: list[tuple[str, float]], w=760, h=180, color="#0d6b55") -> str:
    if len(points) < 2:
        return "<p>هنوز داده کافی نیست.</p>"
    ys = [p[1] for p in points]
    lo, hi = min(ys), max(ys)
    span = (hi - lo) or 1
    step = w / (len(points) - 1)
    pts = " ".join(f"{i * step:.1f},{h - (y - lo) / span * (h - 20) - 10:.1f}" for i, (_, y) in enumerate(points))
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img"><polyline fill="none" stroke="{color}" stroke-width="2" points="{pts}"/>'
            f'<text x="4" y="14" class="ax">{hi:,.0f}</text><text x="4" y="{h - 4}" class="ax">{lo:,.0f}</text></svg>')


def build(run_dir: Path) -> Path:
    ev = read(run_dir / "decisions.jsonl")
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8")) if (run_dir / "state.json").exists() else {}
    eq = [(e["t"], e["equity_tmn"]) for e in ev if e["event"] == "equity"]
    entries = {e["symbol"]: e for e in ev if e["event"] == "entry"}
    exits = [e for e in ev if e["event"] == "exit"]
    reviews = [e for e in ev if e["event"] == "policy_review"]
    errors = [e for e in ev if e["event"].endswith("error")]
    wins = [x for x in exits if x["pnl_tmn"] > 0]
    peak, mdd = 0, 0.0
    for _, v in eq:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak if peak else 0)

    rows = []
    for x in exits:
        rows.append(f"<tr><td>{html.escape(x['t'][11:19])}</td><td>{x['symbol']}</td><td>{x['entry_dev_pct']}%</td>"
                    f"<td>{x['reason']}</td><td>{x['held_s'] // 60} دقیقه</td><td class='num {'pos' if x['pnl_tmn'] > 0 else 'neg'}'>{x['ret_pct']}%</td>"
                    f"<td class='num'>{x['pnl_tmn']:,}</td></tr>")
    rev = []
    for r in reviews[-12:][::-1]:
        on = [s for s, p in r.get("policies", {}).items() if p["enabled"]]
        rev.append(f"<li><b>{html.escape(r['t'][11:16])}</b> حالت: {html.escape(r.get('risk_mode', ''))} · بازارهای فعال: {', '.join(on) or 'هیچ'}"
                   f"<br>{html.escape(r.get('summary_fa', ''))}</li>")
    first, last = (eq[0][1], eq[-1][1]) if eq else (0, 0)
    body = f"""<title>گزارش ربات والکس</title>
<style>
:root{{--bg:#f4f6f5;--fg:#16201c;--mut:#5b6a64;--line:#dde4e0;--acc:#0d6b55;--neg:#b3261e;--card:#fff}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#111715;--fg:#e6ece9;--mut:#9aa8a2;--line:#2a3531;--acc:#4fc29f;--neg:#f2867d;--card:#18201d;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#111715;--fg:#e6ece9;--mut:#9aa8a2;--line:#2a3531;--acc:#4fc29f;--neg:#f2867d;--card:#18201d;color-scheme:dark}}
body{{background:var(--bg);color:var(--fg);font-family:Vazirmatn,Tahoma,sans-serif;direction:rtl;margin:0}}
.w{{max-width:900px;margin:0 auto;padding-block:24px 48px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:flex;flex-direction:column;gap:22px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
.k{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}} .k b{{display:block;font-size:1.4rem}}
.chart{{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}} .ax{{fill:var(--mut);font-size:11px}}
table{{width:100%;border-collapse:collapse;background:var(--card)}} td,th{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;font-size:.9rem}}
.num{{font-variant-numeric:tabular-nums;direction:ltr;text-align:left}} .pos{{color:var(--acc)}} .neg{{color:var(--neg)}}
.scroll{{overflow-x:auto}} li{{margin-bottom:8px}}
</style>
<div class="w">
<h1>گزارش ربات والکس · {html.escape(state.get('mode', ''))}</h1>
<div class="kpis">
<div class="k">بازده<b class="{'pos' if last >= first else 'neg'}">{state.get('return_pct', 0)}%</b></div>
<div class="k">بیشترین افت<b>{mdd * 100:.2f}%</b></div>
<div class="k">معامله‌ها<b>{len(exits)}</b></div>
<div class="k">برد<b>{(len(wins) / len(exits) * 100 if exits else 0):.0f}%</b></div>
<div class="k">موقعیت باز<b>{len(state.get('positions', {}))}</b></div>
<div class="k">خطاها<b>{len(errors)}</b></div>
</div>
<h2>ارزش حساب (تومان)</h2>{_svg_line(eq)}
<h2>معامله‌ها</h2><div class="scroll"><table><tr><th>زمان</th><th>بازار</th><th>فاصله ورود</th><th>دلیل خروج</th><th>مدت</th><th>بازده</th><th>سود (تومان)</th></tr>{''.join(rows) or '<tr><td colspan=7>هنوز معامله‌ای بسته نشده</td></tr>'}</table></div>
<h2>بازبینی‌های هوش مصنوعی</h2><ul>{''.join(rev) or '<li>هنوز بازبینی انجام نشده</li>'}</ul>
<p style="color:var(--mut)">به‌روزرسانی: {html.escape(state.get('t', ''))}</p>
</div>"""
    out = run_dir / "report.html"
    out.write_text(body, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
