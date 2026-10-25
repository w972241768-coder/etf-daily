"""「D3 止损后广度闸门」研究（2026-10-24）

用户设想（07:54 原话）：
  「当轮动池内的动横20 只有1只（可以多做测试）> 0 时就持续空仓，
    但是前提是刚做完止损并空仓5天后，
    目的是为了解决回测过程中提到的震荡市或者阴跌的情况持续买入亏损的情况」

机制解读（关键，不能做成「广度止损」那种已被证伪的形态）：
  ① 先由 D3 触发「本笔亏 ≥3% → 空仓 5 日」（闸门**上膛**）；
  ② 冷却期满的那一天起，若池内动横20 > 0 的只数 <= 阈值 → **继续持币不入场**；
  ③ 直到广度恢复到 gate_recover 才解除闸门、重新买头名。
  ⇒ 广度只在「止损后」起作用，不影响止损前的正常轮动。

⚠️ 与已证伪的「广度止损」的区别（必须记住）：
   旧方案 = 广度差就**立刻切走已有持仓**（砍在上涨途中）→ 24 组全负；
   本方案 = 广度差就**不入场**（本来就没仓位，不存在砍）→ 完全不同。

对照纪律（沿用 2026-10-24 方法论）：
  · 两个窗口：2019-12-31 起（主窗）/ 2015 起（长窗）
  · 零成本 + 含成本 0.2% 双报
  · 随机同频对照：同样次数的随机空仓，看增益是否只是「少交易」的副产品
  · 归因：闸门期间若强行持有头名会怎样（counterfactual）
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

import etf_backtest_long as bl  # noqa: E402
import rot_stop_study as rs      # noqa: E402

W, MA = 20, 20
NAMES = bl.NAMES
COOL = 5          # D3 空仓天数
THR = 0.03        # D3 单笔亏损阈值

WINDOWS = [
    ("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
    ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05"),
]


def run(panel, dates, s_i, e_i, pool, defensive, sig, cost=0.002, **kw):
    r = rs.simulate(panel, dates, s_i, e_i, pool, defensive,
                    buy=cost / 2, sell=cost / 2, sig_list=sig, **kw)
    st = rs.summarize(r["navs"])
    tr = r["trades"]
    n_single = len([t for t in tr if "本笔" in t["reason"] and t["to"] == "CASH"])
    n_gate = len([t for t in tr if "广度闸门" in t["reason"]])
    st.update(trades_n=len(tr), single_trig=n_single, gate_trig=n_gate,
              gate_days=r.get("gate_hits", 0),
              cool_days=r.get("cool_hits", 0) + r.get("cool3_hits", 0),
              cash_pct=r["cash_days"] / len(r["navs"]) * 100)
    return r, st


def curve_of(nav, step=3):
    idx = list(range(0, len(nav), step))
    if idx[-1] != len(nav) - 1:
        idx.append(len(nav) - 1)
    return dict(d=[nav.index[i] for i in idx],
                v=[round(float(nav.iloc[i]), 5) for i in idx])


def gate_counterfactual(panel, dates, r, sig):
    """归因：闸门持币期间，若强行持有「当时的头名」，收益会怎样？

    目的 —— 回答「这个闸门到底在躲什么」：
      · 若被躲掉的其实在涨  → 闸门是在砍收益（负）
      · 若被躲掉的在跌    → 闸门在救收益（正）
    """
    held = {d: t for d, t in r["held_log"]}
    gate_days = []
    for t in r["trades"]:
        if "广度闸门" not in t["reason"]:
            continue
        # 这次换仓是因为广度差而持币 → 找出被放弃的那个标的
        gate_days.append((t["date"], t["frm"], t["to"], t["reason"]))
    out = []
    for dt, frm, to, reason in gate_days:
        if frm == "CASH":
            continue          # 从空仓继续保持空仓，没有被放弃的头名
        i = dates.searchsorted(pd.Timestamp(dt))
        if i >= len(dates) - 1:
            continue
        p0 = float(panel[frm].iloc[i])
        p5 = float(panel[frm].iloc[min(i + 5, len(dates) - 1)])
        p10 = float(panel[frm].iloc[min(i + 10, len(dates) - 1)])
        if p0 != p0 or p0 == 0:
            continue
        out.append(dict(date=dt, code=frm, name=NAMES.get(frm, frm),
                        fwd5=(p5 / p0 - 1) * 100, fwd10=(p10 / p0 - 1) * 100,
                        reason=reason[:60]))
    return out


def mdd_window(nav):
    v = nav.to_numpy(float)
    peak = np.maximum.accumulate(v)
    dd = v / peak - 1.0
    i = int(np.argmin(dd))
    j0 = int(np.argmax(v[:i + 1]))
    return nav.index[j0], nav.index[i], float(dd[i] * 100)


def yearly(nav):
    df = pd.DataFrame({"v": nav.to_numpy(float)}, index=pd.to_datetime(nav.index))
    out = {}
    for y, g in df.groupby(df.index.year):
        v = g["v"].to_numpy()
        peak = np.maximum.accumulate(v)
        out[y] = dict(ret=(v[-1] / v[0] - 1) * 100, mdd=float((v / peak - 1).min() * 100))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "out", "rot_gate.json"))
    args = ap.parse_args()

    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    results = {"meta": dict(cool=COOL, thr=THR, windows=[w[0] for w in WINDOWS]),
               "cases": [], "grid": [], "random": [], "diag": {}}

    for wname, fname, start in WINDOWS:
        if not os.path.exists(os.path.join(HERE, fname)):
            print(f"[skip] 缺数据文件 {fname}")
            continue
        panel, dates, s_i, e_i = rs.load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        sig = [bl.compute_signal(panel, pool, i, W, MA) for i in range(len(dates))]
        base_kw = dict(sticky=1.0, enter_dev=12.0, enter_lead=15.0,
                       exit_dev=9.0, exit_lead=10.0, min_def_days=5)

        # ---------- 基线 ----------
        rA, sA = run(panel, dates, s_i, e_i, pool, defensive, sig, **base_kw)
        rD, sD = run(panel, dates, s_i, e_i, pool, defensive, sig,
                     loss_cool=THR, loss_days=COOL, **base_kw)
        print(f"\n=== 窗口 {wname} ({dates[s_i].date()}~{dates[e_i].date()}) ===")
        print(f"  A 基线        final={sA['final']:.4f} mdd={sA['mdd']:.2f}% "
              f"sharpe={sA['sharpe']:.2f} calmar={sA['calmar']:.2f} trades={sA['trades_n']}")
        print(f"  D3 (无闸门)   final={sD['final']:.4f} mdd={sD['mdd']:.2f}% "
              f"sharpe={sD['sharpe']:.2f} calmar={sD['calmar']:.2f} trades={sD['trades_n']}")

        # ---------- 闸门网格 ----------
        # thr: 广度阈值（<= thr 继续持币）；rec: 恢复到多少才解除
        combos = []
        for thr in (0, 1, 2, 3):
            for rec in (thr + 1, thr + 2, 5):
                if rec <= thr:
                    continue
                for mx in (0, 10, 20, 40):
                    combos.append((thr, rec, mx))
        for thr, rec, mx in combos:
            r, st = run(panel, dates, s_i, e_i, pool, defensive, sig,
                        loss_cool=THR, loss_days=COOL,
                        gate_breadth=thr, gate_recover=rec, gate_max_days=mx,
                        **base_kw)
            yA, yD = yearly(rA["navs"]), yearly(rD["navs"])
            yg = yearly(r["navs"])
            rec_row = dict(
                win=wname, thr=thr, rec=rec, max_days=mx,
                final=st["final"], mdd=st["mdd"], sharpe=st["sharpe"],
                calmar=st["calmar"], total=st["total"],
                trades=st["trades_n"], gate_days=st["gate_days"],
                cash_pct=st["cash_pct"], single_trig=st["single_trig"],
                d_final=sD["final"], d_mdd=sD["mdd"],
                d_sharpe=sD["sharpe"], d_calmar=sD["calmar"],
                a_final=sA["final"], a_mdd=sA["mdd"],
                vs_d3_final=st["final"] - sD["final"],
                vs_d3_mdd=st["mdd"] - sD["mdd"],
                vs_d3_calmar=st["calmar"] - sD["calmar"],
                yearly={y: dict(ret=yg[y]["ret"], mdd=yg[y]["mdd"],
                                d_ret=(yD.get(y, {}).get("ret"), yg[y]["ret"]),
                                a_mdd=yA.get(y, {}).get("mdd"),
                                d_mdd=yD.get(y, {}).get("mdd"))
                        for y in yg})
            results["grid"].append(rec_row)
            print(f"  闸门 thr<={thr} rec>={rec} max={mx or '∞':>2}: "
                  f"final={st['final']:.4f} mdd={st['mdd']:6.2f}% "
                  f"calmar={st['calmar']:.2f} gate天数={st['gate_days']:3d} "
                  f"| vs D3: final{st['final']-sD['final']:+.4f} "
                  f"mdd{st['mdd']-sD['mdd']:+.2f}pp")

        # ---------- 随机同频对照 ----------
        # 口径与 rot_stop3_study 一致：p = 真实触发次数 / 轮动持有日数
        print("  -- 随机同频对照 --")
        n_d3 = sD["single_trig"]
        rot_days = sum(1 for _d, t in rD["held_log"] if t != "CASH")
        p = n_d3 / max(rot_days, 1)
        rnd = []
        for seed in range(80):
            r, st = run(panel, dates, s_i, e_i, pool, defensive, sig,
                        rand_p=p, seed=seed, cool=COOL, stop_to="CASH", **base_kw)
            rnd.append(st["final"])
        rnd = np.array(rnd)
        # 闸门最好的一组 vs 随机分布
        gw = [g for g in results["grid"] if g["win"] == wname]
        gbest = max(gw, key=lambda g: g["final"]) if gw else None
        print(f"  随机 p={p:.4f} ({n_d3} 次/{rot_days} 日) 80 次: final 中位 {np.median(rnd):.4f} "
              f"p95 {np.percentile(rnd,95):.4f} / p05 {np.percentile(rnd,5):.4f}")
        if gbest:
            pct = float((rnd < gbest["final"]).mean() * 100)
            print(f"  闸门最佳 {gbest['thr']}/{gbest['rec']}/{gbest['max_days']}: "
                  f"final={gbest['final']:.4f} → 随机 {pct:.1f}% 分位"
                  f"｜ D3 原 {sD['final']:.4f} → 随机 {float((rnd<sD['final']).mean()*100):.1f}% 分位")
        results["random"].append(dict(win=wname, p=p, n_d3=n_d3, rot_days=rot_days,
                                      med=float(np.median(rnd)),
                                      p95=float(np.percentile(rnd, 95)),
                                      p5=float(np.percentile(rnd, 5)),
                                      d3_pctile=float((rnd < sD["final"]).mean() * 100),
                                      gate_best=(dict(thr=gbest["thr"], rec=gbest["rec"],
                                                      max_days=gbest["max_days"],
                                                      final=gbest["final"],
                                                      pctile=float((rnd < gbest["final"]).mean() * 100))
                                                 if gbest else None),
                                      finals=[float(x) for x in rnd]))

        # ---------- 归因：闸门期间被放弃的头名后续表现 ----------
        # 取最有代表性的 thr=1 组合
        r1, st1 = run(panel, dates, s_i, e_i, pool, defensive, sig,
                      loss_cool=THR, loss_days=COOL,
                      gate_breadth=1, gate_recover=2, **base_kw)
        cf = gate_counterfactual(panel, dates, r1, sig)
        if cf:
            f5 = np.array([x["fwd5"] for x in cf])
            f10 = np.array([x["fwd10"] for x in cf])
            print(f"  归因(thr=1): 闸门放弃 {len(cf)} 次 → 头名后 5 日 均 {f5.mean():+.2f}% "
                  f"中位 {np.median(f5):+.2f}% ；后 10 日 均 {f10.mean():+.2f}%")
            results["diag"][wname] = dict(
                thr1=dict(cases=cf, n=len(cf),
                          fwd5_mean=float(f5.mean()), fwd5_med=float(np.median(f5)),
                          fwd10_mean=float(f10.mean()), fwd10_med=float(np.median(f10)),
                          win5=float((f5 > 0).mean() * 100)))
        # MDD 时段对比
        s_, e_, m_ = mdd_window(rA["navs"])
        ds_, de_, dm_ = mdd_window(rD["navs"])
        gs_, ge_, gm_ = mdd_window(r1["navs"])
        print(f"  MDD 段: A {s_}~{e_} {m_:.2f}% ｜ D3 {ds_}~{de_} {dm_:.2f}% "
              f"｜ 闸门 {gs_}~{ge_} {gm_:.2f}%")
        results["diag"].setdefault(wname, {})["mdd_win"] = dict(
            A=[str(s_), str(e_), m_], D3=[str(ds_), str(de_), dm_],
            GATE1=[str(gs_), str(ge_), gm_])

        results["cases"].append(dict(
            win=wname, start=str(dates[s_i].date()), end=str(dates[e_i].date()),
            A=dict(final=sA["final"], mdd=sA["mdd"], sharpe=sA["sharpe"],
                   calmar=sA["calmar"], total=sA["total"], trades=sA["trades_n"],
                   curve=curve_of(rA["navs"])),
            D3=dict(final=sD["final"], mdd=sD["mdd"], sharpe=sD["sharpe"],
                    calmar=sD["calmar"], total=sD["total"], trades=sD["trades_n"],
                    curve=curve_of(rD["navs"]))))

    # JSON 不能有 NaN
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

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(_clean(results), f, ensure_ascii=False, indent=1)
    print(f"\n写出: {args.out}")


if __name__ == "__main__":
    main()
