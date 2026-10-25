"""生成「中证2000 双均线闸门」研究报告（2026-10-25）"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import etf_backtest_long as bl     # noqa: E402
import online_data as od           # noqa: E402
from rot_stop_study import load_win  # noqa: E402
from zz2000_gate_study import (BASE, COST, KITS, WINDOWS,  # noqa: E402
                               stats_of, zz_regime)

RED, GRN, MUT = "#d4380d", "#0f9d58", "#6c717f"
BLUE, ORANGE, PINK = "#3d57e5", "#e8890c", "#c2185b"
SAFE = {"518880", "513100", "513660", "512040"}


def f2(v, d=2, sign=False):
    if v is None or v != v:
        return "—"
    return f"{v:+.{d}f}" if sign else f"{v:.{d}f}"


def col(v):
    return RED if v and v > 0 else GRN


def main() -> None:
    j = json.load(open(os.path.join(HERE, "out", "zz2000_gate.json"),
                       encoding="utf-8"))
    grid = j["grid"]
    sig = j["signal"]
    rnd = {r["win"]: r for r in j["rand"]}
    att = j.get("attrib", {})

    # ---- 变体 B 重算（保险起见用同一引擎现跑）----
    _d = od.fetch_csindex_daily("932000", beg="2014-01-01")
    _s = _d.set_index("date")["close"].astype(float).sort_index()
    reg_all = zz_regime(_s)
    vb = {}
    for wname, fname, start in WINDOWS:
        panel, dates, s_i, e_i = load_win(os.path.join(HERE, fname), start)
        rot, deff, _ = bl.read_pools()
        pool = [c for c in rot if c in panel.columns]
        sig_all = [bl.compute_signal(panel, pool, i, 20, 20)
                   for i in range(len(dates))]
        reg = reg_all.reindex(dates, method="ffill").fillna(0.0)
        kw = dict(buy=COST, sell=COST, **BASE, **KITS["D3+P3"])
        ps = reg.map({1.0: 1.0, 0.0: 0.5})
        r = bl.backtest(panel, dates, s_i, e_i, pool, deff[0],
                        sig_list=sig_all, pos_scale=ps, pos_exempt=SAFE, **kw)
        vb[wname] = stats_of(r["navs"])

    def get(win, kit, half):
        for g in grid:
            if g["win"] == win and g["kit"] == kit and g["half"] == half:
                return g
        return None

    # ---------------- 表格：主窗 / 长窗 网格 ----------------
    def grid_tbl(win, kit):
        base = get(win, kit, None)
        rows = [f'<tr class="hi"><td>无闸门（现用）</td><td class="num">{base["final"]:.4f}</td>'
                f'<td class="num">{base["total"]:+.2f}%</td>'
                f'<td class="num">{base["mdd"]:.2f}%</td>'
                f'<td class="num">{base["calmar"]:.2f}</td>'
                f'<td class="num">—</td><td class="num">—</td></tr>']
        for half in (0.0, 0.3, 0.5, 0.7):
            g = get(win, kit, half)
            rows.append(
                f'<tr><td>空头期 {half*10:.0f} 成仓</td>'
                f'<td class="num">{g["final"]:.4f}</td>'
                f'<td class="num" style="color:{col(g["total"])}">{g["total"]:+.2f}%</td>'
                f'<td class="num">{g["mdd"]:.2f}%</td>'
                f'<td class="num">{g["calmar"]:.2f}</td>'
                f'<td class="num" style="color:{col(g["final"]-base["final"])}">'
                f'{g["final"]-base["final"]:+.4f}</td>'
                f'<td class="num" style="color:{col(g["mdd"]-base["mdd"])}">'
                f'{g["mdd"]-base["mdd"]:+.2f}pp</td></tr>')
        return ('<table class="t"><thead><tr><th>方案</th><th class="num">净值</th>'
                '<th class="num">区间收益</th><th class="num">最大回撤</th>'
                '<th class="num">Calmar</th><th class="num">Δ净值</th>'
                '<th class="num">Δ回撤</th></tr></thead><tbody>'
                + "".join(rows) + "</tbody></table>")

    def sig_rows():
        out = ""
        for w, n in (("main", "主窗 2019-2026"), ("long", "长窗 2015-2026")):
            s = sig[w]
            out += (f'<tr><td>{n}</td><td class="num">{s["bull"]}</td>'
                    f'<td class="num">{s["bear"]}</td>'
                    f'<td class="num">{s["bull_pct"]:.0f}%</td>'
                    f'<td class="num">{s["switch"]}</td>'
                    f'<td class="num" style="color:{col(s["zz_bull"])}">{s["zz_bull"]:+.3f}%</td>'
                    f'<td class="num" style="color:{col(s["zz_bear"])}">{s["zz_bear"]:+.3f}%</td>'
                    f'<td class="num">{s["sh_bull"]:+.3f}%</td>'
                    f'<td class="num">{s["sh_bear"]:+.3f}%</td></tr>')
        return out

    def rnd_rows():
        out = ""
        for w, n in (("main", "主窗"), ("long", "长窗")):
            r = rnd[w]
            out += (f'<tr><td>{n}</td><td class="num">{r["n_bear"]}</td>'
                    f'<td class="num">{r["real_final"]:.4f}</td>'
                    f'<td class="num">{r["med_final"]:.4f}</td>'
                    f'<td class="num" style="font-weight:700;color:{BLUE}">'
                    f'{r["beat_final"]:.0f}%</td>'
                    f'<td class="num">{r["real_mdd"]:.2f}%</td>'
                    f'<td class="num">{r["med_mdd"]:.2f}%</td>'
                    f'<td class="num">{r["beat_mdd"]:.0f}%</td></tr>')
        return out

    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>中证2000 双均线闸门 · 回测报告</title><style>
body{{margin:0;padding:24px;background:#eef0f3;color:#202020;
font:14px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1180px;margin:0 auto}}
.hero{{border-radius:14px;padding:20px 24px;margin-bottom:16px;color:#fff;
background:linear-gradient(120deg,#3d57e5,#566de7)}}
.hero h1{{margin:0 0 6px;font-size:20px}}
.hero .sub{{font-size:12.5px;opacity:.92}}
h2{{font-size:16px;margin:26px 0 10px;padding-left:10px;color:#202020;
border-left:4px solid;border-image:linear-gradient(180deg,#3d57e5,#566de7) 1}}
h3{{font-size:14px;margin:18px 0 8px;color:#374151}}
table.t{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e5ea;
border-radius:10px;overflow:hidden;font-size:12.5px;margin:8px 0 4px;
box-shadow:0 1px 3px rgba(16,24,40,.05)}}
table.t th,table.t td{{padding:7px 9px;border-bottom:1px solid #eef0f3;white-space:nowrap}}
table.t th{{background:#f4f6fa;font-weight:600;color:#4b5563;text-align:left}}
table.t td.num{{text-align:right;font-variant-numeric:tabular-nums}}
table.t tr.hi td{{background:#f7f9ff;font-weight:600}}
table.t tbody tr:last-child td{{border-bottom:none}}
.verdict{{background:#fff4ec;border:1px solid #ffd9c0;border-left:4px solid #d4380d;
border-radius:8px;padding:12px 16px;margin:12px 0;font-size:13px;line-height:1.8}}
.ok{{background:#f2fbf6;border-color:#c9ecd9;border-left-color:#0f9d58}}
.note{{background:#f7f9fb;border:1px dashed #d8dee6;border-radius:8px;padding:10px 14px;
font-size:12.5px;color:#4b5563;line-height:1.8;margin:10px 0}}
code{{background:#eef1f6;padding:1px 5px;border-radius:4px;font-size:12px}}
.foot{{margin-top:22px;padding-top:12px;border-top:1px solid #dfe3e8;color:{MUT};font-size:11.5px}}
</style></head><body><div class="wrap">
<div class="hero"><h1>中证2000 双均线「多空闸门」回测</h1>
<div class="sub">规则：站上 MA53 → 多头；多头中首次跌破 MA19 → 空头 ｜
闸门：多头满仓 / 空头减仓 ｜ 单边万一 ｜ 初始净值 1 ｜ 2026-10-25</div></div>

<div class="verdict"><b>结论：不可用。</b>双窗口 32 组配置（4 个载体 × 4 档空头仓位 × 2 窗口）
<b>没有一组能在不牺牲收益的前提下改善回撤</b>；最优的「空头 7 成仓」也只是把损失压到 −1.66/−5.00。
把「空头仓位」放开到 0.3 以下，收益直接腰斩（长窗 20.02 → 7.37）。
更关键的是：<b>随机同频对照显示真实择时只优于 53%（主窗）/ 47%（长窗）的随机情形</b>，
即<b>这个闸门的择时点与随机减仓没有统计差别</b>。</div>

<h2>一、信号本身有效，但方向用错了地方</h2>
<table class="t"><thead><tr><th>窗口</th><th class="num">多头日</th><th class="num">空头日</th>
<th class="num">多头占比</th><th class="num">切换次数</th>
<th class="num">中证2000 多头日均</th><th class="num">中证2000 空头日均</th>
<th class="num">上证 多头日均</th><th class="num">上证 空头日均</th></tr></thead>
<tbody>{sig_rows()}</tbody></table>
<div class="note">信号对<b>中证2000 自己</b>是有效的：多头期日均 +0.24%~+0.28%，空头期 −0.21%~−0.23%，
差 0.45~0.51pp。但我们的策略池里有<b>黄金 518880、纳指 513100、恒生 513660</b>——
A 股小盘转弱时，这些避险品种往往正强，减仓等于砍掉最好的那一段。</div>

<h2>二、D3+P3 叠加闸门（主口径）</h2>
<h3>主窗 2019-12-31 ~ 2026（单边万一）</h3>
{grid_tbl("main", "D3+P3")}
<h3>长窗 2015-01-05 ~ 2026</h3>
{grid_tbl("long", "D3+P3")}
<div class="verdict">主窗：<b>每一档都让回撤变差</b>（−15.12% → −15.60%），收益还少 2.9~7.2。
长窗：回撤确实改善最多 2.01pp，但净值从 20.02 掉到 9.91（<b>−50.5%</b>），
Calmar 从 1.66 掉到 1.39。<b>用一半的收益换 2pp 回撤，不划算。</b></div>

<h2>三、变体 B：空头期只减 A 股，放过黄金/纳指/恒生</h2>
<table class="t"><thead><tr><th>窗口</th><th>方案</th><th class="num">净值</th>
<th class="num">最大回撤</th><th class="num">Calmar</th><th class="num">Δ净值</th>
<th class="num">Δ回撤</th></tr></thead><tbody>
<tr class="hi"><td>主窗</td><td>无闸门（现用）</td><td class="num">{get("main","D3+P3",None)["final"]:.4f}</td>
<td class="num">{get("main","D3+P3",None)["mdd"]:.2f}%</td>
<td class="num">{get("main","D3+P3",None)["calmar"]:.2f}</td><td class="num">—</td><td class="num">—</td></tr>
<tr><td>主窗</td><td>变体A 全部减半</td><td class="num">{get("main","D3+P3",0.5)["final"]:.4f}</td>
<td class="num">{get("main","D3+P3",0.5)["mdd"]:.2f}%</td>
<td class="num">{get("main","D3+P3",0.5)["calmar"]:.2f}</td>
<td class="num" style="color:{col(get("main","D3+P3",0.5)["final"]-get("main","D3+P3",None)["final"])}">{get("main","D3+P3",0.5)["final"]-get("main","D3+P3",None)["final"]:+.4f}</td>
<td class="num" style="color:{col(get("main","D3+P3",0.5)["mdd"]-get("main","D3+P3",None)["mdd"])}">{get("main","D3+P3",0.5)["mdd"]-get("main","D3+P3",None)["mdd"]:+.2f}pp</td></tr>
<tr><td>主窗</td><td><b>变体B 只减 A 股</b></td><td class="num">{vb["main"]["final"]:.4f}</td>
<td class="num">{vb["main"]["mdd"]:.2f}%</td><td class="num">{vb["main"]["calmar"]:.2f}</td>
<td class="num" style="color:{col(vb["main"]["final"]-get("main","D3+P3",None)["final"])}">{vb["main"]["final"]-get("main","D3+P3",None)["final"]:+.4f}</td>
<td class="num" style="color:{col(vb["main"]["mdd"]-get("main","D3+P3",None)["mdd"])}">{vb["main"]["mdd"]-get("main","D3+P3",None)["mdd"]:+.2f}pp</td></tr>
<tr class="hi"><td>长窗</td><td>无闸门（现用）</td><td class="num">{get("long","D3+P3",None)["final"]:.4f}</td>
<td class="num">{get("long","D3+P3",None)["mdd"]:.2f}%</td>
<td class="num">{get("long","D3+P3",None)["calmar"]:.2f}</td><td class="num">—</td><td class="num">—</td></tr>
<tr><td>长窗</td><td>变体A 全部减半</td><td class="num">{get("long","D3+P3",0.5)["final"]:.4f}</td>
<td class="num">{get("long","D3+P3",0.5)["mdd"]:.2f}%</td>
<td class="num">{get("long","D3+P3",0.5)["calmar"]:.2f}</td>
<td class="num" style="color:{col(get("long","D3+P3",0.5)["final"]-get("long","D3+P3",None)["final"])}">{get("long","D3+P3",0.5)["final"]-get("long","D3+P3",None)["final"]:+.4f}</td>
<td class="num" style="color:{col(get("long","D3+P3",0.5)["mdd"]-get("long","D3+P3",None)["mdd"])}">{get("long","D3+P3",0.5)["mdd"]-get("long","D3+P3",None)["mdd"]:+.2f}pp</td></tr>
<tr><td>长窗</td><td><b>变体B 只减 A 股</b></td><td class="num">{vb["long"]["final"]:.4f}</td>
<td class="num">{vb["long"]["mdd"]:.2f}%</td><td class="num">{vb["long"]["calmar"]:.2f}</td>
<td class="num" style="color:{col(vb["long"]["final"]-get("long","D3+P3",None)["final"])}">{vb["long"]["final"]-get("long","D3+P3",None)["final"]:+.4f}</td>
<td class="num" style="color:{col(vb["long"]["mdd"]-get("long","D3+P3",None)["mdd"])}">{vb["long"]["mdd"]-get("long","D3+P3",None)["mdd"]:+.2f}pp</td></tr>
</tbody></table>
<div class="note">变体 B 把损失从 −4.45 缩到 −1.83（主窗），说明<b>「砍掉避险品种」确实是主因之一</b>；
但回撤仍然一点没改善，Calmar 也从 2.85 降到 2.52，所以还是<b>不如不做</b>。</div>

<h2>四、随机同频对照（D3+P3，空头 5 成仓）</h2>
<table class="t"><thead><tr><th>窗口</th><th class="num">半仓日数</th>
<th class="num">真实净值</th><th class="num">随机中位</th><th class="num">真实优于随机</th>
<th class="num">真实回撤</th><th class="num">随机回撤中位</th><th class="num">真实回撤更浅占比</th></tr></thead>
<tbody>{rnd_rows()}</tbody></table>
<div class="verdict">真实择时<b>只优于 53% / 47% 的随机减仓</b>——这个数字贴近 50% 就意味着<b>毫无信息量</b>。
回撤维度上真实（−15.60%）甚至比随机中位（−15.29% / −15.29%）<b>还差</b>：
减仓确实能降回撤，但我们挑的减仓时点比随机挑还糟。</div>

<h2>五、为什么必然失败：回撤段不在空头期</h2>
<div class="note">
归因（D3+P3，无闸门）：<b>空头期策略日均仍是正收益</b>（主窗 {att.get("main",{}).get("bear_mean",0):+.3f}%、
胜率 {att.get("main",{}).get("bear_win",0):.0f}%；长窗 {att.get("long",{}).get("bear_mean",0):+.3f}%、
胜率 {att.get("long",{}).get("bear_win",0):.0f}%），只是低于多头期（{att.get("main",{}).get("bull_mean",0):+.3f}%）。
所以减仓 = <b>主动放弃正收益</b>。<br>
更致命的是：主窗那 −15.6% 的最大回撤<b>发生在多头区间里</b>——闸门只在空头期发力，
结构上就覆盖不到该回撤段，因此主窗 MDD 不但没改善，反而因为路径改变略微变差。
</div>

<h2>六、和之前几次失败的关系</h2>
<div class="note">
这是同一类陷阱的<b>第四次复现</b>：①广度止损（24 组全负）②广度闸门（96 组收益 0 胜）
③牛马熊/0AMV 横截面择时（两窗口全负）④本次中证2000 价格择时。<br>
共同根因：<b>「A 股整体转弱」在本池子里不是风险信号，而是避险格局</b>——
池子含黄金/纳指/恒生，A 股资金撤离时它们往往在涨。任何"看 A 股弱就减仓"的闸门都会砍到这一段。
</div>

<div class="foot">数据：中证2000 指数 932000（中证指数公司，2014-01-01 起，含回溯段）｜
ETF 前复权长历史 CSV ｜ 成本单边万一 ｜ 随机对照 60 次/窗口 ｜
本报告仅供研究参考，不构成个人投资建议</div>
</div></body></html>"""

    out = os.path.join(HERE, "out", "中证2000闸门_回测.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("已生成:", out, len(html), "字符")


if __name__ == "__main__":
    main()
