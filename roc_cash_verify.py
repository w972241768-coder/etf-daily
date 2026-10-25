"""「ROC60 → 空仓 N 日」的严格验证（2026-10-24）

⚠️ **已归档（2026-10-24 晚）**：P3 逻辑已并入生产引擎
   `etf_backtest_long.backtest(roc_q=, roc_days=, roc_n=, roc_min_periods=)`，
   日报「P3 追高止盈」参考卡即由该参数驱动。原 `roc_cash_zn_study.combo_backtest`
   随牛马熊研究一并删除，本脚本不再维护；结论已固化在 MEMORY.md。
   如需复现：`for N in (5,10,15,20,25,30): bl.backtest(..., loss_cool=0.03,
   loss_days=5, roc_q=0.95, roc_days=N)` 即可。

发现：ROC60 触发 → 空仓 N 日，MDD 随 N 增大而成片改善：
  主窗 5日 −22.35% / 10日 −18.51% / 20日 −16.62%
  长窗 5日 −22.35% / 10日 −19.22% / 20日 −19.22%
  且长窗 20 日 final 11.1659 反而高于 D3 的 9.7555。

⚠️ 但这可能只是「少交易」的副产品：空仓越久，暴露越少、换手成本越少。
必须按本项目方法论铁律做两个对照：
  ① **随机同频对照**：同样次数、同样空仓天数的随机触发 → 真实是否显著优于随机；
  ② **固定持有 N 日对照**：完全不看指标，每 N 个交易日强制空仓一次 → 收益是否接近。
     若固定持有版能达到接近的效果，说明增益来自「节奏」而非「指标信号」。
③ 邻域稳定性：N = 5/10/15/20/25/30 是否成片。
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
import indicators_lib as ind        # noqa: E402
from rot_stop_study import load_win  # noqa: E402

# 2026-10-24：P3 逻辑已并入生产引擎 etf_backtest_long.backtest(roc_q=...)，
# 原 roc_cash_zn_study.combo_backtest 已随牛马熊研究删除，此处改调生产引擎。
combo_backtest = bl.backtest


def stats(nav):
    st = bl.stats(nav)
    return dict(final=float(nav.iloc[-1]), total=st["total"] * 100,
                mdd=st["mdd"] * 100, sharpe=st["sharpe"], calmar=st["calmar"],
                ann=st["ann"] * 100)

WINDOWS = [("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
           ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05")]


def main():
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    out = {"cases": [], "rnd": [], "fixed": [], "nbr": []}

    for wname, fname, start in WINDOWS:
        if not os.path.exists(os.path.join(HERE, fname)):
            continue
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        sig_all = [bl.compute_signal(panel, pool, i, 20, 20) for i in range(len(dates))]
        mom_df = pd.DataFrame({c: (panel[c] / panel[c].shift(20) - 1.0) * 100.0
                               for c in pool if c in panel.columns})
        hv = roc60_series(panel, pool, dates, mom_df, 60)
        thr95 = hv.expanding(min_periods=250).quantile(0.95)
        thr85 = hv.expanding(min_periods=250).quantile(0.85)
        on95 = (hv >= thr95).fillna(False).reindex(dates).fillna(False)
        off85 = (hv < thr85).fillna(False).reindex(dates).fillna(False)

        b0 = bl.backtest(panel, dates, s_i, e_i, pool, defensive, sticky=1.0,
                         enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
                         exit_lead=10.0, min_def_days=5, buy=0.001, sell=0.001,
                         W=20, MA=20, value_engine=True)
        st_base = stats(b0["navs"])
        r_d3 = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                              loss_cool=0.03, loss_days=5, sig_list=sig_all)
        st_d3 = stats(r_d3["navs"])
        print(f"\n{'='*80}\n窗口 {wname}  基线 {st_base['final']:.4f}/{st_base['mdd']:.2f}%  "
              f"D3 {st_d3['final']:.4f}/{st_d3['mdd']:.2f}%")

        # ---------- ① 邻域稳定性：N = 5/10/15/20/25/30 ----------
        print("\n  【邻域稳定性 · ROC60触发→空仓N日】")
        for N in (5, 10, 15, 20, 25, 30):
            r = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                               sig_list=sig_all, loss_cool=0.03, loss_days=5,
                               roc_on=on95, roc_off=off85, roc_action="cashN",
                               roc_cash_days=N)
            st = stats(r["navs"])
            print(f"    N={N:>2}日: final={st['final']:>7.4f} mdd={st['mdd']:>7.2f}% "
                  f"calmar={st['calmar']:>5.2f} sharpe={st['sharpe']:>5.2f} "
                  f"| vsD3 mdd{st['mdd']-st_d3['mdd']:>+6.2f} final{st['final']-st_d3['final']:>+7.4f} "
                  f"| 触发{r['roc_hits']:>3} 空仓日{r['cash_days']:>4}")
            out["nbr"].append(dict(win=wname, N=N, final=st["final"], mdd=st["mdd"],
                                   calmar=st["calmar"], sharpe=st["sharpe"],
                                   d_vs_d3_mdd=st["mdd"] - st_d3["mdd"],
                                   d_vs_d3_final=st["final"] - st_d3["final"],
                                   roc_hits=r["roc_hits"], cash_days=r["cash_days"]))

        # ---------- ② 随机同频对照（含总空仓日数对齐） ----------
        print("\n  【随机同频对照】")
        for N in (10, 20):
            n_trig = int(on95.values[s_i:e_i].sum())
            idx_pool = np.arange(s_i, e_i)
            # 随机版：同样触发次数、同样空仓 N 日
            arr = []
            for seed in range(200):
                rng = np.random.default_rng(seed)
                pick = rng.choice(idx_pool, size=min(n_trig, len(idx_pool)), replace=False)
                ron = pd.Series(False, index=dates)
                ron.iloc[pick] = True
                rr = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                                    sig_list=sig_all, loss_cool=0.03, loss_days=5,
                                    roc_on=ron, roc_off=off85, roc_action="cashN",
                                    roc_cash_days=N)
                arr.append(stats(rr["navs"])["mdd"])
            arr = np.array(arr)
            r_real = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                                    sig_list=sig_all, loss_cool=0.03, loss_days=5,
                                    roc_on=on95, roc_off=off85, roc_action="cashN",
                                    roc_cash_days=N)
            st_real = stats(r_real["navs"])
            beat = float((arr > st_real["mdd"]).mean() * 100)
            # 真实 vs 随机的 final 分位
            arrf = []
            for seed in range(200):
                rng = np.random.default_rng(seed)
                pick = rng.choice(idx_pool, size=min(n_trig, len(idx_pool)), replace=False)
                ron = pd.Series(False, index=dates)
                ron.iloc[pick] = True
                rr = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                                    sig_list=sig_all, loss_cool=0.03, loss_days=5,
                                    roc_on=ron, roc_off=off85, roc_action="cashN",
                                    roc_cash_days=N)
                arrf.append(stats(rr["navs"])["final"])
            arrf = np.array(arrf)
            beatf = float((arrf < st_real["final"]).mean() * 100)
            print(f"    N={N}: 真实 mdd={st_real['mdd']:>7.2f}% 随机中位{np.median(arr):>7.2f}% "
                  f"→ 优于随机 {beat:>5.1f}% 的情形 ｜ final {st_real['final']:>7.4f} "
                  f"随机中位{np.median(arrf):>7.4f} → 优于随机 {beatf:>5.1f}%")
            out["rnd"].append(dict(win=wname, N=N, n_trig=n_trig,
                                   real_mdd=st_real["mdd"], rnd_med=float(np.median(arr)),
                                   rnd_p05=float(np.percentile(arr, 5)),
                                   beat_pct=beat, real_final=st_real["final"],
                                   rnd_final_med=float(np.median(arrf)),
                                   beat_final_pct=beatf))

        # ---------- ③ 固定持有 N 日对照（不看指标，按节奏强制空仓） ----------
        # 做法：每隔 N 个交易日，强制空仓 roc_cash_days 天（完全不看指标）
        print("\n  【固定持有N日对照（不看指标，纯节奏）】")
        for hold, cash in ((20, 20), (10, 10)):
            arr_dates = pd.Series(False, index=dates)
            k = hold
            while k < e_i:
                for j in range(min(cash, e_i - k)):
                    arr_dates.iloc[k + j] = True
                k += hold + cash
            r = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                               sig_list=sig_all, loss_cool=0.03, loss_days=5,
                               roc_on=arr_dates, roc_off=off85, roc_action="cashN",
                               roc_cash_days=cash)
            st = stats(r["navs"])
            print(f"    每{hold}日强制空仓{cash}日: final={st['final']:>7.4f} "
                  f"mdd={st['mdd']:>7.2f}% calmar={st['calmar']:>5.2f} 空仓日{r['cash_days']:>4}")
            out["fixed"].append(dict(win=wname, hold=hold, cash=cash, final=st["final"],
                                     mdd=st["mdd"], calmar=st["calmar"],
                                     cash_days=r["cash_days"]))

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

    with open(os.path.join(HERE, "out", "roc_cash_verify.json"), "w", encoding="utf-8") as f:
        json.dump(_clean(out), f, ensure_ascii=False, indent=1)
    print("\n写出: out/roc_cash_verify.json")


if __name__ == "__main__":
    main()
