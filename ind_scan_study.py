"""主轮动池「指标风控」广度扫描（2026-10-24 下午）

用户诉求：
  「目前看我提到的防回撤的策略并没有起到很好的作用，是否能寻遍其余的各种指标，能减少回撤？」

设计（针对「减回撤」，与前几轮「增收益」不同）：
  ① 扫描 13 类技术指标 × 多参数 × 多阈值 × 多方向；
  ② 每个组合作为一种「风控触发」：**在 ROT 状态下，指标满足条件 → 切防守（512040 + 价值引擎）**；
     条件不再满足（且过热已降）→ 切回轮动。**不砍已有仓位的逻辑差异见下**：
     防守位是 512040（低 Beta 价值），不是空仓，所以「切防守」= 降低组合 Beta。
  ③ ⛔ 已证伪清单**不再重复测**：J<20 买 / MACD 柱转强买 / J>80 卖 / 柱翻绿卖 /
     广度止损(切走) / 22 种经典止损 / 广度闸门(不入场) / 抄底 / 环境温度择时 / EP。

⚠️ 多重比较铁律（本轮新增，防止「扫出假金」）：
  扫描组合数 ≫ 独立假设数 ⇒ 必然出现「看起来有效」的结果。对策：
  · **样本内(IS 2019-01~2022-06) 选规则 → 样本外(OOS 2022-07~2026-09) 验证**；
    只有 OOS 也改善 MDD 的组合才进入候选。
  · **随机同频对照**：把指标换成「同频率的随机触发」，看真实指标是否显著优于随机。
    （这能识别「只是少交易/少亏钱」的伪信号）
  · **邻域稳定性**：同一指标相邻阈值是否成片有效（点状有效=过拟合）。
  · 双窗口（2019 起 / 2015 起）交叉确认。

指标库：indicators_lib.py（13 类，仅用收盘价；无 high/low ⇒ 不含 ATR/影线类）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import etf_backtest_long as bl     # noqa: E402
import indicators_lib as ind       # noqa: E402
from rot_stop_study import load_win  # noqa: E402

W, MA = 20, 20
NAMES = bl.NAMES
DEFENSIVE = "512040"

# 样本切分点：IS 选规则，OOS 验证
IS_END = "2022-06-30"
OOS_START = "2022-07-01"

WINDOWS = [("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
           ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05")]


# ---------------------------------------------------------------- 引擎
def backtest_gate(panel, dates, start_i, end_i, pool, defensive, *,
                   sticky=1.0, enter_dev=12.0, enter_lead=15.0,
                   exit_dev=9.0, exit_lead=10.0, min_def_days=5,
                   buy=0.001, sell=0.001, value_engine=True,
                   gate_vals=None, gate_on=None, gate_off=None,
                   gate_min_days=0, sig_list=None):
    """带「指标风控闸门」的策略状态机。

    gate_vals : pd.Series，与 dates 对齐；NaN = 无效（不触发）
    gate_on   : 触发条件（布尔 Series）
    gate_off  : 解除条件（布尔 Series）
    gate_min_days : 触发后至少防守多少个交易日才允许解除

    全部闸门关闭（gate_on=None）时与 bl.backtest() 逐位一致。
    """
    state = "ROT"
    def_days = 0
    held = "CASH"
    nav = 1.0
    navs = [(dates[start_i], 1.0)]
    trades = []
    prev_eff = None
    held_log, avail_log, head_log = [], [], []
    stick_hits = 0
    ve_hold = (bl.value_engine_hold(panel, defensive, dates) if value_engine else None)
    gate_days = 0          # 当前处于闸门触发的防守天数
    gate_trig = 0          # 闸门触发次数
    in_gate = False        # 是否因闸门进入防守

    for i in range(start_i, end_i):
        sig = (sig_list[i] if sig_list is not None
               else bl.compute_signal(panel, pool, i, W, MA))
        if sig is None:
            ret = 0.0
            if held != "CASH" and held in panel.columns:
                p0, p1 = panel[held].iloc[i], panel[held].iloc[i + 1]
                if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                    ret = p1 / p0 - 1.0
            nav *= (1.0 + ret)
            avail_log.append((dates[i + 1], 0))
            head_log.append((dates[i + 1], None))
            held_log.append((dates[i + 1], held))
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
            stick_hits += 1
        m_head = sig["m"][raw_head]
        dev_eff = sig["dev"].get(eff_head, float("nan"))

        reason = ""
        if state == "ROT":
            over = ((dev_eff == dev_eff and dev_eff > enter_dev) or
                    (sig["lead"] == sig["lead"] and sig["lead"] > enter_lead))
            if over:
                state, def_days = "DEF", 0
                in_gate = False
                reason = (f"过热(乖离{dev_eff:+.2f}%>{enter_dev:.0f}%"
                          if (dev_eff == dev_eff and dev_eff > enter_dev)
                          else f"过热(领先{sig['lead']:+.2f}pp>{enter_lead:.0f}pp") + " -> 切防守"
            elif gate_on is not None and bool(gate_on.iloc[i]):
                state, def_days = "DEF", 0
                in_gate = True
                gate_trig += 1
                reason = f"指标风控触发 -> 切防守"
        elif state == "DEF":
            def_days += 1
            if in_gate:
                # 闸门防守：需同时满足「闸门条件解除」才可切回
                if gate_off is not None and not bool(gate_off.iloc[i]):
                    gate_days += 1
                elif gate_min_days and gate_days < gate_min_days:
                    gate_days += 1
                else:
                    state, def_days, in_gate, gate_days = "ROT", 0, False, 0
                    reason = "闸门条件解除 -> 回轮动"
            else:
                if (dev_eff == dev_eff and dev_eff < exit_dev) and \
                   (sig["lead"] == sig["lead"] and sig["lead"] < exit_lead) and \
                   def_days >= min_def_days:
                    state, def_days = "ROT", 0
                    reason = (f"降温(乖离{dev_eff:+.2f}%<{exit_dev:.0f}% 且 "
                              f"领先{sig['lead']:+.2f}pp<{exit_lead:.0f}pp) 且防守满 "
                              f"{min_def_days} 日 -> 切回轮动")

        # ---------------- 目标持仓 ----------------
        if state == "ROT":
            target = eff_head
        else:
            ve_ok = (not value_engine) or bool(ve_hold.loc[dates[i]])
            target = defensive if ve_ok else "CASH"

        switched = (target != held)
        if switched:
            nav *= (1.0 - (buy + sell))
            trades.append(dict(date=str(dates[i].date()), frm=held, to=target,
                               frm_name=("空仓" if held == "CASH" else NAMES.get(held, held)),
                               to_name=("持币" if target == "CASH" else NAMES.get(target, target)),
                               state=state, reason=reason, tie=bool(tied),
                               lead=(None if sig["lead"] != sig["lead"] else round(sig["lead"], 2)),
                               m_head=round(m_head, 2),
                               dev=(None if dev_eff != dev_eff else round(dev_eff, 2)),
                               nav=round(nav, 6)))
        ret = 0.0
        if target in panel.columns:
            p0, p1 = panel[target].iloc[i], panel[target].iloc[i + 1]
            if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                ret = p1 / p0 - 1.0
        nav *= (1.0 + ret)
        held = target
        prev_eff = eff_head
        avail_log.append((dates[i + 1], len(sig["m"])))
        head_log.append((dates[i + 1], eff_head))
        held_log.append((dates[i + 1], target))
        navs.append((dates[i + 1], nav))

    nd = pd.Series({d.strftime("%Y-%m-%d"): v for d, v in navs})
    return dict(navs=nd, trades=trades, held_log=held_log, head_log=head_log,
                avail_log=avail_log, stick_hits=stick_hits, final=float(nd.iloc[-1]),
                gate_trig=gate_trig, in_gate=in_gate,
                cash_days=int(sum(1 for _d, t in held_log if t == "CASH")),
                def_days=int(sum(1 for _d, t in held_log if t == DEFENSIVE)))


def stats(nav):
    st = bl.stats(nav)
    return dict(final=float(nav.iloc[-1]), total=st["total"] * 100,
                mdd=st["mdd"] * 100, sharpe=st["sharpe"], calmar=st["calmar"],
                ann=st["ann"] * 100, vol=st["vol"] * 100)


def mdd_win(nav):
    v = nav.to_numpy(float)
    peak = np.maximum.accumulate(v)
    dd = v / peak - 1.0
    i = int(np.argmin(dd))
    j0 = int(np.argmax(v[:i + 1]))
    return str(nav.index[j0])[:10], str(nav.index[i])[:10], float(dd[i] * 100)


def yearly(nav):
    df = pd.DataFrame({"v": nav.to_numpy(float)}, index=pd.to_datetime(nav.index))
    out = {}
    for y, g in df.groupby(df.index.year):
        v = g["v"].to_numpy()
        peak = np.maximum.accumulate(v)
        out[y] = dict(ret=(v[-1] / v[0] - 1) * 100,
                      mdd=float((v / peak - 1).min() * 100))
    return out


def curve_of(nav, step=3):
    idx = list(range(0, len(nav), step))
    if idx[-1] != len(nav) - 1:
        idx.append(len(nav) - 1)
    return dict(d=[nav.index[i] for i in idx],
                v=[round(float(nav.iloc[i]), 5) for i in idx])


def seg_oos(nav, dates, oos_start_i, e_i):
    """OOS 段统计（供随机对照脚本调用）"""
    lo = str(dates[oos_start_i])[:10]
    hi = str(dates[e_i])[:10]
    s = nav.loc[(nav.index >= lo) & (nav.index <= hi)]
    if len(s) < 20:
        return None
    return stats(s / s.iloc[0])


# ---------------------------------------------------------------- 指标组合生成
def build_indicator_series(panel, pool, name, params, dates, mom_df=None):
    """对池内所有标的算指标，返回「横截面聚合」的两种口径：
      - head: 头名（当日动横20 最高）的指标值
      - cross: 池内该指标的横截面分位数（0~100，池内最强者所在分位）
    """
    fn = ind.REGISTRY[name][0]
    per_code = {}
    for c in pool:
        if c not in panel.columns:
            continue
        per_code[c] = fn(panel[c], **params)
    if not per_code:
        return None, None
    df = pd.DataFrame(per_code)

    # 动横 20（缓存传入，避免重复计算）
    if mom_df is None:
        mom_df = pd.DataFrame({c: (panel[c] / panel[c].shift(W) - 1.0) * 100.0
                               for c in df.columns if c in panel.columns})
    # head 口径：每日取「动横20 最高」的标的的指标值（向量化，避免逐日循环）
    head_codes = mom_df.idxmax(axis=1)
    # 用 stack 构造 (日期, 标的) → 指标值 的映射，再按 head_codes 对齐
    stacked = df.stack(dropna=False)          # index=(date, code)
    head_vals = stacked.reindex(
        pd.MultiIndex.from_arrays([head_codes.index, head_codes.values])
    ).reset_index(level=1, drop=True)
    head_vals.index.name = df.index.name
    head_vals = head_vals.reindex(df.index)

    # cross 口径：横截面分位（池内最强的那个指标值的分位）
    cross_vals = df.rank(pct=True).max(axis=1)
    return head_vals, cross_vals


def gen_thresholds(series, direction, n=6):
    """【已弃用】静态全窗口分位阈值 —— 含未来数据（前视偏差），仅供对比。
    正式扫描用 expanding_thresholds() 的滚动扩张分位。"""
    v = series.dropna()
    if len(v) < 100:
        return []
    qs = [2, 5, 10, 15, 20, 30] if direction == "low" else \
         [70, 80, 85, 90, 95, 98]
    out = []
    for q in qs:
        t = float(v.quantile(q / 100.0))
        out.append((q, t))
    return out


def expanding_thresholds(series, direction, min_periods=250):
    """滚动扩张分位阈值（无前视偏差，可实盘使用）。

    对每个时点 i，阈值 = series[:i] 的 q 分位（只看历史）。
    pandas 的 expanding().quantile() 正是这个语义，且已向量化。
    """
    s = series.dropna()
    if len(s) < min_periods + 50:
        return []
    qs = [2, 5, 10, 15, 20, 30] if direction == "low" else \
         [70, 80, 85, 90, 95, 98]
    out = []
    for q in qs:
        thr_series = series.expanding(min_periods=min_periods).quantile(q / 100.0)
        out.append((q, thr_series))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "out", "ind_scan.json"))
    args = ap.parse_args()

    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    results = {"meta": dict(IS_END=IS_END, OOS_START=OOS_START,
                            n_indicators=len(ind.REGISTRY)),
               "scan": [], "random": [], "cases": [], "diags": {}}

    for wname, fname, start in WINDOWS:
        if not os.path.exists(os.path.join(HERE, fname)):
            continue
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        print(f"\n{'='*70}\n窗口 {wname}: {dates[s_i].date()} ~ {dates[e_i].date()}  "
              f"池 {len(pool)} 只\n{'='*70}")

        # 基线
        base = bl.backtest(panel, dates, s_i, e_i, pool, defensive,
                           sticky=1.0, enter_dev=12.0, enter_lead=15.0,
                           exit_dev=9.0, exit_lead=10.0, min_def_days=5,
                           buy=0.001, sell=0.001, W=W, MA=MA, value_engine=True)
        nav0 = base["navs"]
        st0 = stats(nav0)
        # IS / OOS 切分
        is_end_i = int(dates.searchsorted(pd.Timestamp(IS_END)))
        oos_start_i = int(dates.searchsorted(pd.Timestamp(OOS_START)))
        print(f"  基线: final={st0['final']:.4f} mdd={st0['mdd']:.2f}% "
              f"sharpe={st0['sharpe']:.2f} calmar={st0['calmar']:.2f}")

        def seg(nav, lo, hi):
            lo = str(lo)[:10]
            hi = str(hi)[:10]
            s = nav.loc[(nav.index >= lo) & (nav.index <= hi)]
            if len(s) < 20:
                return None
            st = stats(s / s.iloc[0])
            return st

        base_is = seg(nav0, dates[s_i], dates[min(is_end_i, e_i)])
        base_oos = seg(nav0, dates[oos_start_i], dates[e_i])
        print(f"  IS  段: mdd={base_is['mdd']:.2f}% total={base_is['total']:+.1f}%")
        print(f"  OOS 段: mdd={base_oos['mdd']:.2f}% total={base_oos['total']:+.1f}%")

        results["cases"].append(dict(win=wname, start=str(dates[s_i].date()),
                                     end=str(dates[e_i].date()),
                                     base=dict(st0, mdd_win=list(mdd_win(nav0)),
                                               yearly=yearly(nav0),
                                               curve=curve_of(nav0)),
                                     base_is=base_is, base_oos=base_oos))
        n_combo = 0
        mom_df = pd.DataFrame({c: (panel[c] / panel[c].shift(W) - 1.0) * 100.0
                               for c in pool if c in panel.columns})
        # 预计算信号（信号与指标无关，全程复用）—— 这是扫描提速的关键
        sig_all = [bl.compute_signal(panel, pool, i, W, MA) for i in range(len(dates))]
        print(f"  信号预计算完成 ({len(sig_all)} 日)")
        for iname in ind.REGISTRY:
            fn, plist, sem, lag, desc = ind.REGISTRY[iname]
            for params in plist:
                head_vals, cross_vals = build_indicator_series(panel, pool, iname,
                                                               params, dates, mom_df)
                if head_vals is None:
                    continue
                for scope, series in (("头名", head_vals), ("横截面", cross_vals)):
                    if series is None or series.dropna().empty:
                        continue
                    for direction in ("low", "high"):
                        for q, thr_s in expanding_thresholds(series, direction):
                            # 触发：滚动阈值 —— 指标 <= 阈值(low) 或 >= 阈值(high)
                            if direction == "low":
                                on = (series <= thr_s)
                                off = (series > thr_s * 0.5 if True else series > 0)
                            else:
                                on = (series >= thr_s)
                                off = (series < thr_s * 0.5 if True else series < 100)
                            on = on.fillna(False).reindex(dates).fillna(False)
                            off = off.fillna(False).reindex(dates).fillna(False)
                            thr_now = float(thr_s.iloc[-1]) if len(thr_s) else None
                            n_combo += 1
                            r = backtest_gate(panel, dates, s_i, e_i, pool, defensive,
                                              gate_on=on, gate_off=off, sig_list=sig_all)
                            nav = r["navs"]
                            st = stats(nav)
                            is_s = seg(nav, dates[s_i], dates[min(is_end_i, e_i)])
                            oos_s = seg(nav, dates[oos_start_i], dates[e_i])
                            if not is_s or not oos_s:
                                continue
                            rec = dict(win=wname, ind=iname, params=str(params),
                                       scope=scope, direction=direction,
                                       q=q, thr=thr_now, thr_series_tail=thr_s.tail(3).tolist(),
                                       lag=lag,
                                       full=dict(st),
                                       is_=is_s, oos=oos_s,
                                       base_full=st0, base_is=base_is, base_oos=base_oos,
                                       d_is_mdd=is_s["mdd"] - base_is["mdd"],
                                       d_oos_mdd=oos_s["mdd"] - base_oos["mdd"],
                                       d_oos_mdd_pct=(oos_s["mdd"] - base_oos["mdd"])
                                       / abs(base_oos["mdd"]) * 100 if base_oos["mdd"] else None,
                                       d_full_mdd=st["mdd"] - st0["mdd"],
                                       d_full_total=st["total"] - st0["total"],
                                       d_oos_total=oos_s["total"] - base_oos["total"],
                                       d_full_calmar=st["calmar"] - st0["calmar"],
                                       d_oos_calmar=oos_s["calmar"] - base_oos["calmar"],
                                       gate_trig=r["gate_trig"],
                                       def_days=r["def_days"],
                                       yearly=yearly(nav))
                            results["scan"].append(rec)

        print(f"  扫描组合数: {n_combo}")

    # 写 JSON
    def _clean(o):
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(v) for v in o]
        if isinstance(o, (np.floating, np.integer)):
            o = o.item()
        if isinstance(o, float) and o != o:
            return None
        if isinstance(o, bool):
            return o
        return o

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(_clean(results), f, ensure_ascii=False, indent=1)
    print(f"\n写出: {args.out}  （scan 条数 {len(results['scan'])}）")


if __name__ == "__main__":
    main()
