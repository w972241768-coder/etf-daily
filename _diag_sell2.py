# -*- coding: utf-8 -*-
"""严格证伪：把「柱翻绿」替换成「同频率随机事件」，其余完全不变。

设计（这是最干净的对照）：
  C 组卖出条件 = (px > MA200×1.06) 或 (柱翻绿)
  仿 C 组       = (px > MA200×1.06) 或 (每日以 p 概率随机卖)
  其中 p 取「翻绿事件在窗口内的发生频率」，使换手率/持仓率与 C 组同量级。

若仿 C 组的分布能覆盖 C 组净值 ⇒ 柱翻绿没有提供超越「随机选个时点卖出」的信息量。
同时输出每组的持仓率，确认频率确实匹配（不匹配就重调 p）。
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/娱乐（音乐、视频）Q-骑牛/ETF轮动战法")
import ve_j_entry_study as st
import ve_macd_study as mc
import ve_macd_sell_study as ms

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run_rand_or_pct(df, *, ma, buy, sell, p_sell, rng, cost=0.0):
    """卖出 = 6% 止盈 OR 每日 p 概率随机卖。"""
    close = df["close"]
    mav = close.rolling(ma).mean()
    cut = close.index[-1] - pd.DateOffset(years=5)
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
    ns = pd.Series(navs, index=w.index)
    dl = ns.pct_change().dropna()
    sd = float(dl.std())
    return dict(final=float(ns.iloc[-1]),
                mdd=float((ns / ns.cummax() - 1).min() * 100),
                sharpe=float(dl.mean() / sd * np.sqrt(252)) if sd > 0 else None,
                hold_rate=float(sw.mean() * 100))


for tg in ms.TARGETS:
    code, buy, sell, ma = tg["code"], tg["buy"], tg["sell"], tg["ma"]
    df, _, _ = st.extend_tail(st.load_ohlc(code), code)
    hist = mc.macd_hist(df["close"])
    tn = ms.turn_negative(hist)
    cut = df.index[-1] - pd.DateOffset(years=5)
    tn_rate = float(tn[tn.index >= cut].mean())          # 翻绿事件发生率

    C = ms.run(df, ma=ma, buy=buy, sell=sell, hist=hist, mode="or")
    A = ms.run(df, ma=ma, buy=buy, sell=sell, hist=hist, mode="pct")

    print("=" * 92)
    print(f"{code}　翻绿事件发生率 {tn_rate*100:.2f}%/日"
          f"（窗口内 {int(tn[tn.index>=cut].sum())} 次 / {len(tn[tn.index>=cut])} 日）")
    print("=" * 92)
    print(f"  A 原版（仅 6%）        净值 {A['final']:.4f}  持仓 {A['hold_rate']:.1f}%")
    print(f"  C 组（6% 或 翻绿）      净值 {C['final']:.4f}  持仓 {C['hold_rate']:.1f}%")
    print()

    # 用不同 p 扫一遍，找出持仓率最接近 C 组的那档，再做正式对比
    print(f"  {'p/日':>7}{'随机+6%净值(中位)':>20}{'持仓率':>10}{'vs C 组持仓':>12}")
    rng = np.random.default_rng(20261024)
    best = None
    for p in (0.02, tn_rate, 0.05, 0.08):
        vals, holds = [], []
        for _ in range(120):
            r = run_rand_or_pct(df, ma=ma, buy=buy, sell=sell, p_sell=p, rng=rng)
            vals.append(r["final"])
            holds.append(r["hold_rate"])
        med, hm = float(np.median(vals)), float(np.mean(holds))
        if best is None or abs(hm - C["hold_rate"]) < abs(best[2] - C["hold_rate"]):
            best = (p, med, hm, np.array(vals))
        print(f"  {p:>7.4f}{med:>20.4f}{hm:>9.1f}%{hm-C['hold_rate']:>+11.1f}pp")

    p, med, hm, arr = best
    print(f"\n  ⇒ 持仓率最接近的随机版本：p={p:.4f}（持仓 {hm:.1f}% vs C 组 {C['hold_rate']:.1f}%）")
    print(f"    120 次模拟净值分布：p05 {np.percentile(arr,5):.4f} / 中位 {med:.4f}"
          f" / p95 {np.percentile(arr,95):.4f} / 最大 {arr.max():.4f}")
    print(f"    C 组 {C['final']:.4f} 的分位："
          f"{(arr < C['final']).mean()*100:.1f}%（即随机版本有 {100-(arr<C['final']).mean()*100:.1f}% 比它更好）")

    # 含成本对比
    rng2 = np.random.default_rng(7)
    c2 = run_rand_or_pct(df, ma=ma, buy=buy, sell=sell, p_sell=p, rng=rng2, cost=0.002)
    print(f"\n  含双边 0.2% 成本：仿 C 组 {c2['final']:.4f}（持仓 {c2['hold_rate']:.1f}%）")
    print(f"                    C 组见上表（此前已算：512040 2.0808 / 512890 1.9824）")
