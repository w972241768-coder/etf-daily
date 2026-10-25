"""「ROC60 → 空仓N日」最终验证：逐年拆解 + 成本敏感性 + 信号归因

⚠️ **已归档（2026-10-24 晚）**：P3 逻辑已并入生产引擎
   `etf_backtest_long.backtest(roc_q=, roc_days=, roc_n=, roc_min_periods=)`。
   原 `roc_cash_zn_study.combo_backtest` 随牛马熊研究删除，本脚本不再维护。
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
from rot_stop_study import load_win  # noqa: E402

# P3 已并入生产引擎；combo_backtest 即 bl.backtest
combo_backtest = bl.backtest


def stats(nav):
    st = bl.stats(nav)
    return dict(final=float(nav.iloc[-1]), total=st["total"] * 100,
                mdd=st["mdd"] * 100, sharpe=st["sharpe"], calmar=st["calmar"],
                ann=st["ann"] * 100)


def roc60_series(panel, pool, dates, mom_df, n=60):
    """头名 ROC(n) 序列（P3 信号归因用）"""
    hv = bl.roc_head_hold.__wrapped__ if hasattr(bl.roc_head_hold, "__wrapped__") else None
    # 直接复用生产引擎的内部实现：头名动横 + ROC(n)
    roc_df = pd.DataFrame({c: (panel[c] / panel[c].shift(n) - 1.0) * 100.0
                           for c in pool if c in panel.columns})
    head_codes = mom_df.idxmax(axis=1)
    st = roc_df.stack(future_stack=True)
    out = st.reindex(pd.MultiIndex.from_arrays(
        [head_codes.index, head_codes.values])).reset_index(level=1, drop=True)
    out.index.name = roc_df.index.name
    return out.reindex(roc_df.index)

WINDOWS = [("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
           ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05")]


def yearly_mdd(nav):
    df = pd.DataFrame({"v": nav.to_numpy(float)}, index=pd.to_datetime(nav.index))
    out = {}
    for y, g in df.groupby(df.index.year):
        v = g["v"].to_numpy()
        peak = np.maximum.accumulate(v)
        out[y] = dict(mdd=float((v / peak - 1).min() * 100),
                      ret=(v[-1] / v[0] - 1) * 100)
    return out


def main():
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    out = {"cases": [], "cost": [], "attrib": []}

    for wname, fname, start in WINDOWS:
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
        r_d3 = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                              loss_cool=0.03, loss_days=5, sig_list=sig_all)
        st_base, st_d3 = stats(b0["navs"]), stats(r_d3["navs"])
        print(f"\n{'='*84}\n窗口 {wname}  基线 {st_base['final']:.4f}/{st_base['mdd']:.2f}%  "
              f"D3 {st_d3['final']:.4f}/{st_d3['mdd']:.2f}%")

        yb = yearly_mdd(b0["navs"])
        yd = yearly_mdd(r_d3["navs"])

        # ---- 两种形态：固定20日 / 等机会再入场（至85分位）----
        for tag, kw in (
            ("D3+ROC60空仓20日", dict(roc_on=on95, roc_off=off85, roc_action="cashN", roc_cash_days=20)),
            ("D3+ROC60空仓等机会", dict(roc_on=on95, roc_off=off85, roc_action="cash")),
        ):
            r = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                               sig_list=sig_all, loss_cool=0.03, loss_days=5, **kw)
            st = stats(r["navs"])
            yc = yearly_mdd(r["navs"])
            print(f"\n  【{tag}】final={st['final']:.4f} mdd={st['mdd']:.2f}% "
                  f"calmar={st['calmar']:.2f} sharpe={st['sharpe']:.2f} "
                  f"| vsD3 mdd{st['mdd']-st_d3['mdd']:+.2f} final{st['final']-st_d3['final']:+.4f}")
            print(f"    逐年 MDD: " + "  ".join(
                f"{y}:{yb[y]['mdd']:.0f}/{yd[y]['mdd']:.0f}/{yc[y]['mdd']:.0f}" for y in sorted(yc)))
            print(f"    逐年 收益: " + "  ".join(
                f"{y}:{yb[y]['ret']:+.0f}/{yd[y]['ret']:+.0f}/{yc[y]['ret']:+.0f}%" for y in sorted(yc)))
            n_better = sum(1 for y in yc if yc[y]["mdd"] > yd[y]["mdd"])
            print(f"    ⇒ 逐年 MDD 优于 D3 的年份: {n_better}/{len(yc)}")
            out["cases"].append(dict(win=wname, tag=tag, base=st_base, d3=st_d3, combo=st,
                                     d_vs_d3_mdd=st["mdd"] - st_d3["mdd"],
                                     d_vs_d3_final=st["final"] - st_d3["final"],
                                     d_vs_base_mdd=st["mdd"] - st_base["mdd"],
                                     d_vs_base_final=st["final"] - st_base["final"],
                                     d_vs_d3_calmar=st["calmar"] - st_d3["calmar"],
                                     yearly=dict(base=yb, d3=yd, combo=yc),
                                     n_yearly_better=n_better, n_years=len(yc),
                                     cash_days=r["cash_days"], roc_hits=r["roc_hits"]))

            # ---- 成本敏感性 ----
            for cost in (0.001, 0.002, 0.003):
                rb = bl.backtest(panel, dates, s_i, e_i, pool, defensive, sticky=1.0,
                                 enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
                                 exit_lead=10.0, min_def_days=5, buy=cost/2, sell=cost/2,
                                 W=20, MA=20, value_engine=True)
                rd = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                                    loss_cool=0.03, loss_days=5,
                                    buy=cost/2, sell=cost/2, sig_list=sig_all)
                rc = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                                    loss_cool=0.03, loss_days=5,
                                    buy=cost/2, sell=cost/2, sig_list=sig_all, **kw)
                sb, sd, sc = stats(rb["navs"]), stats(rd["navs"]), stats(rc["navs"])
                print(f"      成本{cost*100:.1f}%: 基线 {sb['final']:.4f}/{sb['mdd']:.2f}%  "
                      f"D3 {sd['final']:.4f}/{sd['mdd']:.2f}%  组合 {sc['final']:.4f}/{sc['mdd']:.2f}%")
                out["cost"].append(dict(win=wname, tag=tag, cost=cost,
                                        base=sb, d3=sd, combo=sc))

            # ---- 信号归因：被空仓躲过的那些天，头名后来怎么走 ----
            trig_idx = [i for i in range(s_i, e_i) if bool(on95.iloc[i])]
            fw5, fw10, fw20 = [], [], []
            for i in trig_idx:
                s = sig_all[i]
                if s is None:
                    continue
                h = s["head"]
                p0 = float(panel[h].iloc[i])
                for k, arr in ((5, fw5), (10, fw10), (20, fw20)):
                    p1 = float(panel[h].iloc[min(i + k, len(dates) - 1)])
                    if p0 != p0 and p0 == 0:
                        continue
                    if p1 == p1:
                        arr.append((p1 / p0 - 1) * 100)
            print(f"    信号归因（ROC60极高位触发 {len(trig_idx)} 次，空仓期间头名表现）:")
            for k, arr in ((5, fw5), (10, fw10), (20, fw20)):
                if arr:
                    print(f"      后{k:>2}日: n={len(arr):>3} 均{np.mean(arr):+6.2f}% "
                          f"中位{np.median(arr):+6.2f}% 胜{(np.array(arr)>0).mean()*100:3.0f}%")
            out["attrib"].append(dict(win=wname, tag=tag, n_trig=len(trig_idx),
                                      fwd5=dict(n=len(fw5), mean=float(np.mean(fw5)) if fw5 else None,
                                                med=float(np.median(fw5)) if fw5 else None,
                                                win=float((np.array(fw5)>0).mean()*100) if fw5 else None),
                                      fwd10=dict(n=len(fw10), mean=float(np.mean(fw10)) if fw10 else None,
                                                 med=float(np.median(fw10)) if fw10 else None,
                                                 win=float((np.array(fw10)>0).mean()*100) if fw10 else None),
                                      fwd20=dict(n=len(fw20), mean=float(np.mean(fw20)) if fw20 else None,
                                                 med=float(np.median(fw20)) if fw20 else None,
                                                 win=float((np.array(fw20)>0).mean()*100) if fw20 else None)))

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

    with open(os.path.join(HERE, "out", "roc_cash_final.json"), "w", encoding="utf-8") as f:
        json.dump(_clean(out), f, ensure_ascii=False, indent=1)
    print("\n写出: out/roc_cash_final.json")


if __name__ == "__main__":
    main()
