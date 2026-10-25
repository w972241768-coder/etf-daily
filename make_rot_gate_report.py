"""生成「D3 止损后广度闸门」研究报告 HTML（2026-10-24）"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BG, FG, MUT, BD = "#eef0f3", "#202020", "#6c717f", "#e2e5ea"
BLUE, RED, GREEN, PURPLE = "#3d57e5", "#d4380d", "#0f9d58", "#7b3fe4"


def fmt(v, d=2, sign=False):
    if v is None:
        return "—"
    s = f"{v:+.{d}f}" if sign else f"{v:.{d}f}"
    return s


def col(v):
    if v is None:
        return MUT
    return RED if v > 0 else (GREEN if v < 0 else FG)


def main():
    j = json.load(open(os.path.join(HERE, "out", "rot_gate.json"), encoding="utf-8"))
    at = json.load(open(os.path.join(HERE, "out", "gate_attrib.json"), encoding="utf-8"))
    out_path = os.path.join(HERE, "out", "止损对比_D3止损后广度闸门.html")

    grid = j["grid"]
    rnd = {r["win"]: r for r in j["random"]}
    att = at

    def gw(w):
        return [g for g in grid if g["win"] == w]

    def case(w):
        return [c for c in j["cases"] if c["win"] == w][0]

    # ---- 网格表（主窗 + 长窗各挑代表性的）----
    def grid_rows(w):
        rows = []
        # 全部组合按 thr/rec 分组展示 max_days 的影响
        seen = []
        for g in gw(w):
            key = (g["thr"], g["rec"], g["max_days"])
            rows.append(f"""<tr>
<td class="num">{g['thr']}</td><td class="num">{g['rec']}</td>
<td class="num">{g['max_days'] or '∞'}</td>
<td class="num">{fmt(g['final'],4)}</td>
<td class="num" style="color:{col(g['vs_d3_final'])};font-weight:600">{fmt(g['vs_d3_final'],4,True)}</td>
<td class="num">{fmt(g['mdd'],2)}%</td>
<td class="num" style="color:{col(g['vs_d3_mdd'])};font-weight:600">{fmt(g['vs_d3_mdd'],2,True)}</td>
<td class="num">{fmt(g['calmar'],2)}</td>
<td class="num">{g['gate_days']}</td>
<td class="num">{g['trades']}</td></tr>""")
        return "".join(rows)

    # ---- 归因表 ----
    def attr_rows(w):
        d = att.get(w)
        if not d:
            return ""
        r = d["reentry"]
        rows = ""
        for lab, k in (("广度差 n_pos≤1", "lo"), ("广度好 n_pos≥2", "hi"), ("全部", "all")):
            for hk, hn in (("f5", "5 日"), ("f10", "10 日"), ("f20", "20 日")):
                v = r.get(f"{k}_{hk}") or "n/a"
                rows += f'<tr><td>{lab}</td><td>{hn}</td><td class="mono">{v}</td></tr>'
        return rows

    def cut_rows(w):
        d = att.get(w)
        if not d:
            return ""
        c = d["cut"]
        return (f'<tr><td>广度差 n_pos≤1</td><td class="mono">{c["lo_f5"]}</td></tr>'
                f'<tr><td>广度好 n_pos≥2</td><td class="mono">{c["hi_f5"]}</td></tr>')

    def breadth_rows(w):
        d = att.get(w)
        if not d:
            return ""
        dist = d["breadth_dist"]
        s = "".join(f'<span class="bchip">n_pos={k}：{v}次</span>' for k, v in sorted(dist.items()))
        return f'<div class="chips">{s}</div>'

    main_c, long_c = case("main"), case("long")
    A, D = main_c["A"], main_c["D3"]
    AL, DL = long_c["A"], long_c["D3"]
    m_best = max(gw("main"), key=lambda g: g["final"])
    l_best = max(gw("long"), key=lambda g: g["final"])

    def stat_cards(w):
        c = case(w)
        A, D = c["A"], c["D3"]
        g = max(gw(w), key=lambda x: x["final"])
        return f"""<div class="cards3">
<div class="c3"><div class="c3h">A 基线（现用）</div>
<div class="c3v">{fmt(A['final'],4)}</div>
<div class="c3s">总收益 {fmt(A['total'],2,True)}% ｜ MDD {fmt(A['mdd'],2)}%<br>
夏普 {fmt(A['sharpe'],2)} ｜ Calmar {fmt(A['calmar'],2)} ｜ {A['trades']} 笔</div></div>
<div class="c3 hi"><div class="c3h">D3 单笔止损（无闸门）</div>
<div class="c3v">{fmt(D['final'],4)}</div>
<div class="c3s">总收益 {fmt(D['total'],2,True)}% ｜ MDD {fmt(D['mdd'],2)}%<br>
夏普 {fmt(D['sharpe'],2)} ｜ Calmar {fmt(D['calmar'],2)} ｜ {D['trades']} 笔</div></div>
<div class="c3 bad"><div class="c3h">D3 + 广度闸门（最优组合）</div>
<div class="c3v">{fmt(g['final'],4)}</div>
<div class="c3s">总收益 {fmt(g['total'],2,True)}% ｜ MDD {fmt(g['mdd'],2)}%<br>
夏普 {fmt(g['sharpe'],2)} ｜ Calmar {fmt(g['calmar'],2)} ｜ 闸门 {g['gate_days']} 天</div></div>
</div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>D3 止损后广度闸门 · 回测研究</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:{BG};color:{FG};
 font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.7}}
.wrap{{max-width:1180px;margin:0 auto;padding:24px 20px 60px}}
h1{{font-size:22px;margin:0 0 6px}}
.sub{{color:{MUT};font-size:13px;margin-bottom:18px}}
h2{{font-size:16px;margin:26px 0 10px;padding-left:9px;
 border-left:4px solid {BLUE}}}
h3{{font-size:14px;margin:16px 0 8px;color:#374151}}
table{{width:100%;border-collapse:collapse;background:#fff;font-size:12.5px;
 border:1px solid {BD};border-radius:8px;overflow:hidden}}
th{{background:#f4f6fa;padding:7px 8px;text-align:left;font-weight:600;
 border-bottom:1px solid {BD};white-space:nowrap}}
td{{padding:6px 8px;border-bottom:1px solid #eef0f3;white-space:nowrap}}
tr:last-child td{{border-bottom:none}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
td.mono{{font-size:11.5px;color:#4b5563;white-space:normal}}
.cards3{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
.c3{{flex:1 1 250px;background:#fff;border:1px solid {BD};border-radius:10px;
 padding:12px 14px;box-shadow:0 1px 3px rgba(16,24,40,.05)}}
.c3.hi{{border-left:3px solid {BLUE}}}
.c3.bad{{border-left:3px solid {GREEN}}}
.c3h{{font-size:12px;color:{MUT};font-weight:600}}
.c3v{{font-size:24px;font-weight:700;margin:2px 0 4px;font-variant-numeric:tabular-nums}}
.c3s{{font-size:11.5px;color:#5b6472;line-height:1.65}}
.box{{background:#fff;border:1px solid {BD};border-radius:10px;padding:14px 16px;margin:12px 0;
 box-shadow:0 1px 3px rgba(16,24,40,.05)}}
.chips{{display:flex;flex-wrap:wrap;gap:6px}}
.bchip{{display:inline-block;background:#f0f2f5;border:1px solid {BD};border-radius:20px;
 padding:2px 10px;font-size:11.5px;color:#4b5563}}
.verdict{{background:#fff;border:1px solid {BD};border-left:4px solid {GREEN};
 border-radius:8px;padding:14px 16px;margin:12px 0}}
.verdict h3{{margin:0 0 8px;color:{GREEN}}}
.verdict ul{{margin:6px 0 0;padding-left:20px}}
.verdict li{{margin:4px 0}}
.kv{{font-size:12.5px;color:#374151}}
.kv b{{color:{FG}}}
.note{{font-size:12px;color:{MUT};margin-top:6px}}
.chip{{display:inline-block;background:#f0f2f5;border-radius:4px;padding:1px 7px;
 font-size:11.5px;color:#4b5563;margin-right:6px}}
</style></head><body><div class="wrap">

<h1>「D3 止损后广度闸门」回测研究</h1>
<div class="sub">2026-10-24 ｜ 设想：池内动横20 只有 1 只 &gt; 0 时持续空仓，<b>前提是刚做完止损并空仓 5 天后</b>
｜ 目标：解决震荡市/阴跌中「持续买入亏损」的问题 ｜ 两窗口：2019-12-31 起（主窗）/ 2015 起（长窗）</div>

<div class="verdict">
<h3>结论：❌ 证伪，不予采用</h3>
<ul>
<li><b>96 组参数（2 窗口 × 48 组合）中，收益优于 D3 的组数 = 0</b>（主窗 0/48、长窗 0/48）；Calmar 优于 D3 的组数同样 = 0。</li>
<li>MDD 最多只改善 <b>0.43pp</b>（主窗 thr=1/rec=3），代价是总收益从 4.7579 掉到 4.9011 级别（−1.44 净值）、Calmar 从 1.39 掉到 1.06——<b>用收益换回撤，且换得极不划算</b>。</li>
<li><b>用户的核心假设被事件级数据直接否证</b>：D3 止损后重新入场，整体是<b>赚的</b>（主窗 21 次，10 日均 +2.59%、胜率 62%；长窗 32 次，+2.04%、胜率 69%），并不存在「止损后持续买入亏损」。</li>
<li>更关键：<b>「广度差」组反而比「广度好」组更安全</b>。广度 ≤1 时重新入场的头名 5 日胜率 <b>86%/85%</b>（主/长窗），而广度 ≥2 组只有 43%/47%。</li>
<li>全样本验证：广度 ≤1 时头名未来 20 日胜率 <b>67%/64%</b>、期内最深浮亏仅 <b>−2.32%/−2.54%</b>；广度 ≥2 时胜率 54%、浮亏 <b>−4.65%/−4.18%</b>。
⇒ <b>广度=1 不是「震荡阴跌」，而是避险格局</b>（头名集中为黄金 518880 / 纳指 513100）。闸门把最安全的时段挡在门外。</li>
<li>被止损的标的在止损后 5 日反而上涨（广度差组 +2.83%）——<b>D3 本身已略有「砍在低点」倾向</b>，再叠加闸门会二次错过。</li>
</ul>
</div>

<h2>一、机制定义（与已证伪的「广度止损」本质不同）</h2>
<div class="box kv">
<p style="margin:4px 0"><b>本方案（三步，闸门只在止损后生效）</b><br>
① D3 触发：任何一笔换出时相对建仓价亏损 ≥ <b>3%</b> → 空仓 <b>5</b> 个交易日（闸门<b>上膛</b>）<br>
② 冷却期满那一天起，若池内动横20 &gt; 0 的只数 ≤ 阈值 → <b>继续持币，不重新入场</b><br>
③ 直到广度恢复到 gate_recover 才解除闸门、重新买入头名</p>
<p style="margin:8px 0 4px"><b>与旧「广度止损」的区别（关键）</b><br>
旧方案 = 广度差就<b>立刻切走已有持仓</b>（砍在上涨途中）→ 24 组全负；<br>
本方案 = 广度差就<b>不入场</b>（本来就没仓位，不存在砍）→ 机制完全不同，本次独立验证。</p>
</div>

<h2>二、基线对比（两窗口）</h2>
{stat_cards("main")}
<div class="note">↑ 主窗 2019-12-31 ~ 2026-09-24（双边 0.2% 成本）</div>
{stat_cards("long")}
<div class="note">↑ 长窗 2015-01-05 ~ 2026-09-24（双边 0.2% 成本）</div>

<h2>三、参数网格（96 组全跑）</h2>
<h3>主窗 2019-2026（D3 = 4.7579 → 6.3437，MDD −23.38% → −22.90%）</h3>
<table><thead><tr>
<th>广度阈值<br>≤thr 持币</th><th>恢复阈值<br>≥rec 解闸</th><th>闸门最长<br>天数</th>
<th>净值</th><th>vs D3</th><th>MDD</th><th>MDD vs D3</th><th>Calmar</th>
<th>闸门<br>天数</th><th>总笔数</th>
</tr></thead><tbody>{grid_rows("main")}</tbody></table>
<div class="note">红色 = 更好，绿色 = 更差。</div>

<h3>长窗 2015-2026（D3 = 8.3997 → 9.7555，MDD −23.38% → −22.90%）</h3>
<table><thead><tr>
<th>广度阈值</th><th>恢复阈值</th><th>闸门最长</th>
<th>净值</th><th>vs D3</th><th>MDD</th><th>MDD vs D3</th><th>Calmar</th>
<th>闸门天数</th><th>总笔数</th>
</tr></thead><tbody>{grid_rows("long")}</tbody></table>

<h2>四、为什么失败：事件级归因（直接检验用户的核心假设）</h2>
<div class="box">
<h3>D3 触发后的重新入场，收益如何？</h3>
<table><thead><tr><th>分组</th><th>窗口</th><th>重新入场后头名表现（n / 均 / 中位 / 胜率）</th></tr></thead>
<tbody>{attr_rows("main")}</tbody></table>
<div class="note">↑ 主窗（2019-2026），D3 共触发 21 次</div>
<table><thead><tr><th>分组</th><th>窗口</th><th>重新入场后头名表现</th></tr></thead>
<tbody>{attr_rows("long")}</tbody></table>
<div class="note">↑ 长窗（2015-2026），D3 共触发 32 次</div>
</div>

<div class="box">
<h3>被止损的那只标的，在止损之后又跌了还是涨了？</h3>
<table><thead><tr><th>分组</th><th>止损后 5 日表现</th></tr></thead>
<tbody>{cut_rows("main")}</tbody></table>
<div class="note">↑ 主窗：广度差组 +2.83% / 广度好组 −0.49% ⇒ D3 在广度差时砍在低点。</div>
<table><thead><tr><th>分组</th><th>止损后 5 日表现</th></tr></thead>
<tbody>{cut_rows("long")}</tbody></table>
<div class="note">↑ 长窗：广度差组 +0.64% / 广度好组 −0.84%。</div>
</div>

<div class="box">
<h3>触发时点的广度分布</h3>
<h3>主窗</h3>{breadth_rows("main")}
<h3 style="margin-top:10px">长窗</h3>{breadth_rows("long")}
</div>

<div class="box">
<h3>决定性证据：广度与「头名未来 20 日」的关系（全样本，非仅止损后）</h3>
<table><thead><tr><th>分组</th><th>样本</th><th>头名后 20 日均收益</th><th>胜率</th><th>期内最深浮亏</th><th>典型头名</th></tr></thead><tbody>
<tr><td>广度差 n_pos ≤ 1</td><td class="num">223 / 571</td><td class="num" style="color:{RED};font-weight:600">+1.57% / +1.44%</td>
<td class="num" style="color:{RED};font-weight:600">67% / 64%</td>
<td class="num" style="color:{GREEN};font-weight:600">−2.32% / −2.54%</td><td>黄金 518880、纳指 513100</td></tr>
<tr><td>广度好 n_pos ≥ 2</td><td class="num">1390 / 2240</td><td class="num">+1.14% / +1.04%</td>
<td class="num">54%</td>
<td class="num" style="color:{RED};font-weight:600">−4.65% / −4.18%</td><td>纳指 513100、沪深300 510300</td></tr>
</tbody></table>
<div class="note">格式：主窗 / 长窗。<b>广度差时风险反而更低、收益更高</b>——因为此时只有强势品种还能排在前面，池内其余全是弱势；这类时段是<b>避险格局</b>，不是震荡阴跌。</div>
</div>

<h2>五、随机同频对照（增益是否只是「少交易」的副产品）</h2>
<div class="box">
<table><thead><tr><th>窗口</th><th>随机次数</th><th>随机中位</th><th>随机 p95</th>
<th>D3 原策略</th><th>D3 分位</th><th>闸门最佳</th><th>闸门分位</th></tr></thead><tbody>
<tr><td>主窗 2019-2026</td><td class="num">80</td><td class="num">{fmt(rnd['main']['med'],4)}</td>
<td class="num">{fmt(rnd['main']['p95'],4)}</td>
<td class="num" style="font-weight:600">{fmt(D['final'],4)}</td>
<td class="num" style="color:{RED};font-weight:700">{fmt(rnd['main']['d3_pctile'],1)}%</td>
<td class="num">{fmt(rnd['main']['gate_best']['final'],4)}（thr={rnd['main']['gate_best']['thr']}）</td>
<td class="num" style="color:{GREEN};font-weight:700">{fmt(rnd['main']['gate_best']['pctile'],1)}%</td></tr>
<tr><td>长窗 2015-2026</td><td class="num">80</td><td class="num">{fmt(rnd['long']['med'],4)}</td>
<td class="num">{fmt(rnd['long']['p95'],4)}</td>
<td class="num" style="font-weight:600">{fmt(DL['final'],4)}</td>
<td class="num" style="color:{RED};font-weight:700">{fmt(rnd['long']['d3_pctile'],1)}%</td>
<td class="num">{fmt(rnd['long']['gate_best']['final'],4)}（thr={rnd['long']['gate_best']['thr']}）</td>
<td class="num" style="color:{GREEN};font-weight:700">{fmt(rnd['long']['gate_best']['pctile'],1)}%</td></tr>
</tbody></table>
<div class="note">随机口径与既有研究一致：p = 真实触发次数 / 轮动持有日数，80 次模拟。
<b>D3 本身是真信号（97.5% / 98.8% 分位）；闸门最佳组合只有 96.2% / 95.0% 分位，且绝对净值低于 D3。</b></div>
</div>

<h2>六、对外说明（必须同时讲清的四点）</h2>
<div class="box kv">
<p style="margin:4px 0"><b>① 这不是「原方案的失败」，而是一个新假设的独立检验。</b>
本方案的机制（只在止损后生效、不砍已有仓位）与此前被证伪的「广度止损」完全不同，代码独立实现、96 组参数全跑、两个窗口、含成本、随机对照、事件级归因，六项都做了，仍然全负。</p>
<p style="margin:8px 0 4px"><b>② 失败的根本原因：广度=1 是避险信号，不是风险信号。</b>
这是本项目此前已发现并记录的现象（「广度=1 时头名 75% 为黄金/纳指，头名未来 20 日最大回撤 −2.57%，比全样本 −4.31% 还浅，胜率 63.3% vs 55.4%」）。本次用「不入场」的新形式复验，结论完全一致：<b>闸门挡住的正是最安全、最该进场的时段</b>。</p>
<p style="margin:8px 0 4px"><b>③ 用户的核心假设「止损后持续买入亏损」不成立。</b>
止损后重新入场的 10 日期望是 <b>+2.59% / +2.04%</b>（胜率 62% / 69%），不是亏损。
真正的问题从来不是「止损后买入会亏」，而是<b>止损前那笔亏损本身</b>——而那已经被 D3 处理掉了（4.7579 → 6.3437）。</p>
<p style="margin:8px 0 4px"><b>④ 为什么 MDD 看着能改善一点？那是「躲掉了上涨」不是「躲掉了下跌」。</b>
少数组合 MDD 浅 0.43pp，代价是净值少 1.44、Calmar 从 1.39 掉到 1.06——这不是风险控制的胜利，是<b>用未来的收益换过去的回撤</b>，属于典型的「削峰填谷」式伪改进。</p>
<p style="margin:8px 0 4px"><b>⑤ 结论保持不变：主策略继续用现方案，D3 作为「想切时直接切」的参考卡留在日报里。</b>
本闸门不合并、不上线。</p>
</div>

<div class="note" style="margin-top:20px">
数据源：前复权长历史（2015 起 / 2019 起两套 CSV）｜ 双边换仓成本 0.2% ｜ 随机对照 80 次/窗口 ｜
脚本：<code>rot_gate_study.py</code>（网格+随机）、<code>rot_gate_attrib.py</code>（事件归因）、
<code>rot_stop_study.py</code>（引擎新增 gate_breadth/gate_recover/gate_max_days 钩子，默认关闭，回归 maxdiff 0.0）</div>

</div></body></html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"写出: {out_path}")


if __name__ == "__main__":
    main()
