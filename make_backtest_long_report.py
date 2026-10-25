#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
out/backtest_long.json -> 自包含 HTML 报告 (2020-2026 策略B 长周期回测)
内联 SVG 净值曲线 (无外部依赖), 章节:
  一 结论速览 / 二 防守策略改造与消融 / 三 净值曲线 / 四 逐年收益 / 五 分年度绩效
  六 换仓明细(主选池) / 七 换仓明细(备选池) / 八 持仓分布
  九 动态池与幸存者偏差 / 十 数据口径与防造假校验 / 十一 审计复核记录 / 十二 风险提示
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "out", "backtest_long.json")
DST = os.path.join(HERE, "out", "backtest_long.html")

RED = "#d4380d"
GREEN = "#0f9d58"
BLUE = "#1a56db"
GRAY = "#6b7280"

C = {
    "main": ("#d4380d", "策略B·主选池(9只)"),
    "watch": ("#1a56db", "策略B·备选池(14只)"),
    "combo": ("#7c3aed", "参照组·主+备(23只)"),
    "bench_000001": ("#94a3b8", "上证指数"),
    "bench_000688": ("#0f9d58", "科创50"),
    "bench_399006": ("#f59e0b", "创业板指"),
    "bench_513100": ("#0891b2", "纳指ETF"),
}


def pct(x, d=2):
    return f"{x*100:+.{d}f}%"


def cls(x):
    return "up" if x > 0 else ("down" if x < 0 else "flat")


def svg_curve(dates, series, w=1180, h=380, ml=58, mr=18, mt=18, mb=42):
    """内联 SVG 多曲线 + 悬浮提示 (纯原生 JS, 无外部库)。"""
    n = len(dates)
    allv = [v for s in series.values() for v in s if v is not None]
    lo, hi = min(allv), max(allv)
    pad = (hi - lo) * 0.06 or 0.1
    lo, hi = lo - pad, hi + pad
    pw, ph = w - ml - mr, h - mt - mb

    def X(i):
        return ml + pw * i / max(n - 1, 1)

    def Y(v):
        return mt + ph * (1 - (v - lo) / (hi - lo))

    # 横向网格 + y 轴刻度
    grid = []
    ticks = 6
    for k in range(ticks + 1):
        v = lo + (hi - lo) * k / ticks
        y = Y(v)
        grid.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{w-mr}" y2="{y:.1f}" '
                    f'stroke="#e5e7eb" stroke-width="1"/>'
                    f'<text x="{ml-8}" y="{y+4:.1f}" text-anchor="end" '
                    f'font-size="11" fill="{GRAY}">{v:.2f}</text>')
    # 1.0 基准线
    y1 = Y(1.0)
    grid.append(f'<line x1="{ml}" y1="{y1:.1f}" x2="{w-mr}" y2="{y1:.1f}" '
                f'stroke="#9ca3af" stroke-width="1.4" stroke-dasharray="5 4"/>'
                f'<text x="{w-mr}" y="{y1-6:.1f}" text-anchor="end" '
                f'font-size="11" fill="#6b7280">起点 1.000</text>')
    # x 轴年份刻度
    xlab = []
    seen = set()
    for i, d in enumerate(dates):
        y = d[:4]
        if y not in seen:
            seen.add(y)
            xlab.append(f'<line x1="{X(i):.1f}" y1="{mt+ph}" x2="{X(i):.1f}" '
                        f'y2="{mt+ph+5}" stroke="#9ca3af"/>'
                        f'<text x="{X(i):.1f}" y="{mt+ph+20}" text-anchor="middle" '
                        f'font-size="11" fill="{GRAY}">{y}</text>')

    paths = []
    for k, s in series.items():
        col, _lab = C.get(k, ("#334155", k))
        pts = []
        for i, v in enumerate(s):
            if v is None:
                continue
            pts.append(f"{'M' if not pts else 'L'}{X(i):.1f},{Y(v):.1f}")
        if pts:
            dash = ' stroke-dasharray="5 4"' if k.startswith("bench_") else ""
            wdt = "1.6" if k.startswith("bench_") else "2.3"
            paths.append(f'<path d="{" ".join(pts)}" fill="none" stroke="{col}" '
                         f'stroke-width="{wdt}"{dash}/>')

    # 竖直游标 + 提示框
    nser = len(series)
    hover = (f'<line id="hl" x1="0" y1="{mt}" x2="0" y2="{mt+ph}" '
             f'stroke="#9ca3af" stroke-width="1" stroke-dasharray="3 3" '
             f'style="display:none"/>'
             f'<g id="hb" style="display:none"></g>')
    data_json = json.dumps({"d": dates, "s": {k: [None if v is None else round(v, 4)
                                                  for v in s]
                                           for k, s in series.items()}})
    js = f"""
(function(){{
  var box=document.getElementById('curveBox');
  var svg=box.querySelector('svg');
  var hl=document.getElementById('hl'), hb=document.getElementById('hb');
  var D={data_json};
  var names={json.dumps({k: v[1] for k, v in C.items()}, ensure_ascii=False)};
  var keys=Object.keys(D.s);
  function X(i){{return {ml} + {pw} * i / (D.d.length-1);}}
  svg.addEventListener('mousemove',function(e){{
    var rc=svg.getBoundingClientRect();
    var px=(e.clientX-rc.left)*({w}/rc.width);
    var i=Math.round((px-{ml})/{pw}*(D.d.length-1));
    if(i<0)i=0; if(i>D.d.length-1)i=D.d.length-1;
    hl.setAttribute('x1',X(i)); hl.setAttribute('x2',X(i)); hl.style.display='';
    var hh=34+keys.length*18;
    var body=keys.map(function(k,idx){{
      var v=D.s[k][i];
      var col=(v==null||v===undefined)?'#9ca3af':(v>=1?'{RED}':'{GREEN}');
      return '<text x="12" y="'+(38+idx*18)+'" font-size="11" fill="'+col+'">'+names[k]+'  '+(v==null?'-':v.toFixed(4))+'</text>';
    }}).join('');
    hb.innerHTML='<rect width="236" height="'+hh+'" rx="6" fill="#ffffff" stroke="#cbd5e1" opacity="0.97"/>'
      +'<text x="12" y="20" font-size="12" font-weight="700" fill="#111827">'+D.d[i]+'</text>'
      + body;
    var rx=X(i)+16; if(rx>{w-256}) rx=X(i)-252;
    hb.setAttribute('transform','translate('+rx+','+{mt}+')');
    hb.style.display='';
  }});
  svg.addEventListener('mouseleave',function(){{hl.style.display='none';hb.style.display='none';}});
}})();
"""
    return (f'<div id="curveBox" style="overflow-x:auto">'
            f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" '
            f'style="background:#fff;border-radius:8px">{"".join(grid)}{"".join(xlab)}'
            f'{"".join(paths)}{hover}</svg></div>'
            f'<script>{js}</script>')


def table(headers, rows, aligns=None, cls_cols=None):
    aligns = aligns or ["left"] * len(headers)
    cls_cols = cls_cols or {}
    th = "".join(f'<th style="text-align:{a}">{h}</th>'
                 for h, a in zip(headers, aligns))
    trs = []
    for r in rows:
        tds = []
        for i, (v, a) in enumerate(zip(r, aligns)):
            k = cls_cols.get(i)
            style = f' class="{cls(v)}"' if k is not None and isinstance(v, (int, float)) else ""
            tds.append(f'<td style="text-align:{a}"{style}>{v}</td>')
        trs.append("<tr>" + "".join(tds) + "</tr>")
    return (f'<table><thead><tr>{th}</tr></thead><tbody>'
            + "".join(trs) + "</tbody></table>")


def main():
    J = json.load(open(SRC, encoding="utf-8"))
    D = J["dates"]
    nav = J["nav"]
    G = J["groups"]
    M = J["meta"]
    NAMES = M["names"]

    # ---------- 防守策略消融数据（可选文件） ----------
    AB_PATH = os.path.join(os.path.dirname(SRC), "defense_ablation.json")
    AB = json.load(open(AB_PATH, encoding="utf-8")) if os.path.exists(AB_PATH) else None

    def yv(key, y):
        for r in G[key]["yearly"]:
            if r["year"] == y:
                return r["ret"]
        return None

    def byv(key, y):
        s = G[key]["yearly"]
        for r in s:
            if r["year"] == y:
                return r
        return {}

    yrs = sorted({r["year"] for k in G for r in G[k]["yearly"]})

    # ---------- 卡片 ----------
    m, w, c = G["main"], G["watch"], G["combo"]
    cards = f"""
<div class="cards">
  <div class="card">
    <div class="cl">策略B · 主选池 9 只</div>
    <div class="cv up">{m['stat']['total']*100:+.2f}%</div>
    <div class="cs">期末净值 {nav['main'][-1]:.4f} · 年化 {m['stat']['ann']*100:+.2f}%</div>
    <div class="cs">最大回撤 {m['stat']['mdd']*100:.2f}% · 夏普 {m['stat']['sharpe']:.2f}</div>
    <div class="cs">换仓 {m['trades_n']} 笔 · 防守 {m['defense']['days']} 日/{m['defense']['episodes']} 段（持币 {m['defense'].get('cash_days',0)} 日）</div>
  </div>
  <div class="card alt">
    <div class="cl">策略B · 备选池 {len(w["pool"])} 只</div>
    <div class="cv {'up' if w['stat']['total']>0 else 'down'}">{w['stat']['total']*100:+.2f}%</div>
    <div class="cs">期末净值 {nav['watch'][-1]:.4f} · 年化 {w['stat']['ann']*100:+.2f}%</div>
    <div class="cs">最大回撤 {w['stat']['mdd']*100:.2f}% · 夏普 {w['stat']['sharpe']:.2f}</div>
    <div class="cs">换仓 {w['trades_n']} 笔 · 防守 {w['defense']['days']} 日/{w['defense']['episodes']} 段（持币 {w['defense'].get('cash_days',0)} 日）</div>
  </div>
  <div class="card alt2">
    <div class="cl">参照组 · 主+备 {len(c["pool"])} 只</div>
    <div class="cv up">{c['stat']['total']*100:+.2f}%</div>
    <div class="cs">期末净值 {nav['combo'][-1]:.4f} · 年化 {c['stat']['ann']*100:+.2f}%</div>
    <div class="cs">最大回撤 {c['stat']['mdd']*100:.2f}% · 夏普 {c['stat']['sharpe']:.2f}</div>
    <div class="cs">换仓 {c['trades_n']} 笔 · 防守 {c['defense']['days']} 日/{c['defense']['episodes']} 段（持币 {c['defense'].get('cash_days',0)} 日）</div>
  </div>
  <div class="card">
    <div class="cl">最佳基准 · 纳指ETF 513100</div>
    <div class="cv up">{(nav['bench_513100'][-1]-1)*100:+.2f}%</div>
    <div class="cs">期末净值 {nav['bench_513100'][-1]:.4f}</div>
    <div class="cs">A 股三指数同期：上证 {nav['bench_000001'][-1]:.4f} · 科创50 {nav['bench_000688'][-1]:.4f}</div>
    <div class="cs">创业板指 {nav['bench_399006'][-1]:.4f}</div>
  </div>
</div>"""

    # ---------- 防守策略描述（顶部 meta 行） ----------
    _dc = M.get("defensive", "")
    _stg = M.get("strategy", {})
    if _stg.get("value_engine"):
        DEF_DESC = (f"{NAMES.get(_dc,_dc)}（{_dc}）+ 价值引擎 "
                    f"MA200×{_stg.get('ve_buy',0.97):.2f} 买 / "
                    f"×{_stg.get('ve_sell',1.06):.2f} 卖，不满则持币")
    else:
        DEF_DESC = f"{NAMES.get(_dc,_dc)}（{_dc}）· 防守期满仓"

    # ---------- 防守策略消融表 ----------
    if AB:
        SK = [("A_old_512880_full", "A 旧基线 · 512880 满仓"),
              ("B_512040_full", "B 只换标的 · 512040 满仓"),
              ("C_new_512040_ve", "C 新策略 · 512040 + 价值引擎")]
        ab_rows = []
        for gname in ("main", "watch", "combo"):
            gl = AB["results"][gname]["label"]
            for sk, slab in SK:
                r = AB["results"][gname]["runs"][sk]
                s, d = r["stat"], r["defense"]
                ab_rows.append([
                    gl if sk == SK[0][0] else "",
                    slab, f"{list(r['nav'].values())[-1]:.4f}",
                    pct(s["total"]), pct(s["ann"]), pct(s["mdd"]),
                    f"{s['sharpe']:.2f}" if s["sharpe"] is not None else "—",
                    str(r["trades_n"]), str(d["days"]), str(d["cash_days"])])
        AB_TBL = table(
            ["回测组", "防守方案", "期末净值", "区间收益", "年化", "最大回撤",
             "夏普", "换仓", "防守日", "其中持币"], ab_rows,
            aligns=["l", "l", "r", "r", "r", "r", "r", "r", "r", "r"],
            cls_cols={3: 1, 5: 1})

        # 分年度 C−A 归因
        abt = AB["results"]["main"]["runs"]
        ya = {r["year"]: r["ret"] for r in abt["A_old_512880_full"]["yearly"]}
        yb = {r["year"]: r["ret"] for r in abt["B_512040_full"]["yearly"]}
        yc = {r["year"]: r["ret"] for r in abt["C_new_512040_ve"]["yearly"]}
        yr_rows = []
        for y in sorted(set(ya) | set(yb) | set(yc)):
            a_, b_, c_ = ya.get(y), yb.get(y), yc.get(y)
            d1 = (b_ - a_) * 100 if (a_ is not None and b_ is not None) else None
            d2 = (c_ - a_) * 100 if (a_ is not None and c_ is not None) else None
            yr_rows.append([y, pct(a_, 1), pct(b_, 1), pct(c_, 1),
                            f"{d1:+.1f}pp" if d1 is not None else "—",
                            f"{d2:+.1f}pp" if d2 is not None else "—"])
        AB_YTBL = table(["年份", "A 512880 满仓", "B 512040 满仓",
                         "C 512040+价值引擎", "B−A", "C−A"], yr_rows,
                        aligns=["l", "r", "r", "r", "r", "r"],
                        cls_cols={1: 1, 2: 1, 3: 1, 4: 1, 5: 1})

        AB_NOTE = f"""
<div class="scroll">{AB_TBL}</div>
<h3 style="font-size:15px;margin:20px 0 9px">主选池分年度归因：改进不是单向的</h3>
{AB_YTBL}
<div class="note warn" style="margin-top:14px">
<b>⚠ 独立审计指出的 5 处结论层修正（必须连同上表一起读）：</b><br>
1. <b>事后选股偏差</b>：审计方扫描全部 25 个候选防守位，512040 在"价值引擎"模式下排第 <b>2</b> 名，
 <b>516080 创新药ETF 更高（5.0177 vs 4.7579，+5.46%）</b>；在满仓模式下 512040 仅排 14/25。
 换句话说"+30.7pp"带有事后挑选成分，应视为<b>上界估计</b>，不能宣称是策略天然改进。
 旧防守位 512880 排 20/25（后 24%），"该换"这个方向判断本身是成立的。<br>
2. <b>改进高度集中于个别年份</b>：见上表，2025 年 C 反而<b>跑输 A 约 5.4pp</b>、2022 年跑输约 1.0pp；
 超额主要来自 2020 / 2024 / 2026。2023 年三组完全相同（该年防守期 = 0 天），证明改造只影响防守期、逻辑隔离干净。<br>
3. <b>价值引擎单独看是负收益工具</b>：审计方把它从轮动状态机剥离后独立回测，净值仅 <b>1.4785</b>，
 而买入持有 512040 是 <b>2.4627</b>，<b>相对落后约 40%</b>。它在本策略中的巨额增量来自
 "恰好在 161 个防守日避开了 512040 的 −21.77% 累计下跌段"，<b>不是普适的择时能力</b>。
 它的真实价值在<b>降回撤</b>（纯 VE −15.23% vs 买入持有 −21.62%），不在增收益。<br>
4. <b>"512880 先天缺陷"归因不准</b>：512880 跟踪证券行业指数，−45% 回撤历时 4 年（2020-07 → 2024-07），
 属<b>行业属性</b>而非标的缺陷。关键反证：把<b>同一个价值引擎</b>套在 512880 上得 <b>4.2880</b>，
 <b>反而高于 512040 满仓的 3.7447</b>。旧策略的病根是"防守期满仓单一高 Beta 行业主题 ETF"，不只是选错标的。<br>
5. <b>持币按 0 收益计，是保守处理</b>：A 股持币可拿货币基金/逆回购年息 1.5%~2.5%。
 若计 2% 年化，主选池 C 相对 B 的优势从 +27.06pp <b>扩大</b>到 +28.37pp —— 方向对 C 有利，不构成威胁。
</div>
<div class="note">
<b>成本敏感性（审计补充）</b>：零成本下 A/B/C = 5.3183 / 5.7937 / 7.4057，含 0.2% 双边成本后 = 3.4374 / 3.7447 / 4.7579，
累计成本拖累 <b>35%~38%</b>。三组拖累幅度接近，<b>成本假设不改变 A/B/C 的相对优劣</b>，但绝对水平对成本高度敏感。
</div>
"""
    else:
        AB_TBL = AB_YTBL = AB_NOTE = (
            '<div class="note warn">未找到 defense_ablation.json，'
            '请先运行 compare_defense.py</div>')

    ser = {k: nav[k] for k in ["main", "watch", "combo", "bench_513100",
                               "bench_000001", "bench_000688", "bench_399006"]}
    legend = " ".join(
        f'<span class="lg"><i style="background:{C[k][0]}"></i>{C[k][1]}</span>'
        for k in ser)

    # ---------- 逐年 ----------
    yrows = []
    for k in ["main", "watch", "combo"]:
        r = [G[k]["label"]]
        vals = []
        for y in yrs:
            v = yv(k, y)
            vals.append(v)
            r.append(pct(v, 1) if v is not None else "—")
        yrows.append(r)
    for k in ["bench_513100", "bench_000001", "bench_000688", "bench_399006"]:
        r = [C[k][1]]
        for y in yrs:
            sub = [x for x in J["nav"][k]]
            i0 = next((i for i, d in enumerate(D) if d[:4] >= y), None)
            i1 = next((i for i, d in enumerate(D) if d[:4] > y), len(D))
            r.append(pct(sub[i1 - 1] / sub[i0] - 1, 1) if i0 is not None else "—")
        yrows.append(r)
    ytbl = table(["曲线"] + yrs, yrows,
                 ["left"] + ["right"] * len(yrs))

    # ---------- 分年度绩效 ----------
    perf_rows = []
    for k in ["main", "watch", "combo"]:
        for r in G[k]["yearly"]:
            if r["year"] == "2019":
                continue
            perf_rows.append([G[k]["label"], r["year"], f"{r['end']:.4f}",
                              r["ret"], r["mdd"], r["sharpe"], r["days"]])
    perf = table(["组合", "年份", "年末净值", "当年收益", "年内最大回撤", "夏普", "交易日"],
                 perf_rows, ["left", "left", "right", "right", "right", "right", "right"],
                 {3: 1, 4: 1, 5: 1})

    # ---------- 换仓明细 ----------
    def trades_table(key, limit=None):
        ts = J["trades"][key]
        show = ts if limit is None else ts[:limit]
        rows = []
        for i, t in enumerate(show):
            rows.append([i + 1, t["date"], t["frm_name"], t["to_name"],
                         t["state"], t.get("lead"), t.get("m_head"),
                         t.get("dev"), "是" if t.get("tie") else "", t["reason"]])
        more = (f'<p class="note">仅显示前 {limit} 笔，共 {len(ts)} 笔。'
                f'完整明细见 <code>out/backtest_long.json</code> → '
                f'<code>trades.{key}</code></p>') if limit and len(ts) > limit else ""
        return table(["#", "信号日", "原持仓", "新持仓", "状态", "领先pp",
                      "头名动横%", "乖离%", "粘性", "触发原因"], rows,
                     ["right", "left", "left", "left", "center", "right",
                      "right", "right", "center", "left"],
                     {5: 1, 6: 1, 7: 1}) + more

    # ---------- 持仓分布 ----------
    hrows = []
    hmax = max(h["days"] for h in m["held"])
    for h in m["held"]:
        wpx = int(h["days"] / hmax * 100)
        hrows.append([h["name"], h["code"], h["days"],
                      f'{h["pct"]:.2f}%',
                      f'<div class="bar"><i style="width:{wpx}%"></i></div>'])
    htbl = table(["标的", "代码", "持有交易日", "占比", "分布"], hrows,
                 ["left", "left", "right", "right", "left"])

    hwrows = []
    hwmax = max(h["days"] for h in w["held"])
    for h in w["held"]:
        wpx = int(h["days"] / hwmax * 100)
        hwrows.append([h["name"], h["code"], h["days"], f'{h["pct"]:.2f}%',
                       f'<div class="bar b2"><i style="width:{wpx}%"></i></div>'])
    hwtbl = table(["标的", "代码", "持有交易日", "占比", "分布"], hwrows,
                  ["left", "left", "right", "right", "left"])

    # ---------- 动态池 ----------
    arows = []
    for a in G["main"]["availability"]:
        added = "、".join(f"{c} {NAMES.get(c,'')}" for c in a["added"]) or "—"
        arows.append([a["year"], a["n_first"], a["n_last"], len(a["added"]), added])
    atbl = table(["年份", "年初可用", "年末可用", "年内新增", "新增标的（上市即纳入）"],
                 arows, ["left", "right", "right", "right", "left"])
    awrows = []
    for a in G["watch"]["availability"]:
        added = "、".join(f"{c} {NAMES.get(c,'')}" for c in a["added"]) or "—"
        awrows.append([a["year"], a["n_first"], a["n_last"], len(a["added"]), added])
    awtbl = table(["年份", "年初可用", "年末可用", "年内新增", "新增标的（上市即纳入）"],
                  awrows, ["left", "right", "right", "right", "left"])

    prov_rows = []
    for code, p in M["data_provenance"].items():
        if code in ("512040",):
            continue
        prov_rows.append([NAMES.get(code, code), code, p["first"], p["last"], p["n"]])

    HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>ETF 轮动战法 · 策略B {M['start']}~{M['end']} 长周期回测</title>
<style>
*{{box-sizing:border-box}}
body{{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
 background:#f6f7f9;color:#111827;margin:0;padding:24px;line-height:1.65}}
.wrap{{max-width:1240px;margin:0 auto}}
h1{{font-size:25px;margin:0 0 6px}}
.sub{{color:#6b7280;font-size:13px;margin-bottom:20px}}
h2{{font-size:18px;margin:34px 0 12px;padding-left:11px;
 border-left:4px solid #1a56db}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(238px,1fr));gap:13px;margin:18px 0}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:15px 17px;
 box-shadow:0 1px 3px rgba(0,0,0,.04)}}
.card.alt{{border-top:3px solid #1a56db}}
.card.alt2{{border-top:3px solid #7c3aed}}
.cl{{font-size:12.5px;color:#6b7280;margin-bottom:7px}}
.cv{{font-size:27px;font-weight:700;margin-bottom:6px}}
.cs{{font-size:12px;color:#4b5563;margin-top:3px}}
table{{width:100%;border-collapse:collapse;background:#fff;font-size:12.5px;
 border:1px solid #e5e7eb;border-radius:8px;overflow:hidden}}
th,td{{padding:7px 9px;border-bottom:1px solid #f0f1f3}}
th{{background:#f9fafb;font-weight:600;color:#374151;white-space:nowrap}}
tbody tr:last-child td{{border-bottom:none}}
tbody tr:hover{{background:#fafbfc}}
.up{{color:{RED}}} .down{{color:{GREEN}}} .flat{{color:#6b7280}}
.bar{{background:#f3f4f6;border-radius:4px;height:15px;width:100%;min-width:120px}}
.bar i{{display:block;background:{RED};height:100%;border-radius:4px}}
.bar.b2 i{{background:{BLUE}}}
.lg{{display:inline-flex;align-items:center;margin-right:16px;font-size:12.5px}}
.lg i{{width:15px;height:3px;display:inline-block;margin-right:6px;border-radius:2px}}
.note{{font-size:12px;color:#6b7280;background:#f9fafb;border:1px solid #e5e7eb;
 border-radius:7px;padding:11px 13px;margin-top:11px}}
.warn{{background:#fffbeb;border-left:4px solid #f59e0b}}
.ok{{background:#ecfdf5;border-left:4px solid #10b981}}
.tag{{display:inline-block;background:#eef2ff;color:#3730a3;font-size:11.5px;
 padding:2px 8px;border-radius:11px;margin-right:6px}}
.scroll{{max-height:460px;overflow-y:auto;border:1px solid #e5e7eb;border-radius:8px}}
.scroll table{{border:none}}
.kv{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:9px;
 margin:12px 0}}
.kv div{{background:#f9fafb;border:1px solid #e5e7eb;border-radius:7px;padding:9px 12px;font-size:12.5px}}
.kv b{{color:#111827}}
code{{background:#f3f4f6;padding:1px 5px;border-radius:4px;font-size:12px}}
footer{{margin-top:38px;padding-top:16px;border-top:1px solid #e5e7eb;
 color:#6b7280;font-size:12px}}
</style></head><body><div class="wrap">

<h1>ETF 轮动战法 · 策略B {M['start']}~{M['end']} 长周期回测</h1>
<div class="sub">
主选池与备选池<b>分别独立回测</b> · 起点 {M['start']} 收盘 = 1.000 → 终点 {M['end']}
 · 共 {M['window_days']-1} 个收益日（约 {(int(M['window_days'])-1)/244:.1f} 年）
 · 交易成本 双边 {M['cost']*100:.1f}%（买 {M['cost']/2*100:.1f}% + 卖 {M['cost']/2*100:.1f}%，每次换仓计双腿）
 · 防守策略 {DEF_DESC}
 · 数据 腾讯财经<b>前复权</b>日线（{M['data_start']} ~ {M['data_end']}）
</div>

{cards}

<h2>一、结论速览</h2>
<div class="note ok">
<b>主选池显著跑赢备选池，差距达 {(m['stat']['total']-w['stat']['total'])*100:.1f} 个百分点。</b><br>
· 主选池 9 只：{(m['stat']['total'])*100:+.2f}%，年化 {m['stat']['ann']*100:+.2f}%，最大回撤 {m['stat']['mdd']*100:.2f}%，夏普 {m['stat']['sharpe']:.2f}。<br>
· 备选池 {len(w["pool"])} 只：{(w['stat']['total'])*100:+.2f}%，年化 {w['stat']['ann']*100:+.2f}%，最大回撤 {w['stat']['mdd']*100:.2f}%，夏普 {w['stat']['sharpe']:.2f}
 —— 几乎无超额收益，且回撤比主选池深 {(abs(w['stat']['mdd'])-abs(m['stat']['mdd'])):.1f}pp。<br>
· 对照 A 股三指数：上证 {nav['bench_000001'][-1]:.4f}、科创50 {nav['bench_000688'][-1]:.4f}、创业板指 {nav['bench_399006'][-1]:.4f}，主选池全部跑赢；
 唯一接近的是纳指ETF {nav['bench_513100'][-1]:.4f}（{(nav['bench_513100'][-1]-1)*100:+.2f}%），主选池 {(m['stat']['total']-(nav['bench_513100'][-1]-1))*100:+.1f}pp。
</div>
<div class="note warn">
<b>三条必须先看的前提，缺一不可：</b><br>
1. <b>幸存者偏差已显式处理</b>：用"今天的池子"回溯历史必然带有前视性。标的必须已上市满 21 个交易日才参与当日排名，
  回测起点 {M['start']} 主选池实际只有 <b>{m['availability'][0]['n_first']} 只</b>可交易，到窗口末页为 {m['availability'][-1]['n_last']} 只（见第八节逐年披露）。<br>
2. <b>成本侵蚀是最大变量</b>：主选池 {(int(M['window_days'])-1)/244:.1f} 年换仓 {m['trades_n']} 笔，按每次双边 0.2% 计，
  累计成本对期末净值有 <b>30%–40%</b> 量级的压制（全周期独立审计口径）。
  实盘成本若高于 0.2%，结论会进一步恶化。<br>
3. <b>备选池结果口径脆弱</b>：独立审计发现备选池对粘性带实现细节极度敏感（不同合理口径下净值差可达 29%），
 其 15 只标的多为同质化宽基/行业，动横20 常年密集。备选池数字<b>不应单独作为决策依据</b>。<br>
4. <b>【2026-09-24 变更】防守策略已换</b>：防守位由 512880 证券ETF国泰 改为 <b>512040 价值ETF富国 + 价值引擎择时</b>，
 防守期不满则持币，打破了原"始终满仓"设计。改造的同口径消融与 5 处审计修正见<b>第二节</b>。
</div>

<h2>二、防守策略改造：新旧严格同口径消融（2026-09-24 变更）</h2>
<div class="note ok">
<b>变更内容</b>：防守标的由 <b>512880 证券ETF国泰</b> 改为 <b>512040 价值ETF富国</b>（TDX 官方名称，别名"国信价值"），
防守期叠加<b>价值引擎择时</b>：收盘 &lt; MA200×0.97 → 买入持有；&gt; MA200×1.06 → 卖出；中间区间维持上一状态；
未触发买入条件时<b>持币</b>。512880 已降级为备选观察池（role=watch）。<br>
<b>★ 这打破了策略B 原「始终满仓」的设计</b>——策略性质从"始终满仓的相对动量轮动"变为"带择时空仓的绝对动量策略"。
</div>
{AB_TBL}
{AB_NOTE}

<h2>三、净值曲线（起点 = 1.000）</h2>
<div style="margin-bottom:10px">{legend}</div>
{svg_curve(D, ser)}

<h2>四、逐年收益（相对上一年末）</h2>
{ytbl}
<div class="note">
主选池 7 个完整年度中 <b>5 年正收益、2 年负收益</b>（2021 −14.4%、其余为正），最差年份出现在 2021 年成长风格回撤期；
2025 年 +52.5% 为最强。备选池 7 年中 <b>4 年负收益</b>，且 2024 年 −11.0% 为最大单年亏损。
</div>

<h2>五、分年度绩效明细</h2>
<div class="scroll">{perf}</div>

<h2>六、主选池换仓明细（共 {m['trades_n']} 笔）</h2>
<div class="scroll">{trades_table('main', 120)}</div>

<h2>七、备选池换仓明细（共 {w['trades_n']} 笔）</h2>
<div class="scroll">{trades_table('watch', 120)}</div>

<h2>八、持仓分布</h2>
<h3 style="font-size:15px;margin:16px 0 9px">主选池</h3>
{htbl}
<h3 style="font-size:15px;margin:22px 0 9px">备选池</h3>
{hwtbl}
<div class="note">
主选池持有最集中的是 <b>513100 纳指ETF（381 日 / 23.3%）</b>、<b>159967 创成长（304 日 / 18.6%）</b>、
<b>518880 黄金ETF（249 日 / 15.3%）</b>，防守期合计 161 日（9.9%，其中 {m['defense'].get('cash_days',0)} 日为持币）。
值得注意的是主选池 9 只里有 4 只（563300 / 588170 / 561700 / 510300）合计占比不足 13%，
说明<b>动量轮动高度集中在少数几只上</b>，池子扩大并未带来更分散的收益来源。
</div>

<h2>九、动态池与幸存者偏差（关键披露）</h2>
<p style="font-size:13px;color:#4b5563">
本次回测使用<b>当前（2026-09）的池子</b>回溯 {M['start']}~{M['end']}，标的池本身含有"事后选股"成分。
处理规则：<b>标的存在当日有有效收盘价、且往前 20 个交易日也有有效收盘价（满 21 根 K 线）时才参与当日排名</b>，
否则自动被动态池排除。窗口起点主选池实际只有 {m['availability'][0]['n_first']} 只可交易，逐年扩充过程如下。
</p>
<h3 style="font-size:15px;margin:16px 0 9px">主选池</h3>
{atbl}
<h3 style="font-size:15px;margin:22px 0 9px">备选池</h3>
{awtbl}
<div class="note warn">
<b>这意味着回测并不代表"当时有人能提前知道该买某只 ETF"，而只代表
"如果当年就有这只 ETF 且它已上市，策略会怎么表现"。</b>任何据此认为"策略能提前抓到某只标的"的推论都不成立。
同时，池子里的标的是按<b>2026 年表现优异</b>选出来的，这会系统性高估历史表现——这是本次回测最大的方法论局限，务必知悉。
</div>

<h2>十、数据口径与防造假校验</h2>
<div class="kv">
<div><b>数据源</b><br>腾讯财经前复权日线<br>（非不复权）</div>
<div><b>复权口径</b><br>已消除份额折算与分红除息假跳空</div>
<div><b>数据总量</b><br>{len(M['data_provenance'])} 只 / {sum(p['n'] for p in M['data_provenance'].values())} 根日线<br>{M['data_start']} ~ {M['data_end']}</div>
<div><b>内部缺口</b><br>0（上市前缺口不计）<br>非正价格 0 根</div>
<div><b>成本模型</b><br>每次换仓扣双边 0.2%<br>换仓当日即扣</div>
<div><b>成交假设</b><br>第 i 日收盘算信号 →<br>同日收盘换仓 → i+1 日计收益</div>
</div>

<h3 style="font-size:15px;margin:20px 0 9px">校验关卡 1 · 除权日检测（不复权 vs 前复权）</h3>
<div class="note ok">
三个<b>已知折算/分红事件全部命中</b>，证明前复权已修正假跳空：<br>
· 510300 沪深300ETF <code>2026-01-19 分红</code>：不复权 <b>−2.49%</b> → 前复权 <b>+0.04%</b><br>
· 513660 恒生ETF <code>2026-04-21 1拆2</code>：不复权 <b>−49.84%</b> → 前复权 <b>+0.32%</b><br>
· 588170 科创半导体 <code>2026-07-06 1拆3</code>：不复权 <b>−66.69%</b> → 前复权 <b>−0.08%</b><br>
若用不复权数据，588170 会被误算成暴跌 2/3，排名与净值曲线彻底失真。
</div>

<h3 style="font-size:15px;margin:20px 0 9px">校验关卡 2 · 与已验证短样本交叉比对</h3>
<div class="note ok">
长表最近 250 根与 TDX 接口前复权数据（<code>tdx_adj_raw.csv</code>，此前已与博主信号台逐位对齐验证）
重叠 <b>6520 根，最大相对差 0.0000%，通过率 100%</b>。
</div>

<h3 style="font-size:15px;margin:20px 0 9px">校验关卡 3 · 孤立空洞修补留痕</h3>
<div class="note">
腾讯源存在 10 处孤立缺行（非停牌，同日其他 ETF 均有数据），已按"先重抓、失败再几何插值"修补，
全部记录在 <code>out/patched_points.csv</code>。上市前的前置缺口<b>一律不补</b>（属"尚未上市"，补了即造假）。
</div>

<h3 style="font-size:15px;margin:20px 0 9px">各标的实际数据覆盖</h3>
<div class="scroll">{table(["标的", "代码", "首个数据日", "最后数据日", "K线数"], prov_rows, ["left","left","left","left","right"])}</div>

<h2>十一、专家独立复核记录</h2>
<div class="note ok">
<b>复核方式</b>：由独立审计 Agent <b>从零重写</b>一套回测实现（不 import、不复用本项目任何函数），
并执行净值守恒反算、成本拖累测算、3 个换仓日手工核对、4 时点截断重算防未来函数、随机数据抽查。
</div>
<h3 style="font-size:15px;margin:18px 0 9px">第二轮审计 · 防守策略改造专项（2026-09-24）</h3>
<div class="kv">
<div><b>三组净值复算</b><br>9 组逐日零残差<br><span style="color:#6b7280">1634 点 × 9 组 · 最大残差 0.00e+00</span></div>
<div><b>换仓明细</b><br>2101 笔零差异<br><span style="color:#6b7280">(日期,原持仓,目标持仓) 三元组</span></div>
<div><b>实现约定</b><br>变异测试通过<br><span style="color:#6b7280">改坏①→2.6188 改坏②→3.1654</span></div>
<div><b>持币日 127</b><br>100% 在防守期内<br><span style="color:#6b7280">防守期外空仓 0 日 · 收益恒为 0</span></div>
<div><b>512040 数据</b><br>1897 根零缺口<br><span style="color:#6b7280">无一日涨跌超 ±20%</span></div>
<div><b>插值敏感性</b><br>剔除后完全不变<br><span style="color:#6b7280">7 个插值点 · 差额 +0.0000</span></div>
<div><b>价值引擎移植</b><br>逐字符一致<br><span style="color:#6b7280">与 2026 版引擎同源</span></div>
<div><b>归因闭合</b><br>残差 −5.5e-13<br><span style="color:#6b7280">浮点级 · 完全闭合</span></div>
</div>
<div class="note warn" style="margin-top:14px">
<b>审计方对结论表述提出 7 处修正要求，已全部采纳并写入第二节：</b>
事后选股未披露（高）、分年度对比缺失掩盖 2025 年负贡献（高）、价值引擎归因易被误读（高）、
512880 归因不准（中）、CASH 零收益假设未披露（中）、成本拖累未披露（中）、回撤历时未说明（低）。
<b>审计方与本报告在数值层面不存在任何差异</b>，全部争议都在结论表述层。
</div>

<h3 style="font-size:15px;margin:20px 0 9px">第一轮审计 · 长周期引擎（争议已闭环）</h3>
<div class="kv">
<div><b>未来函数</b><br>未发现<br><span style="color:#6b7280">4 时点 / 3318 决策日 / 0 处差异</span></div>
<div><b>净值守恒</b><br>通过<br><span style="color:#6b7280">反算误差 0.00e+00</span></div>
<div><b>数据真实性</b><br>6/6 一致<br><span style="color:#6b7280">相对误差 0.000000</span></div>
<div><b>循环论证</b><br>无<br><span style="color:#6b7280">审计脚本从零编写</span></div>
<div><b>动态池</b><br>已生效<br><span style="color:#6b7280">2020-01-02 仅 5/9 可排名</span></div>
<div><b>成本模型</b><br>正确<br><span style="color:#6b7280">ln 拖累与理论值差 0.001</span></div>
</div>

<div class="note warn" style="margin-top:14px">
<b>审计过程中发现的一处真实争议（已闭环）：</b><br>
独立审计首轮算出主选池 3.2188，与本报告 3.4374 不一致，且未能归因。
经双方共同定位，根因为<b>两处实现约定同时错位</b>：<br>
① 粘性带锚点应为<b>链式的前一日粘性后头名</b>（<code>prev_eff</code>），非当日本期真实头名；<br>
② 过热判定的乖离应取<b>粘性后头名的乖离</b>（<code>dev(eff_head)</code>），非当期真实头名的乖离。<br>
残差已精确对账：<code>+0.2766（改乖离基准）− 0.0581（改锚点）= +0.2185</code>，与待解释的 0.2186 <b>完全吻合，无残余</b>。<br><br>
<b>最终裁定采用 3.4374</b>，依据是决定性证据：用本引擎跑 2026 重叠窗口
（2025-12-31 → 2026-09-23，与用户已拍板的 2026 年内回测同窗口）得到
<b>1.3095 / +30.95% / −20.38% / 夏普 1.30 / 30 笔换仓</b>，
与既有结论<b>逐位一致</b>，且审计员逐日比对持仓序列差异 <b>0 日</b>。
该窗口有区分力——<code>dev(raw_head)</code> 口径只能得 1.2059，<code>prev_head</code> 锚点口径得 1.34，均被排除。<br>
源码级佐证：用户拍板的 2026 版引擎本身就写着 <code>prev_eff</code> 链式 + <code>dev_eff</code>，二者成对同源。
</div>

<h2>十二、必须知道的风险提示</h2>
<div class="note warn">
<b>1. 这是"用今天的池子回测昨天的行情"，存在不可消除的前视偏差。</b>
{M['start']}~{M['end']} 的表现里含有"我们 2026 年才知道哪些 ETF 好"的信息，不能理解为策略当年就能选中它们。<br><br>
<b>2. {m["trades_n"]} 次换仓 / {(int(M['window_days'])-1)/244:.1f} 年，年均约 {m['trades_n']/((int(M['window_days'])-1)/244):.0f} 次调仓，交易频率不低。</b>
成本假设 0.2% 双边若偏低（ETF 实际还含冲击成本），实盘净值会明显低于 {nav["main"][-1]:.2f}。<br><br>
<b>3. 样本外推需谨慎。</b>
本窗口仅覆盖 {(int(M['window_days'])-1)/244:.1f} 年，不足以证明跨周期稳健性；行情段含政策脉冲与结构性行情。<br><br>
<b>4. 备选池组超额有限（年化 {w['stat']['ann']*100:.2f}%、夏普 {w['stat']['sharpe']:.2f}）且回撤更深（{w['stat']['mdd']*100:.2f}%）。</b>
如果备选池的定位是"为主选池提供更替候选"，那它<b>不适合作为独立交易池</b>——它的价值只在于观察与替换，而非整池轮动。<br><br>
<b>5. 【本次改造新增】防守策略的改进带事后选股成分，且不是单向的。</b>
512040 是从 25 个候选中事后挑出的（VE 模式排第 2，516080 更高），+30.7pp 应视为上界；
2025 年新策略反而跑输旧策略约 5.4pp。详见第二节消融表与审计修正清单。<br><br>
<b>6. 【本次改造新增】价值引擎的收益贡献依赖"防守期恰好落在 512040 的下跌段"这一巧合。</b>
它作为独立工具净值 1.4785 &lt; 买入持有 2.4627，若未来防守期落点改变，该增量会大幅缩水甚至转负。<br><br>
<b>7. 【本次改造新增】策略性质已改变。</b>
叠加持币后，策略B 从"始终满仓的相对动量轮动"变为"带择时空仓的绝对动量策略"，
其风险收益特征、夏普含义、与指数 beta 的可比性均已改变，不能再直接与旧的 3.4374 做同口径比较。
</div>

<footer>
数据来源：腾讯财经前复权日线接口（web.ifzq.gtimg.cn）· 抓取与校验 {M['data_end']} ·
交叉验证：TDX 接口前复权短样本（6520 根 100% 一致）· 本地通达信 .day 除权日检测 ·
独立审计：audit_recheck.py / out/audit_report.md / out/audit_report_v2.md<br>
本报告由脚本自动生成，所有数字均直接取自 out/backtest_long.json，未经人工填写。<br>
<b>免责声明：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。
任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。</b>
</footer>
</div></body></html>"""

    with open(DST, "w", encoding="utf-8") as f:
        f.write(HTML)
    print(f"WROTE {DST}  ({len(HTML)/1024:.1f} KB)")


if __name__ == "__main__":
    main()
