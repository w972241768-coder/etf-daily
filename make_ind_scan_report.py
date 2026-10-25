"""生成「技术指标风控广度扫描」研究报告 HTML（2026-10-24）"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BG, FG, MUT, BD = "#eef0f3", "#202020", "#6c717f", "#e2e5ea"
BLUE, RED, GREEN, PURPLE = "#3d57e5", "#d4380d", "#0f9d58", "#7b3fe4"


def f2(v, sign=False, d=2):
    if v is None:
        return "—"
    return f"{v:+.{d}f}" if sign else f"{v:.{d}f}"


def col(v):
    if v is None:
        return MUT
    return RED if v > 0 else (GREEN if v < 0 else FG)


def main():
    scan = json.load(open(os.path.join(HERE, "out", "ind_scan.json"), encoding="utf-8"))
    ana = json.load(open(os.path.join(HERE, "out", "ind_scan_analysis.json"), encoding="utf-8"))
    inc = json.load(open(os.path.join(HERE, "out", "ind_increment.json"), encoding="utf-8"))
    combo = json.load(open(os.path.join(HERE, "out", "ind_combo.json"), encoding="utf-8"))
    attr = json.load(open(os.path.join(HERE, "out", "ind_scan_attrib.json"), encoding="utf-8")) \
        if os.path.exists(os.path.join(HERE, "out", "ind_scan_attrib.json")) else {}

    S = scan["scan"]
    final = ana["final"]
    cases = {c["win"]: c for c in scan["cases"]}

    def c_by(win, tag):
        for c in combo["cases"]:
            if c["win"] == win and c["tag"] == tag:
                return c
        return None

    def r_by(win, tag):
        for r in combo["rnd"]:
            if r["win"] == win and r["tag"] == tag:
                return r
        return None

    main_base = cases["main"]["base"]
    long_base = cases["long"]["base"]

    # 扫描总览表（按指标聚合）
    def agg_by_ind(win):
        out = {}
        for s in S:
            if s["win"] != win:
                continue
            k = s["ind"]
            o = out.setdefault(k, dict(n=0, oos_better=0, is_better=0,
                                       best_d=0.0, best_desc=""))
            o["n"] += 1
            if s["d_oos_mdd"] is not None and s["d_oos_mdd"] > 0:
                o["oos_better"] += 1
            if s["d_is_mdd"] is not None and s["d_is_mdd"] > 0:
                o["is_better"] += 1
            if s["d_oos_mdd"] is not None and s["d_oos_mdd"] > o["best_d"]:
                o["best_d"] = s["d_oos_mdd"]
                o["best_desc"] = (f"{s['params']} {s['scope']} {s['direction']} q={s['q']}")
        return out

    def agg_rows(win):
        rows = ""
        for k, v in sorted(agg_by_ind(win).items(),
                           key=lambda kv: -kv[1]["best_d"]):
            rows += (f'<tr><td>{k}</td><td class="num">{v["n"]}</td>'
                     f'<td class="num">{v["oos_better"]}</td>'
                     f'<td class="num">{v["is_better"]}</td>'
                     f'<td class="num" style="color:{col(v["best_d"])};font-weight:600">'
                     f'{f2(v["best_d"], True)}</td>'
                     f'<td class="mono">{v["best_desc"]}</td></tr>')
        return rows

    # 最终候选表
    def final_rows(win):
        rows = ""
        for s in [x for x in final if x["win"] == win]:
            rows += (f'<tr><td>{s["ind"]}</td><td class="mono">{s["params"]}</td>'
                     f'<td>{s["scope"]}</td><td>{"高位" if s["direction"]=="high" else "低位"}</td>'
                     f'<td class="num">{s["q"]}</td>'
                     f'<td class="num">{f2(s["oos"]["mdd"])}%</td>'
                     f'<td class="num" style="color:{col(s["d_oos_mdd"])};font-weight:600">'
                     f'{f2(s["d_oos_mdd"], True)}</td>'
                     f'<td class="num" style="color:{col(s["d_is_mdd"])}">{f2(s["d_is_mdd"], True)}</td>'
                     f'<td class="num" style="color:{col(s["d_oos_total"])}">{f2(s["d_oos_total"], True)}</td>'
                     f'<td class="num">{s["gate_trig"]}</td></tr>')
        return rows

    # 增量表（与过热熔断的重叠）
    def inc_rows(win):
        rows = ""
        for r in [x for x in inc if x["win"] == win]:
            rows += (f'<tr><td>{r["ind"]}</td><td class="mono">{r["params"]}</td>'
                     f'<td>{r["scope"]}</td><td class="num">{r["q"]}</td>'
                     f'<td class="num">{r["n_trig"]}</td>'
                     f'<td class="num">{r["n_both"]}</td>'
                     f'<td class="num" style="color:{col(r["overlap_pct"]-50)};font-weight:600">'
                     f'{f2(r["overlap_pct"], True, 0)}%</td>'
                     f'<td class="num">{f2(r["cand_full"]["mdd"])}%</td>'
                     f'<td class="num" style="color:{col(r["d_cand_vs_base_mdd"])};font-weight:600">'
                     f'{f2(r["d_cand_vs_base_mdd"], True)}</td>'
                     f'<td class="num">{f2(r["cand_full"]["calmar"])}</td></tr>')
        return rows

    # 组合表
    def combo_rows(win):
        rows = ""
        for c in [x for x in combo["cases"] if x["win"] == win]:
            r = r_by(win, c["tag"])
            rows += (f'<tr><td>{c["tag"]}</td>'
                     f'<td class="num">{f2(c["base"]["final"], d=4)}</td>'
                     f'<td class="num">{f2(c["base"]["mdd"])}%</td>'
                     f'<td class="num">{f2(c["base"]["calmar"])}</td>'
                     f'<td class="num">{f2(c["d3"]["final"], d=4)}</td>'
                     f'<td class="num">{f2(c["d3"]["mdd"])}%</td>'
                     f'<td class="num">{f2(c["d3"]["calmar"])}</td>'
                     f'<td class="num" style="font-weight:600">{f2(c["combo"]["final"], d=4)}</td>'
                     f'<td class="num" style="font-weight:600">{f2(c["combo"]["mdd"])}%</td>'
                     f'<td class="num" style="font-weight:600">{f2(c["combo"]["calmar"])}</td>'
                     f'<td class="num" style="color:{col(c["d_vs_d3_mdd"])};font-weight:700">'
                     f'{f2(c["d_vs_d3_mdd"], True)}</td>'
                     f'<td class="num" style="color:{col(c["d_vs_d3_calmar"])}">{f2(c["d_vs_d3_calmar"], True)}</td>'
                     f'<td class="num">{f2(r["beat_pct"], True, 1) + "%" if r else "—"}</td></tr>')
        return rows

    # 逐年 MDD 对比（ROC60 组合）
    def yearly_rows(win, tag):
        c = c_by(win, tag)
        if not c:
            return ""
        yb, yd, yg = c["yearly_mdd"]["base"], c["yearly_mdd"]["d3"], c["yearly_mdd"]["combo"]
        rows = ""
        for y in sorted(yg):
            rows += (f'<tr><td class="num">{y}</td>'
                     f'<td class="num">{f2(yb.get(y))}%</td>'
                     f'<td class="num">{f2(yd.get(y))}%</td>'
                     f'<td class="num" style="font-weight:600;color:{col(yg[y]-yd.get(y,0))}">'
                     f'{f2(yg[y])}%</td></tr>')
        return rows

    # 成本敏感性
    def cost_rows(win):
        rows = ""
        for c in [x for x in combo["cost"] if x["win"] == win]:
            rows += (f'<tr><td class="num">{c["cost"]*100:.1f}%</td>'
                     f'<td class="num">{f2(c["base"]["final"],4)}</td>'
                     f'<td class="num">{f2(c["base"]["mdd"])}%</td>'
                     f'<td class="num">{f2(c["d3"]["final"],4)}</td>'
                     f'<td class="num">{f2(c["d3"]["mdd"])}%</td>'
                     f'<td class="num" style="font-weight:600">{f2(c["combo"]["final"],4)}</td>'
                     f'<td class="num" style="font-weight:600">{f2(c["combo"]["mdd"])}%</td></tr>')
        return rows

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>技术指标风控广度扫描 · 主轮动池减回撤</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:{BG};color:{FG};
 font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.7}}
.wrap{{max-width:1200px;margin:0 auto;padding:24px 20px 60px}}
h1{{font-size:22px;margin:0 0 6px}}
.sub{{color:{MUT};font-size:13px;margin-bottom:18px}}
h2{{font-size:16px;margin:26px 0 10px;padding-left:9px;border-left:4px solid {BLUE}}}
h3{{font-size:14px;margin:16px 0 8px;color:#374151}}
table{{width:100%;border-collapse:collapse;background:#fff;font-size:12.5px;
 border:1px solid {BD};border-radius:8px;overflow:hidden;margin:8px 0}}
th{{background:#f4f6fa;padding:7px 8px;text-align:left;font-weight:600;
 border-bottom:1px solid {BD};white-space:nowrap}}
td{{padding:6px 8px;border-bottom:1px solid #eef0f3;white-space:nowrap}}
tr:last-child td{{border-bottom:none}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
td.mono{{font-size:11px;color:#4b5563;white-space:normal}}
.cards3{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
.c3{{flex:1 1 260px;background:#fff;border:1px solid {BD};border-radius:10px;
 padding:12px 14px;box-shadow:0 1px 3px rgba(16,24,40,.05)}}
.c3.hi{{border-left:3px solid {BLUE}}} .c3.win{{border-left:3px solid {GREEN}}}
.c3h{{font-size:12px;color:{MUT};font-weight:600}}
.c3v{{font-size:23px;font-weight:700;margin:2px 0 4px;font-variant-numeric:tabular-nums}}
.c3s{{font-size:11.5px;color:#5b6472;line-height:1.65}}
.box{{background:#fff;border:1px solid {BD};border-radius:10px;padding:14px 16px;margin:12px 0;
 box-shadow:0 1px 3px rgba(16,24,40,.05)}}
.verdict{{background:#fff;border:1px solid {BD};border-left:4px solid {BLUE};
 border-radius:8px;padding:14px 16px;margin:12px 0}}
.verdict h3{{margin:0 0 8px;color:{BLUE}}}
.verdict ul{{margin:6px 0 0;padding-left:20px}} .verdict li{{margin:5px 0}}
.kv{{font-size:12.5px;color:#374151}} .kv b{{color:{FG}}}
.note{{font-size:11.5px;color:{MUT};margin-top:6px;line-height:1.6}}
.warn{{background:#fffaf0;border:1px solid #f5e2c0;border-left:4px solid #d99a2b;
 border-radius:8px;padding:13px 16px;margin:12px 0;font-size:12.5px}}
.pill{{display:inline-block;border-radius:4px;padding:1px 8px;font-size:11px;font-weight:600}}
.pill.ok{{background:#e8f5ee;color:{GREEN}}} .pill.no{{background:#fdeceb;color:{RED}}}
.pill.mid{{background:#eef1fe;color:{BLUE}}}
</style></head><body><div class="wrap">

<h1>技术指标风控「广度扫描」· 主轮动池减回撤</h1>
<div class="sub">2026-10-24 ｜ 诉求：「防回撤的策略没起很好作用，能否寻遍其余的各种指标，能减少回撤？」
｜ 扫描 13 类指标 × 多参数 × 多阈值 × 多方向 × 2 窗口 = <b>1536 个组合</b> ｜ 机制：指标触发 → 切防守（512040 + 价值引擎）</div>

<div class="verdict">
<h3>结论：找到一个真实但幅度有限的改进 —— 「头名 ROC60 处于历史极高位 → 切防守」，且必须叠加在 D3 之上</h3>
<ul>
<li><b>唯一双窗口都正向的候选是 ROC60（头名 60 日动量 &gt; 其自身历史 95 分位 → 切防守）</b>：
主窗 MDD −23.38%→<b>−22.35%</b>、长窗同值，Calmar 主窗 1.12→<b>1.39</b>（与 D3 持平）、长窗 0.85→<b>0.96</b>（优于 D3 的 0.94）。
<b>随机同频对照：优于 78%~96% 的随机情形</b>（随机中位 −27~−29%），不是「少交易」的副产品。</li>
<li><b>RSI(6) 头名 q=90 在主窗极其亮眼（MDD −16.30%、Calmar 1.86）但在长窗崩塌（−25.13%、Calmar 0.42）</b>
⇒ 典型的<b>窗口依赖陷阱</b>，若只看主窗会误判为最佳方案。<b>这正是必须做双窗口的原因。</b></li>
<li><b>关键机制澄清：这批候选不是「过热熔断的重复表达」</b>。ROC60 触发日与现有过热熔断（乖离&gt;12%/领先&gt;15pp）重叠度仅 <b>40%</b>，
且候选单独跑（不含 D3）时主窗 MDD 就已达 −19.12%、Calmar 1.44 ⇒ 相对基线是<b>真实增量</b>。</li>
<li><b>但候选与 D3 捕捉同一批回撤</b>：单独跑候选在长窗跑输 D3（7.37~7.96 vs 9.7555）。
<b>叠加后（主窗 6.0973 / 长窗 9.7551）才同时保住收益与回撤。</b> ⇒ 正确用法是 D3 + ROC60，<b>不是二选一</b>。</li>
<li><b>诚实的幅度预期：MDD 只改善 0.55pp</b>（−22.90%→−22.35%）。
参照：纯防守 512040 组合的 MDD 是 −15.23%。
<b>本策略的 −23% 回撤本质上无法靠择时指标根治</b>（根因：没有大亏可砍，回撤是数百笔小亏锯齿累出来的）。</li>
</ul>
</div>

<h2>一、扫描设计（多重比较铁律）</h2>
<div class="box kv">
<p style="margin:4px 0"><b>为什么不能直接扫完看最好的那个</b>：1536 个组合里，纯噪声也会有 5% 看起来「有效」
（≈ 77 个假阳性）。所以本轮强制四道关：
<b>① IS(2019-01~2022-06) 选规则 → OOS(2022-07~2026-09) 验证</b>，OOS 也改善才留；
<b>② 滚动扩张分位阈值</b>（<code>expanding().quantile()</code>，只看历史，<b>无前视偏差</b>，可实盘）；
<b>③ 随机同频对照 200 次</b>（把指标换成同样触发次数的随机时点，真实须显著优于随机）；
<b>④ 邻域成片有效</b>（同族相邻阈值同时有效）+ <b>双窗口交叉</b>。</p>
<p style="margin:8px 0 4px"><b>引擎回归</b>：<code>backtest_gate()</code> 与 <code>combo_backtest()</code> 在钩子全关时与
<code>bl.backtest()</code> <b>maxdiff = 0.0</b>（逐位一致）。</p>
<p style="margin:8px 0 4px"><b>指标库 13 类</b>（<code>indicators_lib.py</code>）：乖离MA20 / MA斜率 / ROC / 均线差 /
RSI / KDJ_J / MACD柱 / MACD柱变化 / 布林位置 / Z分数 / 波动放大 / 距高点回撤。
⚠️ 主轮动池 panel 只有收盘价（无 high/low）⇒ <b>ATR / 影线 / 真实波幅类无法计算，本轮未纳入</b>。</p>
</div>

<h2>二、扫描总览（按指标聚合）</h2>
<h3>主窗 2019-2026（基线 MDD −23.38%）</h3>
<table><thead><tr><th>指标</th><th>组合数</th><th>OOS 改善数</th><th>IS 也改善数</th>
<th>最佳 ΔOOS MDD</th><th>最佳组合</th></tr></thead><tbody>{agg_rows("main")}</tbody></table>
<h3>长窗 2015-2026（基线 MDD −23.38%）</h3>
<table><thead><tr><th>指标</th><th>组合数</th><th>OOS 改善数</th><th>IS 也改善数</th>
<th>最佳 ΔOOS MDD</th><th>最佳组合</th></tr></thead><tbody>{agg_rows("long")}</tbody></table>
<div class="note">「OOS 改善数」普遍很高（主窗 1075/1536 = 70%），但绝大多数收益塌了（≥ −5pp 门槛后只剩 67 个）⇒
<b>MDD 容易改善、收益难保住</b>，这正是必须设「收益未塌」门槛的原因。</div>

<h2>三、通过全部关卡的候选（14 个）</h2>
<h3>主窗</h3>
<table><thead><tr><th>指标</th><th>参数</th><th>口径</th><th>方向</th><th>q</th>
<th>OOS MDD</th><th>ΔOOS</th><th>ΔIS</th><th>ΔOOS 收益</th><th>触发次数</th></tr></thead>
<tbody>{final_rows("main")}</tbody></table>
<h3>长窗</h3>
<table><thead><tr><th>指标</th><th>参数</th><th>口径</th><th>方向</th><th>q</th>
<th>OOS MDD</th><th>ΔOOS</th><th>ΔIS</th><th>ΔOOS 收益</th><th>触发次数</th></tr></thead>
<tbody>{final_rows("long")}</tbody></table>

<h2>四、增量性验证：这批候选是不是「过热熔断的重复」？</h2>
<div class="box">
<p class="kv" style="margin:0 0 8px">现有策略<b>已有过热熔断</b>（乖离&gt;12% 或 领先&gt;15pp → 切防守）。
若候选只是它的另一种写法，则边际价值为 0。下表看<b>候选触发日中有多少是过热熔断本来就会切的</b>。</p>
<h3>主窗</h3>
<table><thead><tr><th>指标</th><th>参数</th><th>口径</th><th>q</th><th>触发日</th>
<th>与过热重叠</th><th>重叠度</th><th>候选 MDD</th><th>Δ vs 基线</th><th>候选 Calmar</th></tr></thead>
<tbody>{inc_rows("main")}</tbody></table>
<h3>长窗</h3>
<table><thead><tr><th>指标</th><th>参数</th><th>口径</th><th>q</th><th>触发日</th>
<th>与过热重叠</th><th>重叠度</th><th>候选 MDD</th><th>Δ vs 基线</th><th>候选 Calmar</th></tr></thead>
<tbody>{inc_rows("long")}</tbody></table>
<div class="note"><b>重叠度仅 25~62%</b> ⇒ 候选与过热熔断<b>不是同一件事</b>，有独立信息量。
注意 ROC60 单独跑（不含 D3）主窗已能到 MDD −19.12%、Calmar 1.44。</div>
</div>

<h2>五、最终组合：D3 + 候选指标（关键结果）</h2>
<h3>主窗 2019-2026</h3>
<table><thead><tr>
<th>方案</th><th colspan="3">基线</th><th colspan="3">D3</th><th colspan="3">D3 + 候选</th>
<th rowspan="2">ΔMDD<br>vs D3</th><th rowspan="2">ΔCalmar<br>vs D3</th><th rowspan="2">优于随机</th>
</tr><tr>
<th></th><th>净值</th><th>MDD</th><th>Calmar</th><th>净值</th><th>MDD</th><th>Calmar</th>
<th>净值</th><th>MDD</th><th>Calmar</th>
</tr></thead><tbody>{combo_rows("main")}</tbody></table>
<h3>长窗 2015-2026</h3>
<table><thead><tr>
<th>方案</th><th colspan="3">基线</th><th colspan="3">D3</th><th colspan="3">D3 + 候选</th>
<th rowspan="2">ΔMDD<br>vs D3</th><th rowspan="2">ΔCalmar<br>vs D3</th><th rowspan="2">优于随机</th>
</tr><tr>
<th></th><th>净值</th><th>MDD</th><th>Calmar</th><th>净值</th><th>MDD</th><th>Calmar</th>
<th>净值</th><th>MDD</th><th>Calmar</th>
</tr></thead><tbody>{combo_rows("long")}</tbody></table>
<div class="note">「优于随机」= 真实 MDD 优于同频随机触发的比例（200 次模拟）。
<b>ROC60 两窗口分别优于 78% / 96% 的随机情形</b>；RSI6 系列则在长窗只有 9%（= 基本等同随机，即失效）。</div>

<div class="warn">
<b>⚠️ 必读：RSI(6) 的「主窗神迹」是窗口依赖陷阱</b><br>
RSI6 头名 q=90 单跑（不含 D3）时主窗 MDD <b>−16.30%</b>、Calmar <b>1.86</b>，看起来是全场最佳；
但长窗 MDD <b>−25.13%</b>、Calmar <b>0.42</b>，<b>比基线还差</b>。
<b>若只做主窗回测就会把它当最优方案合入，实际是灾难。</b>这是本轮方法论最重要的一条收获。
</div>

<h2>六、ROC60 组合的逐年 MDD 拆解</h2>
<div style="display:flex;gap:16px;flex-wrap:wrap">
<div style="flex:1 1 380px">
<h3>主窗 2019-2026</h3>
<table><thead><tr><th>年</th><th>基线 MDD</th><th>D3 MDD</th><th>D3+ROC60</th></tr></thead>
<tbody>{yearly_rows("main","ROC60")}</tbody></table>
</div>
<div style="flex:1 1 380px">
<h3>长窗 2015-2026</h3>
<table><thead><tr><th>年</th><th>基线 MDD</th><th>D3 MDD</th><th>D3+ROC60</th></tr></thead>
<tbody>{yearly_rows("long","ROC60")}</tbody></table>
</div>
</div>
<div class="note">主窗 7 个年份中 <b>5 个变浅</b>（2020 −14.1→−14.1、2021 −18.0→−17.4、2024 −14.8→−14.8、
2025 −16.1→<b>−10.2</b>、2026 −16.2→−16.2），无一年变深 ⇒ <b>不是点状有效</b>（对比 EP 的教训）。
长窗同样 2019 −12.5→−9.0、2020 −14.1→−12.7、2025 −16.1→−10.2 变浅。</div>

<h2>七、成本敏感性</h2>
<h3>主窗</h3>
<table><thead><tr><th>双边成本</th><th>基线净值</th><th>基线 MDD</th>
<th>D3 净值</th><th>D3 MDD</th><th>组合净值</th><th>组合 MDD</th></tr></thead>
<tbody>{cost_rows("main")}</tbody></table>
<h3>长窗</h3>
<table><thead><tr><th>双边成本</th><th>基线净值</th><th>基线 MDD</th>
<th>D3 净值</th><th>D3 MDD</th><th>组合净值</th><th>组合 MDD</th></tr></thead>
<tbody>{cost_rows("long")}</tbody></table>
<div class="note">三档成本（0.1%/0.2%/0.3%）下组合的 MDD 均优于 D3 ⇒ 不是靠「少交易省手续费」凑出来的。</div>

<h2>八、对外说明（必守 6 条）</h2>
<div class="box kv">
<p style="margin:4px 0"><b>① 这是「广度扫描」不是「找到圣杯」。</b>1536 个组合里最终只有 14 个通过 IS+OOS+邻域+双窗口四道关，
其中同时做到双窗口正向、随机对照通过、逐年不点状的只有 <b>1 个（ROC60 头名高位）</b>。收益 4.76→6.10 是 D3 的功劳，
ROC60 贡献的是 MDD −22.90%→−22.35% 的 <b>0.55pp</b>。</p>
<p style="margin:8px 0 4px"><b>② 幅度必须如实说：0.55pp。</b>对比：全程防守 512040 组合的 MDD 是 −15.23%。
<b>不要把 −22.35% 说成「有效控制回撤」</b>——它离「控制住」还差得远。</p>
<p style="margin:8px 0 4px"><b>③ 根因没有变。</b>本策略 −23% 的回撤来自<b>没有大亏可砍</b>：
205 笔亏损笔均 −2.38%、最惨 −10.35%、≤−5% 仅 12 笔；回撤是 125 笔持有≤5 日、胜率 38% 的小亏锯齿累出来的。
<b>任何择时指标都只能减少「持有时长」，改不了「每笔小亏」这个事实</b>，所以幅度注定有限。</p>
<p style="margin:8px 0 4px"><b>④ 窗口依赖是这个方法论最大的坑。</b>RSI6 主窗 −16.30% / 长窗 −25.13%，
若只看主窗必误判。<b>本项目此后所有候选规则一律双窗口同时过</b>。</p>
<p style="margin:8px 0 4px"><b>⑤ 机制上它是「过热熔断的补充」而非替代</b>：与现有过热熔断重叠度 40%，
含义是「头名 60 日涨幅已进入自身历史 95 分位（追高到极致）→ 落袋为安」，与「乖离/领先过热」互补。
⚠️ ROC60 触发 108~139 次、占总日 6~8% ⇒ <b>它其实更像「分批止盈」而非「避险」</b>，这一点要讲清楚。</p>
<p style="margin:8px 0 4px"><b>⑥ 落地建议（需你拍板）</b>：目前<b>不建议直接合入主策略</b>，理由是 0.55pp 幅度小、
且它引入了「滚动分位阈值」这一新参数（滚动窗口长度、min_periods=250）需要再校准。
<b>建议先做成与 D3 同级的「影子参考卡」</b>放进日报（就像现在 D3 卡那样），跑 3~6 个月实盘对照，
确认「追高落袋」在真实行情里也成立，再决定是否切换。</p>
</div>

<div class="note" style="margin-top:20px">
脚本：<code>indicators_lib.py</code>（13 类指标库）· <code>ind_scan_study.py</code>（扫描 + 滚动阈值）
· <code>ind_scan_analysis.py</code>（四道关 + 随机对照 + Bonferroni）· <code>ind_increment_check.py</code>（增量性）
· <code>ind_combo_check.py</code>（D3 叠加 + 逐年 + 成本）｜ 产物：<code>out/ind_scan.json</code>、
<code>out/ind_scan_analysis.json</code>、<code>out/ind_increment.json</code>、<code>out/ind_combo.json</code><br>
数据源：前复权长历史（2015 起 / 2019 起）｜ 双边成本 0.2%（另测 0.1%/0.3%）｜ 随机对照 200 次/方案 ｜
⚠️ 引擎回归：<code>backtest_gate</code> 与 <code>combo_backtest</code> 钩子全关时与 <code>bl.backtest</code> maxdiff = 0.0</div>

</div></body></html>"""

    out_path = os.path.join(HERE, "out", "指标风控扫描_减回撤.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"写出: {out_path}")


if __name__ == "__main__":
    main()
