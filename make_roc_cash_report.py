"""生成「ROC60 空仓版 + 牛马熊择时」研究报告（2026-10-24 晚）"""
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
    J1 = json.load(open(os.path.join(HERE, "out", "roc_cash_zn.json"), encoding="utf-8"))
    J2 = json.load(open(os.path.join(HERE, "out", "roc_cash_verify.json"), encoding="utf-8"))
    J3 = json.load(open(os.path.join(HERE, "out", "roc_cash_final.json"), encoding="utf-8"))

    def c1(win, tag):
        for c in J1["cases"]:
            if c["win"] == win and c["tag"] == tag:
                return c
        return None

    def c3(win, tag):
        for c in J3["cases"]:
            if c["win"] == win and c["tag"] == tag:
                return c
        return None

    def r3(win, tag):
        for r in J3["rnd"] if "rnd" in J3 else []:
            pass
        for r in J2["rnd"]:
            if r["win"] == win and r["N"] == 20:
                return r
        return None

    # ---- 需求一：全动作对比表 ----
    def action_rows(win):
        rows = ""
        for c in [x for x in J1["cases"] if x["win"] == win]:
            best = (c["tag"] == "ROC60→空仓20日")
            style = ' style="background:#f4f8ff;font-weight:600"' if best else ""
            rows += (f'<tr{style}><td>{c["tag"]}</td>'
                     f'<td class="num">{f2(c["combo"]["final"], d=4)}</td>'
                     f'<td class="num">{f2(c["combo"]["mdd"])}%</td>'
                     f'<td class="num">{f2(c["combo"]["calmar"])}</td>'
                     f'<td class="num">{f2(c["d3"]["final"], d=4)}</td>'
                     f'<td class="num">{f2(c["d3"]["mdd"])}%</td>'
                     f'<td class="num">{f2(c["d3"]["calmar"])}</td>'
                     f'<td class="num" style="color:{col(c["d_vs_d3_mdd"])};font-weight:700">'
                     f'{f2(c["d_vs_d3_mdd"], True)}</td>'
                     f'<td class="num" style="color:{col(c["d_vs_d3_final"])}">'
                     f'{f2(c["d_vs_d3_final"], True, 4)}</td>'
                     f'<td class="num">{c["roc_hits"]}</td>'
                     f'<td class="num">{c["cash_days"]}</td></tr>')
        return rows

    # ---- 邻域稳定性 ----
    def nbr_rows(win):
        rows = ""
        for x in [y for y in J2["nbr"] if y["win"] == win]:
            rows += (f'<tr><td class="num">{x["N"]}</td>'
                     f'<td class="num">{f2(x["final"], d=4)}</td>'
                     f'<td class="num">{f2(x["mdd"])}%</td>'
                     f'<td class="num">{f2(x["calmar"])}</td>'
                     f'<td class="num">{f2(x["sharpe"])}</td>'
                     f'<td class="num" style="color:{col(x["d_vs_d3_mdd"])};font-weight:600">'
                     f'{f2(x["d_vs_d3_mdd"], True)}</td>'
                     f'<td class="num" style="color:{col(x["d_vs_d3_final"])}">'
                     f'{f2(x["d_vs_d3_final"], True, 4)}</td></tr>')
        return rows

    # ---- 随机对照 ----
    def rnd_rows(win):
        rows = ""
        for x in [y for y in J2["rnd"] if y["win"] == win]:
            rows += (f'<tr><td class="num">N={x["N"]}</td><td class="num">{x["n_trig"]}</td>'
                     f'<td class="num">{f2(x["real_mdd"])}%</td>'
                     f'<td class="num">{f2(x["rnd_med"])}%</td>'
                     f'<td class="num">{f2(x["rnd_p05"])}%</td>'
                     f'<td class="num" style="color:{col(x["beat_pct"]-50)};font-weight:600">'
                     f'{f2(x["beat_pct"], True, 1)}%</td>'
                     f'<td class="num" style="font-weight:600">{f2(x["real_final"], d=4)}</td>'
                     f'<td class="num">{f2(x["rnd_final_med"], d=4)}</td>'
                     f'<td class="num" style="color:{RED};font-weight:700">'
                     f'{f2(x["beat_final_pct"], True, 1)}%</td></tr>')
        return rows

    # ---- 固定持有对照 ----
    def fix_rows(win):
        rows = ""
        for x in [y for y in J2["fixed"] if y["win"] == win]:
            rows += (f'<tr><td class="num">每 {x["hold"]} 日空仓 {x["cash"]} 日</td>'
                     f'<td class="num">{f2(x["final"], d=4)}</td>'
                     f'<td class="num">{f2(x["mdd"])}%</td>'
                     f'<td class="num">{f2(x["calmar"])}</td>'
                     f'<td class="num">{x["cash_days"]}</td></tr>')
        return rows

    # ---- 逐年 MDD ----
    def yearly_rows(win, tag):
        c = c3(win, tag)
        if not c:
            return ""
        yb, yd, yc = (c["yearly"]["base"], c["yearly"]["d3"], c["yearly"]["combo"])
        rows = ""
        for y in sorted(yc):
            d = yc[y]["mdd"] - yd[y]["mdd"]
            rows += (f'<tr><td class="num">{y}</td>'
                     f'<td class="num">{f2(yb[y]["mdd"])}%</td>'
                     f'<td class="num">{f2(yd[y]["mdd"])}%</td>'
                     f'<td class="num" style="font-weight:600;color:{col(d)}">{f2(yc[y]["mdd"])}%</td>'
                     f'<td class="num">{f2(yb[y]["ret"], True, 1)}%</td>'
                     f'<td class="num">{f2(yd[y]["ret"], True, 1)}%</td>'
                     f'<td class="num" style="font-weight:600;color:{col(yc[y]["ret"]-yd[y]["ret"])}">'
                     f'{f2(yc[y]["ret"], True, 1)}%</td></tr>')
        return rows

    def cost_rows(win, tag):
        rows = ""
        for c in [x for x in J3["cost"] if x["win"] == win and x["tag"] == tag]:
            rows += (f'<tr><td class="num">{c["cost"]*100:.1f}%</td>'
                     f'<td class="num">{f2(c["base"]["final"], d=4)}</td>'
                     f'<td class="num">{f2(c["base"]["mdd"])}%</td>'
                     f'<td class="num">{f2(c["d3"]["final"], d=4)}</td>'
                     f'<td class="num">{f2(c["d3"]["mdd"])}%</td>'
                     f'<td class="num" style="font-weight:700">{f2(c["combo"]["final"], d=4)}</td>'
                     f'<td class="num" style="font-weight:700">{f2(c["combo"]["mdd"])}%</td></tr>')
        return rows

    def attrib_rows(win):
        rows = ""
        for a in [x for x in J3["attrib"] if x["win"] == win]:
            for k, lab in (("fwd5", "后 5 日"), ("fwd10", "后 10 日"), ("fwd20", "后 20 日")):
                v = a[k]
                if v and v.get("n"):
                    rows += (f'<tr><td>{a["tag"]}</td><td>{lab}</td>'
                             f'<td class="num">{v["n"]}</td>'
                             f'<td class="num" style="color:{col(v["mean"])};font-weight:600">'
                             f'{f2(v["mean"], True)}%</td>'
                             f'<td class="num" style="color:{col(v["med"])}">{f2(v["med"], True)}%</td>'
                             f'<td class="num" style="color:{col(v["win"]-50)};font-weight:600">'
                             f'{f2(v["win"], True, 0)}%</td></tr>')
        return rows

    # 需求二失败表
    def zn_rows(win):
        rows = ""
        d = J1["diag"].get(win, {})
        for k, v in d.items():
            rows += (f'<tr><td>{k}</td><td class="num">{f2(v["final"], d=4)}</td>'
                     f'<td class="num">{f2(v["mdd"])}%</td>'
                     f'<td class="num">{f2(v["calmar"])}</td>'
                     f'<td class="num" style="color:{col(v["d_vs_base_mdd"])}">'
                     f'{f2(v["d_vs_base_mdd"], True)}</td>'
                     f'<td class="num" style="color:{col(v["d_vs_base_final"])}">'
                     f'{f2(v["d_vs_base_final"], True, 4)}</td>'
                     f'<td class="num">{v["cash_days"]}</td></tr>')
        return rows

    mc20 = c3("main", "D3+ROC60空仓20日")
    lc20 = c3("long", "D3+ROC60空仓20日")
    mcw = c3("main", "D3+ROC60空仓等机会")
    lcw = c3("long", "D3+ROC60空仓等机会")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ROC60 空仓版 + 指南针系择时研究</title>
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
.bad{{background:#fdf3f2;border:1px solid #f0d5d2;border-left:4px solid {RED};
 border-radius:8px;padding:13px 16px;margin:12px 0;font-size:12.5px}}
</style></head><body><div class="wrap">

<h1>「ROC60 → 空仓」新策略 + 指南针系择时 研究</h1>
<div class="sub">2026-10-24 晚 ｜ 两条诉求：① ROC60 触发改为<b>空仓等下次机会</b>（原为切防守）；
② 用<b>指南针牛马熊 / 活跃市值</b>做择时，震荡下跌时空仓、震荡上行时开仓 ｜ 双窗口（2019 起 / 2015 起）· 含成本 0.2%</div>

<div class="verdict">
<h3>结论：诉求①找到了双赢方案；诉求②（牛马熊/活跃市值择时）全面失败，且失败原因可解释</h3>
<ul>
<li><b>✅ 「D3 + ROC60空仓20日」：主窗 final {f2(mc20['combo']['final'], d=4)} / MDD −16.62% / Calmar 2.14
（D3 是 6.3437 / −22.90% / 1.39）；长窗 {f2(lc20['combo']['final'], d=4)} / −19.22% / 1.19
（D3 是 9.7555 / −22.90% / 0.98）。收益与回撤<b>双窗口同时改善</b>。</b></li>
<li><b>三重验证全部通过</b>：邻域 N=10~30 的 MDD 成片改善（非点状）；随机同频对照下
<b>真实 final 优于随机 99.5%~100% 的情形</b>；「固定持有N日」纯节奏对照 final 仅 1.8~3.5（完全不可行）
⇒ 增益来自<b>指标选对时机</b>，不是「少交易」的副产品。</li>
<li><b>信号归因解释了为什么有效</b>：ROC60 极高位触发后，头名未来 20 日均 <b>−1.41%</b>、
中位 <b>−4.11%</b>、胜率仅 <b>37%</b>（n=139）⇒ 「头名 60 日涨幅冲进自身历史 95 分位」是<b>真实的负向预测信号</b>，
空仓确实躲过了后面的下跌。</li>
<li><b>✅ 按你原话的「等下次机会再入场」版同样成立</b>（无需调 N 参数、更稳健）：
长窗 final {f2(lcw['combo']['final'], d=4)} 与 D3 持平、MDD 改善 <b>3.68pp</b>；主窗 final 略低 0.38、MDD 改善 0.55pp。
逐年 MDD 优于 D3 的年份 <b>5/12</b>。</li>
<li><b>❌ 牛马熊 / 活跃市值择时：两窗口全负</b>。熊&gt;50%→空仓：主窗 MDD −25.60%（比基线 −23.38% <b>更差</b>）、
final 少 1.81；指数下跌区→空仓更是灾难（MDD −30.72%）。</li>
<li><b>失败的根因很明确</b>：牛马熊「熊&gt;50%」时头名后 20 日均 <b>+1.92%</b>、胜率 <b>60%</b>，
比「熊&lt;50%」时（+1.06% / 55%）<b>更好</b>。这与「广度=1 是避险格局」是同一现象第三次复现
⇒ <b>横截面「多数转弱」在这个 ETF 池里是避险信号，不是风险信号</b>。</li>
</ul>
</div>

<h2>一、诉求①：ROC60 触发后的动作对比（12 种）</h2>
<h3>主窗 2019-2026（D3：6.3437 / −22.90% / Calmar 1.39）</h3>
<table><thead><tr><th>方案</th><th>净值</th><th>MDD</th><th>Calmar</th>
<th>D3净值</th><th>D3 MDD</th><th>D3 Calmar</th>
<th>ΔMDD<br>vs D3</th><th>Δ净值<br>vs D3</th><th>触发</th><th>空仓日</th></tr></thead>
<tbody>{action_rows("main")}</tbody></table>
<h3>长窗 2015-2026（D3：9.7555 / −22.90% / Calmar 0.98）</h3>
<table><thead><tr><th>方案</th><th>净值</th><th>MDD</th><th>Calmar</th>
<th>D3净值</th><th>D3 MDD</th><th>D3 Calmar</th>
<th>ΔMDD<br>vs D3</th><th>Δ净值<br>vs D3</th><th>触发</th><th>空仓日</th></tr></thead>
<tbody>{action_rows("long")}</tbody></table>
<div class="note">蓝底行为推荐方案。可以看到：<b>「切防守」版 MDD 只改善 0.55pp；
改成「空仓」后 MDD 改善跃升到 3.68~6.28pp</b> ⇒ 空仓比降 Beta 有效得多（因为躲的是「头名见顶后回落」，
此时买 512040 仍会承担权益 Beta）。</div>

<h2>二、三重验证（防止这是「少交易」的副产品）</h2>
<div class="box">
<h3>① 邻域稳定性：N=5~30 日</h3>
<table><thead><tr><th>空仓 N 日</th><th>净值</th><th>MDD</th><th>Calmar</th><th>夏普</th>
<th>ΔMDD vs D3</th><th>Δ净值 vs D3</th></tr></thead><tbody>{nbr_rows("main")}</tbody></table>
<table><thead><tr><th>空仓 N 日</th><th>净值</th><th>MDD</th><th>Calmar</th><th>夏普</th>
<th>ΔMDD vs D3</th><th>Δ净值 vs D3</th></tr></thead><tbody>{nbr_rows("long")}</tbody></table>
<div class="note">MDD 维度：N≥10 后<b>成片改善</b>（主窗 −18.51%~−16.62%、长窗全部 −19.22%），不是点状。
但<b>净值维度 N=20 是峰值</b>（主窗 N=15 只有 5.29、N=25 为 6.54）⇒ 若追求收益双赢需承认有选参痕迹；
若求稳健应选「等机会再入场」版（无 N 参数）。</div>
</div>

<div class="box">
<h3>② 随机同频对照（200 次，同样触发次数、同样空仓天数）</h3>
<table><thead><tr><th>方案</th><th>触发数</th><th>真实 MDD</th><th>随机中位</th><th>随机 p05</th>
<th>MDD 优于随机</th><th>真实净值</th><th>随机中位</th><th>净值优于随机</th></tr></thead>
<tbody>{rnd_rows("main")}</tbody></table>
<table><thead><tr><th>方案</th><th>触发数</th><th>真实 MDD</th><th>随机中位</th><th>随机 p05</th>
<th>MDD 优于随机</th><th>真实净值</th><th>随机中位</th><th>净值优于随机</th></tr></thead>
<tbody>{rnd_rows("long")}</tbody></table>
<div class="note"><b>关键读法</b>：MDD 维度真实只优于 3%~14% 的随机情形 —— 因为「空仓本身」就能降回撤，随机空仓也能做到。
<b>但净值维度真实优于 99.5%~100% 的随机情形</b>（主窗 7.6436 vs 随机中位 2.1204；长窗 11.1659 vs 2.8479）。
⇒ <b>ROC60 信号的价值在于「该空仓时才空仓」，既降回撤又不牺牲收益</b>。</div>
</div>

<div class="box">
<h3>③ 固定持有 N 日对照（完全不看指标，纯按节奏强制空仓）</h3>
<table><thead><tr><th>节奏</th><th>净值</th><th>MDD</th><th>Calmar</th><th>空仓日</th></tr></thead>
<tbody>{fix_rows("main")}</tbody></table>
<table><thead><tr><th>节奏</th><th>净值</th><th>MDD</th><th>Calmar</th><th>空仓日</th></tr></thead>
<tbody>{fix_rows("long")}</tbody></table>
<div class="note">纯节奏版 final 只有 <b>1.8~3.5</b>（基线 4.76 / 8.40），灾难性。
⇒ 彻底排除「少交易省成本」的解释：<b>增益来自指标信号本身</b>。</div>
</div>

<h2>三、信号归因：为什么 ROC60 极高位是有效信号</h2>
<div class="box">
<table><thead><tr><th>窗口/方案</th><th>窗口</th><th>样本</th><th>均值</th><th>中位</th><th>胜率</th></tr></thead>
<tbody>{attrib_rows("main")}</tbody></table>
<table><thead><tr><th>窗口/方案</th><th>窗口</th><th>样本</th><th>均值</th><th>胜率</th></tr></thead>
<tbody>{attrib_rows("long")}</tbody></table>
<div class="note">「头名 60 日涨幅冲进自身滚动 95 分位」后，头名未来 20 日中位 <b>−4.11%</b>、胜率仅 <b>37%</b>
⇒ 这是<b>动量透支</b>的典型形态。它与此前「过热熔断（乖离&gt;12%/领先&gt;15pp）」是互补关系：
过热熔断看<b>横截面领先幅度</b>，ROC60 分位看<b>自身时间序列位置</b>（已涨了多少），
重叠度仅 40%，两者叠加才有完整覆盖。</div>
</div>

<h2>四、逐年拆解（D3 + ROC60 空仓 20 日）</h2>
<div style="display:flex;gap:16px;flex-wrap:wrap">
<div style="flex:1 1 400px">
<h3>主窗 2019-2026</h3>
<table><thead><tr><th>年</th><th>基线MDD</th><th>D3 MDD</th><th>组合MDD</th>
<th>基线收益</th><th>D3收益</th><th>组合收益</th></tr></thead>
<tbody>{yearly_rows("main","D3+ROC60空仓20日")}</tbody></table>
</div>
<div style="flex:1 1 400px">
<h3>长窗 2015-2026</h3>
<table><thead><tr><th>年</th><th>基线MDD</th><th>D3 MDD</th><th>组合MDD</th>
<th>基线收益</th><th>D3收益</th><th>组合收益</th></tr></thead>
<tbody>{yearly_rows("long","D3+ROC60空仓20日")}</tbody></table>
</div>
</div>
<div class="note">主窗 8 年中 <b>4 年 MDD 优于 D3</b>、长窗 12 年中 4 年（不算多），
但<b>关键年份改善极大</b>：2021 年 MDD −18%→−11%、收益 −5%→<b>+18%</b>（主窗 −9%→+21%）；
2025 年 MDD −16%→<b>−7%</b>、收益 +61%→<b>+72%</b>。
⇒ 这个信号主要在<b>牛市顶部区域</b>发挥作用（2021 年初、2025 年末），符合其机制。</div>

<h2>五、成本敏感性</h2>
<div style="display:flex;gap:16px;flex-wrap:wrap">
<div style="flex:1 1 400px">
<h3>主窗 · D3+ROC60空仓20日</h3>
<table><thead><tr><th>成本</th><th>基线净值</th><th>基线MDD</th><th>D3净值</th><th>D3 MDD</th>
<th>组合净值</th><th>组合MDD</th></tr></thead>
<tbody>{cost_rows("main","D3+ROC60空仓20日")}</tbody></table>
</div>
<div style="flex:1 1 400px">
<h3>长窗 · D3+ROC60空仓20日</h3>
<table><thead><tr><th>成本</th><th>基线净值</th><th>基线MDD</th><th>D3净值</th><th>D3 MDD</th>
<th>组合净值</th><th>组合MDD</th></tr></thead>
<tbody>{cost_rows("long","D3+ROC60空仓20日")}</tbody></table>
</div>
</div>
<div class="note">三档成本（0.1%/0.2%/0.3%）下组合均优于 D3，且 0.1% 成本下优势最大
（主窗 9.3668/−15.59%）⇒ <b>不是靠手续费省出来的</b>。</div>

<h2>六、诉求②：牛马熊 / 活跃市值择时 —— 全面失败</h2>
<div class="bad">
<b>⚠️ 先说数据限制（这决定了结论的外推边界）</b><br>
长历史 CSV <b>只有 open/high/low/close，没有成交量/成交额</b>：<br>
· <b>牛马熊</b>：原公式 <code>nx = 1 if EMA(C,3) > max(EMA(C,13),EMA(C,55))</code> 只用收盘价，可<b>忠实降级实现</b>；
但 <code>v>1</code> 放量过滤因无成交量而降级（nx=0 仍算马股，分类仍完备）。<br>
· <b>0AMV 活跃市值</b>：核心是成交额 ⇒ <b>无法计算原版</b>。本轮用「池内 ETF 等权指数 + 5/13 均线位置」
作为<b>机制等价物</b>（0AMV 官方用法正是「0AMV 与 5/13 日成本均线的位置关系」划四区）。<br>
· ⚠️ 原版牛马熊是对<b>全市场 5000 只 A 股</b>做横截面统计，我们只有 <b>24 只 ETF</b>。
样本量差距巨大；若你想用真实的全 A 口径，需要另外引入个股行情数据源。
</div>

<h3>择时开关回测（两窗口）</h3>
<table><thead><tr><th>择时规则</th><th>净值</th><th>MDD</th><th>Calmar</th>
<th>ΔMDD vs 基线</th><th>Δ净值 vs 基线</th><th>空仓日</th></tr></thead>
<tbody>{zn_rows("main")}</tbody></table>
<table><thead><tr><th>择时规则</th><th>净值</th><th>MDD</th><th>Calmar</th>
<th>ΔMDD vs 基线</th><th>Δ净值 vs 基线</th><th>空仓日</th></tr></thead>
<tbody>{zn_rows("long")}</tbody></table>

<div class="box">
<h3>失败的根因：横截面「多数转弱」在 ETF 池里是避险信号，不是风险信号</h3>
<table><thead><tr><th>牛马熊状态</th><th>样本</th><th>头名后 20 日均</th><th>胜率</th></tr></thead><tbody>
<tr><td>牛&gt;50%</td><td class="num">420 / 427</td><td class="num">+1.92% / +1.85%</td><td class="num">57% / 56%</td></tr>
<tr><td>牛 30~50%</td><td class="num">490 / 939</td><td class="num">+0.52% / +0.59%</td><td class="num">53% / 54%</td></tr>
<tr><td>牛&lt;30%</td><td class="num">703 / 1445</td><td class="num">+1.24% / +1.24%</td><td class="num">56% / 58%</td></tr>
<tr><td style="color:{RED};font-weight:700">熊&gt;50%</td><td class="num">253 / 253</td>
<td class="num" style="color:{RED};font-weight:700">+1.92% / +1.92%</td>
<td class="num" style="color:{RED};font-weight:700">60% / 60%</td></tr>
<tr><td>熊&lt;50%</td><td class="num">1360 / 2558</td><td class="num">+1.06% / +1.04%</td><td class="num">55% / 56%</td></tr>
</tbody></table>
<div class="note">格式：主窗 / 长窗。<b>熊&gt;50% 时头名后 20 日反而比熊&lt;50% 时更强</b>（+1.92% vs +1.06%，胜率 60% vs 55%）。
原因：池内多数 ETF 走弱时，剩下的强势品种是<b>黄金/纳指等避险资产</b>，
而它们恰是这类时段的最优选择 ⇒ <b>空仓反而错过</b>。<br>
<b>这是同一现象第三次复现</b>：①「广度=1 是避险格局」（广度止损全负）；
②「广度闸门（D3 后不入场）」证伪；③ 本次牛马熊择时失败。
⇒ <b>在这个 ETF 池里，横截面转弱不能用作风险信号</b>，建议今后不再重复尝试同源思路。</div>
</div>

<div class="warn">
<b>⚠️ 为什么「指数下跌区→空仓」最灾难</b>（主窗 MDD −30.72%、final 1.6795）：<br>
等权指数跌破 5/13 日均线时，池内 ETF 已普遍走弱，此时空仓会错过随后的反弹；
而策略的轮动机制本身已在「择强」，等权指数是<b>滞后且重复</b>的信息
——它把「轮动已经避开的弱势」又算了一遍风险，造成双重惩罚。
<b>这与本项目「温度计池必须与排名池同源」「环境温度择时已测为负」的教训一致。</b>
</div>

<h2>七、对外说明（必守 5 条）</h2>
<div class="box kv">
<p style="margin:4px 0"><b>① 诉求①是真突破，但要分清两个版本。</b>
「空仓等机会」（你的原话、无 N 参数）：长窗收益与 D3 持平、MDD 改善 3.68pp，逐年 5/12 年更优；
「空仓 20 日」：收益回撤双赢，但<b>N=20 是净值峰值</b>（N=15/25 都回落），有选参痕迹。
<b>建议优先用「等机会再入场」版</b>，它无需调参、机制更自洽。</p>
<p style="margin:8px 0 4px"><b>② 幅度要说实话。</b>「等机会」版 MDD 从 −22.90% 到 −19.22%（长窗），
仍是 <b>−19% 级别</b>，离「控制住」很远（全程防守 512040 是 −15.23%）。
不要把它宣传成回撤解决方案。</p>
<p style="margin:8px 0 4px"><b>③ 有效性有清晰机制解释，不是数据挖掘。</b>ROC60 极高位后头名 20 日中位 −4.11%、胜率 37%
⇒ 动量透支。这与「过热熔断」互补（一个看横截面领先、一个看自身时间序列位置），重叠度仅 40%。</p>
<p style="margin:8px 0 4px"><b>④ 牛马熊/活跃市值这条路已走到头，不建议再投入。</b>同一现象三次复现
（广度止损、广度闸门、牛马熊择时），根因是该 ETF 池的横截面转弱=避险格局。
若坚持要用全 A 口径的牛马熊，需要额外引入个股行情数据源，且预期不会改变结论。</p>
<p style="margin:8px 0 4px"><b>⑤ 落地建议（待你拍板）</b>：诉求①方案建议先做成<b>影子参考卡</b>进日报
（与 D3 卡并列），跑 3~6 个月实盘对照；牛马熊方案<b>不合入、不再迭代</b>。</p>
</div>

<div class="note" style="margin-top:20px">
脚本：<code>roc_cash_zn_study.py</code>（引擎+动作网格+牛马熊）、<code>roc_cash_verify.py</code>（邻域+随机+固定节奏三重验证）、
<code>roc_cash_final.py</code>（逐年+成本+信号归因）｜ 产物：<code>out/roc_cash_zn.json</code>、
<code>out/roc_cash_verify.json</code>、<code>out/roc_cash_final.json</code><br>
数据源：前复权长历史（2015 起 / 2019 起，仅 OHLC 四价）｜ 双边成本 0.2%（另测 0.1%/0.3%）｜
随机对照 200 次 ｜ 引擎回归：<code>combo_backtest()</code> 钩子全关时与 <code>bl.backtest()</code> <b>maxdiff = 0.0</b></div>

</div></body></html>"""

    out_path = os.path.join(HERE, "out", "ROC60空仓版_牛马熊择时.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"写出: {out_path}")


if __name__ == "__main__":
    main()
