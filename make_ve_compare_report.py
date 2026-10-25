# -*- coding: utf-8 -*-
"""生成 512890 vs 512040 价值引擎对比报告（独立 HTML）。

用法：先跑 ve_512890_study.py 产出 out/ve_512890_vs_512040.json，
     再跑本脚本（或直接 python ve_512890_study.py --html）。
"""
import os
import sys
import json

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

C_A = "#3d57e5"      # 512890 主色（蓝）
C_B = "#d4380d"      # 512040 主色（红=涨，中式）
C_MUT = "#98a1ad"


def _poly(vals, x0, y0, w, h, lo, hi):
    n = len(vals)
    if n < 2 or hi <= lo:
        return ""
    pts = []
    for i, v in enumerate(vals):
        x = x0 + w * i / (n - 1)
        y = y0 + h - h * (float(v) - lo) / (hi - lo)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def svg_chart(payload, *, W=980, H=380):
    """双标的净值曲线（策略实线 + 买入持有虚线）+ 持仓色带 + 买卖点。"""
    sa, sb = payload["series"]["512890"], payload["series"]["512040"]
    pl, pr, pt, pb = 62, 108, 18, 54
    iw, ih = W - pl - pr, H - pt - pb

    # 统一 x 轴 = 512890 的日期序列（两者窗口几乎一致）
    dts = pd.to_datetime(sa["dates"])
    d2i = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(dts)}

    allv = sa["nav"] + sa["bh"] + sb["nav"] + sb["bh"]
    lo, hi = min(allv), max(allv)
    step = next((s for s in (0.1, 0.2, 0.25, 0.5, 1.0) if (hi - lo) / s <= 6), (hi - lo) / 5)
    g0 = np.ceil(lo / step) * step
    g1 = np.floor(hi / step) * step
    ticks = []
    t = g0
    while t <= g1 + 1e-9:
        ticks.append(round(float(t), 4))
        t += step
    if not ticks:
        ticks = [round(lo, 2), round(hi, 2)]

    o = [f'<svg class="csvg" viewBox="0 0 {W} {H}" width="100%" '
         f'style="max-width:{W}px;display:block" xmlns="http://www.w3.org/2000/svg">']
    o.append('<rect width="100%" height="100%" fill="#ffffff"/>')

    # y 网格
    for tv in ticks:
        y = pt + ih - ih * (tv - lo) / (hi - lo)
        o.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}" '
                 f'stroke="#eef0f3" stroke-width="1"/>')
        lbl = f"{tv:.2f}"
        o.append(f'<text x="{pl-9}" y="{y+4:.1f}" text-anchor="end" '
                 f'font-size="11" fill="#6c717f">{lbl}</text>')

    # x 轴标签（每年首个交易日）
    seen = set()
    for i, d in enumerate(dts):
        if d.year not in seen:
            seen.add(d.year)
            x = pl + iw * i / (len(dts) - 1)
            o.append(f'<line x1="{x:.1f}" y1="{pt+ih}" x2="{x:.1f}" y2="{pt+ih+4}" '
                     f'stroke="#c9cdd6" stroke-width="1"/>')
            o.append(f'<text x="{x:.1f}" y="{pt+ih+18}" text-anchor="middle" '
                     f'font-size="11" fill="#6c717f">{d.year}</text>')

    # 持仓色带（512890 在上半条，512040 在下半条）
    rb_h = 7.0
    for idx, (code, s, col) in enumerate([("512890", sa, C_A), ("512040", sb, C_B)]):
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
                         f'height="{rb_h}" fill="{col}" opacity="0.45" rx="1.5"/>')
                i = j + 1
            else:
                i += 1
        o.append(f'<text x="{pl-9}" y="{yb+rb_h-1:.1f}" text-anchor="end" '
                 f'font-size="9" fill="{col}">{code}</text>')

    # 买入持有（虚线，两组：蓝灰=512890 持有，红灰=512040 持有）
    for s, col in [(sa, "#8b96ab"), (sb, "#c09a91")]:
        pts = _poly(s["bh"], pl, pt, iw, ih, lo, hi)
        o.append(f'<polyline points="{pts}" fill="none" stroke="{col}" '
                 f'stroke-width="1.35" stroke-dasharray="4 3"/>')

    # 策略主线
    for s, col, wid in [(sb, C_B, 1.8), (sa, C_A, 2.3)]:
        pts = _poly(s["nav"], pl, pt, iw, ih, lo, hi)
        o.append(f'<polyline points="{pts}" fill="none" stroke="{col}" '
                 f'stroke-width="{wid}" stroke-linejoin="round"/>')

    # 买卖点
    for s, col in [(sa, C_A), (sb, C_B)]:
        dmap = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(pd.to_datetime(s["dates"]))}
        for ev in s["events"]:
            i = dmap.get(ev["date"])
            if i is None:
                continue
            x = pl + iw * i / (len(s["nav"]) - 1)
            y = pt + ih - ih * (s["nav"][i] - lo) / (hi - lo)
            if ev["act"] == "BUY":
                o.append(f'<path d="M{x:.1f},{y-7:.1f} L{x-4:.1f},{y+1:.1f} '
                         f'L{x+4:.1f},{y+1:.1f} Z" fill="#d4380d"/>')
            elif ev["act"] == "SELL":
                o.append(f'<path d="M{x:.1f},{y+7:.1f} L{x-4:.1f},{y-1:.1f} '
                         f'L{x+4:.1f},{y-1:.1f} Z" fill="#0f9d58"/>')

    # 末端净值标注
    for s, col, code in [(sa, C_A, "512890"), (sb, C_B, "512040")]:
        y = pt + ih - ih * (s["nav"][-1] - lo) / (hi - lo)
        o.append(f'<text x="{pl+iw+7}" y="{y+3:.1f}" font-size="11" '
                 f'font-weight="700" fill="{col}">'
                 f'{s["nav"][-1]:.3f}</text>')

    # 图例
    lx, ly = pl + 6, pt + 4
    o.append(f'<line x1="{lx}" y1="{ly}" x2="{lx+16}" y2="{ly}" stroke="{C_A}" stroke-width="2.3"/>')
    o.append(f'<text x="{lx+21}" y="{ly+4}" font-size="11" fill="#202020">512890 红利低波</text>')
    lx += 118
    o.append(f'<line x1="{lx}" y1="{ly}" x2="{lx+16}" y2="{ly}" stroke="{C_B}" stroke-width="1.8"/>')
    o.append(f'<text x="{lx+21}" y="{ly+4}" font-size="11" fill="#202020">512040 价值ETF</text>')
    lx += 124
    o.append(f'<line x1="{lx}" y1="{ly}" x2="{lx+16}" y2="{ly}" stroke="#8b96ab" '
             f'stroke-width="1.35" stroke-dasharray="4 3"/>')
    o.append(f'<text x="{lx+21}" y="{ly+4}" font-size="11" fill="#6c717f">890 持有</text>')
    lx += 82
    o.append(f'<line x1="{lx}" y1="{ly}" x2="{lx+16}" y2="{ly}" stroke="#c09a91" '
             f'stroke-width="1.35" stroke-dasharray="4 3"/>')
    o.append(f'<text x="{lx+21}" y="{ly+4}" font-size="11" fill="#6c717f">040 持有</text>')

    o.append("</svg>")
    return "".join(o)


def build_html(payload):
    m_a = payload["metrics"]["512890"]
    m_b = payload["metrics"]["512040"]
    yr = payload["yearly"]
    w = payload["window"]
    chart = svg_chart(payload)

    def chip(v, good_high=True, unit="%"):
        cls = "up" if (v > 0) == good_high else "dn"
        return f'<span class="num {cls}">{v:+.2f}{unit}</span>'

    rows = [
        ("累计收益", f'{m_a["ret"]:+.2f}%', f'{m_b["ret"]:+.2f}%',
         f'{m_a["ret"]-m_b["ret"]:+.2f}pp', m_a["ret"] > m_b["ret"]),
        ("年化收益", f'{m_a["cagr"]:+.2f}%', f'{m_b["cagr"]:+.2f}%',
         f'{m_a["cagr"]-m_b["cagr"]:+.2f}pp', m_a["cagr"] > m_b["cagr"]),
        ("最大回撤", f'{m_a["mdd"]:.2f}%', f'{m_b["mdd"]:.2f}%',
         f'{m_a["mdd"]-m_b["mdd"]:+.2f}pp', m_a["mdd"] > m_b["mdd"]),
        ("年化波动", f'{m_a["vol"]:.2f}%', f'{m_b["vol"]:.2f}%',
         f'{m_a["vol"]-m_b["vol"]:+.2f}pp', m_a["vol"] < m_b["vol"]),
        ("夏普(2%无风险)", f'{m_a["sharpe"]:.2f}', f'{m_b["sharpe"]:.2f}',
         f'{m_a["sharpe"]-m_b["sharpe"]:+.2f}', m_a["sharpe"] > m_b["sharpe"]),
        ("Calmar", f'{m_a["calmar"]:.2f}', f'{m_b["calmar"]:.2f}',
         f'{m_a["calmar"]-m_b["calmar"]:+.2f}', m_a["calmar"] > m_b["calmar"]),
        ("持仓天数占比", f'{m_a["hold_rate"]:.1f}%', f'{m_b["hold_rate"]:.1f}%',
         f'{m_a["hold_rate"]-m_b["hold_rate"]:+.1f}pp', True),
        ("买卖次数", f'{m_a["n_buy"]}买/{m_a["n_sell"]}卖', f'{m_b["n_buy"]}买/{m_b["n_sell"]}卖',
         "—", None),
        ("买入持有收益", f'{m_a["bh_ret"]:+.2f}%', f'{m_b["bh_ret"]:+.2f}%', "—", None),
        ("买入持有回撤", f'{m_a["bh_mdd"]:.2f}%', f'{m_b["bh_mdd"]:.2f}%', "—", None),
        ("策略超额 vs 持有", f'{m_a["ret"]-m_a["bh_ret"]:+.2f}pp',
         f'{m_b["ret"]-m_b["bh_ret"]:+.2f}pp', "—", None),
    ]
    trows = []
    for name, va, vb, diff, win in rows:
        mark = ""
        if win is True:
            mark = '<td class="win a">A</td>'
        elif win is False:
            mark = '<td class="win b">B</td>'
        else:
            mark = '<td class="win">—</td>'
        trows.append(f'<tr><td>{name}</td><td class="ta">{va}</td>'
                     f'<td class="tb">{vb}</td><td class="df">{diff}</td>{mark}</tr>')

    yrows = []
    for y in sorted(yr["512890"].keys()):
        ra = yr["512890"].get(y, {})
        rb = yr["512040"].get(y, {})
        yrows.append(
            f'<tr><td>{y}</td>'
            f'<td class="ta {("up" if ra.get("ret",0)>0 else "dn")}">{ra.get("ret",0):+.2f}%</td>'
            f'<td class="ta mut">{ra.get("bh",0):+.2f}%</td>'
            f'<td class="tb {("up" if rb.get("ret",0)>0 else "dn")}">{rb.get("ret",0):+.2f}%</td>'
            f'<td class="tb mut">{rb.get("bh",0):+.2f}%</td></tr>')

    srows = []
    for buy, sell in payload["sensi"]:
        s = payload["sensi"][(buy, sell)]
        best = max(s["a_nav"], s["b_nav"])
        srows.append(
            f'<tr><td>{buy*100:.0f}% / {sell*100:.0f}%</td>'
            f'<td class="ta">{s["a_nav"]:.4f}</td><td class="ta mut">{s["a_hold"]:.1f}%</td>'
            f'<td class="tb">{s["b_nav"]:.4f}</td><td class="tb mut">{s["b_hold"]:.1f}%</td>'
            f'<td>{"512890" if s["a_nav"]>s["b_nav"] else "512040"}</td></tr>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>512890 vs 512040 · 价值引擎 5 年对比</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;padding:22px 18px 40px;background:#eef0f3;color:#202020;
  font:14px/1.62 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1010px;margin:0 auto}}
.hero{{background:linear-gradient(120deg,#3d57e5,#4a64e8,#566de7);color:#fff;
  border-radius:12px;padding:20px 24px;margin-bottom:14px;
  box-shadow:0 1px 3px rgba(16,24,40,.05)}}
.hero h1{{margin:0 0 6px;font-size:19px;font-weight:700}}
.hero p{{margin:0;font-size:12.5px;opacity:.92}}
.card{{background:#fff;border:1px solid #e2e5ea;border-radius:12px;padding:16px 18px;
  margin-bottom:14px;box-shadow:0 1px 3px rgba(16,24,40,.05)}}
.card h2{{margin:0 0 12px;font-size:14.5px;font-weight:700;color:#202020}}
.card h2 i{{font-style:normal;color:#3d57e5;margin-right:6px}}
.tblwrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:12.8px}}
th,td{{padding:7px 8px;border-bottom:1px solid #eef0f3;white-space:nowrap}}
th{{background:#f4f6fa;color:#6c717f;font-weight:600;font-size:12px;text-align:right}}
th:first-child{{text-align:left}}
td:first-child{{color:#42474f}}
.ta{{color:#3d57e5;font-weight:600;text-align:right}}
.tb{{color:#d4380d;font-weight:600;text-align:right}}
.df{{text-align:right;color:#6c717f}}
.mut{{color:#8a909c;font-weight:400}}
.win{{text-align:center;font-size:11px;font-weight:700}}
.win.a{{color:#3d57e5}} .win.b{{color:#d4380d}}
.up{{color:#d4380d}} .dn{{color:#0f9d58}}
.good{{color:#3d57e5}}
.chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}}
.chip{{flex:1 1 150px;background:#f7f8fb;border:1px solid #e8ebf1;border-radius:9px;
  padding:9px 11px}}
.chip k{{display:block;font-size:11px;color:#6c717f;font-weight:500;margin-bottom:2px;
  white-space:normal}}
.chip b{{font-size:15.5px;font-weight:700}}
.chip.a b{{color:#3d57e5}} .chip.b b{{color:#d4380d}}
.chip s{{display:block;text-decoration:none;font-size:11px;color:#8a909c;margin-top:1px}}
.note{{background:#fffaf4;border:1px solid #ffe3c8;border-radius:9px;padding:11px 14px;
  font-size:12.5px;color:#7a4f22;line-height:1.7}}
.note b{{color:#b35309}}
.warn{{background:#fff5f5;border-color:#ffd6d6;color:#8a2b2b}}
.warn b{{color:#c0392b}}
ul{{margin:8px 0 0;padding-left:20px;font-size:13px;line-height:1.85}}
li{{margin-bottom:3px}}
.csvg{{overflow:hidden;max-width:100%;height:auto}}
.legend{{font-size:11.5px;color:#6c717f;margin-top:6px;white-space:normal}}
@media(max-width:700px){{
  body{{padding:14px 10px 30px}} .card{{padding:13px 12px}}
  .hero{{padding:16px 16px}} .hero h1{{font-size:16px}}
}}
</style></head><body><div class="wrap">

<div class="hero">
  <h1>红利低波 512890 vs 价值ETF 512040 —— 同一价值引擎规则的 5 年对比</h1>
  <p>窗口 {w['d0']} ~ {w['d1']}｜规则：收盘 &lt; MA200×0.97 买入、&gt; MA200×1.06 卖出、中间维持、未达买点持币
     ｜前复权 · 零手续费 · 初始净值 1</p>
</div>

<div class="card">
  <h2><i>①</i>净值曲线对比</h2>
  <div class="tblwrap">{chart}</div>
  <div class="legend">实线 = 策略净值；虚线 = 同期买入持有；底部色带 = 处于持仓状态的时间段。
    三角标记：▲ 买入、▼ 卖出（红买绿卖）。</div>
</div>

<div class="card">
  <h2><i>②</i>指标对照</h2>
  <div class="tblwrap"><table>
    <tr><th>指标</th><th>512890 红利低波</th><th>512040 价值ETF</th><th>差值</th><th>胜</th></tr>
    {''.join(trows)}
  </table></div>
  <div class="chips">
    <div class="chip a"><k>512890 最终净值</k><b>{m_a['final']:.4f}</b>
      <s>{m_a['ret']:+.2f}% · MDD {m_a['mdd']:.2f}%</s></div>
    <div class="chip b"><k>512040 最终净值</k><b>{m_b['final']:.4f}</b>
      <s>{m_b['ret']:+.2f}% · MDD {m_b['mdd']:.2f}%</s></div>
    <div class="chip"><k>收益差</k><b class="dn">{m_a['ret']-m_b['ret']:+.2f}pp</b>
      <s>512890 落后</s></div>
    <div class="chip"><k>回撤改善</k><b class="good">{m_a['mdd']-m_b['mdd']:+.2f}pp</b>
      <s>512890 回撤更浅</s></div>
  </div>
</div>

<div class="card">
  <h2><i>③</i>逐年表现（策略 / 买入持有）</h2>
  <div class="tblwrap"><table>
    <tr><th>年份</th><th>512890 策略</th><th>512890 持有</th>
        <th>512040 策略</th><th>512040 持有</th></tr>
    {''.join(yrows)}
  </table></div>
  <div class="legend">2021 年仅含 10/21 之后 2 个交易日，收益约 0 属正常。</div>
</div>

<div class="card">
  <h2><i>④</i>阈值敏感性：同一规则换买线/卖线</h2>
  <div class="tblwrap"><table>
    <tr><th>买线 / 卖线</th><th>512890 净值</th><th>持仓占比</th>
        <th>512040 净值</th><th>持仓占比</th><th>当期优胜</th></tr>
    {''.join(srows)}
  </table></div>
  <div class="legend">默认规则为 <b>3% / 6%</b>（即 0.97 / 1.06）。</div>
</div>

<div class="card">
  <h2><i>⑤</i>结论与风险提示</h2>
  <div class="note">
    <b>直接回答：同样规则下 512040 完胜，5 年多赚 {m_b['ret']-m_a['ret']:.2f}pp（净值 {m_b['final']:.4f} vs {m_a['final']:.4f}）。</b>
    但 512890 并非没有价值 —— 它把最大回撤从 {m_b['mdd']:.2f}% 压到 {m_a['mdd']:.2f}%，
    夏普 {m_a['sharpe']:.2f}、Calmar {m_a['calmar']:.2f} 都略优，波动只有 {m_a['vol']:.2f}%（512040 为 {m_b['vol']:.2f}%）。
  </div>
  <ul>
    <li><b>收益差的主因不是选股，而是「买线打不到」。</b>512890 近 5 年相对 MA200 的偏离均值
      +3.50%、10 分位仅 −1.55%，收盘跌破 −3% 的天数只占 <b>4.1%</b>（512040 为 10.1%）。
      同一根 −3% 买线对低波品种过于苛刻，导致持仓率只有 <b>{m_a['hold_rate']:.1f}%</b>
      （512040 为 {m_b['hold_rate']:.1f}%），大段时间在持币看戏。
      把买线放宽到 0%（即回踩 MA200 就买），512890 净值从 1.6707 升到 <b>2.0730</b>，
      反超 512040 的 1.7909 —— 说明<b>规则参数与标的波动特征必须匹配</b>。</li>
    <li><b>逐年看是「一致性 vs 爆发力」。</b>512890 每年都是正收益但幅度温和；
      512040 在 2023（{yr['512040'].get('2023',{}).get('ret',0):+.1f}%）、
      2025（{yr['512040'].get('2025',{}).get('ret',0):+.1f}%）明显更强，
      但 2025 年 512890 策略全年持币收益 0，而标的本身涨
      {yr['512890'].get('2025',{}).get('bh',0):+.1f}% —— 这是规则失效的代价，不是标的问题。</li>
    <li><b>两者同期买入持有的收益几乎一样</b>（{m_a['bh_ret']:+.2f}% vs {m_b['bh_ret']:+.2f}%），
      所以差别全部来自择时规则与标的波动结构的耦合，不是「哪个基金更好」。</li>
    <li><b>样本只有一个 5 年窗口、单标的、事后结果。</b>阈值网格里 0.97/1.06 恰好是最优点，
      存在选参痕迹；换窗口、换标的排序可能翻转。<b>不能据此判定 512890 该替代 512040</b>，
      只能说明：若坚持 −3% 买线，512890 的规则触发率过低，需要单独校准参数。</li>
    <li><b>更重要的是：0.97/1.06 对两只都不是最优。</b>512890 在 <b>1.00/1.06</b> 达到 2.0730
      （反超 512040 同参数下的 1.7909）；512040 在 <b>0.97/1.10</b> 达到 2.5018。
      两只标的最优点不同、且都在网格边缘，说明这套「−3%/+6%」阈值是<b>围绕 512040 的历史
      走势挑出来的</b>，天然不适合 512890。真要换标的，阈值必须重估，不能直接套用。</li>
  </ul>
</div>

<div class="card">
  <h2><i>⑥</i>数据口径与已知坑</h2>
  <div class="note warn">
    <b>512890 在新浪在线日线里有一段 −51% 的假跳空，是份额拆分未还原造成的。</b>
    华泰柏瑞公告：512890 于 <b>2021-10-22</b> 做 <b>1:2 份额拆分</b>
    （拆分前净值 1.6004 → 拆分后 0.8002），2021-10-25 复牌。
    新浪序列没做回溯复权，于是那天出现 −51.13%；而 <code>fetch_online</code> 报的
    <code>factors=1.0</code> 是<b>单常数因子，修不掉序列中段事件</b>。
    512040 在 2023-10-11 也有同类 −50.13%。<br>
    本报告曲线一律用<b>已核验长历史前复权 CSV 打底 + 在线补尾部 13 个交易日</b>，
    窗口内 512890 无任何 |日收益| &gt; 8% 的异常，数据可用。
  </div>
  <div class="legend">收益结算口径：用 <b>k−1 日收盘后的状态</b>决定 k−1→k 的涨跌（信号当日成形、次日才吃涨跌），
    与 <code>etf_backtest_long.backtest()</code> 一致，无未来函数。
    数据来源：{'; '.join(m_a['notes'])}（512890）｜{'; '.join(m_b['notes'])}（512040）。</div>
</div>

</div></body></html>"""


def main():
    p = os.path.join(OUT, "ve_512890_vs_512040.json")
    payload = json.load(open(p, encoding="utf-8"))

    # 阈值敏感性（复用 payload 里的原始数据不够，这里重算）
    import ve_512890_study as study
    sensi = study.sensitivity()
    payload["sensi"] = {tuple(k): v for k, v in sensi.items()}

    # 补风险指标
    for c in ("512890", "512040"):
        s = payload["series"][c]
        m = payload["metrics"][c]
        nav = pd.Series(s["nav"], index=pd.to_datetime(s["dates"]))
        r = nav.pct_change().dropna()
        m["vol"] = float(r.std() * np.sqrt(252) * 100)
        m["sharpe"] = float((m["cagr"] - 2.0) / m["vol"]) if m["vol"] else 0.0
        m["calmar"] = float(m["cagr"] / abs(m["mdd"])) if m["mdd"] else 0.0

    html = build_html(payload)
    out = os.path.join(OUT, "价值引擎对比_512890_vs_512040.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[saved] {out}  ({len(html)} chars)")

    with open(p, "w", encoding="utf-8") as f:
        json.dump({**payload,
                   "sensi": {f"{k[0]}_{k[1]}": v for k, v in payload["sensi"].items()}},
                  f, ensure_ascii=False, indent=1)
    return out


if __name__ == "__main__":
    main()
