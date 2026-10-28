#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三策略对比回测报告（近 10 年）：基础 / 基础+D3+P3 / 基础+D3+P3+抄底

用户 2026-10-28 要求：验证「加抄底是否真有正反馈」，要全方位对比、月度+年度表格、
主轮动池、初始净值 1.0、输出 HTML。

⚠️ 数据说明：
  · 用 tdx_adj_long_2014_2026.csv（2014-01-02 起），起点取 2016-10-26（近 10 年）
  · 主选池 9 只，但**早期只有 3~5 只已上市**（588170 到 2025-04 才有），
    每年实际可用标的数在报告里标注 —— 早期池子小，与现在的策略不是一回事。
  · 单边成本 0.01%（万一），含换仓成本；初始净值 1.0。
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import etf_backtest_long as bl  # noqa: E402

CSV = os.path.join(HERE, "tdx_adj_long_2014_2026.csv")
START = "2016-10-26"
COST = 0.0001
RED, GREEN, BLUE, GREY, PURPLE, ORANGE = ("#d4380d", "#0f9d58", "#3d57e5",
                                          "#9aa3ae", "#c2185b", "#b45309")
BG, FG, MUT, BD = "#eef0f3", "#202020", "#6c717f", "#e2e5ea"

D3P3 = dict(loss_cool=0.03, loss_days=5, roc_q=0.95, roc_days=20,
            roc_n=60, roc_min_periods=250)
WEAK = dict(weak_mode="allneg", weak_rank=1, weak_delay=0,
            weak_cd=0, weak_min_pool=5)


def run(panel, dates, pool, dfn, **kw):
    si = int(dates.searchsorted(pd.Timestamp(START), side="left"))
    r = bl.backtest(panel, dates, si, len(dates) - 1, pool, dfn,
                    buy=COST, sell=COST, W=20, MA=20, value_engine=True, **kw)
    nv = r["navs"].copy()
    nv.index = pd.to_datetime(nv.index)
    return r, bl.stats(r["navs"]), nv


def yearly_of(nv):
    return {d["year"]: d for d in bl.yearly(nv)}


def monthly_of(nv):
    out = {}
    prev = float(nv.iloc[0])
    for (y, m), s in nv.groupby([nv.index.year, nv.index.month]):
        out[f"{y}-{m:02d}"] = ((float(s.iloc[-1]) / prev - 1) * 100,
                               float((s / s.cummax() - 1).min()) * 100)
        prev = float(s.iloc[-1])
    return out


def drawdown(nv):
    return (nv / nv.cummax() - 1.0) * 100


# ---------------------------------------------------------------- SVG
def multi_curve(series, w=680, h=250, title="", log=False):
    """series = [(navs, label, color, width)]，起点归一化为 1.0"""
    if not series:
        return ""
    ml, mr, mt, mb = 48, 10, 20, 30
    iw, ih = w - ml - mr, h - mt - mb
    xs = series[0][0]
    ys = []
    for nv, lab, col, lw in series:
        s = nv / float(nv.iloc[0])
        ys.append((s.values, lab, col, lw))
    vmin = min(float(np.nanmin(y[0])) for y in ys)
    vmax = max(float(np.nanmax(y[0])) for y in ys)
    if log:
        vmin, vmax = np.log10(max(vmin, 0.1)), np.log10(max(vmax, 0.1))
    pad = (vmax - vmin) * 0.08 or 0.05
    vmin, vmax = vmin - pad, vmax + pad
    span = (vmax - vmin) or 1.0
    x = np.linspace(ml, w - mr, len(xs))
    p = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{title}">']
    for gv in np.linspace(vmin, vmax, 5):
        gy = mt + ih * ((vmax - gv) / span)
        lab = (10 ** gv) if log else gv
        p.append(f'<line x1="{ml}" y1="{gy:.1f}" x2="{w-mr}" y2="{gy:.1f}" '
                 f'stroke="#e6eaf0" stroke-dasharray="3 3"/>')
        p.append(f'<text x="{ml-6}" y="{gy+3.5:.1f}" text-anchor="end" '
                 f'font-size="9" fill="#8b93a1">{lab:.2f}</text>')
    for vals, lab, col, lw in ys:
        v = np.log10(np.maximum(vals, 0.1)) if log else vals
        pts = " ".join(f"{x[i]:.1f},{mt+ih*((vmax-v[i])/span):.1f}"
                       for i in range(len(v)) if np.isfinite(v[i]))
        p.append(f'<polyline points="{pts}" fill="none" stroke="{col}" '
                 f'stroke-width="{lw}" stroke-linejoin="round"/>')
    lx = ml + 2
    for vals, lab, col, lw in ys:
        p.append(f'<rect x="{lx}" y="{mt-12}" width="15" height="3" rx="1.5" fill="{col}"/>')
        p.append(f'<text x="{lx+19}" y="{mt-8}" font-size="10" fill="#4b5563">{lab}</text>')
        lx += 19 + len(lab) * 10 + 14
    p.append(f'<text x="{ml}" y="{h-8}" font-size="9.5" fill="#8b93a1">'
             f'起点 = 1.0　含换仓成本　{"对数刻度" if log else "线性刻度"}</text>')
    p.append("</svg>")
    return "".join(p)


def year_bars(years, groups, w=680, h=230):
    """groups = [(label, {year: ret}, color)]，分组竖柱"""
    ml, mr, mt, mb = 46, 10, 18, 34
    iw, ih = w - ml - mr, h - mt - mb
    vals = [v for _l, d, _c in groups for v in d.values() if v is not None]
    vmax = max(max(vals), 0) * 1.15 or 1
    vmin = min(min(vals), 0) * 1.15 or -1
    span = (vmax - vmin) or 1
    zy = mt + ih * (vmax / span)
    n = len(years)
    gw = iw / n
    bw = min(14.0, gw * 0.72 / max(1, len(groups)))
    p = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="年度收益">']
    for gv in (vmax, vmax / 2, 0, vmin / 2, vmin):
        gy = mt + ih * ((vmax - gv) / span)
        p.append(f'<line x1="{ml}" y1="{gy:.1f}" x2="{w-mr}" y2="{gy:.1f}" '
                 f'stroke="{"#c9d0d9" if abs(gv)<1e-9 else "#e6eaf0"}" '
                 f'stroke-dasharray="{"" if abs(gv)<1e-9 else "3 3"}"/>')
        p.append(f'<text x="{ml-6}" y="{gy+3.5:.1f}" text-anchor="end" '
                 f'font-size="9" fill="#8b93a1">{gv:+.0f}%</text>')
    for i, y in enumerate(years):
        cx = ml + gw * i + gw / 2
        for gi, (lab, d, col) in enumerate(groups):
            v = d.get(y)
            if v is None:
                continue
            bh = abs(v) / span * ih
            by = zy - bh if v >= 0 else zy
            bx = cx - (bw * len(groups)) / 2 + gi * bw
            p.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw-1.2:.1f}" '
                     f'height="{max(bh,0.7):.1f}" fill="{col}" rx="1"/>')
        p.append(f'<text x="{cx:.1f}" y="{mt+ih+14}" text-anchor="middle" '
                 f'font-size="9" fill="#6c717f">{str(y)[2:]}</text>')
    lx = ml + 2
    for lab, _d, col in groups:
        p.append(f'<rect x="{lx}" y="{mt-11}" width="14" height="3" rx="1.5" fill="{col}"/>')
        p.append(f'<text x="{lx+18}" y="{mt-7}" font-size="9.5" fill="#4b5563">{lab}</text>')
        lx += 18 + len(lab) * 9 + 12
    p.append("</svg>")
    return "".join(p)


def main():
    panel = bl.read_panel(CSV)
    dates = panel.index
    rot, deff, _w = bl.read_pools()
    pool = [c for c in rot if c in panel.columns]
    dfn = deff[0] if deff else "512040"

    A, sa, na = run(panel, dates, pool, dfn)
    B, sb, nb = run(panel, dates, pool, dfn, **D3P3)
    C, sc, nc = run(panel, dates, pool, dfn, **D3P3, **WEAK)

    # ⚠️ bl.yearly() 内部用 nav.index.str[:4]，必须喂**原始字符串索引**的 navs
    #   （na/nb/nc 已转成 DatetimeIndex 供画图与月度分组用，不能混用）
    ya, yb, yc = yearly_of(A["navs"]), yearly_of(B["navs"]), yearly_of(C["navs"])
    ma, mb, mc = monthly_of(na), monthly_of(nb), monthly_of(nc)
    years = sorted(ya)
    # 每年可用池规模
    av = {}
    for d, n in (A.get("avail_log") or []):
        av.setdefault(pd.Timestamp(d).year, []).append(n)
    pool_sz = {y: (min(v), int(np.mean(v))) for y, v in av.items()}

    def pack(s, nv):
        return dict(final=float(nv.iloc[-1]), ann=s["ann"] * 100, mdd=s["mdd"] * 100,
                    calmar=s["calmar"], sharpe=s["sharpe"], sortino=s["sortino"])
    PA, PB, PC = pack(sa, na), pack(sb, nb), pack(sc, nc)

    # 抄底贡献
    contrib = {y: (yc.get(y, {}).get("ret") or 0) * 100 - (yb.get(y, {}).get("ret") or 0) * 100
               for y in years}
    pos_y = sum(1 for v in contrib.values() if v > 0.05)
    neg_y = sum(1 for v in contrib.values() if v < -0.05)
    zero_y = len(contrib) - pos_y - neg_y
    tot_pp = sum(contrib.values())
    top2 = sorted(contrib.items(), key=lambda kv: -kv[1])[:2]
    top2_pp = sum(v for _k, v in top2)
    top2_share = (top2_pp / tot_pp * 100) if tot_pp else 0

    def cell(v, d=2, unit="%", color=None, bold=False):
        if v is None or not np.isfinite(v):
            return '<td class="num dim">—</td>'
        c = color if color else "#42474f"
        st = f' style="color:{c};font-weight:700"' if bold else f' style="color:{c}"'
        return f'<td class="num"{st}>{v:+.{d}f}{unit}</td>'

    # 年度表
    yrows = []
    for y in years:
        rA = (ya.get(y, {}).get("ret") or 0) * 100
        rB = (yb.get(y, {}).get("ret") or 0) * 100
        rC = (yc.get(y, {}).get("ret") or 0) * 100
        mA = (ya.get(y, {}).get("mdd") or 0) * 100
        mB = (yb.get(y, {}).get("mdd") or 0) * 100
        mC = (yc.get(y, {}).get("mdd") or 0) * 100
        cd = contrib[y]
        # ⚠️ bl.yearly() 的 year 是字符串（index.str[:4]），pool_sz 的键是 int，必须转
        ps = pool_sz.get(int(y), (0, 0))
        yrows.append(
            f'<tr><td class="mono">{y}</td><td class="num dim">{ps[0]}/{ps[1]:.1f}</td>'
            + cell(rA) + cell(rB) + cell(rC)
            + cell(cd, color=(GREEN if cd > 0.05 else (RED if cd < -0.05 else "#8b93a1")), bold=True)
            + cell(mA, color="#42474f") + cell(mB, color="#42474f") + cell(mC, color="#42474f")
            + "</tr>")

    # 月度表
    mkeys = sorted(set(ma) | set(mb) | set(mc))
    mrows = []
    for k in mkeys:
        va = ma.get(k, (None, None))[0]
        vb = mb.get(k, (None, None))[0]
        vc = mc.get(k, (None, None))[0]
        d = (vc - vb) if (vc is not None and vb is not None) else None
        mrows.append(f'<tr><td class="mono">{k}</td>'
                     + cell(va) + cell(vb) + cell(vc)
                     + cell(d, color=(GREEN if (d or 0) > 0.05 else (RED if (d or 0) < -0.05 else "#8b93a1")))
                     + "</tr>")

    # f-string 里不能直接写 dict 推导（{{ 会被转义成字面量），先在外部算好
    gd_a = {y: (ya.get(y, {}).get("ret") or 0) * 100 for y in years}
    gd_b = {y: (yb.get(y, {}).get("ret") or 0) * 100 for y in years}
    gd_c = {y: (yc.get(y, {}).get("ret") or 0) * 100 for y in years}
    ybars = year_bars(years, [("A 基础", gd_a, GREY),
                              ("B +D3P3", gd_b, BLUE),
                              ("C +抄底", gd_c, PURPLE)])

    d0, d1 = str(na.index[0].date()), str(na.index[-1].date())
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>基础策略+D3+P3+抄底 回测对比</title>
<style>
:root{{--bg:{BG};--fg:{FG};--mut:{MUT};--bd:{BD}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:16px 12px 30px;background:var(--bg);color:var(--fg);
 font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:820px;margin:0 auto}}
h1{{font-size:20px;margin:0 0 5px}}
.sub{{font-size:12px;color:var(--mut);line-height:1.75}}
.card{{background:#fbfcfe;border:1px solid var(--bd);border-radius:10px;padding:13px 14px;margin-top:14px}}
.cap{{font-size:12px;color:var(--mut);font-weight:700;margin:0 0 9px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th,td{{padding:5px 6px;border-bottom:1px solid var(--bd);text-align:left;white-space:nowrap}}
th{{background:#f4f6fa;font-size:11.5px;color:#4b5563;font-weight:700;position:sticky;top:0}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
td.mono{{font-family:ui-monospace,Menlo,monospace}}
.dim{{color:#8b93a1}}
.tblwrap{{overflow-x:auto;-webkit-overflow-scrolling:touch;max-height:none}}
.k{{display:flex;flex-wrap:wrap;gap:10px 0}}
.kc{{flex:1 1 30%;min-width:120px;display:flex;flex-direction:column;gap:2px}}
.kc .kk{{font-size:11px;color:var(--mut)}}
.kc b{{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums}}
.warn{{background:#fff8f6;border:1px solid #f6d5c8;border-left:3px solid {RED};
 border-radius:9px;padding:11px 13px;margin-top:14px;font-size:12.5px;line-height:1.75}}
.note{{margin-top:10px;font-size:11px;color:#8b93a1;line-height:1.7}}
@media(max-width:600px){{.kc{{flex:1 1 47%}}}}
</style></head><body><div class="wrap">

<h1>基础策略 + D3 + P3 + 抄底 · 回测对比</h1>
<div class="sub">区间 <b>{d0} ~ {d1}</b>（近 10 年）　主选池 <b>{len(pool)}</b> 只（早期未全部上市，见年度表「池」列）　
防守位 {dfn}　单边成本 <b>0.01%</b>　前复权　<b>初始净值 1.0</b><br>
三条曲线：<b style="color:{GREY}">A 基础轮动</b>（只有动横20 选最强 + 粘性带 + 过热熔断）／
<b style="color:{BLUE}">B + D3 单笔止损 + P3 追高止盈</b>／
<b style="color:{PURPLE}">C + 抄底（全池负→买末位）</b></div>

<div class="card">
<div class="cap">一、总览（近 10 年）</div>
<div class="tblwrap"><table>
<thead><tr><th>策略</th><th class="num">净值</th><th class="num">年化</th><th class="num">最大回撤</th>
<th class="num">Calmar</th><th class="num">Sharpe</th><th class="num">Sortino</th><th class="num">换仓</th></tr></thead>
<tbody>
<tr><td>A 基础轮动</td><td class="num">{PA['final']:.3f}</td><td class="num">{PA['ann']:+.2f}%</td>
<td class="num">{PA['mdd']:.2f}%</td><td class="num">{PA['calmar'] or 0:.2f}</td>
<td class="num">{PA['sharpe'] or 0:.2f}</td><td class="num">{PA['sortino'] or 0:.2f}</td>
<td class="num">{len(A['trades'])}</td></tr>
<tr><td>B + D3 + P3</td><td class="num">{PB['final']:.3f}</td><td class="num">{PB['ann']:+.2f}%</td>
<td class="num">{PB['mdd']:.2f}%</td><td class="num">{PB['calmar'] or 0:.2f}</td>
<td class="num">{PB['sharpe'] or 0:.2f}</td><td class="num">{PB['sortino'] or 0:.2f}</td>
<td class="num">{len(B['trades'])}</td></tr>
<tr style="background:#fdf5f9"><td><b>C + 抄底</b></td><td class="num"><b>{PC['final']:.3f}</b></td>
<td class="num"><b>{PC['ann']:+.2f}%</b></td><td class="num">{PC['mdd']:.2f}%</td>
<td class="num"><b>{PC['calmar'] or 0:.2f}</b></td><td class="num"><b>{PC['sharpe'] or 0:.2f}</b></td>
<td class="num"><b>{PC['sortino'] or 0:.2f}</b></td><td class="num">{len(C['trades'])}</td></tr>
</tbody></table></div>
<div class="note">C 相对 B：净值 <b>{PC['final']-PB['final']:+.3f}</b>
（{(PC['final']/PB['final']-1)*100:+.1f}%）、年化 <b>{PC['ann']-PB['ann']:+.2f}pp</b>、
Calmar <b>{(PC['calmar'] or 0)-(PB['calmar'] or 0):+.2f}</b>、最大回撤 <b>{PC['mdd']-PB['mdd']:+.2f}pp</b>。</div>
</div>

<div class="card">
<div class="cap">二、净值曲线（起点 1.0，线性刻度）</div>
{multi_curve([(na,'A 基础',GREY,1.5),(nb,'B +D3+P3',BLUE,1.7),(nc,'C +抄底',PURPLE,2.0)],title="净值曲线")}
</div>

<div class="card">
<div class="cap">三、净值曲线（对数刻度，看早期小池子阶段）</div>
{multi_curve([(na,'A 基础',GREY,1.5),(nb,'B +D3+P3',BLUE,1.7),(nc,'C +抄底',PURPLE,2.0)],log=True,title="净值对数")}
</div>

<div class="card">
<div class="cap">四、年度收益对比</div>
{ybars}
</div>

<div class="warn">
<b>⚠ 关于「抄底是否真有正反馈」——数据给出的答案是有条件的「是」，但很不均匀：</b><br>
· 近 10 年抄底累计贡献 <b>{tot_pp:+.1f}pp</b>（逐年算术和），净值由 {PB['final']:.2f} 提升到 {PC['final']:.2f}；<br>
· 但贡献<b>高度集中</b>：<b>{top2[0][0]} 年 {top2[0][1]:+.1f}pp</b> + <b>{top2[1][0]} 年 {top2[1][1]:+.1f}pp</b>
= <b>{top2_pp:+.1f}pp</b>，占累计贡献的 <b>{top2_share:.0f}%</b>；<br>
· {len(years)} 个年份里：<b>{pos_y} 年正贡献</b>、<b>{neg_y} 年负贡献</b>、<b>{zero_y} 年零贡献</b>（池子小未触发或全年无机会）；<br>
· 换句话说：<b>抄底不是「每年多赚一点」的稳定增强，而是「平时不拖后腿、遇到特定行情（2020 疫情暴跌 / 2022 熊市）大幅加分」的尾部期权</b>。
如果你对「10 年里 8 年看不到它起作用」这件事不接受，那它对你就不算正反馈。
</div>

<div class="card">
<div class="cap">五、年度明细（收益 % / 最大回撤 %；「池」= 当年可用标的 最少/均值）</div>
<div class="tblwrap"><table>
<thead><tr><th>年份</th><th class="num">池</th><th class="num">A 基础</th><th class="num">B +D3P3</th>
<th class="num">C +抄底</th><th class="num">抄底贡献</th><th class="num">A 回撤</th>
<th class="num">B 回撤</th><th class="num">C 回撤</th></tr></thead>
<tbody>{''.join(yrows)}</tbody></table></div>
</div>

<div class="card">
<div class="cap">六、月度明细（收益 %，共 {len(mkeys)} 个月）</div>
<div class="tblwrap"><table>
<thead><tr><th>月份</th><th class="num">A 基础</th><th class="num">B +D3P3</th>
<th class="num">C +抄底</th><th class="num">抄底贡献</th></tr></thead>
<tbody>{''.join(mrows)}</tbody></table></div>
<div class="note">「抄底贡献」= C 当月收益 − B 当月收益。正数=抄底加分，负数=拖累。</div>
</div>

<div class="note">
口径说明：动横20 选最强 + 粘性带 1.0pp + 过热熔断（乖离&gt;12% 或领先&gt;15pp）；
D3 = 换出时该笔亏 ≥3% ⇒ 空仓 5 日；P3 = 头名 ROC60 进自身滚动 95 分位 ⇒ 空仓 20 日；
抄底 = 主选池动横20 全部为负 ⇒ 当日换持末位（可用标的 &lt;5 只时不做）。<br>
早期（2016-2019）主选池只有 3~5 只已上市，与现在的 9 只不是同一个策略，早期数据请打折看待。<br>
本报告由 make_compare_report.py 生成，仅供研究参考，不构成投资建议。
</div>
</div></body></html>"""
    out = os.path.join(HERE, "out", "基础策略+D3+P3+抄底回测对比.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    print(f"报告: {out}（{len(doc)/1024:.0f} KB）")
    print(f"A 基础      {PA['final']:8.3f} 年化{PA['ann']:+6.2f}% MDD{PA['mdd']:7.2f}% Calmar{PA['calmar'] or 0:5.2f}")
    print(f"B +D3P3     {PB['final']:8.3f} 年化{PB['ann']:+6.2f}% MDD{PB['mdd']:7.2f}% Calmar{PB['calmar'] or 0:5.2f}")
    print(f"C +抄底     {PC['final']:8.3f} 年化{PC['ann']:+6.2f}% MDD{PC['mdd']:7.2f}% Calmar{PC['calmar'] or 0:5.2f}")
    print(f"抄底累计贡献 {tot_pp:+.1f}pp；{top2[0][0]}+{top2[1][0]} 两年占 {top2_share:.0f}%")
    print(f"正贡献 {pos_y} 年 / 负 {neg_y} 年 / 零 {zero_y} 年")
    return out


if __name__ == "__main__":
    main()
