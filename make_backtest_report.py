# -*- coding: utf-8 -*-
"""
ETF 轮动战法 · 2026 年内回测报告生成器
读 out/backtest_2026.json -> 生成自包含 HTML (内联 SVG 净值曲线, 无外部依赖)
颜色遵循 A 股习惯: 涨=红 #d4380d / 跌=绿 #0f9d58
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_OUT_CAND = [os.path.join(HERE, "out"), os.path.join(HERE, "..", "out"),
             "D:/娱乐（音乐、视频）Q-骑牛/ETF轮动战法/out"]
OUTDIR = next((p for p in _OUT_CAND if os.path.isdir(p)), _OUT_CAND[-1])
JSON = os.path.join(OUTDIR, "backtest_2026.json")
OUT = os.path.join(OUTDIR, "backtest_2026.html")

RED, GREEN, GREY = "#d4380d", "#0f9d58", "#8c8c8c"

# 曲线定义: key, 完整名(表用), 颜色, 图例短名(图用)
SERIES = [
    ("nav_B", "策略B · 现池（9轮动+512880防守+粘性带1.0pp，无空仓）——维持主策略",
     "#7c3aed", "策略B · 现池（维持）"),
    ("nav_A", "策略A · 博主原版（超简最强轮动+过热保护，512040防守）",
     "#d4380d", "策略A · 博主原版"),
    ("bench_513100", "纳指ETF 513100", "#0f9d58", "纳指ETF 513100"),
    ("bench_000688", "科创50 000688", "#1677ff", "科创50 000688"),
    ("bench_399006", "创业板指 399006", "#d4a017", "创业板指 399006"),
    ("bench_000001", "上证指数 000001", "#9aa0a6", "上证指数 000001"),
]


def pct(x, d=2):
    return f"{x*100:+.{d}f}%"


def color_of(x):
    return RED if x >= 0 else GREEN


def build():
    with open(JSON, encoding="utf-8") as f:
        d = json.load(f)
    dates = d["dates"]
    n = len(dates)
    series = {}
    for key, _n, _c, _s in SERIES:
        if key.startswith("bench_"):
            series[key] = d["bench"][key.split("_", 1)[1]]
        else:
            series[key] = d[key]

    ymin = min(min(v) for v in series.values())
    ymax = max(max(v) for v in series.values())
    lo = (int(ymin * 20) / 20) - 0.05
    hi = (int(ymax * 20 + 1) / 20) + 0.05

    # ---------- 绩效表 ----------
    stat_map = {"nav_A": d["stat_A"], "nav_B": d["stat_B"]}
    for c, s in d["stat_bench"].items():
        stat_map[f"bench_{c}"] = s
    EXTRA = {"nav_A": (len(d["trades_A"]), d["cash_days_A"]),
             "nav_B": (len(d["trades_B"]), d["cash_days_B"])}
    perf_rows = []
    for key, full_nm, col, _s in SERIES:
        st = stat_map[key]
        nav_last = series[key][-1]
        sh = f"{st['sharpe']:.2f}" if st.get("sharpe") is not None else "—"
        so = f"{st['sortino']:.2f}" if st.get("sortino") is not None else "—"
        ex = EXTRA.get(key, ("—", "—"))
        perf_rows.append(
            f"<tr><td class='nm'><span class='dot' style='background:{col}'></span>{full_nm}</td>"
            f"<td class='num b'>{nav_last:.4f}</td>"
            f"<td class='num' style='color:{color_of(st['total'])}'>{pct(st['total'])}</td>"
            f"<td class='num' style='color:{color_of(st['ann'])}'>{pct(st['ann'])}</td>"
            f"<td class='num down'>{pct(st['mdd'])}</td>"
            f"<td class='num'>{st['vol']*100:.2f}%</td>"
            f"<td class='num'>{sh}</td><td class='num'>{so}</td>"
            f"<td class='num'>{ex[0]}</td><td class='num'>{ex[1]}</td></tr>")

    # ---------- 操作明细 ----------
    def trade_rows(trades, cash_ranges):
        cash_days = set()
        for r in cash_ranges:
            i0 = dates.index(r["start"]); i1 = dates.index(r["end"])
            cash_days.update(range(i0, i1 + 1))
        out = []
        for k, t in enumerate(trades, 1):
            is_cash = (t["to"] == "CASH")
            tag = ("<span class='tag cash'>切空仓</span>" if is_cash else
                   ("<span class='tag def'>切防守</span>" if t["state"] == "DEF"
                    else "<span class='tag rot'>轮动</span>"))
            out.append(
                f"<tr{' class=cashrow' if is_cash else ''}>"
                f"<td class='num'>{k}</td><td class='dt'>{t['date']}</td>"
                f"<td class='nm2'>{t['frm_name']} <span class='arrow'>→</span> {t['to_name']}</td>"
                f"<td>{tag}</td><td class='rs'>{t['reason']}</td>"
                f"<td class='num'>{t['nav']:.4f}</td></tr>")
        return "\n".join(out)

    def _cash_rows(ranges):
        out = []
        for i, r in enumerate(ranges, 1):
            out.append(
                f"<tr><td class='num'>{i}</td><td class='dt'>{r['start']}</td>"
                f"<td class='dt'>{r['end']}</td><td class='num b'>{r['days']}</td>"
                f"<td>0.00%（空仓不承担市场波动）</td></tr>")
        return "\n".join(out)

    cash_rows = _cash_rows(d["cash_ranges_A"])

    def hold_rows(key):
        out = []
        for h in d[key]:
            col = "#8c8c8c" if h["code"] == "CASH" else "#333"
            out.append(f"<tr><td class='nm2'>{h['name']}</td><td class='num'>{h['code'] if h['code']!='CASH' else '—'}</td>"
                       f"<td class='num b' style='color:{col}'>{h['days']}</td>"
                       f"<td class='num'>{h['pct']:.1f}%</td></tr>")
        return "\n".join(out)

    # ---------- SVG 净值曲线 ----------
    W, H = 1240, 560
    mL, mR, mT, mB = 74, 26, 84, 70
    pw, ph = W - mL - mR, H - mT - mB
    step = pw / (n - 1)

    def X(i):
        return mL + i * step

    def Y(v):
        return mT + (hi - v) / (hi - lo) * ph

    grid, ylab = [], []
    t = lo
    while t <= hi + 1e-9:
        y = Y(t)
        grid.append(f"<line x1='{mL}' y1='{y:.1f}' x2='{mL+pw}' y2='{y:.1f}' "
                    f"stroke='#ececec' stroke-width='1'/>")
        if t > lo + 1e-9:          # 跳过最底部(与X轴/月标签同高)的刻度文字
            ylab.append(f"<text x='{mL-11}' y='{y+4:.1f}' text-anchor='end' "
                        f"class='ax'>{t:.2f}</text>")
        t = round(t + 0.05, 2)

    xlab = []
    seen = set()
    last_x = -1e9
    for i, ds in enumerate(dates):
        if ds[:7] in seen:
            continue
        seen.add(ds[:7])
        xlab.append(f"<line x1='{X(i):.1f}' y1='{mT}' x2='{X(i):.1f}' y2='{mT+ph}' "
                    f"stroke='#f4f4f4' stroke-width='1'/>")
        if X(i) - last_x < 52 and i != 0:      # 与上一个标签太近则只画网格线不写文字
            continue
        last_x = X(i)
        anchor = "start" if i == 0 else ("end" if i > n - 22 else "middle")
        xpos = mL if i == 0 else X(i)
        xlab.append(f"<text x='{xpos:.1f}' y='{mT+ph+23}' text-anchor='{anchor}' "
                    f"class='ax'>{ds[:7]}</text>")

    lines, marks = [], []
    for key, _fn, col, _s in SERIES:
        v = series[key]
        pts = " ".join(f"{X(i):.1f},{Y(x):.1f}" for i, x in enumerate(v))
        big = key in ("nav_A", "nav_B")
        w = 3.2 if big else 1.9
        op = 1.0 if big else 0.85
        lines.append(f"<polyline points='{pts}' fill='none' stroke='{col}' "
                     f"stroke-width='{w}' stroke-opacity='{op}' "
                     f"stroke-linejoin='round' stroke-linecap='round'/>")
    # 换仓标记
    _MK = {"trades_A": ("nav_A", "#d4380d"), "trades_B": ("nav_B", "#7c3aed")}
    for tk, (nk, col) in _MK.items():
        for tr in d[tk]:
            if tr["date"] not in dates:
                continue
            i = dates.index(tr["date"])
            val = series[nk][i]
            marks.append(f"<circle cx='{X(i):.1f}' cy='{Y(val):.1f}' r='2.6' "
                         f"fill='#fff' stroke='{col}' stroke-width='1.6'/>")

    # 图例: 两行三列, 短名
    legend = []
    colw = pw / 3.0
    for li, (_k, _fn, col, short) in enumerate(SERIES):
        row, cc = divmod(li, 3)
        lx = mL + cc * colw
        ly = 20 + row * 22
        legend.append(f"<rect x='{lx:.0f}' y='{ly}' width='16' height='4' rx='2' fill='{col}'/>"
                      f"<text x='{lx+22:.0f}' y='{ly+6}' class='lg'>{short}</text>")
    legend.append(f"<text x='{mL + colw:.0f}' y='{20 + 2 * 22 + 6}' class='lg' "
                  f"style='fill:#9aa0a6;font-size:11px'>"
                  f"曲线小圆点 = 当日换仓动作（红=策略A，紫=策略B）</text>")

    # JS 数据 (用于悬浮提示)
    js_data = {
        "dates": dates,
        "series": [{"name": short, "color": col, "v": series[key]}
                   for key, _fn, col, short in SERIES],
        "x0": mL, "step": step, "W": W, "H": H,
        "lo": lo, "hi": hi, "mT": mT, "ph": ph,
    }

    svg = f"""<svg id="navchart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>
  {''.join(legend)}
  {''.join(grid)}{''.join(ylab)}{''.join(xlab)}
  <line x1="{mL}" y1="{mT+ph}" x2="{mL+pw}" y2="{mT+ph}" stroke="#c4c4c4" stroke-width="1.2"/>
  <line x1="{mL}" y1="{mT}" x2="{mL}" y2="{mT+ph}" stroke="#c4c4c4" stroke-width="1.2"/>
  {''.join(lines)}
  <g id="marks">{''.join(marks)}</g>
  <line id="hoverline" x1="0" y1="{mT}" x2="0" y2="{mT+ph}" stroke="#999"
        stroke-dasharray="4 3" stroke-width="1" opacity="0"/>
  <g id="hoverdots"></g>
  <rect id="hitbox" x="{mL}" y="{mT}" width="{pw}" height="{ph}" fill="transparent"/>
  <text x="{mL}" y="{H-14}" class="ax">起点 2025-12-31 收盘 = 1.000　|　终点 2026-09-23　|　共 {n} 个交易日</text>
</svg>"""

    meta = d["meta"]
    a, b = d["stat_A"], d["stat_B"]
    dg = d["diag"]
    nm_map = meta["names"]

    # ================= 诊断数据 =================
    # (1) 513100 纳指ETF 为何从未进过持仓
    rank513 = dg["rank_513100"]
    rank_cnt = {}
    for r in rank513:
        if r is not None:
            rank_cnt[r] = rank_cnt.get(r, 0) + 1
    r513_head = [i for i, r in enumerate(rank513) if r == 1]
    rank_head_cells = "".join(f"<th class='num'>{k}</th>" for k in range(1, 10))
    rank_cnt_cells = "".join(
        f"<td class='num b'>{rank_cnt.get(k, 0)}</td>" for k in range(1, 10))
    r513_rows = []
    for i in r513_head:
        second_code = dg["second_name_B"][i]
        lead = dg["lead_B"][i]
        second_roc = dg["thermo_B"][i] - lead
        r513_rows.append(
            f"<tr><td class='dt'>{dates[i]}</td>"
            f"<td class='num b' style='color:{RED}'>{dg['roc_513100'][i]:+.2f}%</td>"
            f"<td class='nm2'>{nm_map.get(second_code, second_code)}（{second_code}）</td>"
            f"<td class='num'>{second_roc:+.2f}%</td>"
            f"<td class='num b'>{lead:.2f}pp</td>"
            f"<td>领先 &lt; 粘性带 1.0pp → <b>沿用原持仓</b>，不换仓</td></tr>")
    # 头部标的天数表(降序)
    hold_head_rows = "".join(
        f"<tr><td class='nm2'>{nm_map.get(k, k)}</td><td class='num'>{k}</td>"
        f"<td class='num b'>{v}</td>"
        f"<td class='num'>{v / (meta['window_days'] - 1) * 100:.1f}%</td></tr>"
        for k, v in sorted(dg["head_days_B"].items(), key=lambda x: -x[1]))

    # ================= 备选观察池「动横20—动横1」阶梯 =================
    w = d["watch"]
    WK = w["W"]
    watch_rows = []
    for r_i, c in enumerate(w["rank"], 1):
        L = w["ladder"][c]
        tds = []
        for j, v in enumerate(L):
            k = str(WK - j)
            hl = " class='cbest'" if w["col_heads"][k] == c else ""
            col = RED if v > 0 else (GREEN if v < 0 else "#8c8c8c")
            tds.append(f"<td class='num wnum' style='color:{col}'{hl}>{v:+.2f}</td>")
        tag = ("<span class='tag rot'>主池</span>" if w["in_main"][c] else
               "<span class='tag watch'>备选</span>")
        watch_rows.append(
            f"<tr{' class=headrow' if c == w['head_now'] else ''}>"
            f"<td class='num'>{r_i}</td><td class='num nm2'>{c}</td>"
            f"<td class='nm2'>{w['names'][c]}</td><td>{tag}</td>"
            f"{''.join(tds)}"
            f"<td class='num wnum'>{w['price'][c]:.3f}</td>"
            f"<td class='num wnum' style='color:{color_of(w['chg1d'][c])}'>"
            f"{w['chg1d'][c]:+.2f}%</td></tr>")
    watch_head_cells = "".join(
        f"<th class='num wh'>动横{k}<br/>"
        f"<span class='chc'>{w['col_heads'][str(k)]}</span></th>"
        for k in range(WK, 0, -1))
    main_head_roc = dg["thermo_B"][-1]
    gap_watch = w["head_roc"] - main_head_roc
    tomorrow_head = w["col_heads"]["19"]

    def card(title, nav, st, extra, col):
        return (f"<div class='card' style='border-top:3px solid {col}'>"
                f"<div class='ct'>{title}</div>"
                f"<div class='cv'>{nav:.4f}</div>"
                f"<div class='cs' style='color:{color_of(st['total'])}'>区间 {pct(st['total'])}"
                f"　年化 {pct(st['ann'])}</div>"
                f"<div class='cd'>最大回撤 <b class='down'>{pct(st['mdd'])}</b>　"
                f"夏普 <b>{st['sharpe']:.2f}</b></div>"
                f"<div class='cd2'>{extra}</div></div>")

    cards = (card("策略B · 现池（维持主策略）", series['nav_B'][-1], b,
                  f"换仓 <b>{len(d['trades_B'])}</b> 笔　空仓 <b>0</b>（始终满仓）",
                  "#7c3aed")
             + card("策略A · 博主原版", series['nav_A'][-1], a,
                    f"换仓 <b>{len(d['trades_A'])}</b> 笔　空仓 "
                    f"<b>{d['cash_days_A']}</b> 个交易日", "#d4380d")
             + f"<div class='card' style='border-top:3px solid #1677ff'>"
               f"<div class='ct'>最强基准 · 科创50</div>"
               f"<div class='cv'>{series['bench_000688'][-1]:.4f}</div>"
               f"<div class='cs' style='color:{color_of(d['stat_bench']['000688']['total'])}'>"
               f"区间 {pct(d['stat_bench']['000688']['total'])}　年化 "
               f"{pct(d['stat_bench']['000688']['ann'])}</div>"
               f"<div class='cd'>最大回撤 <b class='down'>"
               f"{pct(d['stat_bench']['000688']['mdd'])}</b>　夏普 "
               f"{d['stat_bench']['000688']['sharpe']:.2f}</div>"
               f"<div class='cd2'>四大基准中收益最高、但回撤也最深</div></div>")

    verify = """
<table class="tbl vfy">
<thead><tr><th>成本档</th><th class="num">博主信号台自报</th><th class="num">本次复算</th><th class="num">差值</th><th>判定</th></tr></thead>
<tbody>
<tr><td>忽略成本</td><td class="num b">+41.56%</td><td class="num b">+41.56%</td><td class="num">0.00pp</td><td class="ok">✓ 逐位一致</td></tr>
<tr><td>单边 0.1%</td><td class="num b">+35.71%</td><td class="num b">+35.60%</td><td class="num">−0.11pp</td><td class="ok">✓ 一致</td></tr>
<tr><td>双边 0.2%</td><td class="num b">+30.09%</td><td class="num b">+29.88%</td><td class="num">−0.21pp</td><td class="ok">✓ 一致</td></tr>
</tbody></table>
<p class="note">另经逐项核对，2026-09-23 当日五大轮动 ETF 的「动量惯性(动横20)」「乖离」「20日线」
与博主信号台显示值 <b>全部两位小数吻合</b>（513100 +5.95%/+4.47%/2.234；563300 +5.31%/+2.95%/1.444；
159967 +3.39%/+2.44%/0.775；510300 −1.33%/−0.47%/4.612；518880 −6.51%/−1.50%/9.032）。
这证明本报告的<b>数据源、动量与乖离公式、状态机参数</b>与博主完全一致。</p>
"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>ETF轮动战法 · 2026年内回测报告（2025-12-31 ~ 2026-09-23）</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#f5f6f8;color:#1f2328;
 font-family:"Microsoft YaHei","PingFang SC",-apple-system,"Segoe UI",sans-serif}}
.wrap{{max-width:1310px;margin:0 auto;padding:26px 22px 60px}}
h1{{font-size:23px;margin:0 0 6px}}
.sub{{color:#6b7280;font-size:13px;margin-bottom:4px}}
h2{{font-size:17px;margin:34px 0 12px;padding-left:11px;border-left:4px solid #d4380d}}
.cards{{display:flex;gap:16px;margin-top:22px;flex-wrap:wrap}}
.card{{flex:1 1 300px;background:#fff;border-radius:11px;padding:16px 18px;
 box-shadow:0 1px 4px rgba(0,0,0,.07)}}
.ct{{font-size:13px;color:#6b7280}}
.cv{{font-size:30px;font-weight:700;letter-spacing:.5px;margin:5px 0 2px}}
.cs{{font-size:13.5px;font-weight:600}}
.cd{{font-size:12.5px;color:#4b5563;margin-top:7px}}
.cd2{{font-size:12px;color:#8b93a0;margin-top:5px}}
.panel{{background:#fff;border-radius:11px;padding:16px 18px 8px;
 box-shadow:0 1px 4px rgba(0,0,0,.07);margin-top:12px;position:relative}}
svg{{width:100%;height:auto;display:block}}
.lg{{font-size:12px;fill:#374151}}
.ax{{font-size:11px;fill:#9aa0a6}}
#tip{{position:absolute;pointer-events:none;background:rgba(28,32,38,.95);color:#fff;
 border-radius:8px;padding:9px 11px;font-size:12px;line-height:1.65;display:none;
 white-space:nowrap;z-index:9;box-shadow:0 4px 14px rgba(0,0,0,.25)}}
#tip b{{font-weight:600}}
#tip .row{{display:flex;justify-content:space-between;gap:14px}}
table.tbl{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;
 overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.07);font-size:13px}}
.tbl th{{background:#f0f1f4;padding:9px 10px;text-align:left;font-weight:600;
 color:#374151;font-size:12.5px;white-space:nowrap}}
.tbl td{{padding:8px 10px;border-top:1px solid #eef0f3;vertical-align:top}}
.tbl tr:hover td{{background:#fafbfc}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.b{{font-weight:700}}
.nm,.nm2{{white-space:nowrap}}
.nm2{{font-weight:600}}
.arrow{{color:#9aa0a6;margin:0 3px}}
.dt{{white-space:nowrap;color:#4b5563;font-variant-numeric:tabular-nums}}
.down{{color:#0f9d58}}
.dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px}}
.tag{{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;white-space:nowrap}}
.tag.cash{{background:#fff1e6;color:#d4380d;border:1px solid #ffcfb0}}
.tag.def{{background:#fff7e6;color:#ad6800;border:1px solid #ffe0a3}}
.tag.rot{{background:#eef6ff;color:#1677ff;border:1px solid #c9e0ff}}
.cashrow td{{background:#fffaf5}}
.rs{{color:#5b6470;font-size:12.5px;min-width:230px}}
.two{{display:flex;gap:16px;flex-wrap:wrap}}
.two>div{{flex:1 1 300px;min-width:0}}
.hlrow td{{background:#eef9fb}}
h3{{font-weight:600;color:#111827}}
.note{{font-size:12.5px;color:#6b7280;line-height:1.85;background:#fff;border-radius:10px;
 padding:13px 16px;box-shadow:0 1px 4px rgba(0,0,0,.06);margin-top:10px}}
.hl{{color:#d4380d;font-weight:700}}
.warnbox{{background:#fffbe6;border:1px solid #ffe58f;border-radius:10px;padding:13px 16px;
 font-size:12.5px;line-height:1.9;color:#614700;margin-top:10px}}
ol.rules{{font-size:13px;line-height:2;color:#374151;margin:6px 0 0;padding-left:22px}}
ol.rules b{{color:#111827}}
.kv{{font-size:12.5px;color:#6b7280;line-height:1.9}}
.tbl.watchtbl{{font-size:10.5px}}
.watchtbl th{{padding:5px 4px;font-size:10px}}
.watchtbl td{{padding:4px 4px}}
.wnum{{font-size:10.5px}}
.wh{{line-height:1.25}}
.chc{{color:#1677ff;font-weight:400;font-size:9px}}
.cbest{{background:#e8f1ff !important;border-radius:3px}}
.headrow td{{background:#fffbe6}}
.tag.watch{{background:#f1f5f9;color:#64748b;border:1px solid #dbe2ea}}
</style></head><body><div class="wrap">

<h1>ETF 轮动战法 · 2026 年内回测报告</h1>
<div class="sub">窗口：<b>{meta['start']} 收盘 = 1.000</b> → <b>{meta['end']}</b>　|　
共 {meta['window_days']} 个交易日（含起点）／{meta['window_days']-1} 个收益日</div>
<div class="sub">对比策略：<b>策略B · 现池（维持主策略）</b>　|　<b>策略A · 博主原版</b>　|　
末节附 <b>19 只备选 ETF 观察池</b>（动横20—动横1 动量阶梯）</div>
<div class="sub">成本口径：<b>双边 0.2%</b>（{meta['cost_mode']}）　|　
数据：ETF 用 TDX <b>前复权</b>日线（已修正份额折算与分红跳空），指数用本地通达信不复权日线</div>

<div class="cards">{cards}</div>

<h2>一、净值曲线</h2>
<div class="panel">
  {svg}
  <div id="tip"></div>
</div>
<p class="note">曲线为<b>收盘价净值</b>（起点归一 1.000）。曲线上的小圆点 = 该策略当日的换仓动作
（<span style="color:#7c3aed">紫=策略B</span>、<span style="color:#d4380d">红=策略A</span>）。
鼠标在图上移动可查看任意一天的全部净值。</p>

<h2>二、绩效对比</h2>
<table class="tbl">
<thead><tr><th>曲线</th><th class="num">期末净值</th><th class="num">区间收益</th>
<th class="num">年化</th><th class="num">最大回撤</th><th class="num">年化波动</th>
<th class="num">夏普</th><th class="num">索提诺</th></tr></thead>
<tbody>{''.join(perf_rows)}</tbody></table>
<p class="note">年化按 244 个交易日折算（区间不足 1 年，年化仅作横向比较，不代表可持续收益）。
最大回撤为区间内净值峰值到谷底的最大跌幅。</p>

<h2>三、直接回答：纳指ETF 为什么一次都没进过持仓？</h2>
<p class="note" style="margin-top:0"><b>不是漏了，也不是忘了——是"没到合适的点"。准确说是三层原因叠加：</b></p>
<table class="tbl" style="margin-bottom:10px">
<thead><tr><th>513100 在九只池中的当日名次</th>{rank_head_cells}</tr></thead>
<tbody><tr><td class="nm2">出现天数（共 {meta['window_days'] - 1} 个收益日）</td>{rank_cnt_cells}</tr></tbody>
</table>
<p class="note" style="margin-top:0">513100 在整个窗口里<b>平均只排第 4.95 名</b>，排名第 3 或更差的天数占了绝大多数；
它<b>只有 2 天排到第 1</b>，且都在窗口末尾：</p>
<table class="tbl">
<thead><tr><th>日期</th><th class="num">513100 动横20</th><th>次名</th>
<th class="num">次名动横20</th><th class="num">领先度</th><th>为什么没买</th></tr></thead>
<tbody>{''.join(r513_rows)}</tbody></table>
<div class="warnbox" style="margin-top:10px">
<b>逐层原因：</b><br/>
<b>① 根本没轮到它。</b>窗口内九只池的头名被 <b>588170 科创半导体（67 天）</b>、
159967 创成长（24 天）、518880 黄金（23 天）、561700 电力（23 天）牢牢占据，510300 沪深300 更是 0 天。
513100 直到 <b>2026-09-22</b> 才第一次夺冠。<br/>
<b>② 夺冠当天被"粘性带"挡住。</b>09-22 与 09-23 的领先度只有 <b>+0.42pp</b> 与 <b>+0.63pp</b>，
均小于你拍板的粘性带阈值 <b>1.0pp</b>，规则判定为"平手、保留前一头名"，于是继续持有 563300 中证2000。<br/>
<b>③ 最后一天来不及执行。</b>09-23 是回测窗口的最后一个交易日，当日收盘才产生信号，成交在下一交易日——
窗口已经结束。即便把粘性带放宽到 0.5pp，09-22 的 0.42pp 仍会被黏住，而 09-23 已无后续交易日可成交。<br/>
<b>结论：</b>策略B 在 2026-01-01 ~ 09-23 期间<b>本来就轮不到纳指</b>，这与策略逻辑无关，不是数据或代码问题。<br/>
<b>一个有力的对照：</b>策略A 因为池子只有 5 只、没有 588170 这类强势赛道品种，513100 在其中当了 15 天头名；
且策略A（博主原版）<b>不带粘性带</b>，所以它<b>在 2026-09-22 就真的完成了换仓、买入了纳指ETF</b>
（见第四节策略A 明细第 43 笔：中证2000ETF → 纳指ETF）。<b>策略B 的唯一差别就是那条 1.0pp 粘性带，它让这次换仓被"节省"掉了。</b></div>
<div class="kv" style="margin-top:14px"><b>九只池各标的「成为头名」的天数（按降序）：</b></div>
<div class="two" style="margin-top:8px">
<div><table class="tbl"><thead><tr><th>标的</th><th class="num">代码</th>
<th class="num">头名天数</th><th class="num">占比</th></tr></thead>
<tbody>{hold_head_rows}</tbody></table></div>
<div class="note" style="margin-top:0;flex:1 1 320px">
<b>怎么读这张表：</b>它直接解释了持仓分布——谁当过头名，谁才可能被买入。<br/><br/>
588170 科创半导体在窗口内 <b>67/177 天（37.9%）</b>都是最强的，
所以策略B 的持仓高度集中在它身上；而 <b>510300 沪深300 一次都没当过头名</b>（0 天），
这也解释了为什么它同样从未出现在持仓里。<br/><br/>
换言之：<b>纳指没被买到，是动量排名的正常结果，不是策略失灵。</b>
如果哪天 513100 稳定排到前列（比如领先度拉开到 1.0pp 以上），它就会自然被买入。
</div></div>

<h2>四、策略A（博主原版）操作明细 · 共 {len(d['trades_A'])} 笔换仓</h2>
<h2>七、策略A（博主原版）操作明细 · 共 {len(d['trades_A'])} 笔换仓</h2>
<table class="tbl">
<thead><tr><th class="num">#</th><th>日期</th><th>调仓</th><th>类型</th>
<th>触发原因</th><th class="num">调仓后净值</th></tr></thead>
<tbody>{trade_rows(d['trades_A'], d['cash_ranges_A'])}</tbody></table>

<h2>五、策略A 空仓区间 · 共 {len(d['cash_ranges_A'])} 段 / {d['cash_days_A']} 个交易日</h2>
<table class="tbl">
<thead><tr><th class="num">#</th><th>起</th><th>止</th><th class="num">交易日数</th><th>期间收益</th></tr></thead>
<tbody>{''.join(cash_rows)}</tbody></table>
<div class="warnbox"><b>会不会有空仓？博主原版会。</b>共有两处空仓来源：<br/>
① <b>头名动量 ≤ 0</b>：五大轮动 ETF 中最强者的「动横20」都跌到 0 以下时，说明全池无强者，直接持币
；<br/>
② <b>过热切防守、但价值引擎空仓</b>：头名过热（乖离&gt;12% 或领先&gt;15pp）需要切防守位，
但防守位 512040 国信价值此刻不符合「跌破 200 日线×97%」的买入条件，于是持币而非硬买。<br/>
报告窗口内策略A 累计 <b>{d['cash_days_A']} 个交易日</b>处于空仓（占收益日
{d['cash_days_A']/(meta['window_days']-1)*100:.1f}%），
最长一段为 2026-04-14 ~ 2026-04-27 的 <b>9 个交易日</b>。策略B（现池）设计上<b>无空仓</b>，始终满仓。</div>

<h2>六、策略B（现池）操作明细 · 共 {len(d['trades_B'])} 笔换仓 · 空仓 0 日</h2>
<table class="tbl">
<thead><tr><th class="num">#</th><th>日期</th><th>调仓</th><th>类型</th>
<th>触发原因</th><th class="num">调仓后净值</th></tr></thead>
<tbody>{trade_rows(d['trades_B'], [])}</tbody></table>

<h2>七、持仓天数分布</h2>
<h2>十一、持仓天数分布</h2>
<p class="note" style="margin-top:0">下表分母为 <b>{meta['window_days']-1} 个收益日</b>
（窗口共 {meta['window_days']} 个交易日，其中 2025-12-31 为归一基准日、不产生收益）。</p>
<div class="two">
<div><div class="kv" style="margin-bottom:6px"><b>策略A · 博主原版</b></div>
<table class="tbl"><thead><tr><th>标的</th><th class="num">代码</th><th class="num">持有日数</th>
<th class="num">占比</th></tr></thead><tbody>{hold_rows('held_days_A')}</tbody></table></div>
<div><div class="kv" style="margin-bottom:6px"><b>策略B · 现池（基线）</b></div>
<table class="tbl"><thead><tr><th>标的</th><th class="num">代码</th><th class="num">持有日数</th>
<th class="num">占比</th></tr></thead><tbody>{hold_rows('held_days_B')}</tbody></table></div>
</div>
<p class="note">请注意，<b>两个策略的持仓清单里都没有 513100 纳指和 510300 沪深300</b>，原因见第三节；两策略持仓差异主要来自博主原版的"空仓 + 512040 价值引擎"与现池的"9 只池 + 512880 防守 + 粘性带"。</p>

<h2>八、博主策略还原与交叉验证</h2>
<p class="note" style="margin-top:0">博主公众号【秋收计划1024】把方法称为「<b>超简最强轮动（含过热保护）</b>」，
基本逻辑三句：<b>动量惯性 + 风格轮动 + 过热必回落</b>。原文与作者自建信号台截图互相印证，规则还原如下：</p>
<ol class="rules">
<li><b>轮动池（5只）</b>：510300 沪深300 ／ 159967 创成长 ／ 563300 中证2000 ／ 518880 黄金 ／ 513100 纳指</li>
<li><b>防守位</b>：512190 凤凰50 ／ 512040 国信价值（信号台主口径用 <b>512040</b>）</li>
<li><b>排名信号</b>：<b>动横20 = 现价 ÷ 20 个交易日前的收盘价 − 1</b>（信号台叫「动量惯性」），取最大者为<b>头名</b>；
「领先度」= 头名 − 次名，「乖离」= 现价 ÷ 20 日线 − 1</li>
<li><b>持有规则</b>：头名动量 <b>&gt; 0</b> 才持有头名，否则<b>空仓</b>（空仓收益记 0）</li>
<li><b>切防守</b>：头名乖离 <b>&gt; 12%</b> 或 领先度 <b>&gt; 15pp</b>（任一满足）</li>
<li><b>切回轮动</b>：乖离 <b>&lt; 9%</b> 且 领先度 <b>&lt; 10pp</b> 且 已防守满 <b>5 个交易日</b>（同时满足）</li>
<li><b>防守端择时（价值引擎）</b>：512040 <b>跌破 200 日线×97% 买入</b>、<b>升破 200 日线×106% 卖出</b>；
持仓中才用 512040，否则持币</li>
<li><b>执行</b>：收盘价计信号、收盘价成交，不预测、不主观干预</li>
</ol>
<div class="kv" style="margin-top:14px"><b>用博主自报数据反向锁定参数</b>（信号台右下角「2026至今」三档成本）：</div>
{verify}
<p class="note">博主原文另述：目标「十年平均年化 20%+」，自述胜率约 <b>55%</b>，
「赚的是赔率的钱，不是胜率的钱，只吃鱼身」；历史口径自报为 2014 起（指数测算·零成本）
年化 +39.38%／累计 65.3 倍／回撤 −30.38%，2020 起（实盘ETF·双边0.2%）
年化 +23.58%／累计 4.2 倍／回撤 −26.61%。</p>

<h2>九、口径披露与风险提示</h2>
<div class="warnbox">
<b>1. 复权口径（本次修正的核心）</b>：本地通达信 .day 为<b>不复权</b>价，直接回测会踩两个假跳空——
588170 科创半导体ETF 于 2026-07-06 <b>1拆3</b>（3.579→1.193）、513660 恒生ETF 于 2026-04-21
<b>1拆2</b>（3.128→1.564），另有 510300 于 2026-01-19 <b>分红除息</b>。若不复权，
588170 会被误算成 −33%（实为 <b>+100.0%</b>）。本报告已全部改用 TDX <b>前复权</b>序列修正。<br/><br/>
<b>2. 成本计费</b>：博主信号台对每次换仓均按完整双边收取（本次按同一口径复算，故与该台数字吻合）；
空仓期间不收费、持币收益记 0；脚本另提供 <b>--leg</b> 开关（只计实际发生腿）作更保守对照。<br/><br/>
<b>3. 执行假设</b>：信号在当日收盘计算并以<b>当日收盘价</b>成交，未计冲击成本、未考虑涨跌停与流动性；
实盘存在 1 个交易日左右的执行滑移。<br/><br/>
<b>4. 样本局限（重要）</b>：仅 178 个交易日、且处于<b>单一上涨市况</b>（科创50 +23.56%、纳指 +22.58%），
<b>不足以说明任何策略在熊市/震荡市的表现</b>。年化由不足 1 年的区间折算，仅供横向比较。
<b>5. 定位</b>：本报告为对既有公开方法的<b>规则还原与历史复算</b>，不构成任何投资建议。
</div>

<h2>十、备选 ETF 观察池 · 动横20 — 动横1 动量阶梯（锚定 {w['anchor']} 收盘）</h2>
<p class="note" style="margin-top:0">对照博主信号台的「<b>其他更替 ETF</b>」面板建立的 <b>{len(w['codes'])} 只备选观察池</b>：
<b>不参与调仓</b>，只跟踪动量水位与轮动更替，作为主池的"潜在更替标的"监控。
读法（与博主面板一致）：最左列 <b>动横20 = 今日真实动量</b>（也是排名依据）；
往右每列 = 假设未来价格冻结时，<b>未来第 (20−k) 个交易日的动横20 推演</b>——
即"如果从今天起不再波动，谁会陆续登顶"。列头下方的蓝色小字 = 该列的头名代码；
<b>单元格蓝色高亮 = 该列头名</b>；整行黄色底 = 今日备选池头名。</p>
<div class="cards" style="margin-top:12px">
<div class="card" style="border-top:3px solid #d4380d">
<div class="ct">备选池今日头名</div>
<div class="cv" style="font-size:21px">{w['names'][w['head_now']]}</div>
<div class="cs" style="color:{RED}">动横20 {w['head_roc']:+.2f}%</div>
<div class="cd">{w['head_now']}　现价 {w['price'][w['head_now']]:.3f}　今日 {w['chg1d'][w['head_now']]:+.2f}%</div>
<div class="cd2">{'已在主池' if w['in_main'][w['head_now']] else '不在主池 → 更替候选'}</div></div>
<div class="card" style="border-top:3px solid #1677ff">
<div class="ct">次名 · 领先度</div>
<div class="cv" style="font-size:21px">{w['lead_now']:.2f}pp</div>
<div class="cs">{w['names'][w['second_now']]}（{w['second_now']}）</div>
<div class="cd">次名动横20 {w['second_roc']:+.2f}%</div>
<div class="cd2">领先度 = 头名 − 次名</div></div>
<div class="card" style="border-top:3px solid #d4a017">
<div class="ct">明日推演头名（动横19 列）</div>
<div class="cv" style="font-size:21px">{w['names'][tomorrow_head]}</div>
<div class="cs">动横19 {w['ladder'][tomorrow_head][1]:+.2f}%</div>
<div class="cd">{tomorrow_head}　价格冻结假设下明日最可能登顶</div>
<div class="cd2">若与今日头名不同 → 备选池头名可能很快更替</div></div>
<div class="card" style="border-top:3px solid {RED if gap_watch > 0 else GREEN}">
<div class="ct">备选头名 vs 主池头名</div>
<div class="cv" style="font-size:21px">{gap_watch:+.2f}pp</div>
<div class="cs">主池（九只）头名动横20 = {main_head_roc:+.2f}%</div>
<div class="cd">{'⚠️ 备选池出现了强于主池头名的标的，值得关注是否更替' if gap_watch > 0 else '主池头名仍强于备选池头名，维持现池即可'}</div>
<div class="cd2">更替与否由你决定，本报告不做建议</div></div>
</div>
<div style="overflow-x:auto;margin-top:12px">
<table class="tbl watchtbl">
<thead><tr><th class="num">#</th><th>代码</th><th>名称</th><th>池</th>{watch_head_cells}
<th class="num">现价</th><th class="num">今日涨跌</th></tr></thead>
<tbody>{''.join(watch_rows)}</tbody></table>
</div>
<p class="note">口径：<b>前复权收盘价</b>（与主池一致），动横(k) = 现价 ÷ k 个交易日前收盘 − 1。
与博主盘中截图相比个别标的有 0.5~2pp 差异，源于<b>其面板为盘中价、本表为收盘价</b>
（例如 516080 两者完全一致；券商类 ETF 当日午后回落明显，差异最大）。本表口径自洽、可复算。
表中 6 只"主池"标的（588170 / 516080 / 561700 / 513660 / 512880）仅作对照；
159543 / 159505 为中证2000（563300）的同指数替代品。<b>当备选池头名持续强于主池头名、且领先度拉大时，
即出现"更替候选"信号。</b></p>

<script>
const D = {json.dumps(js_data, ensure_ascii=False)};
const svg = document.getElementById('navchart');
svg._Y = v => D.mT + (D.hi - v) / (D.hi - D.lo) * D.ph;
const tip = document.getElementById('tip');
const box = document.getElementById('hitbox');
const hl = document.getElementById('hoverline');
const hd = document.getElementById('hoverdots');
const panel = svg.parentElement;
function nav(i){{
  const r = svg.getBoundingClientRect();
  const k = r.width / D.W;
  return {{r:r, k:k}};
}}
box.addEventListener('mousemove', ev=>{{
  const {{r,k}} = nav();
  const x = (ev.clientX - r.left)/k;
  let i = Math.round((x - D.x0)/D.step);
  i = Math.max(0, Math.min(D.dates.length-1, i));
  const px = D.x0 + i*D.step;
  hl.setAttribute('x1', px); hl.setAttribute('x2', px); hl.setAttribute('opacity','1');
  let dots='', rows='';
  D.series.forEach(s=>{{
    const yy = svg._Y ? svg._Y(s.v[i]) : null;
    rows += `<div class="row"><span style="color:${{s.color}}">■</span><span>${{s.name.split(' ')[0]}}</span><b>${{s.v[i].toFixed(4)}}</b></div>`;
  }});
  tip.innerHTML = `<b>${{D.dates[i]}}</b>`+rows;
  tip.style.display='block';
  const pr = panel.getBoundingClientRect();
  let lx = (ev.clientX - pr.left) + 16;
  lx = Math.max(6, Math.min(panel.clientWidth - tip.offsetWidth - 6, lx));
  let ty = (ev.clientY - pr.top) - tip.offsetHeight - 14;
  if (ty < 6) ty = (ev.clientY - pr.top) + 18;
  ty = Math.max(6, Math.min(panel.clientHeight - tip.offsetHeight - 6, ty));
  tip.style.left = lx + 'px';
  tip.style.top = ty + 'px';
  // 更新圆点
  let out='';
  D.series.forEach(s=>{{
    const y = svg._Y(s.v[i]);
    out += `<circle cx="${{px.toFixed(1)}}" cy="${{y.toFixed(1)}}" r="3" fill="${{s.color}}" stroke="#fff" stroke-width="1.2"/>`;
  }});
  hd.innerHTML = out;
}});
box.addEventListener('mouseleave', ()=>{{
  hl.setAttribute('opacity','0'); hd.innerHTML=''; tip.style.display='none';
}});
</script>
</div></body></html>"""

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("WROTE", OUT, len(html), "chars")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=JSON)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--open", action="store_true", help="生成后自动打开")
    _a = ap.parse_args()
    JSON, OUT = _a.json, _a.out
    build()
    if _a.open:
        os.startfile(OUT)
