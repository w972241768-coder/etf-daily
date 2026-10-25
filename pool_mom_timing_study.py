"""池内平均动横择时（2026-10-24 晚）—— 修正前一轮的设计缺陷

前一轮（mom_timing_study.py）全部无效，根因：
  「头名的动横20」是**排名指标**，9 只里选最强的，天然偏高 ⇒
  度量的是「池内谁最强」，不是「市场好不好」⇒ 用它判断「该不该持有」无效
  （主窗「下穿 0」仅触发 19 次，择时等于没择时）。

本轮修正：用**池内等权平均动横20**（= 池内等权指数的 20 日涨幅）。
  这才是「市场整体好不好」的正确度量 —— 用户说的「所有都在下跌」正体现在这里。
  诊断依据：基线 MDD 段（2025-09~2025-12）内**头名**动横20 均值 +11.35%（52/54 天为正），
  但**池内平均**必然为负（否则不会创新低）。

信号设计（滞回带，避免抖动）：
  离场：池内平均动横20 下穿 exit_lvl
  入场：池内平均动横20 上穿 enter_lvl（enter > exit）
口径：绝对值（-5%/0%/5% × -10%/-5%）+ 滚动分位（20/30/50 分位 × 10/20 分位）
载体：基线 / D3 / P3；双窗口；单边万一；随机同频对照 + 邻域稳定性。
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
COST = 0.0001
BASE = dict(sticky=1.0, enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
            exit_lead=10.0, min_def_days=5, W=W, MA=MA, value_engine=True)
MINP = 250


def pool_avg_mom(panel, pool):
    """池内等权平均动横20（%）—— 「市场整体强弱」的正确度量"""
    cols = [c for c in pool if c in panel.columns]
    rets = panel[cols].pct_change()
    # 等权指数 20 日涨幅 = prod(1+r)[-20:] - 1
    eq = (1.0 + rets.mean(axis=1)).cumprod()
    return (eq / eq.shift(W) - 1.0) * 100.0


def timing_backtest(panel, dates, s_i, e_i, pool, defensive, *, sig_list,
                    enter_on, exit_on, roc_hold=None, **kw):
    state = "ROT"
    def_days = 0
    held = "CASH"
    timed_out = False
    nav = 1.0
    navs = [(dates[s_i], 1.0)]
    trades = []
    prev_eff = None
    ve_hold = (bl.value_engine_hold(panel, defensive, dates)
               if kw.get("value_engine", True) else None)
    entry_px = None
    cool_until = -1
    roc_until = -1
    held_log = []
    n_enter, n_exit = 0, 0

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
                if timed_out:
                    if bool(enter_on.iloc[i]):
                        timed_out = False
                        n_enter += 1
                        reason = "池内动横上穿 -> 重新入场"
                elif bool(exit_on.iloc[i]) and held != "CASH":
                    timed_out = True
                    n_exit += 1
                    reason = "池内动横跌破 -> 离场空仓"
        elif state == "DEF":
            def_days += 1
            if (dev_eff == dev_eff and dev_eff < kw.get("exit_dev", 9.0)) and \
               (sig["lead"] == sig["lead"] and sig["lead"] < kw.get("exit_lead", 10.0)) and \
               def_days >= kw.get("min_def_days", 5):
                state, def_days = "ROT", 0
                reason = "降温 -> 切回轮动"

        if state != "ROT":
            ve_ok = (not kw.get("value_engine", True)) or bool(ve_hold.loc[dates[i]])
            target = defensive if ve_ok else "CASH"
        else:
            target = eff_head
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
                cash_days=int(sum(1 for _d, t in held_log if t == "CASH")),
                n_enter=n_enter, n_exit=n_exit)


def stats(nav):
    st = bl.stats(nav)
    return dict(final=float(nav.iloc[-1]), total=st["total"] * 100,
                mdd=st["mdd"] * 100, sharpe=st["sharpe"], calmar=st["calmar"])


def main():
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    out = {"cases": [], "rnd": [], "nbr": [], "diag": []}

    for wname, fname, start in WINDOWS:
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        sig_all = [bl.compute_signal(panel, pool, i, W, MA) for i in range(len(dates))]
        pm = pool_avg_mom(panel, pool)
        roc_hold = bl.roc_head_hold(panel, pool, dates, W, 60, 0.95, MINP)
        print(f"\n{'#'*98}\n# 窗口 {wname}  {dates[s_i].date()} ~ {dates[e_i].date()}  单边万一\n{'#'*98}")

        # 诊断：基线 MDD 段内，池内平均动横 vs 头名动横
        b0 = bl.backtest(panel, dates, s_i, e_i, pool, defensive, buy=COST, sell=COST, **BASE)
        v = b0["navs"].to_numpy(float)
        peak = np.maximum.accumulate(v)
        dd = v / peak - 1.0
        i_m = int(np.argmin(dd))
        j0 = int(np.argmax(v[:i_m + 1]))
        s0, e0 = b0["navs"].index[j0], b0["navs"].index[i_m]
        mseg = (pm.index >= s0) & (pm.index <= e0)
        pm_seg = pm[mseg]
        print(f"\n【诊断】基线 MDD 段 {s0} → {e0}（{dd[i_m]*100:.2f}%）")
        print(f"  池内平均动横20: 均 {pm_seg.mean():+.2f}%  最低 {pm_seg.min():+.2f}%  "
              f"最高 {pm_seg.max():+.2f}%  为正当数 {(pm_seg>0).sum()}/{len(pm_seg)}")
        print(f"  全样本池内平均动横20: 均 {pm.mean():+.2f}%  中位 {pm.median():+.2f}%")
        out["diag"].append(dict(win=wname, seg=[s0, e0, float(dd[i_m]*100)],
                                pm_mean=float(pm_seg.mean()), pm_min=float(pm_seg.min()),
                                pm_max=float(pm_seg.max()), pm_pos=int((pm_seg>0).sum()),
                                pm_n=int(len(pm_seg)),
                                all_mean=float(pm.mean()), all_med=float(pm.median())))

        bases = {}
        for tag, kw in (("基线", dict()),
                        ("D3", dict(loss_cool=0.03, loss_days=5)),
                        ("P3", dict(loss_cool=0.03, loss_days=5, roc_q=0.95, roc_days=20,
                                    roc_n=60, roc_min_periods=MINP))):
            r = bl.backtest(panel, dates, s_i, e_i, pool, defensive,
                            buy=COST, sell=COST, **BASE, **kw)
            bases[tag] = stats(r["navs"])
            print(f"  {tag:<5} final={bases[tag]['final']:>8.4f} mdd={bases[tag]['mdd']:>7.2f}% "
                  f"calmar={bases[tag]['calmar']:>5.2f} sharpe={bases[tag]['sharpe']:>5.2f}")

        t20 = pm.expanding(min_periods=MINP).quantile(0.20)
        t30 = pm.expanding(min_periods=MINP).quantile(0.30)
        t50 = pm.expanding(min_periods=MINP).quantile(0.50)

        def band(en_lvl, ex_lvl):
            up = ((pm >= en_lvl) & (pm.shift(1) < en_lvl)).fillna(False).reindex(dates).fillna(False)
            dn = ((pm < ex_lvl) & (pm.shift(1) >= ex_lvl)).fillna(False).reindex(dates).fillna(False)
            return up, dn

        COMBOS = []
        for en in (-5.0, 0.0, 5.0):
            for ex in (-10.0, -5.0, 0.0):
                if ex >= en:
                    continue
                COMBOS.append((f"均动横 上穿{en:+.0f}%/下穿{ex:+.0f}%", band(en, ex)))
        for enq, ens in (("20分位", t20), ("30分位", t30), ("50分位", t50)):
            for exq, exs in (("10分位", t20 * 0.0 + pm.expanding(min_periods=MINP).quantile(0.10)),
                             ("20分位", t20)):
                COMBOS.append((f"均动横 上穿{enq}/下穿{exq}", band(ens, exs)))

        print(f"\n  {'组合':<30}{'载体':<6}{'净值':>9}{'MDD':>9}{'Calmar':>8}{'夏普':>7}"
              f"{'换仓':>6}{'空仓日':>7}{'入/出':>9}{'vs载体MDD':>11}{'vs载体收益':>12}")
        best = None
        for lab, (up, dn) in COMBOS:
            for carrier, ckw in (("基线", dict()),
                                 ("D3", dict(loss_cool=0.03, loss_days=5)),
                                 ("P3", dict(loss_cool=0.03, loss_days=5, roc_q=0.95,
                                             roc_days=20, roc_n=60, roc_min_periods=MINP))):
                r = timing_backtest(panel, dates, s_i, e_i, pool, defensive,
                                    sig_list=sig_all, enter_on=up, exit_on=dn,
                                    roc_hold=roc_hold, buy=COST, sell=COST, **BASE, **ckw)
                st = stats(r["navs"])
                cb = bases[carrier]
                rec = dict(win=wname, label=lab, carrier=carrier, final=st["final"],
                           mdd=st["mdd"], calmar=st["calmar"], sharpe=st["sharpe"],
                           trades=len(r["trades"]), cash_days=r["cash_days"],
                           n_enter=r["n_enter"], n_exit=r["n_exit"],
                           carrier_final=cb["final"], carrier_mdd=cb["mdd"],
                           carrier_calmar=cb["calmar"],
                           d_mdd=st["mdd"] - cb["mdd"],
                           d_total=st["total"] - cb["total"],
                           d_calmar=st["calmar"] - cb["calmar"])
                out["cases"].append(rec)
                if carrier == "P3":
                    print(f"  {lab:<30}{carrier:<6}{st['final']:>9.4f}{st['mdd']:>8.2f}%"
                          f"{st['calmar']:>8.2f}{st['sharpe']:>7.2f}{len(r['trades']):>6}"
                          f"{r['cash_days']:>7}{r['n_enter']:>4}/{r['n_exit']:<4}"
                          f"{rec['d_mdd']:>+10.2f}pp{rec['d_total']:>+11.2f}pp")
                    if best is None or st["calmar"] > best[1]["calmar"]:
                        best = (rec, st, up, dn)

        # 邻域 + 随机
        if best:
            rec, st, up, dn = best
            print(f"\n  【邻域稳定性】{rec['label']} on P3（相邻入场阈值）")
            for dl in (-5.0, 0.0, 5.0):
                m = (pm.index >= "1900-01-01")
                for lab0, (u0, d0) in COMBOS:
                    if lab0 == rec["label"]:
                        # 解析当前入场电平
                        pass
                # 直接用相邻电平重建
                en_cur = None
                for en in (-5.0, 0.0, 5.0):
                    for ex in (-10.0, -5.0, 0.0):
                        if ex < en and f"上穿{en:+.0f}%/下穿{ex:+.0f}%" in rec["label"]:
                            en_cur, ex_cur = en, ex
                if en_cur is None:
                    break
                en2 = en_cur + dl
                if not (-15.0 <= en2 <= 10.0):
                    continue
                u2, d2 = band(en2, ex_cur)
                r2 = timing_backtest(panel, dates, s_i, e_i, pool, defensive,
                                     sig_list=sig_all, enter_on=u2, exit_on=d2,
                                     roc_hold=roc_hold, buy=COST, sell=COST, **BASE,
                                     loss_cool=0.03, loss_days=5, roc_q=0.95, roc_days=20,
                                     roc_n=60, roc_min_periods=MINP)
                s2 = stats(r2["navs"])
                print(f"    入场 {en2:+.0f}%: final={s2['final']:>8.4f} mdd={s2['mdd']:>7.2f}% "
                      f"calmar={s2['calmar']:>5.2f} (入/出 {r2['n_enter']}/{r2['n_exit']})")
                out["nbr"].append(dict(win=wname, label=rec["label"], entry=en2,
                                       final=s2["final"], mdd=s2["mdd"], calmar=s2["calmar"]))

            n_trig = rec["n_enter"]
            if n_trig > 0:
                idx_pool = np.arange(s_i, e_i)
                arr = []
                for seed in range(200):
                    rng = np.random.default_rng(seed)
                    pick = rng.choice(idx_pool, size=min(n_trig, len(idx_pool)), replace=False)
                    rup = pd.Series(False, index=dates)
                    rup.iloc[pick] = True
                    r2 = timing_backtest(panel, dates, s_i, e_i, pool, defensive,
                                         sig_list=sig_all, enter_on=rup, exit_on=dn,
                                         roc_hold=roc_hold, buy=COST, sell=COST, **BASE,
                                         loss_cool=0.03, loss_days=5, roc_q=0.95, roc_days=20,
                                         roc_n=60, roc_min_periods=MINP)
                    arr.append(stats(r2["navs"])["mdd"])
                arr = np.array(arr)
                beat = float((arr > st["mdd"]).mean() * 100)
                print(f"\n  【随机同频对照】入场触发 {n_trig} 次  真实 mdd={st['mdd']:.2f}% "
                      f"随机中位={np.median(arr):.2f}% → 优于随机 {beat:.1f}% 的情形")
                out["rnd"].append(dict(win=wname, label=rec["label"], n_trig=n_trig,
                                       real_mdd=st["mdd"], rnd_med=float(np.median(arr)),
                                       beat_pct=beat, real_final=st["final"],
                                       real_calmar=st["calmar"]))

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

    with open(os.path.join(HERE, "out", "pool_mom_timing.json"), "w", encoding="utf-8") as f:
        json.dump(_clean(out), f, ensure_ascii=False, indent=1)
    print("\n写出: out/pool_mom_timing.json")


if __name__ == "__main__":
    main()
