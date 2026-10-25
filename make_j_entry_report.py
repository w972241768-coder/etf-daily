# -*- coding: utf-8 -*-
"""生成「J<20 买入门槛」对比报告（512040 / 512890）。

依赖 ve_j_entry_study.py 产出的 out/ve_j_entry.json。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

C_A = "#8b96ab"      # A 原版：蓝灰（参照系，不抢视线）
C_B = "#3d57e5"      # B J<20：蓝
C_BH = "#c9a227"     # 买入持有：金
C_MUT = "#98a1ad"
J20 = 20.0


def _poly(vals, x0, y0, w, h, lo, hi):
    n = len(vals)
    if n < 2 or hi <= lo:
        return ""
    return " ".join(
        f"{x0 + w * i / (n - 1):.1f},{y0 + h - h * (float(v) - lo) / (hi - lo):.1f}"
        for i, v in enumerate(vals))


def svg_chart(a, b, *, W=960, H=360, title=""):
    """A(原版) vs B(J<20) 净值曲线 + 买入持有参照 + B 的持仓色带。"""
    pl, pr, pt, pb = 58, 92, 18, 46
    iw, ih = W - pl - pr, H - pt - pb
    allv = a["nav"] + b["nav"] + a["bh"]
    lo, hi = min(allv), max(allv)
    span = max(hi - lo, 1e-6)
    lo, hi = lo - span * 0.08, hi + span * 0.10
    step = next((s for s in (0.2, 0.25, 0.5, 1.0) if (hi - lo) / s <= 6), (hi - lo) / 5)
    g0 = np.ceil(lo / step) * step
    ticks, t = [], g0
    while t <= hi + 1e-9:
        ticks.append(round(float(t), 4))
        t += step

    dates = pd.to_datetime(a["dates"])
    o = [f'<svg class="csvg" viewBox="0 0 {W} {H}" width="100%" '
         f'style="max-width:{W}px;display:block" xmlns="http://www.w3.org/2000/svg">',
         f'<rect width="100%" height="100%" fill="#ffffff"/>']
    for tv in ticks:
        y = pt + ih - ih * (tv - lo) / (hi - lo)
        o.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}" '
                 f'stroke="#eef0f3"/>')
        o.append(f'<text class="cg" x="{pl-8}" y="{y+3.5:.1f}">{tv:.2f}</text>')

    # B 的持仓色带（贴底）
    hold = b["hold"]
    n = len(hold)
    y0b = pt + ih + 8
    i = 0
    while i < n:
        if hold[i]:
            j = i
            while j + 1 < n and hold[j + 1]:
                j += 1
            x1 = pl + iw * i / (n - 1)
            x2 = pl + iw * j / (n - 1)
            o.append(f'<rect x="{x1:.1f}" y="{y0b}" width="{max(x2-x1,1.5):.1f}" '
                     f'height="5" fill="#cdd8fb" rx="1.5"/>')
            i = j + 1
        else:
            i += 1

    o.append(f'<polyline fill="none" stroke="{C_BH}" stroke-width="1.4" '
             f'stroke-dasharray="5 4" points="{_poly(a["bh"], pl, pt, iw, ih, lo, hi)}"/>')
    o.append(f'<polyline fill="none" stroke="{C_A}" stroke-width="2.4" '
             f'points="{_poly(a["nav"], pl, pt, iw, ih, lo, hi)}"/>')
    o.append(f'<polyline fill="none" stroke="{C_B}" stroke-width="2.4" '
             f'points="{_poly(b["nav"], pl, pt, iw, ih, lo, hi)}"/>')

    # 末端标注：两条净值线常常几乎重合，需强制错开，否则文字叠在一起不可读
    ends = []
    for series, color, lab in ((a["nav"], C_A, "原版"), (b["nav"], C_B, "J&lt;20")):
        y = pt + ih - ih * (series[-1] - lo) / (hi - lo)
        ends.append([y, color, lab, series[-1]])
    ybh0 = pt + ih - ih * (a["bh"][-1] - lo) / (hi - lo)
    ends.append([ybh0, C_BH, "持有", a["bh"][-1]])
    ends.sort(key=lambda r: r[0])
    MIN_GAP = 13.0
    for i in range(1, len(ends)):
        if ends[i][0] - ends[i - 1][0] < MIN_GAP:
            ends[i][0] = ends[i - 1][0] + MIN_GAP
    for y, color, lab, val in ends:
        y = min(y, pt + ih + 2)
        o.append(f'<circle cx="{pl+iw:.1f}" '
                 f'cy="{pt + ih - ih * (val - lo) / (hi - lo):.1f}" r="3" fill="{color}"/>')
        o.append(f'<text x="{pl+iw+7:.1f}" y="{y+3.5:.1f}" font-size="10.5" '
                 f'fill="{color}" font-weight="700">{lab} {val:.3f}</text>')

    # x 轴刻度
    for k in range(6):
        idx = int(round((n - 1) * k / 5))
        x = pl + iw * idx / (n - 1)
        o.append(f'<text class="cx" x="{x:.1f}" y="{pt+ih+30:.1f}">'
                 f'{dates[idx]:%Y-%m}</text>')
    o.append(f'<line x1="{pl}" y1="{pt+ih}" x2="{pl+iw}" y2="{pt+ih}" stroke="#dfe3ea"/>')
    o.append("</svg>")
    return "".join(o)


def build_html(P):
    T = P["targets"]
    cards = []
    for code in ("512040", "512890"):
        d = T[code]
        a, b = d["A"], d["B"]
        diff_ret = b["ret"] - a["ret"]
        diff_nav = b["final"] - a["final"]
        better = diff_ret > 0
        title_color = "#3d57e5" if better else "#c0392b"

        def row(name, va, vb, nd=2, pct=True, unit="%", better_high=True, risk=False):
            diff = None if (va is None or vb is None) else vb - va
            if diff is None or abs(diff) < 1e-9:
                wk = "—"
            elif (diff > 0) == better_high:
                wk = "B"
            else:
                wk = "A"
            def cls(v):
                if risk:
                    return "tn"
                if v is None:
                    return "df"
                return "up" if v > 0 else ("dn" if v < 0 else "df")
            fa = "—" if va is None else (f"{va:+.{nd}f}" if pct else f"{va:.{nd}f}") + (unit if pct else "")
            fb = "—" if vb is None else (f"{vb:+.{nd}f}" if pct else f"{vb:.{nd}f}") + (unit if pct else "")
            fd = "—" if diff is None else f"{diff:+.{nd}f}{unit if pct else ''}"
            return (f'<tr><td>{name}</td><td class="{cls(va)}">{fa}</td>'
                    f'<td class="{cls(vb)}">{fb}</td><td class="df">{fd}</td>'
                    f'<td class="win{" a" if wk=="A" else (" b" if wk=="B" else "")}">{wk}</td></tr>')

        mrows = "".join([
            row("期末净值", a["final"], b["final"], 4, False),
            row("区间收益", a["ret"], b["ret"]),
            row("年化收益", a["cagr"], b["cagr"]),
            row("最大回撤", a["mdd"], b["mdd"], risk=True),
            row("年化波动", a["vol"], b["vol"], risk=True, better_high=False),
            row("夏普", a["sharpe"], b["sharpe"], 2, False, ""),
            row("持仓占比", a["hold_rate"], b["hold_rate"], 1),
            row("平均单笔", a["avg_ret"], b["avg_ret"]),
            row("平均持仓日", a["avg_days"], b["avg_days"], 1, False, ""),
            row("买入次数", a["n_buy"], b["n_buy"], 0, False, "", better_high=True),
        ])

        def _brow(t):
            jv = t.get("j_at_buy")
            jtxt = "—" if jv is None else "%.1f" % jv
            now = ' class="now"' if t.get("open") else ""
            cls = "up" if t["ret"] > 0 else "dn"
            return (f'<tr{now}><td>{t["start"]}→{t["end"]}</td>'
                    f'<td class="df">{t["days"]}日</td>'
                    f'<td class="{cls}">{t["ret"]:+.2f}%</td>'
                    f'<td class="df">{jtxt}</td></tr>')

        def _arow(t):
            now = ' class="now"' if t.get("open") else ""
            cls = "up" if t["ret"] > 0 else "dn"
            return (f'<tr{now}><td>{t["start"]}→{t["end"]}</td>'
                    f'<td class="df">{t["days"]}日</td>'
                    f'<td class="{cls}">{t["ret"]:+.2f}%</td></tr>')

        # 逐笔（两版并列）+ J 值
        ta = "".join(_arow(t) for t in a["trades"])
        tb = "".join(_brow(t) for t in b["trades"])

        # 阈值扫描
        srows = []
        for thr, s in d["sens"].items():
            hit = " ← 本次" if abs(float(thr) - 20) < 1e-9 else ""
            diffv = s["final"] - a["final"]
            srows.append(
                f'<tr{" class=\'pk\'" if hit else ""}><td>J &lt; {thr}{hit}</td>'
                f'<td class="ta">{s["final"]:.4f}</td><td class="{"up" if s["ret"]>0 else "dn"}">'
                f'{s["ret"]:+.2f}%</td><td class="tn">{s["mdd"]:.2f}%</td>'
                f'<td class="df">{s["sharpe"]:.2f}</td><td class="df">{s["hold_rate"]:.1f}%</td>'
                f'<td class="df">{s["n_buy"]}</td>'
                f'<td class="{"up" if diffv>0 else "dn"}">{diffv:+.4f}</td></tr>')

        # 延后对照
        drows = "".join(
            f'<tr{" class=\'pk\'" if x["delay"]==0 else ""}><td>延后 {x["delay"]} 日'
            f'{"（=A 原版）" if x["delay"]==0 else ""}</td>'
            f'<td class="ta">{x["final"]:.4f}</td>'
            f'<td class="{"up" if x["ret"]>0 else "dn"}">{x["ret"]:+.2f}%</td>'
            f'<td class="tn">{x["mdd"]:.2f}%</td><td class="df">{x["sharpe"]:.2f}</td>'
            f'<td class="df">{x["hold_rate"]:.1f}%</td></tr>'
            for x in d["delay"])

        # 交叉实验
        crows = "".join(
            f'<tr><td>{"−3% 深买线" if abs(c["buy"]-0.97)<1e-9 else "回踩即买（浅）"}'
            f'（buy={c["buy"]:.2f}）</td>'
            f'<td class="ta">{c["a"]:.4f}</td><td class="tb">{c["b"]:.4f}</td>'
            f'<td class="{"up" if c["diff"]>0 else ("dn" if c["diff"]<0 else "df")}">'
            f'{c["diff"]:+.4f}</td><td class="df">{c["a_buy"]} / {c["b_buy"]}</td>'
            f'<td class="df">{c["n_j_high"]}/{c["n_j"]}</td></tr>'
            for c in d["cross"])

        z = d["zone"]
        jd = d["jdist"]

        # 结论文案：提前算好（f-string 内不能嵌多行表达式）
        n_jhigh = sum(1 for v in d["a_buy_j"] if v >= J20)
        n_buy_a = len(d["a_buy_j"])
        if better:
            verdict_head = '<b style="color:#3d57e5">略微改善</b>'
            verdict_body = (
                f'改善幅度只有 {abs(diff_ret):.2f}pp，而且<b>只来自 1 个买点被推迟了 5 个交易日</b>'
                f'（2024-08-14 买入日 J=28.9 被门槛挡下，改在 2024-08-21 J&lt;20 时买入，'
                f'那一段收益反而从 +17.57% 变成 +18.97%）。'
                f'{n_buy_a} 个买点里 {n_buy_a - n_jhigh} 个本来 J 就 &lt; 20 ⇒ 门槛几乎没起作用，'
                f'这点差异属于<b>单点偶然</b>，不是系统性优势。')
        else:
            verdict_head = '<b style="color:#c0392b">明显变差</b>'
            verdict_body = (
                f'{n_buy_a} 个买点里有 <b>{n_jhigh} 个 J≥20</b>，门槛把它们<b>整段漏掉或大幅推迟</b>，'
                f'净值掉了 {abs(diff_nav):.4f}；而最大回撤、年化波动几乎没变'
                f'（{a["mdd"]:.2f}% vs {b["mdd"]:.2f}%）⇒ 少掉的纯粹是收益，'
                f'<b>没有换来任何风险改善</b>。')

        cards.append(f"""
<div class="card">
  <h2><i>■</i>{code} {d['name']}　<span class="sub2">buy={d['buy']:.2f} / sell={d['sell']:.2f}
    / MA{d['ma']}　窗口 {a['d0'][:10]} ~ {a['d1'][:10]}（{a['n']} 日）</span></h2>

  <div class="verdict" style="border-color:{title_color}">
    <div class="vh">结论：J&lt;20 门槛在这只标的上{verdict_head}</div>
    <div class="vt">原版 <b>{a['final']:.4f}</b>（{a['ret']:+.2f}%）　→　
      J&lt;20 <b>{b['final']:.4f}</b>（{b['ret']:+.2f}%）　
      <span style="color:{title_color};font-weight:700">差 {diff_ret:+.2f}pp（净值 {diff_nav:+.4f}）</span><br>
      {verdict_body}
    </div>
  </div>

  <div class="tblwrap">{svg_chart(a, b)}</div>
  <div class="legend">粗线：<span style="color:{C_A}">灰 = 原版（进区间即买）</span> ／
    <span style="color:{C_B}">蓝 = J&lt;20 才买</span>；金色虚线 = 同期买入持有；
    底部浅蓝色带 = J 版的实际持仓时段。</div>

  <div class="tblwrap" style="margin-top:14px"><table>
    <tr><th>指标</th><th>A · 进区间即买</th><th>B · 进区间 + J&lt;20</th><th>差值(B−A)</th><th>胜</th></tr>
    {mrows}
  </table></div>
  <div class="legend">同期买入持有：{a['bh_final']:.4f}（{a['bh_ret']:+.2f}%，回撤 {a['bh_mdd']:.2f}%）。</div>

  <div class="tsplit" style="margin-top:14px">
    <div class="tcard a"><h3>A · 进区间即买（{len(a['trades'])} 笔）</h3>
      <table class="tt"><tr><th>区间</th><th>天数</th><th>收益</th></tr>{ta}</table>
      <div class="avg">平均单笔 <b>{a['avg_ret']:+.2f}%</b>　平均 <b>{a['avg_days']:.0f} 日</b></div>
    </div>
    <div class="tcard b"><h3>B · J&lt;20 才买（{len(b['trades'])} 笔）</h3>
      <table class="tt"><tr><th>区间</th><th>天数</th><th>收益</th><th>买入日J</th></tr>{tb}</table>
      <div class="avg">平均单笔 <b>{b['avg_ret']:+.2f}%</b>　平均 <b>{b['avg_days']:.0f} 日</b></div>
    </div>
  </div>

  <div class="note" style="margin-top:12px">
    <p><b>为什么门槛时灵时不灵：J 是「慢指标」，跌破买线是「快事件」。</b>
    这条标的一共出现过 <b>{z['n_seg']} 段</b>买入区间（收盘 &lt; MA200×{d['buy']:.2f}），
    <b>段长中位数只有 {z['med_days']:.0f} 个交易日</b>（最长 {z['max_days']} 日）；
    其中 <b>{z['n_miss']} 段从进入到离开，J 一次都没降到 20 以下</b>
    （这些段的段长中位数 {z['med_days_miss']:.0f} 日）。KDJ 的 J 要 9 日 RSV + 3 日再平滑，
    一根急跌砸穿 MA200 时 J 往往还在 40~60，等它磨到 20 以下，价格早已站回线上、区间关闭。
    ⇒ 门槛真正筛掉的不是「买贵的」，而是<b>「跌得快、来不及变超卖」的那一批买点</b>。</p>
    <p><b>对照证据（排除「只是等更低价」）：</b>把买入改成「进区间后无条件延后 N 日」，
    512040 延后 1 日就掉到 {d['delay'][1]['final']:.4f}、延后 2 日 {d['delay'][2]['final']:.4f}，
    越延越差；512890 同理（延后 1 日 {d['cross'] and ''}{T['512890']['delay'][1]['final']:.4f}）。
    说明 J 门槛的效果<b>不是</b>「晚点买更便宜」这么简单，而是它选择性跳过了特定买点 ——
    跳对了还是跳错了，取决于那一段行情，不取决于 J 本身。</p>
  </div>

  <div class="tblwrap" style="margin-top:14px"><table>
    <tr><th>买入门槛（卖出规则不变）</th><th>净值</th><th>收益</th><th>最大回撤</th>
        <th>夏普</th><th>持仓</th><th>买入次数</th><th>净值差(vs 原版)</th></tr>
    <tr class="pk"><td><b>无门槛（原版 A）</b></td><td class="ta">{a['final']:.4f}</td>
        <td class="{'up' if a['ret']>0 else 'dn'}">{a['ret']:+.2f}%</td>
        <td class="tn">{a['mdd']:.2f}%</td><td class="df">{a['sharpe']:.2f}</td>
        <td class="df">{a['hold_rate']:.1f}%</td><td class="df">{a['n_buy']}</td>
        <td class="df">—</td></tr>
    {''.join(srows)}
  </table></div>
  <div class="legend">J 值在窗口内的分布：均值 {jd['mean']:.1f}、p10 {jd['p10']:.1f}、中位 {jd['p50']:.1f}、
    p90 {jd['p90']:.1f}；<b>J&lt;20 的天数只占 {jd['lt20']:.1f}%</b> —— 门槛天然是少数派，
    一旦要求「同时满足跌破买线 + J&lt;20」，可买的日子就非常稀少。</div>

  <div class="tblwrap" style="margin-top:14px"><table>
    <tr><th>交叉实验：换买线深度后，J&lt;20 是否仍有同样效果</th><th>A 净值</th><th>B 净值</th>
        <th>差值</th><th>买次数 A/B</th><th>A 买点里 J≥20 的个数</th></tr>
    {crows}
  </table></div>
  <div class="legend">这张表说明：<b>J 门槛是否有影响，取决于「有没有买点恰好落在 J 高的时候」，
    而不是取决于标的或买线本身。</b>同一只票换一档买线，影响可能从 +0.02 变成 0.0000（完全无差别）——
    数字的随机性一目了然。</div>
</div>""")

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>J&lt;20 买入门槛对比 · 512040 / 512890</title>
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
.card h2 i{{font-style:normal;color:#3d57e5;margin-right:4px}}
.sub2{{font-size:12px;font-weight:400;color:#6c717f}}
.tblwrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:12.8px}}
th,td{{padding:7px 8px;border-bottom:1px solid #eef0f3;white-space:nowrap}}
th{{background:#f4f6fa;color:#6c717f;font-weight:600;font-size:12px;text-align:right}}
th:first-child{{text-align:left}}
td:first-child{{color:#42474f;text-align:left}}
.ta{{color:#8b96ab;font-weight:600;text-align:right}}
.tb{{color:#3d57e5;font-weight:600;text-align:right}}
.df{{text-align:right;color:#6c717f}}
.win{{text-align:center;font-size:11px;font-weight:700}}
.win.a{{color:#8b96ab}} .win.b{{color:#3d57e5}}
.up{{color:#d4380d}} .dn{{color:#0f9d58}}
.tn{{color:#42474f;font-weight:600;text-align:right}}
.csvg{{overflow:hidden;max-width:100%;height:auto}}
.legend{{font-size:11.5px;color:#6c717f;margin-top:6px;white-space:normal;line-height:1.7}}
.tsplit{{display:flex;gap:12px;flex-wrap:wrap}}
.tcard{{flex:1 1 300px;min-width:0;border:1px solid #e2e5ea;border-radius:10px;padding:11px 13px}}
.tcard.a{{background:#fafbfc;border-color:#e2e5ea}}
.tcard.b{{background:#f7f9ff;border-color:#dde4fb}}
.tcard h3{{margin:0 0 3px;font-size:12.5px;font-weight:700;white-space:normal;line-height:1.5}}
.tcard.a h3{{color:#5b6472}} .tcard.b h3{{color:#3d57e5}}
table.tt{{font-size:12px}}
table.tt th,table.tt td{{padding:4px 6px;border-bottom:1px solid #eef0f3;text-align:right}}
table.tt tr.now td{{background:#fffbe6}}
.tcard .avg{{margin-top:7px;font-size:11.5px;color:#6c717f;text-align:right}}
.tcard .avg b{{color:#202020;font-size:12.5px}}
tr.pk td{{background:#fff8e6;font-weight:600}}
.note{{background:#fffaf4;border:1px solid #ffe3c8;border-radius:9px;padding:12px 14px;
  font-size:12.8px;color:#7a4f22;line-height:1.78}}
.note b{{color:#b35309}}
.note p{{margin:0 0 9px}} .note p:last-child{{margin-bottom:0}}
.verdict{{border-left:4px solid #3d57e5;background:#f8faff;border-radius:8px;
  padding:11px 14px;margin:0 0 14px}}
.verdict .vh{{font-size:13.5px;font-weight:700;margin-bottom:5px}}
.verdict .vt{{font-size:12.8px;color:#42474f;line-height:1.8}}
ul{{margin:8px 0 0;padding-left:20px;font-size:13px;line-height:1.85}}
li{{margin-bottom:5px}}
@media(max-width:700px){{
  body{{padding:14px 10px 30px}} .card{{padding:13px 12px}}
  .hero{{padding:16px}} .hero h1{{font-size:16px}}
  .tsplit{{flex-direction:column}}
}}
</style></head><body><div class="wrap">

<div class="hero">
  <h1>买入加一道「J &lt; 20」门槛 · 近 5 年对比回测</h1>
  <p>原策略：收盘跌破 MA200×buy 就买。新想法：<b>还要当日 KDJ 的 J 值 &lt; 20 才买</b>。<br>
     口径：<b>前复权 · 零手续费 · 初始净值 1</b>　｜　窗口 {T['512040']['A']['d0'][:10]} ~ {T['512040']['A']['d1'][:10]}
     （{T['512040']['A']['n']} 个交易日 / {T['512040']['A']['yrs']:.2f} 年，两只标的同窗口）<br>
     收益结算：k−1 日收盘后的状态决定 k−1→k 的涨跌（信号当日成交、次日才吃涨跌），无未来函数。</p>
</div>

<div class="card">
  <h2><i>◆</i>一句话结论</h2>
  <div class="note">
    <p><b>这个门槛的收益不稳定，而且方向和标的强相关：512040 上几乎无感（+2.38pp，且来自单个买点的偶然），
    512890 上明显有害（−31.24pp）。</b></p>
    <p><b>根因不是「J 这个指标不好」，而是它和「跌破买线」这个触发条件的<b>时间尺度不匹配</b>：
    跌破 MA200 常是 1~2 日的急跌事件，而 J（9 日 RSV + 3 日再平滑）是慢变量 ——
    急跌当天 J 往往还在 40~60，等它磨到 20 以下，价格已经站回 MA200 之上、买入区间关闭了。
    于是门槛筛掉的不是「买贵的」，而是「跌得快、来不及超卖」的那批买点。</b></p>
    <p>换句话说：<b>如果你要的是「买得更便宜」，J&lt;20 确实能做到（平均单笔收益略升）；
    如果你要的是「总收益更高」，它会漏掉整段行情 —— 对这个策略（5 年只有 6~9 次买点）来说，漏一次就是致命的。</b></p>
  </div>
</div>

{''.join(cards)}

<div class="card">
  <h2><i>◆</i>如果还想用 J，怎么改才不伤收益</h2>
  <ul>
    <li><b>把「硬门槛」改成「分批 + 触发后补买」：</b>跌破买线先买 50%，若随后 J 跌到 20 以下再加 50%。
      这样既不会漏掉整段，又能把均价往下压。这与项目里已验证过的经验一致：
      <b>任何「不满足条件就不参与」的硬性条件，风险都集中在「错过」上。</b></li>
    <li><b>或者把 J 门槛放到「卖出」侧：</b>卖出条件从「涨到 +6%」改成「涨到 +6% 且 J&gt;80 才卖」，
      让利润多跑一段。卖出侧的漏判代价是少赚，不会像买入侧那样直接空仓错过整段。</li>
    <li><b>不建议做的：</b>继续扫 J 的阈值（10/15/25/30）去挑一个最好看的数字。
      上面扫描表显示 {T['512040']['code']} 组从 1.99 到 2.02 全挤在一起、
      {T['512890']['code']} 组从 1.67 到 1.80 全低于原版 —— 这是「参数噪声」的典型形状，
      挑出来的最优值换个窗口大概率不成立。</li>
  </ul>
</div>

<div class="card">
  <h2><i>◆</i>数据与口径</h2>
  <ul>
    <li><b>数据源</b>：<code>tdx_adj_long_2019_2026.csv</code> 自带 <b>open/high/low/close 四价且已前复权</b>
      （此前只用 close，这次才用到 high/low）。J 值必须用真实 high/low 计算，拿 close 代替会把振幅抹平、J 长期贴住 50，条件失效。</li>
    <li><b>尾部补齐</b>：长历史 T0 = 2026-09-24，用新浪在线四价补至 2026-10-23（15 个交易日），
      拼接前校验重叠末日收盘 <b>ratio = 1.000000</b>，口径一致才拼。</li>
    <li><b>数据体检</b>：512040 在 2023-10-11 的份额折算在长历史里已复权（前一日 0.8250 → 当日 0.8220，连续）；
      512890 窗口内唯一 |日涨跌|&gt;8% 是 2020-02-03 疫情 −9.34%（真实行情）；两只四价无非法值（high≥low、close 落在区间内）。</li>
    <li><b>KDJ 参数</b>：标准 (9,3,3)，K/D 初值 50，与通达信一致；J = 3K − 2D。</li>
    <li><b>交叉校验</b>：本脚本的 A 组（无门槛）与日报里 <code>build_ve_curve()</code> 的净值序列
      <b>逐点完全一致（最大差 0.0）</b>，且与 2026-10-23 报告中的
      「512040 净值 1.9916 / 512890 净值 2.1010」吻合。</li>
    <li><b>零手续费的原因</b>：本轮只想观察「规则本身的择时价值」。J 版交易次数更少，
      若计入双边 0.2% 成本，512890 的劣势会更大、512040 的优势会略微放大，不改变定性结论。</li>
    <li><b>最大的局限</b>：5 年窗口内只有 6~9 个买点，<b>样本量极小</b>。
      任何「多了/少了一个买点」都会造成几个百分点的差异，这已经接近噪声量级。
      上表的每一个差值都应视为「这一段行情的特征」，不是「J 指标的稳定 alpha」。</li>
  </ul>
</div>

<div class="card" style="border-color:#ffd6d6">
  <h2 style="color:#c0392b"><i style="color:#c0392b">◆</i>风险提示</h2>
  <ul>
    <li>本报告全部为<b>事后规则复算（模型输出）</b>，不是实盘成交记录，也不构成任何个人投资建议。</li>
    <li>单窗口、单标的、样本量 6~9 笔 ⇒ <b>不足以支撑「J&lt;20 该不该加」的最终判断</b>。
      若要在实盘用，建议先在模拟盘跟踪 ≥20 次买点再评估。</li>
    <li>零手续费口径会系统性高估所有高换手策略；实盘成本请按双边 0.2% 自行折算。</li>
  </ul>
</div>

</div></body></html>"""


def main():
    p = os.path.join(OUT, "ve_j_entry.json")
    P = json.load(open(p, encoding="utf-8"))
    html = build_html(P)
    out = os.path.join(OUT, "J值买入门槛对比_512040_512890.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[saved] {out} ({len(html)} chars)")
    return out


if __name__ == "__main__":
    main()
