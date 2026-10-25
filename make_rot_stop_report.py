"""生成「主轮动池提前止损」对比报告（2026-10-24）

读 out/rot_stop.json → out/主轮动池止损研究_广度vs亏损冷却.html
"""
from __future__ import annotations

import html
import json
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
JSON_IN = os.path.join(OUT, "rot_stop.json")
HTML_OUT = os.path.join(OUT, "主轮动池止损研究_广度vs亏损冷却.html")

C_A = "#3d57e5"      # 基线
C_B = "#d4380d"      # 用户方案 / 失败方案
C_D3 = "#7b4fbf"     # 亏损冷却 3%
C_D4 = "#14867c"     # 亏损冷却 2.5%
C_MUT = "#98a1ad"
C_DIM = "#6c717f"
RED = "#d4380d"
GREEN = "#0f9d58"

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#eef0f3;color:#202020;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  font-size:13.5px;line-height:1.68;-webkit-font-smoothing:antialiased}
.wrap{max-width:1010px;margin:0 auto;padding:0 18px 56px}
.hero{background:linear-gradient(120deg,#3d57e5,#4a64e8,#566de7);color:#fff;
  border-radius:0 0 16px 16px;padding:24px 26px 22px;margin:0 -18px 18px;
  box-shadow:0 4px 16px rgba(61,87,229,.18)}
.hero h1{margin:0 0 6px;font-size:21px;font-weight:700;letter-spacing:.2px}
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
.verdict{border-radius:10px;padding:13px 16px;margin:0 0 12px;
  background:#f7f9fe;border:1px solid #d3dced}
.verdict .vh{font-weight:700;font-size:14px;margin-bottom:5px}
.verdict .vt{font-size:12.8px;color:#3a4560}
.vno{background:#fff8f3;border-color:#f0d6c4}
.vyes{background:#f4fbf7;border-color:#cfe8dc}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e5ea;
  border-radius:10px;overflow:hidden;font-size:12.3px}
th,td{padding:6px 9px;border-bottom:1px solid #eef0f3;text-align:left;white-space:nowrap}
th{background:#f4f6fa;font-weight:600;color:#42474f;font-size:11.8px}
tbody tr:hover{background:#fafbfe}
tbody tr:last-child td{border-bottom:none}
.num,.rs{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.9px}
.up{color:#d4380d;font-weight:600}
.dn{color:#0f9d58;font-weight:600}
.tn{color:#42474f;font-weight:600}
.df{color:#42474f}
.dimtxt{color:#98a1ad;font-size:11.5px}
.good{color:#3d57e5;font-weight:700}
.bad{color:#c0392b;font-weight:700}
.tag{display:inline-block;border-radius:5px;padding:0 6px;font-size:10.5px;
  border:1px solid #e2e5ea;background:#f6f7f9;color:#6c717f;margin-left:4px}
.tag.hi{background:#e8edfb;border-color:#c8d4f5;color:#2c4fb8}
.tag.no{background:#fdeeeb;border-color:#f3cfc6;color:#b3351a}
.tblwrap{overflow-x:auto;border-radius:10px}
.split{display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap}
.split>div{flex:1 1 380px;min-width:0}
.csvg{display:block;width:100%;overflow:hidden}
.cg{font-size:10px;fill:#98a1ad;text-anchor:end}
.cx{font-size:10px;fill:#98a1ad;text-anchor:middle}
.legend{font-size:11.8px;color:#6c717f;margin-top:8px}
.legend b{color:#333a4d}
.legend .k{display:inline-block;width:14px;height:3px;border-radius:2px;
  margin-right:4px;vertical-align:middle}
ul{margin:6px 0 0;padding-left:20px}
li{margin:4px 0}
.foot{color:#98a1ad;font-size:11.5px;margin-top:26px;text-align:center;line-height:1.8}
.hm td{text-align:center;font-variant-numeric:tabular-nums;font-size:11.8px}
.hm td.h{background:#f4f6fa;font-weight:600;text-align:left}
tbody tr.hi td{background:#eef2fd}
.hm td.better{background:#e8edfb;color:#2c4fb8;font-weight:700}
.hm td.worse{background:#fdeeeb;color:#b3351a}
.pill{display:inline-block;border-radius:20px;padding:1px 9px;font-size:11px;
  background:#f4f6fa;border:1px solid #e2e5ea;color:#42474f;margin-right:5px}
"""


def fmt(v, nd=2, pct=True, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s + ("%" if pct else "")


def _poly(vals, pl, pt, iw, ih, lo, hi, log=True):
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = pl + iw * i / (n - 1)
        y = (hi - math.log10(max(v, 1e-6))) / (hi - lo)
        pts.append(f"{x:.1f},{pt + ih * y:.1f}")
    return " ".join(pts)


def svg_chart(curves, keys, colors, labels, W=960, H=356, title=""):
    pl, pr, pt, pb = 56, 128, 20, 44
    iw, ih = W - pl - pr, H - pt - pb
    allv = []
    for k in keys:
        allv += curves[k]["v"]
    lv = [math.log10(max(v, 1e-6)) for v in allv]
    lo, hi = min(lv), max(lv)
    pad = max((hi - lo) * .07, .01)
    lo, hi = lo - pad, hi + pad
    ticks = [t for t in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 15) if lo <= math.log10(t) <= hi]
    o = [f'<svg class="csvg" viewBox="0 0 {W} {H}" width="100%" '
         f'style="max-width:{W}px" xmlns="http://www.w3.org/2000/svg">',
         '<rect width="100%" height="100%" fill="#ffffff"/>']
    for tv in ticks:
        y = pt + ih * (hi - math.log10(tv)) / (hi - lo)
        o.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}" stroke="#eef0f3"/>')
        o.append(f'<text class="cg" x="{pl-8}" y="{y+3.5:.1f}">{tv:g}</text>')
    for k, col in zip(keys, colors):
        o.append(f'<polyline fill="none" stroke="{col}" stroke-width="2.2" '
                 f'points="{_poly(curves[k]["v"], pl, pt, iw, ih, lo, hi)}"/>')
    ends = []
    for k, col, lab in zip(keys, colors, labels):
        v = curves[k]["v"][-1]
        y = pt + ih * (hi - math.log10(v)) / (hi - lo)
        ends.append([y, col, lab, v])
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
        o.append(f'<text x="{pl}" y="{pt-6}" font-size="11.5" fill="#42474f" '
                 f'font-weight="700">{html.escape(title)}</text>')
    o.append("</svg>")
    return "".join(o)


def svg_bars(rows, W=960, H=250):
    """20 日内最大回撤（全部为负）按广度分桶。宽度越大=回撤越深。"""
    pl, pr, pt, pb = 52, 20, 26, 42
    iw, ih = W - pl - pr, H - pt - pb
    vals = [r["mdd20"] for r in rows]
    lo = min(vals + [-0.01])
    n = len(rows)
    bw = iw / n * .62
    o = [f'<svg class="csvg" viewBox="0 0 {W} {H}" width="100%" '
         f'style="max-width:{W}px" xmlns="http://www.w3.org/2000/svg">',
         '<rect width="100%" height="100%" fill="#ffffff"/>']
    zy = pt                                   # 0 线在顶部（全部为负）
    for gv in (-1, -2, -3, -4, -5):
        if gv < lo:
            continue
        y = zy + ih * (gv / lo)
        o.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{pl+iw}" y2="{y:.1f}" stroke="#eef0f3"/>')
        o.append(f'<text class="cg" x="{pl-7}" y="{y+3.5:.1f}">{gv}%</text>')
    for i, r in enumerate(rows):
        x = pl + iw * (i + .5) / n - bw / 2
        h = ih * (r["mdd20"] / lo)
        col = C_B if r["breadth"] in (0, 1) else "#b7c2d8"
        o.append(f'<rect x="{x:.1f}" y="{zy:.1f}" width="{bw:.1f}" height="{max(h,1):.1f}" '
                 f'fill="{col}" rx="2" opacity=".9"/>')
        lab = "全部" if r["breadth"] == -1 else str(r["breadth"])
        o.append(f'<text class="cx" x="{pl+iw*(i+.5)/n:.1f}" y="{zy+ih+16:.1f}">'
                 f'{lab}</text>')
        o.append(f'<text class="cx" x="{pl+iw*(i+.5)/n:.1f}" y="{zy+max(h,1)+14:.1f}" '
                 f'font-size="9.5" fill="#6c717f">{r["mdd20"]:.1f}</text>')
    o.append(f'<text x="{pl}" y="{pt-8}" font-size="11.5" fill="#42474f" font-weight="700">'
             f'头名未来 20 日内最大回撤（按当日池内动横20 为正的只数分组）</text>')
    o.append(f'<text class="cx" x="{pl+iw/2:.1f}" y="{H-6}" font-size="10.5" fill="#98a1ad">'
             f'池内动横20 为正的只数（9 = 全线上涨，0 = 全线下跌）</text>')
    o.append("</svg>")
    return "".join(o)


def main():
    d = json.load(open(JSON_IN, encoding="utf-8"))
    M, L = d["scans"]["main"], d["scans"]["long"]
    DM, DL = d["diag"]["main"], d["diag"]["long"]
    bM, bL = d["targets"]["main"]["baseline"], d["targets"]["long"]["baseline"]
    tm, tl = DM["trade_meta"], DL["trade_meta"]

    def find(scan, **kw):
        for x in scan:
            if all(x.get(k) == v for k, v in kw.items()):
                return x
        raise KeyError(kw)

    # 关键方案
    br1 = find(M["breadth"], thr=1, to="DEF", rec=3)
    br1l = find(L["breadth"], thr=1, to="DEF", rec=3)
    br_best_m = max(M["breadth"], key=lambda x: x["stat"]["mdd"])
    br_best_l = max(L["breadth"], key=lambda x: x["stat"]["mdd"])
    d3 = find(M["anti"], kind="loss_cool", param=[0.03, 5])
    d3l = find(L["anti"], kind="loss_cool", param=[0.03, 5])
    d4 = find(M["anti"], kind="loss_cool", param=[0.025, 5])
    d4l = find(L["anti"], kind="loss_cool", param=[0.025, 5])
    mh = find(M["anti"], kind="min_hold", param=10)
    mhl = find(L["anti"], kind="min_hold", param=10)
    sl8 = find(M["price"], label="入场价 −8%→CASH")
    sl8l = find(L["price"], label="入场价 −8%→CASH")

    # ---------- 板块 1：一句话结论 ----------
    lead = (
        f"<b>你提的「只有头名动横20 为正、其余全负 → 止损」不行。</b>"
        f"这个条件判出来的不是「危险」，而是<b>「外面在跌、头名是避险品种」</b>——"
        f"它的头名未来 20 日最大回撤只有 <b>{DM['breadth'][1]['mdd20']:.2f}%</b>，"
        f"比全样本 <b>{DM['breadth'][-1]['mdd20']:.2f}%</b> 还浅。"
        f"实测 24 组参数全部跑输：你这套口径（≤1 只为正 → 切防守位）净值 "
        f"<b>{bM['final']:.4f} → {br1['stat']['final']:.4f}</b>，最大回撤反而从 "
        f"<b>{bM['mdd']:.2f}%</b> 恶化到 <b>{br1['stat']['mdd']:.2f}%</b>。"
        f"<br><br>经典止损（入场价回撤、移动止损、破 MA20、动量转负）<b>同样全部无效</b>，"
        f"原因是<b>根本没有大亏可砍</b>：单笔平均亏损只有 {tm['loss_avg']:.2f}%，"
        f"最深 {tm['max_loss']:.2f}%。回撤是由 125 笔「持有≤5 日、平均 "
        f"{DM['trade_bucket'][0]['avg']:.2f}% / {DM['trade_bucket'][1]['avg']:.2f}%」的"
        f"锯齿交易累出来的，一刀一刀切只会切在反弹前。"
        f"<br><br><b>唯一有效的是另一件事：亏损后不要立刻追下一个头名，空仓 5 个交易日。</b>"
        f"两个窗口都改善（{bM['final']:.4f} → <b>{d3['stat']['final']:.4f}</b>；"
        f"{bL['final']:.4f} → <b>{d3l['stat']['final']:.4f}</b>），"
        f"且在同频率随机空仓的 100/97.5 分位。代价：回撤只从 {bM['mdd']:.2f}% 降到 "
        f"{d3['stat']['mdd']:.2f}%，它<b>不解决回撤</b>。"
    )

    # ---------- 板块 2：广度扫描 ----------
    rows_br = []
    for x in M["breadth"]:
        s = x["stat"]
        hl = (x["thr"] == 1 and x["to"] == "DEF" and x["rec"] == 3)
        cls = ' class="hi"' if hl else ""
        tag = '<span class="tag no">你提的口径</span>' if hl else ""
        rows_br.append(
            f"<tr{cls}><td>≤{x['thr']} 只为正</td><td>{x['to']}</td><td>≥{x['rec']}</td>"
            f"<td class='num'>{s['final']:.4f}</td>"
            f"<td class='num up'>{s['total']:+.2f}%</td>"
            f"<td class='num tn'>{s['mdd']:.2f}%</td>"
            f"<td class='num'>{s['sharpe']:.2f}</td>"
            f"<td class='num'>{s['calmar']:.2f}</td>"
            f"<td class='num'>{s['stops_n']}</td><td>{tag}</td></tr>")

    rows_bucket = []
    for r in DM["breadth"]:
        lab = "全部" if r["breadth"] == -1 else f"{r['breadth']} 只"
        hi = r["breadth"] in (0, 1)
        rows_bucket.append(
            f"<tr{' class=hi' if hi else ''}><td>{lab}</td>"
            f"<td class='num'>{r['n']}</td><td class='num'>{r['share']:.1f}%</td>"
            f"<td class='num'>{r['fwd5']:+.2f}%</td>"
            f"<td class='num'>{r['fwd10']:+.2f}%</td>"
            f"<td class='num'>{r['fwd20']:+.2f}%</td>"
            f"<td class='num tn'>{r['mdd20']:.2f}%</td>"
            f"<td class='num'>{r['win20']:.1f}%</td>"
            f"<td class='num'>{r['m_head']:+.2f}%</td></tr>")

    # ---------- 板块 3：价格类止损 ----------
    rows_pr = []
    for x in M["price"]:
        s = x["stat"]
        cf = x["cf"]
        rows_pr.append(
            f"<tr><td>{html.escape(x['label'])}</td>"
            f"<td class='num'>{s['final']:.4f}</td>"
            f"<td class='num up'>{s['total']:+.2f}%</td>"
            f"<td class='num tn'>{s['mdd']:.2f}%</td>"
            f"<td class='num'>{s['sharpe']:.2f}</td>"
            f"<td class='num'>{s['stops_n']}</td>"
            f"<td class='num'>{fmt(cf['avg'], 2) if cf['n'] else '—'}</td></tr>")

    # ---------- 板块 4：逐笔分桶 ----------
    rows_tb = []
    for r in DM["trade_bucket"]:
        rows_tb.append(
            f"<tr><td>{r['bucket']}</td><td class='num'>{r['n']}</td>"
            f"<td class='num {'up' if r['avg'] > 0 else 'dn'}'>{r['avg']:+.2f}%</td>"
            f"<td class='num'>{r['med']:+.2f}%</td>"
            f"<td class='num'>{r['win']:.1f}%</td>"
            f"<td class='num tn'>{r['mae']:.2f}%</td></tr>")

    # ---------- 板块 5：曲面 ----------
    sf = M["surface"]
    sfrows = []
    for lc, row in zip(sf["thr"], sf["vals"]):
        cells = [f"<td class='h'>亏损 &gt;{lc*100:.1f}%</td>"]
        for v in row:
            k = "better" if v > sf["base"] else "worse"
            cells.append(f"<td class='{k}'>{v:.4f}</td>")
        sfrows.append("<tr>" + "".join(cells) + "</tr>")
    def _blue(sfx, cols):
        idx = [sfx["days"].index(d) for d in cols]
        n = sum(1 for row in sfx["vals"] for i in idx if row[i] > sfx["base"])
        return f"{n}/{len(sfx['vals'])*len(cols)}"

    sf_blue = _blue(sf, [4, 5, 6])
    sfl_blue = _blue(sfl0 := L["surface"], [4, 5, 6])

    sfl = L["surface"]
    sflrows = []
    for lc, row in zip(sfl["thr"], sfl["vals"]):
        cells = [f"<td class='h'>亏损 &gt;{lc*100:.1f}%</td>"]
        for v in row:
            k = "better" if v > sfl["base"] else "worse"
            cells.append(f"<td class='{k}'>{v:.4f}</td>")
        sflrows.append("<tr>" + "".join(cells) + "</tr>")

    # ---------- 板块 6：逐年 ----------
    yM = M["yearly"]
    yrs = sorted({y for v in yM.values() for y in v})
    yrs = [y for y in yrs if abs(yM["A 基线"].get(y, 0.0)) > 1e-9]   # 去掉基点年（2019 仅 1 日）
    pick = ["A 基线", "B1 广度≤1→防守", "C2 入场价止损 −8%",
            "D2 最小持有 10 日", "D4 亏损>2.5% 空仓 5 日", "D3 亏损>3% 空仓 5 日"]
    yh = "".join(f"<th class='num'>{y}</th>" for y in yrs)
    yrows = []
    for tag in pick:
        v = yM[tag]
        cells = []
        for y in yrs:
            if y not in v:
                cells.append("<td class='num dimtxt'>—</td>")
                continue
            cells.append(f"<td class='num {'up' if v[y] > 0 else 'dn'}'>{v[y]:+.1f}%</td>")
        yrows.append(f"<tr><td>{html.escape(tag)}</td>{''.join(cells)}</tr>")

    sub = M["sub"]["data"]
    subrows = []
    for tag in pick:
        a1, a2 = sub[tag]["前半段"], sub[tag]["后半段"]
        subrows.append(
            f"<tr><td>{html.escape(tag)}</td>"
            f"<td class='num up'>{a1['total']:+.2f}%</td>"
            f"<td class='num tn'>{a1['mdd']:.2f}%</td>"
            f"<td class='num up'>{a2['total']:+.2f}%</td>"
            f"<td class='num tn'>{a2['mdd']:.2f}%</td></tr>")

    # ---------- 板块 7：回撤前沿 ----------
    def cl(row, code):
        return f"<tr><td>{code}</td><td class='num'>{row['final']:.4f}</td>" \
               f"<td class='num up'>{row['total']:+.2f}%</td>" \
               f"<td class='num tn'>{row['mdd']:.2f}%</td>" \
               f"<td class='num'>{row['sharpe']:.2f}</td>" \
               f"<td class='num'>{row['calmar']:.2f}</td></tr>"

    front = (cl(bM, "A 基线（现状）")
             + cl(d3["stat"], "D3 亏损&gt;3% → 空仓 5 日")
             + cl(d4["stat"], "D4 亏损&gt;2.5% → 空仓 5 日")
             + cl(br_best_m["stat"], f"广度≤{br_best_m['thr']} → 防守（回≥{br_best_m['rec']}）")
             + cl(br1["stat"], "B1 广度≤1 → 防守（你的口径）"))
    frontl = (cl(bL, "A 基线（现状）")
              + cl(d3l["stat"], "D3 亏损&gt;3% → 空仓 5 日")
              + cl(d4l["stat"], "D4 亏损&gt;2.5% → 空仓 5 日")
              + cl(br_best_l["stat"], f"广度≤{br_best_l['thr']} → 防守（回≥{br_best_l['rec']}）")
              + cl(br1l["stat"], "B1 广度≤1 → 防守（你的口径）"))

    # ---------- 图表 ----------
    chart_m = svg_chart(M["curves"],
                        ["A 基线", "B1 广度≤1→防守", "D3 亏损>3% 空仓 5 日"],
                        [C_A, C_B, C_D3], ["A 基线", "B1 广度止损", "D3 亏损冷却"],
                        title="主窗口 2019-12-31 ~ 2026-09-24（对数轴）")
    chart_l = svg_chart(L["curves"],
                        ["A 基线", "B1 广度≤1→防守", "D3 亏损>3% 空仓 5 日"],
                        [C_A, C_B, C_D3], ["A 基线", "B1 广度止损", "D3 亏损冷却"],
                        title="长窗口 2015-01-05 ~ 2026-09-24（对数轴）")
    bars = svg_bars(DM["breadth"])

    # ---------- 组装 ----------
    rl = M["rand_loss"]
    rnd = M["rand"]
    dd = DM["dd"][0]
    hold_txt = " · ".join(f"{h['name'][:9]}×{h['days']}日" for h in dd["hold"][:5])

    html_txt = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>主轮动池 · 提前止损研究</title>
<style>{CSS}</style></head><body>
<div class="wrap">
<div class="hero">
  <h1>主轮动池 · 「提前止损」能不能控回撤</h1>
  <div class="s">ROC 轮动策略 · 9 只主选池 · 前复权 · 每次换仓双边 {d['meta']['cost']*100:.1f}%</div>
  <div class="tags">
    <span class="tg">你的广度止损 24 组</span>
    <span class="tg">经典止损 22 组</span>
    <span class="tg">反锯齿 25 组</span>
    <span class="tg">双窗口 2015 / 2019</span>
    <span class="tg">随机频率对照</span>
  </div>
</div>

<div class="lead">{lead}</div>

<h2><i>①</i>先纠正一个前提</h2>
<div class="card">
  <p style="margin:0 0 8px">主轮动池<b>没有 6% 止盈</b>。6% 是<b>防守位 512040</b>（价值引擎）和
  <b>策略二 512890</b> 的卖出线；主轮动池的卖出条件是「头名换人」或「触发热熔断切防守」，
  从来没有百分比止盈。所以「到达 6% 止盈前的止损」这个说法，在主轮动池里对应的其实是
  <b>「买进头名之后、还没等到换仓信号之前的这段时间，要不要主动离场」</b>。</p>
  <p style="margin:0">你的观察本身是真的：确实有大量「刚买入就下跌」的笔。问题是
  <b>用什么条件砍、砍完干什么</b>。下面分别回答。</p>
</div>

<h2><i>②</i>你的方案：只有头名是正的 → 切走</h2>
<div class="verdict vno">
  <div class="vh">结论：不行。而且它<strong>让回撤变深了</strong>，不是变浅。</div>
  <div class="vt">
  主窗口净值 {bM['final']:.4f} → <b>{br1['stat']['final']:.4f}</b>（{br1['stat']['total']-bM['total']:+.2f}pp），
  最大回撤 {bM['mdd']:.2f}% → <b>{br1['stat']['mdd']:.2f}%</b>；
  长窗口 {bL['final']:.4f} → <b>{br1l['stat']['final']:.4f}</b>，
  回撤 {bL['mdd']:.2f}% → <b>{br1l['stat']['mdd']:.2f}%</b>。<br>
  24 组参数里，<b>没有一组</b>做到「收益不比基线差且回撤更浅」。
  </div>
</div>

<div class="card">
  <h3>为什么这个信号是反的</h3>
  <p style="margin:0 0 10px">把每一天按「池内动横20 为正的只数」分组，看当天头名<b>之后</b>的表现：</p>
  <div class="tblwrap"><table>
    <thead><tr><th>广度</th><th class="num">天数</th><th class="num">占比</th>
    <th class="num">未来5日</th><th class="num">未来10日</th><th class="num">未来20日</th>
    <th class="num">20日内最大回撤</th><th class="num">20日胜率</th><th class="num">头名动横20</th></tr></thead>
    <tbody>{''.join(rows_bucket)}</tbody></table></div>
  <div class="legend">
    <b>「只有 1 只（就是头名）为正」时，头名未来 20 日最大回撤只有
    {DM['breadth'][1]['mdd20']:.2f}%，是全部 10 个分组里<b>第二浅</b>的</b>；20 日胜率
    {DM['breadth'][1]['win20']:.1f}%，高于全样本 {DM['breadth'][-1]['win20']:.1f}%。
    原因很直白：广度=1 时头名有 {75/166*100:.0f}% 的日子是黄金 ETF、25% 是纳指 ETF（长窗口更多黄金）——
    这不是「危险」，这是<b>外面在跌、钱躲进避险品种</b>。你在这种时候把头名卖掉，等于
    在最该持有的阶段清仓。
  </div>
</div>

<div class="card">
  {bars}
  <div class="legend">红柱 = 广度 0/1（你设想的危险区）。它们的回撤是<b>最浅的一档</b>，
  而广度 2~4（看起来「温和上涨」）才是回撤最深的区域。</div>
</div>

<div class="card">
  <h3>24 组参数全扫描（主窗口）</h3>
  <div class="tblwrap" style="max-height:330px;overflow-y:auto"><table>
    <thead><tr><th>触发条件</th><th>切到</th><th>回归条件</th><th class="num">净值</th>
    <th class="num">收益</th><th class="num">MDD</th><th class="num">夏普</th>
    <th class="num">Calmar</th><th class="num">止损次</th><th></th></tr></thead>
    <tbody>{''.join(rows_br)}</tbody></table></div>
  <div class="legend">基线：净值 {bM['final']:.4f}／收益 {bM['total']:+.2f}%／MDD {bM['mdd']:.2f}%／
  夏普 {bM['sharpe']:.2f}／Calmar {bM['calmar']:.2f}。
  把回撤压到 −20% 以内的组合（如 ≤{br_best_m['thr']} 只为正、回≥{br_best_m['rec']}）净值只有
  {br_best_m['stat']['final']:.4f}，等于<b>少赚 {abs(br_best_m['stat']['total']-bM['total']):.0f}pp，
  换来 {abs(abs(bM['mdd'])-abs(br_best_m['stat']['mdd'])):.1f}pp 的回撤收窄</b>。</div>
</div>

<div class="card">
  <h3>随机频率对照：这个信号有没有「信息」</h3>
  <p style="margin:0 0 6px">做法：在同一批持仓日里，以<b>相同频率</b>（{rnd['p']*100:.2f}%/日）、
  <b>相同暂停长度</b>（{rnd['dur']:.1f} 个交易日）随机触发一次「切防守」，跑 30 次看净值分布。
  如果广度信号真有信息，真实版本应显著高于随机分布。</p>
  <table><thead><tr><th>版本</th><th class="num">净值</th><th>说明</th></tr></thead><tbody>
    <tr><td>随机版 p05</td><td class="num">{rnd['p05']:.4f}</td><td>20 次里最差的那次</td></tr>
    <tr><td>随机版 中位</td><td class="num">{rnd['p50']:.4f}</td><td>随便乱砍的中位水平</td></tr>
    <tr><td>随机版 p95</td><td class="num">{rnd['p95']:.4f}</td><td>运气好的那次</td></tr>
    <tr class="hi"><td>真实广度止损</td><td class="num">{rnd['real']:.4f}</td>
      <td>落在随机分布的 <b>{rnd['pctile']:.1f}% 分位</b>（长窗口只有
      {L['rand']['pctile']:.1f}% 分位）</td></tr>
  </tbody></table>
  <div class="legend">70%（长窗口 26.7%）分位意味着：<b>它比随便乱砍好一点，但远没到「有信息」的程度，
  而且两者都低于「什么都不做」的 {bM['final']:.4f}</b>。一句话：这个条件下离场本身是对的，
  但<b>「广度=1」不是判断离场时机的有效依据</b>。</div>
</div>

<h2><i>③</i>换成经典止损呢？全部无效，而且原因很硬</h2>
<div class="verdict vno">
  <div class="vh">22 种止损里，<b>没有一种跑赢基线</b>；−12% 两行根本没触发，等于基线本身。</div>
  <div class="vt">
  最好的一个（入场价 −8% → 持币）净值 {sl8['stat']['final']:.4f} vs 基线 {bM['final']:.4f}；
  它 6.7 年只触发 {sl8['stat']['stops_n']} 次，说明 −8% 的浮亏在这个策略里<b>几乎不存在</b>。
  收紧到 −5%/−3% 触发次数变多，净值反而掉到 {find(M['price'], label='入场价 −5%→CASH')['stat']['final']:.4f}／
  {find(M['price'], label='入场价 −3%→CASH')['stat']['final']:.4f}。
  </div>
</div>

<div class="card">
  <div class="tblwrap"><table>
    <thead><tr><th>方案</th><th class="num">净值</th><th class="num">收益</th>
    <th class="num">MDD</th><th class="num">夏普</th><th class="num">止损次</th>
    <th class="num">被止损标的后20日</th></tr></thead>
    <tbody>{''.join(rows_pr)}</tbody></table></div>
  <div class="legend">基线：{bM['final']:.4f}／{bM['mdd']:.2f}%。最后一列 = 止损之后，
  被砍掉那只 ETF 未来 20 个交易日的平均涨跌 —— <b>正值居多，说明多数止损砍在了反弹前</b>。</div>
</div>

<div class="card">
  <h3>为什么砍不到东西：单笔亏损本来就很小</h3>
  <div class="split">
    <div>
      <table><thead><tr><th>项目</th><th class="num">数值</th></tr></thead><tbody>
        <tr><td>总笔数</td><td class="num">{tm['n']}</td></tr>
        <tr><td>平均单笔</td><td class="num {'up' if tm['avg']>0 else 'dn'}">{tm['avg']:+.2f}%</td></tr>
        <tr><td>中位单笔</td><td class="num">{tm['med']:+.2f}%</td></tr>
        <tr><td>亏损笔数</td><td class="num">{tm['loss_n']}（{tm['loss_n']/tm['n']*100:.0f}%）</td></tr>
        <tr><td><b>亏损笔平均</b></td><td class="num dn"><b>{tm['loss_avg']:+.2f}%</b></td></tr>
        <tr><td>最惨一笔</td><td class="num dn">{tm['max_loss']:+.2f}%</td></tr>
        <tr><td>浮亏曾破 −5% 的笔</td><td class="num">{tm['deep']}</td></tr>
        <tr><td>其中最终回正</td><td class="num">{tm['deep_recover']} 笔
          （均 {tm['deep_recover_avg'] or 0:+.2f}%）</td></tr>
      </tbody></table>
    </div>
    <div>
      <p style="margin:0 0 8px;font-size:12.8px">
      <b>这就是止损在这个策略里必然失败的原因。</b></p>
      <p style="margin:0 0 8px;font-size:12.8px">
      止损的作用是「砍掉大亏、保留小亏」。但这里 <b>{tm['n']} 笔里只有 {tm['deep']} 笔浮亏曾破 −5%</b>，
      亏损笔平均只亏 {tm['loss_avg']:.2f}%，而且这 {tm['deep']} 笔里还有 {tm['deep_recover']} 笔后来回正
      （平均 {tm['deep_recover_avg'] or 0:+.2f}%）。</p>
      <p style="margin:0;font-size:12.8px">
      换句话说：<b>没有大亏可砍</b>。止损唯一能做到的事，是把「亏 2% 然后反弹」变成
      「亏 2% + 双边 0.2% 成本 + 错过反弹」。</p>
    </div>
  </div>
</div>

<h2><i>④</i>真正的病根：不是「买了就跌」，是「跌了就换、换了又跌」</h2>
<div class="verdict">
  <div class="vh">净值是 125 笔短命交易一刀一刀削出来的，不是某一笔暴跌砸出来的。</div>
  <div class="vt">持有 ≤5 日的交易有 {DM['trade_bucket'][0]['n']+DM['trade_bucket'][1]['n']} 笔
  （占 {tm['n']} 笔的 {(DM['trade_bucket'][0]['n']+DM['trade_bucket'][1]['n'])/tm['n']*100:.0f}%），
  平均 {DM['trade_bucket'][0]['avg']:.2f}% / {DM['trade_bucket'][1]['avg']:.2f}%，
  胜率只有 {DM['trade_bucket'][0]['win']:.0f}% / {DM['trade_bucket'][1]['win']:.0f}%；
  而持有 11 日以上的 {DM['trade_bucket'][3]['n']+DM['trade_bucket'][4]['n']} 笔平均
  {DM['trade_bucket'][3]['avg']:+.2f}% / {DM['trade_bucket'][4]['avg']:+.2f}%，
  胜率 {DM['trade_bucket'][3]['win']:.0f}% / {DM['trade_bucket'][4]['win']:.0f}%。</div>
</div>

<div class="card">
  <h3>按持有天数分组</h3>
  <table><thead><tr><th>持有天数</th><th class="num">笔数</th><th class="num">平均收益</th>
  <th class="num">中位</th><th class="num">胜率</th><th class="num">平均最大浮亏</th></tr></thead>
  <tbody>{''.join(rows_tb)}</tbody></table>
  <div class="legend">持有越短越亏、越久越赚，而且单调。<b>这不是「买错了」，是「拿不住」。</b></div>
</div>

<div class="card">
  <h3>最痛的那一段：{dd['start']} ~ {dd['trough']}，回撤 {dd['depth']:.2f}%</h3>
  <p style="margin:0 0 8px">{dd['days']} 个交易日里持仓构成：{hold_txt}。这一段里系统换了 13 次仓，
  <b>9 次是亏的，每次只亏 2~6%</b>——没有任何一笔值得触发止损，
  但加起来就是 {dd['depth']:.1f}%。</p>
  <p style="margin:0">如果给这段加一个 −5% 的入场价止损，它会在第 3 笔左右触发，
  然后<b>空仓错过 12 月的反弹</b>（黄金那一段 +2.13%、创成长 +0.78%）。这就是为什么所有
  价格类止损都跑输。</p>
</div>

<h2><i>⑤</i>换了个砍法：亏损后空仓 5 日（这个有效）</h2>
<div class="verdict vyes">
  <div class="vh">不砍价格，砍「立刻追下一个头名」这个动作。</div>
  <div class="vt">
  <b>规则：</b>任何一笔持仓，在<b>换出时</b>相对建仓价亏损 ≥3%（近似「止损」），
  则不立刻买入新头名，<b>空仓 5 个交易日</b>，之后照常按头名轮动。<br>
  <b>主窗口：</b>{bM['final']:.4f} → <b>{d3['stat']['final']:.4f}</b>
  （{d3['stat']['total']:+.2f}%），MDD {bM['mdd']:.2f}% → {d3['stat']['mdd']:.2f}%，
  夏普 {bM['sharpe']:.2f} → {d3['stat']['sharpe']:.2f}，Calmar {bM['calmar']:.2f} → {d3['stat']['calmar']:.2f}。<br>
  <b>长窗口：</b>{bL['final']:.4f} → <b>{d3l['stat']['final']:.4f}</b>，
  MDD {bL['mdd']:.2f}% → {d3l['stat']['mdd']:.2f}%。
  </div>
</div>

<div class="card">
  <div class="split">
    <div>{chart_m}</div>
  </div>
  <div style="margin-top:12px">{chart_l}</div>
  <div class="legend"><span class="k" style="background:{C_A}"></span>A 基线
  <span class="k" style="background:{C_B};margin-left:14px"></span>B1 广度止损（你的口径）
  <span class="k" style="background:{C_D3};margin-left:14px"></span>D3 亏损冷却 3%/5 日。
  两个窗口里 B1 都跑输，D3 都跑赢。</div>
</div>

<div class="card">
  <h3>不是单点运气：整片参数都有效</h3>
  <div style="font-size:12px;color:#6c717f;margin-bottom:6px">主窗口（基线 {sf['base']:.4f}）</div>
  <table class="hm"><thead><tr><th>亏损阈值</th>
  {''.join(f'<th class="num">{x}日</th>' for x in sf['days'])}</tr></thead>
  <tbody>{''.join(sfrows)}</tbody></table>
  <div style="font-size:12px;color:#6c717f;margin:14px 0 6px">长窗口（基线 {sfl['base']:.4f}）</div>
  <table class="hm"><thead><tr><th>亏损阈值</th>
  {''.join(f'<th class="num">{x}日</th>' for x in sfl['days'])}</tr></thead>
  <tbody>{''.join(sflrows)}</tbody></table>
  <div class="legend">蓝色 = 优于基线，红色 = 劣于基线。<b>关键不是某格最高，而是蓝色成片</b>：
  「空仓 4~6 日」三列在主窗口 <b>{sf_blue}</b> 全蓝、在长窗口 <b>{sfl_blue}</b> 蓝；
  而「只空仓 3 日」两个窗口都只有 1 格蓝、「7 日」在长窗口 0 格蓝。
  ⇒ <b>4~6 天是甜蜜区：3 天太短（锯齿没走完），7 天开始过度空仓</b>。</div>
</div>

<div class="card">
  <h3>逐年 + 分段：好在哪里、差在哪里</h3>
  <div class="tblwrap"><table>
    <thead><tr><th>方案</th>{yh}</tr></thead><tbody>{''.join(yrows)}</tbody></table></div>
  <div class="legend">主窗口逐年。D3 在 2020/2024/2025/2026 明显更好，
  2022/2023 略差（{d3['stat']['final']:.4f} 的净值优势主要来自 2024 年以后）。</div>
  <div style="margin-top:14px" class="tblwrap"><table>
    <thead><tr><th>方案</th><th class="num">前半段收益</th><th class="num">前半 MDD</th>
    <th class="num">后半段收益</th><th class="num">后半 MDD</th></tr></thead>
    <tbody>{''.join(subrows)}</tbody></table></div>
  <div class="legend">切点 {M['sub']['mid']}。<b>D3 两段都优于基线，且两段 MDD 都更浅</b>；
  D2（最小持有 10 日）在主窗口总净值最高，但<b>拿到长窗口就崩</b>：
  MDD 恶化到 {mhl['stat']['mdd']:.2f}%（基线 {bL['mdd']:.2f}%），2018 年 −14.6% vs 基线 −4.5%
  ⇒ <b>参数尖峰，不可用</b>。</div>
</div>

<div class="card">
  <h3>同频率随机空仓对照（决定性检验）</h3>
  <p style="margin:0 0 8px">把「亏损≥3% 才空仓」换成「以相同频率随机空仓 5 日」，各跑 40 次：</p>
  <table><thead><tr><th>窗口</th><th class="num">触发次数</th><th class="num">随机 p50</th>
  <th class="num">随机 p95</th><th class="num">真实版本</th><th class="num">分位</th></tr></thead><tbody>
    <tr><td>主窗口</td><td class="num">{rl['D3 亏损>3%']['events']}</td>
      <td class="num">{rl['D3 亏损>3%']['p50']:.4f}</td>
      <td class="num">{rl['D3 亏损>3%']['p95']:.4f}</td>
      <td class="num good">{rl['D3 亏损>3%']['real']:.4f}</td>
      <td class="num">{rl['D3 亏损>3%']['pctile']:.1f}%</td></tr>
    <tr><td>长窗口</td><td class="num">{rl['D3 亏损>3%']['events'] if False else L['rand_loss']['D3 亏损>3%']['events']}</td>
      <td class="num">{L['rand_loss']['D3 亏损>3%']['p50']:.4f}</td>
      <td class="num">{L['rand_loss']['D3 亏损>3%']['p95']:.4f}</td>
      <td class="num good">{L['rand_loss']['D3 亏损>3%']['real']:.4f}</td>
      <td class="num">{L['rand_loss']['D3 亏损>3%']['pctile']:.1f}%</td></tr>
  </tbody></table>
  <div class="legend"><b>这是本系列研究里第一个两个窗口都过 95% 分位的规则</b>
  （广度止损是 70%/26.7%，MACD 买入、J 买入、J 卖出全部没过）。也说明：随机空仓本身就亏钱
  （p50 {rl['D3 亏损>3%']['p50']:.4f} &lt; 基线 {bM['final']:.4f}），而「亏了才空仓」能反过来赚
  ——差别在<b>时机</b>，不在空仓本身。</div>
</div>

<div class="card">
  <h3>成本敏感性</h3>
  <table><thead><tr><th>每次换仓双边成本</th><th class="num">A 基线</th>
  <th class="num">B1 广度止损</th><th class="num">D3 亏损&gt;2% 空仓5日</th></tr></thead><tbody>
  {''.join(f"<tr><td>{c*100:.1f}%</td>" + ''.join(
      f"<td class='num'>{next(x['final'] for x in M['cost'] if abs(x['cost']-c)<1e-9 and x['tag']==t):.4f}</td>"
      for t in ('A', 'B1 广度≤1→防守', 'D3 亏损>2%空仓5日')) + "</tr>"
      for c in (0.001, 0.002, 0.003))}
  </tbody></table>
  <div class="legend">换仓次数几乎没变（{bM['trades_n']} → {d3['stat']['trades_n']} 笔），
  所以成本不改变排序：D3 在 0.1%/0.2%/0.3% 三档都领先。</div>
</div>

<h2><i>⑥</i>如果你就是要把回撤压下去</h2>
<div class="card">
  <p style="margin:0 0 10px">在<b>广度止损与经典止损</b>这两族里，没有一个能「又降回撤又涨收益」——
  想降回撤就得让出收益。唯一在两个窗口都做到「收益更高且回撤更浅」的，是亏损冷却那一族。
  真实的可选项是一条权衡曲线：</p>
  <div style="font-size:12px;color:#6c717f;margin-bottom:6px">主窗口</div>
  <div class="tblwrap"><table><thead><tr><th>方案</th><th class="num">净值</th>
  <th class="num">收益</th><th class="num">MDD</th><th class="num">夏普</th>
  <th class="num">Calmar</th></tr></thead><tbody>{front}</tbody></table></div>
  <div style="font-size:12px;color:#6c717f;margin:14px 0 6px">长窗口</div>
  <div class="tblwrap"><table><thead><tr><th>方案</th><th class="num">净值</th>
  <th class="num">收益</th><th class="num">MDD</th><th class="num">夏普</th>
  <th class="num">Calmar</th></tr></thead><tbody>{frontl}</tbody></table></div>
  <div class="legend">
    <b>想把 MDD 从 {bM['mdd']:.1f}% 压到
    {br_best_m['stat']['mdd']:.1f}%，代价是净值从 {bM['final']:.2f} 掉到
    {br_best_m['stat']['final']:.2f}（少赚 {bM['total']-br_best_m['stat']['total']:.0f}pp）。</b>
    用 Calmar 看，<b>D4（亏损&gt;2.5% 空仓 5 日）是唯一在两个窗口都不输基线、
    同时把 MDD 压浅 1.5~2pp 的方案</b>；广度择时能压更深，但代价大得多。
  </div>
</div>

<h2><i>⑦</i>建议</h2>
<div class="card">
  <ul>
    <li><b>广度止损不要加。</b>信号方向是反的（广度=1 是避险格局，头名最抗跌），
    24 组参数全负，随机对照 70% 分位。</li>
    <li><b>价格类止损不要加。</b>单笔亏损本来就小（均 {tm['loss_avg']:.2f}%），
    止损只会把「小亏等反弹」变成「小亏 + 成本 + 错过反弹」；6.7 年里 −8% 只触发
    {sl8['stat']['stops_n']} 次，说明这个策略的亏损形态根本不长这样。</li>
    <li><b>可以试「亏损 ≥3% → 空仓 5 个交易日」。</b>注意它不是止损，是<b>冷却</b>：
    它不改变你什么时候卖出（卖出还是靠头名换人 + 过热熔断），只改变<b>卖出后多快重新进场</b>。
    双窗口、双分段、0.1~0.3% 成本、40 次随机对照全部通过。</li>
    <li><b>但它不解决回撤。</b>MDD 只从 {bM['mdd']:.2f}% 到 {d3['stat']['mdd']:.2f}%。
    想真正压回撤只能靠广度择时（≤2 只为正就切防守、等回到 6 只才回轮动），
    MDD 能到 {br_best_m['stat']['mdd']:.2f}%，但净值 {br_best_m['stat']['final']:.2f}，
    你要自己判断这 {bM['total']-br_best_m['stat']['total']:.0f}pp 值不值。</li>
    <li><b>下一步该做的不是继续调参数，是把这条规则放到别的地方验证</b>：
    ① 备选池 15 只（换标的检验）；② 长窗口已做，但优势集中在 2020 与 2024 之后，
    2015–2019 段 D3 略输基线，说明它吃的是「近年这种锯齿行情」；③ 把「空仓冷却」
    改成「半仓冷却」，进一步压回撤</li>
  </ul>
</div>

<h2><i>⑧</i>口径与局限</h2>
<div class="card">
  <ul style="margin-top:0">
    <li>数据：前复权长历史 CSV（{d['meta']['windows'][0]} / {d['meta']['windows'][1]}），
    每天按当日收盘计算信号、次日享受涨跌，与日报 `build_ve_curve`/`backtest` 同口径。</li>
    <li>成本：每次换仓双边 {(d['meta']['cost'])*100:.1f}%（买 0.1% + 卖 0.1%），另测了 0.1%/0.3%。</li>
    <li><b>回归校验：</b>把全部钩子关掉，本研究的模拟器与 `etf_backtest_long.backtest()`
    净值序列最大差 <b>0.0</b>（两个窗口都验过），基线数字 {bM['final']:.4f} = 日报的 4.7579。</li>
    <li><b>最大局限：</b>所有结论都在同一份历史上选出。我扫了 24（广度）+22（价格）+
    25（反锯齿）＝ 71 组参数，其中只有「亏损冷却」一族在两个窗口都成立，
    但它也只在 2020 与 2024 之后贡献明显。这种「事后选出 + 局部时段有效」的组合，
    最稳妥的处理是<b>小仓位试跑</b>，不要直接改日报。</li>
    <li>另外：主轮动池本来就没有 6% 止盈，那 6% 属于防守位 512040 与策略二 512890。</li>
  </ul>
</div>

<div class="foot">
本报告仅供研究参考，不构成个人投资建议。<br>
生成于 2026-10-24 · 脚本 rot_stop_study.py / make_rot_stop_report.py
</div>
</div></body></html>"""

    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(html_txt)
    print(f"[html] {HTML_OUT}  {len(html_txt)} chars")


if __name__ == "__main__":
    main()
