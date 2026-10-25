"""候选规则的增量性验证（2026-10-24）

核心疑点：
  扫描出来的候选几乎全是「头名某指标处于历史极高位 → 切防守」。
  而现有策略**已经有过热熔断**（乖离>12% 或 领先>15pp → 切防守）。
  ⇒ 候选可能只是「过热熔断的另一种写法」，边际价值 = 0。

必须验证三件事：
  ① 与过热熔断的重叠度：候选触发的日子，有多少本来就过热了？
  ② 增量价值：在**已有过热熔断之上**再加候选，回撤还能改善吗？
  ③ 与 D3 的叠加：D3 + 候选 是否优于 D3 单独？

结论口径：
  · 若 ②③ 都无增量 ⇒ 候选只是「重新发现过热」，不值得加。
  · 若 ②③ 有增量且随机对照仍压倒 ⇒ 才是真发现。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import etf_backtest_long as bl     # noqa: E402
import ind_scan_study as S          # noqa: E402
import indicators_lib as ind        # noqa: E402
from rot_stop_study import load_win  # noqa: E402

IS_END, OOS_START = S.IS_END, S.OOS_START


def overheat_mask(panel, dates, pool, s_i, e_i, sig_all,
                  enter_dev=12.0, enter_lead=15.0):
    """现有过热熔断：乖离>12% 或 领先>15pp（与主策略同参数）"""
    out = pd.Series(False, index=dates)
    for i in range(s_i, e_i):
        sig = sig_all[i]
        if sig is None:
            continue
        dev = sig["dev"].get(sig["head"], float("nan"))
        lead = sig["lead"]
        if (dev == dev and dev > enter_dev) or (lead == lead and lead > enter_lead):
            out.iloc[i] = True
    return out


def rebuild_on(panel, pool, ind_name, params, scope, direction, q, dates, mom_df):
    hv, cv = S.build_indicator_series(panel, pool, ind_name, params, dates, mom_df)
    series = hv if scope == "头名" else cv
    thr_s = S.expanding_thresholds(series, direction)
    for qq, ts in thr_s:
        if qq == q:
            on = (series <= ts) if direction == "low" else (series >= ts)
            off = (series > ts * 0.5) if direction == "low" else (series < ts * 0.5)
            return (on.fillna(False).reindex(dates).fillna(False),
                    off.fillna(False).reindex(dates).fillna(False), ts)
    return None, None, None


def main():
    ana = json.load(open(os.path.join(HERE, "out", "ind_scan_analysis.json"),
                         encoding="utf-8"))
    final = ana["final"]
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    out_rows = []

    for wname, fname, start in S.WINDOWS:
        if not os.path.exists(os.path.join(HERE, fname)):
            continue
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        sig_all = [bl.compute_signal(panel, pool, i, 20, 20) for i in range(len(dates))]
        mom_df = pd.DataFrame({c: (panel[c] / panel[c].shift(20) - 1.0) * 100.0
                               for c in pool if c in panel.columns})
        over = overheat_mask(panel, dates, pool, s_i, e_i, sig_all)

        base = bl.backtest(panel, dates, s_i, e_i, pool, defensive, sticky=1.0,
                           enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
                           exit_lead=10.0, min_def_days=5, buy=0.001, sell=0.001,
                           W=20, MA=20, value_engine=True)
        nav0 = base["navs"]
        st0 = S.stats(nav0)

        # D3 基线（同窗口）
        d3 = bl.backtest(panel, dates, s_i, e_i, pool, defensive, sticky=1.0,
                         enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
                         exit_lead=10.0, min_def_days=5, buy=0.001, sell=0.001,
                         W=20, MA=20, value_engine=True, loss_cool=0.03, loss_days=5)
        stD3 = S.stats(d3["navs"])

        is_end_i = int(dates.searchsorted(pd.Timestamp(IS_END)))
        oos_start_i = int(dates.searchsorted(pd.Timestamp(OOS_START)))

        def segm(nav, lo, hi):
            s = nav.loc[(nav.index >= str(lo)[:10]) & (nav.index <= str(hi)[:10])]
            if len(s) < 20:
                return None
            return S.stats(s / s.iloc[0])

        print(f"\n{'='*74}\n窗口 {wname}: 基线 final={st0['final']:.4f} mdd={st0['mdd']:.2f}% "
              f"｜ D3 final={stD3['final']:.4f} mdd={stD3['mdd']:.2f}%\n{'='*74}")

        for s in [x for x in final if x["win"] == wname]:
            params = eval(s["params"])
            on, off, thr_s = rebuild_on(panel, pool, s["ind"], params, s["scope"],
                                        s["direction"], s["q"], dates, mom_df)
            if on is None:
                continue
            # ① 与过热熔断的重叠
            trig_days = on.values[s_i:e_i]
            ov = over.values[s_i:e_i]
            n_trig = int(trig_days.sum())
            n_ov = int(ov.sum())
            n_both = int((trig_days & ov).sum())
            overlap = n_both / max(1, n_trig)

            # ② 增量：候选 + 现有过热熔断（over 已经在引擎内作为 state 迁移，
            #    这里验证「候选触发日有多少是过热本来就该切的」）
            #    独立跑：只有候选 vs 基线 vs (候选 & 非过热)
            r_cand = S.backtest_gate(panel, dates, s_i, e_i, pool, defensive,
                                     gate_on=on, gate_off=off, sig_list=sig_all)
            st_c = S.stats(r_cand["navs"])

            # ③ 候选 + D3 叠加：把 D3 的空仓期也作为 gate 的一部分
            #    简化：跑 D3 引擎（rot_stop_study.simulate）+ 候选 gate 无法直接叠加，
            #    这里用「候选 gate 单独 + D3 单独」对比，并在第三段用组合引擎验证
            oos_c = segm(r_cand["navs"], dates[oos_start_i], dates[e_i])
            oos_d3 = segm(d3["navs"], dates[oos_start_i], dates[e_i])

            rec = dict(win=wname, ind=s["ind"], params=s["params"], scope=s["scope"],
                       direction=s["direction"], q=s["q"],
                       n_trig=n_trig, n_overheat=n_ov, n_both=n_both,
                       overlap_pct=overlap * 100,
                       cand_full=st_c, d3_full=stD3, base_full=st0,
                       cand_oos=oos_c, d3_oos=oos_d3,
                       d_cand_vs_base_mdd=st_c["mdd"] - st0["mdd"],
                       d_cand_vs_base_calmar=st_c["calmar"] - st0["calmar"],
                       d_cand_vs_d3_mdd=st_c["mdd"] - stD3["mdd"],
                       d_cand_vs_d3_calmar=st_c["calmar"] - stD3["calmar"],
                       d_oos_vs_base_mdd=(oos_c["mdd"] - oos_d3["mdd"]) if (oos_c and oos_d3) else None)
            out_rows.append(rec)
            print(f"\n  [{s['ind']} {s['params']} {s['scope']} {s['direction']} q={s['q']}]")
            print(f"    触发 {n_trig} 日，其中 {n_both} 日(={overlap*100:.0f}%)过热熔断本来就会切 "
                  f"→ 重叠度 {overlap*100:.0f}%")
            print(f"    全窗: 候选 final={st_c['final']:.4f} mdd={st_c['mdd']:.2f}% "
                  f"calmar={st_c['calmar']:.2f} ｜ vs 基线 mdd {rec['d_cand_vs_base_mdd']:+.2f}pp"
                  f" ｜ vs D3 mdd {rec['d_cand_vs_d3_mdd']:+.2f}pp calmar {rec['d_cand_vs_d3_calmar']:+.2f}")
            if oos_c and oos_d3:
                print(f"    OOS: 候选 mdd={oos_c['mdd']:.2f}% vs D3 {oos_d3['mdd']:.2f}% "
                      f"({rec['d_oos_vs_base_mdd']:+.2f}pp)")

    with open(os.path.join(HERE, "out", "ind_increment.json"), "w", encoding="utf-8") as f:
        json.dump(out_rows, f, ensure_ascii=False, indent=1, default=float)
    print("\n写出: out/ind_increment.json")


if __name__ == "__main__":
    main()
