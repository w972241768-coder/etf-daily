#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卖出侧改用「MACD 柱由正转负（红柱翻绿）」的对比研究（512040 / 512890，近 5 年）

用户 2026-10-24 需求：
  买入策略**保持原样**（进买入区间即买，不做任何指标门槛）；
  卖出条件从「涨到 +6%」改成「**柱由正转负（红柱翻绿）才卖**」，
  理由是它和 6% 止盈不重叠 —— 上一轮 J>80 之所以无效，正是因为「涨了 6% 自然超买」而与之高度重叠。

为什么值得单独测这一条
--------------
前两轮已经证伪（见 ve_j_entry_study.py / ve_macd_study.py 顶部）：
  · J<20 买入门槛：慢指标配快事件，漏掉「跌得快、来不及超卖」的买点。
  · MACD 柱转强买入：砸出买点的正是让柱「绿柱加长」的下跌，6 个买点无一满足。
  · J>80 卖出：涨 6% 时 J 天然 >80，条件空转。
共性：**买入侧加条件 = 漏收益；卖出侧加「与超买重叠」的条件 = 不生效。**
而「柱翻绿」是**趋势动量转向**，与「价格涨了 6%」确实不同维度 ⇒ 这是唯一还没被否掉的组合。

「柱由正转负」的精确定义
--------------
  turn_neg[t] = (hist[t−1] > 0) and (hist[t] <= 0)
即**当日**从红柱翻成绿柱（含恰好等于 0 的临界）。它是一个**事件**，不是状态 ——
若写成「当前是绿柱就卖」则等于买入次日立刻卖，无意义。

四种卖出模式（买入全部相同 = 进区间即买）
--------------
  A  原版       ：px > MA200×sell                  → 卖        （基准，仅 6% 止盈）
  B  只柱翻绿   ：turn_neg                          → 卖        （用户本轮要求：完全替代 6%）
  C  先到先卖   ：px > MA200×sell 或 turn_neg       → 卖        （保留止盈 + 加趋势确认）
  D  两者同时   ：px > MA200×sell 且 turn_neg       → 卖        （先到 6% 不算，还得等翻绿）

口径：前复权 / 零手续费 / 初始净值 1 / 近 5 年；
    收益按「k−1 日收盘后的状态」决定 k−1→k 涨跌（信号当日成交、次日吃涨跌，无未来函数）。
    A 组净值必须与日报 `build_ve_curve()` 逐点一致（脚本内有断言）。

用法：
  python ve_macd_sell_study.py
  python ve_macd_sell_study.py --json out/ve_macd_sell.json
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = "D:/娱乐（音乐、视频）Q-骑牛/ETF轮动战法"
for _p in (_HERE, PROJECT_DIR):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

import ve_j_entry_study as st          # 数据加载 / 尾部拼接
import ve_macd_study as mc             # macd_hist()

YEARS = 5.0
MODES = [
    ("A", "原版（+6% 止盈）", "pct"),
    ("B", "只按柱翻绿卖", "hist"),
    ("C", "先到先卖（6% 或 翻绿）", "or"),
    ("D", "两者同时（6% 且 翻绿）", "and"),
]

TARGETS = [
    dict(code="512040", name="价值ETF富国", buy=0.97, sell=1.06, ma=200),
    dict(code="512890", name="红利低波ETF华泰柏瑞", buy=1.00, sell=1.06, ma=200),
]


def turn_negative(hist: pd.Series) -> pd.Series:
    """柱由正转负（红柱翻绿）：昨日 >0 且今日 <=0。首日 NaN 不算事件。"""
    prev = hist.shift(1)
    return ((prev > 0) & (hist <= 0)).fillna(False).rename("turn_neg")


# --------------------------------------------------------------------------- 回测
def run(df, *, ma=200, buy=0.97, sell=1.06, hist=None, mode="pct") -> dict:
    """买入固定 = 进区间即买；卖出按 mode 变化。"""
    close = df["close"]
    mav = close.rolling(ma).mean()
    tn = turn_negative(hist) if hist is not None else None

    state, hold = [], False
    for t in close.index:
        px = float(close.loc[t])
        mv = float(mav.loc[t]) if mav.loc[t] == mav.loc[t] else None
        if mv:
            if hold:
                hit_pct = px > mv * sell
                hit_tn = bool(tn.loc[t]) if tn is not None else False
                if mode == "pct":
                    do = hit_pct
                elif mode == "hist":
                    do = hit_tn
                elif mode == "or":
                    do = hit_pct or hit_tn
                elif mode == "and":
                    do = hit_pct and hit_tn
                else:
                    raise ValueError(mode)
                if do:
                    hold = False
            elif px < mv * buy:
                hold = True
        state.append(hold)
    stt = pd.Series(state, index=close.index, dtype=bool)

    cut = close.index[-1] - pd.DateOffset(years=int(YEARS))
    w, sw = close[close.index >= cut], stt[stt.index >= cut]
    if len(w) < 60:
        raise SystemExit("窗口内数据不足")

    # 逐笔 + 退出原因
    nav, navs, events, trades = 1.0, [1.0], [], []
    entry_i = 0 if bool(sw.iloc[0]) else None
    if bool(sw.iloc[0]):
        events.append(dict(date=w.index[0], act="HOLD"))
    for i in range(1, len(w)):
        if bool(sw.iloc[i - 1]):
            nav *= float(w.iloc[i]) / float(w.iloc[i - 1])
        navs.append(nav)
        if bool(sw.iloc[i]) != bool(sw.iloc[i - 1]):
            if bool(sw.iloc[i]):
                events.append(dict(date=w.index[i], act="BUY"))
                entry_i = i
            else:
                events.append(dict(date=w.index[i], act="SELL"))
                if entry_i is not None:
                    seg = w.iloc[entry_i:i + 1]
                    _d = w.index[i]
                    _mv = float(mav.loc[_d])
                    _hit_pct = float(w.loc[_d]) > _mv * sell
                    _hit_tn = bool(tn.loc[_d]) if tn is not None else False
                    trades.append(dict(
                        start=str(w.index[entry_i].date()), end=str(_d.date()),
                        days=i - entry_i,
                        ret=float(seg.iloc[-1] / seg.iloc[0] - 1.0) * 100.0,
                        over=(float(w.loc[_d]) / (_mv * sell) - 1.0) * 100.0,
                        by_pct=bool(_hit_pct), by_tn=bool(_hit_tn),
                        hist_at_buy=(None if hist is None else
                                     round(float(hist.loc[w.index[entry_i]]), 4)),
                        hist_at_sell=(None if hist is None else
                                      round(float(hist.loc[_d]), 4))))
                entry_i = None
    if entry_i is not None:
        seg = w.iloc[entry_i:]
        trades.append(dict(
            start=str(w.index[entry_i].date()), end=str(w.index[-1].date()),
            days=len(seg) - 1,
            ret=float(seg.iloc[-1] / seg.iloc[0] - 1.0) * 100.0,
            over=None, by_pct=False, by_tn=False,
            hist_at_buy=(None if hist is None else
                         round(float(hist.loc[w.index[entry_i]]), 4)),
            hist_at_sell=None, open=True))

    navs = pd.Series(navs, index=w.index)
    bh = w / float(w.iloc[0])
    yrs = (w.index[-1] - w.index[0]).days / 365.25
    daily = navs.pct_change().dropna()
    sd = float(daily.std())
    win = [t["ret"] for t in trades]
    mdd = float((navs / navs.cummax() - 1).min() * 100)
    cagr = (float(navs.iloc[-1]) ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0
    return dict(
        dates=[d.strftime("%Y-%m-%d") for d in w.index],
        nav=[float(v) for v in navs.values],
        bh=[float(v) for v in bh.values],
        hold=[bool(v) for v in sw.values],
        events=events, trades=trades,
        d0=w.index[0], d1=w.index[-1], n=len(w), yrs=yrs,
        final=float(navs.iloc[-1]), ret=(float(navs.iloc[-1]) - 1) * 100,
        cagr=cagr, mdd=mdd,
        sharpe=float(daily.mean() / sd * np.sqrt(252)) if sd > 0 else None,
        calmar=(cagr / abs(mdd)) if mdd else None,
        vol=float(sd * np.sqrt(252) * 100),
        hold_rate=float(sw.mean() * 100),
        n_buy=sum(1 for e in events if e["act"] == "BUY"),
        n_sell=sum(1 for e in events if e["act"] == "SELL"),
        avg_ret=(float(np.mean(win)) if win else None),
        avg_days=(float(np.mean([t["days"] for t in trades])) if trades else None),
        win_rate=(float(np.mean([1.0 if r > 0 else 0.0 for r in win]) * 100) if win else None),
        max_days=(max((t["days"] for t in trades), default=None)),
        bh_final=float(bh.iloc[-1]), bh_ret=(float(bh.iloc[-1]) - 1) * 100,
        bh_mdd=float((bh / bh.cummax() - 1).min() * 100),
        holding_now=bool(sw.iloc[-1]),
    )


def event_gap(df, hist, res_a, *, ma=200, sell=1.06, nmax=250):
    """诊断：同一段持仓里，「6% 止盈信号」与「柱翻绿」谁先到？相差几个交易日？

    这是本轮的核心 —— 若柱翻绿普遍晚于 6%（甚至晚很多），就是「让利润多跑」；
    若普遍早于 6%，就是「提前下车」。二者决定了 B 模式的好坏。
    """
    close = df["close"]
    mav = close.rolling(ma).mean()
    tn = turn_negative(hist)
    out = []
    for t in res_a["trades"]:
        d0 = pd.Timestamp(t["start"])
        if d0 not in close.index:
            continue
        i0 = int(df.index.get_loc(d0))
        first_pct = first_tn = None
        for k in range(1, nmax + 1):
            if i0 + k >= len(df.index):
                break
            d = df.index[i0 + k]
            if first_pct is None and float(close.loc[d]) > float(mav.loc[d]) * sell:
                first_pct = k
            if first_tn is None and bool(tn.loc[d]):
                first_tn = k
            if first_pct is not None and first_tn is not None:
                break
        gap = (None if (first_pct is None or first_tn is None) else first_tn - first_pct)
        out.append(dict(start=t["start"], end=t["end"], days=t["days"],
                        ret=round(t["ret"], 2),
                        pct_after=first_pct, tn_after=first_tn, gap=gap))
    return out


def fmt(v, nd=2, pct=True, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s + ("%" if pct else "")


# --------------------------------------------------------------------------- 证伪三件套
def run_cost(df, *, ma=200, buy=0.97, sell=1.06, hist=None, mode="pct", cost=0.0):
    """同 run()，但每次换仓扣 cost（双边）。

    为什么必须看：C/B 组把换手从 11 次抬到 31 次，零成本口径会系统性高估它们。
    实盘双边 0.2%（佣金+冲击），成本是这类「更频繁进出」方案的生死线。
    """
    close = df["close"]
    mav = close.rolling(ma).mean()
    tn = turn_negative(hist) if hist is not None else None
    state, hold = [], False
    for t in close.index:
        px = float(close.loc[t])
        mv = float(mav.loc[t]) if mav.loc[t] == mav.loc[t] else None
        if mv:
            if hold:
                hp = px > mv * sell
                ht = bool(tn.loc[t]) if tn is not None else False
                do = {"pct": hp, "hist": ht, "or": hp or ht, "and": hp and ht}[mode]
                if do:
                    hold = False
            elif px < mv * buy:
                hold = True
        state.append(hold)
    stt = pd.Series(state, index=close.index, dtype=bool)
    cut = close.index[-1] - pd.DateOffset(years=int(YEARS))
    w, sw = close[close.index >= cut], stt[stt.index >= cut]
    nav, navs = 1.0, [1.0]
    for i in range(1, len(w)):
        if bool(sw.iloc[i - 1]):
            nav *= float(w.iloc[i]) / float(w.iloc[i - 1])
        if bool(sw.iloc[i]) != bool(sw.iloc[i - 1]) and cost:
            nav *= (1.0 - cost)
        navs.append(nav)
    ns = pd.Series(navs, index=w.index)
    dl = ns.pct_change().dropna()
    sd = float(dl.std())
    return dict(final=float(ns.iloc[-1]), ret=(float(ns.iloc[-1]) - 1) * 100,
                mdd=float((ns / ns.cummax() - 1).min() * 100),
                sharpe=float(dl.mean() / sd * np.sqrt(252)) if sd > 0 else None,
                hold_rate=float(sw.mean() * 100),
                n_trade=int((sw.astype(int).diff().fillna(0) != 0).sum()))


def run_fixed_hold(df, *, ma=200, buy=0.97, nmax=20):
    """进区间买入 → 无条件持有 N 个交易日后卖出（完全不看价格与 MACD）。

    用途：若「固定持有 N 日」能达到 C 组水平，说明 C 的改善来自「缩短持仓」而非选时能力。
    """
    close = df["close"]
    mav = close.rolling(ma).mean()
    state, hold, cnt = [], False, 0
    for t in close.index:
        px = float(close.loc[t])
        mv = float(mav.loc[t]) if mav.loc[t] == mav.loc[t] else None
        if mv:
            if hold:
                cnt += 1
                if cnt >= nmax:
                    hold, cnt = False, 0
            elif px < mv * buy:
                hold, cnt = True, 0
        state.append(hold)
    stt = pd.Series(state, index=close.index, dtype=bool)
    cut = close.index[-1] - pd.DateOffset(years=int(YEARS))
    w, sw = close[close.index >= cut], stt[stt.index >= cut]
    nav, navs = 1.0, [1.0]
    for i in range(1, len(w)):
        if bool(sw.iloc[i - 1]):
            nav *= float(w.iloc[i]) / float(w.iloc[i - 1])
        navs.append(nav)
    ns = pd.Series(navs, index=w.index)
    dl = ns.pct_change().dropna()
    sd = float(dl.std())
    return dict(n=nmax, final=float(ns.iloc[-1]),
                ret=(float(ns.iloc[-1]) - 1) * 100,
                mdd=float((ns / ns.cummax() - 1).min() * 100),
                sharpe=float(dl.mean() / sd * np.sqrt(252)) if sd > 0 else None,
                hold_rate=float(sw.mean() * 100))


def run_rand_bench(df, *, ma=200, buy=0.97, sell=1.06, p_sell=0.02,
                   trials=120, cost=0.0, seed=20261024):
    """最严格对照：把「柱翻绿」换成「同频率随机事件」，其余结构完全不变。

    卖出 = (px > MA200×sell) 或 (每日以 p 概率随机卖)。
    p 取到使持仓率与 C 组相当，从而在同换手/同在场程度下比较。
    """
    close = df["close"]
    mav = close.rolling(ma).mean()
    cut = close.index[-1] - pd.DateOffset(years=int(YEARS))
    rng = np.random.default_rng(seed)
    vals, holds = [], []
    for _ in range(trials):
        state, hold = [], False
        for t in close.index:
            px = float(close.loc[t])
            mv = float(mav.loc[t]) if mav.loc[t] == mav.loc[t] else None
            if mv:
                if hold:
                    if px > mv * sell or rng.random() < p_sell:
                        hold = False
                elif px < mv * buy:
                    hold = True
            state.append(hold)
        stt = pd.Series(state, index=close.index, dtype=bool)
        w, sw = close[close.index >= cut], stt[stt.index >= cut]
        nav, navs = 1.0, [1.0]
        for i in range(1, len(w)):
            if bool(sw.iloc[i - 1]):
                nav *= float(w.iloc[i]) / float(w.iloc[i - 1])
            if bool(sw.iloc[i]) != bool(sw.iloc[i - 1]) and cost:
                nav *= (1.0 - cost)
            navs.append(nav)
        vals.append(float(navs[-1]))
        holds.append(float(sw.mean() * 100))
    a = np.array(vals)
    return dict(p=p_sell, trials=trials,
                mean=float(a.mean()), p05=float(np.percentile(a, 5)),
                p50=float(np.percentile(a, 50)), p95=float(np.percentile(a, 95)),
                max=float(a.max()), hold_rate=float(np.mean(holds)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(PROJECT_DIR, "out", "ve_macd_sell.json"))
    args = ap.parse_args()

    allout = dict(meta=dict(adj_csv=st.ADJ_CSV, years=YEARS,
                            macd=f"MACD({mc.MACD_FAST},{mc.MACD_SLOW},{mc.MACD_SIG})·柱=(DIF-DEA)×2",
                            turn_neg="hist[t-1]>0 且 hist[t]<=0",
                            cost="零手续费",
                            modes={k: d for k, d, _m in MODES}),
                  targets={})

    for tg in TARGETS:
        code, buy, sell, ma = tg["code"], tg["buy"], tg["sell"], tg["ma"]
        df, note, ext = st.extend_tail(st.load_ohlc(code), code)
        hist = mc.macd_hist(df["close"])
        tn = turn_negative(hist)

        res = {}
        for key, _label, mode in MODES:
            res[key] = run(df, ma=ma, buy=buy, sell=sell, hist=hist, mode=mode)
        A = res["A"]

        cut = df.index[-1] - pd.DateOffset(years=int(YEARS))
        hw = hist[hist.index >= cut].dropna()
        tnw = tn[tn.index >= cut]
        gaps = event_gap(df, hist, A, ma=ma, sell=sell)

        # ---- 证伪三件套 ----
        cost_scan = {}
        for key, _label, mode in MODES:
            cost_scan[key] = dict(
                c0=run_cost(df, ma=ma, buy=buy, sell=sell, hist=hist, mode=mode, cost=0.0),
                c2=run_cost(df, ma=ma, buy=buy, sell=sell, hist=hist, mode=mode, cost=0.002),
                c3=run_cost(df, ma=ma, buy=buy, sell=sell, hist=hist, mode=mode, cost=0.003))
        fixed_hold = [run_fixed_hold(df, ma=ma, buy=buy, nmax=N)
                      for N in (5, 10, 15, 20, 25, 30, 40, 50, 60, 80)]
        # 随机基准：p 取到持仓率与 C 组最接近
        best_rand = None
        for p in (0.015, 0.02, 0.03, 0.04, 0.06):
            rb = run_rand_bench(df, ma=ma, buy=buy, sell=sell, p_sell=p)
            if best_rand is None or abs(rb["hold_rate"] - res["C"]["hold_rate"]) < \
                    abs(best_rand["hold_rate"] - res["C"]["hold_rate"]):
                best_rand = rb
        # C 组在随机分布中的分位（越高越好；>95% 才算跑赢随机）
        Cfin = res["C"]["final"]
        pctile = None
        if best_rand:
            # 用正态近似由 p05/p50/p95 推，避免再跑一轮模拟
            lo, med, hi = best_rand["p05"], best_rand["p50"], best_rand["p95"]
            sd_est = (hi - lo) / (2 * 1.645) if hi > lo else 0
            if sd_est > 0:
                from math import erf, sqrt
                pctile = 0.5 * (1 + erf((Cfin - med) / (sd_est * sqrt(2)))) * 100

        allout["targets"][code] = dict(
            code=code, name=tg["name"], buy=buy, sell=sell, ma=ma,
            data_note=note, extended=ext, data_end=str(df.index.max().date()),
            **res,
            gaps=gaps, cost_scan=cost_scan, fixed_hold=fixed_hold,
            rand=best_rand, rand_pctile=pctile,
            hist_dist=dict(p50=float(hw.median()), neg=float((hw < 0).mean() * 100)),
            tn_count=int(tnw.sum()), tn_rate=float(tnw.mean() * 100),
        )

        # ---------------- 控制台 ----------------
        print("\n" + "=" * 98)
        print(f"{code} {tg['name']}　buy={buy:.2f} / sell={sell:.2f} / MA{ma}"
              f"　窗口 {A['d0']:%Y-%m-%d} ~ {A['d1']:%Y-%m-%d}（{A['n']} 日 / {A['yrs']:.2f} 年）")
        print(f"  数据：{note}")
        print("=" * 98)
        print(f"{'':20}" + "".join(f"{lab:>20}" for _k, lab, _m in MODES))
        for lab, key, nd, pct in (("期末净值", "final", 4, False),
                                  ("区间收益", "ret", 2, True),
                                  ("年化收益", "cagr", 2, True),
                                  ("最大回撤", "mdd", 2, True),
                                  ("夏普", "sharpe", 2, False),
                                  ("Calmar", "calmar", 2, False),
                                  ("持仓占比", "hold_rate", 1, True),
                                  ("买入/卖出", None, 0, False),
                                  ("平均单笔", "avg_ret", 2, True),
                                  ("平均持仓日", "avg_days", 1, False),
                                  ("最长持仓日", "max_days", 0, False)):
            cells = []
            for k, _l, _m in MODES:
                R = res[k]
                if key is None:
                    cells.append(f"{R['n_buy']}/{R['n_sell']}")
                elif key in ("sharpe", "calmar"):
                    cells.append("—" if R[key] is None else f"{R[key]:.2f}")
                elif pct:
                    cells.append(fmt(R[key], nd))
                else:
                    cells.append("—" if R[key] is None else f"{R[key]:.{nd}f}")
            print(f"{lab:20}" + "".join(f"{c:>20}" for c in cells))

        print(f"\n  柱翻绿事件（窗口内）：{allout['targets'][code]['tn_count']} 次；"
              f"绿柱天数占比 {allout['targets'][code]['hist_dist']['neg']:.1f}%")
        print(f"  诊断：每段持仓里「6% 止盈」与「柱翻绿」谁先到（相对买入日的交易日数）")
        print(f"    {'买入日':>12}{'A段结束':>12}{'6%在第N日':>11}{'翻绿在第N日':>12}"
              f"{'差(翻绿−6%)':>13}")
        for g in gaps:
            p = "—" if g["pct_after"] is None else str(g["pct_after"])
            q = "—" if g["tn_after"] is None else str(g["tn_after"])
            d = "—" if g["gap"] is None else f"{g['gap']:+d}"
            print(f"    {g['start']:>12}{g['end']:>12}{p:>11}{q:>12}{d:>13}")
        gd = [g["gap"] for g in gaps if g["gap"] is not None]
        if gd:
            print(f"    ⇒ 有 {len(gd)} 段可比：翻绿比 6% 晚 {sum(1 for x in gd if x>0)} 段、"
                  f"早 {sum(1 for x in gd if x<0)} 段、同日 {sum(1 for x in gd if x==0)} 段；"
                  f"中位差 {np.median(gd):+.0f} 个交易日")
        print(f"  B 组逐笔（只柱翻绿卖）：")
        for t in res["B"]["trades"]:
            by = "翻绿" if t.get("by_tn") else ("6%" if t.get("by_pct") else "窗末未平")
            print(f"    {t['start']} → {t['end']}  {t['days']:>3}日  {fmt(t['ret'])}"
                  f"  退出={by}{'  [未平仓]' if t.get('open') else ''}")
        print(f"  A 组逐笔（原版）：")
        for t in A["trades"]:
            print(f"    {t['start']} → {t['end']}  {t['days']:>3}日  {fmt(t['ret'])}"
                  f"{'  [未平仓]' if t.get('open') else ''}")

        # ---- 证伪结果 ----
        print(f"\n  【证伪 1】成本敏感性（每次换仓扣双边成本）")
        print(f"    {'模式':<20}{'零成本':>10}{'0.2%':>10}{'0.3%':>10}{'换仓次数':>9}")
        for key, label, _m in MODES:
            cs = cost_scan[key]
            print(f"    {label:<20}{cs['c0']['final']:>10.4f}{cs['c2']['final']:>10.4f}"
                  f"{cs['c3']['final']:>10.4f}{cs['c0']['n_trade']:>9}")

        print(f"\n  【证伪 2】固定持有 N 日就卖（不看 MACD；C 组平均持仓 {res['C']['avg_days']:.0f} 日）")
        bf = max(fixed_hold, key=lambda r: r["final"])
        for r in fixed_hold:
            mark = "  ← ≈C 组持仓天数" if abs(r["n"] - round(res["C"]["avg_days"])) <= 2 else ""
            print(f"    {r['n']:>3} 日  {r['final']:.4f}  {fmt(r['ret'])}"
                  f"  夏普 {r['sharpe']:.2f}  持仓 {r['hold_rate']:.1f}%{mark}")
        print(f"    ⇒ 固定持有最优 N={bf['n']} 日 = {bf['final']:.4f}；"
              f"C 组 = {res['C']['final']:.4f}"
              f"{'（C 组未跑赢简单持有）' if res['C']['final'] <= bf['final'] else '（C 组优于简单持有）'}")

        if best_rand:
            print(f"\n  【证伪 3】随机卖出基准（把「翻绿」换成同频率随机事件，结构不变）")
            print(f"    p={best_rand['p']:.3f}/日　{best_rand['trials']} 次模拟，持仓率 "
                  f"{best_rand['hold_rate']:.1f}%（C 组 {res['C']['hold_rate']:.1f}%）")
            print(f"    分布：p05 {best_rand['p05']:.4f} / 中位 {best_rand['p50']:.4f}"
                  f" / p95 {best_rand['p95']:.4f} / 最大 {best_rand['max']:.4f}")
            print(f"    C 组 {res['C']['final']:.4f} → 位于随机分布约 "
                  f"{'—' if pctile is None else f'{pctile:.1f}%'} 分位"
                  f"{'（跑赢 95% 随机，有信息量）' if (pctile or 0) > 95 else '（未达 95% 分位，随机也能做到）'}")

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(allout, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nJSON → {args.json}")


if __name__ == "__main__":
    main()
