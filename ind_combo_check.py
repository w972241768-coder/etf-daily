"""候选 × D3 叠加验证（2026-10-24）

已确认的事实：
  · 候选（尤其 RSI(6) 头名 q=90）相对**基线**是真实增量：MDD −23.38%→−16.30%、Calmar 1.12→1.86；
    与过热熔断重叠度仅 29%（不是过热熔断的重复表达）。
  · 但候选相对 **D3** 反而略差（OOS 段：候选 −16.02% vs D3 −16.18%，实质持平），
    且 long 窗口上候选 final 全面跑输 D3（7.96/7.51/7.37 vs D3 9.7555）。
  ⇒ 两者很可能在**捕捉同一批回撤段**，真正的问题是「叠加后是否更好」。

本脚本做三件事：
  ① 在 rot_stop_study.simulate（支持 D3）上叠加指标 gate → 需要给引擎加 gate 支持；
     为避免改坏既有引擎，这里在 simulate 之上做「候选 gate 作为额外 D3 式冷却」的组合实现。
  ② 双窗口 + 逐年 MDD 拆解 + 随机同频对照（对照 = 同样触发次数的随机 gate，叠加在 D3 之上）。
  ③ 成本敏感性（0.1% / 0.2% / 0.3% 双边）。
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
import rot_stop_study as rs         # noqa: E402
from rot_stop_study import load_win  # noqa: E402

IS_END, OOS_START = S.IS_END, S.OOS_START


def combo_backtest(panel, dates, s_i, e_i, pool, defensive, *,
                   gate_on=None, gate_off=None, loss_cool=None, loss_days=0,
                   sticky=1.0, enter_dev=12.0, enter_lead=15.0,
                   exit_dev=9.0, exit_lead=10.0, min_def_days=5,
                   buy=0.001, sell=0.001, value_engine=True, sig_list=None):
    """D3 + 指标 gate 组合引擎。

    逻辑：在 rs.simulate 的基础上，把「指标 gate 触发」也作为一种「切防守 + 冷却」，
    冷却结束需满足 gate 条件解除才回轮动。与 D3 的冷却独立计数。
    钩子全关（gate_on=None, loss_cool=None）时必须与 bl.backtest() 逐位一致。
    """
    state = "ROT"
    def_days = 0
    held = "CASH"
    nav = 1.0
    navs = [(dates[s_i], 1.0)]
    trades = []
    prev_eff = None
    held_log, head_log = [], []
    ve_hold = (bl.value_engine_hold(panel, defensive, dates) if value_engine else None)
    cool_until = -1
    gate_until = -1          # 指标 gate 冷却到期
    gate_on_flag = False
    entry_px = None
    peak_px = None
    held_days = 0
    prev_raw_head = None
    closed_rets = []
    cool_hits = 0
    gate_hits = 0

    for i in range(s_i, e_i):
        sig = (sig_list[i] if sig_list is not None
               else bl.compute_signal(panel, pool, i, 20, 20))
        if sig is None:
            ret = 0.0
            if held != "CASH" and held in panel.columns:
                p0, p1 = panel[held].iloc[i], panel[held].iloc[i + 1]
                if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                    ret = p1 / p0 - 1.0
            nav *= (1.0 + ret)
            held_log.append((dates[i + 1], held))
            head_log.append((dates[i + 1], None))
            navs.append((dates[i + 1], nav))
            continue

        raw_head = sig["head"]
        eff_head = raw_head
        tied = False
        if prev_eff is not None and prev_eff not in sig["rank"]:
            prev_eff = None
        if sticky > 0 and prev_eff is not None and sig["lead"] == sig["lead"] \
                and sig["lead"] < sticky:
            eff_head = prev_eff
            tied = True
        m_head = sig["m"][raw_head]
        dev_eff = sig["dev"].get(eff_head, float("nan"))
        n_pos = sum(1 for v in sig["m"].values() if v > 0)
        n_list = len(sig["m"])

        reason = ""
        cooled_now = False
        if state == "ROT":
            over = ((dev_eff == dev_eff and dev_eff > enter_dev) or
                    (sig["lead"] == sig["lead"] and sig["lead"] > enter_lead))
            if over:
                state, def_days = "DEF", 0
                reason = "过热 -> 切防守"
            else:
                # —— D3：本笔亏超阈值 → 空仓冷却 ——
                if loss_cool is not None and i < cool_until:
                    pass                      # 冷却中（下面统一处理 target）
                elif (loss_cool is not None and eff_head != held and held != "CASH"
                        and entry_px and held in panel.columns):
                    _px = float(panel[held].iloc[i])
                    if _px == _px and _px / entry_px - 1.0 <= -loss_cool:
                        cool_until = min(i + loss_days, len(dates) - 1)
                        cool_hits += 1
                        cooled_now = True
                        reason = f"本笔亏超 {loss_cool*100:.0f}% → 空仓冷却 {loss_days} 日"
                # —— 指标 gate 触发 → 空仓冷却 ——
                if gate_on is not None and bool(gate_on.iloc[i]) and i >= gate_until \
                        and held != "CASH":
                    gate_until = min(i + max(1, loss_days if loss_days else 5),
                                     len(dates) - 1)
                    gate_hits += 1
                    reason = f"指标 gate 触发 → 空仓冷却"
        elif state == "DEF":
            def_days += 1
            # 解除条件：过热已降 + 冷却期满
            cool_done = (i >= cool_until) and (i >= gate_until)
            if cool_done and (dev_eff == dev_eff and dev_eff < exit_dev) and \
               (sig["lead"] == sig["lead"] and sig["lead"] < exit_lead) and \
               def_days >= min_def_days:
                state, def_days = "ROT", 0
                reason = "降温 + 冷却期满 -> 回轮动"

        # ---------------- 目标持仓 ----------------
        if state == "ROT":
            target = eff_head
            note = ""
            if loss_cool is not None and i < cool_until:
                target = "CASH"
                note = f" | D3 冷却中(至 {dates[cool_until].date()})"
                cool_hits += 0
            elif gate_on is not None and i < gate_until:
                target = "CASH"
                note = f" | 指标 gate 冷却中(至 {dates[gate_until].date()})"
            elif (loss_cool is not None and cooled_now):
                target = "CASH"
        else:
            ve_ok = (not value_engine) or bool(ve_hold.loc[dates[i]])
            target = defensive if ve_ok else "CASH"
            note = ""

        switched = (target != held)
        if switched:
            nav *= (1.0 - (buy + sell))
            trades.append(dict(date=str(dates[i].date()), frm=held, to=target,
                               state=state, reason=reason + note,
                               m_head=round(m_head, 2), nav=round(nav, 6)))
            if (held != "CASH" and held != defensive and entry_px
                    and held in panel.columns):
                _c_ = float(panel[held].iloc[i])
                if _c_ == _c_:
                    closed_rets.append(_c_ / entry_px - 1.0)
                    closed_rets = closed_rets[-4:]
            entry_px = (float(panel[target].iloc[i])
                        if target in panel.columns and not pd.isna(panel[target].iloc[i])
                        else None)
            peak_px = entry_px

        ret = 0.0
        if target in panel.columns:
            p0, p1 = panel[target].iloc[i], panel[target].iloc[i + 1]
            if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                ret = p1 / p0 - 1.0
        nav *= (1.0 + ret)
        held = target
        held_days = 0 if switched else held_days + 1
        if held != "CASH" and held in panel.columns:
            px = float(panel[held].iloc[i + 1])
            if px == px:
                peak_px = px if peak_px is None or px > peak_px else peak_px
        prev_eff = eff_head
        prev_raw_head = raw_head
        held_log.append((dates[i + 1], target))
        head_log.append((dates[i + 1], eff_head))
        navs.append((dates[i + 1], nav))

    nd = pd.Series({d.strftime("%Y-%m-%d"): v for d, v in navs})
    return dict(navs=nd, trades=trades, held_log=held_log, head_log=head_log,
                final=float(nd.iloc[-1]), cool_hits=cool_hits, gate_hits=gate_hits,
                cash_days=int(sum(1 for _d, t in held_log if t == "CASH")))


def rebuild_on(panel, pool, ind_name, params, scope, direction, q, dates, mom_df):
    hv, cv = S.build_indicator_series(panel, pool, ind_name, params, dates, mom_df)
    series = hv if scope == "头名" else cv
    for qq, ts in S.expanding_thresholds(series, direction):
        if qq == q:
            on = (series <= ts) if direction == "low" else (series >= ts)
            off = (series > ts * 0.5) if direction == "low" else (series < ts * 0.5)
            return (on.fillna(False).reindex(dates).fillna(False),
                    off.fillna(False).reindex(dates).fillna(False))
    return None, None


def yearly_mdd(nav):
    df = pd.DataFrame({"v": nav.to_numpy(float)}, index=pd.to_datetime(nav.index))
    out = {}
    for y, g in df.groupby(df.index.year):
        v = g["v"].to_numpy()
        peak = np.maximum.accumulate(v)
        out[y] = float((v / peak - 1).min() * 100)
    return out


def main():
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    results = {"cases": [], "rnd": [], "cost": []}

    CANDS = [
        ("RSI6", "RSI", dict(n=6), "头名", "high", 90),
        ("RSI6b", "RSI", dict(n=6), "头名", "high", 95),
        ("ROC60", "ROC", dict(n=60), "头名", "high", 95),
        ("MACD横", "MACD柱", dict(fast=6, slow=13), "横截面", "high", 95),
        ("MA斜60", "MA斜率", dict(n=60, k=10), "头名", "high", 95),
    ]

    for wname, fname, start in S.WINDOWS:
        if not os.path.exists(os.path.join(HERE, fname)):
            continue
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        sig_all = [bl.compute_signal(panel, pool, i, 20, 20) for i in range(len(dates))]
        mom_df = pd.DataFrame({c: (panel[c] / panel[c].shift(20) - 1.0) * 100.0
                               for c in pool if c in panel.columns})
        is_end_i = int(dates.searchsorted(pd.Timestamp(IS_END)))
        oos_start_i = int(dates.searchsorted(pd.Timestamp(OOS_START)))

        def segm(nav, lo, hi):
            s = nav.loc[(nav.index >= str(lo)[:10]) & (nav.index <= str(hi)[:10])]
            if len(s) < 20:
                return None
            return S.stats(s / s.iloc[0])

        # ---- 回归：组合引擎钩子全关 == bl.backtest ----
        r_chk = combo_backtest(panel, dates, s_i, e_i, pool, defensive, sig_list=sig_all)
        b0 = bl.backtest(panel, dates, s_i, e_i, pool, defensive, sticky=1.0,
                         enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
                         exit_lead=10.0, min_def_days=5, buy=0.001, sell=0.001,
                         W=20, MA=20, value_engine=True)
        n = min(len(r_chk["navs"]), len(b0["navs"]))
        dmax = max(abs(float(r_chk["navs"].iloc[i]) - float(b0["navs"].iloc[i]))
                   for i in range(n))
        print(f"\n{'='*76}\n窗口 {wname}  回归(组合引擎钩子全关 vs bl.backtest) maxdiff={dmax}")
        assert dmax == 0.0, f"组合引擎回归失败 maxdiff={dmax}"

        st_base = S.stats(b0["navs"])
        r_d3 = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                              loss_cool=0.03, loss_days=5, sig_list=sig_all)
        st_d3 = S.stats(r_d3["navs"])
        print(f"  基线   final={st_base['final']:.4f} mdd={st_base['mdd']:.2f}% "
              f"sharpe={st_base['sharpe']:.2f} calmar={st_base['calmar']:.2f}")
        print(f"  D3     final={st_d3['final']:.4f} mdd={st_d3['mdd']:.2f}% "
              f"sharpe={st_d3['sharpe']:.2f} calmar={st_d3['calmar']:.2f}")

        for tag, iname, params, scope, direction, q in CANDS:
            on, off = rebuild_on(panel, pool, iname, params, scope, direction, q,
                                 dates, mom_df)
            if on is None:
                continue
            r = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                               gate_on=on, gate_off=off,
                               loss_cool=0.03, loss_days=5, sig_list=sig_all)
            st = S.stats(r["navs"])
            yb, yd, yg = yearly_mdd(b0["navs"]), yearly_mdd(r_d3["navs"]), yearly_mdd(r["navs"])
            print(f"\n  【D3 + {tag}】final={st['final']:.4f} mdd={st['mdd']:.2f}% "
                  f"sharpe={st['sharpe']:.2f} calmar={st['calmar']:.2f} "
                  f"｜ vs 基线 mdd {st['mdd']-st_base['mdd']:+.2f}pp "
                  f"｜ vs D3 mdd {st['mdd']-st_d3['mdd']:+.2f}pp "
                  f"calmar {st['calmar']-st_d3['calmar']:+.2f}")
            print("    逐年 MDD: " + "  ".join(
                f"{y}: 基{yb.get(y,0):.1f}/D3{yd.get(y,0):.1f}/+{tag[:4]}{yg.get(y,0):.1f}"
                for y in sorted(yg)))

            # ---- 随机同频对照（叠加在 D3 之上）----
            n_trig = int(on.values[s_i:e_i].sum())
            pool_idx = np.arange(s_i, e_i)
            arr = []
            for seed in range(200):
                rng = np.random.default_rng(seed)
                pick = rng.choice(pool_idx, size=min(n_trig, len(pool_idx)), replace=False)
                ron = pd.Series(False, index=dates)
                ron.iloc[pick] = True
                rr = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                                    gate_on=ron, gate_off=off,
                                    loss_cool=0.03, loss_days=5, sig_list=sig_all)
                arr.append(S.stats(rr["navs"])["mdd"])
            arr = np.array(arr)
            beat = float((arr > st["mdd"]).mean() * 100)
            print(f"    随机同频对照(200次, 叠加D3上): 中位 {np.median(arr):.2f}% "
                  f"p05 {np.percentile(arr,5):.2f}% ｜ 真实优于随机 {beat:.1f}% 的情形")
            results["rnd"].append(dict(win=wname, tag=tag, n_trig=n_trig,
                                       real_mdd=st["mdd"],
                                       rnd_med=float(np.median(arr)),
                                       rnd_p05=float(np.percentile(arr, 5)),
                                       beat_pct=beat))
            results["cases"].append(dict(win=wname, tag=tag, ind=iname,
                                         params=str(params), scope=scope, q=q,
                                         base=st_base, d3=st_d3, combo=st,
                                         d_vs_base_mdd=st["mdd"] - st_base["mdd"],
                                         d_vs_d3_mdd=st["mdd"] - st_d3["mdd"],
                                         d_vs_d3_calmar=st["calmar"] - st_d3["calmar"],
                                         yearly_mdd=dict(base=yb, d3=yd, combo=yg),
                                         n_trig=n_trig))

        # ---- 成本敏感性（对最优候选）----
        best = max([c for c in results["cases"] if c["win"] == wname],
                   key=lambda c: c["combo"]["calmar"])
        print(f"\n  -- 成本敏感性（最优候选 {best['tag']}）--")
        for cost in (0.001, 0.002, 0.003):
            on, off = rebuild_on(panel, pool, best["ind"], eval(best["params"]),
                                 best["scope"], "high", best["q"], dates, mom_df)
            rb = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                                buy=cost/2, sell=cost/2, sig_list=sig_all)
            rd = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                                loss_cool=0.03, loss_days=5,
                                buy=cost/2, sell=cost/2, sig_list=sig_all)
            rc = combo_backtest(panel, dates, s_i, e_i, pool, defensive,
                                gate_on=on, gate_off=off, loss_cool=0.03, loss_days=5,
                                buy=cost/2, sell=cost/2, sig_list=sig_all)
            sb, sd, sc = S.stats(rb["navs"]), S.stats(rd["navs"]), S.stats(rc["navs"])
            print(f"    成本{cost*100:.1f}%: 基线 {sb['final']:.4f}/{sb['mdd']:.2f}% ｜ "
                  f"D3 {sd['final']:.4f}/{sd['mdd']:.2f}% ｜ 组合 {sc['final']:.4f}/{sc['mdd']:.2f}%")
            results["cost"].append(dict(win=wname, tag=best["tag"], cost=cost,
                                        base=sb, d3=sd, combo=sc))

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

    with open(os.path.join(HERE, "out", "ind_combo.json"), "w", encoding="utf-8") as f:
        json.dump(_clean(results), f, ensure_ascii=False, indent=1)
    print("\n写出: out/ind_combo.json")


if __name__ == "__main__":
    main()
