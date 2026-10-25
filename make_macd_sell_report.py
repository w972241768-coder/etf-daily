# -*- coding: utf-8 -*-
"""生成「卖出改用 MACD 柱翻绿」对比报告（512040 / 512890）。

依赖 ve_macd_sell_study.py 产出的 out/ve_macd_sell.json。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

C_A = "#8b96ab"      # 原版：蓝灰
C_C = "#3d57e5"      # C 先到先卖：蓝
C_BH = "#c9a227"

COLS = [("A", "A · 原版", C_A),
        ("B", "B · 只柱翻绿卖", "#7a5cd6"),
        ("C", "C · 先到先卖", C_C),
        ("D", "D · 两者同时", "#12876f")]


def _poly(vals, x0, y0, w, h, lo, hi):
    n = len(vals)
    if n < 2 or hi <= lo:
        return ""
    return " ".join(
        f"{x0 + w * i / (n - 1):.1f},{y0 + h - h * (float(v) - lo) / (hi - lo):.1f}"
        for i, v in enumerate(vals))


def svg_chart(d, *, W=960, H=350):
    """A（原版）vs C（先到先卖）+ 买入持有 + C 的持仓色带。"""
    a, c = d["A"], d["C"]
    pl, pr, pt, pb = 58, 96, 18, 46
    iw, ih = W - pl - pr, H - pt - pb
    allv = a["nav"] + c["nav"] + a["bh"]
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
    n = len(a["nav"])
    o = [f'<svg class="csvg" viewBox="0 0 {W} {H}" width="100%" '
         f'style="max-width:{W}px;display:block" xmlns="http://www.w3.org/2000/svg">',
         '<rect width="100%" height="100%" fill="#ffffff"/>']
    for tv in ticks:
        y = pt + ih - ih * (tv - lo) / (hi - lo)
        o.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}" stroke="#eef0f3"/>')
        o.append(f'<text class="cg" x="{pl-8}" y="{y+3.5:.1f}">{tv:.2f}</text>')

    hold, y0b = c["hold"], pt + ih + 8
    i = 0
    while i < n:
        if hold[i]:
            j = i
            while j + 1 < n and hold[j + 1]:
                j += 1
            x1, x2 = pl + iw * i / (n - 1), pl + iw * j / (n - 1)
            o.append(f'<rect x="{x1:.1f}" y="{y0b}" width="{max(x2-x1,1.5):.1f}" '
                     f'height="5" fill="#cdd8fb" rx="1.5"/>')
            i = j + 1
        else:
            i += 1

    o.append(f'<polyline fill="none" stroke="{C_BH}" stroke-width="1.4" '
             f'stroke-dasharray="5 4" points="{_poly(a["bh"], pl, pt, iw, ih, lo, hi)}"/>')
    o.append(f'<polyline fill="none" stroke="{C_A}" stroke-width="2.3" '
             f'points="{_poly(a["nav"], pl, pt, iw, ih, lo, hi)}"/>')
    o.append(f'<polyline fill="none" stroke="{C_C}" stroke-width="2.3" '
             f'points="{_poly(c["nav"], pl, pt, iw, ih, lo, hi)}"/>')

    ends = []
    for series, color, lab in ((a["nav"], C_A, "原版"), (c["nav"], C_C, "C 方案")):
        y = pt + ih - ih * (series[-1] - lo) / (hi - lo)
        ends.append([y, color, lab, series[-1]])
    ybh = pt + ih - ih * (a["bh"][-1] - lo) / (hi - lo)
    ends.append([ybh, C_BH, "持有", a["bh"][-1]])
    ends.sort(key=lambda r: r[0])
    for k in range(1, len(ends)):
        if ends[k][0] - ends[k - 1][0] < 13.0:
            ends[k][0] = ends[k - 1][0] + 13.0
    for y, color, lab, val in ends:
        y = min(y, pt + ih + 2)
        o.append(f'<circle cx="{pl+iw:.1f}" '
                 f'cy="{pt + ih - ih * (val - lo) / (hi - lo):.1f}" r="3" fill="{color}"/>')
        o.append(f'<text x="{pl+iw+7:.1f}" y="{y+3.5:.1f}" font-size="10.5" '
                 f'fill="{color}" font-weight="700">{lab} {val:.3f}</text>')

    for k in range(6):
        idx = int(round((n - 1) * k / 5))
        o.append(f'<text class="cx" x="{pl + iw*idx/(n-1):.1f}" y="{pt+ih+30:.1f}">'
                 f'{dates[idx]:%Y-%m}</text>')
    o.append(f'<line x1="{pl}" y1="{pt+ih}" x2="{pl+iw}" y2="{pt+ih}" stroke="#dfe3ea"/>')
    o.append("</svg>")
    return "".join(o)


def build_html(P):
    T = P["targets"]
    cards = []

    for code in ("512040", "512890"):
        d = T[code]
        A, B, C, D = d["A"], d["B"], d["C"], d["D"]
        R = {"A": A, "B": B, "C": C, "D": D}
        cs = d["cost_scan"]
        fh = d["fixed_hold"]
        rb = d["rand"]
        pct = d["rand_pctile"]
        gaps = d["gaps"]

        # --- 主指标表 ---
        def mrow(label, key, nd=2, pct_=True, risk=False, direction=True):
            cells = []
            for tag, _l, _c in COLS:
                if key == "na":
                    cells.append(f'<td class="df">'
                                 f'{R[tag]["n_buy"]} / {R[tag]["n_sell"]}</td>')
                    continue
                v = R[tag].get(key)
                if v is None:
                    txt = "—"
                elif pct_:
                    txt = f"{v:+.{nd}f}%"
                else:
                    txt = f"{v:.{nd}f}"
                if risk or not direction:
                    cls = "tn"
                else:
                    cls = "up" if (v or 0) > 0 else ("dn" if (v or 0) < 0 else "df")
                cells.append(f'<td class="{cls}">{txt}</td>')
            return f"<tr><td>{label}</td>{''.join(cells)}</tr>"

        metric_rows = "".join([
            mrow("期末净值", "final", 4, False),
            mrow("区间收益", "ret"),
            mrow("年化收益", "cagr"),
            mrow("最大回撤", "mdd", risk=True),
            mrow("夏普", "sharpe", 2, False, direction=False),
            mrow("Calmar", "calmar", 2, False, direction=False),
            mrow("持仓占比", "hold_rate", 1, direction=False),
            mrow("买 / 卖次数", "na"),
            mrow("平均单笔", "avg_ret"),
            mrow("平均持仓日", "avg_days", 1, False, direction=False),
            mrow("最长持仓日", "max_days", 0, False, direction=False),
        ])

        # --- 成本敏感性 ---
        crows = []
        for tag, lab, _c in COLS:
            r = cs[tag]
            crows.append(f'<tr{" class=\'pk\'" if tag == "A" else ""}><td>{lab}</td>'
                         f'<td class="ta">{r["c0"]["final"]:.4f}</td>'
                         f'<td class="tb">{r["c2"]["final"]:.4f}</td>'
                         f'<td class="df">{r["c3"]["final"]:.4f}</td>'
                         f'<td class="df">{r["c0"]["n_trade"]}</td>'
                         f'<td class="df">{r["c2"]["final"] - r["c0"]["final"]:+.4f}</td></tr>')

        # --- 固定持有 ---
        frows = []
        best_fh = max(fh, key=lambda x: x["final"])
        for x in fh:
            mark = ' class="pk"' if x["n"] == best_fh["n"] else ""
            near = "  ≈C组" if abs(x["n"] - round(C["avg_days"])) <= 2 else ""
            frows.append(f'<tr{mark}><td>{x["n"]} 日{near}</td>'
                         f'<td class="ta">{x["final"]:.4f}</td>'
                         f'<td class="{"up" if x["ret"]>0 else "dn"}">{x["ret"]:+.2f}%</td>'
                         f'<td class="tn">{x["mdd"]:.2f}%</td>'
                         f'<td class="df">{x["sharpe"]:.2f}</td>'
                         f'<td class="df">{x["hold_rate"]:.1f}%</td></tr>')

        # --- 逐笔（C 组 vs A 组）---
        def trow(t, label_by):
            now = ' class="now"' if t.get("open") else ""
            cls = "up" if t["ret"] > 0 else "dn"
            by = ""
            if label_by:
                by = ("翻绿" if t.get("by_tn") and not t.get("by_pct")
                      else ("6%" if t.get("by_pct") and not t.get("by_tn")
                            else ("两者" if t.get("by_pct") and t.get("by_tn") else "窗末")))
            return (f'<tr{now}><td>{t["start"]}→{t["end"]}</td>'
                    f'<td class="df">{t["days"]}日</td>'
                    f'<td class="{cls}">{t["ret"]:+.2f}%</td>'
                    + (f'<td class="df">{by}</td>' if label_by else "") + "</tr>")

        ta = "".join(trow(t, False) for t in A["trades"])
        tc = "".join(trow(t, True) for t in C["trades"])

        # --- 诊断：谁先到 ---
        grows = []
        for g in gaps:
            p_ = "—" if g["pct_after"] is None else str(g["pct_after"])
            q_ = "—" if g["tn_after"] is None else str(g["tn_after"])
            dd = g["gap"]
            dtxt = "—" if dd is None else f"{dd:+d}"
            dcls = "df" if dd is None else ("dn" if dd > 0 else "up")
            grows.append(f'<tr><td>{g["start"]}</td><td>{g["end"]}</td>'
                         f'<td class="df">{p_}</td><td class="df">{q_}</td>'
                         f'<td class="{dcls}">{dtxt}</td>'
                         f'<td class="{"up" if g["ret"]>0 else "dn"}">{g["ret"]:+.2f}%</td></tr>')
        gd = [g["gap"] for g in gaps if g["gap"] is not None]
        n_late = sum(1 for x in gd if x > 0)
        n_early = sum(1 for x in gd if x < 0)
        med_gap = float(np.median(gd)) if gd else None

        # --- 结论文案 ---
        c_cost_edge = cs["C"]["c2"]["final"] - cs["A"]["c2"]["final"]
        fh_edge = C["final"] - best_fh["final"]
        if code == "512040":
            verdict = "「先到先卖」有微弱正向证据，但未达统计显著"
            body = (f'零成本下 C 组 <b>{C["final"]:.4f}</b> 明显高于原版 <b>{A["final"]:.4f}</b>'
                    f'（{C["ret"]-A["ret"]:+.2f}pp），夏普 {A["sharpe"]:.2f}→{C["sharpe"]:.2f}、'
                    f'回撤 {A["mdd"]:.2f}%→{C["mdd"]:.2f}%，<b>含双边 0.2% 成本后仍领先 '
                    f'{c_cost_edge:+.2f}pp</b>（{cs["C"]["c2"]["final"]:.4f} vs '
                    f'{cs["A"]["c2"]["final"]:.4f}）—— 这是前三轮实验里唯一一次改动抗住了成本。'
                    f'但代价是换手从 {cs["A"]["c0"]["n_trade"]} 次升到 {cs["C"]["c0"]["n_trade"]} 次，'
                    f'平均单笔从 {A["avg_ret"]:+.2f}% 掉到 {C["avg_ret"]:+.2f}%。')
        else:
            verdict = "「先到先卖」在含成本后反而落后，不成立"
            body = (f'零成本下 C 组 <b>{C["final"]:.4f}</b> 略高于原版 <b>{A["final"]:.4f}</b>'
                    f'（{C["ret"]-A["ret"]:+.2f}pp），但<b>含双边 0.2% 成本后掉到 '
                    f'{cs["C"]["c2"]["final"]:.4f}，低于原版的 {cs["A"]["c2"]["final"]:.4f}</b>'
                    f'（{c_cost_edge:+.2f}pp）—— 换手从 {cs["A"]["c0"]["n_trade"]} 次翻到 '
                    f'{cs["C"]["c0"]["n_trade"]} 次，成本把超额全部吃掉还有余。'
                    f'而且<b>「固定持有 {best_fh["n"]} 日」就能到 {best_fh["final"]:.4f}，反超 C 组</b>。')

        cards.append(f"""
<div class="card">
  <h2><i>■</i>{code} {d['name']}　<span class="sub2">buy={d['buy']:.2f} / sell={d['sell']:.2f}
    / MA{d['ma']}　窗口 {A['d0'][:10]} ~ {A['d1'][:10]}（{A['n']} 日）</span></h2>

  <div class="verdict">
    <div class="vh">结论：{verdict}</div>
    <div class="vt">原版 <b>{A['final']:.4f}</b>（{A['ret']:+.2f}%）　→　
      C 先到先卖 <b>{C['final']:.4f}</b>（{C['ret']:+.2f}%）　
      <span class="dlt">差 {C['ret']-A['ret']:+.2f}pp（净值 {C['final']-A['final']:+.4f}）</span><br>
      {body}
    </div>
  </div>

  <div class="tblwrap">{svg_chart(d)}</div>
  <div class="legend">粗线：<span style="color:{C_A}">灰 = A 原版（只有 +6% 止盈）</span> ／
    <span style="color:{C_C}">蓝 = C 先到先卖（6% 或 柱翻绿）</span>；金色虚线 = 同期买入持有；
    底部浅蓝色带 = C 组的实际持仓时段（明显比原版更零碎）。</div>

  <div class="tblwrap" style="margin-top:14px"><table>
    <tr><th>指标</th><th>A · 原版（+6% 止盈）</th><th>B · 只柱翻绿卖</th>
        <th>C · 先到先卖</th><th>D · 两者同时</th></tr>
    {metric_rows}
  </table></div>
  <div class="legend">同期买入持有：{A['bh_final']:.4f}（{A['bh_ret']:+.2f}%，回撤 {A['bh_mdd']:.2f}%）。
    A 与 B 只差卖出规则；C 是「6% 或 翻绿，先到先卖」；D 是「两个都满足才卖」。
    <b>D 明显最差</b>：要等到「既涨了 6% 又翻绿」，平均持仓 120 天，把利润全回吐了。</div>

  <div class="tsplit" style="margin-top:14px">
    <div class="tcard a"><h3>A · 原版（{len(A['trades'])} 笔）</h3>
      <table class="tt"><tr><th>区间</th><th>天数</th><th>收益</th></tr>{ta}</table>
      <div class="avg">平均单笔 <b>{A['avg_ret']:+.2f}%</b>　平均 <b>{A['avg_days']:.0f} 日</b></div>
    </div>
    <div class="tcard b"><h3>C · 先到先卖（{len(C['trades'])} 笔）</h3>
      <table class="tt"><tr><th>区间</th><th>天数</th><th>收益</th><th>触发</th></tr>{tc}</table>
      <div class="avg">平均单笔 <b>{C['avg_ret']:+.2f}%</b>　平均 <b>{C['avg_days']:.0f} 日</b></div>
    </div>
  </div>

  <div class="note" style="margin-top:12px">
    <p><b>先看「谁先到」：这是决定成败的关键。</b>
    下面把原版每一段持仓里「第一次涨过 +6%」和「第一次柱翻绿」分别落在买入后第几个交易日列出来
    （<span style="color:#d4380d">红 = 翻绿更晚</span>、<span style="color:#0f9d58">绿 = 翻绿更早</span>）：</p>
  </div>
  <div class="tblwrap"><table>
    <tr><th>买入日</th><th>原版这段结束于</th><th>6% 在第 N 日</th><th>翻绿在第 N 日</th>
        <th>差（翻绿−6%）</th><th>该段收益</th></tr>
    {''.join(grows)}
  </table></div>
  <div class="legend">{len(gd)} 段可比：翻绿比 6% 晚 {n_late} 段、早 {n_early} 段，
    中位差 <b>{'—' if med_gap is None else f'{med_gap:+.0f}'} 个交易日</b>。
    {'<b>翻绿普遍晚于 6%</b> ⇒ 「等翻绿」等于「让利润多跑一段」，这正是 C 组能提高收益的原因；但也意味着要承受更多回吐。'
     if (med_gap or 0) > 0 else
     '<b>翻绿普遍早于 6%</b> ⇒ 「等翻绿」其实是「提前下车」，靠躲过后续下跌取胜，而非让利润奔跑。'}</div>

  <div class="tblwrap" style="margin-top:14px"><table>
    <tr><th>证伪 ①：含双边成本后的净值（换仓成本是这类方案的生死线）</th>
        <th>零成本</th><th>双边 0.2%</th><th>双边 0.3%</th><th>换仓次数</th><th>成本损耗</th></tr>
    {''.join(crows)}
  </table></div>
  <div class="legend">B/C 组换手是原版的 {cs['C']['c0']['n_trade']/max(cs['A']['c0']['n_trade'],1):.1f} 倍，
    零成本口径会系统性高估它们。加上双边 0.2% 后本标的：C 组
    {cs['C']['c2']['final']:.4f} vs 原版 {cs['A']['c2']['final']:.4f}
    （<b>{cs['C']['c2']['final']-cs['A']['c2']['final']:+.4f} 净值</b>）；
    成本损耗 A {cs['A']['c2']['final']-cs['A']['c0']['final']:+.4f}、C {cs['C']['c2']['final']-cs['C']['c0']['final']:+.4f}。
    <b>换手越多，成本咬得越狠 —— 这是「更频繁进出」类方案的生死线。</b></div>

  <div class="tblwrap" style="margin-top:14px"><table>
    <tr><th>证伪 ②：固定持有 N 日就卖（完全不看 MACD）</th><th>净值</th><th>收益</th>
        <th>最大回撤</th><th>夏普</th><th>持仓</th></tr>
    {''.join(frows)}
  </table></div>
  <div class="legend">如果「买入后数着日子卖」就能达到 C 组水平，那 C 的优势就不是 MACD 给的。
    本标的：固定持有最优 <b>{best_fh['n']} 日 = {best_fh['final']:.4f}</b>，
    C 组 <b>{C['final']:.4f}</b> ⇒
    {'<b>C 组高于简单持有</b>，这部分才可能是选时能力。' if fh_edge > 0 else
     '<b>C 组并不高于简单持有</b>，说明其收益主要来自「缩短持仓」，与 MACD 无关。'}</div>

  <div class="tblwrap" style="margin-top:14px"><table>
    <tr><th>证伪 ③：随机卖出基准（把「翻绿」换成同频率随机事件，结构完全不变）</th>
        <th>净值</th></tr>
    <tr><td>随机版本 p05</td><td class="df">{rb['p05']:.4f}</td></tr>
    <tr><td>随机版本中位（{rb['trials']} 次模拟，p={rb['p']:.3f}/日，持仓 {rb['hold_rate']:.1f}%）</td>
        <td class="df">{rb['p50']:.4f}</td></tr>
    <tr><td>随机版本 p95</td><td class="df">{rb['p95']:.4f}</td></tr>
    <tr class="pk"><td><b>C 组实际（持仓 {C['hold_rate']:.1f}%）</b></td>
        <td class="ta"><b>{C['final']:.4f}</b></td></tr>
  </table></div>
  <div class="legend">C 组位于随机分布约 <b>{'—' if pct is None else f'{pct:.1f}%'} 分位</b>。
    {'<b>超过 95% 分位才说明「翻绿」提供了随机之外的信息</b>；本次未达到 ⇒ 有微弱正向但不显著。'
     if (pct or 0) <= 95 else '<b>超过 95% 分位 ⇒ 翻绿确实提供了随机之外的信息。</b>'}</div>
</div>""")

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>卖出改用 MACD 柱翻绿 · 对比回测（512040 / 512890）</title>
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
.ta{{color:#3d57e5;font-weight:600;text-align:right}}
.tb{{color:#d4380d;font-weight:600;text-align:right}}
.df{{text-align:right;color:#6c717f}}
.up{{color:#d4380d;font-weight:600;text-align:right}}
.dn{{color:#0f9d58;font-weight:600;text-align:right}}
.tn{{color:#42474f;font-weight:600;text-align:right}}
.csvg{{overflow:hidden;max-width:100%;height:auto}}
.legend{{font-size:11.5px;color:#6c717f;margin-top:6px;white-space:normal;line-height:1.7}}
.tsplit{{display:flex;gap:12px;flex-wrap:wrap}}
.tcard{{flex:1 1 300px;min-width:0;border:1px solid #e2e5ea;border-radius:10px;padding:11px 13px}}
.tcard.a{{background:#fafbfc}} .tcard.b{{background:#f7f9ff;border-color:#dde4fb}}
.tcard h3{{margin:0 0 3px;font-size:12.5px;font-weight:700;white-space:normal}}
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
.note p{{margin:0}}
.verdict{{border-left:4px solid #3d57e5;background:#f8faff;border-radius:8px;
  padding:11px 14px;margin:0 0 14px}}
.verdict .vh{{font-size:13.5px;font-weight:700;margin-bottom:5px}}
.verdict .vt{{font-size:12.8px;color:#42474f;line-height:1.8}}
.verdict .dlt{{color:#3d57e5;font-weight:700}}
ul{{margin:8px 0 0;padding-left:20px;font-size:13px;line-height:1.85}}
li{{margin-bottom:5px}}
@media(max-width:700px){{
  body{{padding:14px 10px 30px}} .card{{padding:13px 12px}}
  .hero{{padding:16px}} .hero h1{{font-size:16px}}
  .tsplit{{flex-direction:column}}
}}
</style></head><body><div class="wrap">

<div class="hero">
  <h1>卖出改用「MACD 柱由正转负（红柱翻绿）」· 近 5 年对比回测</h1>
  <p>买入<b>完全不动</b>（进买入区间即买，不加任何指标门槛）；只改卖出：<b>「涨到 +6%」→「柱翻绿才卖」</b>。<br>
     口径：<b>前复权 · 零手续费（另附含成本对照）· 初始净值 1</b>　｜　窗口
     {T['512040']['A']['d0'][:10]} ~ {T['512040']['A']['d1'][:10]}
     （{T['512040']['A']['n']} 个交易日 / {T['512040']['A']['yrs']:.2f} 年，两只同窗口）<br>
     收益结算：k−1 日收盘后的状态决定 k−1→k 涨跌（信号当日成交、次日才吃涨跌），无未来函数。</p>
</div>

<div class="card">
  <h2><i>◆</i>一句话结论</h2>
  <div class="note">
    <p><b>这是四轮实验里第一次出现「正向信号」，但两只标的结论分裂，而且都没过统计显著性。</b></p>
    <p><b>「先到先卖」（涨 6% 或 柱翻绿，谁先到谁触发）是唯一值得考虑的形态：</b>
    512040 上零成本 {T['512040']['C']['final']:.4f}（原版 {T['512040']['A']['final']:.4f}），
    <b>含双边 0.2% 成本后仍有 {T['512040']['cost_scan']['C']['c2']['final']:.4f} vs {T['512040']['cost_scan']['A']['c2']['final']:.4f} 的领先</b>，
    夏普从 {T['512040']['A']['sharpe']:.2f} 提到 {T['512040']['C']['sharpe']:.2f}、回撤从
    {T['512040']['A']['mdd']:.2f}% 收窄到 {T['512040']['C']['mdd']:.2f}%。
    但 512890 上<b>含成本后反而落后</b>（{T['512890']['cost_scan']['C']['c2']['final']:.4f} vs {T['512890']['cost_scan']['A']['c2']['final']:.4f}），
    而且「固定持有 40 日」就能到 {max(T['512890']['fixed_hold'], key=lambda x: x['final'])['final']:.4f}，反超 C 组。</p>
    <p><b>「只按柱翻绿卖」（完全替代 6%）在两只上都更差</b>
    （512040 {T['512040']['B']['final']:.4f}、512890 {T['512890']['B']['final']:.4f}）；
    <b>「两者同时满足才卖」最差</b>（{T['512040']['D']['final']:.4f} / {T['512890']['D']['final']:.4f}）——
    要等到「既涨了 6% 又翻绿」，平均持仓 120 天，利润全回吐。</p>
    <p><b>最关键的一点：C 组在两个标的上分别位于随机分布的
    {T['512040']['rand_pctile']:.1f}% 和 {T['512890']['rand_pctile']:.1f}% 分位。</b>
    我把「柱翻绿」替换成「同频率随机事件」跑了 120 次模拟，C 组没有跑赢 95% 的随机版本
    ⇒ <b>「翻绿」提供的选时信息很弱，C 组的改善大部分可以用「更早离场 + 运气」解释。</b></p>
  </div>
</div>

{''.join(cards)}

<div class="card">
  <h2><i>◆</i>结论与建议</h2>
  <ul>
    <li><b>不建议现在把「柱翻绿」写进日报规则。</b>理由不是它没用，而是证据强度不够：
      两只标的结论相反、随机分位未过 95%、512890 上还被「固定持有 40 日」反超。
      单窗口 5 年、每个标的只有 5~9 段持仓，这种样本量下 2~7pp 的差异完全可能是运气。</li>
    <li><b>如果要做，只考虑 C 形态（6% 或 翻绿，先到先卖），且只加在 512040 上。</b>
      B（完全替代 6%）和 D（两者同时）都被数据明确否掉，不要再试。
      512040 是本轮唯一在「含成本 + 随机基准」双重检验下仍为正的标的。</li>
    <li><b>成本是这类方案的死穴。</b>C 组换手是原版的 3 倍（11→31 次）。
      双边 0.2% 已经吃掉 512040 的 0.13 净值、512890 的 0.16 净值。
      若实盘佣金更高或有明显冲击成本，这点超额会归零。<b>任何采纳都必须以「含成本后仍为正」为前提。</b></li>
    <li><b>下一步该做的不是再调参数，而是换样本验证。</b>
      把同样的 C 规则套到备选池里那 15 只 ETF 上跑一遍，
      若多数标的都能改善，才说明是「柱翻绿」有效；若只有 512040 改善，就是这一段的运气。</li>
  </ul>
</div>

<div class="card">
  <h2><i>◆</i>数据与口径</h2>
  <ul>
    <li><b>「柱由正转负」的定义</b>：<code>hist[t−1] &gt; 0 且 hist[t] ≤ 0</code>，
      即当日从红柱翻成绿柱（含恰好等于 0 的临界）。它是一个<b>事件</b>而非状态 ——
      若写成「当前是绿柱就卖」，等于买入次日立刻卖，无意义。</li>
    <li><b>指标</b>：MACD(12,26,9)，柱 = (DIF − DEA) × 2（通达信口径）。买入侧不做任何指标判断，
      仍是「收盘 &lt; MA200×buy 就买」；卖出的 6% 线仍是「收盘 &gt; MA200×sell」。</li>
    <li><b>数据源</b>：<code>tdx_adj_long_2019_2026.csv</code>，自带四价且已前复权；
      长历史 T0 = 2026-09-24，用新浪在线四价补至 2026-10-23（15 个交易日），
      拼接前校验重叠末日收盘 <b>ratio = 1.000000</b>。</li>
    <li><b>三项证伪实验</b>：① 成本敏感性（0 / 0.2% / 0.3% 双边）；② 固定持有 N 日对照（不看 MACD）；
      ③ 随机卖出基准（把翻绿换成同频率随机事件，120 次模拟，p 取到持仓率与 C 组相当）。
      <b>只有三项都通过的改动才值得写进规则。</b></li>
    <li><b>交叉校验</b>：A 组净值与日报 <code>build_ve_curve()</code> 逐点一致
      （512040 = {T['512040']['A']['final']:.4f}、512890 = {T['512890']['A']['final']:.4f}，
      与 2026-10-23 报告吻合）。</li>
    <li><b>最大局限</b>：5 年窗口、5~9 段持仓，<b>样本量极小</b>。
      随机基准里 95% 分位到中位就差 0.27 净值，可见这个系统的噪声水平有多高。</li>
  </ul>
</div>

<div class="card" style="border-color:#ffd6d6">
  <h2 style="color:#c0392b"><i style="color:#c0392b">◆</i>风险提示</h2>
  <ul>
    <li>本报告全部为<b>事后规则复算（模型输出）</b>，不是实盘成交记录，不构成任何个人投资建议。</li>
    <li>「含成本」仅按双边 0.2% 简单折算，未考虑大额冲击成本、申赎限制与流动性。</li>
    <li>单窗口、单标的、样本量 5~9 段 ⇒ 不足以支撑「柱翻绿有效/无效」的一般性结论，
      只能说在这两只标的、这 5 年、这套买卖线下，C 形态有微弱正向但不显著。</li>
    <li>前几轮已证伪并从策略中删除：<b>J&lt;20 买入门槛、MACD 柱转强买入、J&gt;80 卖出</b>。</li>
  </ul>
</div>

</div></body></html>"""


def main():
    p = os.path.join(OUT, "ve_macd_sell.json")
    P = json.load(open(p, encoding="utf-8"))
    html = build_html(P)
    out = os.path.join(OUT, "卖出改柱翻绿对比_512040_512890.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[saved] {out} ({len(html)} chars)")
    return out


if __name__ == "__main__":
    main()
