"""按「单边万一」(0.01%) 重算各策略盈利（2026-10-24）

背景：D3/P3 上线后换仓笔数增加，手续费影响需要重新评估。
原回测口径：buy=sell=0.001（单边 0.1% ⇒ 双边 0.2%）。
本脚本：buy=sell=0.0001（单边 0.01% ⇒ 双边 0.02%）。

对比四个方案：基线 / D3 / P3(=D3+ROC60空仓20日) / 基线+P3(无D3)
另报「零成本」与「0.1%/0.3% 单边」作敏感性参照。
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import etf_backtest_long as bl      # noqa: E402
from rot_stop_study import load_win  # noqa: E402

WINDOWS = [("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
           ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05")]
BASE = dict(sticky=1.0, enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
            exit_lead=10.0, min_def_days=5, W=20, MA=20, value_engine=True)

SCHEMES = [
    ("基线（无 D3/P3）", dict()),
    ("D3 单笔止损", dict(loss_cool=0.03, loss_days=5)),
    ("P3 追高止盈", dict(loss_cool=0.03, loss_days=5, roc_q=0.95,
                          roc_days=20, roc_n=60, roc_min_periods=250)),
    ("仅 P3 无 D3", dict(roc_q=0.95, roc_days=20, roc_n=60, roc_min_periods=250)),
]
COSTS = [0.0, 0.0001, 0.001, 0.003]        # 单边费率


def main():
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    rows = []
    for wname, fname, start in WINDOWS:
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        print(f"\n{'='*92}\n窗口 {wname}  {dates[s_i].date()} ~ {dates[e_i].date()}\n{'='*92}")
        print(f"{'方案':<18}{'单边费率':>9}{'净值':>10}{'区间收益':>11}{'MDD':>9}"
              f"{'Calmar':>8}{'夏普':>7}{'换仓笔数':>9}{'空仓日':>8}")
        for sname, kw in SCHEMES:
            for c in COSTS:
                r = bl.backtest(panel, dates, s_i, e_i, pool, defensive,
                                buy=c, sell=c, **BASE, **kw)
                st = bl.stats(r["navs"])
                ntr = len(r["trades"])
                cashd = int(sum(1 for _d, t in r["held_log"] if t == "CASH"))
                tag = f"{c*100:.2f}%" if c else "零成本"
                mark = " ◄" if abs(c - 0.0001) < 1e-9 else ""
                print(f"{sname:<18}{tag:>9}{float(r['navs'].iloc[-1]):>10.4f}"
                      f"{st['total']*100:>10.2f}%{st['mdd']*100:>8.2f}%"
                      f"{st['calmar']:>8.2f}{st['sharpe']:>7.2f}{ntr:>9}{cashd:>8}{mark}")
                if abs(c - 0.0001) < 1e-9:
                    rows.append(dict(win=wname, scheme=sname, cost=c,
                                     final=float(r["navs"].iloc[-1]),
                                     total=st["total"] * 100, mdd=st["mdd"] * 100,
                                     calmar=st["calmar"], sharpe=st["sharpe"],
                                     trades=ntr, cash_days=cashd))
        print()

    # 万一口径下的横向对比
    print("=" * 92)
    print("单边万一（0.01%）口径汇总")
    print("=" * 92)
    for wname, _f, _s in WINDOWS:
        rs = [r for r in rows if r["win"] == wname]
        if not rs:
            continue
        base = rs[0]
        print(f"\n【{wname}】")
        print(f"{'方案':<18}{'净值':>10}{'收益':>11}{'MDD':>9}{'Calmar':>8}"
              f"{'换仓':>7}{'vs基线收益':>12}{'vs基线MDD':>12}")
        for r in rs:
            print(f"{r['scheme']:<18}{r['final']:>10.4f}{r['total']:>10.2f}%"
                  f"{r['mdd']:>8.2f}%{r['calmar']:>8.2f}{r['trades']:>7}"
                  f"{r['total']-base['total']:>+11.2f}pp{r['mdd']-base['mdd']:>+11.2f}pp")

    import json
    with open(os.path.join(HERE, "out", "cost_compare.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("\n写出: out/cost_compare.json")


if __name__ == "__main__":
    main()
