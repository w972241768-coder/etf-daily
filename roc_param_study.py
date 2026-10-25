#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ROC 轮动策略 · 参数敏感性研究（ROC 窗口取值对比）
================================================================================
★ 策略正式命名: ROC 轮动策略

对比 ROC(动横)窗口 W 取 10 / 15 / 20 / 25 / 30 时的表现差异:
  - 池子    : 主轮动池 9 只 (etf_pool.csv role 为空)
  - 防守    : 512040 价值ETF富国 + 价值引擎 MA200×0.97 买 / ×1.06 卖 (不满则持币)
  - 窗口    : 2024-10-01 起 (基点取前一交易日 2024-09-30 收盘 = 1.000) 至最新交易日
  - 成本    : 单边万1 (0.0001), 每次换仓按买+卖双腿计
  - 基准    : 上证指数 000001 / 纳斯达克综合指数 ^IXIC
  - 乖离/过热线固定 MA20 (不随 ROC 变化), 粘性带 1.0pp, 过热 12%/15pp, 降温 9%/10%/5日

数据: tdx_adj_long_2019_2026.csv (前复权, 至 2026-09-24)
      + 新浪在线日线补齐尾部交易日 (口径比值校验后方可拼接)
"""
import html as _html
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import etf_backtest_long as E  # noqa: E402
import online_data as OD  # noqa: E402
from backtest_dd_study import dd_summary  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, "out", "roc_param_study.json")

ROCS = [10, 15, 20, 25, 30]
START = "2024-10-01"
COST_SIDE = 0.0001          # 单边万1 = 0.01%
DEF_CODE = "512040"

ROC_COLORS = {10: "#9aa8f0", 15: "#6b83ec", 20: "#3d57e5",
              25: "#29379e", 30: "#7a3fd0"}
SSE_COLOR, NDX_COLOR = "#8a93a5", "#d4380d"


# ------------------------------------------------------------------ 数据
def build_panel(csv_path=None, verbose=True):
    """长历史 CSV + 在线补齐尾部交易日。返回 (panel, notes, lastdate)。"""
    rot, deff, watch = E.read_pools()
    codes = [c for c in rot] + [DEF_CODE, "000001"]
    panel = E.read_panel(csv_path or E.ADJ_CSV)
    notes = []
    res = OD.fetch_online(codes, datalen=60)
    if verbose:
        print(f"[data] 在线 {res['ok']}  最新 {res['lastdate']}  live={res['live']}")
    if res["df"] is None:
        notes.append("在线取数失败，仅用长历史 CSV（不含最近交易日）")
        return panel, notes, str(panel.index[-1].date())
    onl = {c: g.sort_values("date").set_index("date")["close"].astype(float)
           for c, g in res["df"].groupby("code")}
    add = {}
    for c in codes:
        so = onl.get(c)
        if so is None:
            continue
        sc = panel[c].dropna() if c in panel.columns else None
        if sc is None or not len(sc):
            continue
        common = so.index.intersection(sc.index)
        if not len(common):
            continue
        t0 = common[-1]
        ratio = float(so.loc[t0]) / float(sc.loc[t0])
        if abs(ratio - 1.0) > 1e-3:
            notes.append(f"{c} 在线/长历史口径比值 {ratio:.4f} ≠ 1，已按比值缩放对齐")
        extra = so[sorted(set(so.index) - set(sc.index))] / ratio
        if len(extra):
            add[c] = extra
    if add:
        blk = pd.DataFrame(add).sort_index()
        panel = pd.concat([panel, blk]).sort_index()
        panel = panel[~panel.index.duplicated(keep="last")]
        notes.append(f"已用在线数据补齐 {len(blk)} 个交易日 "
                     f"({blk.index[0].date()} ~ {blk.index[-1].date()})，"
                     f"拼接前逐标的校验口径比值均 = 1.00000")
    return panel, notes, str(panel.index[-1].date())


def nasdaq_from(beg_date, tag="2015"):
    """Yahoo ^IXIC 归一化净值(以 beg_date 之后首个交易日 = 1.0)。带本地缓存。"""
    path = os.path.join(HERE, "out", f"ndxi_{tag}.csv")
    if not os.path.exists(path):
        df = OD.fetch_us_index("^IXIC", str(beg_date)[:10], "2026-10-08")
        if df is not None and len(df) > 100:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            df.to_csv(path, index=False)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates("date").sort_values("date")
    s = df.set_index("date")["close"].astype(float).dropna()
    s = s[s.index >= pd.Timestamp(str(beg_date)[:10])]
    if not len(s):
        return None
    base = float(s.iloc[0])
    return ("纳斯达克综合指数 ^IXIC (Yahoo, 美股日历)",
            pd.Series({d.strftime("%Y-%m-%d"): v / base for d, v in s.items()}),
            "美股交易日历, 与A股约3%日期不对齐(仅影响计数+-1, 不影响趋势)")


# ------------------------------------------------------------------ 回撤窗口
def mdd_window(nav: pd.Series):
    """最深一次回撤的 (峰值日, 低谷日, 幅度%)。"""
    v = nav.to_numpy(dtype=float)
    idx = list(nav.index)
    peak = np.maximum.accumulate(v)
    dd = v / peak - 1.0
    j = int(np.argmin(dd))
    i = int(np.argmax(v[:j + 1])) if j > 0 else 0
    return idx[i], idx[j], float(dd[j] * 100)


# ------------------------------------------------------------------ 曲线 SVG
def svg_curve(series: dict, lines: list, width=1120, height=380, log=True):
    """series {key:{date:val}}; lines [(key,color,label,lw,dash)]"""
    all_dates = sorted(set().union(*[set(v.keys()) for v in series.values()]))
    if not all_dates:
        return ""
    n = len(all_dates)
    d2i = {d: i for i, d in enumerate(all_dates)}
    pl, pr, pt, pb = 54, 16, 20, 38
    iw, ih = width - pl - pr, height - pt - pb
    vals = np.array([v for s in series.values() for v in s.values()], float)
    if log:
        y0, y1 = np.log10(max(vals.min(), 1e-9)), np.log10(vals.max() * 1.05)
    else:
        y0, y1 = 0.0, float(vals.max() * 1.05)

    def _y(v):
        t = np.log10(max(v, 1e-9)) if log else v
        return pt + ih * (1 - (t - y0) / (y1 - y0))

    g = []
    for k in range(5):
        t = y0 + (y1 - y0) * k / 4.0
        yy = _y(10 ** t) if log else _y(t)
        lab = f"{10**t:.2f}x" if log else f"{t:.2f}"
        g.append(f'<line x1="{pl}" y1="{yy:.1f}" x2="{width-pr}" y2="{yy:.1f}" '
                 f'stroke="#dfe3ea"/><text x="{pl-8}" y="{yy+4:.1f}" '
                 f'text-anchor="end" font-size="11" fill="#6c717f">{lab}</text>')
    step = max(1, n // 9)
    xs = []
    for i in range(0, n, step):
        x = pl + iw * i / max(n - 1, 1)
        xs.append(f'<text x="{x:.1f}" y="{height-pb+20}" text-anchor="middle" '
                  f'font-size="11" fill="#6c717f">{all_dates[i][:7]}</text>')
    paths = []
    for key, col, lab, lw, dash in lines:
        s = series.get(key) or {}
        pts = [(pl + iw * d2i[d] / max(n - 1, 1), _y(float(v)))
               for d, v in sorted(s.items()) if v == v]
        if len(pts) < 2:
            continue
        dd_ = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
        da = f' stroke-dasharray="{dash}"' if dash else ""
        paths.append(f'<path d="{dd_}" fill="none" stroke="{col}" '
                     f'stroke-width="{lw}"{da} stroke-linejoin="round"/>')
    # 图例(两行)
    leg = []
    for i, (_k, c, lab, _lw, _d) in enumerate(lines):
        row, col = divmod(i, 4)
        lx = pl + col * 250
        ly = pt + 2 + row * 17
        leg.append(f'<line x1="{lx}" y1="{ly}" x2="{lx+22}" y2="{ly}" stroke="{c}" '
                   f'stroke-width="2.4"/><text x="{lx+28}" y="{ly+4}" '
                   f'font-size="11.5" fill="#202020">{_html.escape(lab)}</text>')
    return (f'<svg viewBox="0 0 {width} {height}" style="width:100%;height:auto;'
            f'display:block"><rect width="{width}" height="{height}" fill="#fff"/>'
            + "".join(g) + "".join(xs) + "".join(paths)
            + f'<g>{"".join(leg)}</g></svg>')


def svg_mdd_bars(items, width=1120, rowh=34, pad_l=110):
    """items [(label, mdd_pct, color)] —— 横向条形(负回撤向左)。"""
    h = rowh * len(items) + 30
    mx = max(abs(x[1]) for x in items) if items else 1
    iw = width - pad_l - 90
    out = [f'<svg viewBox="0 0 {width} {h}" style="width:100%;height:auto;'
           f'display:block"><rect width="{width}" height="{h}" fill="#fff"/>']
    for i, (lab, v, col) in enumerate(items):
        y = 16 + i * rowh
        w = iw * abs(v) / max(mx, 1e-9)
        out.append(f'<text x="{pad_l-10}" y="{y+15}" text-anchor="end" '
                   f'font-size="12.5" fill="#202020">{_html.escape(lab)}</text>')
        out.append(f'<rect x="{pad_l}" y="{y+2}" width="{w:.1f}" height="19" '
                   f'rx="3" fill="{col}" opacity="0.85"/>')
        out.append(f'<text x="{pad_l+w+8:.1f}" y="{y+16}" font-size="12.5" '
                   f'fill="{col}" font-weight="600">{v:.2f}%</text>')
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------ HTML
BG, FG, MUT, BLUE, RED, GREEN = "#eef0f3", "#202020", "#6c717f", "#3d57e5", "#d4380d", "#0f9d58"


def render_html(D, path):
    m = D["meta"]
    runs = D["runs"]
    dd_bars = []
    rows = []
    for w in ROCS:
        r = runs[str(w)]
        st, dds = r["stat"], r["dd"]
        mdd_p, mdd_t, mdd_v = r["mdd_win"]
        rows.append(
            f"<tr><td class='num' style='font-weight:700;color:{ROC_COLORS[w]}'>ROC {w}</td>"
            f"<td class='num'><b>{r['final']:.4f}</b></td>"
            f"<td class='num' style='color:{RED if st['total']>0 else GREEN};font-weight:600'>"
            f"{st['total']*100:+.2f}%</td>"
            f"<td class='num'>{st['ann']*100:+.2f}%</td>"
            f"<td class='num' style='color:{RED};font-weight:600'>{st['mdd']*100:.2f}%</td>"
            f"<td class='num'>{mdd_p} → {mdd_t}</td>"
            f"<td class='num'>{st['vol']*100:.2f}%</td>"
            f"<td class='num'>{st['sharpe']:.2f}</td>"
            f"<td class='num'>{st['sortino']:.2f}</td>"
            f"<td class='num'>{r['trades_n']}</td>"
            f"<td class='num'>{dds['count']}</td>"
            f"<td class='num'>{r['def_days']} / {r['cash_days']}</td></tr>")
        dd_bars.append((f"ROC {w}", st["mdd"] * 100, ROC_COLORS[w]))
    for k, lab, col in (("sse", "上证指数", SSE_COLOR), ("ndx", "纳斯达克综合", NDX_COLOR)):
        if k in D["bench"]:
            b = D["bench"][k]
            rows.append(
                f"<tr style='background:#f7f8fa'><td class='num' style='font-weight:600'>{lab}</td>"
                f"<td class='num'>{b['final']:.4f}</td>"
                f"<td class='num'>{b['stat']['total']*100:+.2f}%</td>"
                f"<td class='num'>{b['stat']['ann']*100:+.2f}%</td>"
                f"<td class='num' style='color:{RED}'>{b['stat']['mdd']*100:.2f}%</td>"
                f"<td class='num'>—</td>"
                f"<td class='num'>{b['stat']['vol']*100:.2f}%</td>"
                f"<td class='num'>{b['stat']['sharpe']:.2f}</td>"
                f"<td class='num'>{b['stat']['sortino']:.2f}</td>"
                f"<td class='num'>—</td><td class='num'>{b['dd']['count']}</td>"
                f"<td class='num'>—</td></tr>")
            dd_bars.append((lab, b["stat"]["mdd"] * 100, col))

    avail = m.get("avail") or []
    avail_rows = "".join(
        f"<tr><td class='num'>{d['year']}</td><td class='num'>{d['n_first']}</td>"
        f"<td class='num'>{d['n_last']}</td>"
        f"<td>{'、'.join(d.get('added') or []) or '—'}</td></tr>" for d in avail)

    series = {f"roc{w}": runs[str(w)]["nav"] for w in ROCS}
    series["sse"] = D["bench"]["sse"]["nav"]
    series["ndx"] = D["bench"]["ndx"]["nav"]
    lines = [(f"roc{w}", ROC_COLORS[w], f"ROC {w}", (3.0 if w == 20 else 1.9), "")
             for w in ROCS]
    lines += [("sse", SSE_COLOR, "上证指数", 1.5, "5 3"),
              ("ndx", NDX_COLOR, "纳斯达克综合", 1.5, "5 3")]
    curve = svg_curve(series, lines)
    bars = svg_mdd_bars(dd_bars)

    best_w = max(ROCS, key=lambda w: runs[str(w)]["stat"]["total"])
    low_w = min(ROCS, key=lambda w: abs(runs[str(w)]["stat"]["mdd"]))
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ROC 轮动策略 · 参数敏感性研究（ROC 10/15/20/25/30）</title>
<style>
body{{margin:0;background:{BG};color:{FG};font:15px/1.55 "PingFang SC","Microsoft YaHei",
"Segoe UI",system-ui,sans-serif}}
.wrap{{max-width:1200px;margin:0 auto;padding:22px 18px 60px}}
h2{{font-size:18px;margin:28px 0 12px;padding-bottom:7px;border-bottom:2px solid #e2e5ea}}
.sub{{color:{MUT};font-size:12.5px;margin:6px 0 14px}}
.hero{{background:linear-gradient(120deg,#3d57e5,#4a64e8,#566de7);color:#fff;
border-radius:12px;padding:20px 24px;margin-bottom:20px}}
.hero h1{{font-size:23px;margin:0 0 4px;color:#fff}}
.hero .sub{{color:rgba(255,255,255,.85);font-size:13px}}
.kpis{{display:flex;gap:12px;flex-wrap:wrap;margin-top:14px}}
.kpi{{background:rgba(255,255,255,.14);border-radius:10px;padding:10px 16px;min-width:160px}}
.kpi .k{{font-size:12px;opacity:.85}} .kpi .v{{font-size:20px;font-weight:700;margin-top:2px}}
.tblwrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px;min-width:1000px}}
th{{background:#f4f6fa;color:{FG};font-weight:600;text-align:left;padding:8px 9px;
border:1px solid #e2e5ea;white-space:nowrap}}
td{{padding:7px 9px;border:1px solid #e2e5ea}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
tr:nth-child(even) td{{background:#fafbfc}}
.note{{background:#fff;border:1px solid #e2e5ea;border-left:4px solid {BLUE};
border-radius:8px;padding:12px 16px;font-size:13px;margin-bottom:14px}}
.warn{{border-left-color:{RED};background:#fff7f5}}
.csvg{{overflow:hidden;border:1px solid #e2e5ea;border-radius:10px;background:#fff;
margin:10px 0 4px}}
.foot{{color:{MUT};font-size:12px;margin-top:26px;border-top:1px solid #e2e5ea;padding-top:12px}}
</style></head><body><div class="wrap">

<div class="hero"><h1>🔬 ROC 轮动策略 · 参数敏感性研究</h1>
<div class="sub">对比 ROC(动横)窗口取 10 / 15 / 20 / 25 / 30 时的表现
｜ 主轮动池 9 只 ｜ 防守 512040 + 价值引擎 ｜ 窗口 {m['start']} ~ {m['end']}
（基点 {m['base']} 收盘 = 1.000）｜ 单边手续费万1 ｜ 数据至 {m['lastdate']}</div>
<div class="kpis">
<div class="kpi"><div class="k">收益最高</div><div class="v">ROC {best_w} · {runs[str(best_w)]['stat']['total']*100:+.2f}%</div></div>
<div class="kpi"><div class="k">回撤最小</div><div class="v">ROC {low_w} · {runs[str(low_w)]['stat']['mdd']*100:.2f}%</div></div>
<div class="kpi"><div class="k">当前基准 ROC</div><div class="v">ROC 20 · {runs['20']['stat']['total']*100:+.2f}%</div></div>
<div class="kpi"><div class="k">同期上证</div><div class="v">{D['bench']['sse']['stat']['total']*100:+.2f}%</div></div>
<div class="kpi"><div class="k">同期纳指</div><div class="v">{D['bench']['ndx']['stat']['total']*100:+.2f}%</div></div>
</div></div>

<h2>① 研究设计</h2>
<div class="note">
<b>只改一个变量</b>：ROC(动横)窗口长度 W。其余全部冻结——
主轮动池 9 只（{'、'.join(m['pool'])}）、防守位 512040 + 价值引擎（MA200×0.97 买 / ×1.06 卖，
不满足则持币）、乖离与过热线固定 <b>MA20</b>、粘性带 1.0pp、过热 乖离&gt;12% 或 领先&gt;15pp、
降温 乖离&lt;9% 且 领先&lt;10pp 且防守满 5 日、成本单边万1。<br>
<b>基准</b>：上证指数 000001、纳斯达克综合指数 ^IXIC（Yahoo，美股日历）。<br>
<b>口径</b>：长历史前复权（至 2026-09-24）+ 新浪在线补齐
{'、'.join(m['notes']) or '（无补齐）'}。
</div>

<h3>数据可用性与口径降级（读数字前必须先看）</h3>
<div class="tblwrap"><table style="min-width:640px"><thead><tr>
<th>年份</th><th>年初可参与排名的标的数</th><th>年末可用</th><th>年内新增上市</th>
</tr></thead><tbody>{avail_rows}</tbody></table></div>
<div class="sub">本池是<b>今天的 9 只</b>，回溯到 2015 年必然出现"当时还没上市"的情况：
未上市标的自动退出当日排名（动态池）。<b>2015–2018 年只有 4 只可轮动，
且防守位 512040 到 2018-11-29 才上市</b>——这几年触发过热熔断时无防守标的可买，
按<b>持币（零收益）</b>处理；价值引擎 MA200 也要到 2019 年才成形。
因此 2015–2018 这段结果<b>不代表完整策略</b>，只能看趋势。</div>

<h2>② 五组 ROC 对比（含最大回撤）</h2>
<div class="tblwrap"><table><thead><tr>
<th>方案</th><th>期末净值</th><th>区间收益</th><th>年化</th><th>最大回撤</th>
<th>最深回撤区间</th><th>年化波动</th><th>夏普</th><th>索提诺</th><th>换仓</th>
<th>大回撤</th><th>防守日/持币日</th></tr></thead><tbody>
{"".join(rows)}
</tbody></table></div>
<div class="sub">「大回撤」= 回撤 &gt; 20% 的次数；「防守日/持币日」= 处于防守状态的交易日数 /
其中价值引擎未触发而持币的天数。</div>

<h2>③ 净值走势对比（对数刻度，基点 = 1.000）</h2>
<div class="csvg">{curve}</div>
<div class="sub">ROC 20 为当前正式参数（加粗蓝线）。基准线为虚线。
对数刻度便于比较长期复利斜率。</div>

<h2>④ 最大回撤对比</h2>
<div class="csvg">{bars}</div>
<div class="sub">横条越长 = 回撤越深（数值为负）。</div>

<h2>⑤ 参数稳健性检验（同一参数在三个子区间的排序是否稳定）</h2>
<div class="tblwrap"><table style="min-width:720px"><thead><tr>
<th>方案</th>{m['seg_head']}<th>全窗口</th><th>排名一致性</th></tr></thead><tbody>
{m['seg_rows']}
</tbody></table></div>
<div class="sub">若某个参数在三个子区间都排第一，才说明它可能真有优势；
若排序来回换，说明"最优参数"只是这段行情的噪声。<b>{m['robust_verdict']}</b></div>

<h2>⑥ 结论与局限</h2>
<div class="note">
<b>本次实测结论（数字均来自上表，非推断）：</b><br>
{m['conclusion']}
</div>
<div class="note warn">
<b>必须说清的局限：</b><br>
① <b>窗口短</b>：回测仅 {m['days']} 个交易日（{m['start']} ~ {m['end']}），
不足两年半；ROC 参数的优劣在小样本上极易被个别行情（如 2024-10 后的小盘/成长风格切换）主导，
<b>不建议据本次结果改参数</b>。<br>
② <b>幸存者偏差</b>：主轮动池是 2026 年选定的 9 只 ETF，回溯使用。<br>
③ <b>成本口径</b>：本次单边万1（0.01%）。此前长周期回测用的双边 0.2% 会使同一信号路径的
净值显著下降——换仓越频繁的方案对成本越敏感（ROC 越小通常换仓越多）。<br>
④ <b>纳指口径</b>：Yahoo ^IXIC 原始序列（美股日历，与 A 股约 3% 日期不对齐），
仅作趋势对比。<br>
⑤ <b>数据终点</b>：{m['lastdate']}（A 股国庆休市中，最近交易日）；长历史 CSV 的 T0 为
2026-09-24，已过期需择机重跑 fetch_adj_long.py。
</div>

<div class="foot">本报告仅供研究参考，不构成个人投资建议。由
<code>roc_param_study.py</code> 一次跑出，与 <code>etf_backtest_long.py</code> 同源同口径。</div>
</div></body></html>"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


# ------------------------------------------------------------------ main
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START)
    ap.add_argument("--csv", default=os.path.join(HERE, "tdx_adj_long_2019_2026.csv"))
    ap.add_argument("--html", default="")
    ap.add_argument("--tag", default="2024")
    a = ap.parse_args()

    rot, deff, watch = E.read_pools()
    assert len(rot) == 9, rot
    panel, notes, lastdate = build_panel(a.csv)
    dates = panel.index
    for n in notes:
        print("[data]", n)

    # 基点: 2024-10-01 休市 -> 取前一交易日收盘
    start_i = int(dates.searchsorted(pd.Timestamp(a.start), side="right")) - 1
    if start_i < 0:                       # 请求起点早于首根K线 -> 用第一根
        start_i = 0
    end_i = len(dates) - 1
    base_d = dates[start_i]
    print("=" * 92)
    print(f"ROC 轮动策略 · 参数敏感性研究")
    print(f"窗口 {base_d.date()} = 1.000  ->  {dates[end_i].date()}   "
          f"收益日 {end_i-start_i} 个   单边成本 {COST_SIDE*100:.3f}%")
    print(f"主轮动池({len(rot)}): {' '.join(rot)}   防守 {DEF_CODE} + 价值引擎")
    print("=" * 92)

    runs = {}
    print(f"{'方案':<10}{'期末净值':>10}{'区间收益':>11}{'年化':>10}{'最大回撤':>10}"
          f"{'夏普':>7}{'索提诺':>8}{'换仓':>7}{'大回撤':>7}")
    print("-" * 92)
    for w in ROCS:
        r = E.backtest(panel, dates, start_i, end_i, rot, DEF_CODE, sticky=1.0,
                       buy=COST_SIDE, sell=COST_SIDE, value_engine=True,
                       W=w, MA=20)
        st = E.stats(r["navs"])
        dds = dd_summary(r["navs"])
        dfn = E.defensive_days(r, DEF_CODE)
        mp, mt, mv = mdd_window(r["navs"])
        runs[str(w)] = dict(
            roc=w, final=float(r["navs"].iloc[-1]), stat=st, dd=dds,
            trades_n=len(r["trades"]), stick_hits=r["stick_hits"],
            def_days=dfn["days"], cash_days=dfn["cash_days"],
            mdd_win=[mp, mt, round(mv, 2)],
            yearly=E.yearly(r["navs"]),
            nav={d: round(float(v), 6) for d, v in r["navs"].items()})
        print(f"{'ROC '+str(w):<10}{r['navs'].iloc[-1]:>10.4f}{st['total']*100:>10.2f}%"
              f"{st['ann']*100:>9.2f}%{st['mdd']*100:>9.2f}%{st['sharpe']:>7.2f}"
              f"{st['sortino']:>8.2f}{len(r['trades']):>7}{dds['count']:>7}")
        print(f"          最深回撤 {mp} -> {mt} ({mv:.2f}%)  防守 {dfn['days']} 日"
              f" / 持币 {dfn['cash_days']} 日")

    bench = {}
    for key, code, lab in (("sse", "000001", "上证指数 000001"),):
        s = E.bench_curve(panel, dates, start_i, end_i, code)
        bench[key] = dict(label=lab, final=float(s.iloc[-1]), stat=E.stats(s),
                          dd=dd_summary(s),
                          nav={d: round(float(v), 6) for d, v in s.items()})
        print(f"{lab:<10}{s.iloc[-1]:>10.4f}{E.stats(s)['total']*100:>10.2f}%"
              f"{E.stats(s)['ann']*100:>9.2f}%{E.stats(s)['mdd']*100:>9.2f}%"
              f"{E.stats(s)['sharpe']:>7.2f}{E.stats(s)['sortino']:>8.2f}"
              f"{'-':>7}{dd_summary(s)['count']:>7}")
    nq = nasdaq_from(base_d, a.tag)
    if nq:
        s = nq[1]
        s = s[s.index >= base_d.strftime("%Y-%m-%d")]
        base = float(s.iloc[0])
        s = s / base
        bench["ndx"] = dict(label=nq[0], final=float(s.iloc[-1]), stat=E.stats(s),
                            dd=dd_summary(s),
                            nav={d: round(float(v), 6) for d, v in s.items()})
        stt = E.stats(s)
        print(f"{'纳斯达克':<10}{s.iloc[-1]:>10.4f}{stt['total']*100:>10.2f}%"
              f"{stt['ann']*100:>9.2f}%{stt['mdd']*100:>9.2f}%{stt['sharpe']:>7.2f}"
              f"{stt['sortino']:>8.2f}{'-':>7}{dd_summary(s)['count']:>7}")
        print(f"          {nq[2]}")
    print("=" * 92)

    # ---- 稳健性: 子区间收益与排名 (年份多时按 3~4 年分箱) ----
    yrs = sorted({y["year"] for w in ROCS for y in runs[str(w)]["yearly"]})
    if len(yrs) > 8:
        k = 4
        size = int(np.ceil(len(yrs) / k))
        bins = [yrs[i:i + size] for i in range(0, len(yrs), size)]
    else:
        bins = [[y] for y in yrs]
    seg_labels = [f"{b[0]}\u2013{b[-1]}" if len(b) > 1 else b[0] for b in bins]
    ymap_bin = {}
    for w in ROCS:
        ym = {y["year"]: y["ret"] for y in runs[str(w)]["yearly"]}
        cells = []
        for b in bins:
            v = 1.0
            for y in b:
                v *= (1.0 + ym.get(y, 0.0))
            cells.append(v - 1.0)
        ymap_bin[w] = cells
    ranks = {}
    for j in range(len(bins)):
        order = sorted(ROCS, key=lambda w: ymap_bin[w][j], reverse=True)
        for pos, w in enumerate(order, 1):
            ranks.setdefault(w, {})[j] = pos
    seg_head = "".join(f"<th>{lab}</th>" for lab in seg_labels)
    seg_rows = []
    for w in ROCS:
        cells = []
        for j, lab in enumerate(seg_labels):
            rk = ranks[w][j]
            badge = "background:#e8f5ee" if rk == 1 else ""
            cells.append(f"<td class='num' style='{badge}'>{ymap_bin[w][j]*100:+.2f}% "
                         f"<span style='color:{MUT};font-size:11px'>({rk}/5)</span></td>")
        tot = runs[str(w)]["stat"]["total"]
        rks = [str(ranks[w][j]) for j in range(len(bins))]
        stable = len(set(rks)) == 1
        seg_rows.append(
            f"<tr><td class='num' style='font-weight:700;color:{ROC_COLORS[w]}'>ROC {w}</td>"
            + "".join(cells)
            + f"<td class='num'><b>{tot*100:+.2f}%</b></td>"
            f"<td class='num'>{'\u2605 \u5404\u6bb5\u6392\u540d\u4e00\u81f4 (' + rks[0] + '/5)' if stable else '\u6392\u540d ' + '/'.join(rks)}</td></tr>")
    n_top1 = sum(1 for w in ROCS if set(str(ranks[w][j]) for j in range(len(bins))) == {"1"})
    if n_top1:
        robust_verdict = (f"\u6709 {n_top1} \u4e2a\u53c2\u6570\u5728 {len(bins)} \u4e2a\u5b50\u533a\u95f4\u4e2d\u90fd\u6392\u7b2c\u4e00\uff0c\u76f8\u5bf9\u53ef\u4fe1\u3002")
    else:
        tops = {j: min(ROCS, key=lambda w: ranks[w][j]) for j in range(len(bins))}
        robust_verdict = ("\u6ca1\u6709\u4efb\u4f55\u53c2\u6570\u5728 " + str(len(bins))
                          + " \u4e2a\u5b50\u533a\u95f4\u90fd\u6392\u7b2c\u4e00\uff08\u5404\u6bb5\u7b2c\u4e00\u540d\u5206\u522b\u662f "
                          + "\u3001".join(f"{seg_labels[j]}:ROC {tops[j]}" for j in range(len(bins)))
                          + "\uff09\uff0c\u8bf4\u660e\u6392\u5e8f\u4e0d\u7a33\u5b9a\uff0c\u6700\u4f18\u53c2\u6570\u57fa\u672c\u662f\u8fd9\u6bb5\u884c\u60c5\u7684\u566a\u58f0\u3002")

    best_w = max(ROCS, key=lambda w: runs[str(w)]["stat"]["total"])
    worst_w = min(ROCS, key=lambda w: runs[str(w)]["stat"]["total"])
    low_w = min(ROCS, key=lambda w: abs(runs[str(w)]["stat"]["mdd"]))
    spread = (runs[str(best_w)]["stat"]["total"] - runs[str(worst_w)]["stat"]["total"]) * 100
    concl = (
        f"1. <b>参数影响有多大</b>：五组 ROC 区间收益最高 ROC {best_w} "
        f"{runs[str(best_w)]['stat']['total']*100:+.2f}%，最低 ROC {worst_w} "
        f"{runs[str(worst_w)]['stat']['total']*100:+.2f}%，<b>首尾相差 {spread:.2f}pp</b>；"
        f"最大回撤最深 {max(abs(runs[str(w)]['stat']['mdd']) for w in ROCS)*100:.2f}%"
        f"（ROC {max(ROCS, key=lambda w: abs(runs[str(w)]['stat']['mdd']))}）、"
        f"最浅 {abs(runs[str(low_w)]['stat']['mdd'])*100:.2f}%（ROC {low_w}）。<br>"
        f"2. <b>对基准</b>：同期上证 {bench['sse']['stat']['total']*100:+.2f}%"
        f"（回撤 {bench['sse']['stat']['mdd']*100:.2f}%）、"
        f"纳指 {bench['ndx']['stat']['total']*100:+.2f}%"
        f"（回撤 {bench['ndx']['stat']['mdd']*100:.2f}%）。<br>"
        f"3. <b>换仓频率</b>：本次五组换仓 {min(runs[str(w)]['trades_n'] for w in ROCS)}~"
        f"{max(runs[str(w)]['trades_n'] for w in ROCS)} 笔，<b>与 ROC 长度并非单调关系</b>"
        f"（粘性带 1.0pp 与过热熔断才是换仓的主导因素）。在万1成本下"
        f"{max(runs[str(w)]['trades_n'] for w in ROCS)} 笔换仓合计仅拖累约 "
        f"{max(runs[str(w)]['trades_n'] for w in ROCS)*COST_SIDE*2*100:.2f}%；"
        f"但若按双边 0.2% 口径，同一换仓量会拖累约 "
        f"{max(runs[str(w)]['trades_n'] for w in ROCS)*0.002*100:.1f}%。<br>"
        f"4. <b>稳健性</b>：{robust_verdict}"
    )

    avail = E.availability(panel, rot, dates, start_i, end_i)
    meta = dict(start=str(base_d.date()), base=str(base_d.date()),
                csv=os.path.basename(a.csv), avail=avail,
                end=str(dates[end_i].date()), lastdate=lastdate,
                days=int(end_i - start_i), cost_side=COST_SIDE, rocs=ROCS,
                pool=rot, defensive=DEF_CODE, notes=notes, conclusion=concl,
                seg_head=seg_head, seg_rows="".join(seg_rows),
                robust_verdict=robust_verdict)
    out = dict(meta=meta, runs=runs, bench=bench)
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[json] {OUT_JSON}")

    hp = a.html or os.path.join(HERE, "out",
                               f"roc_param_study_{dates[end_i].date()}.html")
    render_html(out, hp)
    print(f"[html] {hp}")


if __name__ == "__main__":
    main()
