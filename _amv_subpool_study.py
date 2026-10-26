#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""0AMV 空头区间「小轮动池」研究（2026-10-26）

在 D3+P3 生产口径之上，用官方 0AMV 真值判定多/空头：
  · 多头区间：主轮动池 9 只正常轮动
  · 空头区间：改用小轮动池（从主池内选出，不再新增基金）继续轮动，保持满仓
  · 信号次日生效（shift 1），无未来函数

候选小池（按资产属性先验选取，杜绝事后按历史收益选股）：
  S1 避险三杰：518880黄金/513100纳指/513660恒生（非 A 股三低相关）
  S2 避险+电力：S1 + 561700电力（防御性 A 股）
  S3 黄金+纳指：518880/513100（最小避险池）
  S4 低波四杰：S2 + 512040价值（含防守位，允许参与轮动排名）

对照：
  A 基线 D3+P3（全程主池）
  B1~B4 空头换池（各候选）
先验证钩子逐位等价（alt_pool=None → maxdiff 0.0）。
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import etf_backtest_long as bl  # noqa: E402

ADJ_2019 = os.path.join(HERE, "tdx_adj_long_2019_2026.csv")
ADJ_2015 = os.path.join(HERE, "tdx_adj_long_2015_2026.csv")
AMV_CSV = os.path.join(HERE, "out", "amv_official.csv")

AMV_BULL = 0.04
AMV_BEAR = -0.023

SUB_POOLS = {
    "S1避险三杰": ["518880", "513100", "513660"],
    "S2避险+电力": ["518880", "513100", "513660", "561700"],
    "S3黄金+纳指": ["518880", "513100"],
    "S4低波四杰": ["518880", "513100", "513660", "561700", "512040"],
}


def load_amv() -> pd.Series:
    df = pd.read_csv(AMV_CSV, parse_dates=["date"])
    df = df.sort_values("date").set_index("date")
    return df["change_pct"] / 100.0


def build_amv_state(r: pd.Series, bear_priority: bool = True,
                    init: str = "bull") -> pd.Series:
    r = r.astype(float)
    two = (1 + r).rolling(2).apply(np.prod, raw=True) - 1.0
    three = (1 + r).rolling(3).apply(np.prod, raw=True) - 1.0
    bull_trig = (r >= AMV_BULL) | (two >= AMV_BULL)
    bear_trig = (r <= AMV_BEAR) | (three <= AMV_BEAR)
    states = []
    st = init
    for b, be in zip(bull_trig.tolist(), bear_trig.tolist()):
        b, be = bool(b), bool(be)
        if be and b:
            st = "bear" if bear_priority else "bull"
        elif be:
            st = "bear"
        elif b:
            st = "bull"
        states.append(st)
    return pd.Series(states, index=r.index)


def amv_alt_mask(state: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """次日生效的空头掩码：True = 该日使用小池。"""
    s = pd.Series(False, index=dates)
    am = state.reindex(dates).ffill().fillna("bull")
    s[am == "bear"] = True
    return s.shift(1).fillna(False).astype(bool)


def run_base(panel, pool, defensive, start, end, kw):
    dates = panel.index
    start_i = int(dates.searchsorted(pd.Timestamp(start)))
    end_i = min(int(dates.searchsorted(pd.Timestamp(end))), len(dates) - 1)
    r = bl.backtest(panel, dates, start_i, end_i, pool, defensive, **kw)
    return r, bl.stats(r["navs"]), start_i, end_i


def run_alt(panel, pool, defensive, start, end, alt_pool, state, kw):
    dates = panel.index
    start_i = int(dates.searchsorted(pd.Timestamp(start)))
    end_i = min(int(dates.searchsorted(pd.Timestamp(end))), len(dates) - 1)
    mask = amv_alt_mask(state, dates)
    r = bl.backtest(panel, dates, start_i, end_i, pool, defensive,
                    **kw, alt_pool=alt_pool, alt_mask=mask)
    st = bl.stats(r["navs"])
    return r, st


def main():
    t0 = time.time()
    amv_r = load_amv()
    state = build_amv_state(amv_r)
    rot, deff, watch = bl.read_pools()
    defensive = deff[0] if deff else bl.DEFENSIVE
    kw = dict(sticky=1.0, buy=0.001, sell=0.001, value_engine=True,
              **bl.LIVE_COMPOSITE)

    results = {}
    for key, (csv, start) in {
        "main": (ADJ_2019, "2018-12-03"),
        "long": (ADJ_2015, "2014-12-31"),
    }.items():
        panel = bl.read_panel(csv)
        r_base, st_base, si, ei = run_base(panel, rot, defensive, start,
                                            "2026-12-31", kw)
        rows = [dict(variant="A基线D3+P3", nav=r_base["navs"], st=st_base,
                     trades=len(r_base["trades"]), cash=r_base["cash_days"],
                     alt=0, r=r_base)]
        # 钩子等价性验证（主窗）
        verify = None
        if key == "main":
            all_none = bl.backtest(panel, panel.index, si, ei, rot, defensive,
                                   **kw, alt_pool=None, alt_mask=None)
            verify = float((r_base["navs"] - all_none["navs"]).abs().max())
        for label, sub in SUB_POOLS.items():
            r2, st2 = run_alt(panel, rot, defensive, start, "2026-12-31",
                               sub, state, kw)
            rows.append(dict(variant=f"B_{label}", nav=r2["navs"], st=st2,
                             trades=len(r2["trades"]), cash=r2["cash_days"],
                             alt=r2.get("alt_days", 0), r=r2))
        results[key] = dict(rows=rows, verify=verify)

    pd.set_option("display.width", 200)
    print("=" * 118)
    print("0AMV 空头区间小轮动池 　vs　 D3+P3 基线（多头仍主池）")
    print("=" * 118)
    for key, res in results.items():
        print(f"\n【{key} 窗口】  钩子等价 maxdiff = {res['verify']}")
        hdr = (f"{'方案':<26}{'净值':>9}{'总收益':>10}{'年化':>9}{'MDD':>9}"
                f"{'夏普':>7}{'Calmar':>8}{'换仓':>6}{'持币日':>7}{'空头池日':>9}")
        print(hdr)
        print("-" * len(hdr))
        for row in res["rows"]:
            st = row["st"]
            print(f"{row['variant']:<26}{row['nav'].iloc[-1]:>9.4f}"
                  f"{st['total']*100:>9.2f}%{st['ann']*100:>8.2f}%"
                  f"{st['mdd']*100:>8.2f}%"
                  f"{(f'{st['sharpe']:.2f}' if st['sharpe'] is not None else '-'):>7}"
                  f"{(f'{st['calmar']:.2f}' if st['calmar'] is not None else '-'):>8}"
                  f"{row['trades']:>6}{row['cash']:>7}{row['alt']:>9}")

    print("\n【逐年收益·主窗】 A基线 / 各空头小池")
    rows_main = results["main"]["rows"]
    yrows = [dict(variant=r["variant"],
                  yearly={y["year"]: y for y in bl.yearly(r["nav"])})
             for r in rows_main]
    yrs = sorted({y for row in yrows for y in row["yearly"]})
    print(f"{'方案':<26}" + "".join(f"{y:>10}" for y in yrs))
    for row in yrows:
        cells = []
        for y in yrs:
            v = row["yearly"].get(y)
            cells.append(f"{v['ret']*100:>9.1f}%" if v else f"{'-':>10}")
        print(f"{row['variant']:<26}" + "".join(cells))

    out = {}
    for key, res in results.items():
        out[key] = dict(verify=res["verify"],
                        rows=[dict(variant=r["variant"],
                                   nav=round(float(r["nav"].iloc[-1]), 6),
                                   total=round(r["st"]["total"], 6),
                                   ann=round(r["st"]["ann"], 6),
                                   mdd=round(r["st"]["mdd"], 6),
                                   sharpe=r["st"]["sharpe"],
                                   calmar=r["st"]["calmar"],
                                   trades=r["trades"], cash=r["cash"],
                                   alt=r["alt"]) for r in res["rows"]])
    with open(os.path.join(HERE, "out", "amv_subpool_results.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n详情已存 out/amv_subpool_results.json  耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
