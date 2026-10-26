#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""月度实盘回测报告（2025 年至今，按月拆解）

按**当前生产口径**（etf_backtest_long.LIVE_COMPOSITE = 基础轮动 + D3 + P3 + 抄底）
从 --start 起回测，输出一份独立 HTML：
  · 汇总卡（累计收益 / 年化 / 最大回撤 / Calmar / 换仓笔数 / 抄底次数）
  · 月度收益柱状图（红涨绿跌）
  · 净值曲线（策略 vs 上证指数）
  · 月度明细表（月末持仓 / 月收益 / 月内最大回撤 / 换仓 / 抄底 / 同期上证 / 超额）

用法：
  python make_monthly_report.py                      # 默认 2025-01-01 起，写 out/monthly_2025_2026.html
  python make_monthly_report.py --start 2024-01-01 --out docs/monthly.html
  python make_monthly_report.py --no-open            # 定时任务/后台用（不自动打开）
"""
import argparse
import html
import os
import subprocess
import sys
import webbrowser
from typing import Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import etf_backtest_long as bl  # noqa: E402

CSV = os.path.join(HERE, "tdx_adj_long_2019_2026.csv")
COST = 0.0001                      # 单边万一，与日报一致
RED, GREEN, BLUE, GREY = "#d4380d", "#0f9d58", "#3d57e5", "#9aa3ae"
BG, FG, MUT, BD = "#eef0f3", "#202020", "#6c717f", "#e2e5ea"
BENCH = "000001"                   # 上证指数


# ---------------------------------------------------------------- 月度切分
def monthly_stats(navs: pd.Series, trades: list, weak_events: list,
                  bench: Optional[pd.Series]) -> list:
    """按月切分，返回 [(标签, 月收益%, 月内MDD%, 换仓数, 抄底数, 月末持仓, 上证月收益%, 超额pp)]"""
    out = []
    if len(navs) < 2:
        return out
    base = float(navs.iloc[0])
    # 逐月切
    grp = navs.groupby([navs.index.year, navs.index.month])
    tdf = pd.DataFrame(trades) if trades else pd.DataFrame(columns=["date"])
    wdf = pd.DataFrame(weak_events) if weak_events else pd.DataFrame(columns=["date"])
    for (yy, mm), s in grp:
        if s.empty:
            continue
        prev = out[-1]["nav_end"] if out else base
        nav_end = float(s.iloc[-1])
        ret = (nav_end / prev - 1.0) * 100.0
        mdd = float((s / s.cummax() - 1.0).min()) * 100.0
        _p = f"{yy:04d}-{mm:02d}"
        n_tr = int((tdf["date"].str.startswith(_p)).sum()) if len(tdf) else 0
        n_wk = int((wdf["date"].str.startswith(_p)).sum()) if len(wdf) else 0
        # 月末持仓 = 截至该月末的最后一笔换仓的 to（跨月继承，
        #   否则「本月没换仓」的月份会显示成「—」）
        hold = "—"
        if len(tdf):
            _end = s.index[-1].strftime("%Y-%m-%d")
            sub = tdf[tdf["date"] <= _end]
            if len(sub):
                hold = str(sub.iloc[-1].get("to") or "—")
        br = None
        if bench is not None and len(bench):
            bs = bench[(bench.index.year == yy) & (bench.index.month == mm)]
            if len(bs):
                bp = out[-1]["bench_end"] if out and out[-1].get("bench_end") else (
                    float(bench.iloc[0]) if len(bench) else None)
                if bp:
                    br = (float(bs.iloc[-1]) / bp - 1.0) * 100.0
        out.append(dict(
            label=_p, year=yy, month=mm, ret=ret, mdd=mdd,
            trades=n_tr, weak=n_wk, hold=hold,
            bench=br, alpha=(ret - br) if br is not None else None,
            nav_end=nav_end,
            bench_end=(float(bs.iloc[-1]) if br is not None else None),
        ))
    return out


# ---------------------------------------------------------------- SVG 图
def bar_chart(ms: list, w: int = 680, h: int = 230) -> str:
    """月度收益柱状图：红涨绿跌，0 轴居中。"""
    if not ms:
        return ""
    ml, mr, mt, mb = 46, 8, 14, 46
    iw, ih = w - ml - mr, h - mt - mb
    vals = [m["ret"] for m in ms]
    vmax = max(max(vals), 0.0) * 1.12 or 1.0
    vmin = min(min(vals), 0.0) * 1.12 or -1.0
    span = (vmax - vmin) or 1.0
    zero_y = mt + ih * (vmax / span)
    n = len(ms)
    bw = iw / n
    cw = bw * 0.62
    p = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" '
         f'aria-label="月度收益柱状图">']
    # 网格
    for gv in (vmax, vmax / 2, 0.0, vmin / 2, vmin):
        gy = mt + ih * ((vmax - gv) / span)
        p.append(f'<line x1="{ml}" y1="{gy:.1f}" x2="{w-mr}" y2="{gy:.1f}" '
                 f'stroke="{"#c9d0d9" if abs(gv) < 1e-9 else "#e6eaf0"}" '
                 f'stroke-width="{1 if abs(gv) < 1e-9 else 1}" stroke-dasharray="'
                 f'{"" if abs(gv) < 1e-9 else "3 3"}"/>')
        p.append(f'<text x="{ml-6}" y="{gy+3.5:.1f}" text-anchor="end" '
                 f'font-size="9.5" fill="#8b93a1">{gv:+.0f}%</text>')
    for i, m in enumerate(ms):
        v = m["ret"]
        cx = ml + bw * i + bw / 2
        bh = abs(v) / span * ih
        by = zero_y - bh if v >= 0 else zero_y
        col = RED if v >= 0 else GREEN
        p.append(f'<rect x="{cx-cw/2:.1f}" y="{by:.1f}" width="{cw:.1f}" '
                 f'height="{max(bh,0.8):.1f}" fill="{col}" rx="1.5"/>')
        # 年份分隔 + 标签（跨年处加粗）
        is_jan = (m["month"] == 1)
        if is_jan or i == 0:
            p.append(f'<line x1="{ml+bw*i:.1f}" y1="{mt}" x2="{ml+bw*i:.1f}" '
                     f'y2="{mt+ih}" stroke="#c9d0d9" stroke-dasharray="2 2"/>')
        p.append(f'<text x="{cx:.1f}" y="{mt+ih+16}" text-anchor="end" '
                 f'font-size="9" fill="{"#202020" if is_jan else "#8b93a1"}" '
                 f'transform="rotate(-42 {cx:.1f} {mt+ih+16})">'
                 f'{m["year"]%100:02d}-{m["month"]:02d}</text>')
    p.append(f'<text x="{ml}" y="{h-6}" font-size="9.5" fill="#8b93a1">'
             f'每月收益（红涨绿跌，已含换仓成本）</text>')
    p.append("</svg>")
    return "".join(p)


def curve_chart(navs: pd.Series, bench: Optional[pd.Series],
                w: int = 680, h: int = 220) -> str:
    """净值曲线：策略 vs 上证，起点均归一化为 1.0。"""
    if len(navs) < 2:
        return ""
    ml, mr, mt, mb = 46, 8, 16, 30
    iw, ih = w - ml - mr, h - mt - mb
    s = navs / float(navs.iloc[0])
    ys = [s.values]
    cols = [BLUE]
    names = ["策略"]
    if bench is not None and len(bench) > 2:
        b = bench.reindex(navs.index).ffill()
        b = b / float(b.iloc[0])
        if np.isfinite(b.values).all() and len(b) == len(s):
            ys.append(b.values)
            cols.append(GREY)
            names.append("上证指数")
    ymin = min(float(np.nanmin(y)) for y in ys)
    ymax = max(float(np.nanmax(y)) for y in ys)
    pad = (ymax - ymin) * 0.08 or 0.05
    ymin, ymax = ymin - pad, ymax + pad
    span = (ymax - ymin) or 1.0
    x = np.linspace(ml, w - mr, len(s))
    p = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="净值曲线">']
    for gv in np.linspace(ymin, ymax, 5):
        gy = mt + ih * ((ymax - gv) / span)
        p.append(f'<line x1="{ml}" y1="{gy:.1f}" x2="{w-mr}" y2="{gy:.1f}" '
                 f'stroke="#e6eaf0" stroke-dasharray="3 3"/>')
        p.append(f'<text x="{ml-6}" y="{gy+3.5:.1f}" text-anchor="end" '
                 f'font-size="9.5" fill="#8b93a1">{gv:.2f}</text>')
    # 1.0 基准线
    if ymin < 1.0 < ymax:
        gy = mt + ih * ((ymax - 1.0) / span)
        p.append(f'<line x1="{ml}" y1="{gy:.1f}" x2="{w-mr}" y2="{gy:.1f}" '
                 f'stroke="#c9d0d9"/>')
    for y, c, nm in zip(ys, cols, names):
        pts = " ".join(
            f"{x[i]:.1f},{mt+ih*((ymax-y[i])/span):.1f}"
            for i in range(len(y)) if np.isfinite(y[i]))
        sw = 2.0 if nm == "策略" else 1.4
        p.append(f'<polyline points="{pts}" fill="none" stroke="{c}" '
                 f'stroke-width="{sw}" stroke-linejoin="round"/>')
    lx = ml + 4
    for c, nm in zip(cols, names):
        p.append(f'<rect x="{lx}" y="{mt-11}" width="16" height="3" rx="1.5" fill="{c}"/>')
        p.append(f'<text x="{lx+21}" y="{mt-7}" font-size="10" fill="#4b5563">{nm}</text>')
        lx += 21 + len(nm) * 11 + 16
    p.append(f'<text x="{ml}" y="{h-8}" font-size="9.5" fill="#8b93a1">'
             f'起点=1.0 ／ 已含换仓成本</text>')
    p.append("</svg>")
    return "".join(p)


# ---------------------------------------------------------------- 主流程
def build(start: str, out_path: str, open_html: bool = True) -> str:
    panel = bl.read_panel(CSV)
    dates = panel.index
    rot, deff, _w = bl.read_pools()
    pool = [c for c in rot if c in panel.columns]
    dfn = deff[0] if deff else "512040"
    si = max(0, int(dates.searchsorted(pd.Timestamp(start), side="left")))
    r = bl.backtest(panel, dates, si, len(dates) - 1, pool, dfn,
                    buy=COST, sell=COST, W=20, MA=20, value_engine=True,
                    **bl.LIVE_COMPOSITE)
    navs = r["navs"].copy()
    # backtest 返回的 navs 索引可能是 object，统一转成 DatetimeIndex 才能按 .year/.month 分组
    navs.index = pd.to_datetime(navs.index)
    st = bl.stats(navs)
    bench = panel[BENCH] if BENCH in panel.columns else None
    ms = monthly_stats(navs, r["trades"], r.get("weak_events") or [], bench)

    d0, d1 = str(navs.index[0].date()), str(navs.index[-1].date())
    pos_months = sum(1 for m in ms if m["ret"] > 0)
    total_m = len(ms)
    # 最佳/最差月
    best = max(ms, key=lambda m: m["ret"]) if ms else None
    worst = min(ms, key=lambda m: m["ret"]) if ms else None
    # 上证同期
    btxt = ""
    if bench is not None and len(bench):
        bs = bench.loc[navs.index[0]:navs.index[-1]]
        if len(bs) > 1:
            br = (float(bs.iloc[-1]) / float(bs.iloc[0]) - 1) * 100
            btxt = (f'<span class="k">同期上证</span>'
                    f'<b style="color:{RED if br>0 else GREEN}">{br:+.2f}%</b>')

    def _cell(v, d=2, unit="%", col=True):
        if v is None or not np.isfinite(v):
            return '<td class="num dimtxt">—</td>'
        c = (RED if v > 0 else GREEN) if col else "#42474f"
        return f'<td class="num" style="color:{c}">{v:+.{d}f}{unit}</td>'

    rows = []
    for m in ms:
        hname = ("持币" if m["hold"] == "CASH"
                 else bl.NAMES.get(m["hold"], m["hold"] if m["hold"] != "—" else "—"))
        wc = ('<span class="wb">抄底' + str(m["weak"]) + "</span>"
              if m["weak"] else '<span class="dimtxt">·</span>')
        rows.append(
            f'<tr><td class="mono">{m["label"]}</td>'
            f'<td>{html.escape(hname)}</td>'
            + _cell(m["ret"])
            + _cell(m["mdd"], col=False)
            + f'<td class="num">{m["trades"]}</td>'
            + f'<td class="num">{wc}</td>'
            + (_cell(m["bench"]) if m["bench"] is not None
               else '<td class="num dimtxt">—</td>')
            + (_cell(m["alpha"], unit="pp") if m["alpha"] is not None
               else '<td class="num dimtxt">—</td>')
            + "</tr>")

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>策略月度回测 · {d0} ~ {d1}</title>
<style>
:root{{--bg:{BG};--fg:{FG};--mut:{MUT};--bd:{BD}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:14px 12px 26px;background:var(--bg);color:var(--fg);
  font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
  "Hiragino Sans GB","Microsoft YaHei",sans-serif}}
.wrap{{max-width:760px;margin:0 auto}}
h1{{font-size:19px;margin:0 0 4px}}
.sub{{font-size:12px;color:var(--mut);line-height:1.7}}
.card{{background:#fbfcfe;border:1px solid var(--bd);border-radius:10px;
  padding:12px 14px;margin-top:14px}}
.cap{{font-size:11.5px;color:var(--mut);margin:0 0 8px;font-weight:600}}
.kgrid{{display:flex;flex-wrap:wrap;gap:10px 0;margin-top:12px}}
.kc{{flex:1 1 33%;min-width:104px;display:flex;flex-direction:column;gap:2px}}
.kc .k{{font-size:11px;color:var(--mut)}}
.kc b{{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th,td{{padding:5px 6px;border-bottom:1px solid var(--bd);text-align:left;
  white-space:nowrap}}
th{{background:#f4f6fa;font-size:11.5px;color:#4b5563;font-weight:700;
  position:sticky;top:0}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
td.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
.dimtxt{{color:#8b93a1}}
.wb{{background:#fff4e8;color:#b45309;border:1px solid #fcd9a8;border-radius:4px;
  padding:0 5px;font-size:10.5px;font-weight:700}}
.tblwrap{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
.note{{margin-top:10px;font-size:11px;color:#8b93a1;line-height:1.7}}
@media(max-width:600px){{.kc{{flex:1 1 50%}}}}
</style></head><body><div class="wrap">
<h1>策略月度回测 · 2025 年至今</h1>
<div class="sub">口径 <b>基础轮动 + D3 单笔止损 + P3 追高止盈 + 抄底</b>
（与日报现行口径一致，唯一事实源 etf_backtest_long.LIVE_COMPOSITE）<br>
区间 <b>{d0} ~ {d1}</b>　单边成本 0.01%　前复权　主选池 {len(pool)} 只 + 防守 {dfn}</div>

<div class="card">
<div class="cap">汇总</div>
<div class="kgrid">
  <div class="kc"><span class="k">累计收益</span>
    <b style="color:{RED if st['total']>0 else GREEN}">{st['total']*100:+.2f}%</b></div>
  <div class="kc"><span class="k">年化</span><b>{st['ann']*100:+.2f}%</b></div>
  <div class="kc"><span class="k">最大回撤</span>
    <b style="color:#42474f">{st['mdd']*100:.2f}%</b></div>
  <div class="kc"><span class="k">Calmar</span><b>{st['calmar'] or 0:.2f}</b></div>
  <div class="kc"><span class="k">Sharpe</span><b>{st['sharpe'] or 0:.2f}</b></div>
  <div class="kc"><span class="k">盈利月份</span>
    <b>{pos_months}/{total_m}</b></div>
</div>
<div class="note">
{'最好 <b>' + best['label'] + '</b> ' + format(best['ret'], '+.2f') + '%' if best else ''}
{'　最差 <b>' + worst['label'] + '</b> ' + format(worst['ret'], '+.2f') + '%' if worst else ''}
　换仓 <b>{len(r['trades'])}</b> 笔　抄底触发 <b>{len(r.get('weak_events') or [])}</b> 次
　{btxt}
</div>
</div>

<div class="card">
<div class="cap">月度收益</div>
{bar_chart(ms)}
</div>

<div class="card">
<div class="cap">净值曲线（起点 = 1.0）</div>
{curve_chart(navs, bench)}
</div>

<div class="card">
<div class="cap">月度明细</div>
<div class="tblwrap"><table>
<thead><tr><th>月份</th><th>月末持仓</th><th class="num">月收益</th>
<th class="num">月内回撤</th><th class="num">换仓</th><th class="num">抄底</th>
<th class="num">同期上证</th><th class="num">超额</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<div class="note">「月内回撤」= 该月净值自身的最高点到最低点，负数；
「超额」= 月收益 − 同期上证月收益。数据来自前复权长历史
（tdx_adj_long_2019_2026.csv），每次换仓按双边 0.02% 计成本。</div>
</div>

<div class="note">本报告由 make_monthly_report.py 生成，仅供研究参考，不构成投资建议。</div>
</div></body></html>"""
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    print(f"报告: {out_path}  （{len(doc)/1024:.0f} KB）")
    print(f"区间 {d0} ~ {d1}｜月数 {total_m}｜累计 {st['total']*100:+.2f}%｜"
          f"年化 {st['ann']*100:+.2f}%｜MDD {st['mdd']*100:.2f}%｜Calmar {st['calmar'] or 0:.2f}")
    print(f"盈利月 {pos_months}/{total_m}｜换仓 {len(r['trades'])} 笔｜"
          f"抄底 {len(r.get('weak_events') or [])} 次")
    if open_html:
        try:
            webbrowser.open("file://" + os.path.abspath(out_path))
        except Exception:                      # noqa: BLE001
            pass
    return out_path


def main():
    ap = argparse.ArgumentParser(description="策略月度回测报告（2025 年至今）")
    ap.add_argument("--start", default="2025-01-01", help="起始日，默认 2025-01-01")
    ap.add_argument("--out", default=os.path.join("out", "monthly_2025_2026.html"),
                    help="输出 HTML 路径")
    ap.add_argument("--no-open", action="store_true", help="不自动打开（定时任务用）")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    build(a.start, a.out, open_html=not a.no_open)


if __name__ == "__main__":
    main()
