# -*- coding: utf-8 -*-
"""生成「MACD 柱买入 + J 卖出」对比报告（512040 / 512890）。

依赖 ve_macd_study.py 产出的 out/ve_macd.json。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

C_A = "#8b96ab"      # A 原版：蓝灰
C_D = "#3d57e5"      # D 完整方案：蓝
C_BH = "#c9a227"     # 买入持有：金

VAR = [("A", "A · 原版", "进区间即买 ／ +6% 卖", C_A),
       ("B", "B · 只加 MACD", "进区间＋柱转强 ／ +6% 卖", "#7a5cd6"),
       ("C", "C · 只改卖出", "进区间即买 ／ +6%且J>80 卖", "#12876f"),
       ("D", "D · 两个都改", "进区间＋柱转强 ／ +6%且J>80 卖", C_D)]


def _poly(vals, x0, y0, w, h, lo, hi):
    n = len(vals)
    if n < 2 or hi <= lo:
        return ""
    return " ".join(
        f"{x0 + w * i / (n - 1):.1f},{y0 + h - h * (float(v) - lo) / (hi - lo):.1f}"
        for i, v in enumerate(vals))


def svg_chart(d, *, W=960, H=360):
    """A(原版) vs D(完整方案) 净值曲线 + 买入持有 + D 的持仓色带。"""
    a, b = d["A"], d["D"]
    pl, pr, pt, pb = 58, 96, 18, 46
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
    n = len(a["nav"])
    o = [f'<svg class="csvg" viewBox="0 0 {W} {H}" width="100%" '
         f'style="max-width:{W}px;display:block" xmlns="http://www.w3.org/2000/svg">',
         '<rect width="100%" height="100%" fill="#ffffff"/>']
    for tv in ticks:
        y = pt + ih - ih * (tv - lo) / (hi - lo)
        o.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}" stroke="#eef0f3"/>')
        o.append(f'<text class="cg" x="{pl-8}" y="{y+3.5:.1f}">{tv:.2f}</text>')

    # D 持仓色带
    hold = b["hold"]
    y0b = pt + ih + 8
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
    o.append(f'<polyline fill="none" stroke="{C_D}" stroke-width="2.3" '
             f'points="{_poly(b["nav"], pl, pt, iw, ih, lo, hi)}"/>')

    ends = []
    for series, color, lab in ((a["nav"], C_A, "原版"), (b["nav"], C_D, "D 方案")):
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
        x = pl + iw * idx / (n - 1)
        o.append(f'<text class="cx" x="{x:.1f}" y="{pt+ih+30:.1f}">{dates[idx]:%Y-%m}</text>')
    o.append(f'<line x1="{pl}" y1="{pt+ih}" x2="{pl+iw}" y2="{pt+ih}" stroke="#dfe3ea"/>')
    o.append("</svg>")
    return "".join(o)


def build_html(P):
    T = P["targets"]
    cards = []

    for code in ("512040", "512890"):
        d = T[code]
        A, B, C, D = d["A"], d["B"], d["C"], d["D"]
        zone = d["zone"]
        bs = d["buy_state"]
        ss = d["sell_state"]
        fb = d["fallback"]
        n_buy_ok = sum(1 for b in bs if b["direction"] == "缩短↑")
        n_wait_fail = sum(1 for b in bs if b["waited"] is None)
        n_sell_ok = sum(1 for s in ss if s["passed"])
        d_macd = D["final"] - A["final"]
        c_ret = C["ret"] - A["ret"]

        # --- 四组指标表 ---
        # 只有「收益类」用红涨绿跌；夏普/Calmar/持仓/天数 等非方向性指标一律中性色，
        # 否则「1.32 红、0.70 也红」看不出谁更好（项目既有规范）。
        DIRECTIONAL = {"ret", "cagr", "avg_ret"}

        def mrow(label, key, nd=2, pct=True, risk=False):
            cells = []
            for tag, _lab, _x, col in VAR:
                v = {"A": A, "B": B, "C": C, "D": D}[tag][key]
                if v is None:
                    txt = "—"
                elif pct:
                    txt = f"{v:+.{nd}f}%"
                else:
                    txt = f"{v:.{nd}f}"
                if risk:
                    cls = "tn"
                elif key in DIRECTIONAL:
                    cls = "up" if (v or 0) > 0 else ("dn" if (v or 0) < 0 else "df")
                else:
                    cls = "neu"
                cells.append(f'<td class="{cls}">{txt}</td>')
            return f"<tr><td>{label}</td>{''.join(cells)}</tr>"

        metric_rows = "".join([
            mrow("期末净值", "final", 4, False),
            mrow("区间收益", "ret"),
            mrow("年化收益", "cagr"),
            mrow("最大回撤", "mdd", risk=True),
            mrow("夏普", "sharpe", 2, False),
            mrow("Calmar", "calmar", 2, False),
            mrow("持仓占比", "hold_rate", 1),
            mrow("平均单笔", "avg_ret"),
            mrow("平均持仓日", "avg_days", 1, False),
        ])
        cnt_row = (f'<tr><td>买 / 卖次数</td>'
                   + "".join(f'<td class="df">{R["n_buy"]} / {R["n_sell"]}</td>'
                             for R in (A, B, C, D)) + "</tr>")

        # --- 买点柱状态表 ---
        brows = []
        for b in bs:
            w = "—" if b["waited"] is None else f"{b['waited']} 日"
            fd = "区间内未等到" if b["waited"] is None else b["found"]
            dcls = "dir-up" if b["direction"] == "缩短↑" else "dir-dn"
            brows.append(f'<tr><td>{b["date"]}</td>'
                         f'<td class="df">{b["hist"]:+.4f}</td>'
                         f'<td class="df">{b["prev"]:+.4f}</td>'
                         f'<td class="{dcls}">{b["direction"]}</td>'
                         f'<td class="df">{w}</td><td class="df">{fd}</td>'
                         f'<td class="df">{b["ret"]:+.2f}%</td></tr>')

        # --- 卖出日 J 表 ---
        srows = []
        for s in ss:
            pc = "yes" if s["passed"] else "no"
            srows.append(f'<tr><td>{s["date"]}</td>'
                         f'<td class="df">{s["over"]:+.2f}%</td>'
                         f'<td class="df">{s["j"]:.1f}</td>'
                         f'<td class="{pc}">{"是" if s["passed"] else "否 · 拦住"}</td>'
                         f'<td class="df">{s["ret"]:+.2f}%</td></tr>')

        # --- J 阈值扫描 ---
        jrows = []
        for thr, s in d["jscan"].items():
            hit = ' class="pk"' if abs(float(thr) - 80.0) < 1e-9 else ""
            jrows.append(f'<tr{hit}><td>J &gt; {thr}{"（本次）" if hit else ""}</td>'
                         f'<td class="ta">{s["final"]:.4f}</td>'
                         f'<td class="{"up" if s["ret"]>0 else "dn"}">{s["ret"]:+.2f}%</td>'
                         f'<td class="tn">{s["mdd"]:.2f}%</td>'
                         f'<td class="df">{s["sharpe"]:.2f}</td>'
                         f'<td class="df">{s["hold_rate"]:.1f}%</td>'
                         f'<td class="df">{s["n_buy"]} / {s["n_sell"]}</td></tr>')

        # --- J 扫描结论文案（两只标的截然不同，必须分开写）---
        _js = {k: v["final"] for k, v in d["jscan"].items()}
        _jmin, _jmax = min(_js.values()), max(_js.values())
        if _jmax - _jmin < 1e-9 and abs(_jmax - A["final"]) < 1e-9:
            jscan_note = (
                f"<b>阈值从 70 扫到 90，净值纹丝不动，全部等于 A 原版的 {A['final']:.4f}。</b>"
                f"原因就是上面那张表：{n_sell_ok}/{len(ss)} 个卖出日的 J 已经 &gt;80，"
                f"最低也有 {min(x['j'] for x in ss):.1f}，所以任何 ≤90 的阈值都拦不住任何一次卖出。"
                f"<b>这道门槛在这只标的上是纯粹的空转。</b>")
        else:
            _best = max(d["jscan"].items(), key=lambda kv: kv[1]["final"])
            _v70 = _js.get("70", float("nan"))
            _v75 = _js.get("75", float("nan"))
            _v80 = _js.get("80", float("nan"))
            jscan_note = (
                f"阈值 70 / 75 时净值 {_v70:.4f} / {_v75:.4f}，"
                f"与 A 原版 {A['final']:.4f} 完全相同 —— 因为唯一一次 J&lt;80 的卖出"
                f"（{ss[0]['date']} J={ss[0]['j']:.1f}）在阈值 ≤75 时会被放行。"
                f"　阈值提到 80 后净值 {_v80:.4f}，"
                f"{_best[0]} 时最高 {_best[1]['final']:.4f}"
                f"（比原版 {_best[1]['final'] - A['final']:+.2f}pp）。"
                f"但这点差异只来自<b>一次卖出被推迟 1 个交易日</b>，属单点事件，不是可复用的规律。"
                f"<b>结论：J 卖出条件最多能改变个别卖点，不构成系统性改善。</b>")

        # --- 逐笔（A vs D）---
        def trow(t, show_hist):
            now = ' class="now"' if t.get("open") else ""
            cls = "up" if t["ret"] > 0 else "dn"
            extra = ""
            if show_hist:
                hb = "—" if t.get("hist_at_buy") is None else f'{t["hist_at_buy"]:+.4f}'
                extra = f'<td class="df">{hb}</td>'
            return (f'<tr{now}><td>{t["start"]}→{t["end"]}</td>'
                    f'<td class="df">{t["days"]}日</td>'
                    f'<td class="{cls}">{t["ret"]:+.2f}%</td>{extra}</tr>')

        ta = "".join(trow(t, False) for t in A["trades"])
        td = "".join(trow(t, True) for t in D["trades"])

        # --- 结论文案（提前算，避免 f-string 内多行）---
        if abs(d_macd) < 1e-6 and c_ret == 0:
            verdict = "两个改动都没有产生任何影响"
        elif code == "512040":
            verdict = "两个改动都不利（MACD 灾难性，J 卖出完全无效）"
        else:
            verdict = "MACD 拖后腿，J 卖出几乎无感"

        if code == "512040":
            body = (f'<b>只加 MACD 柱转强：{B["final"]:.4f}（{B["ret"]:+.2f}%），比原版少 '
                    f'{abs(B["final"]-A["final"]):.4f} 净值。</b>根因极其明确 —— '
                    f'<b>6 个买点里没有一个</b>当天柱子在「缩短」，{n_wait_fail} 个在剩余区间内'
                    f'也等不到转强，于是买入次数从 6 次塌到 2 次。'
                    f'<b>只改卖出（+6% 且 J&gt;80）：净值 {C["final"]:.4f}，与不做改动逐位相同</b> —— '
                    f'5 个卖出日的 J 分别是 {", ".join(f"{s["j"]:.1f}" for s in ss)}，全部已 &gt;80，'
                    f'这道门槛等于没有。')
        else:
            body = (f'<b>只加 MACD 柱转强：{B["final"]:.4f}（{B["ret"]:+.2f}%），略差于原版。</b>'
                    f'9 个买点里只有 {n_buy_ok} 个当天柱在缩短，{n_wait_fail} 个整段等不到；'
                    f'但它对买点的筛选确实提高了单笔质量（{A["avg_ret"]:+.2f}% → {B["avg_ret"]:+.2f}%）'
                    f'——只是<b>漏掉的更多</b>，持仓从 {A["hold_rate"]:.1f}% 掉到 {B["hold_rate"]:.1f}%。'
                    f'<b>只改卖出：{C["final"]:.4f}（{C["ret"]:+.2f}%），比原版多 {c_ret:+.2f}pp</b> —— '
                    f'唯一一处正贡献，但只有 {len(ss)} 次卖出里 1 次被 J 拦住'
                    f'（{ss[0]["date"]} J={ss[0]["j"]:.1f}），属噪声量级。')

        cards.append(f"""
<div class="card">
  <h2><i>■</i>{code} {d['name']}　<span class="sub2">buy={d['buy']:.2f} / sell={d['sell']:.2f}
    / MA{d['ma']}　窗口 {A['d0'][:10]} ~ {A['d1'][:10]}（{A['n']} 日）</span></h2>

  <div class="verdict">
    <div class="vh">结论：{verdict}</div>
    <div class="vt">原版 <b>{A['final']:.4f}</b>（{A['ret']:+.2f}%）　→　
      D 完整方案 <b>{D['final']:.4f}</b>（{D['ret']:+.2f}%）　
      <span class="dlt">差 {D['ret']-A['ret']:+.2f}pp（净值 {d_macd:+.4f}）</span><br>
      {body}
    </div>
  </div>

  <div class="tblwrap">{svg_chart(d)}</div>
  <div class="legend">粗线：<span style="color:{C_A}">灰 = A 原版</span> ／
    <span style="color:{C_D}">蓝 = D 完整方案</span>；金色虚线 = 同期买入持有；
    底部浅蓝色带 = D 方案的实际持仓时段（对比曲线可见它明显更少在场）。</div>

  <div class="tblwrap" style="margin-top:14px"><table>
    <tr><th>指标</th><th>A · 原版</th><th>B · 只加 MACD</th><th>C · 只改卖出</th><th>D · 两个都改</th></tr>
    {metric_rows}{cnt_row}
  </table></div>
  <div class="legend">同期买入持有：{A['bh_final']:.4f}（{A['bh_ret']:+.2f}%，回撤 {A['bh_mdd']:.2f}%）。
    单变量拆开看才能分清是谁在起作用：B 只动买入、C 只动卖出、D 是两者叠加。</div>

  <div class="tsplit" style="margin-top:14px">
    <div class="tcard a"><h3>A · 原版（{len(A['trades'])} 笔）</h3>
      <table class="tt"><tr><th>区间</th><th>天数</th><th>收益</th></tr>{ta}</table>
      <div class="avg">平均单笔 <b>{A['avg_ret']:+.2f}%</b>　平均 <b>{A['avg_days']:.0f} 日</b></div>
    </div>
    <div class="tcard b"><h3>D · 完整方案（{len(D['trades'])} 笔）</h3>
      <table class="tt"><tr><th>区间</th><th>天数</th><th>收益</th><th>买入柱</th></tr>{td}</table>
      <div class="avg">平均单笔 <b>{D['avg_ret']:+.2f}%</b>　平均 <b>{D['avg_days']:.0f} 日</b></div>
    </div>
  </div>

  <div class="note" style="margin-top:12px">
    <p><b>根因一：买点当天，MACD 柱几乎总在「绿柱加长」——因为把价格砸到买线以下的，正是那段下跌。</b>
    下表逐笔列出原版每个买点当天的柱子与前一天的对比，以及「在仍处买入区间的前提下」等到第一次转强要几天：</p>
  </div>
  <div class="tblwrap"><table>
    <tr><th>原版买点日</th><th>当日柱</th><th>前日柱</th><th>方向</th>
        <th>等到转强</th><th>首根转强日</th><th>该笔实际收益</th></tr>
    {''.join(brows)}
  </table></div>
  <div class="legend"><b>{len(bs)} 个买点里只有 {n_buy_ok} 个当天柱已在缩短</b>；
    <b>{n_wait_fail} 个在剩余区间内根本等不到转强</b>（买入区间段长中位数仅 {zone['med_days']:.0f} 个交易日，
    {zone['n_miss']}/{zone['n_seg']} 段整段等不到）。
    「柱转强」是滞后指标，「跌破买线」是同步事件，两者时间尺度不匹配 —— 和上一轮 J&lt;20 门槛的病灶完全一样。</div>

  <div class="note" style="margin-top:12px">
    <p><b>根因二：「+6% 且 J&gt;80」里的 J&gt;80 是自动满足的。</b>
    涨了 6% 的标的本就处在超买区，J 天然在高位。下表是原版每个「卖出信号日」的 J 值：</p>
  </div>
  <div class="tblwrap"><table>
    <tr><th>卖出信号日</th><th title="收盘 ÷ (MA200×sell) − 1；+0.01% = 刚踩线就卖">越过卖线</th><th>当日 J</th><th>J&gt;80</th><th>该笔收益</th></tr>
    {''.join(srows)}
  </table></div>
  <div class="legend"><b>{n_sell_ok}/{len(ss)} 个卖出日的 J 已 &gt;80</b>。
    所以这个「组合条件」实际上退化成了原来的「+6% 就卖」，
    {('这也是为什么 C 组（只改卖出）净值与 A 组逐位相同。' if code == '512040' else '只在 1 次卖出上产生了差别。')}</div>

  <div class="tblwrap" style="margin-top:14px"><table>
    <tr><th>卖出 J 阈值（买入固定 = 原版「进区间即买」，只动卖出侧）</th><th>净值</th><th>收益</th>
        <th>最大回撤</th><th>夏普</th><th>持仓</th><th>买/卖</th></tr>
    {''.join(jrows)}
    <tr class="pk"><td><b>不做 J 限制（= A 原版）</b></td><td class="ta">{A['final']:.4f}</td>
        <td class="{'up' if A['ret']>0 else 'dn'}">{A['ret']:+.2f}%</td>
        <td class="tn">{A['mdd']:.2f}%</td><td class="df">{A['sharpe']:.2f}</td>
        <td class="df">{A['hold_rate']:.1f}%</td><td class="df">{A['n_buy']} / {A['n_sell']}</td></tr>
  </table></div>
  <div class="legend">{jscan_note}</div>

  <div class="note" style="margin-top:12px">
    <p><b>如果一定要保留这个门槛，必须加「兜底」。</b>
    把规则改成「区间内等到柱转强就买，等不到就在区间最后一天买」，结果：
    <b>F 兜底版 {fb['F']['final']:.4f}</b>（持仓 {fb['F']['hold_rate']:.1f}%，买 {fb['F']['n_buy']} 次）、
    <b>G 兜底+J&gt;80卖 {fb['G']['final']:.4f}</b>，对比原版 {A['final']:.4f}。
    {'兜底能把损失从 −0.67 收回到 −0.12，但仍然是亏的 —— 说明 MACD 在这只标的上连「挑买点」都没做到。'
     if code == '512040' else
     f'兜底后反而略高于原版（{fb["F"]["ret"] - A["ret"]:+.2f}pp）。'
     f'但这不是 MACD 的信息量：下一张表显示<b>无条件延后 1 日就掉到 {d["delay"][1]["final"]:.4f}</b>，'
     f'「晚买」本身是有害的。F 之所以略好，只是因为它恰好在少数几个买点上延后对了地方 —— '
     f'和「J&lt;20 能挑到更好的买点」是同一类错觉，换窗口即失效。'}</p>
  </div>

  <div class="tblwrap"><table>
    <tr><th>对照：无条件延后 N 个交易日买入（完全不看 MACD）</th><th>净值</th><th>收益</th>
        <th>持仓</th><th>夏普</th></tr>
    {''.join(f'<tr{" class=\'pk\'" if x["delay"]==0 else ""}><td>延后 {x["delay"]} 日'
             f'{"（= 原版）" if x["delay"]==0 else ""}</td>'
             f'<td class="ta">{x["final"]:.4f}</td>'
             f'<td class="{"up" if x["ret"]>0 else "dn"}">{x["ret"]:+.2f}%</td>'
             f'<td class="df">{x["hold_rate"]:.1f}%</td>'
             f'<td class="df">{x["sharpe"]:.2f}</td></tr>' for x in d["delay"])}
  </table></div>
  <div class="legend">无条件延后 1 日就掉到 {d['delay'][1]['final']:.4f}、2 日 {d['delay'][2]['final']:.4f}，
    <b>越延越差</b>。所以 MACD 版的表现不能简单归因于「晚买」——它确实选择性跳过了特定买点，
    只是跳过的地方多数是盈利段。<b>这也说明该策略对「第一时间进场」高度敏感</b>：
    买线一触发就该买，任何额外等待都在漏收益。</div>
</div>""")

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MACD 柱买入 + J 卖出 · 对比回测（512040 / 512890）</title>
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
.df{{text-align:right;color:#6c717f}}
.up{{color:#d4380d;font-weight:600;text-align:right}}
.dn{{color:#0f9d58;font-weight:600;text-align:right}}
.tn{{color:#42474f;font-weight:600;text-align:right}}
.neu{{color:#42474f;font-weight:600;text-align:right}}
.yes{{color:#0f9d58;font-weight:600;text-align:center}}
.no{{color:#d4380d;font-weight:600;text-align:right}}
.dir-up{{color:#0f9d58;font-weight:600;text-align:right}}
.dir-dn{{color:#d4380d;font-weight:600;text-align:right}}
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
  <h1>MACD 柱买入择时 ＋ J 卖出择时 · 近 5 年对比回测</h1>
  <p>买入：进买入区间后，<b>以 MACD 柱转强那天买</b>（绿柱缩短／红柱加长，数学上是同一个条件：
     今日柱 &gt; 昨日柱）。卖出：从「涨到 +6%」改成「<b>涨到 +6% 且 J&gt;80</b>」。<br>
     口径：<b>前复权 · 零手续费 · 初始净值 1</b>　｜　窗口 {T['512040']['A']['d0'][:10]} ~
     {T['512040']['A']['d1'][:10]}（{T['512040']['A']['n']} 个交易日 / {T['512040']['A']['yrs']:.2f} 年，两只同窗口）<br>
     收益结算：k−1 日收盘后的状态决定 k−1→k 涨跌（信号当日成交、次日才吃涨跌），无未来函数。</p>
</div>

<div class="card">
  <h2><i>◆</i>一句话结论</h2>
  <div class="note">
    <p><b>两个改动都没有价值，而且各自有一个非常干脆的致命原因。</b></p>
    <p><b>① MACD 柱转强买入 —— 无效。</b>
    把价格砸到买线以下的，正是那段让 MACD 柱不断「绿柱加长」的下跌。
    所以买点当天柱子几乎总在变长而不是缩短：512040 的 <b>6 个买点没有一个</b>当天满足，
    <b>5 个</b>在整个买入区间内都等不到转强，买入次数从 6 次塌到 2 次，净值 1.9916 → 1.3221（−33.6%）。
    512890 好一些（9 个买点里 2 个当天满足），但仍是 2.1010 → 2.0463。</p>
    <p><b>② 卖出加 J&gt;80 —— 形同虚设。</b>
    涨了 6% 的标的本来就处在超买区，J 天然在高位：512040 的 <b>5 个卖出日 J 全部 &gt;80</b>
    （96.9 / 92.5 / 106.5 / 115.6 / 96.2），512890 是 <b>7/8</b>。
    所以「+6% 且 J&gt;80」几乎等于原来的「+6%」：512040 上只改卖出，净值 <b>逐位相同</b>（1.9916）；
    512890 上多 +0.78pp，属噪声。</p>
    <p><b>叠加起来（D 完整方案）：512040 1.3221（−66.95 净值），512890 2.0539（−4.71 净值）。两个都亏。</b></p>
    <p>这里有一条贯穿三轮实验的规律：<b>这套策略的钱来自「买线一触发就进场、涨到 +6% 就走」这个极简节奏。
    任何在买入侧额外加的「等待条件」（J&lt;20、MACD 柱转强、哪怕只是无条件延后 1 天）都在漏收益；
    而卖出侧的「再加一个条件」因为 6% 已经隐含超买，基本不会生效。</b></p>
  </div>
</div>

{''.join(cards)}

<div class="card">
  <h2><i>◆</i>如果还想用 MACD，哪些位置才有意义</h2>
  <ul>
    <li><b>放在卖出侧，而不是买入侧。</b>卖出条件改成「柱由正转负（红柱翻绿）才卖」，
      这个信号是<b>趋势转弱</b>的确认，和「涨到 +6%」不重叠，才有可能真正多留利润。
      买入侧的条件都被验证无效，因为买点本身就发生在下跌末端，任何「等确认」都会漏掉反弹第一段。</li>
    <li><b>如果一定要在买入侧加门槛，必须配「兜底」。</b>本次兜底版（等不到就在区间末日买）能把
      512040 从 1.3221 拉回 {T['512040']['fallback']['F']['final']:.4f}、
      512890 拉到 {T['512890']['fallback']['F']['final']:.4f}。但前者仍不如原版，
      说明兜底只是止损、不是增强。</li>
    <li><b>更值得试的方向是仓位而非开关。</b>把「进区间买 50%」当作底仓，
      再让 J 或 MACD 决定「加不加另外 50%」——这样即使指标判断错了，也只是少赚，不会空仓错过整段。</li>
    <li><b>不建议做的：</b>继续微调 J 的卖出阈值（70/75/85/90）或 MACD 参数（5/35/5 之类）。
      512040 上 J 阈值从 70 扫到 90 结果<b>逐位相同</b>（都是 {T['512040']['A']['final']:.4f}）；
      512890 上虽然 J&gt;90 能到 {T['512890']['jscan']['90']['final']:.4f}，但对比逐笔可见
      差异只来自<b>一次卖出晚了一天</b>。这类「靠单个事件撑起来的改善」换个窗口就会消失。</li>
  </ul>
</div>

<div class="card">
  <h2><i>◆</i>数据与口径</h2>
  <ul>
    <li><b>数据源</b>：<code>tdx_adj_long_2019_2026.csv</code>，自带 open/high/low/close 四价且已前复权
      （MACD 用 close，J 用 high/low）。长历史 T0 = 2026-09-24，用新浪在线四价补至 2026-10-23
      （15 个交易日），拼接前校验重叠末日收盘 <b>ratio = 1.000000</b>。</li>
    <li><b>指标参数</b>：MACD(12,26,9)，柱 = (DIF − DEA) × 2（通达信口径；乘 2 不影响「变长/缩短」判断）。
      KDJ(9,3,3)，K/D 初值 50，J = 3K − 2D。两只均用各自已拍板的买线（512040 用 −3%，512890 用回踩）。</li>
    <li><b>「柱转强」的定义</b>：<code>hist[t] &gt; hist[t−1]</code>。同时覆盖「绿柱缩短」（−5→−3）与
      「红柱加长」（+3→+5）；红柱缩短、绿柱加长、持平都不算。<b>本规则等价于用户的原始描述，未做任何放宽。</b></li>
    <li><b>四组单变量拆解</b>：A 原版 / B 只加 MACD / C 只改卖出 / D 两个都改。
      只有这样拆才能判断「是谁在起作用」，否则 D 的亏损会被误记到某一个改动头上。</li>
    <li><b>交叉校验</b>：A 组净值与日报 <code>build_ve_curve()</code> 逐点一致
      （512040 = 1.9916、512890 = 2.1010，与 2026-10-23 报告吻合）；
      并用<b>向量化独立实现</b>复算全部 8 组，最大差 <b>0.00e+00</b>。</li>
    <li><b>零手续费的原因</b>：只观察规则本身的择时价值。若计入双边 0.2%，
      MACD 版换手更低会略微受益，但改变不了「漏掉大量盈利段」的定性结论。</li>
    <li><b>最大局限</b>：5 年窗口内只有 6~9 个买点、5~8 个卖点，<b>样本量极小</b>。
      任何「少了 1 个买点」都会造成几十个百分点差异，这已在噪声量级。
      本报告能给出的确定结论是「这个改法不改善」，而不是「MACD 对所有策略无用」。</li>
  </ul>
</div>

<div class="card" style="border-color:#ffd6d6">
  <h2 style="color:#c0392b"><i style="color:#c0392b">◆</i>风险提示</h2>
  <ul>
    <li>本报告全部为<b>事后规则复算（模型输出）</b>，不是实盘成交记录，不构成任何个人投资建议。</li>
    <li>单窗口、单标的、样本量 6~9 笔 ⇒ <b>不足以支撑「MACD 无效」的一般性结论</b>，
      只能说在这两只标的、这 5 年、这套买卖线下，加上去是负贡献。</li>
    <li>零手续费口径会系统性高估所有高换手策略；实盘请按双边 0.2% 自行折算。</li>
    <li>J 的买入门槛（J&lt;20）已在上一轮实验中证伪并从策略中删除；本报告的 J 仅用于卖出侧。</li>
  </ul>
</div>

</div></body></html>"""


def main():
    p = os.path.join(OUT, "ve_macd.json")
    P = json.load(open(p, encoding="utf-8"))
    html = build_html(P)
    out = os.path.join(OUT, "MACD买入_J卖出对比_512040_512890.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[saved] {out} ({len(html)} chars)")
    return out


if __name__ == "__main__":
    main()
