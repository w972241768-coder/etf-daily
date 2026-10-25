"""生成「新 ETF 池子」三策略对比报告（2026-10-24）"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BG, FG, MUT, BD = "#eef0f3", "#202020", "#6c717f", "#e2e5ea"
BLUE, RED, GREEN = "#3d57e5", "#d4380d", "#0f9d58"
NAMES = {"515880": "通信ETF国泰", "159516": "半导体设备ETF国泰",
         "588200": "科创芯片ETF嘉实", "159530": "机器人ETF易方达",
         "513100": "纳指ETF", "512040": "价值ETF富国"}


def f2(v, d=2, sign=False):
    if v is None:
        return "—"
    return f"{v:+.{d}f}" if sign else f"{v:.{d}f}"


def col(v):
    if v is None:
        return MUT
    return RED if v > 0 else (GREEN if v < 0 else FG)


def main():
    j = json.load(open(os.path.join(HERE, "out", "new_pool_compare.json"), encoding="utf-8"))
    cases = j["cases"]
    # 主选池同口径对照（从 watch_pool_compare.json 取主窗）
    wj = json.load(open(os.path.join(HERE, "out", "watch_pool_compare.json"),
                        encoding="utf-8"))
    main_pool = {c["scheme"]: c for c in wj["cases"]
                 if c["pool"] == "主选池 9 只" and c["win"] == "主窗"}

    def get(win, scheme):
        for c in cases:
            if c["win"] == win and c["scheme"] == scheme:
                return c
        return None

    def head_rows(win):
        rows = ""
        for s in ("基线", "D3", "P3"):
            c = get(win, s)
            if not c:
                continue
            mp = main_pool.get(s)
            rows += (f'<tr><td><b>{s}</b></td>'
                     f'<td class="num">{f2(c["final"], 4)}</td>'
                     f'<td class="num" style="color:{col(c["total"])};font-weight:600">'
                     f'{f2(c["total"], 2, True)}%</td>'
                     f'<td class="num" style="color:{col(c["mdd"])};font-weight:700">{f2(c["mdd"])}%</td>'
                     f'<td class="num">{f2(c["calmar"])}</td>'
                     f'<td class="num">{f2(c["sharpe"])}</td>'
                     f'<td class="num">{c["trades"]}</td>'
                     f'<td class="num">{c["cash_days"]}</td>'
                     + (f'<td class="num" style="color:#8a8f99">{f2(mp["final"], 4)}</td>'
                        f'<td class="num" style="color:#8a8f99">{f2(mp["mdd"])}%</td>'
                        f'<td class="num" style="color:#8a8f99">{f2(mp["calmar"])}</td>'
                        if mp else "") + "</tr>")
        return rows

    def year_rows(win):
        b, d, p = get(win, "基线"), get(win, "D3"), get(win, "P3")
        if not b:
            return ""
        rows = ""
        for y in sorted(b["yearly"].keys()):
            yb, yd, yp = b["yearly"][y], d["yearly"].get(y, {}), p["yearly"].get(y, {})
            flag = ""
            if yp.get("ret", 0) < yd.get("ret", 0) - 20:
                flag = ' <span style="color:#b3351a;font-weight:700">← P3 大幅跑输</span>'
            rows += (f'<tr><td class="num">{y}</td>'
                     f'<td class="num">{f2(yb["ret"], 1, True)}%</td>'
                     f'<td class="num" style="color:{col(yb["mdd"])}">{f2(yb["mdd"], 1)}%</td>'
                     f'<td class="num">{f2(yd.get("ret", 0), 1, True)}%</td>'
                     f'<td class="num" style="color:{col(yd.get("mdd", 0))}">{f2(yd.get("mdd", 0), 1)}%</td>'
                     f'<td class="num">{f2(yp.get("ret", 0), 1, True)}%</td>'
                     f'<td class="num" style="color:{col(yp.get("mdd", 0))}">{f2(yp.get("mdd", 0), 1)}%</td>'
                     f'<td>{flag}</td></tr>')
        return rows

    def avail_rows(win):
        for a in j["avail"]:
            if a["win"] == win:
                return "  ".join(f'<span class="chip">{y}: {n} 只</span>'
                                 for y, n in a["avail"].items())
        return ""

    mw = get("主窗(全齐)", "P3")
    lw = get("长窗(动态池)", "P3")
    mb = get("主窗(全齐)", "基线")
    lb = get("长窗(动态池)", "基线")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>新 ETF 池子 · 三策略对比</title>
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
.chip{{display:inline-block;background:#f0f2f5;border:1px solid {BD};border-radius:20px;
 padding:2px 10px;font-size:11px;color:#4b5563;margin:0 4px 4px 0}}
.warn{{background:#fffaf0;border:1px solid #f5e2c0;border-left:4px solid #d99a2b;
 border-radius:8px;padding:13px 16px;margin:12px 0;font-size:12.5px}}
</style></head><body><div class="wrap">

<h1>新 ETF 池子 · 基线 / D3 / P3 三策略对比</h1>
<div class="sub">2026-10-24 ｜ 池子：515880 通信ETF国泰 · 159516 半导体设备ETF国泰 ·
588200 科创芯片ETF嘉实 · 159530 机器人ETF易方达 · 513100 纳指ETF（防守位 512040）<br>
初始净值 1 · 单边万一（双边 0.02%）· 512890 红利低波已移出轮动池（独立策略）</div>

<div class="verdict">
<h3>结论：这个池子回撤是主选池的 2 倍以上；D3/P3 改善幅度更大但绝对值仍差 —— 不建议替换</h3>
<ul>
<li><b>主窗（2024-01-18 起，5 只全齐）：基线 MDD <span style="color:#b3351a">−45.71%</span>、
Calmar 仅 0.29</b>；同口径主选池是 −21.57% / 1.58。
长窗（动态池）MDD −45.71%、Calmar 0.34，主选池同起点是 −21.57% / 1.56。<b>回撤深一倍以上。</b></li>
<li><b>D3/P3 在这个池子上的改善幅度反而比主选池更大</b>：MDD −45.71% → D3 −39.84% →
P3 <b>−29.59%</b>（改善 16.1pp）；收益 +41.13% → +75.04% → <b>+81.57%</b>。
⇒ 再次证明<b>策略框架可移植</b>，且池子越差、风控的相对价值越大。</li>
<li><b>⚠️ 但 P3 出现了一个主选池没有的问题：2025 年 P3 只 +32.7%，D3 却有 +93.7%。</b>
原因是 515880（通信ETF）的 ROC60 在 2025 年 7~10 月<b>连续三次</b>冲进 95 分位
（+7.24% / +18.55% / +23.14%），P3 连续空仓 60 天，恰好把通信的主升浪砍掉。
⇒ <b>小池子（5 只）里，选出的头名会反复触及高位</b>，P3 的「追高即止盈」容易误伤趋势。
主选池 9 只分散度高，头名冲高后通常真的见顶，所以没这个问题。</li>
<li><b>根因：这个池子同质性太高</b>——通信、半导体设备、科创芯片、机器人、纳指，
5 只里 4 只是 A 股高波动科技成长，涨跌高度同步（2026 年 4 只全跌），
缺乏主选池那种「宽基 + 成长 + 黄金 + 纳指 + 恒生」的跨属性分散。<b>这再次验证：
决定回撤的是池子的资产属性，不是策略参数。</b></li>
<li><b>结论：不建议替换主选池。</b>若你要用这个池子，建议至少加入黄金、宽基一类
低相关资产，否则 −45% 级别的回撤会长期存在。</li>
</ul>
</div>

<h2>一、池子构成与数据覆盖</h2>
<div class="box kv">
<table><thead><tr><th>代码</th><th>名称</th><th>数据起始</th><th>根数</th></tr></thead><tbody>
<tr><td class="mono">515880</td><td>通信ETF国泰</td><td class="mono">2019-09-06</td><td class="num">1725</td></tr>
<tr><td class="mono">588200</td><td>科创芯片ETF嘉实</td><td class="mono">2022-10-26</td><td class="num">968</td></tr>
<tr><td class="mono">159516</td><td>半导体设备ETF国泰</td><td class="mono">2023-07-27</td><td class="num">784</td></tr>
<tr><td class="mono">159530</td><td>机器人ETF易方达</td><td class="mono">2024-01-18</td><td class="num">666</td></tr>
<tr><td class="mono">513100</td><td>纳指ETF</td><td class="mono">2018-12-03</td><td class="num">1896</td></tr>
<tr><td class="mono">512040</td><td>价值ETF富国（防守位）</td><td class="mono">2018-12-03</td><td class="num">1897</td></tr>
</tbody></table>
<div class="note"><b>上市时间差异大</b> ⇒ 主窗取「5 只全齐」的 2024-01-18，长窗用动态池
（早期只有 1~2 只可交易，<b>长窗结果仅供参考</b>，此时实际上接近单押纳指）。</div>
</div>

<h2>二、主窗结果（2024-01-18 ~ 2026-10-23，666 个交易日）</h2>
<table><thead><tr>
<th rowspan="2">方案</th><th colspan="6">新池子（5 只）</th><th colspan="3" style="color:#8a8f99">主选池 9 只（同窗对照）</th>
</tr><tr>
<th>净值</th><th>区间收益</th><th>MDD</th><th>Calmar</th><th>夏普</th><th>换仓</th>
<th>净值</th><th>MDD</th><th>Calmar</th>
</tr></thead><tbody>{head_rows("主窗(全齐)")}</tbody></table>
<div class="note">各年可用标的数：{avail_rows("主窗(全齐)")}</div>

<h3>逐年拆解（主窗）</h3>
<table><thead><tr><th>年</th><th>基线收益</th><th>基线MDD</th><th>D3收益</th><th>D3 MDD</th>
<th>P3收益</th><th>P3 MDD</th><th></th></tr></thead>
<tbody>{year_rows("主窗(全齐)")}</tbody></table>
<div class="note"><b>注意 2025 年</b>：P3 +32.7% 大幅跑输 D3 的 +93.7% —— 通信ETF 三次冲高触发空仓，
错过主升浪。<b>但 2026 年 P3 +23.9% vs 基线 −23.1% vs D3 −10.4%</b>，P3 在下跌段保护明显。
两相抵消后主窗整体仍是 P3 最优。</div>

<h2>三、长窗结果（2018-12-03 ~ 2026-10-23，动态池）</h2>
<table><thead><tr>
<th rowspan="2">方案</th><th colspan="6">新池子（动态）</th><th colspan="3" style="color:#8a8f99">主选池 9 只（同起点对照）</th>
</tr><tr>
<th>净值</th><th>区间收益</th><th>MDD</th><th>Calmar</th><th>夏普</th><th>换仓</th>
<th>净值</th><th>MDD</th><th>Calmar</th>
</tr></thead><tbody>{head_rows("长窗(动态池)")}</tbody></table>
<div class="note">各年可用标的数：{avail_rows("长窗(动态池)")}
<br>⚠️ 2018-2021 年只有 1~2 只可交易（基本是纳指 ETF 单押），此段结果不代表池子真实表现。</div>

<h3>逐年拆解（长窗）</h3>
<table><thead><tr><th>年</th><th>基线收益</th><th>基线MDD</th><th>D3收益</th><th>D3 MDD</th>
<th>P3收益</th><th>P3 MDD</th><th></th></tr></thead>
<tbody>{year_rows("长窗(动态池)")}</tbody></table>

<h2>四、与主选池的关键差异</h2>
<div class="box kv">
<table><thead><tr><th>维度</th><th>新池子（5 只）</th><th>主选池（9 只）</th></tr></thead><tbody>
<tr><td>资产属性</td><td>4 只 A 股高波动科技成长 + 1 只纳指，同质性极高</td>
<td>A股宽基 + A股成长 + <b>黄金</b> + <b>纳指</b> + <b>恒生</b> + 行业主题</td></tr>
<tr><td>主窗 MDD（基线）</td><td style="color:#b3351a;font-weight:700">−45.71%</td>
<td style="font-weight:700">−21.57%</td></tr>
<tr><td>主窗 MDD（P3）</td><td style="color:#b3351a;font-weight:700">−29.59%</td>
<td style="font-weight:700;color:{GREEN}">−15.12%</td></tr>
<tr><td>Calmar（P3）</td><td>0.83</td><td style="font-weight:700">2.85</td></tr>
<tr><td>持仓集中度 TOP3</td><td>纳指 31% / 通信 28% / 半导体设备 14%</td>
<td>纳指 26% / 创成长 19% / 黄金 16%</td></tr>
</tbody></table>
<div class="note"><b>数量不是关键，属性才是</b>：5 只里 4 只 A 股科技同步涨跌（2026 年 4 只全跌、无一对冲），
所以回撤深；主选池 9 只里有黄金和纳指两个与 A 股低相关的资产，这才是回撤浅的根本原因。</div>
</div>

<div class="warn">
<b>⚠️ 必读：P3 在「小池子 / 高同质池」上有趋势误伤风险</b><br>
主选池 9 只里，头名 ROC60 冲进 95 分位后通常真的见顶（触发后 20 日中位 −4.11%、胜率 37%）；
但这个 5 只池里，515880 因「其他 4 只更弱」而长期是最强，其 ROC60 会<b>反复触及高位</b>，
P3 于是一次次空仓，把趋势砍断（2025 年连续 3 次、共 60 天）。
⇒ <b>若未来要用 P3 于小池子，需要额外加「同一标的不重复止盈」或提高分位阈值</b>，
但在主选池上不需要（当前 q=0.95 已验证最优邻域成片）。
</div>

<div class="note" style="margin-top:20px">
脚本：<code>fetch_new_pool.py</code>（抓 4 只新代码长历史）、<code>new_pool_study.py</code>（回测）｜
产物：<code>out/new_pool_compare.json</code>、<code>tdx_adj_new_2019_2026.csv</code><br>
数据源：腾讯 fqkline 前复权（与既有长历史同口径）；513100/512040 取自既有长历史保证同源</div>

</div></body></html>"""

    p = os.path.join(HERE, "out", "新池子三策略对比.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"写出: {p}")


if __name__ == "__main__":
    main()
