# -*- coding: utf-8 -*-
"""证伪实验（本轮最关键）：
实验1  成本敏感性：双边 0.2%（实盘口径）下重算四种模式 —— 交易次数翻 3 倍会不会吃掉超额？
实验2  固定持有 N 日对照：若「买入后无条件持有 N 日就卖」也能达到 C 组水平，
        则 C 的改善来自「缩短持仓 / 少在场」，而不是 MACD 的信息量。
实验3  随机卖出对照：随机选点卖出（同样交易次数），看 C 组是否落在随机分布之外。
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/娱乐（音乐、视频）Q-骑牛/ETF轮动战法")
import ve_j_entry_study as st
import ve_macd_study as mc
import ve_macd_sell_study as ms

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run_cost(df, *, ma, buy, sell, hist, mode, cost=0.0):
    """与 ms.run 同逻辑，额外支持每次换仓扣 cost（双边）。"""
    close = df["close"]
    mav = close.rolling(ma).mean()
    tn = ms.turn_negative(hist)
    state, hold = [], False
    for t in close.index:
        px = float(close.loc[t])
        mv = float(mav.loc[t]) if mav.loc[t] == mav.loc[t] else None
        if mv:
            if hold:
                hp, ht = px > mv * sell, bool(tn.loc[t])
                do = {"pct": hp, "hist": ht, "or": hp or ht, "and": hp and ht}[mode]
                if do:
                    hold = False
            elif px < mv * buy:
                hold = True
        state.append(hold)
    stt = pd.Series(state, index=close.index, dtype=bool)

    cut = close.index[-1] - pd.DateOffset(years=5)
    w, sw = close[close.index >= cut], stt[stt.index >= cut]
    nav, navs = 1.0, [1.0]
    for i in range(1, len(w)):
        r = float(w.iloc[i]) / float(w.iloc[i - 1])
        if bool(sw.iloc[i - 1]):
            nav *= r
        if bool(sw.iloc[i]) != bool(sw.iloc[i - 1]) and cost:
            nav *= (1.0 - cost)          # 换仓当日扣成本
        navs.append(nav)
    ns = pd.Series(navs, index=w.index)
    dl = ns.pct_change().dropna()
    sd = float(dl.std())
    return dict(final=float(ns.iloc[-1]), ret=(float(ns.iloc[-1]) - 1) * 100,
                mdd=float((ns / ns.cummax() - 1).min() * 100),
                sharpe=float(dl.mean() / sd * np.sqrt(252)) if sd > 0 else None,
                hold_rate=float(sw.mean() * 100),
                n_trade=int((sw.astype(int).diff().fillna(0) != 0).sum()))


def run_fixed_hold(df, *, ma, buy, sell, nmax):
    """进区间买入 → 无条件持有 N 个交易日后卖出（不看价格，不看 MACD）。"""
    close = df["close"]
    mav = close.rolling(ma).mean()
    idx = list(close.index)
    state, hold, cnt = [], False, 0
    for i, t in enumerate(idx):
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
    cut = close.index[-1] - pd.DateOffset(years=5)
    w, sw = close[close.index >= cut], stt[stt.index >= cut]
    nav, navs = 1.0, [1.0]
    for i in range(1, len(w)):
        if bool(sw.iloc[i - 1]):
            nav *= float(w.iloc[i]) / float(w.iloc[i - 1])
        navs.append(nav)
    ns = pd.Series(navs, index=w.index)
    dl = ns.pct_change().dropna()
    sd = float(dl.std())
    return dict(final=float(ns.iloc[-1]), ret=(float(ns.iloc[-1]) - 1) * 100,
                mdd=float((ns / ns.cummax() - 1).min() * 100),
                sharpe=float(dl.mean() / sd * np.sqrt(252)) if sd > 0 else None,
                hold_rate=float(sw.mean() * 100))


def run_random_sell(df, *, ma, buy, sell, hist, p_sell=0.06, trials=400, seed=7):
    """买入后每个交易日以概率 p_sell 随机卖出 —— 用于给出「同样换手率下的随机基准」分布。"""
    close = df["close"]
    mav = close.rolling(ma).mean()
    rng = np.random.default_rng(seed)
    cut = close.index[-1] - pd.DateOffset(years=5)
    w = close[close.index >= cut]
    out = []
    for _ in range(trials):
        hold = False
        navs, nav = [1.0], 1.0
        for i in range(1, len(w)):
            if hold:
                nav *= float(w.iloc[i]) / float(w.iloc[i - 1])
            if i < len(w) - 1:
                px = float(w.iloc[i])
                mv = float(mav.loc[w.index[i]])
                if mv == mv:
                    if hold:
                        if rng.random() < p_sell:
                            hold = False
                    elif px < mv * buy:
                        hold = True
            navs.append(nav)
        out.append(float(navs[-1]))
    a = np.array(out)
    return dict(mean=float(a.mean()), p05=float(np.percentile(a, 5)),
                p50=float(np.percentile(a, 50)), p95=float(np.percentile(a, 95)),
                max=float(a.max()))


for tg in ms.TARGETS:
    code, buy, sell, ma = tg["code"], tg["buy"], tg["sell"], tg["ma"]
    df, _, _ = st.extend_tail(st.load_ohlc(code), code)
    hist = mc.macd_hist(df["close"])

    print("=" * 92)
    print(f"{code} {tg['name']}")
    print("=" * 92)

    # ---- 实验 1：成本敏感性 ----
    print("实验 1：双边 0.2% 成本下（实盘口径）")
    print(f"{'模式':<22}{'零成本':>12}{'双边0.2%':>12}{'双边0.3%':>12}{'换仓次数':>10}")
    for key, label, mode in ms.MODES:
        r0 = run_cost(df, ma=ma, buy=buy, sell=sell, hist=hist, mode=mode, cost=0.0)
        r2 = run_cost(df, ma=ma, buy=buy, sell=sell, hist=hist, mode=mode, cost=0.002)
        r3 = run_cost(df, ma=ma, buy=buy, sell=sell, hist=hist, mode=mode, cost=0.003)
        print(f"{label:<22}{r0['final']:>12.4f}{r2['final']:>12.4f}"
              f"{r3['final']:>12.4f}{r0['n_trade']:>10}")

    # ---- 实验 2：固定持有 N 日 ----
    C = ms.run(df, ma=ma, buy=buy, sell=sell, hist=hist, mode="or")
    c_days = C["avg_days"]
    print(f"\n实验 2：固定持有 N 日就卖（完全不看 MACD；C 组平均持仓 {c_days:.0f} 日）")
    print(f"{'固定持有':<12}{'净值':>10}{'收益':>11}{'回撤':>10}{'夏普':>8}{'持仓':>8}")
    best = None
    for N in (5, 10, 15, 20, 22, 25, 30, 40, 50, 60, 80):
        r = run_fixed_hold(df, ma=ma, buy=buy, sell=sell, nmax=N)
        mark = ""
        if best is None or r["final"] > best[1]:
            best = (N, r["final"])
        if abs(N - round(c_days)) <= 1:
            mark = "  ← 与 C 组持仓天数相当"
        print(f"{N:>3} 日{'':<6}{r['final']:>10.4f}{r['ret']:>10.2f}%"
              f"{r['mdd']:>9.2f}%{r['sharpe']:>8.2f}{r['hold_rate']:>7.1f}%{mark}")
    print(f"  ⇒ 固定持有最优 N={best[0]} 日 → {best[1]:.4f}；"
          f"C 组（柱翻绿/6%）={C['final']:.4f}；原版 A={ms.run(df, ma=ma, buy=buy, sell=sell, hist=hist, mode='pct')['final']:.4f}")

    # ---- 实验 3：随机卖出基准 ----
    A = ms.run(df, ma=ma, buy=buy, sell=sell, hist=hist, mode="pct")
    p_hat = 1.0 / max(A["avg_days"], 1.0)
    rs = run_random_sell(df, ma=ma, buy=buy, sell=sell, hist=hist, p_sell=p_hat)
    print(f"\n实验 3：随机卖出基准（每日期望卖出概率 p={p_hat:.3f}，400 次模拟）")
    print(f"  随机分布：p05 {rs['p05']:.4f} / 中位 {rs['p50']:.4f} / 均值 {rs['mean']:.4f} / p95 {rs['p95']:.4f}")
    print(f"  C 组 {C['final']:.4f} 位于随机分布的"
          f"{'前 5%（有信息量）' if C['final'] > rs['p95'] else '中部（与随机无区别）'}")
