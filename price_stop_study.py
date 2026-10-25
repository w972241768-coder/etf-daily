"""① 真正的「价格止损」模式回测 ② P3 与过热熔断的关系（2026-10-24）

问题①：把 D3 换成「买入后该笔亏损 3% 就清仓，等 5 日再进场」
  —— 与现行 D3 的本质区别：现行 D3 只在「换仓时刻」检查亏损；
     本模式是**每个交易日**检查，价格一触及 −3% 就清仓，不等换仓。

问题②：P3（头名 ROC60 进 95 分位 → 空仓 20 日）与主策略固有「过热熔断」
      （乖离>12% 或 领先>15pp → 切防守 512040；乖离<9% 且 领先<10pp 且 满5日 → 切回）
      是否冲突？谁效果更好？
  检验方法：
    a) 重叠度：P3 触发日 vs 过热触发日
    b) 四组对照：基线(含过热) / 基线+P3 / 关过热+P3 / 关过热无P3
       ⇒ 若「关过热+P3」≈「基线」说明 P3 能替代过热；若远差说明两者互补、都需要
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

COST = 0.0001                       # 单边万一
W, MA = 20, 20
WINDOWS = [("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
           ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05")]
BASE = dict(sticky=1.0, enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
            exit_lead=10.0, min_def_days=5, buy=COST, sell=COST,
            W=W, MA=MA, value_engine=True)
NO_OVERHEAT = dict(enter_dev=999.0, enter_lead=999.0)   # 关掉过热熔断


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


def yearly_ret(nav):
    df = pd.DataFrame({"v": nav.to_numpy(float)}, index=pd.to_datetime(nav.index))
    out = {}
    for y, g in df.groupby(df.index.year):
        v = g["v"].to_numpy()
        out[y] = (v[-1] / v[0] - 1) * 100
    return out


def price_stop_backtest(panel, dates, s_i, e_i, pool, defensive, *, sig_list,
                        stop_pct=0.03, cool_days=5, **kw):
    """真正的「价格止损」模式：持仓期间任一交易日，若该标的相对建仓价
    回撤 ≤ −stop_pct ⇒ 当日清仓（CASH），空仓 cool_days 个交易日后恢复轮动。

    与现行 D3 的区别：D3 只在「换仓时刻」检查；本模式**每日**检查。
    钩子关闭（stop_pct=None）时与 bl.backtest() 逐位一致。
    """
    state, def_days = "ROT", 0
    held, nav = "CASH", 1.0
    navs = [(dates[s_i], 1.0)]
    prev_eff = None
    ve_hold = (bl.value_engine_hold(panel, defensive, dates)
               if kw.get("value_engine", True) else None)
    entry_px = None
    cool_until = -1
    stop_n = 0
    stops = []
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
        reason = ""

        # ---- 价格止损：每日检查（在状态迁移之前，优先于其他逻辑） ----
        if stop_pct is not None and held != "CASH" and held in panel.columns \
                and entry_px and i >= cool_until:
            _px = float(panel[held].iloc[i])
            if _px == _px and _px / entry_px - 1.0 <= -stop_pct:
                cool_until = min(i + cool_days, len(dates) - 1)
                stop_n += 1
                stops.append(dict(date=str(dates[i].date()), code=held,
                                  name=bl.NAMES.get(held, held),
                                  ret=round((_px / entry_px - 1) * 100, 2),
                                  until=str(dates[cool_until].date())))
                reason = f"价格止损({(_px/entry_px-1)*100:+.2f}% ≤ -{stop_pct*100:.0f}%) → 清仓{cool_days}日"

        if state == "ROT":
            over = ((dev_eff == dev_eff and dev_eff > kw.get("enter_dev", 12.0)) or
                    (sig["lead"] == sig["lead"] and sig["lead"] > kw.get("enter_lead", 15.0)))
            if over and not reason:
                state, def_days = "DEF", 0
                reason = "过热 -> 切防守"
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
            if stop_pct is not None and i < cool_until:
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
    nd = pd.Series({d.strftime("%Y-%m-%d"): v for d, v in navs})
    return dict(navs=nd, stop_n=stop_n, stops=stops, final=float(nd.iloc[-1]),
                cash_days=int(sum(1 for d, v in navs if v == v and False)))


def main():
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    out = {"q1": [], "q2": {"overlap": [], "cases": []}}

    for wname, fname, start in WINDOWS:
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        sig_all = [bl.compute_signal(panel, pool, i, W, MA) for i in range(len(dates))]
        print(f"\n{'#'*100}\n# 窗口 {wname}  {dates[s_i].date()} ~ {dates[e_i].date()}  单边万一\n{'#'*100}")

        # ============ 问题①：价格止损模式 ============
        b = bl.backtest(panel, dates, s_i, e_i, pool, defensive, **BASE)
        d3 = bl.backtest(panel, dates, s_i, e_i, pool, defensive,
                         loss_cool=0.03, loss_days=5, **BASE)
        p3 = bl.backtest(panel, dates, s_i, e_i, pool, defensive,
                         loss_cool=0.03, loss_days=5, roc_q=0.95, roc_days=20,
                         roc_n=60, roc_min_periods=250, **BASE)
        sb, sd3, sp3 = stats(b["navs"]), stats(d3["navs"]), stats(p3["navs"])
        print(f"\n【问题①】价格止损模式（买入后亏 3% 即清仓，等 5 日再进场）")
        print(f"  对照：基线 {sb['final']:.4f}/{sb['mdd']:.2f}%/C{sb['calmar']:.2f}  "
              f"｜ 现行D3 {sd3['final']:.4f}/{sd3['mdd']:.2f}%/C{sd3['calmar']:.2f}  "
              f"｜ P3 {sp3['final']:.4f}/{sp3['mdd']:.2f}%/C{sp3['calmar']:.2f}")
        print(f"\n  {'止损阈值':>8}{'冷却日':>7}{'净值':>10}{'MDD':>9}{'Calmar':>8}{'夏普':>7}"
              f"{'止损次数':>9}{'换仓':>6}{'vs基线MDD':>11}{'vs基线净值':>12}")
        for sp in (0.02, 0.03, 0.05):
            for cd in (5,):
                r = price_stop_backtest(panel, dates, s_i, e_i, pool, defensive,
                                        sig_list=sig_all, stop_pct=sp, cool_days=cd,
                                        **{k: v for k, v in BASE.items()})
                st = stats(r["navs"])
                ntr = len(r["navs"])   # 占位
                # 换仓笔数从 navs 变化粗算不了，用 stops 长度代替展示
                print(f"  {sp*100:>7.0f}%{cd:>7}{st['final']:>10.4f}{st['mdd']:>8.2f}%"
                      f"{st['calmar']:>8.2f}{st['sharpe']:>7.2f}{r['stop_n']:>9}"
                      f"{'—':>6}{st['mdd']-sb['mdd']:>+10.2f}pp{st['final']-sb['final']:>+11.4f}")
                out["q1"].append(dict(win=wname, stop_pct=sp, cool_days=cd,
                                      final=st["final"], mdd=st["mdd"],
                                      calmar=st["calmar"], sharpe=st["sharpe"],
                                      stop_n=r["stop_n"],
                                      d_vs_base_mdd=st["mdd"] - sb["mdd"],
                                      d_vs_base_final=st["final"] - sb["final"],
                                      d_vs_d3_final=st["final"] - sd3["final"],
                                      base=sb, d3=sd3,
                                      yearly=yearly_mdd(r["navs"]),
                                      yearly_ret=yearly_ret(r["navs"]),
                                      stops=r["stops"][:8]))

        # ============ 问题②：P3 vs 过热熔断 ============
        print(f"\n【问题②】P3 与「过热熔断」的关系")
        # a) 重叠度：过热触发日（reason 含「过热」）
        over_dates = {t["date"] for t in b["trades"] if "过热" in (t.get("reason") or "")}
        p3_dates = {c["date"] for c in p3["roc_cools"]}
        both = over_dates & p3_dates
        print(f"  过热熔断触发 {len(over_dates)} 次 ｜ P3 触发 {len(p3_dates)} 次 ｜ "
              f"同日 {len(both)} 次（占 P3 的 {len(both)/max(1,len(p3_dates))*100:.0f}%）")
        print(f"  ⇒ 重叠度低说明两者**不冲突**，捕捉的是不同情形")
        # b) 四组对照
        print(f"\n  {'方案':<24}{'净值':>10}{'MDD':>9}{'Calmar':>8}{'夏普':>7}{'换仓':>6}")
        rows = {}
        for tag, kw in (
            ("基线（含过热）", dict()),
            ("基线 + P3", dict(roc_q=0.95, roc_days=20, roc_n=60, roc_min_periods=250)),
            ("关过热 + P3", dict(enter_dev=999.0, enter_lead=999.0, roc_q=0.95,
                                 roc_days=20, roc_n=60, roc_min_periods=250)),
            ("关过热 无P3", dict(enter_dev=999.0, enter_lead=999.0)),
            ("关过热 + D3", dict(enter_dev=999.0, enter_lead=999.0,
                                 loss_cool=0.03, loss_days=5)),
            ("关过热 + D3 + P3", dict(enter_dev=999.0, enter_lead=999.0, loss_cool=0.03,
                                      loss_days=5, roc_q=0.95, roc_days=20,
                                      roc_n=60, roc_min_periods=250)),
        ):
            merged = dict(BASE)
            merged.update(kw)
            r = bl.backtest(panel, dates, s_i, e_i, pool, defensive, **merged)
            st = stats(r["navs"])
            rows[tag] = st
            print(f"  {tag:<24}{st['final']:>10.4f}{st['mdd']:>8.2f}%{st['calmar']:>8.2f}"
                  f"{st['sharpe']:>7.2f}{len(r['trades']):>6}")
            out["q2"]["cases"].append(dict(win=wname, tag=tag, **st,
                                           trades=len(r["trades"]),
                                           yearly=yearly_mdd(r["navs"])))
        # 解读
        base_c = rows["基线（含过热）"]
        no_p3 = rows["关过热 + P3"]
        no_both = rows["关过热 无P3"]
        print(f"\n  解读：")
        print(f"    · 关掉过热本身：净值 {no_both['final']:.4f} vs 基线 {base_c['final']:.4f}"
              f" ⇒ 过热熔断贡献 {no_both['final']-base_c['final']:+.4f}")
        print(f"    · 关过热但加 P3：{no_p3['final']:.4f}（ recovering {no_p3['final']-no_both['final']:+.4f}）"
              f"  vs 基线 {base_c['final']:.4f}")
        if no_p3["final"] < base_c["final"] * 0.9:
            print(f"    ⇒ **P3 不能替代过热熔断**，两者互补、都需要")
        else:
            print(f"    ⇒ P3 在关掉过热后仍能 recover 大部分，有一定替代性")
        out["q2"]["overlap"].append(dict(win=wname, n_over=len(over_dates),
                                         n_p3=len(p3_dates), n_both=len(both),
                                         overlap_pct=len(both) / max(1, len(p3_dates)) * 100))

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

    with open(os.path.join(HERE, "out", "price_stop_and_p3_overheat.json"), "w",
              encoding="utf-8") as f:
        json.dump(_clean(out), f, ensure_ascii=False, indent=1)
    print("\n写出: out/price_stop_and_p3_overheat.json")


if __name__ == "__main__":
    main()
