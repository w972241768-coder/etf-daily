"""动横择时状态机回测（2026-10-24 晚）

诊断结论（mom_timing_diag.py）：
  · 头名动横20 **低位**时未来反而涨得更好（≤0 时 20 日胜 73%）⇒
    「动横低 → 空仓」方向是**反的**（会砍在启动前）。
  · 头名动横20 **上穿** 0%/5%/10% 后未来 20 日跑赢基准（上穿 0%：+2.25%/胜 68%
    vs 基准 +1.20%/55%）⇒ **「上穿才在场、跌破才离场」是正确的方向**。

本脚本实现状态机（只改「何时在场」，不改选股/排名）：
  离场：头名动横20 **跌破** exit_lvl（下穿）
  入场：头名动横20 **上穿** entry_lvl
  · entry_lvl > exit_lvl 形成滞回带（hysteresis），避免抖动
  · 三种口径：绝对值（0/5/10%）与滚动分位（自身 10/20/30 分位）
  · 叠加在 D3/P3 之上，检验增量
  · 双窗口 + 单边万一 + 随机同频对照 + 邻域稳定性

⚠️ 与已证伪方案的区别：本轮用**头名自身的时间序列位置**（时序），
   不是池内横截面广度（横截面）。前者是「强势品种自己强不强」，
   后者是「多少只强」——后者已三次证伪。
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

W, MA = 20, 20
WINDOWS = [("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
           ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05")]
COST = 0.0001                       # 单边万一
BASE = dict(sticky=1.0, enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
            exit_lead=10.0, min_def_days=5, W=W, MA=MA, value_engine=True)
MINP = 250


def head_mom_series(panel, pool, dates):
    mom_df = pd.DataFrame({c: (panel[c] / panel[c].shift(W) - 1.0) * 100.0
                           for c in pool if c in panel.columns})
    head_codes = mom_df.idxmax(axis=1)
    st = mom_df.stack(future_stack=True)
    hv = st.reindex(pd.MultiIndex.from_arrays(
        [head_codes.index, head_codes.values])).reset_index(level=1, drop=True)
    hv.index.name = mom_df.index.name
    return hv.reindex(mom_df.index)


def timing_backtest(panel, dates, s_i, e_i, pool, defensive, *, sig_list,
                    enter_on, exit_on, roc_hold=None, **kw):
    """头名动横择时状态机：enter_on 触发入场、exit_on 触发离场（滞回带由两阈值保证）。

    与 bl.backtest 的差别：在 ROT 状态下，若尚未入场（timed_out）则持币，
    enter_on 满足才买入当日头名；入场后 exit_on 满足则卖出持币。
    钩子全关（enter_on/exit_on 恒 True）时与 bl.backtest 逐位一致。
    """
    state = "ROT"
    def_days = 0
    held = "CASH"
    timed_out = False                  # True = 因择时空仓
    nav = 1.0
    navs = [(dates[s_i], 1.0)]
    trades = []
    prev_eff = None
    ve_hold = (bl.value_engine_hold(panel, defensive, dates) if kw.get("value_engine", True) else None)
    entry_px = None
    cool_until = -1
    roc_until = -1
    held_log = []

    for i in range(s_i, e_i):
        sig = sig_list[i]
        if sig is None:
            ret = 0.0
            if held != "CASH" and held in panel.columns:
                p0, p1 = panel[held].iloc[i], panel[held].iloc[i + 1]
                if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                    ret = p1 / p0 - 1.0
            nav *= (1.0 + ret)
            held_log.append((dates[i + 1], held))
            navs.append((dates[i + 1], nav))
            continue

        raw_head = sig["head"]
        eff_head = raw_head
        if prev_eff is not None and prev_eff not in sig["rank"]:
            prev_eff = None
        if kw.get("sticky", 1.0) > 0 and prev_eff is not None and sig["lead"] == sig["lead"] \
                and sig["lead"] < kw.get("sticky", 1.0):
            eff_head = prev_eff
        dev_eff = sig["dev"].get(eff_head, float("nan"))
        reason = ""

        if state == "ROT":
            over = ((dev_eff == dev_eff and dev_eff > kw.get("enter_dev", 12.0)) or
                    (sig["lead"] == sig["lead"] and sig["lead"] > kw.get("enter_lead", 15.0)))
            if over:
                state, def_days = "DEF", 0
                reason = "过热 -> 切防守"
            else:
                # ---- 动横择时：离场 / 入场 ----
                if timed_out:
                    if bool(enter_on.iloc[i]):
                        timed_out = False
                        reason = "动横择时: 上穿 -> 重新入场"
                elif bool(exit_on.iloc[i]) and held != "CASH":
                    timed_out = True
                    reason = "动横择时: 跌破 -> 离场空仓"
        elif state == "DEF":
            def_days += 1
            if (dev_eff == dev_eff and dev_eff < kw.get("exit_dev", 9.0)) and \
               (sig["lead"] == sig["lead"] and sig["lead"] < kw.get("exit_lead", 10.0)) and \
               def_days >= kw.get("min_def_days", 5):
                state, def_days = "ROT", 0
                reason = "降温 -> 切回轮动"

        # ---- 目标持仓 ----
        if state != "ROT":
            ve_ok = (not kw.get("value_engine", True)) or bool(ve_hold.loc[dates[i]])
            target = defensive if ve_ok else "CASH"
        else:
            target = eff_head
            # D3 / P3 冷却优先
            if kw.get("loss_cool") is not None and i < cool_until:
                target = "CASH"
            elif kw.get("roc_q") is not None and i < roc_until:
                target = "CASH"
            elif timed_out:
                target = "CASH"
            elif kw.get("loss_cool") is not None and target != held and held != "CASH" \
                    and entry_px and held in panel.columns:
                _px = panel[held].iloc[i]
                if not pd.isna(_px) and _px / entry_px - 1.0 <= -kw["loss_cool"]:
                    target = "CASH"
                    cool_until = min(i + kw.get("loss_days", 5), len(dates) - 1)
                    reason = "D3 冷却"
            # P3 触发（roc_hold 预计算传入，避免逐步重算）
            if kw.get("roc_q") is not None and target != "CASH" and held != "CASH" \
                    and i >= roc_until and roc_hold is not None and bool(roc_hold.iloc[i]):
                roc_until = min(i + kw.get("roc_days", 20), len(dates) - 1)
                target = "CASH"
                reason = "P3 追高止盈"

        if target != held:
            c = kw.get("buy", COST) + kw.get("sell", COST)
            nav *= (1.0 - c)
            trades.append(dict(date=str(dates[i].date()), frm=held, to=target, reason=reason))
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
        held_log.append((dates[i + 1], target))
        navs.append((dates[i + 1], nav))

    nd = pd.Series({d.strftime("%Y-%m-%d"): v for d, v in navs})
    return dict(navs=nd, trades=trades, held_log=held_log, final=float(nd.iloc[-1]),
                cash_days=int(sum(1 for _d, t in held_log if t == "CASH")))


def stats(nav):
    st = bl.stats(nav)
    return dict(final=float(nav.iloc[-1]), total=st["total"] * 100,
                mdd=st["mdd"] * 100, sharpe=st["sharpe"], calmar=st["calmar"])


def main():
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    out = {"cases": [], "rnd": [], "nbr": []}

    for wname, fname, start in WINDOWS:
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        sig_all = [bl.compute_signal(panel, pool, i, W, MA) for i in range(len(dates))]
        hv = head_mom_series(panel, pool, dates)
        roc_hold = bl.roc_head_hold(panel, pool, dates, W, 60, 0.95, MINP)
        print(f"\n{'#'*96}\n# 窗口 {wname}  {dates[s_i].date()} ~ {dates[e_i].date()}  "
              f"单边万一\n{'#'*96}")

        # 基准四方案
        bases = {}
        for tag, kw in (
            ("基线", dict()),
            ("D3", dict(loss_cool=0.03, loss_days=5)),
            ("P3", dict(loss_cool=0.03, loss_days=5, roc_q=0.95, roc_days=20,
                        roc_n=60, roc_min_periods=MINP)),
        ):
            r = bl.backtest(panel, dates, s_i, e_i, pool, defensive,
                            buy=COST, sell=COST, **BASE, **kw)
            bases[tag] = (r, stats(r["navs"]))
            print(f"  {tag:<6} final={bases[tag][1]['final']:>8.4f} "
                  f"mdd={bases[tag][1]['mdd']:>7.2f}% calmar={bases[tag][1]['calmar']:>5.2f} "
                  f"sharpe={bases[tag][1]['sharpe']:>5.2f} 换仓{len(r['trades']):>4}")

        # 动横择时组合（绝对值口径 + 滚动分位口径）
        thr10 = hv.expanding(min_periods=MINP).quantile(0.10)
        thr20 = hv.expanding(min_periods=MINP).quantile(0.20)
        thr30 = hv.expanding(min_periods=MINP).quantile(0.30)
        thr50 = hv.expanding(min_periods=MINP).quantile(0.50)

        def cross_up(level):
            return ((hv >= level) & (hv.shift(1) < level)).fillna(False).reindex(dates).fillna(False)

        def cross_dn(level):
            return ((hv < level) & (hv.shift(1) >= level)).fillna(False).reindex(dates).fillna(False)

        def band_cross(enter_lvl, exit_lvl):
            """滞回带：enter_lvl 上穿入场、exit_lvl 下穿离场"""
            up = ((hv >= enter_lvl) & (hv.shift(1) < enter_lvl)).fillna(False).reindex(dates).fillna(False)
            dn = ((hv < exit_lvl) & (hv.shift(1) >= exit_lvl)).fillna(False).reindex(dates).fillna(False)
            return up, dn

        COMBOS = []
        # 绝对值滞回带：入场 0%/5%/10% × 离场 −5%/0%
        for en in (0.0, 5.0, 10.0):
            for ex in (-5.0, 0.0):
                if ex >= en:
                    continue
                COMBOS.append((f"上穿{en:.0f}%/下穿{ex:.0f}%", band_cross(en, ex), "abs"))
        # 滚动分位滞回带：入场 30/50 分位 × 离场 10/20 分位
        for en_q, en_s in (("30分位", thr30), ("50分位", thr50)):
            for ex_q, ex_s in (("10分位", thr10), ("20分位", thr20)):
                COMBOS.append((f"上穿{en_q}/下穿{ex_q}", band_cross(en_s, ex_s), "pct"))

        print(f"\n  {'组合':<26}{'载体':<6}{'净值':>9}{'MDD':>9}{'Calmar':>8}{'夏普':>7}"
              f"{'换仓':>6}{'空仓日':>7}{'vs载体MDD':>11}")
        best = None
        for lab, (up, dn), kind in COMBOS:
            for carrier, ckw in (("基线", dict()), ("D3", dict(loss_cool=0.03, loss_days=5)),
                                 ("P3", dict(loss_cool=0.03, loss_days=5, roc_q=0.95,
                                             roc_days=20, roc_n=60, roc_min_periods=MINP))):
                r = timing_backtest(panel, dates, s_i, e_i, pool, defensive,
                                    sig_list=sig_all, enter_on=up, exit_on=dn, roc_hold=roc_hold,
                                    buy=COST, sell=COST, **BASE, **ckw)
                st = stats(r["navs"])
                cb = bases[carrier][1]
                dmd = st["mdd"] - cb["mdd"]
                dtot = st["total"] - cb["total"]
                if carrier == "P3":
                    print(f"  {lab:<26}{carrier:<6}{st['final']:>9.4f}{st['mdd']:>8.2f}%"
                          f"{st['calmar']:>8.2f}{st['sharpe']:>7.2f}{len(r['trades']):>6}"
                          f"{r['cash_days']:>7}{dmd:>+10.2f}pp")
                rec = dict(win=wname, label=lab, kind=kind, carrier=carrier,
                           final=st["final"], mdd=st["mdd"], calmar=st["calmar"],
                           sharpe=st["sharpe"], trades=len(r["trades"]),
                           cash_days=r["cash_days"],
                           carrier_final=cb["final"], carrier_mdd=cb["mdd"],
                           carrier_calmar=cb["calmar"],
                           d_vs_carrier_mdd=dmd, d_vs_carrier_total=dtot,
                           d_vs_carrier_calmar=st["calmar"] - cb["calmar"])
                out["cases"].append(rec)
                if best is None or (rec["carrier"] == "P3" and st["calmar"] > best[1]["calmar"]):
                    if rec["carrier"] == "P3":
                        best = (rec, st, up, dn)

        # 邻域稳定性：最佳组合的相邻阈值
        if best:
            rec, st, up, dn = best
            print(f"\n  【邻域稳定性】最佳组合 {rec['label']} on P3")
            for dl in (-5.0, 0.0, 5.0):
                en = 0.0 + dl if rec["kind"] == "abs" else None
                if en is None:
                    continue
                u2, d2 = band_cross(en, -5.0)
                r2 = timing_backtest(panel, dates, s_i, e_i, pool, defensive,
                                     sig_list=sig_all, enter_on=u2, exit_on=d2, roc_hold=roc_hold,
                                     buy=COST, sell=COST, **BASE,
                                     loss_cool=0.03, loss_days=5, roc_q=0.95,
                                     roc_days=20, roc_n=60, roc_min_periods=MINP)
                s2 = stats(r2["navs"])
                print(f"    入口 {en:+.0f}%: final={s2['final']:>8.4f} mdd={s2['mdd']:>7.2f}% "
                      f"calmar={s2['calmar']:>5.2f}")
                out["nbr"].append(dict(win=wname, label=rec["label"], entry=en,
                                       final=s2["final"], mdd=s2["mdd"], calmar=s2["calmar"]))

        # 随机同频对照：把「上穿」事件替换为等次数随机日
        if best:
            rec, st, up, dn = best
            n_trig = int(up.values[s_i:e_i].sum())
            idx_pool = np.arange(s_i, e_i)
            arr = []
            for seed in range(200):
                rng = np.random.default_rng(seed)
                pick = rng.choice(idx_pool, size=min(n_trig, len(idx_pool)), replace=False)
                rup = pd.Series(False, index=dates)
                rup.iloc[pick] = True
                r2 = timing_backtest(panel, dates, s_i, e_i, pool, defensive,
                                     sig_list=sig_all, enter_on=rup, exit_on=dn, roc_hold=roc_hold,
                                     buy=COST, sell=COST, **BASE,
                                     loss_cool=0.03, loss_days=5, roc_q=0.95,
                                     roc_days=20, roc_n=60, roc_min_periods=MINP)
                arr.append(stats(r2["navs"])["mdd"])
            arr = np.array(arr)
            beat = float((arr > st["mdd"]).mean() * 100)
            print(f"\n  【随机同频对照】n_trig={n_trig}  真实 mdd={st['mdd']:.2f}% "
                  f"随机中位={np.median(arr):.2f}% p05={np.percentile(arr,5):.2f}% "
                  f"→ 真实优于随机 {beat:.1f}% 的情形")
            out["rnd"].append(dict(win=wname, label=rec["label"], n_trig=n_trig,
                                   real_mdd=st["mdd"], rnd_med=float(np.median(arr)),
                                   rnd_p05=float(np.percentile(arr, 5)), beat_pct=beat,
                                   real_final=st["final"]))

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

    with open(os.path.join(HERE, "out", "mom_timing.json"), "w", encoding="utf-8") as f:
        json.dump(_clean(out), f, ensure_ascii=False, indent=1)
    print("\n写出: out/mom_timing.json")


if __name__ == "__main__":
    main()
