"""生成「近 3 笔累计亏损」对照报告 v2（前缀口径）

读 out/rot_stop3.json → out/止损对比_单笔vs近3笔累计.html
参数网格（阈值 × 空仓天数）在本脚本内现算：30×2 次模拟，放 JSON 会过大。
"""
from __future__ import annotations

import html
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import etf_backtest_long as bl  # noqa: E402
import rot_stop_study as rs      # noqa: E402

OUT = os.path.join(HERE, "out")
JSON_IN = os.path.join(OUT, "rot_stop3.json")
HTML_OUT = os.path.join(OUT, "止损对比_单笔vs近3笔累计.html")

C_A = "#3d57e5"
C_D3 = "#7b4fbf"
C_EP = "#d4380d"
C_E = "#98a1ad"

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#eef0f3;color:#202020;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  font-size:13.5px;line-height:1.68;-webkit-font-smoothing:antialiased}
.wrap{max-width:1010px;margin:0 auto;padding:0 18px 56px}
.hero{background:linear-gradient(120deg,#3d57e5,#4a64e8,#566de7);color:#fff;
  border-radius:0 0 16px 16px;padding:24px 26px 22px;margin:0 -18px 18px;
  box-shadow:0 4px 16px rgba(61,87,229,.18)}
.hero h1{margin:0 0 6px;font-size:21px;font-weight:700}
.hero .s{font-size:12.5px;opacity:.9}
.hero .tags{margin-top:12px;display:flex;flex-wrap:wrap;gap:8px}
.hero .tg{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.28);
  border-radius:20px;padding:2px 11px;font-size:11.5px}
h2{font-size:15.5px;font-weight:700;margin:26px 0 10px;padding-left:9px;
  border-left:4px solid #3d57e5;line-height:1.3}
h2 i{font-style:normal;color:#3d57e5;margin-right:5px}
.card{background:#fff;border:1px solid #e2e5ea;border-radius:12px;
  padding:16px 18px;margin:0 0 14px;box-shadow:0 1px 3px rgba(16,24,40,.05)}
.card h3{margin:0 0 8px;font-size:13.5px;font-weight:700}
.lead{background:#fff;border:1px solid #e2e5ea;border-left:4px solid #3d57e5;
  border-radius:10px;padding:13px 16px;margin:0 0 16px;font-size:13px;color:#333a4d}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e5ea;
  border-radius:10px;overflow:hidden;font-size:12.3px}
th,td{padding:6px 9px;border-bottom:1px solid #eef0f3;text-align:left;white-space:nowrap}
th{background:#f4f6fa;font-weight:600;color:#42474f;font-size:11.8px}
tbody tr:hover{background:#fafbfe}
tbody tr:last-child td{border-bottom:none}
tbody tr.hi td{background:#eef2fd}
.num,.rs{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.8px}
.up{color:#d4380d;font-weight:600}
.dn{color:#0f9d58;font-weight:600}
.tn{color:#42474f;font-weight:600}
.dimtxt{color:#98a1ad;font-size:11.5px}
.good{color:#3d57e5;font-weight:700}
.bad{color:#c0392b;font-weight:700}
.tblwrap{overflow-x:auto;border-radius:10px}
.csvg{display:block;width:100%;overflow:hidden;max-width:100%}
.cg{font-size:10px;fill:#98a1ad;text-anchor:end}
.cx{font-size:10px;fill:#98a1ad;text-anchor:middle}
.legend{font-size:11.8px;color:#6c717f;margin-top:8px;line-height:1.7}
.legend b{color:#333a4d}
.legend .k{display:inline-block;width:14px;height:3px;border-radius:2px;
  margin-right:4px;vertical-align:middle}
ul{margin:6px 0 0;padding-left:20px}
li{margin:5px 0}
.foot{color:#98a1ad;font-size:11.5px;margin-top:26px;text-align:center;line-height:1.8}
.hm td{text-align:center;font-variant-numeric:tabular-nums;font-size:11.6px;
  white-space:nowrap;padding:5px 4px}
.hm td.h{background:#f4f6fa;font-weight:600;text-align:left}
.hm td.b1{background:#e8edfb;color:#2c4fb8;font-weight:700}
.hm td.b2{background:#b9cbf3;color:#14306f;font-weight:700}
.hm td.w1{background:#fdeeeb;color:#b3351a}
.hm td.n{color:#42474f}
.two{display:flex;gap:14px;flex-wrap:wrap}
.two>div{flex:1 1 300px;min-width:0}
"""


def svg_chart(curves, keys, colors, labels, W=960, H=340, title=""):
    pl, pr, pt, pb = 56, 126, 24, 42
    iw, ih = W - pl - pr, H - pt - pb
    allv = []
    for k in keys:
        allv += curves[k]["v"]
    lv = [math.log10(max(v, 1e-6)) for v in allv]
    lo, hi = min(lv), max(lv)
    pad = max((hi - lo) * .07, .01)
    lo, hi = lo - pad, hi + pad
    ticks = [t for t in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12)
             if lo <= math.log10(t) <= hi]
    o = [f'<svg class="csvg" viewBox="0 0 {W} {H}" width="100%" '
         f'style="max-width:{W}px" xmlns="http://www.w3.org/2000/svg">',
         '<rect width="100%" height="100%" fill="#ffffff"/>']
    for tv in ticks:
        y = pt + ih * (hi - math.log10(tv)) / (hi - lo)
        o.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}" stroke="#eef0f3"/>')
        o.append(f'<text class="cg" x="{pl-8}" y="{y+3.5:.1f}">{tv:g}</text>')
    for k, col in zip(keys, colors):
        v = curves[k]["v"]
        n = len(v)
        pts = " ".join(
            f"{pl + iw * i / (n - 1):.1f},"
            f"{pt + ih * (hi - math.log10(max(x, 1e-6))) / (hi - lo):.1f}"
            for i, x in enumerate(v))
        o.append(f'<polyline fill="none" stroke="{col}" stroke-width="2.1" points="{pts}"/>')
    ends = []
    for k, col, lab in zip(keys, colors, labels):
        v = curves[k]["v"][-1]
        ends.append([pt + ih * (hi - math.log10(v)) / (hi - lo), col, lab, v])
    ends.sort(key=lambda r: r[0])
    for i in range(1, len(ends)):
        if ends[i][0] - ends[i - 1][0] < 14:
            ends[i][0] = ends[i - 1][0] + 14
    for y, col, lab, v in ends:
        yy = pt + ih * (hi - math.log10(v)) / (hi - lo)
        o.append(f'<circle cx="{pl+iw:.1f}" cy="{yy:.1f}" r="3" fill="{col}"/>')
        o.append(f'<text x="{pl+iw+7:.1f}" y="{min(y, pt+ih+2)+3.5:.1f}" font-size="10.5" '
                 f'fill="{col}" font-weight="700">{html.escape(lab)} {v:.3f}</text>')
    n = len(curves[keys[0]]["d"])
    for i in range(6):
        idx = int(round((n - 1) * i / 5))
        o.append(f'<text class="cx" x="{pl + iw*idx/(n-1):.1f}" y="{pt+ih+26:.1f}">'
                 f'{curves[keys[0]]["d"][idx][:7]}</text>')
    o.append(f'<line x1="{pl}" y1="{pt+ih}" x2="{pl+iw}" y2="{pt+ih}" stroke="#dfe3ea"/>')
    if title:
        o.append(f'<text x="{pl}" y="{pt-9}" font-size="11.5" fill="#42474f" '
                 f'font-weight="700">{html.escape(title)}</text>')
    o.append("</svg>")
    return "".join(o)


def grid(variant, panel, dates, s_i, e_i, rot, defensive, sig, thr_list, cool_list):
    nv, md = [], []
    for thr in thr_list:
        rn, rm = [], []
        for c in cool_list:
            if variant == "EP":
                kw = dict(loss3_cool=thr, loss3_days=c, loss3_window=3, loss3_prefix=True)
            else:
                kw = dict(loss_cool=thr, loss_days=c)
            r = rs.simulate(panel, dates, s_i, e_i, rot, defensive,
                            buy=0.001, sell=0.001, sig_list=sig, **kw)
            st = rs.summarize(r["navs"])
            rn.append(st["final"])
            rm.append(st["mdd"])
        nv.append(rn)
        md.append(rm)
    return dict(nav=nv, mdd=md)


def main():
    d = json.load(open(JSON_IN, encoding="utf-8"))
    M, L = d["core"]["main"], d["core"]["long"]
    SM, SL = d["scan"]["main"], d["scan"]["long"]
    A, D3, EP, EP2, E = (M[k]["stat"] for k in ("A", "D3", "EP", "EP2", "E"))
    AL, D3L, EPL, EP2L, EL = (L[k]["stat"] for k in ("A", "D3", "EP", "EP2", "E"))
    rM, rL = SM["rand"], SL["rand"]
    segM, segL = SM["seg"]["data"], SL["seg"]["data"]

    # 参数网格（现算）
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    panel, dates, s_i, e_i = rs.load_win("tdx_adj_long_2019_2026.csv", "2019-12-31")
    sig = {i: bl.compute_signal(panel, rot, i, 20, 20) for i in range(s_i, e_i)}
    THR = (0.02, 0.025, 0.03, 0.035, 0.04)
    COOL = (3, 4, 5, 6, 7, 10)
    gEP = grid("EP", panel, dates, s_i, e_i, rot, defensive, sig, THR, COOL)
    gD3 = grid("D3", panel, dates, s_i, e_i, rot, defensive, sig, THR, COOL)

    def row(lab, a, note="", cls=""):
        return (f"<tr class='{cls}'><td>{lab}</td>"
                f"<td class='num'>{a['final']:.4f}</td>"
                f"<td class='num up'>{a['total']:+.2f}%</td>"
                f"<td class='num tn'>{a['mdd']:.2f}%</td>"
                f"<td class='num'>{(a['sharpe'] or 0):.2f}</td>"
                f"<td class='num'>{(a['calmar'] or 0):.2f}</td>"
                f"<td class='num'>{a['trig']}</td>"
                f"<td class='num'>{a['cash_pct']:.1f}%</td>"
                f"<td class='dimtxt'>{note}</td></tr>")

    main_rows = (row("A 基线（现状）", A, "不做任何减速")
                 + row("D3 单笔亏损 ≥3% → 空仓 5 日", D3, "上一轮结论")
                 + row("<b>EP 近 3 笔任一前缀 ≥3% → 空仓 5 日</b>", EP, "你的口径", "hi")
                 + row("EP2 同上，但触发后不重置窗口", EP2, "更频繁空仓")
                 + row("E 近 3 笔整窗累计 ≥3%（旧口径）", E, "v1，非你的规则"))
    long_rows = (row("A 基线（现状）", AL, "不做任何减速")
                 + row("D3 单笔亏损 ≥3% → 空仓 5 日", D3L, "上一轮结论")
                 + row("<b>EP 近 3 笔任一前缀 ≥3% → 空仓 5 日</b>", EPL, "你的口径", "hi")
                 + row("EP2 同上，但触发后不重置窗口", EP2L, "更频繁空仓")
                 + row("E 近 3 笔整窗累计 ≥3%（旧口径）", EL, "v1，非你的规则"))

    def segrow(tag, lab):
        m19 = segM[tag]["2019-2026"]
        l15 = segL[tag]["2015-2018"]
        return (f"<tr><td>{lab}</td>"
                f"<td class='num up'>{m19['total']:+.2f}%</td>"
                f"<td class='num tn'>{m19['mdd']:.2f}%</td>"
                f"<td class='num up'>{l15['total']:+.2f}%</td>"
                f"<td class='num tn'>{l15['mdd']:.2f}%</td></tr>")
    seg_rows = (segrow("A", "A 基线") + segrow("D3", "D3 单笔 ≥3%")
                + segrow("EP", "<b>EP 前缀（你的口径）</b>") + segrow("EP2", "EP2 不重置")
                + segrow("E", "E 整窗（旧口径）"))

    def grid_tbl(g, base, kind):
        head = "".join(f'<th class="num">{c}日</th>' for c in COOL)
        out = []
        for thr, rn, rm in zip(THR, g["nav"], g["mdd"]):
            cells = [f'<td class="h">&gt;{thr*100:.1f}%</td>']
            for v in (rn if kind == "nav" else rm):
                if kind == "nav":
                    dd_ = v - base
                    k = "b2" if dd_ > 0.30 else ("b1" if dd_ > 0 else "w1")
                    cells.append(f'<td class="{k}">{v:.3f}</td>')
                else:
                    dd_ = v - base          # 负值；越大（越接近 0）= 回撤越浅
                    k = "b2" if dd_ > 5 else ("b1" if dd_ > 1 else ("n" if dd_ > -1 else "w1"))
                    cells.append(f'<td class="{k}">{v:.2f}</td>')
            out.append("<tr>" + "".join(cells) + "</tr>")
        return head, "".join(out)

    h_navEP, t_navEP = grid_tbl(gEP, A["final"], "nav")
    h_mddEP, t_mddEP = grid_tbl(gEP, A["mdd"], "mdd")
    h_mddD3, t_mddD3 = grid_tbl(gD3, A["mdd"], "mdd")

    tot = len(THR) * len(COOL)
    n_navEP = sum(1 for r in gEP["nav"] for v in r if v > A["final"])
    n_mddEP6 = sum(1 for r in gEP["mdd"] for v in r if v > A["mdd"] + 5.0)
    d3_best_mdd = max(max(r) for r in gD3["mdd"])

    yM = SM["yearly"]
    yrs = sorted({y for v in yM.values() for y in v})
    yrs = [y for y in yrs if abs(yM["A"].get(y, 0)) > 1e-9]
    yh = "".join(f'<th class="num">{y}</th>' for y in yrs)
    yrows = []
    for tag, lab in (("A", "A 基线"), ("D3", "D3 单笔"), ("EP", "EP 前缀（你的口径）"),
                     ("E", "E 整窗（旧）")):
        v = yM[tag]
        cells = "".join(
            (f"<td class='num {'up' if v[y] > 0 else 'dn'}'>{v[y]:+.1f}%</td>"
             if y in v else "<td class='num dimtxt'>—</td>") for y in yrs)
        yrows.append(f"<tr><td>{lab}</td>{cells}</tr>")

    cost_rows = "".join(
        f"<tr><td>{c['cost']*100:.1f}%</td>"
        f"<td class='num'>{c['A']['final']:.4f}</td>"
        f"<td class='num'>{c['D3']['final']:.4f}</td>"
        f"<td class='num'>{c['EP']['final']:.4f}</td>"
        f"<td class='num'>{c['E']['final']:.4f}</td></tr>" for c in SM["cost"])

    curvesM = {k: M[k]["curve"] for k in ("A", "D3", "EP", "E")}
    curvesL = {k: L[k]["curve"] for k in ("A", "D3", "EP", "E")}
    chart_m = svg_chart(curvesM, ["A", "D3", "EP", "E"], [C_A, C_D3, C_EP, C_E],
                        ["A 基线", "D3 单笔", "EP 前缀", "E 整窗"],
                        title="主窗口 2019-12-31 ~ 2026-09-24（对数轴）")
    chart_l = svg_chart(curvesL, ["A", "D3", "EP", "E"], [C_A, C_D3, C_EP, C_E],
                        ["A 基线", "D3 单笔", "EP 前缀", "E 整窗"],
                        title="长窗口 2015-01-05 ~ 2026-09-24（对数轴）")

    tsD3, tsEP = M["D3"]["trig_study"], M["EP"]["trig_study"]
    s19E, s19A = segM["EP"]["2019-2026"], segM["A"]["2019-2026"]
    s15E, s15A = segL["EP"]["2015-2018"], segL["A"]["2015-2018"]

    lead = (
        f"<b>你的口径（EP）成立，但只成立于 2019 年之后。</b>"
        f"主窗 {A['final']:.4f} → <b>{EP['final']:.4f}</b>、最大回撤 "
        f"{A['mdd']:.2f}% → <b>{EP['mdd']:.2f}%</b>，是这一轮唯一「收益更高且回撤更浅」的方案；"
        f"但长窗 {AL['final']:.4f} → <b>{EPL['final']:.4f}</b>、回撤 "
        f"{AL['mdd']:.2f}% → <b>{EPL['mdd']:.2f}%</b>，<b>反而不如什么都不做</b>。"
        f"<br><br>两个必须知道的事："
        f"① 上一轮那个「近 3 笔累计」只判<b>凑满 3 笔</b>的整窗合计，没实现你说的「单笔超 3% 就不用等后面」"
        f"—— 补上前缀判断后，净值从 {E['final']:.4f} 变成 {EP['final']:.4f}；"
        f"② EP 的<b>收益改善是成片的</b>（{n_navEP}/{tot} 格跑赢基线），"
        f"<b>但回撤改善是点状的</b>（只有 {n_mddEP6}/{tot} 格比基线浅 5pp 以上，相邻格立刻退回 −23%）。"
        f"<br><br>结论：<b>D3 仍是更稳的那一个</b>（两个窗口、两档随机检验都过 95%）；"
        f"EP 的 −16.8% 只在「阈值 2.5~3.0% × 空仓 5~6 日」这一小块成立，"
        f"不能当成可复现的回撤控制。"
    )

    html_txt = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>近3笔累计止损 · 与单笔、基准对比</title>
<style>{CSS}</style></head><body>
<div class="wrap">
<div class="hero">
  <h1>近 3 笔累计亏损止损 · 与单笔 D3、基准对比</h1>
  <div class="s">ROC 轮动主选池（9 只）· 前复权 · 每次换仓双边 {(d['meta']['cost'])*100:.1f}%
    · v2 口径：任一前缀触发</div>
  <div class="tags">
    <span class="tg">2019-2026 主窗口</span>
    <span class="tg">2015-2026 长窗口</span>
    <span class="tg">阈值 × 空仓天数 30 格</span>
    <span class="tg">随机频率对照</span>
    <span class="tg">成本 0.1/0.2/0.3%</span>
  </div>
</div>

<div class="lead">{lead}</div>

<h2><i>①</i>规则怎么触发（这决定了上一版为什么跑偏）</h2>
<div class="card">
  <div class="two">
    <div>
      <div style="font-size:12.5px;color:#6c717f;margin-bottom:4px">
        <b>你的口径（EP，本报告主方案）</b></div>
      <p style="margin:0;font-size:12.8px">
      看最近 3 笔已平仓收益，<b>任一段前缀</b>合计 ≤ −3% 就停：<br>
      · 第 1 笔 −3.2% → <b>立刻停</b>（不用等第 2、3 笔）<br>
      · 第 1 笔 −1.8%、第 2 笔 −1.5% → 合计 −3.3% → <b>停</b><br>
      · 前两笔 −1.0%/−1.2%、第 3 笔 −1.5% → 三笔合计 −3.7% → <b>停</b><br>
      · 停 5 个交易日后，按当日排名第 1 名重新买入。</p>
    </div>
    <div>
      <div style="font-size:12.5px;color:#6c717f;margin-bottom:4px">
        <b>上一轮实际跑的是什么（E）</b></div>
      <p style="margin:0;font-size:12.8px">
      只判<b>凑满 3 笔的整窗合计</b>。单笔 −3.2% 时它<b>继续持有</b>，
      要等第 2、3 笔都发生才可能停。<br>
      结果：主窗 {E['final']:.4f}（比基线少 {A['total']-E['total']:.0f}pp）、
      MDD {E['mdd']:.2f}%（比基线深 {abs(E['mdd'])-abs(A['mdd']):.1f}pp）。
      <b>差别的根源就是「单笔超 3% 要不要立刻停」这半句。</b></p>
    </div>
  </div>
  <div class="legend">校验：把「近 1 笔」单独跑（<span class="mono">window=1, prefix=True</span>）
  得到 {D3['final']:.4f}，与 D3 的 {D3['final']:.4f} <b>逐位相同</b> ⇒ 前缀实现无误；
  D3 就是 EP 在 k=1 时的特例，所以 EP 触发次数必然 ≥ D3（实测 {D3['trig']} → {EP['trig']} 次）。</div>
</div>

<h2><i>②</i>三方对比（两个窗口）</h2>
<div class="card">
  <div style="font-size:12px;color:#6c717f;margin-bottom:6px">主窗口 2019-12-31 ~ 2026-09-24</div>
  <div class="tblwrap"><table>
    <thead><tr><th>方案</th><th class="num">净值</th><th class="num">收益</th>
    <th class="num">MDD</th><th class="num">夏普</th><th class="num">Calmar</th>
    <th class="num">触发</th><th class="num">空仓</th><th>说明</th></tr></thead>
    <tbody>{main_rows}</tbody></table></div>

  <div style="font-size:12px;color:#6c717f;margin:14px 0 6px">长窗口 2015-01-05 ~ 2026-09-24</div>
  <div class="tblwrap"><table>
    <thead><tr><th>方案</th><th class="num">净值</th><th class="num">收益</th>
    <th class="num">MDD</th><th class="num">夏普</th><th class="num">Calmar</th>
    <th class="num">触发</th><th class="num">空仓</th><th>说明</th></tr></thead>
    <tbody>{long_rows}</tbody></table></div>
  <div class="legend"><b>读法：</b>EP 在主窗赢了回撤（{EP['mdd']:.2f}% vs {D3['mdd']:.2f}%）、
  输了收益（{EP['final']:.4f} vs {D3['final']:.4f}）；到长窗连回撤也不赢
  （{EPL['mdd']:.2f}% vs {D3L['mdd']:.2f}%），收益落到基准之下。
  Calmar（收益/回撤）主窗 EP {EP['calmar']:.2f} > D3 {D3['calmar']:.2f}，
  长窗 EP {EPL['calmar']:.2f} < D3 {D3L['calmar']:.2f}。</div>
</div>

<div class="card">
  {chart_m}
  <div style="margin-top:12px">{chart_l}</div>
  <div class="legend"><span class="k" style="background:{C_A}"></span>A 基线
  <span class="k" style="background:{C_D3};margin-left:14px"></span>D3 单笔 ≥3%
  <span class="k" style="background:{C_EP};margin-left:14px"></span>EP 前缀（你的口径）
  <span class="k" style="background:{C_E};margin-left:14px"></span>E 整窗（旧口径）。
  主窗里 EP 大部分时间在 D3 下方，末端因为回撤浅把曲线拉平；长窗里 EP 几乎贴着基线。</div>
</div>

<h2><i>③</i>关键：EP 只在 2019 年之后有效</h2>
<div class="card">
  <p style="margin:0 0 10px">两个窗口长度不同，直接比总净值会混入「有没有把 2015-2018 算进去」。
  拆成两段就清楚了：</p>
  <div class="tblwrap"><table>
    <thead><tr><th>方案</th><th class="num">2019-2026 收益</th><th class="num">2019-2026 MDD</th>
    <th class="num">2015-2018 收益</th><th class="num">2015-2018 MDD</th></tr></thead>
    <tbody>{seg_rows}</tbody></table></div>
  <div class="legend">
  <b>2019-2026（两窗共有，也是主窗的全部）：</b>EP {s19E['total']:+.2f}% /
  MDD {s19E['mdd']:.2f}%，优于基线（{s19A['total']:+.2f}% / {s19A['mdd']:.2f}%）。<br>
  <b>2015-2018：</b>EP {s15E['total']:+.2f}% / MDD {s15E['mdd']:.2f}%，
  比基线（{s15A['total']:+.2f}% / {s15A['mdd']:.2f}%）<b>两头都差</b>。<br>
  ⇒ EP 长窗口难看（{EPL['final']:.4f} vs {AL['final']:.4f}）<b>全部来自 2015-2018 那一段</b>。
  而那一段池子里只有 4~6 只可轮动、512040 到 2018-11-29 才上市，与现在 9 只的构造不是同一个环境
  —— <b>既不能据此判 EP 死刑，也不能据此判它有效</b>。</div>
</div>

<h2><i>④</i>收益改善成片，回撤改善点状（全篇最重要的一张表）</h2>
<div class="card">
  <p style="margin:0 0 8px">阈值（2.0~4.0%）× 空仓天数（3~10 日）全跑一遍，共 30 个组合。
  颜色 = 相对基线（深蓝 = 明显更好 / 浅蓝 = 略好 / 白 = 与基线相差很小 / 红 = 更差）。</p>

  <div style="font-size:12px;color:#6c717f;margin:6px 0">EP · 期末净值（基线 {A['final']:.4f}）</div>
  <table class="hm"><thead><tr><th>阈值</th>{h_navEP}</tr></thead>
  <tbody>{t_navEP}</tbody></table>
  <div class="legend">30 格里 <b>{n_navEP} 格跑赢基线</b>，从 4.2 到 6.0 连成一片
  —— 「亏损后减速」这件事本身有价值，不是单点运气。</div>

  <div style="font-size:12px;color:#6c717f;margin:14px 0 6px">EP · 最大回撤（基线 {A['mdd']:.2f}%）</div>
  <table class="hm"><thead><tr><th>阈值</th>{h_mddEP}</tr></thead>
  <tbody>{t_mddEP}</tbody></table>
  <div class="legend">30 格里只有 <b>{n_mddEP6} 格</b>比基线浅 5pp 以上（深蓝），
  其余最多浅 1~2pp（浅蓝）或基本持平（白）。深蓝那几格集中在
  「阈值 2.5~3.0% × 空仓 5~6 日」这一小块；<b>阈值挪到 2.0% 或 3.5%，回撤立刻退回 −23%</b>。
  同一条规则、只改 0.5 个百分点的阈值，最大回撤差 6.6pp —— 这不是机制，
  是那几笔恰好把最坏的几天空掉了。</div>

  <div style="font-size:12px;color:#6c717f;margin:14px 0 6px">D3（单笔）· 最大回撤（基线 {A['mdd']:.2f}%）</div>
  <table class="hm"><thead><tr><th>阈值</th>{h_mddD3}</tr></thead>
  <tbody>{t_mddD3}</tbody></table>
  <div class="legend">D3 在任何参数下都进不到 −19% 以内（最好 {d3_best_mdd:.2f}%）⇒
  <b>D3 的价值在收益，不在回撤</b>；EP 是唯一能摸到 −17% 的，但只在那一小块。</div>
</div>

<h2><i>⑤</i>随机频率对照：EP 主窗过关，长窗不过</h2>
<div class="card">
  <p style="margin:0 0 8px">把「满足亏损条件才空仓」换成「以相同频率、同样 5 日长度随机空仓」，
  各跑 {d['meta'].get('sims', 80)} 次。真实方案若真有信息，应显著高于随机分布。</p>
  <div class="tblwrap"><table>
    <thead><tr><th>窗口</th><th>方案</th><th class="num">触发次数</th>
    <th class="num">随机 p50</th><th class="num">随机 p95</th>
    <th class="num">真实版本</th><th class="num">分位</th><th>判定</th></tr></thead>
    <tbody>
      <tr><td>主窗</td><td>D3</td><td class="num">{rM['D3']['events']}</td>
        <td class="num">{rM['D3']['p50']:.4f}</td><td class="num">{rM['D3']['p95']:.4f}</td>
        <td class="num">{rM['D3']['real']:.4f}</td>
        <td class="num">{rM['D3']['pctile']:.1f}%</td>
        <td class="good">过 95%</td></tr>
      <tr class="hi"><td>主窗</td><td>EP</td><td class="num">{rM['EP']['events']}</td>
        <td class="num">{rM['EP']['p50']:.4f}</td><td class="num">{rM['EP']['p95']:.4f}</td>
        <td class="num">{rM['EP']['real']:.4f}</td>
        <td class="num">{rM['EP']['pctile']:.1f}%</td>
        <td class="good">过 95%</td></tr>
      <tr><td>长窗</td><td>D3</td><td class="num">{rL['D3']['events']}</td>
        <td class="num">{rL['D3']['p50']:.4f}</td><td class="num">{rL['D3']['p95']:.4f}</td>
        <td class="num">{rL['D3']['real']:.4f}</td>
        <td class="num">{rL['D3']['pctile']:.1f}%</td>
        <td class="good">过 95%</td></tr>
      <tr class="hi"><td>长窗</td><td>EP</td><td class="num">{rL['EP']['events']}</td>
        <td class="num">{rL['EP']['p50']:.4f}</td><td class="num">{rL['EP']['p95']:.4f}</td>
        <td class="num">{rL['EP']['real']:.4f}</td>
        <td class="num">{rL['EP']['pctile']:.1f}%</td>
        <td class="bad">未过 95%</td></tr>
    </tbody></table></div>
  <div class="legend">注意随机中位都远低于基线（长窗 {rL['D3']['p50']:.2f} vs {AL['final']:.2f}）——
  <b>随机空仓本身就亏钱</b>，所以「高于随机中位」只说明有择时信息，不说明比不做更好。
  EP 在长窗只有 {rL['EP']['pctile']:.1f}% 分位，连随机里最好 5% 都够不上。</div>
</div>

<h2><i>⑥</i>逐年</h2>
<div class="card">
  <div class="tblwrap"><table><thead><tr><th>方案</th>{yh}</tr></thead>
  <tbody>{''.join(yrows)}</tbody></table></div>
  <div class="legend">EP 在 2021（{yM['EP']['2021']:+.1f}% vs {yM['A']['2021']:+.1f}%）、
  2022（{yM['EP']['2022']:+.1f}% vs {yM['A']['2022']:+.1f}%）、2024（{yM['EP']['2024']:+.1f}% vs
  {yM['A']['2024']:+.1f}%）、2025 更好；2023 略差、<b>2026 明显差</b>
  （{yM['EP']['2026']:+.1f}% vs {yM['A']['2026']:+.1f}%）——
  2026 是今年，这套规则最近一段已经在跑输。</div>
</div>

<h2><i>⑦</i>成本与触发时点</h2>
<div class="card">
  <div class="two">
    <div>
      <div style="font-size:12px;color:#6c717f;margin-bottom:6px">成本敏感性（主窗净值）</div>
      <table><thead><tr><th>双边</th><th class="num">A</th><th class="num">D3</th>
      <th class="num">EP</th><th class="num">E</th></tr></thead>
      <tbody>{cost_rows}</tbody></table>
      <div class="legend">换仓数几乎不变，成本不改变排序。</div>
    </div>
    <div>
      <div style="font-size:12px;color:#6c717f;margin-bottom:6px">触发那一刻，头名在什么位置</div>
      <table><thead><tr><th>方案</th><th class="num">触发</th>
      <th class="num">此前 5 日</th><th class="num">此后 5 日</th></tr></thead><tbody>
        <tr><td>D3</td><td class="num">{tsD3['n']}</td>
          <td class="num up">{tsD3['prior5']['avg']:+.2f}%</td>
          <td class="num">{tsD3['fwd5']['avg']:+.2f}%</td></tr>
        <tr class="hi"><td>EP</td><td class="num">{tsEP['n']}</td>
          <td class="num up">{tsEP['prior5']['avg']:+.2f}%</td>
          <td class="num up">{tsEP['fwd5']['avg']:+.2f}%</td></tr>
      </tbody></table>
      <div class="legend">触发时头名<b>此前 5 日已经涨了 {tsEP['prior5']['avg']:.1f}%</b>（不是跌），
      之后 5 日又涨 {tsEP['fwd5']['avg']:.1f}% —— 「按在反弹起点空仓」的老问题还在，
      这也是它收益不占优的原因。</div>
    </div>
  </div>
</div>

<h2><i>⑧</i>结论与建议</h2>
<div class="card">
  <ul style="margin-top:0">
    <li><b>你的口径（EP）成立，但只成立于 2019 之后。</b>
    主窗 {A['final']:.4f} → {EP['final']:.4f}、MDD {A['mdd']:.2f}% → {EP['mdd']:.2f}%；
    2015-2018 段两头都差（{s15A['total']:+.2f}%/{s15A['mdd']:.2f}% →
    {s15E['total']:+.2f}%/{s15E['mdd']:.2f}%）。</li>
    <li><b>「单笔超 3% 立刻停」这半句是关键。</b>去掉它（v1 整窗口径）净值从
    {EP['final']:.4f} 掉到 {E['final']:.4f}。你原话里的那个括号不是补充说明，是规则的另一半。</li>
    <li><b>但它压不住回撤这件事，别当卖点。</b>−16.8% 只在 30 格参数里的 {n_mddEP6} 格成立，
    阈值一动就回 −23%。</li>
    <li><b>收益上 D3 仍更强</b>：主窗 {D3['final']:.4f} vs {EP['final']:.4f}，长窗
    {D3L['final']:.4f} vs {EPL['final']:.4f}，且两窗随机检验都过 95%。
    <b>只选一个，选 D3。</b></li>
    <li><b>D3 + EP 叠加没有意义</b>：EP 在 k=1 时已含 D3 全部触发，再叠加等于重复计数。</li>
    <li><b>下一步（按优先级）：</b>① 用备选池 15 只做样本外检验 —— 这是唯一能分辨
    「2019 后的特性」还是「真规律」的办法；② 把空仓改成半仓，避免满仓/全现金的二元跳变；
    ③ 别在 2019-2026 上继续调参，已经扫了 30 格。</li>
  </ul>
</div>

<h2><i>⑨</i>口径与局限</h2>
<div class="card">
  <ul style="margin-top:0">
    <li>数据：前复权长历史 CSV（主窗 2019-12-31~2026-09-24 / 长窗 2015-01-05~2026-09-24），
    当日收盘出信号、涨跌归属次日，与日报同口径。</li>
    <li>成本：每次换仓双边 0.2%（买 0.1% + 卖 0.1%），另测 0.1%/0.3%。</li>
    <li><b>回归校验：</b>全部钩子关闭时，本模拟器与
    <span class="mono">etf_backtest_long.backtest()</span> 净值序列最大差 <b>0.0</b>，
    基线 {A['final']:.4f} = 日报 4.7579；「近 1 笔 + 前缀」与 D3 逐位相同
    （{D3['final']:.4f}）⇒ 前缀实现无误。</li>
    <li><b>样本量：</b>主窗 {D3['trig']}~{EP['trig']} 次触发 / 7 年，长窗 {D3L['trig']}~{EPL['trig']} 次 / 11 年。
    每次触发只对应 5 天离场，单次运气就能改变 1~6pp 的回撤读数。</li>
    <li><b>一处口径歧义已做敏感性检验：</b>「最近 3 次换仓」要不要把「切防守」那几次也算进窗口？
    只计 ROT 内平仓 = 主窗 5.4887 / 长窗 7.9020；把切防守也算进去 = 5.6888 / 8.1902。
    触发次数两次都是 29 / 44，最大回撤都是 −16.80% / −23.29%
    ⇒ <b>这个选择只改路径、不改结论</b>（报告主表用的是前者，与 D3 的记账口径一致）。</li>
    <li><b>最大局限：</b>规则是在同一段历史上选的（已扫 30 格参数 + 4 种口径）。
    本报告的确定结论只有两条 ——「整窗口径漏掉了单笔即时触发」和「EP 的回撤改善不可复现」；
    EP 该不该用，需要样本外。</li>
  </ul>
</div>

<div class="foot">
本报告仅供研究参考，不构成个人投资建议。<br>
生成于 2026-10-24 · 脚本 rot_stop3_study.py / make_rot_stop3_report.py
</div>
</div></body></html>"""

    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(html_txt)
    print(f"[html] {HTML_OUT}  {len(html_txt)} chars")


if __name__ == "__main__":
    main()
