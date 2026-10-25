# -*- coding: utf-8 -*-
"""生成规则对比报告：512890 回踩买/+3% 卖  vs  512040 −3%/+6%（原策略）。

依赖 ve_rule_compare.py 产出的 out/ve_rule_compare.json。
"""
import os
import sys
import json

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

C_A = "#3d57e5"      # 蓝
C_B = "#d4380d"      # 红
C_MUT = "#98a1ad"


def _poly(vals, x0, y0, w, h, lo, hi):
    n = len(vals)
    if n < 2 or hi <= lo:
        return ""
    return " ".join(
        f"{x0 + w * i / (n - 1):.1f},{y0 + h - h * (float(v) - lo) / (hi - lo):.1f}"
        for i, v in enumerate(vals))


def svg_chart(S, *, W=980, H=390):
    """主对比双曲线：A（512890 新规则）vs B（512040 原策略）+ 参照虚线 + 持仓色带。"""
    sa, sb = S["A"], S["B"]
    s1 = S.get("A1")
    pl, pr, pt, pb = 62, 104, 16, 56
    iw, ih = W - pl - pr, H - pt - pb

    dts = pd.to_datetime(sa["dates"])
    allv = sa["nav"] + sb["nav"]
    if s1:
        allv += s1["nav"]
    lo, hi = min(allv), max(allv)
    step = next((s for s in (0.1, 0.2, 0.25, 0.5, 1.0) if (hi - lo) / s <= 6), (hi - lo) / 5)
    g0 = np.ceil(lo / step) * step
    g1 = np.floor(hi / step) * step
    ticks, t = [], g0
    while t <= g1 + 1e-9:
        ticks.append(round(float(t), 4))
        t += step
    if not ticks:
        ticks = [round(lo, 2), round(hi, 2)]

    o = [f'<svg class="csvg" viewBox="0 0 {W} {H}" width="100%" '
         f'style="max-width:{W}px;display:block" xmlns="http://www.w3.org/2000/svg">',
         '<rect width="100%" height="100%" fill="#ffffff"/>']

    for tv in ticks:
        y = pt + ih - ih * (tv - lo) / (hi - lo)
        o.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}" '
                 f'stroke="#eef0f3" stroke-width="1"/>')
        o.append(f'<text x="{pl-9}" y="{y+4:.1f}" text-anchor="end" '
                 f'font-size="11" fill="#6c717f">{tv:.2f}</text>')

    seen = set()
    for i, d in enumerate(dts):
        if d.year not in seen:
            seen.add(d.year)
            x = pl + iw * i / (len(dts) - 1)
            o.append(f'<line x1="{x:.1f}" y1="{pt+ih}" x2="{x:.1f}" y2="{pt+ih+4}" '
                     f'stroke="#c9cdd6" stroke-width="1"/>')
            o.append(f'<text x="{x:.1f}" y="{pt+ih+18}" text-anchor="middle" '
                     f'font-size="11" fill="#6c717f">{d.year}</text>')

    # 参照组 A1（浅蓝细虚线，置于底层）
    if s1:
        pts = _poly(s1["nav"], pl, pt, iw, ih, lo, hi)
        o.append(f'<polyline points="{pts}" fill="none" stroke="#a8b4f0" '
                 f'stroke-width="1.4" stroke-dasharray="4 3"/>')

    # 买入持有（浅灰）
    for s in (sa, sb):
        pts = _poly(s["bh"], pl, pt, iw, ih, lo, hi)
        o.append(f'<polyline points="{pts}" fill="none" stroke="#d3d8e0" '
                 f'stroke-width="1.2" stroke-dasharray="3 3"/>')

    # 主曲线
    for s, col, wid in ((sb, C_B, 2.2), (sa, C_A, 2.2)):
        pts = _poly(s["nav"], pl, pt, iw, ih, lo, hi)
        o.append(f'<polyline points="{pts}" fill="none" stroke="{col}" '
                 f'stroke-width="{wid}" stroke-linejoin="round"/>')

    # 买卖点
    for key, s in (("A", sa), ("B", sb)):
        dmap = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(pd.to_datetime(s["dates"]))}
        for ev in s["events"]:
            i = dmap.get(ev["date"])
            if i is None:
                continue
            x = pl + iw * i / (len(s["nav"]) - 1)
            y = pt + ih - ih * (s["nav"][i] - lo) / (hi - lo)
            if ev["act"] == "BUY":
                o.append(f'<path d="M{x:.1f},{y-6.5:.1f} L{x-3.8:.1f},{y+1:.1f} '
                         f'L{x+3.8:.1f},{y+1:.1f} Z" fill="#d4380d"/>')
            elif ev["act"] == "SELL":
                o.append(f'<path d="M{x:.1f},{y+6.5:.1f} L{x-3.8:.1f},{y-1:.1f} '
                         f'L{x+3.8:.1f},{y-1:.1f} Z" fill="#0f9d58"/>')

    # 持仓色带
    rb_h = 7.0
    for idx, (s, col, tag) in enumerate(((sa, C_A, "A"), (sb, C_B, "B"))):
        yb = pt + ih + 26 + idx * (rb_h + 3)
        hold = s["hold"]
        i = 0
        while i < len(hold):
            if hold[i]:
                j = i
                while j + 1 < len(hold) and hold[j + 1]:
                    j += 1
                x1 = pl + iw * i / (len(hold) - 1)
                x2 = pl + iw * j / (len(hold) - 1)
                o.append(f'<rect x="{x1:.1f}" y="{yb}" width="{max(x2-x1,1.2):.1f}" '
                         f'height="{rb_h}" fill="{col}" opacity="0.42" rx="1.5"/>')
                i = j + 1
            else:
                i += 1
        o.append(f'<text x="{pl-9}" y="{yb+rb_h-1:.1f}" text-anchor="end" '
                 f'font-size="9" font-weight="700" fill="{col}">{tag}</text>')

    # 末端标注
    for s, col in ((sa, C_A), (sb, C_B)):
        y = pt + ih - ih * (s["nav"][-1] - lo) / (hi - lo)
        o.append(f'<text x="{pl+iw+7}" y="{y+3:.1f}" font-size="11.5" '
                 f'font-weight="700" fill="{col}">{s["nav"][-1]:.3f}</text>')

    # 图例
    lx, ly = pl + 6, pt + 3
    items = [(C_A, "A · 512890 回踩买/+3%卖", "solid", 2.2),
             (C_B, "B · 512040 −3%买/+6%卖", "solid", 2.2)]
    if s1:
        items.append(("#a8b4f0", "A1 · 512890 回踩买/+6%卖", "dash", 1.4))
    items.append(("#d3d8e0", "买入持有", "dash", 1.2))
    for col, txt, kind, wid in items:
        dash = ' stroke-dasharray="4 3"' if kind == "dash" else ""
        o.append(f'<line x1="{lx}" y1="{ly}" x2="{lx+15}" y2="{ly}" stroke="{col}" '
                 f'stroke-width="{wid}"{dash}/>')
        o.append(f'<text x="{lx+20}" y="{ly+4}" font-size="10.5" fill="#42474f">{txt}</text>')
        lx += 20 + len(txt) * 6.4 + 14

    o.append("</svg>")
    return "".join(o)


def build_html(P):
    M = P["metrics"]
    C = P["configs"]
    a, b = M["A"], M["B"]
    a1, a0 = M.get("A1"), M.get("A0")
    tr = P["trades"]
    yr = P["yearly"]
    scan = {float(k): v for k, v in P["scan"].items()}
    w = P["window"]
    chart = svg_chart(P["series"])

    def row(name, ka, kb, better_high=True, fmt="{:.2f}", unit="%"):
        va, vb = ka, kb
        diff = va - vb
        if abs(diff) < 1e-9:
            win, mark = "—", 'class="win"'
        elif (diff > 0) == better_high:
            win, mark = "A", 'class="win a"'
        else:
            win, mark = "B", 'class="win b"'
        # 净值/收益类用红涨绿跌；风险类（回撤/波动）统一用深浅表达「更小更好」
        risk = name in ("最大回撤", "年化波动")
        ca = "tn" if risk else ("ta" if va > 0 else "dn")
        cb = "tn" if risk else ("tb" if vb > 0 else "dn")
        return (f'<tr><td>{name}</td><td class="{ca}">{fmt.format(va)}{unit}</td>'
                f'<td class="{cb}">{fmt.format(vb)}{unit}</td>'
                f'<td class="df">{diff:+.2f}pp</td><td {mark}>{win}</td></tr>')

    rows = [
        ("累计收益", a["ret"], b["ret"], True),
        ("年化收益", a["cagr"], b["cagr"], True),
        ("最大回撤", a["mdd"], b["mdd"], True),
        ("年化波动", a["vol"], b["vol"], False),
        ("夏普(2%无风险)", a["sharpe"], b["sharpe"], True),
        ("Calmar", a["calmar"], b["calmar"], True),
        ("持仓天数占比", a["hold_rate"], b["hold_rate"], True),
    ]
    trows = "".join(row(n, x, y, bh) for n, x, y, bh in rows)
    trows += (f'<tr><td>买卖次数</td><td class="ta">{a["n_buy"]}买/{a["n_sell"]}卖</td>'
              f'<td class="tb">{b["n_buy"]}买/{b["n_sell"]}卖</td><td class="df">—</td>'
              f'<td class="win">—</td></tr>')
    trows += (f'<tr><td>平均单笔盈亏</td>'
              f'<td class="ta">{np.mean([t["pnl"] for t in tr["A"]]):+.2f}%</td>'
              f'<td class="tb">{np.mean([t["pnl"] for t in tr["B"]]):+.2f}%</td>'
              f'<td class="df">—</td><td class="win">—</td></tr>')
    trows += (f'<tr><td>平均持有天数</td>'
              f'<td class="ta">{np.mean([t["days"] for t in tr["A"]]):.0f} 日</td>'
              f'<td class="tb">{np.mean([t["days"] for t in tr["B"]]):.0f} 日</td>'
              f'<td class="df">—</td><td class="win">—</td></tr>')
    trows += (f'<tr><td>买入持有收益</td><td class="ta">{a["bh_ret"]:+.2f}%</td>'
              f'<td class="tb">{b["bh_ret"]:+.2f}%</td><td class="df">—</td>'
              f'<td class="win">—</td></tr>')

    # 逐笔
    def tblock(key, col):
        out = [f'<div class="tcard {col}"><h3>{C[key]["label"]}</h3>'
               f'<div class="sum">净值 <b>{M[key]["final"]:.4f}</b> · '
               f'{M[key]["ret"]:+.2f}% · MDD {M[key]["mdd"]:.2f}%</div><table class="tt">']
        for t in tr[key]:
            mark = ' class="now"' if t["sell"] == "持有中" else ""
            out.append(f'<tr{mark}><td>{t["buy"]}</td><td>{t["sell"]}</td>'
                       f'<td class="n">{t["days"]}d</td>'
                       f'<td class="p {"up" if t["pnl"]>0 else "dn"}">{t["pnl"]:+.2f}%</td></tr>')
        avg = np.mean([t["pnl"] for t in tr[key]])
        out.append(f'</table><div class="avg">{len(tr[key])} 笔 · 平均 '
                   f'<b>{avg:+.2f}%</b> · 平均持有 '
                   f'{np.mean([t["days"] for t in tr[key]]):.0f} 日</div></div>')
        return "".join(out)

    yrows = "".join(
        f'<tr><td>{y}</td>'
        f'<td class="ta {("up" if yr["A"].get(y,{}).get("ret",0)>0 else "dn")}">'
        f'{yr["A"].get(y,{}).get("ret",0):+.2f}%</td>'
        f'<td class="tb {("up" if yr["B"].get(y,{}).get("ret",0)>0 else "dn")}">'
        f'{yr["B"].get(y,{}).get("ret",0):+.2f}%</td></tr>'
        for y in sorted(yr["A"].keys()))

    # 卖线扫描
    base3 = scan.get(1.03, {}).get("ret", 0)
    srows = []
    for s in sorted(scan.keys()):
        v = scan[s]
        hi = ' class="pk"' if s == 1.08 else ""
        srows.append(f'<tr{hi}><td>超 MA200 +{(s-1)*100:.0f}%</td>'
                     f'<td>{v["nav"]:.4f}</td><td>{v["cagr"]:+.2f}%</td>'
                     f'<td>{v["mdd"]:.2f}%</td><td>{v["hold"]:.1f}%</td>'
                     f'<td>{v["n_buy"]}买/{v["n_sell"]}卖</td>'
                     f'<td class="{"up" if v["ret"]>base3 else "dn"}">'
                     f'{v["ret"]-base3:+.2f}pp</td></tr>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>512890 回踩买/+3%卖 vs 512040 原策略</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;padding:22px 18px 40px;background:#eef0f3;color:#202020;
  font:14px/1.62 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1010px;margin:0 auto}}
.hero{{background:linear-gradient(120deg,#3d57e5,#4a64e8,#566de7);color:#fff;
  border-radius:12px;padding:20px 24px;margin-bottom:14px;
  box-shadow:0 1px 3px rgba(16,24,40,.05)}}
.hero h1{{margin:0 0 8px;font-size:19px;font-weight:700}}
.hero p{{margin:0;font-size:12.5px;opacity:.94;line-height:1.75}}
.card{{background:#fff;border:1px solid #e2e5ea;border-radius:12px;padding:16px 18px;
  margin-bottom:14px;box-shadow:0 1px 3px rgba(16,24,40,.05)}}
.card h2{{margin:0 0 12px;font-size:14.5px;font-weight:700}}
.card h2 i{{font-style:normal;color:#3d57e5;margin-right:6px}}
.tblwrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:12.8px}}
th,td{{padding:7px 8px;border-bottom:1px solid #eef0f3;white-space:nowrap}}
th{{background:#f4f6fa;color:#6c717f;font-weight:600;font-size:12px;text-align:right}}
th:first-child{{text-align:left}}
td:first-child{{color:#42474f;text-align:left}}
.ta{{color:#3d57e5;font-weight:600;text-align:right}}
.tb{{color:#d4380d;font-weight:600;text-align:right}}
.df{{text-align:right;color:#6c717f}}
.win{{text-align:center;font-size:11px;font-weight:700}}
.win.a{{color:#3d57e5}} .win.b{{color:#d4380d}}
.up{{color:#d4380d}} .dn{{color:#0f9d58}}
.good{{color:#3d57e5}}
.tn{{color:#42474f;font-weight:600;text-align:right}}
.csvg{{overflow:hidden;max-width:100%;height:auto}}
.legend{{font-size:11.5px;color:#6c717f;margin-top:6px;white-space:normal;line-height:1.7}}
.tsplit{{display:flex;gap:12px;flex-wrap:wrap}}
.tcard{{flex:1 1 300px;min-width:0;border:1px solid #e2e5ea;border-radius:10px;padding:11px 13px}}
.tcard.a{{background:#f7f9ff;border-color:#dde4fb}}
.tcard.b{{background:#fff9f7;border-color:#fbded4}}
.tcard h3{{margin:0 0 3px;font-size:12.5px;font-weight:700;white-space:normal;line-height:1.5}}
.tcard.a h3{{color:#3d57e5}} .tcard.b h3{{color:#d4380d}}
.tcard .sum{{font-size:11.5px;color:#6c717f;margin-bottom:7px;white-space:normal}}
.tcard .sum b{{color:#202020}}
table.tt{{font-size:12px}}
table.tt th,table.tt td{{padding:4px 6px;border-bottom:1px solid #eef0f3;text-align:right}}
table.tt tr.now td{{background:#fffbe6}}
table.tt td.n{{color:#8a909c;font-size:11px}}
table.tt td.p{{font-weight:600}}
.tcard .avg{{margin-top:7px;font-size:11.5px;color:#6c717f;text-align:right;white-space:normal}}
.tcard .avg b{{color:#202020;font-size:12.5px}}
tr.pk td{{background:#fff8e6;font-weight:600}}
.note{{background:#fffaf4;border:1px solid #ffe3c8;border-radius:9px;padding:12px 14px;
  font-size:12.8px;color:#7a4f22;line-height:1.78}}
.note b{{color:#b35309}}
.warn{{background:#fff5f5;border-color:#ffd6d6;color:#8a2b2b}}
.warn b{{color:#c0392b}}
.note p{{margin:0 0 8px}} .note p:last-child{{margin-bottom:0}}
ul{{margin:8px 0 0;padding-left:20px;font-size:13px;line-height:1.85}}
li{{margin-bottom:5px}}
@media(max-width:700px){{
  body{{padding:14px 10px 30px}} .card{{padding:13px 12px}}
  .hero{{padding:16px}} .hero h1{{font-size:16px}}
  .tsplit{{flex-direction:column}}
}}
</style></head><body><div class="wrap">

<div class="hero">
  <h1>512890「回踩买 / +3% 卖」 vs 512040 原策略「−3% 买 / +6% 卖」</h1>
  <p>窗口 {w['d0']} ~ {w['d1']}｜前复权 · 零手续费 · 初始净值 1 · 收益按 k−1 日状态结算（无未来函数）<br>
     A = 512890：收盘回踩 MA200 即买（×1.00），高于 MA200 3% 即卖（×1.03）｜
     B = 512040：收盘低于 MA200 3% 买（×0.97），高于 6% 卖（×1.06）</p>
</div>

<div class="card">
  <h2><i>①</i>结论先说：卖出线收到 +3% 反而更差</h2>
  <div class="note">
    <p><b>A（1.6628 / +66.28%）跑输 B（2.0122 / +101.22%）达 {b['ret']-a['ret']:.2f}pp。</b>
    把买入线从 −3% 放宽到「回踩 MA200」确实提高了触发率（持仓 18.3% → {a['hold_rate']:.1f}%，
    交易 5 笔 → {a['n_buy']} 笔），方向是对的；但<b>同时把卖出线从 +6% 收到 +3%，
    等于把每笔利润的天花板压低了一半</b>，两下相抵还倒亏。</p>
    <p>同比证据：<b>只改买入线、卖出线保持 +6%（A1）</b>，512890 净值直接到
    <b>{a1['final']:.4f}（{a1['ret']:+.2f}%）</b>，不但反超 B，年化 {a1['cagr']:+.2f}%、
    夏普 {a1['sharpe']:.2f}、Calmar {a1['calmar']:.2f} 全面更好，回撤还更浅（{a1['mdd']:.2f}% vs {b['mdd']:.2f}%）。
    ⇒ <b>买入线该放宽，卖出线不该收紧。</b></p>
  </div>
</div>

<div class="card">
  <h2><i>②</i>净值曲线</h2>
  <div class="tblwrap">{chart}</div>
  <div class="legend">粗实线 = A（蓝）/ B（红）；浅蓝细虚线 = A1（512890 回踩买 / +6% 卖，参照组）；
    浅灰虚线 = 各自买入持有；底部色带 = 持仓时段。</div>
</div>

<div class="card">
  <h2><i>③</i>指标对照</h2>
  <div class="tblwrap"><table>
    <tr><th>指标</th><th>A · 512890 回踩/+3%</th><th>B · 512040 −3%/+6%</th><th>差值</th><th>胜</th></tr>
    {trows}
  </table></div>
</div>

<div class="card">
  <h2><i>④</i>逐笔交易拆解 —— 问题就出在这里</h2>
  <div class="tsplit">{tblock("A", "a")}{tblock("B", "b")}</div>
  <div class="note" style="margin-top:12px">
    A 共 {len(tr["A"])} 笔、平均单笔 <b>{np.mean([t["pnl"] for t in tr["A"]]):+.2f}%</b>、
    平均持有 {np.mean([t["days"] for t in tr["A"]]):.0f} 日；
    B 共 {len(tr["B"])} 笔、平均单笔 <b>{np.mean([t["pnl"] for t in tr["B"]]):+.2f}%</b>、
    平均持有 {np.mean([t["days"] for t in tr["B"]]):.0f} 日。
    <b>同样的行情里，A 每次只拿 3~6% 就被 +3% 卖线踢出来，B 能拿到 10~17%。</b>
    交易次数多了一倍，但每次赚的不到一半 —— 这就是「频繁进出反而少赚」的典型。
  </div>
</div>

<div class="card">
  <h2><i>⑤</i>卖出线扫描：+3% 是整条曲线上最差的位置之一</h2>
  <div class="tblwrap"><table>
    <tr><th>卖出线（买入固定 = 回踩 MA200）</th><th>净值</th><th>年化</th>
        <th>最大回撤</th><th>持仓占比</th><th>买卖</th><th>相对 +3% 的收益差</th></tr>
    {''.join(srows)}
  </table></div>
  <div class="legend">净值随卖出线放宽先升后降，峰值在 <b>+8%</b>（{scan.get(1.08,{}).get("nav",0):.4f}）；
    +3% 处为 {scan.get(1.03,{}).get("nav",0):.4f}，是全部候选里偏低的。
    卖出线太紧 → 频繁被踢出、吃不到主升；太松（≥12%）→ 卖出信号几乎不触发、退化成接近满仓持有。</div>
</div>

<div class="card">
  <h2><i>⑥</i>逐年表现</h2>
  <div class="tblwrap"><table>
    <tr><th>年份</th><th>A · 512890 回踩/+3%</th><th>B · 512040 −3%/+6%</th></tr>
    {yrows}
  </table></div>
  <div class="legend">2021 年只含 10/21 起的 51 个交易日（A 组 51 日、B 组 52 日；
    A 少一天是因 512890 在 2021-10-22 份额拆分当日停牌）。</div>
</div>

<div class="card">
  <h2><i>⑦</i>四组参数横比（同一窗口）</h2>
  <div class="tblwrap"><table>
    <tr><th>编号</th><th>标的 / 规则</th><th>净值</th><th>年化</th><th>最大回撤</th>
        <th>夏普</th><th>Calmar</th><th>持仓</th></tr>
    <tr><td>A</td><td>512890 · 回踩买 / +3% 卖</td><td>{a['final']:.4f}</td>
        <td>{a['cagr']:+.2f}%</td><td>{a['mdd']:.2f}%</td><td>{a['sharpe']:.2f}</td>
        <td>{a['calmar']:.2f}</td><td>{a['hold_rate']:.1f}%</td></tr>
    <tr><td>B</td><td>512040 · −3% 买 / +6% 卖（原策略）</td><td>{b['final']:.4f}</td>
        <td>{b['cagr']:+.2f}%</td><td>{b['mdd']:.2f}%</td><td>{b['sharpe']:.2f}</td>
        <td>{b['calmar']:.2f}</td><td>{b['hold_rate']:.1f}%</td></tr>
    <tr class="pk"><td>A1</td><td>512890 · 回踩买 / +6% 卖（最优推荐）</td><td>{a1['final']:.4f}</td>
        <td>{a1['cagr']:+.2f}%</td><td>{a1['mdd']:.2f}%</td><td>{a1['sharpe']:.2f}</td>
        <td>{a1['calmar']:.2f}</td><td>{a1['hold_rate']:.1f}%</td></tr>
    <tr><td>A0</td><td>512890 · −3% 买 / +6% 卖（原参数）</td><td>{a0['final']:.4f}</td>
        <td>{a0['cagr']:+.2f}%</td><td>{a0['mdd']:.2f}%</td><td>{a0['sharpe']:.2f}</td>
        <td>{a0['calmar']:.2f}</td><td>{a0['hold_rate']:.1f}%</td></tr>
  </table></div>
</div>

<div class="card">
  <h2><i>⑧</i>风险提示与数据口径</h2>
  <ul>
    <li><b>单窗口、单标的、事后结果。</b>上面所有数字都是 2021-10~2026-10 这一个 5 年窗口跑出来的，
      阈值又是对比多组挑出来的，存在<b>选参痕迹</b>；换窗口、换标的排序可能翻转。
      不能据此认为 A1 就是 512890 的「正确参数」。</li>
    <li><b>A1 的优势有一个合理解释，但也仅此而已</b>：回踩 MA200 买、+6% 卖，本质是
      「低波品种的均值回复」——512890 波动小、偏离 MA200 的幅度窄（p10 −1.55%、
      p90 +8.79%），+6% 恰好落在它偏离分布的高分位附近，卖出不会过早。
      而 +3% 只到中位数附近，天然容易被打断。</li>
    <li><b>512890 在数据层有一个隐藏雷</b>：它 2021-10-22 做了 1:2 份额拆分
      （净值 1.6004 → 0.8002，华泰柏瑞公告），新浪在线日线未回溯复权，
      在 2021-10-25 报 −51.13%，而 <code>factors=1.0</code> 是常数因子修不掉；
      这根假跳空<b>正落在本 5 年窗口的第 2 个交易日</b>。
      本报告全部用已核验长历史前复权 CSV 打底 + 在线补 13 日，窗口内无 |日收益| &gt; 8% 的异常。</li>
    <li><b>结算口径</b>：k−1 日收盘后的状态决定 k−1→k 的涨跌（信号当日成形、次日才吃涨跌），
      与 <code>etf_backtest_long.backtest()</code> 一致，无未来函数。</li>
  </ul>
</div>

</div></body></html>"""


def main():
    p = os.path.join(OUT, "ve_rule_compare.json")
    P = json.load(open(p, encoding="utf-8"))
    html = build_html(P)
    out = os.path.join(OUT, "规则对比_512890回踩3卖_vs_512040.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[saved] {out} ({len(html)} chars)")
    return out


if __name__ == "__main__":
    main()
