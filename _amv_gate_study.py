#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""0AMV（指南针活跃市值官方真值）空头半仓大闸研究（2026-10-26）

在【D3+P3】生产口径（LIVE_COMPOSITE）之上，叠加活跃市值波段择时：
  · 多头：活跃市值单根 +4% 阳线 或 连续两日累计 ≥ +4% → 满仓（scale=1.0）
  · 空头：活跃市值单根 -2.3% 阴线 或 连续三日累计 ≤ -2.3% → 半仓（scale=0.5）尝试
信号以当日收盘定，次日生效（shift 1），无未来函数。

对照组：
  A 基线 D3+P3（scale 全 1.0）
  B 主案 D3+P3 + AMV空头半仓（0.5）
  C 极端案 D3+P3 + AMV空头清零（0.0，退场）
敏感度：初始态（多头/空头）、触发冲突优先级（空头优先/多头优先）、仅单日触发。

先验证钩子逐位等价（scale_series=None vs 全1.0 → maxdiff 0.0）。
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


def load_amv() -> pd.Series:
    df = pd.read_csv(AMV_CSV, parse_dates=["date"])
    df = df.sort_values("date").set_index("date")
    r = df["change_pct"] / 100.0
    return r


def build_amv_state(r: pd.Series, bear_priority: bool = True,
                    init: str = "bull") -> pd.Series:
    """返回 'bull'/'bear' 状态的逐日序列（当日收盘判定，供次日应用）。"""
    r = r.astype(float)
    # 连续触发：两日累计 / 三日累计（精确复利）
    two = (1 + r).rolling(2).apply(np.prod, raw=True) - 1.0
    three = (1 + r).rolling(3).apply(np.prod, raw=True) - 1.0
    bull_trig = (r >= AMV_BULL) | (two >= AMV_BULL)
    bear_trig = (r <= AMV_BEAR) | (three <= AMV_BEAR)
    states = []
    st = init
    for b, be in zip(bull_trig.tolist(), bear_trig.tolist()):
        b = bool(b)
        be = bool(be)
        if be and b:
            st = "bear" if bear_priority else "bull"
        elif be:
            st = "bear"
        elif b:
            st = "bull"
        # 否则保持
        states.append(st)
    return pd.Series(states, index=r.index)


def amv_scale(state: pd.Series, dates: pd.DatetimeIndex,
              bear_scale: float) -> pd.Series:
    """状态序列对齐到回测 dates 并 shift(1)（次日生效），转成 scale 序列。"""
    s = pd.Series(1.0, index=dates)
    am = state.reindex(dates).ffill().fillna("bull")
    s[am == "bear"] = bear_scale
    # 次日生效：信号日收盘定 -> 下一天开始应用
    return s.shift(1).fillna(1.0)


def run_window(panel, pool, defensive, start, end, label, bear_scale,
               bear_priority=True, init="bull", amv_r=None):
    dates = panel.index
    start_i = int(dates.searchsorted(pd.Timestamp(start)))
    end_i = min(int(dates.searchsorted(pd.Timestamp(end))), len(dates) - 1)
    buy = sell = 0.001
    kw = dict(sticky=1.0, buy=buy, sell=sell, value_engine=True,
              **bl.LIVE_COMPOSITE)

    base = bl.backtest(panel, dates, start_i, end_i, pool, defensive, **kw)
    base_st = bl.stats(base["navs"])

    # 钩子等价性验证（仅主窗跑一次）
    verify = None
    if label == "main":
        all_one = bl.backtest(panel, dates, start_i, end_i, pool, defensive,
                              **kw, scale_series=pd.Series(1.0, index=dates))
        mx = float((base["navs"] - all_one["navs"]).abs().max())
        verify = mx

    rows = [dict(variant="A基线D3+P3", scale="1.0", nav=base["navs"],
                 st=base_st, trades=len(base["trades"]),
                 cash=base["cash_days"], r=base, amv=None)]

    if amv_r is not None:
        state = build_amv_state(amv_r, bear_priority=bear_priority, init=init)
        scale_ser = amv_scale(state, dates, bear_scale)
        r2 = bl.backtest(panel, dates, start_i, end_i, pool, defensive,
                         **kw, scale_series=scale_ser)
        st2 = bl.stats(r2["navs"])
        bear_days = int((scale_ser < 1.0).sum())
        trig_events = int((state != state.shift(1)).sum())
        rows.append(dict(variant=f"B{label}_AMV空头{'半仓' if bear_scale==0.5 else '清零'}",
                         scale=str(bear_scale), nav=r2["navs"], st=st2,
                         trades=len(r2["trades"]), cash=r2["cash_days"],
                         r=r2, amv=dict(bear_days=bear_days,
                                         trig_events=trig_events)))
    return rows, verify


def yearly_rows(rows):
    out = []
    for row in rows:
        yl = {y["year"]: y for y in bl.yearly(row["nav"])}
        out.append(dict(variant=row["variant"], yearly=yl))
    return out


def fmt_row(row, years):
    st = row["st"]
    cells = [row["variant"], f"{row['nav'].iloc[-1]:.4f}",
             f"{st['total']*100:+.2f}%", f"{st['ann']*100:+.2f}%",
             f"{st['mdd']*100:.2f}%",
             (f"{st['sharpe']:.2f}" if st["sharpe"] is not None else "-"),
             (f"{st['calmar']:.2f}" if st["calmar"] is not None else "-"),
             str(row["trades"]), str(row["cash"])]
    if row.get("amv"):
        cells += [str(row["amv"]["bear_days"]), str(row["amv"]["trig_events"])]
    return cells


def main():
    t0 = time.time()
    amv_r = load_amv()
    rot, deff, watch = bl.read_pools()
    defensive = deff[0] if deff else bl.DEFENSIVE

    # 双窗口
    results = {}
    for key, (csv, start, label) in {
        "main": (ADJ_2019, "2019-12-31", "main"),
        "long": (ADJ_2015, "2014-12-31", "long"),
    }.items():
        panel = bl.read_panel(csv)
        rows, verify = run_window(panel, rot, defensive, start, "2026-12-31",
                                  label, 0.5, amv_r=amv_r)
        # 极端对照（仅主窗额外跑清零版）
        if label == "main":
            rows2, _ = run_window(panel, rot, defensive, start, "2026-12-31",
                                  label, 0.0, amv_r=amv_r)
            rows = [rows[0], rows[1], rows2[1]]
        results[key] = dict(panel=panel, rows=rows, verify=verify)

    # ---- 敏感度（主窗）：初始态 / 冲突优先级 / 仅单日触发 ----
    panel = results["main"]["panel"]
    dates = panel.index
    start_i = int(dates.searchsorted(pd.Timestamp("2019-12-31")))
    end_i = min(int(dates.searchsorted(pd.Timestamp("2026-12-31"))),
                len(dates) - 1)
    buy = sell = 0.001
    kw = dict(sticky=1.0, buy=buy, sell=sell, value_engine=True,
              **bl.LIVE_COMPOSITE)
    sens = []
    for name, bp, init in [("初始空头", True, "bear"), ("多头优先", False, "bull"),
                           ("空头优先", True, "bull")]:
        state = build_amv_state(amv_r, bear_priority=bp, init=init)
        sc = amv_scale(state, dates, 0.5)
        rr = bl.backtest(panel, dates, start_i, end_i, rot, defensive,
                         **kw, scale_series=sc)
        st = bl.stats(rr["navs"])
        sens.append(dict(name=name, nav=rr["navs"], st=st,
                         bear=int((sc < 1.0).sum()),
                         trig=int((state != state.shift(1)).sum())))

    # ---- 输出 ----
    pd.set_option("display.width", 200)
    print("=" * 110)
    print("0AMV 官方真值 空头半仓大闸　vs　D3+P3 基线")
    print("=" * 110)
    for key, res in results.items():
        print(f"\n【{key} 窗口】 校验: maxdiff(None vs 全1.0) = {res['verify']}")
        hdr = (f"{'方案':<24}{'净值':>9}{'总收益':>10}{'年化':>9}{'MDD':>9}"
                f"{'夏普':>7}{'Calmar':>8}{'换仓':>6}{'持币日':>7}"
                f"{'空头日':>8}{'状态翻转':>8}")
        print(hdr)
        print("-" * len(hdr))
        for row in res["rows"]:
            print("  ".join(fmt_row(row, None)))
    print(f"\n【敏感度·主窗】 空头半仓 0.5")
    hdr = (f"{'变体':<16}{'净值':>9}{'总收益':>10}{'年化':>9}{'MDD':>9}"
            f"{'夏普':>7}{'Calmar':>8}{'空头日':>8}{'状态翻转':>8}")
    print(hdr)
    print("-" * len(hdr))
    for s in sens:
        st = s["st"]
        print(f"{s['name']:<16}{s['nav'].iloc[-1]:>9.4f}"
              f"{st['total']*100:>9.2f}%{st['ann']*100:>8.2f}%"
              f"{st['mdd']*100:>8.2f}%"
              f"{(f'{st['sharpe']:.2f}' if st['sharpe'] is not None else '-'):>7}"
              f"{(f'{st['calmar']:.2f}' if st['calmar'] is not None else '-'):>8}"
              f"{s['bear']:>8}{s['trig']:>8}")

    # ---- 逐年（主窗） ----
    print("\n【逐年收益·主窗】 A基线 / B空头半仓 / C空头清零")
    yrows = yearly_rows(results["main"]["rows"])
    yrs = sorted({y for row in yrows for y in row["yearly"]})
    print(f"{'方案':<24}" + "".join(f"{y:>10}" for y in yrs))
    for row in yrows:
        cells = []
        for y in yrs:
            v = row["yearly"].get(y)
            cells.append(f"{v['ret']*100:>9.1f}%" if v else f"{'-':>10}")
        print(f"{row['variant']:<24}" + "".join(cells))

    # 保存 JSON
    out = {}
    for key, res in results.items():
        out[key] = dict(
            verify=res["verify"],
            rows=[dict(variant=r["variant"], nav=round(float(r["nav"].iloc[-1]), 6),
                       total=round(r["st"]["total"], 6),
                       ann=round(r["st"]["ann"], 6),
                       mdd=round(r["st"]["mdd"], 6),
                       sharpe=r["st"]["sharpe"],
                       calmar=r["st"]["calmar"],
                       trades=r["trades"], cash=r["cash"],
                       amv=r.get("amv")) for r in res["rows"]])
    out["sens"] = [dict(name=s["name"], nav=round(float(s["nav"].iloc[-1]), 6),
                        total=round(s["st"]["total"], 6),
                        mdd=round(s["st"]["mdd"], 6),
                        calmar=s["st"]["calmar"], bear=s["bear"],
                        trig=s["trig"]) for s in sens]
    with open(os.path.join(HERE, "out", "amv_gate_results.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n详情已存 out/amv_gate_results.json  耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
