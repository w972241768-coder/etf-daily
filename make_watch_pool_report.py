"""生成「备选池 vs 主选池」三策略对比报告（2026-10-24）"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BG, FG, MUT, BD = "#eef0f3", "#202020", "#6c717f", "#e2e5ea"
BLUE, RED, GREEN = "#3d57e5", "#d4380d", "#0f9d58"


def f2(v, d=2, sign=False):
    if v is None:
        return "—"
    return f"{v:+.{d}f}" if sign else f"{v:.{d}f}"


def col(v):
    if v is None:
        return MUT
    return RED if v > 0 else (GREEN if v < 0 else FG)


def main():
    j = json.load(open(os.path.join(HERE, "out", "watch_pool_compare.json"), encoding="utf-8"))
    cases = j["cases"]
    pools = ["主选池 9 只", "备选池 14 只（剔除512880）", "备选池 15 只（全含）"]

    def get(pool, win, scheme):
        for c in cases:
            if c["pool"] == pool and c["win"] == win and c["scheme"] == scheme:
                return c
        return None

    def summary_rows(win):
        rows = ""
        for p in pools:
            b, d, p3 = get(p, win, "基线"), get(p, win, "D3"), get(p, win, "P3")
            if not b:
                continue
            best = "主选" if p.startswith("主选") else "备选"
            rows += (f'<tr><td><b>{p}</b></td>'
                     f'<td class="num">{f2(b["final"], 4)}</td>'
                     f'<td class="num">{f2(b["mdd"])}%</td>'
                     f'<td class="num">{f2(b["calmar"])}</td>'
                     f'<td class="num">{f2(d["final"], 4)}</td>'
                     f'<td class="num" style="color:{col(d["mdd"]-b["mdd"])};font-weight:600">'
                     f'{f2(d["mdd"])}%</td>'
                     f'<td class="num" style="color:{col(d["calmar"]-b["calmar"])}">{f2(d["calmar"])}</td>'
                     f'<td class="num" style="font-weight:700">{f2(p3["final"], 4)}</td>'
                     f'<td class="num" style="color:{col(p3["mdd"]-b["mdd"])};font-weight:700">'
                     f'{f2(p3["mdd"])}%</td>'
                     f'<td class="num" style="font-weight:700">{f2(p3["calmar"])}</td>'
                     f'<td class="num" style="color:{col(p3["mdd"]-b["mdd"])};font-weight:700">'
                     f'{f2(p3["mdd"]-b["mdd"], 2, True)}</td></tr>')
        return rows

    def year_rows(pool, win):
        b, d, p3 = get(pool, win, "基线"), get(pool, win, "D3"), get(pool, win, "P3")
        if not b:
            return ""
        rows = ""
        for y in sorted(b["yearly"].keys()):
            yb, yd, yp = b["yearly"][y], d["yearly"].get(y, {}), p3["yearly"].get(y, {})
            rows += (f'<tr><td class="num">{y}</td>'
                     f'<td class="num">{f2(yb["ret"], 1, True)}%</td>'
                     f'<td class="num" style="color:{col(yb["mdd"])}">{f2(yb["mdd"], 1)}%</td>'
                     f'<td class="num">{f2(yd.get("ret", 0), 1, True)}%</td>'
                     f'<td class="num" style="color:{col(yd.get("mdd", 0))}">{f2(yd.get("mdd", 0), 1)}%</td>'
                     f'<td class="num">{f2(yp.get("ret", 0), 1, True)}%</td>'
                     f'<td class="num" style="color:{col(yp.get("mdd", 0))}">{f2(yp.get("mdd", 0), 1)}%</td></tr>')
        return rows

    def avail_rows(pool, win):
        for a in j["avail"]:
            if a["pool"] == pool and a["win"] == win:
                return "  ".join(f'<span class="chip">{y}: {n} 只</span>'
                                 for y, n in a["avail"].items())
        return ""

    m_same = get("主选池 9 只", "长窗(同起点)", "P3")
    w14_same = get("备选池 14 只（剔除512880）", "长窗(同起点)", "P3")
    w15_same = get("备选池 15 只（全含）", "长窗(同起点)", "P3")
    m_main = get("主选池 9 只", "主窗", "P3")
    w14_main = get("备选池 14 只（剔除512880）", "主窗", "P3")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>备选池 vs 主选池 · 三策略对比</title>
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

<h1>备选池 vs 主选池 · 基线 / D3 / P3 三策略对比</h1>
<div class="sub">2026-10-24 ｜ 初始净值 1 · 单边万一（双边 0.02%）· 防守位 512040
｜ 目的：检验策略是否适用于备选池（watch 15 只）</div>

<div class="verdict">
<h3>结论：策略框架适用于任何池子，但备选池本身质地远差于主选池 —— 不建议换池</h3>
<ul>
<li><b>同起点（2018-12-03 起）长窗对比，主选池 P3 净值 {f2(m_same['final'], 4)}，
备选池14只 P3 仅 {f2(w14_same['final'], 4)}、备选池15只 {f2(w15_same['final'], 4)}</b>
—— 相差 <b>4 倍以上</b>。MDD 同样悬殊：主选 <b>−15.12%</b> vs 备选 −26.63%/−29.07%。</li>
<li><b>D3/P3 在备选池上仍是正向的</b>（MDD 从 −36.03% 改善到 −26.63%，收益从 +124% 提升到 +169%）
⇒ <b>策略框架可移植</b>，问题在池子本身。</li>
<li><b>根因：备选池缺少主选池的两个核心收益/避险来源 —— 黄金（518880）与纳指（513100）。</b>
主选池持仓集中度 TOP3 是 纳指 26% / 创成长 19% / 黄金 18%；
备选池 TOP3 是 红利低波 24% / 创业板 16% / 科创50 12% —— 全集中在 A 股，
没有跨市场分散，也没有避险资产 ⇒ 牛市跟不上、熊市躲不掉。</li>
<li><b>512880 的加入是双刃剑</b>：15 只版本 2019/2026 年收益更高，但 2020 年
基线 +29.2% → D3 +13.2% → P3 +11.6%（P3 的追高空仓砍掉了券商的连续暴涨）⇒
高 Beta 单一行业品种与 P3 机制存在冲突。</li>
<li><b>你的判断是对的：主选池已够用。</b>它同时覆盖了 A 股宽基、成长、黄金、纳指、恒生、
行业主题，跨市场+跨风险属性，这是它跑赢的根本原因。换到备选池会同时损失收益和回撤表现。</li>
</ul>
</div>

<h2>一、同起点长窗对比（2018-12-03 ~ 2026-09-24，约 7.8 年）</h2>
<div class="box">
<p class="kv" style="margin:0 0 8px"><b>为什么不是 2015 起步长窗</b>：备选池多只标的 2023~2025 年才上市
（159259 于 2025-08、159543 于 2023-11、159505 于 2023-12、588220 于 2023-09），
2015-2018 的数据<b>不存在</b>，无法做。故备选池用数据允许的最早起点 2018-12-03，
并让主选池跑同一起点以保证可比。</p>
<table><thead><tr>
<th rowspan="2">池子</th><th colspan="3">基线</th><th colspan="3">D3</th>
<th colspan="3">P3 追高止盈</th><th rowspan="2">MDD 改善<br>(P3−基线)</th>
</tr><tr>
<th>净值</th><th>MDD</th><th>Calmar</th>
<th>净值</th><th>MDD</th><th>Calmar</th>
<th>净值</th><th>MDD</th><th>Calmar</th>
</tr></thead><tbody>{summary_rows("长窗(同起点)")}</tbody></table>
</div>

<h2>二、主窗对比（2019-12-31 ~ 2026-09-24）</h2>
<table><thead><tr>
<th rowspan="2">池子</th><th colspan="3">基线</th><th colspan="3">D3</th>
<th colspan="3">P3 追高止盈</th><th rowspan="2">MDD 改善</th>
</tr><tr>
<th>净值</th><th>MDD</th><th>Calmar</th>
<th>净值</th><th>MDD</th><th>Calmar</th>
<th>净值</th><th>MDD</th><th>Calmar</th>
</tr></thead><tbody>{summary_rows("主窗")}</tbody></table>

<h2>三、各池可用标的数（sanity check）</h2>
<div class="box">
<h3>主选池 · 长窗(同起点)</h3><div>{avail_rows("主选池 9 只", "长窗(同起点)")}</div>
<h3>备选池 14 只 · 长窗(同起点)</h3><div>{avail_rows("备选池 14 只（剔除512880）", "长窗(同起点)")}</div>
<h3>备选池 15 只 · 长窗(同起点)</h3><div>{avail_rows("备选池 15 只（全含）", "长窗(同起点)")}</div>
<div class="note">备选池从 2018 年的 8~9 只增长到 2026 年的 14~15 只 —— 后期标的增加有「事后选样」成分，
前期结果更能代表池子本身的质地。</div>
</div>

<h2>四、逐年拆解</h2>
<div style="display:flex;gap:16px;flex-wrap:wrap">
<div style="flex:1 1 380px">
<h3>主选池 · 长窗(同起点)</h3>
<table><thead><tr><th>年</th><th>基线收益</th><th>基线MDD</th>
<th>D3收益</th><th>D3 MDD</th><th>P3收益</th><th>P3 MDD</th></tr></thead>
<tbody>{year_rows("主选池 9 只", "长窗(同起点)")}</tbody></table>
</div>
<div style="flex:1 1 380px">
<h3>备选池 14 只 · 长窗(同起点)</h3>
<table><thead><tr><th>年</th><th>基线收益</th><th>基线MDD</th>
<th>D3收益</th><th>D3 MDD</th><th>P3收益</th><th>P3 MDD</th></tr></thead>
<tbody>{year_rows("备选池 14 只（剔除512880）", "长窗(同起点)")}</tbody></table>
</div>
</div>

<h2>五、为什么主选池赢 —— 持仓集中度对照</h2>
<div class="box kv">
<table><thead><tr><th>池子</th><th>持仓集中度 TOP3（长窗同起点）</th></tr></thead><tbody>
<tr><td>主选池 9 只</td><td>纳指ETF 26% ｜ 创成长 19% ｜ <b>黄金 18%</b></td></tr>
<tr><td>备选池 14 只</td><td>红利低波 24% ｜ 创业板 16% ｜ 科创50 12%</td></tr>
<tr><td>备选池 15 只</td><td>红利低波 23% ｜ 创业板 15% ｜ 科创50 12%</td></tr>
</tbody></table>
<div class="note"><b>关键差异在资产属性，不在数量</b>：主选池 9 只已覆盖
「A股宽基 + A股成长 + <b>黄金</b> + <b>纳指</b> + <b>恒生</b> + 行业主题」，
跨市场、跨币种、跨风险属性；备选池 15 只全是 A 股（宽基/成长/行业/红利），
同质性高、无避险资产。数量多不等于分散 —— <b>资产属性不同才叫分散</b>。</div>
</div>

<div class="warn">
<b>⚠️ 必须同时说明的三点</b><br>
① <b>512880 口径</b>：512880 是原主选池成员、后被降级为 watch。同口径对比须剔除，
故 14 只版本才是与主选池可比的基准；15 只版本仅作参考（它含已降级标的）。<br>
② <b>样本外限制</b>：备选池后期（2023 后）可用标的数从 10 只增至 14~15 只，
新增标的的历史表现是「事后可见」的，因此<b>前期（2019-2022）的结果更能代表池子质地</b>。
主选池同样存在此问题，这是本项目最大的方法论局限（幸存者偏差）。<br>
③ <b>成本口径</b>：全部按单边万一（双边 0.02%）计算，初始净值 1。
成本仍是最大变量 —— 若按单边 0.1%，备选池 P3 收益会再降约 100pp。
</div>

<div class="note" style="margin-top:20px">
脚本：<code>watch_pool_study.py</code> ｜ 产物：<code>out/watch_pool_compare.json</code><br>
数据源：前复权长历史（主选池长窗 2015 起 / 备选池 2018-12-03 起，受上市时间限制）</div>

</div></body></html>"""

    p = os.path.join(HERE, "out", "备选池vs主选池_三策略对比.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"写出: {p}")


if __name__ == "__main__":
    main()
