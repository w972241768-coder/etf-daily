#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新旧防守策略 · 严格同口径消融对比。

三组只差防守端, 池子/参数/成本/窗口全部一致:
  A 旧基线 : 防守 512880 证券ETF国泰, 防守期满仓 (原策略B)
  B 只换标的: 防守 512040 价值ETF富国, 防守期满仓 (隔离"换标的"贡献)
  C 新策略 : 防守 512040 + 价值引擎 MA200×0.97买/×1.06卖, 不满则持币

★ 备选池口径: 三组统一剔除 512880 (它从防守位变成了 watch, 若计入会让
  备选池从 14 只变 15 只, 对比就不公平)。主选池本来就 9 只, 不受影响。
"""
import os
import sys
from collections import OrderedDict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import etf_backtest_long as E  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out", "defense_ablation.json")

SCEN = OrderedDict([
    ("A_old_512880_full", dict(defensive="512880", value_engine=False,
                               label="A 旧基线 · 512880 满仓")),
    ("B_512040_full",     dict(defensive="512040", value_engine=False,
                               label="B 只换标的 · 512040 满仓")),
    ("C_new_512040_ve",   dict(defensive="512040", value_engine=True,
                               label="C 新策略 · 512040 + 价值引擎")),
])

# 独立基准: 买入持有 512040 / 512880
BENCH_DEF = ["512040", "512880"]


def main():
    rot, deff, watch = E.read_pools()
    panel = E.read_panel(E.ADJ_CSV)
    dates = panel.index
    start_i = int(dates.searchsorted(pd.Timestamp("2019-12-31")))
    end_i = min(int(dates.searchsorted(pd.Timestamp("2026-12-31"))), len(dates) - 1)
    # ★ 同口径: 备选池剔除 512880 与防守位, 保证三组都是 14 只
    watch14 = [c for c in watch if c not in rot and c not in deff and c != "512880"]
    assert len(rot) == 9, rot
    assert len(watch14) == 14, watch14
    print(f"窗口 {dates[start_i].date()} = 1.000 -> {dates[end_i].date()}  "
          f"收益日 {end_i-start_i}  双边成本 0.2%")
    print(f"主选池 {len(rot)} 只 | 备选池 {len(watch14)} 只 (三组统一剔除 512880)\n")

    groups = OrderedDict([
        ("main", ("主选池(9只)", rot)),
        ("watch", ("备选池(14只)", watch14)),
        ("combo", ("参照组·主+备(23只)", rot + watch14)),
    ])

    out = dict(window=dict(start=str(dates[start_i].date()),
                           end=str(dates[end_i].date()),
                           days=end_i - start_i, cost=0.002,
                           note="备选池三组统一剔除512880以保证同口径"),
               scenarios={k: dict(label=v["label"], defensive=v["defensive"],
                                  value_engine=v["value_engine"]) for k, v in SCEN.items()},
               results={})

    for gname, (glabel, pool) in groups.items():
        print("=" * 92)
        print(f"【{glabel}】")
        print(f"{'场景':<30}{'期末净值':>10}{'区间收益':>11}{'年化':>10}"
              f"{'最大回撤':>10}{'夏普':>7}{'换仓':>7}{'防守日':>8}{'持币日':>8}")
        print("-" * 92)
        out["results"][gname] = dict(label=glabel, pool=pool, runs={})
        for sk, sv in SCEN.items():
            r = E.backtest(panel, dates, start_i, end_i, pool, sv["defensive"],
                           sticky=1.0, buy=0.001, sell=0.001,
                           value_engine=sv["value_engine"])
            st = E.stats(r["navs"])
            dfn = E.defensive_days(r, sv["defensive"])
            out["results"][gname]["runs"][sk] = dict(
                label=sv["label"], stat=st, trades_n=len(r["trades"]),
                stick_hits=r["stick_hits"], defense=dfn,
                held=E.held_summary(r),
                yearly=E.yearly(r["navs"]),
                nav={str(d): round(float(v), 6)
                     for d, v in r["navs"].items()},
                trades=r["trades"])
            sh = f"{st['sharpe']:.2f}" if st["sharpe"] is not None else "-"
            print(f"{sv['label']:<30}{r['navs'].iloc[-1]:>10.4f}"
                  f"{st['total']*100:>10.2f}%{st['ann']*100:>9.2f}%"
                  f"{st['mdd']*100:>9.2f}%{sh:>7}{len(r['trades']):>7}"
                  f"{dfn['days']:>8}{dfn['cash_days']:>8}")
        # 增量
        A = out["results"][gname]["runs"]["A_old_512880_full"]["stat"]
        for sk in ("B_512040_full", "C_new_512040_ve"):
            Bx = out["results"][gname]["runs"][sk]["stat"]
            print(f"    {sk:<26} vs A: 净值 {Bx['total']*100-A['total']*100:+7.2f}pp"
                  f"  回撤 {Bx['mdd']*100-A['mdd']*100:+6.2f}pp"
                  f"  夏普 {(Bx['sharpe'] or 0)-(A['sharpe'] or 0):+.2f}")
        # 防守标的买入持有基准
        for bc in BENCH_DEF:
            if bc in panel.columns:
                cv = E.bench_curve(panel, dates, start_i, end_i, bc)
                if len(cv):
                    bst = E.stats(cv)
                    out["results"][gname].setdefault("bench_def", {})[bc] = dict(
                        name=E.NAMES.get(bc, bc), stat=bst,
                        nav={d: round(float(v), 6) for d, v in cv.items()})
                    print(f"    [基准] 买入持有 {E.NAMES.get(bc,bc)}({bc}): "
                          f"净值 {cv.iloc[-1]:.4f} 收益 {bst['total']*100:+.2f}% "
                          f"回撤 {bst['mdd']*100:.2f}%")
        print()

    # 价值引擎自身统计: 512040 全窗口持仓天数 + 切换次数
    ve = E.value_engine_hold(panel, "512040", dates)
    ve_win = ve.loc[dates[start_i]:dates[end_i]]
    flips = int((ve_win.astype(int).diff().fillna(0) != 0).sum())
    out["value_engine"] = dict(
        code="512040", name=E.NAMES["512040"], ma=200, buy=0.97, sell=1.06,
        hold_days_in_window=int(ve_win.sum()), total_days_in_window=int(len(ve_win)),
        hold_pct=round(float(ve_win.sum()) / len(ve_win) * 100, 2),
        state_flips=flips,
        note="价值引擎在回测窗口内的自身持仓状态 (独立于策略状态机)")
    print("=" * 92)
    print(f"【价值引擎自身统计 · 512040】窗口内持仓 {int(ve_win.sum())}/{len(ve_win)} 日 "
          f"= {ve_win.sum()/len(ve_win)*100:.1f}%  状态翻转 {flips} 次")
    print("  (即: 不在策略里, 512040 自身有 "
          f"{ve_win.sum()/len(ve_win)*100:.1f}% 的时间处于'该买'状态)")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    import json
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[json] {OUT}")


if __name__ == "__main__":
    main()
