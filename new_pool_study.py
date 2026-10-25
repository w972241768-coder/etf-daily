"""新 ETF 池子三策略回测（2026-10-24）

用户指定池子：515880 通信ETF国泰 / 159516 半导体设备ETF国泰 /
588200 科创芯片ETF嘉实 / 159530 机器人ETF易方达 / 513100 纳指ETF
（512890 红利低波移出轮动池 —— 用户已为其设定独立策略）

口径：初始净值 1 · 单边万一（buy=sell=0.0001）· 防守位 512040

⚠️ 各标的上市时间差异大，长窗按「全部标的齐备」的同起点切，避免幸存者偏差：
  515880 2019-09-06 / 588200 2022-10-26 / 159516 2023-07-27 / 159530 2024-01-18
  ⇒ 全齐起点约 2024-01-18。同时报告「部分可用」的更长窗口作参考，并打印各年可用数。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import etf_backtest_long as bl      # noqa: E402
from rot_stop_study import load_win  # noqa: E402

COST = 0.0001
BASE = dict(sticky=1.0, enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
            exit_lead=10.0, min_def_days=5, W=20, MA=20, value_engine=True)
CSV = "tdx_adj_new_2019_2026.csv"
SCHEMES = [
    ("基线", dict()),
    ("D3", dict(loss_cool=0.03, loss_days=5)),
    ("P3", dict(loss_cool=0.03, loss_days=5, roc_q=0.95, roc_days=20,
                roc_n=60, roc_min_periods=250)),
]
NAMES = {"515880": "通信ETF国泰", "159516": "半导体设备ETF国泰",
         "588200": "科创芯片ETF嘉实", "159530": "机器人ETF易方达",
         "513100": "纳指ETF"}


def avail_by_year(panel, pool, dates, s_i):
    cols = [c for c in pool if c in panel.columns]
    out = {}
    for i in range(s_i, len(dates)):
        y = dates[i].year
        n = sum(1 for c in cols if not pd.isna(panel[c].iloc[i]))
        out.setdefault(y, []).append(n)
    return {y: int(np.mean(v)) for y, v in sorted(out.items())}


def yearly_ret(nav):
    df = pd.DataFrame({"v": nav.to_numpy(float)}, index=pd.to_datetime(nav.index))
    out = {}
    for y, g in df.groupby(df.index.year):
        v = g["v"].to_numpy()
        peak = np.maximum.accumulate(v)
        out[y] = dict(ret=(v[-1] / v[0] - 1) * 100,
                      mdd=float((v / peak - 1).min() * 100))
    return out


def main():
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    panel, dates, s_i, e_i = load_win(CSV, "2018-12-01")
    pool = [c for c in panel.columns if c in ("515880", "159516", "588200",
                                              "159530", "513100")]
    print(f"池子: {pool}")
    # 各标的起始日
    for c in pool:
        s = panel[c].dropna()
        print(f"  {c} {NAMES.get(c, c):16s} {s.index.min().date()} ~ {s.index.max().date()} "
              f"({len(s)} 根)")
    # 全齐起点
    first = max(panel[c].dropna().index.min() for c in pool)
    print(f"\n全部标的齐备起点: {first.date()}")
    full_i = int(dates.searchsorted(first))

    # 两个窗口：主窗(全齐起点) + 长窗(数据最早可用，标的动态)
    WINDOWS = [("主窗(全齐)", full_i), ("长窗(动态池)", s_i)]
    res = {"meta": dict(cost=COST, pool=pool,
                        starts={c: str(panel[c].dropna().index.min().date())
                                for c in pool}),
           "cases": [], "avail": []}

    for wname, ws in WINDOWS:
        use = [c for c in pool if c in panel.columns]
        av = avail_by_year(panel, use, dates, ws)
        nav_days = len(dates) - ws
        print(f"\n{'='*100}")
        print(f"【{wname}】{dates[ws].date()} ~ {dates[e_i].date()}  "
              f"({nav_days} 个交易日)  单边万一 · 初始净值 1")
        print(f"  各年可用标的数: " + "  ".join(f"{y}:{n}" for y, n in av.items()))
        print(f"{'='*100}")
        print(f"  {'方案':<6}{'净值':>10}{'区间收益':>12}{'年化':>9}{'MDD':>9}"
              f"{'Calmar':>8}{'夏普':>7}{'换仓':>6}{'空仓日':>7}{'持仓集中度TOP3':>20}")
        for sname, kw in SCHEMES:
            r = bl.backtest(panel, dates, ws, e_i, use, defensive,
                            buy=COST, sell=COST, **BASE, **kw)
            st = bl.stats(r["navs"])
            held = pd.Series([t for _d, t in r["held_log"]])
            held = held[held != "CASH"]
            conc = held.value_counts(normalize=True).head(3) * 100
            conc_txt = "/".join(f"{NAMES.get(c,c)[:4]}{v:.0f}%" for c, v in conc.items())
            print(f"  {sname:<6}{float(r['navs'].iloc[-1]):>10.4f}"
                  f"{st['total']*100:>11.2f}%{st['ann']*100:>8.2f}%"
                  f"{st['mdd']*100:>8.2f}%{st['calmar']:>8.2f}{st['sharpe']:>7.2f}"
                  f"{len(r['trades']):>6}"
                  f"{int(sum(1 for _d,t in r['held_log'] if t=='CASH')):>7}"
                  f"{conc_txt:>20}")
            res["cases"].append(dict(
                win=wname, scheme=sname, start=str(dates[ws].date()),
                end=str(dates[e_i].date()), nav_days=nav_days,
                final=float(r["navs"].iloc[-1]), total=st["total"] * 100,
                ann=st["ann"] * 100, mdd=st["mdd"] * 100, sharpe=st["sharpe"],
                calmar=st["calmar"], trades=len(r["trades"]),
                cash_days=int(sum(1 for _d, t in r["held_log"] if t == "CASH")),
                yearly=yearly_ret(r["navs"]),
                conc={c: float(v) for c, v in conc.items()}))
        res["avail"].append(dict(win=wname, avail=av))
        # 逐年
        print(f"\n    逐年收益（基线/D3/P3）：")
        rj = {}
        for sname, kw in SCHEMES:
            r = bl.backtest(panel, dates, ws, e_i, use, defensive,
                            buy=COST, sell=COST, **BASE, **kw)
            rj[sname] = yearly_ret(r["navs"])
        for y in sorted(rj["基线"].keys()):
            b = rj["基线"][y]
            d = rj["D3"].get(y, {})
            p = rj["P3"].get(y, {})
            print(f"      {y}: 收益 {b['ret']:+7.1f}%/{d.get('ret',0):+7.1f}%/"
                  f"{p.get('ret',0):+7.1f}%   MDD {b['mdd']:6.1f}%/{d.get('mdd',0):6.1f}%/"
                  f"{p.get('mdd',0):6.1f}%")

    def _clean(o):
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(v) for v in o]
        if isinstance(o, (np.floating, np.integer)):
            o = o.item()
        if isinstance(o, float) and o != o:
            return None
        return o

    with open(os.path.join(HERE, "out", "new_pool_compare.json"), "w", encoding="utf-8") as f:
        json.dump(_clean(res), f, ensure_ascii=False, indent=1)
    print("\n写出: out/new_pool_compare.json")


if __name__ == "__main__":
    main()
