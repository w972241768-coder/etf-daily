"""「全池动横20 均为负 → 切换空仓」广度闸门研究（2026-10-25）

用户设想：震荡下行时池内所有品种动横20 < 0 ⇒ 空仓躲掉「每笔小亏累积」；
         广度恢复（至少 1 只动横20 > 0）再按当日头名入场。

与已证伪的「广度止损」区别：那个是随时砍已有仓位；这个是**择时空仓**
（改「能不能在场」，不改选股与卖出条件），且阈值取最严的 n_pos == 0。

载体：基线 / D3 / P3(仅 ROC60) / D3+P3 叠加
闸门：G0 n_pos<=1(历史证伪版，作参照) / G1 n_pos==0 / G2 连续2日 / G3 连续3日
窗口：主窗 2019-12-31 起、长窗 2015-01-05 起；单边万一；初始净值 1
对照：① 随机同频（整体打散 + 保持段长结构两种）② 空仓占比 ③ 事件归因
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import etf_backtest_long as bl          # noqa: E402
from rot_stop_study import load_win     # noqa: E402

COST = 0.0001                           # 单边万一
BASE = dict(sticky=1.0, enter_dev=12.0, enter_lead=15.0,
            exit_dev=9.0, exit_lead=10.0, min_def_days=5,
            W=20, MA=20, value_engine=True)

CARRIERS = [
    ("基线",   dict()),
    ("D3",     dict(loss_cool=0.03, loss_days=5)),
    ("P3",     dict(roc_q=0.95, roc_days=20, roc_n=60, roc_min_periods=250)),
    ("D3+P3",  dict(loss_cool=0.03, loss_days=5,
                    roc_q=0.95, roc_days=20, roc_n=60, roc_min_periods=250)),
]

WINDOWS = [("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
           ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05")]

RND_N = 40


def npos_series(panel, pool, dates, sig_all):
    """每日池内动横20 > 0 的只数（NaN 不计）。"""
    out = np.zeros(len(dates), dtype=float)
    for i in range(len(dates)):
        s = sig_all[i]
        if s is None:
            out[i] = np.nan
            continue
        out[i] = sum(1 for v in s["m"].values() if v == v and v > 0)
    return pd.Series(out, index=dates)


def gate_mask_from(npos, mode):
    """mode: 'le1' | 'eq0' | 'eq0x2' | 'eq0x3'"""
    m = pd.Series(False, index=npos.index)
    valid = npos == npos
    if mode == "le1":
        m = valid & (npos <= 1)
    elif mode == "eq0":
        m = valid & (npos == 0)
    elif mode in ("eq0x2", "eq0x3"):
        k = 2 if mode == "eq0x2" else 3
        base = valid & (npos == 0)
        acc = base.copy()
        for j in range(1, k):                 # 今日 + 前 j 日均为 0
            acc = acc & base.shift(j).fillna(False)
        m = acc
    return m.fillna(False)


def spells(mask):
    """连续 True 段的 (起始下标, 长度) 列表。"""
    v = mask.values.astype(bool)
    idx = np.where(v)[0]
    if len(idx) == 0:
        return []
    brk = np.where(np.diff(idx) > 1)[0]
    starts = np.concatenate([[idx[0]], idx[brk + 1]])
    ends = np.concatenate([idx[brk], [idx[-1]]])
    return [(int(a), int(b - a + 1)) for a, b in zip(starts, ends)]


def shuffled_mask(mask, seed, keep_blocks):
    """同频随机对照：保持 True 总天数（keep_blocks=True 时还保持段长结构）。"""
    rng = np.random.default_rng(seed)
    n = len(mask)
    if keep_blocks:
        sp = spells(mask)
        if not sp:
            return mask.copy()
        out = np.zeros(n, dtype=bool)
        lens = [L for _, L in sp]
        # 随机选起点，段不重叠、不越界
        cand = rng.permutation(n)
        used = np.zeros(n, dtype=bool)
        for L in lens:
            placed = False
            for s in cand:
                if s + L <= n and not used[s:s + L].any():
                    used[s:s + L] = True
                    out[s:s + L] = True
                    placed = True
                    break
            if not placed:
                break
        return pd.Series(out, index=mask.index)
    idx = np.where(mask.values.astype(bool))[0]
    if len(idx) == 0:
        return mask.copy()
    pick = rng.choice(np.arange(n), size=len(idx), replace=False)
    out = np.zeros(n, dtype=bool)
    out[pick] = True
    return pd.Series(out, index=mask.index)


def stats_of(navs, cost):
    st = bl.stats(navs)
    return dict(final=float(navs.iloc[-1]), total=st["total"] * 100,
                mdd=st["mdd"] * 100, sharpe=st["sharpe"],
                calmar=st["calmar"], cost=cost)


def run(panel, dates, s_i, e_i, pool, defensive, sig_all, carrier_kw,
        gate=None, **kw):
    return bl.backtest(panel, dates, s_i, e_i, pool, defensive,
                       buy=COST, sell=COST, sig_list=sig_all,
                       **BASE, **carrier_kw,
                       **({"gate_mask": gate} if gate is not None else {}),
                       **kw)


def main():
    out = dict(windows={}, grid=[], attrib={}, random=[])
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]

    for wname, fname, start in WINDOWS:
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        sig_all = [bl.compute_signal(panel, pool, i, BASE["W"], BASE["MA"])
                   for i in range(len(dates))]
        npos = npos_series(panel, pool, dates, sig_all)
        n_valid = int((npos == npos).sum())
        d0 = int((npos == 0).sum())
        d_le1 = int((npos <= 1).sum())
        print(f"\n{'#'*100}")
        print(f"# 窗口 {wname}: {dates[s_i].date()} ~ {dates[e_i].date()}  "
              f"({e_i - s_i} 交易日)  池内可用 {len(pool)} 只")
        print(f"#   全池动横20<0 的天数: {d0} ({d0/max(1,n_valid)*100:.1f}%)   "
              f"n_pos<=1 的天数: {d_le1} ({d_le1/max(1,n_valid)*100:.1f}%)")
        out["windows"][wname] = dict(
            start=str(dates[s_i].date()), end=str(dates[e_i].date()),
            n_days=int(e_i - s_i), n_pool=len(pool),
            days_eq0=d0, pct_eq0=round(d0 / max(1, n_valid) * 100, 2),
            days_le1=d_le1, pct_le1=round(d_le1 / max(1, n_valid) * 100, 2))

        # ---------- 基线载体（无闸门） ----------
        base_r = {}
        for cname, ckw in CARRIERS:
            r = run(panel, dates, s_i, e_i, pool, defensive, sig_all, ckw)
            base_r[cname] = r
            st = stats_of(r["navs"], COST)
            print(f"  {cname:<6} (无闸门) final={st['final']:.4f} "
                  f"mdd={st['mdd']:.2f}% calmar={st['calmar']:.2f} "
                  f"trades={len(r['trades'])}")

        # ---------- 闸门网格 ----------
        for gname in ("le1", "eq0", "eq0x2", "eq0x3"):
            gmask = gate_mask_from(npos, gname)
            gd = int(gmask.sum())
            sp = spells(gmask)
            mean_len = float(np.mean([L for _, L in sp])) if sp else 0.0
            for cname, ckw in CARRIERS:
                r = run(panel, dates, s_i, e_i, pool, defensive, sig_all, ckw,
                        gate=gmask)
                st = stats_of(r["navs"], COST)
                b = base_r[cname]
                bs = stats_of(b["navs"], COST)
                rec = dict(win=wname, gate=gname, carrier=cname,
                           final=st["final"], mdd=st["mdd"],
                           sharpe=st["sharpe"], calmar=st["calmar"],
                           total=st["total"], trades=len(r["trades"]),
                           gate_days=r.get("gate_days", 0),
                           gate_trig=r.get("gate_trig", 0),
                           base_final=bs["final"], base_mdd=bs["mdd"],
                           base_calmar=bs["calmar"],
                           d_final=st["final"] - bs["final"],
                           d_mdd=st["mdd"] - bs["mdd"],
                           d_calmar=st["calmar"] - bs["calmar"],
                           n_spells=len(sp), mean_spell=mean_len)
                out["grid"].append(rec)
                print(f"    闸门 {gname:<6} × {cname:<6}: final={st['final']:.4f} "
                      f"({st['final']-bs['final']:+.4f}) mdd={st['mdd']:6.2f}% "
                      f"({st['mdd']-bs['mdd']:+.2f}pp) calmar={st['calmar']:.2f} "
                      f"| 闸门{gd}日/{len(sp)}段(均{mean_len:.1f}日) "
                      f"trades={len(r['trades'])}")

        # ---------- 随机同频对照（只对 eq0 闸门） ----------
        gmask = gate_mask_from(npos, "eq0")
        for cname, ckw in CARRIERS:
            for tag, kb in (("打散", False), ("保段", True)):
                arr = []
                for seed in range(RND_N):
                    gm = shuffled_mask(gmask, seed, kb)
                    r = run(panel, dates, s_i, e_i, pool, defensive, sig_all,
                            ckw, gate=gm)
                    arr.append(stats_of(r["navs"], COST))
                arr_f = np.array([a["final"] for a in arr])
                arr_m = np.array([a["mdd"] for a in arr])
                real = run(panel, dates, s_i, e_i, pool, defensive, sig_all,
                           ckw, gate=gmask)
                rs = stats_of(real["navs"], COST)
                beat_f = float((arr_f < rs["final"]).mean() * 100)
                beat_m = float((arr_m < rs["mdd"]).mean() * 100)
                out["random"].append(dict(
                    win=wname, carrier=cname, kind=tag, n=RND_N,
                    real_final=rs["final"], real_mdd=rs["mdd"],
                    rnd_med_f=float(np.median(arr_f)),
                    rnd_p95_f=float(np.percentile(arr_f, 95)),
                    rnd_p05_f=float(np.percentile(arr_f, 5)),
                    rnd_med_m=float(np.median(arr_m)),
                    rnd_p95_m=float(np.percentile(arr_m, 95)),
                    rnd_p05_m=float(np.percentile(arr_m, 5)),
                    beat_final_pct=beat_f, beat_mdd_pct=beat_m))
                print(f"    [随机{tag}] {cname:<6}: 真实final={rs['final']:.4f} "
                      f"vs 随机中位{np.median(arr_f):.4f} → final优于{beat_f:.0f}% "
                      f"mdd优于{beat_m:.0f}%")

        # ---------- 事件归因：闸门生效时 vs 无效时，头名未来 20 日表现 ----------
        rows = []
        for i in range(s_i, min(len(dates) - 21, e_i)):
            s = sig_all[i]
            if s is None:
                continue
            h = s["head"]
            p0 = float(panel[h].iloc[i])
            p20 = float(panel[h].iloc[min(i + 20, len(dates) - 1)])
            if p0 != p0 or p0 == 0 or p20 != p20:
                continue
            g = bool(gmask.iloc[i]) if i < len(gmask) else False
            seg = panel[h].iloc[i:i + 21].dropna()
            mdd20 = (seg.min() / p0 - 1) * 100 if len(seg) else np.nan
            rows.append(dict(gate=g, fwd20=(p20 / p0 - 1) * 100, mdd20=mdd20))
        d = pd.DataFrame(rows)
        at = {}
        for g in (True, False):
            sub = d[d["gate"] == g]
            if len(sub):
                at["on" if g else "off"] = dict(
                    n=int(len(sub)), fwd20_mean=float(sub["fwd20"].mean()),
                    fwd20_med=float(sub["fwd20"].median()),
                    win=float((sub["fwd20"] > 0).mean() * 100),
                    mdd20_mean=float(sub["mdd20"].mean()))
        out["attrib"][wname] = at
        print(f"  [归因] 闸门期 n={at.get('on',{}).get('n',0)}: 头名后20日 "
              f"均{at.get('on',{}).get('fwd20_mean',float('nan')):+.2f}% "
              f"胜{at.get('on',{}).get('win',float('nan')):.0f}% "
              f"最深{at.get('on',{}).get('mdd20_mean',float('nan')):.2f}% ｜ "
              f"非闸门期 n={at.get('off',{}).get('n',0)}: "
              f"均{at.get('off',{}).get('fwd20_mean',float('nan')):+.2f}% "
              f"胜{at.get('off',{}).get('win',float('nan')):.0f}%")

    with open(os.path.join(HERE, "out", "allneg_gate.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print("\n[OK] out/allneg_gate.json")


if __name__ == "__main__":
    main()
