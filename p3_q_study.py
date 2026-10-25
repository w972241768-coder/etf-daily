"""D3 / P3 机制澄清 + ROC60 分位数网格测试（2026-10-24）

用户理解（需澄清）：
  D3 = 买入后跌 3% 就止损，5 日后按动横20第一重入
  P3 = D3 止损时若 ROC60 处 95 分位，则空仓 20 日再按动横20第一重入

实际实现（关键差异）：
  D3 **不是价格止损**，而是「换仓时刻检查」：当策略本来就要换出头名时，
     若被换出的那笔相对其建仓价亏损 ≥3% ⇒ 不立刻买新头名，改为空仓 5 个交易日。
     ⇒ 触发点是「换仓」，不是「价格触及 -3%」。不换仓就永远不触发。
  P3 与 D3 **完全独立**，与亏损无关：只要「头名 ROC60 进入自身滚动 q 分位」
     ⇒ 空仓 roc_days 个交易日。不是「止损时再看 ROC60」。
     两者独立计时、独立解除；目标持仓优先级 D3 > P3。

本脚本：
  ① 打印实际触发记录，用数据证明上述差异（D3 触发日 vs P3 触发日重合度）
  ② ROC60 分位数 × 空仓天数网格：q ∈ {0.80,0.85,0.90,0.95,0.98} × days ∈ {10,20,30}
  ③ 邻域稳定性（围绕最优 q 的相邻阈值）
  ④ 随机同频对照（注入同等触发次数的随机序列，用可注入 roc_hold 的独立状态机）
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
WINDOWS = [("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
           ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05")]


def stats(nav):
    st = bl.stats(nav)
    return dict(final=float(nav.iloc[-1]), total=st["total"] * 100,
                mdd=st["mdd"] * 100, sharpe=st["sharpe"], calmar=st["calmar"])


def yearly_mdd(nav):
    df = pd.DataFrame({"v": nav.to_numpy(float)}, index=pd.to_datetime(nav.index))
    out = {}
    for y, g in df.groupby(df.index.year):
        v = g["v"].to_numpy()
        peak = np.maximum.accumulate(v)
        out[y] = float((v / peak - 1).min() * 100)
    return out


def gate_backtest(panel, dates, s_i, e_i, pool, defensive, *, sig_list,
                  roc_hold, roc_days, loss_cool=0.03, loss_days=5):
    """可注入 roc_hold 布尔序列的状态机（用于随机对照）。
    roc_hold.iloc[i] 为 True ⇒ 当日触发「空仓 roc_days 日」。
    与 bl.backtest(roc_q=...) 的差异仅在信号来源，其余逻辑一致。
    """
    state, def_days = "ROT", 0
    held, nav = "CASH", 1.0
    navs = [(dates[s_i], 1.0)]
    prev_eff = None
    ve_hold = bl.value_engine_hold(panel, defensive, dates)
    entry_px = None
    cool_until, roc_until = -1, -1
    for i in range(s_i, e_i):
        sig = sig_list[i]
        if sig is None:
            ret = 0.0
            if held != "CASH" and held in panel.columns:
                p0, p1 = panel[held].iloc[i], panel[held].iloc[i + 1]
                if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                    ret = p1 / p0 - 1.0
            nav *= (1.0 + ret)
            navs.append((dates[i + 1], nav))
            continue
        raw_head = sig["head"]
        eff_head = raw_head
        if prev_eff is not None and prev_eff not in sig["rank"]:
            prev_eff = None
        if prev_eff is not None and sig["lead"] == sig["lead"] and sig["lead"] < 1.0:
            eff_head = prev_eff
        dev_eff = sig["dev"].get(eff_head, float("nan"))
        if state == "ROT":
            over = ((dev_eff == dev_eff and dev_eff > 12.0) or
                    (sig["lead"] == sig["lead"] and sig["lead"] > 15.0))
            if over:
                state, def_days = "DEF", 0
            else:
                if i < cool_until:
                    pass
                elif (eff_head != held and held != "CASH" and entry_px
                      and held in panel.columns):
                    _px = panel[held].iloc[i]
                    if not pd.isna(_px) and _px / entry_px - 1.0 <= -loss_cool:
                        cool_until = min(i + loss_days, len(dates) - 1)
                if roc_hold is not None and held != "CASH" and i >= roc_until \
                        and bool(roc_hold.iloc[i]):
                    roc_until = min(i + roc_days, len(dates) - 1)
        elif state == "DEF":
            def_days += 1
            if (dev_eff == dev_eff and dev_eff < 9.0) and \
               (sig["lead"] == sig["lead"] and sig["lead"] < 10.0) and def_days >= 5:
                state, def_days = "ROT", 0

        if state != "ROT":
            ve_ok = bool(ve_hold.loc[dates[i]])
            target = defensive if ve_ok else "CASH"
        else:
            target = eff_head
            if i < cool_until:
                target = "CASH"
            elif i < roc_until:
                target = "CASH"
        if target != held:
            nav *= (1.0 - 2 * COST)
            entry_px = (float(panel[target].iloc[i])
                        if target in panel.columns and not pd.isna(panel[target].iloc[i])
                        else None)
        ret = 0.0
        if target in panel.columns:
            p0, p1 = panel[target].iloc[i], panel[target].iloc[i + 1]
            if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                ret = p1 / p0 - 1.0
        nav *= (1.0 + ret)
        held = target
        prev_eff = eff_head
        navs.append((dates[i + 1], nav))
    return pd.Series({d.strftime("%Y-%m-%d"): v for d, v in navs})


def main():
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    out = {"clarify": [], "grid": [], "nbr": [], "rnd": []}

    for wname, fname, start in WINDOWS:
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        sig_all = [bl.compute_signal(panel, pool, i, 20, 20) for i in range(len(dates))]
        print(f"\n{'#'*98}\n# 窗口 {wname}  {dates[s_i].date()} ~ {dates[e_i].date()}\n{'#'*98}")

        r_d3 = bl.backtest(panel, dates, s_i, e_i, pool, defensive,
                           buy=COST, sell=COST, loss_cool=0.03, loss_days=5, **BASE)
        r_p3 = bl.backtest(panel, dates, s_i, e_i, pool, defensive,
                           buy=COST, sell=COST, loss_cool=0.03, loss_days=5,
                           roc_q=0.95, roc_days=20, roc_n=60, roc_min_periods=250, **BASE)
        st_d3, st_p3 = stats(r_d3["navs"]), stats(r_p3["navs"])
        d3_dates = {c["date"] for c in r_d3["cools"]}
        p3_dates = {c["date"] for c in r_p3["roc_cools"]}
        both = d3_dates & p3_dates
        print(f"\n【① 机制澄清】")
        print(f"  D3 触发 {len(d3_dates)} 次 ｜ P3 触发 {len(p3_dates)} 次 ｜ "
              f"同日触发 {len(both)} 次")
        print(f"  ⇒ P3 触发中仅 {len(both)}/{len(p3_dates)} 与 D3 重合 ⇒ "
              f"**P3 绝大多数与「止损」无关，是独立触发器**")
        print(f"\n  D3 触发明细（被换出那笔的亏损）：")
        for c in r_d3["cools"][:5]:
            print(f"    {c['date']}  {c['name']:<14s} 该笔亏损 {c['ret']:+6.2f}%  → 空仓至 {c['until']}")
        print(f"  P3 触发明细（当时头名 ROC60）：")
        for c in r_p3["roc_cools"][:5]:
            print(f"    {c['date']}  {c['name']:<14s} ROC60={c['roc']:+6.2f}%   → 空仓至 {c['until']}")
        out["clarify"].append(dict(win=wname, n_d3=len(d3_dates), n_p3=len(p3_dates),
                                   n_both=len(both), d3=r_d3["cools"],
                                   p3=r_p3["roc_cools"], st_d3=st_d3, st_p3=st_p3))

        # ② 分位数 × 空仓天数网格
        print(f"\n【② ROC60 分位数 × 空仓天数 网格】（均叠加 D3，单边万一）")
        print(f"  基准 D3: {st_d3['final']:.4f} / {st_d3['mdd']:.2f}% / C{st_d3['calmar']:.2f}"
              f"   （现用 q=0.95+20日: {st_p3['final']:.4f} / {st_p3['mdd']:.2f}% / C{st_p3['calmar']:.2f}）")
        print(f"\n  {'q':>6}{'空仓日':>7}{'净值':>10}{'MDD':>9}{'Calmar':>8}{'夏普':>7}"
              f"{'触发':>6}{'空仓总日':>9}{'ΔMDD vs D3':>13}{'Δ净值 vs D3':>13}")
        best = None
        for q in (0.80, 0.85, 0.90, 0.95, 0.98):
            for days in (10, 20, 30):
                r = bl.backtest(panel, dates, s_i, e_i, pool, defensive,
                                buy=COST, sell=COST, loss_cool=0.03, loss_days=5,
                                roc_q=q, roc_days=days, roc_n=60, roc_min_periods=250,
                                **BASE)
                st = stats(r["navs"])
                dmd, dfn = st["mdd"] - st_d3["mdd"], st["final"] - st_d3["final"]
                mark = " ◄ 现用" if (abs(q - 0.95) < 1e-9 and days == 20) else ""
                print(f"  {q:>6.2f}{days:>7}{st['final']:>10.4f}{st['mdd']:>8.2f}%"
                      f"{st['calmar']:>8.2f}{st['sharpe']:>7.2f}{r['roc_n']:>6}"
                      f"{r['cash_days']:>9}{dmd:>+12.2f}pp{dfn:>+12.4f}{mark}")
                out["grid"].append(dict(win=wname, q=q, days=days, final=st["final"],
                                        mdd=st["mdd"], calmar=st["calmar"],
                                        sharpe=st["sharpe"], trig=r["roc_n"],
                                        cash_days=r["cash_days"], d_mdd=dmd,
                                        d_final=dfn, d3=dict(st_d3),
                                        yearly=yearly_mdd(r["navs"])))
                if best is None or st["calmar"] > best[1]["calmar"]:
                    best = (dict(q=q, days=days), st, r)

        # ③ 邻域稳定性
        if best:
            bq = float(best[0]["q"])
            bd = int(best[0]["days"])
            bst = best[1]
            br = best[2]
            print(f"\n【③ 邻域稳定性】最优 q={bq:.2f} 空仓{bd}日（Calmar {bst['calmar']:.2f}）→ 相邻 q：")
            for q in (bq - 0.05, bq - 0.02, bq + 0.02, bq + 0.05):
                if not (0.5 < q < 1.0):
                    continue
                r = bl.backtest(panel, dates, s_i, e_i, pool, defensive,
                                buy=COST, sell=COST, loss_cool=0.03, loss_days=5,
                                roc_q=round(q, 3), roc_days=bd, roc_n=60,
                                roc_min_periods=250, **BASE)
                st = stats(r["navs"])
                print(f"    q={q:.3f}: final={st['final']:>8.4f} mdd={st['mdd']:>7.2f}% "
                      f"calmar={st['calmar']:>5.2f} 触发{r['roc_n']:>4}")
                out["nbr"].append(dict(win=wname, q=round(q, 3), days=bd,
                                       final=st["final"], mdd=st["mdd"],
                                       calmar=st["calmar"], trig=r["roc_n"]))

            # ④ 随机同频对照（注入同等次数随机触发序列）
            n_trig = br["roc_n"]
            idx_pool = np.arange(s_i, e_i)
            arr = []
            for seed in range(200):
                rng = np.random.default_rng(seed)
                pick = rng.choice(idx_pool, size=min(n_trig, len(idx_pool)), replace=False)
                ron = pd.Series(False, index=dates)
                ron.iloc[pick] = True
                nav_r = gate_backtest(panel, dates, s_i, e_i, pool, defensive,
                                      sig_list=sig_all, roc_hold=ron, roc_days=bd)
                arr.append(stats(nav_r)["mdd"])
            arr = np.array(arr)
            beat = float((arr > bst["mdd"]).mean() * 100)
            print(f"\n【④ 随机同频对照】触发 {n_trig} 次 · 200 次随机")
            print(f"    真实 q={bq:.2f}: mdd={bst['mdd']:.2f}%  final={bst['final']:.4f}  "
                  f"calmar={bst['calmar']:.2f}")
            print(f"    随机 中位: mdd={np.median(arr):.2f}%  p05={np.percentile(arr,5):.2f}%"
                  f"  → 真实优于随机 {beat:.1f}% 的情形")
            out["rnd"].append(dict(win=wname, q=bq, days=bd, n_trig=n_trig,
                                   real_mdd=bst["mdd"], real_final=bst["final"],
                                   real_calmar=bst["calmar"],
                                   rnd_med=float(np.median(arr)),
                                   rnd_p05=float(np.percentile(arr, 5)),
                                   beat_pct=beat))

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

    with open(os.path.join(HERE, "out", "p3_q_grid.json"), "w", encoding="utf-8") as f:
        json.dump(_clean(out), f, ensure_ascii=False, indent=1)
    print("\n写出: out/p3_q_grid.json")


if __name__ == "__main__":
    main()
