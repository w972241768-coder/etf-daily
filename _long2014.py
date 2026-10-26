#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""超长周期回测（2014 起）：验证策略是否长期维持高收益

⚠️ 关键前提：主选池 9 只里，2014 年只有 3~4 只已上市
   （588170 是 2025-04 才上市）。早期"池子小"与现在"池子大"是**不同策略**，
   所以每段都必须标出「当段实际可用标的数」，否则结论会被误读。

⚠️ 本地研究脚本，不入库。
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import etf_backtest_long as bl  # noqa: E402

BASE = bl.LIVE_COMPOSITE
A1 = dict(weak_mode="allneg", weak_rank=1, weak_delay=0)
CSV = os.path.join(HERE, "tdx_adj_long_2014_2026.csv")


def avail(r):
    ns = [n for _d, n in (r.get("avail_log") or []) if n]
    if not ns:
        return (0, 0.0)
    return (min(ns), float(np.mean(ns)))


def run(panel, dates, pool, dfn, s_date, **kw):
    si = max(int(dates.searchsorted(pd.Timestamp(s_date), side="left")), 0)
    p = dict(BASE)
    p.update(kw)
    r = bl.backtest(panel, dates, si, len(dates) - 1, pool, dfn,
                    buy=0.0001, sell=0.0001, W=20, MA=20, value_engine=True, **p)
    return r, bl.stats(r["navs"])


def main():
    panel = bl.read_panel(CSV)
    dates = panel.index
    rot, deff, _w = bl.read_pools()
    pool = [c for c in rot if c in panel.columns]
    dfn = deff[0] if deff else "512040"

    print(f"数据 {dates[0]:%Y-%m-%d} → {dates[-1]:%Y-%m-%d}（{len(dates)} 根）")
    print("\n各标的首个有效交易日：")
    for c in pool:
        s = panel[c].dropna()
        print(f"  {c} {bl.NAMES.get(c, c)[:18]:20} {s.index[0]:%Y-%m-%d}")

    print("\n" + "=" * 122)
    print("① 分段回测（每段都从该起点跑到 2026-10-26，段间相互重叠）")
    print(f"{'起点':>12} {'池规模(少/均)':>14} {'基线净值':>10} {'＋抄底':>10} {'Δ净值':>9} "
          f"{'基线MDD':>9} {'＋抄底MDD':>10} {'基线Calmar':>11} {'＋抄底C':>8} {'触发':>4}")
    print("-" * 122)
    segs = ["2014-01-03", "2015-01-05", "2016-01-04", "2017-01-03",
            "2018-12-03", "2019-12-31", "2021-10-25"]
    for sd in segs:
        rb, sb = run(panel, dates, pool, dfn, sd)
        ra, sa = run(panel, dates, pool, dfn, sd, **A1)
        mn, av = avail(rb)
        print(f"{sd:>12} {f'{mn:.0f}/{av:.1f}':>14} {rb['final']:10.4f} {ra['final']:10.4f} "
              f"{ra['final']-rb['final']:+9.4f} {sb['mdd']*100:8.2f}% {sa['mdd']*100:9.2f}% "
              f"{sb['calmar'] or 0:11.2f} {sa['calmar'] or 0:8.2f} "
              f"{len(ra.get('weak_events') or []):4d}")

    print("\n" + "=" * 122)
    print("② 逐年（2014 起，收益 %；Δ = ＋抄底 − 基线）")
    rb, sb = run(panel, dates, pool, dfn, "2014-01-03")
    ra, sa = run(panel, dates, pool, dfn, "2014-01-03", **A1)
    yb = {d["year"]: d for d in bl.yearly(rb["navs"])}
    ya = {d["year"]: d for d in bl.yearly(ra["navs"])}
    print(f"{'年份':>6} {'基线':>8} {'基线MDD':>9} {'＋抄底':>9} {'＋抄底MDD':>10} {'Δ':>9} 判定")
    print("-" * 74)
    win = lose = 0
    for y in sorted(yb):
        b, a = yb[y], ya.get(y, {})
        br, ar = (b.get("ret") or 0) * 100, (a.get("ret") or 0) * 100
        bm, am = (b.get("mdd") or 0) * 100, (a.get("mdd") or 0) * 100
        d = ar - br
        win += 1 if d > 0.05 else 0
        lose += 1 if d < -0.05 else 0
        print(f"{y:>6} {br:7.1f}% {bm:8.1f}% {ar:8.1f}% {am:9.1f}% {d:+8.1f}pp  "
              f"{'✔' if d > 0.05 else ('✘' if d < -0.05 else '—')}")
    print(f"跑赢 {win} 年 / 跑输 {lose} 年 / 持平 {len(yb)-win-lose} 年")

    print("\n" + "=" * 122)
    print("③ 纯轮动（不加 D3/P3）在超长周期的表现 —— 检验规则本身是否依赖特定时段")
    for sd in ("2014-01-03", "2018-12-03"):
        r0, s0 = run(panel, dates, pool, dfn, sd)
        # 关掉 D3/P3 = 基础策略
        si = max(int(dates.searchsorted(pd.Timestamp(sd), side="left")), 0)
        rp = bl.backtest(panel, dates, si, len(dates) - 1, pool, dfn,
                         buy=0.0001, sell=0.0001, W=20, MA=20, value_engine=True)
        sp = bl.stats(rp["navs"])
        print(f"  {sd} 起：基础轮动 {rp['final']:8.4f} / MDD {sp['mdd']*100:6.2f}% "
              f"/ Calmar {sp['calmar'] or 0:5.2f} ｜ 年化 {sp['ann']*100:6.2f}%")


if __name__ == "__main__":
    main()
