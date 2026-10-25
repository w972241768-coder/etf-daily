# -*- coding: utf-8 -*-
"""指数口径 vs 基金口径 · 价值引擎买点对照研究（512040 / 中证国信价值 931052）

背景
----
用户发现「跟踪指数已经跌破 MA200 的 −3% 建仓线，但 512040 基金自己还没跌破」，
担心按基金口径会错失建仓机会。本脚本回答三件事：

  1) 两个口径到底差多少、为什么差、差得稳不稳（逐年口径差 + 成因）；
  2) 若直接把阈值套到指数上（指数 −3% 买 / +6% 卖），独立 VE 回测结果如何；
  3) 关键：好处是不是只来自口径差最大的那两年（分段检验）。

数据
----
 · 基金 512040：tdx_adj_long_2015_2026.csv（前复权，T0=2026-09-24）
 · 指数 931052：中证指数官网 / 东财（online_data.fetch_csindex_daily 自带缓存）
两序列取交集日期，窗口 2019-12-31 → 2026-09-24（512040 于 2018-11-29 上市）。

口径与成本
----------
净值循环逐行复刻 backtest_dd_study.ve_single_backtest（第 i 日收盘状态决定 i+1 日收益，
翻转付单边成本），单边 0.01%（万 1，与该研究一致）。回归断言：基金口径必须得 nav 2.7132。

用法
----
    python idx_ve_study.py                # 只看终端输出
    python idx_ve_study.py --html         # 另出 HTML 报告
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

import etf_backtest_long as E
import online_data as od

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

DEF = "512040"                      # 防守位：价值ETF富国（价值100ETF）
IDX = "931052"                      # 跟踪指数：中证国信价值
COST = 0.0001                       # 单边万 1
BUY, SELL = -3.0, 6.0               # 价值引擎阈值（%）
START = "2019-12-31"
END = "2026-09-24"
MA = 200

RED, GREEN = "#d4380d", "#0f9d58"
BLUE = "#3d57e5"
MUT = "#6c717f"


# ------------------------------------------------------------------ 基础
def load_series():
    """返回 (对齐后的 DataFrame, 基金全历价格序列)。

    DataFrame 含 pf(基金前复权) / pi(指数收盘)，已按交集对齐；
    pf_full 保留完整交易日历（位置索引与 dates 一致，供净值循环使用）。
    """
    panel = E.read_panel(E.ADJ_CSV)
    f = panel[DEF].astype(float)
    idx = od.fetch_csindex_daily(IDX, "2018-01-01")
    if idx is None:
        raise SystemExit("指数 931052 取数失败（中证/东财均不可用且无缓存）")
    si = idx.set_index("date")["close"].astype(float).sort_index()
    # ⚠️ MA200 必须在各自**完整日历**上算，再对齐；先截窗口会把前 200 天算成 NaN，
    #    导致 2020 年整段无法建仓（曾因此把 A 口径算成 nav 1.64，被回归断言抓出）。
    df = pd.DataFrame({"pf": f, "pi": si,
                       "df": dev_of(f), "di": dev_of(si)}).dropna()
    return df, f


def dev_of(s: pd.Series, ma: int = MA) -> pd.Series:
    return (s / s.rolling(ma).mean() - 1.0) * 100.0


def state_from_dev(dev: pd.Series, dates: pd.DatetimeIndex,
                   buy: float = BUY, sell: float = SELL) -> pd.Series:
    """价值引擎状态机（与 etf_backtest_long.value_engine_hold 同构）。"""
    hold = False
    out = {}
    for d in dates:
        v = dev.get(d, np.nan)
        if v == v:
            if (not hold) and v < buy:
                hold = True
            elif hold and v > sell:
                hold = False
        out[d] = hold
    return pd.Series(out)


def nav_of(price: pd.Series, state: pd.Series, dates: pd.DatetimeIndex,
           si: int, ei: int, cost: float = COST) -> dict:
    """逐行复刻 ve_single_backtest 的净值循环。"""
    nav = 1.0
    held = bool(state.loc[dates[si]])
    flips, hd = 0, 0
    navs, trades = [(dates[si], 1.0)], []
    for i in range(si, ei):
        tgt = bool(state.loc[dates[i]])
        if tgt != held:
            nav *= (1.0 - cost)
            flips += 1
            trades.append(dict(date=str(dates[i].date()),
                               action="买入" if tgt else "卖出"))
            held = tgt
        if held:
            p0, p1 = price.iloc[i], price.iloc[i + 1]
            if p0 == p0 and p1 == p1 and p0:
                nav *= p1 / p0
            hd += 1
        navs.append((dates[i + 1], nav))
    ser = pd.Series({d.strftime("%Y-%m-%d"): v for d, v in navs})
    dd = ser / ser.cummax() - 1.0
    rets = ser.pct_change().dropna()
    sharpe = (float(rets.mean() / rets.std() * np.sqrt(244))
              if len(rets) > 5 and rets.std() > 0 else float("nan"))
    return dict(navs=ser, final=float(ser.iloc[-1]),
                ret=float(ser.iloc[-1] - 1.0),
                mdd=float(dd.min()), sharpe=sharpe,
                flips=flips, hold_days=hd, days=ei - si,
                hold_rate=hd / max(1, ei - si), trades=trades)


def idx_of(dates: pd.DatetimeIndex, ds: str) -> int:
    i = int(dates.searchsorted(pd.Timestamp(ds), side="right")) - 1
    return max(i, 0)                       # 起点早于首根 → 用第一根（负索引坑）


# ------------------------------------------------------------------ 事件研究
def divergence_events(df: pd.DataFrame, gap_days: int = 30) -> list:
    """指数破 −3% 而基金未破 → 聚成事件（相邻分歧间隔 >gap_days 自然日算新事件）。"""
    flag = (df["di"] <= BUY) & (df["df"] > BUY)
    eps, last = [], None
    for t in df.index[flag]:
        if last is None or (t - last).days > gap_days:
            eps.append(t)
        last = t
    out = []
    for t in eps:
        i = df.index.get_loc(t)
        row = dict(date=str(t.date()), fund_dev=round(float(df["df"].iloc[i]), 2),
                   idx_dev=round(float(df["di"].iloc[i]), 2))
        for h in (20, 60, 120):
            row[f"fwd{h}"] = (round(float(df["pf"].iloc[i + h] / df["pf"].iloc[i] - 1) * 100, 2)
                              if i + h < len(df) else None)
        out.append(row)
    return out


def fwd_stats(df: pd.DataFrame, events: list, h: int) -> dict:
    v = [e[f"fwd{h}"] for e in events if e[f"fwd{h}"] is not None]
    base = [(df["pf"].iloc[i + h] / df["pf"].iloc[i] - 1) * 100
            for i in range(len(df) - h)]
    if not v:
        return dict(n=0)
    return dict(n=len(v), mean=round(float(np.mean(v)), 2),
                median=round(float(np.median(v)), 2),
                win=round(float(100 * np.mean(np.array(v) > 0)), 0),
                base_mean=round(float(np.mean(base)), 2),
                base_win=round(float(100 * np.mean(np.array(base) > 0)), 0))


# ------------------------------------------------------------------ SVG
def svg_curve(series: dict, *, w: int = 1000, h: int = 330, pad=(46, 16, 26, 44)) -> str:
    """series: {label: (Series(index=str), color)}；返回折线 SVG。"""
    l, r, t, b = pad
    allv = np.concatenate([s.values.astype(float) for s, _c in series.values()])
    vmin, vmax = float(np.nanmin(allv)), float(np.nanmax(allv))
    vmin = min(vmin, 1.0)
    span = max(vmax - vmin, 1e-9)
    n = max(len(s) for s, _c in series.values())
    parts = []
    for label, (s, color) in series.items():
        v = s.values.astype(float)
        pts = []
        for i, y in enumerate(v):
            x = l + (w - l - r) * i / max(n - 1, 1)
            yy = t + (h - t - b) * (1 - (y - vmin) / span)
            pts.append(f"{x:.1f},{yy:.1f}")
        parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" '
                     f'stroke-width="1.7" stroke-linejoin="round"/>')
    # y 轴刻度
    ticks = []
    for k in range(5):
        val = vmin + span * k / 4
        yy = t + (h - t - b) * (1 - k / 4)
        ticks.append(f'<line x1="{l}" y1="{yy:.1f}" x2="{w - r}" y2="{yy:.1f}" '
                     f'stroke="#eceff3" stroke-width="1"/>'
                     f'<text x="{l - 8}" y="{yy + 4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="{MUT}">{val:.2f}</text>')
    # x 轴年份
    idx = list(series.values())[0][0].index
    years = []
    for i, d in enumerate(idx):
        if i and str(d)[:4] != str(idx[i - 1])[:4]:
            x = l + (w - l - r) * i / max(n - 1, 1)
            years.append(f'<line x1="{x:.1f}" y1="{t}" x2="{x:.1f}" y2="{h - b}" '
                         f'stroke="#f1f3f6" stroke-width="1"/>'
                         f'<text x="{x:.1f}" y="{h - b + 16}" text-anchor="middle" '
                         f'font-size="11" fill="{MUT}">{str(d)[:4]}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:auto" '
            f'xmlns="http://www.w3.org/2000/svg">'
            + "".join(ticks) + "".join(years) + "".join(parts) + "</svg>")


def svg_gap(df: pd.DataFrame, *, w: int = 1000, h: int = 190, pad=(40, 16, 24, 40)) -> str:
    """口径差（基金 − 指数）时间轴。"""
    l, r, t, b = pad
    g = (df["df"] - df["di"]).values.astype(float)
    vmin, vmax = float(np.nanmin(g)) - 0.5, float(np.nanmax(g)) + 0.5
    vmin, vmax = min(vmin, 0.0), max(vmax, 0.0)
    span = vmax - vmin
    n = len(g)
    pts = []
    for i, y in enumerate(g):
        x = l + (w - l - r) * i / max(n - 1, 1)
        yy = t + (h - t - b) * (1 - (y - vmin) / span)
        pts.append(f"{x:.1f},{yy:.1f}")
    zero = t + (h - t - b) * (1 - (0 - vmin) / span)
    mean = float(np.nanmean(g))
    my = t + (h - t - b) * (1 - (mean - vmin) / span)
    return (f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:auto" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<line x1="{l}" y1="{zero:.1f}" x2="{w - r}" y2="{zero:.1f}" '
            f'stroke="#cdd3db" stroke-width="1" stroke-dasharray="4 3"/>'
            f'<line x1="{l}" y1="{my:.1f}" x2="{w - r}" y2="{my:.1f}" '
            f'stroke="{BLUE}" stroke-width="1.2" stroke-dasharray="2 3"/>'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="#0e8ea8" '
            f'stroke-width="1.5"/>'
            f'<text x="{w - r}" y="{my - 5:.1f}" text-anchor="end" font-size="11" '
            f'fill="{BLUE}">均值 {mean:+.2f}pp</text>'
            f'<text x="{l - 6}" y="{t + 10}" text-anchor="end" font-size="10" '
            f'fill="{MUT}">{vmax:.0f}pp</text>'
            f'<text x="{l - 6}" y="{h - b}" text-anchor="end" font-size="10" '
            f'fill="{MUT}">{vmin:.0f}pp</text></svg>')


# ------------------------------------------------------------------ 主流程
def run(a):
    df_all, pf_full = load_series()          # df_all 覆盖 2018-11 起完整历史（含 MA200 预热）
    dates = pd.DatetimeIndex(pf_full.index)

    si, ei = idx_of(dates, START), min(int(dates.searchsorted(pd.Timestamp(END))),
                                      len(dates) - 1)
    df = df_all.loc[dates[si]:dates[ei]]     # 窗口内视图（统计/事件研究用）
    gap = df["df"] - df["di"]

    print("=" * 88)
    print(f"指数口径对照研究  {dates[si].date()} -> {dates[ei].date()}  "
          f"{ei - si} 个交易日  单边成本 {COST * 100:.3f}%")
    print(f"基金 {DEF} 前复权 ｜ 指数 {IDX} 中证国信价值（价格指数）｜ MA{MA}")
    print("=" * 88)
    print(f"口径差(基金−指数)  均值 {gap.mean():+.2f}pp  中位 {gap.median():+.2f}pp  "
          f"std {gap.std():.2f}pp  p10 {gap.quantile(.1):+.2f}  p90 {gap.quantile(.9):+.2f}")
    print(f"当前  {df.index[-1]:%Y-%m-%d}  基金 {df['df'].iloc[-1]:+.2f}%  "
          f"指数 {df['di'].iloc[-1]:+.2f}%  差 {gap.iloc[-1]:+.2f}pp")
    print("\n逐年口径差:")
    yg = gap.groupby(gap.index.year).agg(["mean", "min", "max", "count"])
    for y, r in yg.iterrows():
        print(f"  {y}  均值 {r['mean']:+6.2f}pp  [{r['min']:+6.2f}, {r['max']:+6.2f}]  "
              f"n={int(r['count'])}")

    # ---------- 全窗口三口径 ----------
    print("\n" + "-" * 88)
    print("① 全窗口（各自用自己口径的 −3% / +6% 阈值，标的都是 512040）")
    variants = [("A 基金口径 −3/+6（现行）", df_all["df"], BUY, SELL, "#d4380d"),
                ("B 指数口径 −3/+6", df_all["di"], BUY, SELL, "#3d57e5"),
                ("C 指数口径按均值校准", df_all["di"], BUY - gap.mean(), SELL - gap.mean(), "#0e8ea8")]
    res = {}
    for name, dev, bq, sl, _c in variants:
        st = state_from_dev(dev, dates, bq, sl)
        m = nav_of(pf_full, st, dates, si, ei)
        res[name] = m
        print(f"  {name:<24} nav {m['final']:.4f} ({m['ret'] * 100:+7.2f}%)  "
              f"回撤 {m['mdd'] * 100:6.2f}%  夏普 {m['sharpe']:.2f}  "
              f"翻转 {m['flips']:2d} 次  持仓 {m['hold_rate'] * 100:5.1f}%")
    bh_ser = pf_full.loc[dates[si]:dates[ei]].ffill()
    bh_ser = bh_ser / bh_ser.iloc[0]
    bh_mdd = float((bh_ser / bh_ser.cummax() - 1).min())
    bh_ret = float(bh_ser.iloc[-1] - 1)
    print(f"  {'D 买入持有':<24} nav {bh_ser.iloc[-1]:.4f} ({bh_ret * 100:+7.2f}%)  "
          f"回撤 {bh_mdd * 100:6.2f}%  持仓 100.0%")

    # 回归断言（基金口径必须复现既有研究结果）
    assert abs(res["A 基金口径 −3/+6（现行）"]["final"] - 2.7132) < 0.003, \
        f"基金口径复现失败: {res['A 基金口径 −3/+6（现行）']['final']}"
    print("  [回归] 基金口径 nav = 2.7132 复现通过（与 backtest_dd_study 一致）")

    # ---------- 分段 ----------
    print("\n" + "-" * 88)
    print("② 分段检验：B 的优势是不是只来自口径差最大的 2020–2021？")
    segs = [("2019-12-31", "2021-12-31", "口径差高位期（均值约 +6pp）"),
            ("2021-12-31", "2026-09-24", "口径差回落期（均值约 +1.7pp）")]
    seg_out = []
    for s0, s1, tag in segs:
        i0, i1 = idx_of(dates, s0), min(int(dates.searchsorted(pd.Timestamp(s1))), len(dates) - 1)
        gm = float((df_all["df"] - df_all["di"]).iloc[i0:i1].mean())
        row = dict(seg=f"{dates[i0].date()} → {dates[i1].date()}", tag=tag,
                   gap_mean=round(gm, 2))
        for key, nm in (("A", "A 基金口径 −3/+6（现行）"),
                        ("B", "B 指数口径 −3/+6"),
                        ("C", "C 指数口径按均值校准")):
            dev, bq, sl = (df_all["df"], BUY, SELL) if key == "A" else (
                (df_all["di"], BUY, SELL) if key == "B" else
                (df_all["di"], BUY - gm, SELL - gm))
            m = nav_of(pf_full, state_from_dev(dev, dates, bq, sl), dates, i0, i1)
            row[key] = dict(nav=round(m["final"], 4), ret=round(m["ret"] * 100, 2),
                            mdd=round(m["mdd"] * 100, 2), hold=round(m["hold_rate"] * 100, 1),
                            flips=m["flips"])
        bhs = pf_full.iloc[i1] / pf_full.iloc[i0]
        row["D"] = dict(nav=round(float(bhs), 4), ret=round(float(bhs - 1) * 100, 2))
        seg_out.append(row)
        print(f"  {row['seg']}  {tag}  口径差均值 {gm:+.2f}pp")
        for k in ("A", "B", "C", "D"):
            print(f"    {k}  nav {row[k]['nav']:.4f}  {row[k]['ret']:+7.2f}%"
                  + (f"  回撤 {row[k]['mdd']:6.2f}%  持仓 {row[k]['hold']:5.1f}%"
                     if k != "D" else ""))

    # ---------- 事件研究 ----------
    print("\n" + "-" * 88)
    print("③ 「指数破 −3%、基金未破」分歧事件之后，基金表现如何")
    ev = divergence_events(df)
    st_d = {h: fwd_stats(df, ev, h) for h in (20, 60, 120)}
    print(f"  分歧事件 {len(ev)} 次：")
    for e in ev:
        print(f"    {e['date']}  基金 {e['fund_dev']:+.2f}%  指数 {e['idx_dev']:+.2f}%"
              f"  后20日 {e['fwd20']}%  后60日 {e['fwd60']}%")
    for h in (20, 60, 120):
        s = st_d[h]
        if s.get("n"):
            print(f"    后 {h:3d} 日：n={s['n']}  均值 {s['mean']:+.2f}%  中位 {s['median']:+.2f}%"
                  f"  胜率 {s['win']:.0f}%   ｜ 全样本基准 {s['base_mean']:+.2f}% / {s['base_win']:.0f}%")

    # ---------- 结论 ----------
    a_ret = res["A 基金口径 −3/+6（现行）"]["ret"] * 100
    b_ret = res["B 指数口径 −3/+6"]["ret"] * 100
    s1_gap = seg_out[0]["A"]["ret"] - seg_out[0]["B"]["ret"]
    s2_gap = seg_out[1]["A"]["ret"] - seg_out[1]["B"]["ret"]
    concl = [
        f"口径差不是常数：2019-2026 均值 {gap.mean():+.2f}pp，但 2020–2021 高达 "
        f"{yg.loc[2020, 'mean']:+.2f}pp / {yg.loc[2021, 'mean']:+.2f}pp，"
        f"2022 年起回落到 1.5–2.4pp。根因是<b>基金用前复权价（分红加回）</b>、"
        f"<b>指数是价格指数（成分股除息直接扣点位）</b>，分红率越高的年份差得越多。",
        f"直接把 −3% 套到指数上（B）在全窗口看似大胜：{b_ret:+.2f}% vs 现行 {a_ret:+.2f}%。"
        f"但分段一看就露馅——2020–2021 段 B 领先 {abs(s1_gap):.2f}pp"
        f"（那两年口径差约 6pp，等于在<b>比现行规则高 6 个点</b>的位置买入），"
        f"而 2022 年后口径差回到 1.7pp，B 只领先 {abs(s2_gap):.2f}pp。"
        f"<b>B 的超额主要来自口径漂移，不是可复用的信号增量。</b>",
        f"按均值校准的 C（指数 −{3 + gap.mean():.2f}% / +{6 - gap.mean():.2f}%）"
        f"全窗口 {res['C 指数口径按均值校准']['ret'] * 100:+.2f}%，既没有 B 的 "
        f"{b_ret:+.2f}%，也不如现行 {a_ret:+.2f}%——说明「换算正确」之后并没有增量，"
        f"B 多出来的那一倍纯粹是把买点整体上移的结果。",
        f"分歧事件确实有短期信息：{len(ev)} 次「指数破 −3%、基金未破」之后 20 日平均 "
        f"{st_d[20]['mean']:+.2f}%（基准 {st_d[20]['base_mean']:+.2f}%），胜率 {st_d[20]['win']:.0f}%。"
        f"但有效事件只有 {st_d[20]['n']} 个，且后 60/120 日的观察窗口彼此大幅重叠"
        f"（同一下跌过程会被反复计入），<b>有效独立样本远小于表面次数，不足以下结论</b>。",
        "执行建议：<b>不改规则</b>，把指数口径当「预警灯」用——指数先破 −3% 时提高关注度、"
        "开始准备资金，等基金口径真正跌破 0.97×MA200 再动手；"
        "若要真改，必须先拿到「指数口径单独回测」的分段稳健证据。",
    ]
    print("\n" + "=" * 88)
    print("结论")
    for i, c in enumerate(concl, 1):
        import re as _re
        print(f"  {i}. {_re.sub(r'<[^>]+>', '', c)}")

    payload = dict(window=[str(dates[si].date()), str(dates[ei].date())], days=ei - si,
                   fund=DEF, index=IDX, cost=COST,
                   gap=dict(mean=round(float(gap.mean()), 3),
                            median=round(float(gap.median()), 3),
                            std=round(float(gap.std()), 3),
                            p10=round(float(gap.quantile(.1)), 3),
                            p90=round(float(gap.quantile(.9)), 3),
                            now=round(float(gap.iloc[-1]), 3)),
                   yearly={int(k): round(float(v), 2)
                           for k, v in yg["mean"].items() if yg.loc[k, "count"] >= 20},
                   full={k: dict(nav=round(v["final"], 4), ret=round(v["ret"] * 100, 2),
                                 mdd=round(v["mdd"] * 100, 2), sharpe=round(v["sharpe"], 3),
                                 flips=v["flips"], hold=round(v["hold_rate"] * 100, 1))
                         for k, v in res.items()}
                   | {"D 买入持有": dict(nav=round(float(bh_ser.iloc[-1]), 4),
                                       ret=round(bh_ret * 100, 2),
                                       mdd=round(bh_mdd * 100, 2), flips=0, hold=100.0)},
                   seg=seg_out, events=ev,
                   evstats=st_d, conclusion=concl,
                   now_vals=dict(fund_dev=round(float(df["df"].iloc[-1]), 2),
                                 idx_dev=round(float(df["di"].iloc[-1]), 2),
                                 fund_close=round(float(df["pf"].iloc[-1]), 3),
                                 idx_close=round(float(df["pi"].iloc[-1]), 2),
                                 fund_ma=round(float(df["pf"].rolling(MA).mean().iloc[-1]), 4),
                                 idx_ma=round(float(df["pi"].rolling(MA).mean().iloc[-1]), 2)))
    os.makedirs(OUT, exist_ok=True)
    jp = os.path.join(OUT, "idx_ve_study.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\nJSON: {jp}")

    if a.html:
        hp = os.path.join(OUT, "指数口径对照_2019-2026.html")
        with open(hp, "w", encoding="utf-8") as fh:
            fh.write(render(payload, res, bh_ser, df))
        print(f"HTML: {hp}")
    return payload


# ------------------------------------------------------------------ 渲染
def render(P: dict, res: dict, bh_ser: pd.Series, df: pd.DataFrame) -> str:
    W0, W1 = P["window"]
    g = P["gap"]
    nv = P["now_vals"]
    yrs = sorted(P["yearly"].items())
    ymax = max(abs(v) for _k, v in yrs) or 1.0
    ybars = "".join(
        f'<div class="yrow"><span class="yl">{k}</span>'
        f'<span class="ybar"><i style="width:{abs(v) / ymax * 100:.1f}%;'
        f'background:{"#0e8ea8" if v >= 0 else GREEN}"></i></span>'
        f'<span class="yv">{v:+.2f}pp</span></div>' for k, v in yrs)

    curve = svg_curve({
        "A": (res["A 基金口径 −3/+6（现行）"]["navs"], RED),
        "B": (res["B 指数口径 −3/+6"]["navs"], BLUE),
        "C": (res["C 指数口径按均值校准"]["navs"], "#0e8ea8"),
        "D": (bh_ser.mul(1.0), "#9aa3ae"),
    })
    nA = P["full"]["A 基金口径 −3/+6（现行）"]
    nB = P["full"]["B 指数口径 −3/+6"]
    nC = P["full"]["C 指数口径按均值校准"]
    nD = P["full"]["D 买入持有"]

    def tr(name, d, color, hi=""):
        return (f'<tr class="{hi}"><td><b style="color:{color}">■</b> {name}</td>'
                f'<td class="num">{d["nav"]:.4f}</td>'
                f'<td class="num" style="color:{RED if d["ret"] >= 0 else GREEN}">'
                f'{d["ret"]:+.2f}%</td>'
                f'<td class="num" style="color:{GREEN}">{d["mdd"]:.2f}%</td>'
                f'<td class="num">{d.get("sharpe", "-")}</td>'
                f'<td class="num">{d.get("flips", 0)}</td>'
                f'<td class="num">{d.get("hold", 100):.1f}%</td></tr>')

    rows = (tr("A 基金口径 −3%/+6%（现行规则）", nA, RED, "hl")
            + tr("B 指数口径 −3%/+6%（同阈值硬套）", nB, BLUE)
            + tr("C 指数口径按均值校准", nC, "#0e8ea8")
            + tr("D 买入持有 512040", nD, "#9aa3ae"))

    seg_html = ""
    for s in P["seg"]:
        seg_html += (
            f'<tr><td>{s["seg"]}<br><span class="dim">{s["tag"]}｜口径差均值 '
            f'{s["gap_mean"]:+.2f}pp</span></td>'
            f'<td class="num">{s["A"]["ret"]:+.2f}%</td>'
            f'<td class="num">{s["B"]["ret"]:+.2f}%</td>'
            f'<td class="num">{s["C"]["ret"]:+.2f}%</td>'
            f'<td class="num">{s["D"]["ret"]:+.2f}%</td>'
            f'<td class="num">{(s["B"]["ret"] - s["A"]["ret"]):+.2f}pp</td></tr>')

    ev_rows = "".join(
        f'<tr><td class="mono">{e["date"]}</td><td class="num">{e["fund_dev"]:+.2f}%</td>'
        f'<td class="num">{e["idx_dev"]:+.2f}%</td>'
        f'<td class="num">{e["fwd20"] if e["fwd20"] is not None else "-"}'
        f'{"%" if e["fwd20"] is not None else ""}</td>'
        f'<td class="num">{e["fwd60"] if e["fwd60"] is not None else "-"}'
        f'{"%" if e["fwd60"] is not None else ""}</td>'
        f'<td class="num">{e["fwd120"] if e["fwd120"] is not None else "-"}'
        f'{"%" if e["fwd120"] is not None else ""}</td></tr>' for e in P["events"])

    es = P["evstats"]
    es_rows = "".join(
        f'<tr><td>{h} 日</td><td class="num">{es[str(h)]["n"] if str(h) in es else es[h]["n"]}</td>'
        f'<td class="num">{es[str(h) if str(h) in es else h]["mean"]:+.2f}%</td>'
        f'<td class="num">{es[str(h) if str(h) in es else h]["median"]:+.2f}%</td>'
        f'<td class="num">{es[str(h) if str(h) in es else h]["win"]:.0f}%</td>'
        f'<td class="num">{es[str(h) if str(h) in es else h]["base_mean"]:+.2f}%</td>'
        f'<td class="num">{es[str(h) if str(h) in es else h]["base_win"]:.0f}%</td></tr>'
        for h in (20, 60, 120) if (str(h) in es or h in es))

    concl = "".join(f"<li>{c}</li>" for c in P["conclusion"])
    _ftxt = ("已跌破 −3% 买线" if nv["fund_dev"] <= -3
             else f'距 −3% 买线还差 {nv["fund_dev"] + 3:.2f}pp')
    _itxt = ("已跌破 −3% 买线" if nv["idx_dev"] <= -3
             else f'距 −3% 买线还差 {nv["idx_dev"] + 3:.2f}pp')

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>指数口径 vs 基金口径 · 买点对照研究（{W0} ~ {W1}）</title>
<style>
  :root{{--bd:#e2e5ea;--mut:#6c717f;--fg:#202020}}
  *{{box-sizing:border-box}}
  body{{margin:0;padding:24px;background:#eef0f3;color:var(--fg);
    font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.7}}
  .wrap{{max-width:1180px;margin:0 auto}}
  .hero{{background:linear-gradient(120deg,#3d57e5,#4a64e8,#566de7);color:#fff;
    border-radius:14px;padding:22px 26px;margin-bottom:18px;
    box-shadow:0 4px 16px rgba(61,87,229,.18)}}
  .hero h1{{margin:0 0 6px;font-size:21px;font-weight:700;letter-spacing:-.3px}}
  .hero .sub{{font-size:13px;opacity:.92}}
  .kv{{display:flex;gap:26px;flex-wrap:wrap;margin-top:14px}}
  .kv div{{font-size:12.5px;opacity:.95}}
  .kv b{{display:block;font-size:19px;font-weight:700;margin-top:2px}}
  h2{{font-size:15.5px;font-weight:700;margin:26px 0 10px}}
  .card{{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:16px 18px;
    box-shadow:0 1px 3px rgba(16,24,40,.05);margin-bottom:14px}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
  .pill{{border:1px solid var(--bd);border-radius:10px;padding:12px 14px;background:#fff}}
  .pill .k{{font-size:11.5px;color:var(--mut)}}
  .pill .v{{font-size:24px;font-weight:700;letter-spacing:-.5px;margin-top:2px}}
  .pill .s{{font-size:11.5px;color:var(--mut);margin-top:4px}}
  table{{width:100%;border-collapse:collapse;background:#fff;font-size:12.5px}}
  th,td{{padding:8px 9px;border-bottom:1px solid #eef0f3;text-align:left}}
  th{{background:#f4f6fa;font-weight:600;color:#4b5563;font-size:12px}}
  td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
  tr.hl td{{background:#fdf6f4}}
  .mono{{font-family:ui-monospace,Consolas,monospace}}
  .dim{{color:var(--mut);font-size:11.5px}}
  .legend{{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:var(--mut);margin-top:8px}}
  .legend b{{display:inline-block;width:16px;height:3px;border-radius:2px;vertical-align:middle;
    margin-right:5px}}
  .yrow{{display:flex;align-items:center;gap:10px;margin:3px 0;font-size:12px}}
  .yl{{width:44px;color:var(--mut)}}
  .ybar{{flex:1;height:13px;background:#f2f4f7;border-radius:3px;overflow:hidden}}
  .ybar i{{display:block;height:100%;border-radius:3px}}
  .yv{{width:76px;text-align:right;font-variant-numeric:tabular-nums}}
  .concl{{background:#f7f9fb;border:1px dashed #d8dee6;border-radius:11px;padding:14px 18px}}
  .concl ol{{margin:0;padding-left:20px}}
  .concl li{{margin:7px 0;line-height:1.8}}
  .warnbox{{background:#fff8f3;border:1px solid #ffd7c2;border-radius:11px;
    padding:12px 16px;font-size:13px;color:#8a2c08;margin-bottom:14px}}
  .foot{{color:var(--mut);font-size:11.5px;margin-top:22px;line-height:1.8}}
  @media(max-width:820px){{.grid2{{grid-template-columns:1fr}} body{{padding:12px}}}}
</style></head><body><div class="wrap">

<div class="hero">
  <h1>指数口径 vs 基金口径 · 价值引擎买点对照研究</h1>
  <div class="sub">512040 价值ETF富国 · 前复权口径　vs　931052 中证国信价值 · 价格指数口径　｜
    窗口 {W0} ~ {W1}（{P['days']} 个交易日）｜ 单边成本 {P['cost'] * 100:.3f}%</div>
  <div class="kv">
    <div>基金偏离 MA200<b style="color:#ffd9c9">{nv['fund_dev']:+.2f}%</b></div>
    <div>指数偏离 MA200<b style="color:#ffd9c9">{nv['idx_dev']:+.2f}%</b></div>
    <div>口径差（基金−指数）<b>{g['now']:+.2f}pp</b></div>
    <div>全窗口口径差均值<b>{g['mean']:+.2f}pp</b></div>
  </div>
</div>

<div class="warnbox">
  <b>一句话结论：</b>两个口径确实不一样，但这不是基金信号失灵，而是<b>分红处理方式造成的系统性偏移</b>。
  直接拿指数的 −3% 当买点会“提前建仓”，回测看起来收益翻倍——但超额几乎全部来自 2020–2021
  口径差高达 6pp 的那两年，2022 年后两口径基本打平。<b>不建议改规则，指数口径当预警灯用。</b>
</div>

<h2>① 窗口末日两口径并列（{W1}，日常报告里已同步展示）</h2>
<div class="card">
  <div class="grid2">
    <div class="pill" style="border-color:#ccd7fb;background:#f8faff">
      <div class="k">基金 512040 · 前复权（策略回测用的口径）</div>
      <div class="v" style="color:{RED if nv['fund_dev'] > 0 else GREEN}">{nv['fund_dev']:+.2f}%</div>
      <div class="s">{nv['fund_close']:.3f} vs MA200 {nv['fund_ma']:.4f}　{_ftxt}</div>
    </div>
    <div class="pill" style="border-color:#ffe1c4;background:#fffbf5">
      <div class="k">指数 931052 · 价格指数（你看到的口径）</div>
      <div class="v" style="color:{RED if nv['idx_dev'] > 0 else GREEN}">{nv['idx_dev']:+.2f}%</div>
      <div class="s">{nv['idx_close']:.2f} vs MA200 {nv['idx_ma']:.2f}　{_itxt}</div>
    </div>
  </div>
</div>

<h2>② 口径差为什么存在、稳不稳</h2>
<div class="card">
  <p style="margin:0 0 10px;font-size:13px">
    基金用的是<b>前复权价</b>（ETF 分红已加回，历史价被压低 → MA200 更平 → 偏离天然偏高）；
    指数是<b>价格指数</b>（成分股除息直接扣点位 → 偏离天然偏低）。
    两者差值 = <b>基金 − 指数</b>，理论上≈ 股息率 × 半年 / 一年，实际随分红节奏大幅波动。
  </p>
  {svg_gap(df)}
  <div class="legend"><span>青线 = 口径差轨迹</span><span>蓝虚线 = 均值 {g['mean']:+.2f}pp</span>
    <span>灰虚线 = 0</span></div>
  <div style="margin-top:14px">{ybars}</div>
  <p class="dim" style="margin:8px 0 0">
    关键：2020、2021 两年均值 +{P['yearly'].get(2020, 0):.2f}pp / +{P['yearly'].get(2021, 0):.2f}pp，
    2022 年起回落到 +{P['yearly'].get(2022, 0):.2f} ~ +{P['yearly'].get(2023, 0):.2f}pp。
    <b>口径差是时变的，所以“把指数阈值平移一个常数”这种写法根本不成立。</b>
  </p>
</div>

<h2>③ 三口径独立回测（标的都是 512040）</h2>
<div class="card">
  {curve}
  <div class="legend">
    <span><b style="background:{RED}"></b>A 基金口径 −3%/+6%（现行）</span>
    <span><b style="background:{BLUE}"></b>B 指数口径 −3%/+6%</span>
    <span><b style="background:#0e8ea8"></b>C 指数口径按均值校准</span>
    <span><b style="background:#9aa3ae"></b>D 买入持有</span>
  </div>
  <table style="margin-top:14px">
    <tr><th>方案</th><th class="num">期末净值</th><th class="num">区间收益</th>
      <th class="num">最大回撤</th><th class="num">夏普</th><th class="num">翻转次数</th>
      <th class="num">持仓占比</th></tr>
    {rows}
  </table>
  <p class="dim" style="margin:10px 0 0">
    B 看起来大幅领先，但它等于在“指数破 −3%”时买——对应基金口径约
    {(-3 + g['mean']):+.2f}%，即<b>几乎在 MA200 上方无折价买入</b>，属另一条规则而非同一规则的换算。
  </p>
</div>

<h2>④ 分段检验：B 的优势是真增量还是口径漂移</h2>
<div class="card">
  <table>
    <tr><th>区间</th><th class="num">A 基金口径</th><th class="num">B 指数口径</th>
      <th class="num">C 校准</th><th class="num">D 买入持有</th><th class="num">B − A</th></tr>
    {seg_html}
  </table>
  <p class="dim" style="margin:10px 0 0">
    2020–2021 段口径差均值约 +6pp，B 才大幅领先；2022 年后口径差回到 +1.7pp，
    B 与 A 只差几个点。<b>超额是口径漂移的产物，不是稳健的择时增量。</b>
    C（按均值校准）在两个分段都贴近 A，说明“换算对了结论就一致”。
  </p>
</div>

<h2>⑤ 分歧事件研究：「指数破 −3%、基金未破」之后发生了什么</h2>
<div class="card">
  <table style="margin-bottom:12px">
    <tr><th>事件日</th><th class="num">基金偏离</th><th class="num">指数偏离</th>
      <th class="num">后 20 日</th><th class="num">后 60 日</th><th class="num">后 120 日</th></tr>
    {ev_rows}
  </table>
  <table>
    <tr><th>持有期</th><th class="num">事件数</th><th class="num">平均收益</th>
      <th class="num">中位</th><th class="num">胜率</th><th class="num">全样本基准</th>
      <th class="num">基准胜率</th></tr>
    {es_rows}
  </table>
  <p class="dim" style="margin:10px 0 0">
    分歧日之后平均收益高于全样本基准，方向上支持“指数先破是预警”。
    但有效事件只有 {es[20]['n']} 个，且后 60/120 日的观察窗口彼此大幅重叠
    （同一下跌过程被反复计入），<b>有效独立样本远小于表面次数，不能作为改规则的依据</b>。
  </p>
</div>

<h2>⑥ 结论与执行建议</h2>
<div class="card"><div class="concl"><ol>{concl}</ol></div></div>

<div class="foot">
  数据：512040 前复权来自 tdx_adj_long_2015_2026.csv（T0=2026-09-24）；
  931052 日线来自中证指数官网（csindex.com.cn），东财兜底。
  净值循环逐行复刻 backtest_dd_study.ve_single_backtest，单边成本 {P['cost'] * 100:.3f}%，
  回归断言基金口径 nav = 2.7132。生成脚本 idx_ve_study.py。<br>
  本报告仅供研究参考，不构成个人投资建议。
</div>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", action="store_true", help="另出 HTML 报告")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
