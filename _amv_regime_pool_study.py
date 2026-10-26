#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""0AMV 空头区间切换「小轮动池」研究（2026-10-26）

在【D3+P3】生产口径之上：
  · 多头（0AMV 单根≥+4% 或两日累计≥+4%）→ 主轮动池（9只）正常轮动
  · 空头（0AMV 单根≤−2.3% 或三日累计≤−2.3%）→ 切换小子轮动池轮动
信号次日生效（shift 1），无未来函数。

小子池候选（先验，不事后挑）：
  B 避险双雄 [518880 黄金, 513100 纳指]
  C 避险三杰 [518880, 513100, 513660 恒生]
  D 避险+防御 [518880, 513100, 513660, 561700 电力]
  E 避险+宽基 [518880, 513100, 510300 沪深300]
对照：
  A 基线 D3+P3（全程主池，不动）
"""
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import etf_backtest_long as bl  # noqa: E402
from _amv_gate_study import load_amv, build_amv_state  # noqa: E402

ADJ_2019 = os.path.join(HERE, "tdx_adj_long_2019_2026.csv")
ADJ_2015 = os.path.join(HERE, "tdx_adj_long_2015_2026.csv")

ALT_POOLS = {
    "B避险双雄": ["518880", "513100"],
    "C避险三杰": ["518880", "513100", "513660"],
    "D避险+防御": ["518880", "513100", "513660", "561700"],
    "E避险+宽基": ["518880", "513100", "510300"],
}


def alt_mask_from_state(state, dates):
    """空头日（状态='bear'）次日生效 → alt_mask 布尔序列。"""
    s = pd.Series(1.0, index=dates)
    am = state.reindex(dates).ffill().fillna("bull")
    s[am == "bear"] = 1.0
    am = pd.Series(am.values, index=dates)
    return am.shift(1).fillna("bull") == "bear"


def run_window(panel, pool, defensive, start, end, label, state_src, alt_pools):
    dates = panel.index
    start_i = int(dates.searchsorted(pd.Timestamp(start)))
    end_i = min(int(dates.searchsorted(pd.Timestamp(end))), len(dates) - 1)
    kw = dict(sticky=1.0, buy=0.001, sell=0.001, value_engine=True,
              **bl.LIVE_COMPOSITE)

    base = bl.backtest(panel, dates, start_i, end_i, pool, defensive, **kw)
    base_st = bl.stats(base["navs"])
    rows = [dict(variant="A基线(全程主池)", alt=None, nav=base["navs"],
                 st=base_st, trades=len(base["trades"]),
                 cash=base["cash_days"], r=base)]

    amv_r = state_src
    state = build_amv_state(amv_r, bear_priority=True, init="bull")
    am = alt_mask_from_state(state, dates)
    for name, altp in alt_pools.items():
        r2 = bl.backtest(panel, dates, start_i, end_i, pool, defensive,
                         **kw, alt_pool=altp, alt_mask=am)
        st2 = bl.stats(r2["navs"])
        rows.append(dict(variant=f"{name}(空头小子池)", alt=altp,
                         nav=r2["navs"], st=st2,
                         trades=len(r2["trades"]), cash=r2["cash_days"],
                         r=r2))
    return rows


def main():
    t0 = time.time()
    amv_r = load_amv()
    rot, deff, watch = bl.read_pools()
    defensive = deff[0]

    results = {}
    for key, (csv, start, label) in {
        "main": (ADJ_2019, "2018-12-03", "main"),
        "long": (ADJ_2015, "2014-12-31", "long"),
    }.items():
        panel = bl.read_panel(csv)
        rows = run_window(panel, rot, defensive, start, "2026-12-31",
                          label, amv_r, ALT_POOLS)
        results[key] = dict(panel=panel, rows=rows)

    print("=" * 118)
    print("0AMV 空头区间切换小子轮动池　vs　D3+P3 基线")
    print("=" * 118)
    for key, res in results.items():
        print(f"\n【{key} 窗口】")
        hdr = (f"{'方案':<22}{'净值':>9}{'总收益':>10}{'年化':>9}{'MDD':>9}"
                f"{'夏普':>7}{'Calmar':>8}{'换仓':>6}{'持币日':>7}")
        print(hdr)
        print("-" * len(hdr))
        for row in res["rows"]:
            st = row["st"]
            print(f"{row['variant']:<22}{row['nav'].iloc[-1]:>9.4f}"
                  f"{st['total']*100:>9.2f}%{st['ann']*100:>8.2f}%"
                  f"{st['mdd']*100:>8.2f}%"
                  f"{(f'{st['sharpe']:.2f}' if st['sharpe'] is not None else '-'):>7}"
                  f"{(f'{st['calmar']:.2f}' if st['calmar'] is not None else '-'):>8}"
                  f"{row['trades']:>6}{row['cash']:>7}")

    # ---- 逐年（主窗） ----
    print("\n【逐年收益·主窗】 A基线 vs 小子池变体")
    yrows = {row["variant"]: {y["year"]: y for y in bl.yearly(row["nav"])}
             for row in results["main"]["rows"]}
    yrs = sorted({y for yl in yrows.values() for y in yl})
    print(f"{'方案':<22}" + "".join(f"{y:>10}" for y in yrs))
    for v, yl in yrows.items():
        cells = []
        for y in yrs:
            vv = yl.get(y)
            cells.append(f"{vv['ret']*100:>9.1f}%" if vv else f"{'-':>10}")
        print(f"{v:<22}" + "".join(cells))

    # ---- 空头日小子池内各标的表现（诊断） ----
    state = build_amv_state(amv_r, bear_priority=True, init="bull")
    dates = results["main"]["panel"].index
    am = alt_mask_from_state(state, dates)
    panel = results["main"]["panel"]
    print("\n【诊断·主窗】 空头日（次日生效）主池各标的持有收益分布：")
    seg = panel.loc[dates]
    rets = seg.pct_change().shift(-1)
    for c in rot:
        if c not in seg.columns:
            continue
        rr = rets[c][dates >= "2018-12-03"]
        bearer = rr[am.reindex(rr.index).fillna(False)]
        buller = rr[~am.reindex(rr.index).fillna(False)]
        print(f"  {c:<8} 空头日均 {bearer.mean()*100:+7.3f}%  多头日均 {buller.mean()*100:+7.3f}%"
              f"  空头正占比 {100*(bearer>0).mean():5.1f}%")

    print(f"\n耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
