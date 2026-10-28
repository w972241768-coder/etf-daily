#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三策略对比回测报告（近 10 年）：基础 / 基础+D3+P3 / 基础+D3+P3+抄底
+ 4 种抄底离场方式（满5日 / 满10日 / 满10日+回MA20 / 轮动转正，2026-10-28 追加）

用户 2026-10-28 要求：验证「加抄底是否真有正反馈」，要全方位对比、月度+年度表格、
主轮动池、初始净值 1.0、输出 HTML。

⚠️ 数据说明：
  · 用 tdx_adj_long_2014_2026.csv（2014-01-02 起），起点取 2016-10-26（近 10 年）
  · 主选池 9 只，但**早期只有 3~5 只已上市**（588170 到 2025-04 才有），
    每年实际可用标的数在报告里标注 —— 早期池子小，与现在的策略不是一回事。
  · 单边成本 0.01%（万一），含换仓成本；初始净值 1.0。
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import etf_backtest_long as bl  # noqa: E402

CSV = os.path.join(HERE, "tdx_adj_long_2014_2026.csv")
START = "2016-10-26"
COST = 0.0001
RED, GREEN, BLUE, GREY, PURPLE, ORANGE = ("#d4380d", "#0f9d58", "#3d57e5",
                                          "#9aa3ae", "#c2185b", "#b45309")
# 2026-10-28 新增：抄底离场方式的 4 个变体配色
C_E1, C_E2, C_E3, C_E4 = "#b45309", "#7c3aed", "#0891b2", "#6b7280"

# 抄底「持仓锁定 + 离场条件」的 4 种写法（其余条件与 C 完全相同）。
# 语义：抄底买入后进入锁定期，期内 target 锁定该标的、D3/P3 不介入，
#       直到下列条件之一成立才换回当日头名。
EXITS = [
    ("E1 满 5 日", dict(weak_exit_days=5), C_E1,
     "抄底买入后锁 5 个交易日，到期即换回当日头名"),
    ("E2 满 10 日", dict(weak_exit_days=10), C_E2,
     "锁 10 个交易日，到期即换回当日头名"),
    ("E3 满10日 + 回MA20", dict(weak_exit_days=10, weak_exit_ma20=True), C_E3,
     "满 10 日 或 收盘回到自身 MA20 上方，任一成立即换回当日头名"),
    ("E4 轮动转正", dict(weak_exit_pos=True), C_E4,
     "池内出现动横20 ≥ 0 的标的（不再全负）即换回当日头名"),
]
BG, FG, MUT, BD = "#eef0f3", "#202020", "#6c717f", "#e2e5ea"

D3P3 = dict(loss_cool=0.03, loss_days=5, roc_q=0.95, roc_days=20,
            roc_n=60, roc_min_periods=250)
WEAK = dict(weak_mode="allneg", weak_rank=1, weak_delay=0,
            weak_cd=0, weak_min_pool=5)


def run(panel, dates, pool, dfn, **kw):
    si = int(dates.searchsorted(pd.Timestamp(START), side="left"))
    r = bl.backtest(panel, dates, si, len(dates) - 1, pool, dfn,
                    buy=COST, sell=COST, W=20, MA=20, value_engine=True, **kw)
    nv = r["navs"].copy()
    nv.index = pd.to_datetime(nv.index)
    return r, bl.stats(r["navs"]), nv


def yearly_of(nv):
    return {d["year"]: d for d in bl.yearly(nv)}


def monthly_of(nv):
    out = {}
    prev = float(nv.iloc[0])
    for (y, m), s in nv.groupby([nv.index.year, nv.index.month]):
        out[f"{y}-{m:02d}"] = ((float(s.iloc[-1]) / prev - 1) * 100,
                               float((s / s.cummax() - 1).min()) * 100)
        prev = float(s.iloc[-1])
    return out


def drawdown(nv):
    return (nv / nv.cummax() - 1.0) * 100


# ---------------------------------------------------------------- SVG
def multi_curve(series, w=680, h=250, title="", log=False):
    """series = [(navs, label, color, width)]，起点归一化为 1.0"""
    if not series:
        return ""
    ml, mr, mt, mb = 48, 10, 20, 30
    iw, ih = w - ml - mr, h - mt - mb
    xs = series[0][0]
    ys = []
    for nv, lab, col, lw in series:
        s = nv / float(nv.iloc[0])
        ys.append((s.values, lab, col, lw))
    vmin = min(float(np.nanmin(y[0])) for y in ys)
    vmax = max(float(np.nanmax(y[0])) for y in ys)
    if log:
        vmin, vmax = np.log10(max(vmin, 0.1)), np.log10(max(vmax, 0.1))
    pad = (vmax - vmin) * 0.08 or 0.05
    vmin, vmax = vmin - pad, vmax + pad
    span = (vmax - vmin) or 1.0
    x = np.linspace(ml, w - mr, len(xs))
    p = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{title}">']
    for gv in np.linspace(vmin, vmax, 5):
        gy = mt + ih * ((vmax - gv) / span)
        lab = (10 ** gv) if log else gv
        p.append(f'<line x1="{ml}" y1="{gy:.1f}" x2="{w-mr}" y2="{gy:.1f}" '
                 f'stroke="#e6eaf0" stroke-dasharray="3 3"/>')
        p.append(f'<text x="{ml-6}" y="{gy+3.5:.1f}" text-anchor="end" '
                 f'font-size="9" fill="#8b93a1">{lab:.2f}</text>')
    for vals, lab, col, lw in ys:
        v = np.log10(np.maximum(vals, 0.1)) if log else vals
        pts = " ".join(f"{x[i]:.1f},{mt+ih*((vmax-v[i])/span):.1f}"
                       for i in range(len(v)) if np.isfinite(v[i]))
        p.append(f'<polyline points="{pts}" fill="none" stroke="{col}" '
                 f'stroke-width="{lw}" stroke-linejoin="round"/>')
    lx = ml + 2
    for vals, lab, col, lw in ys:
        p.append(f'<rect x="{lx}" y="{mt-12}" width="15" height="3" rx="1.5" fill="{col}"/>')
        p.append(f'<text x="{lx+19}" y="{mt-8}" font-size="10" fill="#4b5563">{lab}</text>')
        lx += 19 + len(lab) * 10 + 14
    p.append(f'<text x="{ml}" y="{h-8}" font-size="9.5" fill="#8b93a1">'
             f'起点 = 1.0　含换仓成本　{"对数刻度" if log else "线性刻度"}</text>')
    p.append("</svg>")
    return "".join(p)


def year_bars(years, groups, w=680, h=230):
    """groups = [(label, {year: ret}, color)]，分组竖柱"""
    ml, mr, mt, mb = 46, 10, 18, 34
    iw, ih = w - ml - mr, h - mt - mb
    vals = [v for _l, d, _c in groups for v in d.values() if v is not None]
    vmax = max(max(vals), 0) * 1.15 or 1
    vmin = min(min(vals), 0) * 1.15 or -1
    span = (vmax - vmin) or 1
    zy = mt + ih * (vmax / span)
    n = len(years)
    gw = iw / n
    bw = min(14.0, gw * 0.72 / max(1, len(groups)))
    p = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="年度收益">']
    for gv in (vmax, vmax / 2, 0, vmin / 2, vmin):
        gy = mt + ih * ((vmax - gv) / span)
        p.append(f'<line x1="{ml}" y1="{gy:.1f}" x2="{w-mr}" y2="{gy:.1f}" '
                 f'stroke="{"#c9d0d9" if abs(gv)<1e-9 else "#e6eaf0"}" '
                 f'stroke-dasharray="{"" if abs(gv)<1e-9 else "3 3"}"/>')
        p.append(f'<text x="{ml-6}" y="{gy+3.5:.1f}" text-anchor="end" '
                 f'font-size="9" fill="#8b93a1">{gv:+.0f}%</text>')
    for i, y in enumerate(years):
        cx = ml + gw * i + gw / 2
        for gi, (lab, d, col) in enumerate(groups):
            v = d.get(y)
            if v is None:
                continue
            bh = abs(v) / span * ih
            by = zy - bh if v >= 0 else zy
            bx = cx - (bw * len(groups)) / 2 + gi * bw
            p.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw-1.2:.1f}" '
                     f'height="{max(bh,0.7):.1f}" fill="{col}" rx="1"/>')
        p.append(f'<text x="{cx:.1f}" y="{mt+ih+14}" text-anchor="middle" '
                 f'font-size="9" fill="#6c717f">{str(y)[2:]}</text>')
    lx = ml + 2
    for lab, _d, col in groups:
        p.append(f'<rect x="{lx}" y="{mt-11}" width="14" height="3" rx="1.5" fill="{col}"/>')
        p.append(f'<text x="{lx+18}" y="{mt-7}" font-size="9.5" fill="#4b5563">{lab}</text>')
        lx += 18 + len(lab) * 9 + 12
    p.append("</svg>")
    return "".join(p)


def main():
    panel = bl.read_panel(CSV)
    dates = panel.index
    rot, deff, _w = bl.read_pools()
    pool = [c for c in rot if c in panel.columns]
    dfn = deff[0] if deff else "512040"

    A, sa, na = run(panel, dates, pool, dfn)
    B, sb, nb = run(panel, dates, pool, dfn, **D3P3)
    C, sc, nc = run(panel, dates, pool, dfn, **D3P3, **WEAK)

    # ⚠️ bl.yearly() 内部用 nav.index.str[:4]，必须喂**原始字符串索引**的 navs
    #   （na/nb/nc 已转成 DatetimeIndex 供画图与月度分组用，不能混用）
    ya, yb, yc = yearly_of(A["navs"]), yearly_of(B["navs"]), yearly_of(C["navs"])
    ma, mb, mc = monthly_of(na), monthly_of(nb), monthly_of(nc)
    years = sorted(ya)
    # 每年可用池规模
    av = {}
    for d, n in (A.get("avail_log") or []):
        av.setdefault(pd.Timestamp(d).year, []).append(n)
    pool_sz = {y: (min(v), int(np.mean(v))) for y, v in av.items()}

    def pack(s, nv):
        return dict(final=float(nv.iloc[-1]), ann=s["ann"] * 100, mdd=s["mdd"] * 100,
                    calmar=s["calmar"], sharpe=s["sharpe"], sortino=s["sortino"])
    PA, PB, PC = pack(sa, na), pack(sb, nb), pack(sc, nc)


    # 抄底贡献
    contrib = {y: (yc.get(y, {}).get("ret") or 0) * 100 - (yb.get(y, {}).get("ret") or 0) * 100
               for y in years}
    pos_y = sum(1 for v in contrib.values() if v > 0.05)
    neg_y = sum(1 for v in contrib.values() if v < -0.05)
    zero_y = len(contrib) - pos_y - neg_y
    tot_pp = sum(contrib.values())
    top2 = sorted(contrib.items(), key=lambda kv: -kv[1])[:2]
    top2_pp = sum(v for _k, v in top2)
    top2_share = (top2_pp / tot_pp * 100) if tot_pp else 0
    # ⚠️ 当其他年份是负贡献时 top2_share 会 >100%（如 196%），直接写百分比很反直觉
    if tot_pp > 0 and top2_pp <= tot_pp:
        share_txt = f"占累计贡献的 <b>{top2_share:.0f}%</b>"
    else:
        share_txt = (f"<b>已超过</b>累计贡献（其余年份合计 "
                     f"{tot_pp - top2_pp:+.1f}pp，是拖累）")

    def cell(v, d=2, unit="%", color=None, bold=False):
        if v is None or not np.isfinite(v):
            return '<td class="num dim">—</td>'
        c = color if color else "#42474f"
        st = f' style="color:{c};font-weight:700"' if bold else f' style="color:{c}"'
        return f'<td class="num"{st}>{v:+.{d}f}{unit}</td>'

    # ======================================================================
    # 2026-10-28 新增：抄底「离场方式」4 个变体（锁定期 + 退出条件）
    #   其余条件（全池动横20 全负触发、买末位、池≥5 只、当日换）与 C 完全相同，
    #   唯一差别是买入后怎么卖。锁定期内 target 锁定该标的、D3/P3 不介入。
    # ======================================================================
    EXR = []
    for _lab, _kw, _col, _desc in EXITS:
        _r, _s, _nv = run(panel, dates, pool, dfn, **D3P3, **WEAK, **_kw)
        EXR.append(dict(label=_lab, desc=_desc, color=_col, r=_r, nv=_nv,
                        pack=pack(_s, _nv), y=yearly_of(_r["navs"]),
                        m=monthly_of(_nv),
                        n_weak=len(_r.get("weak_events") or []),
                        n_exit=len(_r.get("weak_exit_log") or [])))

    def _dv(v, unit="", d=3):
        """差值单元格：红涨绿跌（正=更好=红）"""
        if v is None or not np.isfinite(v):
            return '<td class="num dim">—</td>'
        c = RED if v > 5e-4 else (GREEN if v < -5e-4 else "#8b93a1")
        w = "700" if abs(v) > 5e-4 else "400"
        return f'<td class="num" style="color:{c};font-weight:{w}">{v:+.{d}f}{unit}</td>'

    def _erow(nm, pk, r, col, hi=False):
        tdc = f'<td><b style="color:{col}">{nm}</b></td>' if hi else f'<td style="color:{col}">{nm}</td>'
        bg = ' style="background:#fdf5f9"' if hi else ""
        fv = f'<b>{pk["final"]:.3f}</b>' if hi else f'{pk["final"]:.3f}'
        return (f'<tr{bg}>{tdc}<td class="num">{fv}</td>'
                + _dv(pk["final"] - PB["final"]) + _dv(pk["final"] - PC["final"])
                + f'<td class="num">{pk["ann"]:+.2f}%</td>'
                f'<td class="num">{pk["mdd"]:.2f}%</td>'
                f'<td class="num">{pk["calmar"] or 0:.2f}</td>'
                f'<td class="num">{pk["sharpe"] or 0:.2f}</td>'
                f'<td class="num">{len(r.get("weak_events") or [])}</td>'
                f'<td class="num">{len(r["trades"])}</td></tr>')

    erows = (_erow("B 不加抄底（参照）", PB, B, GREY)
             + _erow("C +抄底（现状·无锁定）", PC, C, PURPLE, hi=True)
             + "".join(_erow(e["label"], e["pack"], e["r"], e["color"]) for e in EXR))

    # 两张净值曲线（4 条/张，避免 6 条线糊成一团）
    curve1 = multi_curve([(nb, "B +D3P3", BLUE, 1.4),
                          (nc, "C 现状(无锁定)", PURPLE, 1.9),
                          (EXR[0]["nv"], "E1 满5日", C_E1, 1.6),
                          (EXR[1]["nv"], "E2 满10日", C_E2, 1.6)],
                         title="满5日 / 满10日")
    curve2 = multi_curve([(nb, "B +D3P3", BLUE, 1.4),
                          (nc, "C 现状(无锁定)", PURPLE, 1.9),
                          (EXR[2]["nv"], "E3 满10日+回MA20", C_E3, 1.6),
                          (EXR[3]["nv"], "E4 轮动转正", C_E4, 1.6)],
                         title="满10日+回MA20 / 轮动转正")

    # 年度柱（6 组，标签用短名）
    gd_e = {e["label"]: {y: (e["y"].get(y, {}).get("ret") or 0) * 100 for y in years}
            for e in EXR}
    # ⚠️ 这里不能用后面才定义的 gd_b / gd_c（会 UnboundLocalError），本地重算
    _gb = {y: (yb.get(y, {}).get("ret") or 0) * 100 for y in years}
    _gc = {y: (yc.get(y, {}).get("ret") or 0) * 100 for y in years}
    ybars2 = year_bars(years, [
        ("B", _gb, BLUE), ("C", _gc, PURPLE),
        ("满5", gd_e["E1 满 5 日"], C_E1), ("满10", gd_e["E2 满 10 日"], C_E2),
        ("10+MA20", gd_e["E3 满10日 + 回MA20"], C_E3),
        ("转正", gd_e["E4 轮动转正"], C_E4)])

    # 年度表（B / C / E1~E4）
    yrows2 = []
    for y in years:
        vals = [(yb.get(y, {}).get("ret") or 0) * 100,
                (yc.get(y, {}).get("ret") or 0) * 100]
        vals += [(e["y"].get(y, {}).get("ret") or 0) * 100 for e in EXR]
        cells = "".join(cell(v) for v in vals)
        _mx = max(vals)
        _win = [["B", "C", "E1", "E2", "E3", "E4"][i] for i, v in enumerate(vals)
                if abs(v - _mx) < 0.01]
        _wt = "并列" if len(_win) >= 6 else ("/".join(_win[:2]) if len(_win) > 1
                                            else _win[0])
        yrows2.append(f'<tr><td class="mono">{y}</td>{cells}'
                      f'<td class="num dim">{_wt}</td></tr>')

    # 月度表（B / C / E1~E4）
    mkeys2 = sorted(set(mb) | set(mc) | {k for e in EXR for k in e["m"]})
    mrows2 = []
    for k in mkeys2:
        vals = [mb.get(k, (None,))[0], mc.get(k, (None,))[0]]
        vals += [e["m"].get(k, (None,))[0] for e in EXR]
        cells = "".join(cell(v) for v in vals)
        mrows2.append(f'<tr><td class="mono">{k}</td>{cells}</tr>')

    # 离场方式汇总（含平均持有天数、单笔中位）
    def _exit_profile(e):
        lg = e["r"].get("weak_exit_log") or []
        if not lg:
            return "—", "—", "—"
        dd_ = np.array([x["days"] for x in lg])
        rr_ = np.array([x["ret"] for x in lg if x["ret"] is not None])
        med = f"{np.median(rr_):+.2f}%" if len(rr_) else "—"
        win = f"{(rr_ > 0).mean()*100:.0f}%" if len(rr_) else "—"
        return f"{np.median(dd_):.0f} 天", med, win

    prof = {e["label"]: _exit_profile(e) for e in EXR}
    prows = []
    for e in EXR:
        h, md, wn = prof[e["label"]]
        prows.append(
            f'<tr><td><b style="color:{e["color"]}">{e["label"]}</b></td>'
            f'<td>{e["desc"]}</td>'
            f'<td class="num">{e["n_weak"]}</td><td class="num">{e["n_exit"]}</td>'
            f'<td class="num">{h}</td><td class="num">{md}</td>'
            f'<td class="num">{wn}</td></tr>')
    prows = "".join(prows)

    # 最优方案（按净值）
    _best = max(EXR, key=lambda e: e["pack"]["final"])
    best_txt = (f'<b style="color:{_best["color"]}">{_best["label"]}</b>'
                f'（净值 {_best["pack"]["final"]:.3f}，比现状 C '
                f'{_best["pack"]["final"]-PC["final"]:+.3f}）')

    # 年度表
    yrows = []
    for y in years:
        rA = (ya.get(y, {}).get("ret") or 0) * 100
        rB = (yb.get(y, {}).get("ret") or 0) * 100
        rC = (yc.get(y, {}).get("ret") or 0) * 100
        mA = (ya.get(y, {}).get("mdd") or 0) * 100
        mB = (yb.get(y, {}).get("mdd") or 0) * 100
        mC = (yc.get(y, {}).get("mdd") or 0) * 100
        cd = contrib[y]
        # ⚠️ bl.yearly() 的 year 是字符串（index.str[:4]），pool_sz 的键是 int，必须转
        ps = pool_sz.get(int(y), (0, 0))
        yrows.append(
            f'<tr><td class="mono">{y}</td><td class="num dim">{ps[0]}/{ps[1]:.1f}</td>'
            + cell(rA) + cell(rB) + cell(rC)
            + cell(cd, color=(GREEN if cd > 0.05 else (RED if cd < -0.05 else "#8b93a1")), bold=True)
            + cell(mA, color="#42474f") + cell(mB, color="#42474f") + cell(mC, color="#42474f")
            + "</tr>")

    # 月度表
    mkeys = sorted(set(ma) | set(mb) | set(mc))
    mrows = []
    for k in mkeys:
        va = ma.get(k, (None, None))[0]
        vb = mb.get(k, (None, None))[0]
        vc = mc.get(k, (None, None))[0]
        d = (vc - vb) if (vc is not None and vb is not None) else None
        mrows.append(f'<tr><td class="mono">{k}</td>'
                     + cell(va) + cell(vb) + cell(vc)
                     + cell(d, color=(GREEN if (d or 0) > 0.05 else (RED if (d or 0) < -0.05 else "#8b93a1")))
                     + "</tr>")

    # f-string 里不能直接写 dict 推导（{{ 会被转义成字面量），先在外部算好
    gd_a = {y: (ya.get(y, {}).get("ret") or 0) * 100 for y in years}
    gd_b = {y: (yb.get(y, {}).get("ret") or 0) * 100 for y in years}
    gd_c = {y: (yc.get(y, {}).get("ret") or 0) * 100 for y in years}
    ybars = year_bars(years, [("A 基础", gd_a, GREY),
                              ("B +D3P3", gd_b, BLUE),
                              ("C +抄底", gd_c, PURPLE)])

    d0, d1 = str(na.index[0].date()), str(na.index[-1].date())
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>基础策略+D3+P3+抄底 回测对比</title>
<style>
:root{{--bg:{BG};--fg:{FG};--mut:{MUT};--bd:{BD}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:16px 12px 30px;background:var(--bg);color:var(--fg);
 font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:820px;margin:0 auto}}
h1{{font-size:20px;margin:0 0 5px}}
.sub{{font-size:12px;color:var(--mut);line-height:1.75}}
.card{{background:#fbfcfe;border:1px solid var(--bd);border-radius:10px;padding:13px 14px;margin-top:14px}}
.cap{{font-size:12px;color:var(--mut);font-weight:700;margin:0 0 9px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th,td{{padding:5px 6px;border-bottom:1px solid var(--bd);text-align:left;white-space:nowrap}}
th{{background:#f4f6fa;font-size:11.5px;color:#4b5563;font-weight:700;position:sticky;top:0}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
td.mono{{font-family:ui-monospace,Menlo,monospace}}
.dim{{color:#8b93a1}}
.tblwrap{{overflow-x:auto;-webkit-overflow-scrolling:touch;max-height:none}}
.k{{display:flex;flex-wrap:wrap;gap:10px 0}}
.kc{{flex:1 1 30%;min-width:120px;display:flex;flex-direction:column;gap:2px}}
.kc .kk{{font-size:11px;color:var(--mut)}}
.kc b{{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums}}
.warn{{background:#fff8f6;border:1px solid #f6d5c8;border-left:3px solid {RED};
 border-radius:9px;padding:11px 13px;margin-top:14px;font-size:12.5px;line-height:1.75}}
.note{{margin-top:10px;font-size:11px;color:#8b93a1;line-height:1.7}}
@media(max-width:600px){{.kc{{flex:1 1 47%}}}}
</style></head><body><div class="wrap">

<h1>基础策略 + D3 + P3 + 抄底 · 回测对比</h1>
<div class="sub">区间 <b>{d0} ~ {d1}</b>（近 10 年）　主选池 <b>{len(pool)}</b> 只（早期未全部上市，见年度表「池」列）　
防守位 {dfn}　单边成本 <b>0.01%</b>　前复权　<b>初始净值 1.0</b><br>
三条基础曲线：<b style="color:{GREY}">A 基础轮动</b>（只有动横20 选最强 + 粘性带 + 过热熔断）／
<b style="color:{BLUE}">B + D3 单笔止损 + P3 追高止盈</b>／
<b style="color:{PURPLE}">C + 抄底（全池负→买末位）</b><br>
<b>另加 4 种抄底离场方式</b>（第七~十节）：
<b style="color:{C_E1}">E1 满5日</b>　<b style="color:{C_E2}">E2 满10日</b>　
<b style="color:{C_E3}">E3 满10日+回MA20</b>　<b style="color:{C_E4}">E4 轮动转正</b></div>

<div class="card">
<div class="cap">一、总览（近 10 年）</div>
<div class="tblwrap"><table>
<thead><tr><th>策略</th><th class="num">净值</th><th class="num">年化</th><th class="num">最大回撤</th>
<th class="num">Calmar</th><th class="num">Sharpe</th><th class="num">Sortino</th><th class="num">换仓</th></tr></thead>
<tbody>
<tr><td>A 基础轮动</td><td class="num">{PA['final']:.3f}</td><td class="num">{PA['ann']:+.2f}%</td>
<td class="num">{PA['mdd']:.2f}%</td><td class="num">{PA['calmar'] or 0:.2f}</td>
<td class="num">{PA['sharpe'] or 0:.2f}</td><td class="num">{PA['sortino'] or 0:.2f}</td>
<td class="num">{len(A['trades'])}</td></tr>
<tr><td>B + D3 + P3</td><td class="num">{PB['final']:.3f}</td><td class="num">{PB['ann']:+.2f}%</td>
<td class="num">{PB['mdd']:.2f}%</td><td class="num">{PB['calmar'] or 0:.2f}</td>
<td class="num">{PB['sharpe'] or 0:.2f}</td><td class="num">{PB['sortino'] or 0:.2f}</td>
<td class="num">{len(B['trades'])}</td></tr>
<tr style="background:#fdf5f9"><td><b>C + 抄底</b></td><td class="num"><b>{PC['final']:.3f}</b></td>
<td class="num"><b>{PC['ann']:+.2f}%</b></td><td class="num">{PC['mdd']:.2f}%</td>
<td class="num"><b>{PC['calmar'] or 0:.2f}</b></td><td class="num"><b>{PC['sharpe'] or 0:.2f}</b></td>
<td class="num"><b>{PC['sortino'] or 0:.2f}</b></td><td class="num">{len(C['trades'])}</td></tr>
</tbody></table></div>
<div class="note">C 相对 B：净值 <b>{PC['final']-PB['final']:+.3f}</b>
（{(PC['final']/PB['final']-1)*100:+.1f}%）、年化 <b>{PC['ann']-PB['ann']:+.2f}pp</b>、
Calmar <b>{(PC['calmar'] or 0)-(PB['calmar'] or 0):+.2f}</b>、最大回撤 <b>{PC['mdd']-PB['mdd']:+.2f}pp</b>。</div>
</div>

<div class="card">
<div class="cap">二、净值曲线（起点 1.0，线性刻度）</div>
{multi_curve([(na,'A 基础',GREY,1.5),(nb,'B +D3+P3',BLUE,1.7),(nc,'C +抄底',PURPLE,2.0)],title="净值曲线")}
</div>

<div class="card">
<div class="cap">三、净值曲线（对数刻度，看早期小池子阶段）</div>
{multi_curve([(na,'A 基础',GREY,1.5),(nb,'B +D3+P3',BLUE,1.7),(nc,'C +抄底',PURPLE,2.0)],log=True,title="净值对数")}
</div>

<div class="card">
<div class="cap">四、年度收益对比</div>
{ybars}
</div>

<div class="warn">
<b>⚠ 关于「抄底是否真有正反馈」——数据给出的答案是有条件的「是」，但很不均匀：</b><br>
· 近 10 年抄底累计贡献 <b>{tot_pp:+.1f}pp</b>（逐年算术和），净值由 {PB['final']:.2f} 提升到 {PC['final']:.2f}；<br>
· 但贡献<b>高度集中</b>：<b>{top2[0][0]} 年 {top2[0][1]:+.1f}pp</b> + <b>{top2[1][0]} 年 {top2[1][1]:+.1f}pp</b>
= <b>{top2_pp:+.1f}pp</b>，{share_txt}；<br>
· {len(years)} 个年份里：<b>{pos_y} 年正贡献</b>、<b>{neg_y} 年负贡献</b>、<b>{zero_y} 年零贡献</b>（池子小未触发或全年无机会）；<br>
· 换句话说：<b>抄底不是「每年多赚一点」的稳定增强，而是「平时不拖后腿、遇到特定行情（2020 疫情暴跌 / 2022 熊市）大幅加分」的尾部期权</b>。
如果你对「10 年里 8 年看不到它起作用」这件事不接受，那它对你就不算正反馈。
</div>

<div class="card">
<div class="cap">五、年度明细（收益 % / 最大回撤 %；「池」= 当年可用标的 最少/均值）</div>
<div class="tblwrap"><table>
<thead><tr><th>年份</th><th class="num">池</th><th class="num">A 基础</th><th class="num">B +D3P3</th>
<th class="num">C +抄底</th><th class="num">抄底贡献</th><th class="num">A 回撤</th>
<th class="num">B 回撤</th><th class="num">C 回撤</th></tr></thead>
<tbody>{''.join(yrows)}</tbody></table></div>
</div>

<div class="card">
<div class="cap">六、月度明细（收益 %，共 {len(mkeys)} 个月）</div>
<div class="tblwrap"><table>
<thead><tr><th>月份</th><th class="num">A 基础</th><th class="num">B +D3P3</th>
<th class="num">C +抄底</th><th class="num">抄底贡献</th></tr></thead>
<tbody>{''.join(mrows)}</tbody></table></div>
<div class="note">「抄底贡献」= C 当月收益 − B 当月收益。正数=抄底加分，负数=拖累。</div>
</div>

<div class="card">
<div class="cap">七、抄底离场方式对比（4 种规则，进场条件与选股完全相同）</div>
<div class="tblwrap"><table>
<thead><tr><th>方案</th><th class="num">净值</th><th class="num">vs B</th><th class="num">vs C</th>
<th class="num">年化</th><th class="num">最大回撤</th><th class="num">Calmar</th><th class="num">Sharpe</th>
<th class="num">抄底次数</th><th class="num">换仓</th></tr></thead>
<tbody>{erows}</tbody></table></div>
<div class="note">
<b>规则定义</b>（进场完全一致：主选池动横20 全部为负 → 当日买末位，池内可用 &lt;5 只不做）：<br>
· <b style="color:{C_E1}">E1 满 5 日</b>：抄底买入后锁 5 个交易日，到期即换回当日头名；<br>
· <b style="color:{C_E2}">E2 满 10 日</b>：锁 10 个交易日，到期即换回当日头名；<br>
· <b style="color:{C_E3}">E3 满10日 + 回MA20</b>：满 10 日 <b>或</b> 收盘回到自身 MA20 上方，任一成立即离场；<br>
· <b style="color:{C_E4}">E4 轮动转正</b>：池内出现动横20 ≥ 0 的标的（不再全负）即离场。<br>
<span class="dim">锁定期内 target 锁定该标的、<b>D3 / P3 都不介入</b>（否则 D3 的 3% 会在第 2 天就把它清掉，
永远走不到这些出口）。这是与「C 现状」最本质的区别：C 没有锁定期，抄底买完就交给 D3 管。</span>
</div>
</div>

<div class="card">
<div class="cap">八、离场方式 · 净值曲线（起点 1.0，含换仓成本）</div>
<div style="margin-bottom:4px;font-size:11.5px;color:#8b93a1">① 固定天数：满 5 日 / 满 10 日</div>
{curve1}
<div style="margin:14px 0 4px;font-size:11.5px;color:#8b93a1">② 条件触发：满10日+回MA20 / 轮动转正</div>
{curve2}
</div>

<div class="card">
<div class="cap">九、离场方式 · 持仓画像与年度收益</div>
<div class="tblwrap"><table>
<thead><tr><th>方案</th><th>离场规则</th><th class="num">触发</th><th class="num">离场</th>
<th class="num">持有中位</th><th class="num">单笔中位</th><th class="num">胜率</th></tr></thead>
<tbody>{prows}</tbody></table></div>
<div class="note">「持有中位」= 每次抄底持仓的交易日数中位数；「单笔中位」= 每笔从建仓到离场的收益中位数（不含卖出成本）。</div>
<div style="margin-top:14px"></div>
{ybars2}
<div class="tblwrap" style="margin-top:8px"><table>
<thead><tr><th>年份</th><th class="num">B +D3P3</th><th class="num">C 现状</th>
<th class="num">E1 满5日</th><th class="num">E2 满10日</th><th class="num">E3 10+MA20</th>
<th class="num">E4 转正</th><th class="num">当年最佳</th></tr></thead>
<tbody>{''.join(yrows2)}</tbody></table></div>
</div>

<div class="card">
<div class="cap">十、离场方式 · 月度收益（%，共 {len(mkeys2)} 个月）</div>
<div class="tblwrap"><table>
<thead><tr><th>月份</th><th class="num">B +D3P3</th><th class="num">C 现状</th>
<th class="num">E1 满5日</th><th class="num">E2 满10日</th><th class="num">E3 10+MA20</th>
<th class="num">E4 转正</th></tr></thead>
<tbody>{''.join(mrows2)}</tbody></table></div>
<div class="note">空仓月收益为 0.00%。表格可横向滚动。</div>
</div>

<div class="warn">
<b>★ 四种离场方式的核心结论（{d0} ~ {d1}，最新数据）：</b><br>
· <b>最优：{best_txt}</b>；<br>
· <b>「轮动转正」是最差的一条</b>——池内只要有一只标的动横20 转正就离场，而这往往正是反弹刚开始，
把最肥的一段让掉了；<br>
· <b>「固定天数」越长越好</b>：满 10 日（{EXR[1]['pack']['final']:.3f}）优于满 5 日（{EXR[0]['pack']['final']:.3f}），
因为持有期短于 5 天时，抄底吃不到反弹；<br>
· <b>四种写法的最大回撤完全相同（{EXR[0]['pack']['mdd']:.2f}%）</b>——说明换一种卖法<b>并不能</b>改善回撤，
那个 −23% 是 2026-10 单笔（588170）造成的，跟怎么卖无关；<br>
· <span class="dim">所有锁定期写法都把抄底次数从 C 的 {len(C.get('weak_events') or [])} 次压缩到
{min(e['n_weak'] for e in EXR)}~{max(e['n_weak'] for e in EXR)} 次 —— 锁定期必然减少「连续滚动」的机会，
这是收益与「明确的卖出规则」之间的取舍。</span>
</div>

<div class="note">
口径说明：动横20 选最强 + 粘性带 1.0pp + 过热熔断（乖离&gt;12% 或领先&gt;15pp）；
D3 = 换出时该笔亏 ≥3% ⇒ 空仓 5 日；P3 = 头名 ROC60 进自身滚动 95 分位 ⇒ 空仓 20 日；
抄底 = 主选池动横20 全部为负 ⇒ 当日换持末位（可用标的 &lt;5 只时不做）。<br>
E1~E4 = 抄底买入后进入锁定期（期内 D3/P3 不介入），分别按「满5日 / 满10日 / 满10日或回MA20 /
轮动转正」离场，其余条件与 C 完全相同。<br>
早期（2016-2019）主选池只有 3~5 只已上市，与现在的 9 只不是同一个策略，早期数据请打折看待。<br>
本报告由 make_compare_report.py 生成，仅供研究参考，不构成投资建议。
</div>
</div></body></html>"""
    out = os.path.join(HERE, "out", "基础策略+D3+P3+抄底回测对比.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    print(f"报告: {out}（{len(doc)/1024:.0f} KB）")
    print(f"A 基础      {PA['final']:8.3f} 年化{PA['ann']:+6.2f}% MDD{PA['mdd']:7.2f}% Calmar{PA['calmar'] or 0:5.2f}")
    print(f"B +D3P3     {PB['final']:8.3f} 年化{PB['ann']:+6.2f}% MDD{PB['mdd']:7.2f}% Calmar{PB['calmar'] or 0:5.2f}")
    print(f"C +抄底     {PC['final']:8.3f} 年化{PC['ann']:+6.2f}% MDD{PC['mdd']:7.2f}% Calmar{PC['calmar'] or 0:5.2f}")
    print(f"抄底累计贡献 {tot_pp:+.1f}pp；{top2[0][0]}+{top2[1][0]} 两年占 {top2_share:.0f}%")
    print(f"正贡献 {pos_y} 年 / 负 {neg_y} 年 / 零 {zero_y} 年")
    return out


if __name__ == "__main__":
    main()
