"""「D3 止损后是否真的持续买入亏损」直接事件归因（2026-10-24）

用户假设（07:54 原话）：
  「广度闸门……目的是为了解决回测过程中提到的震荡市或者阴跌的情况持续买入亏损的情况」

⇒ 要检验的事实：**D3 止损触发之后（冷却期满重新入场），是不是真的在持续亏钱？**

方法（事件级，不用策略回测，避免干扰）：
  对每个 D3 触发事件（单笔亏 ≥3% → 空仓 5 日）：
    · 记录当日池内广度 n_pos（动横20 > 0 的只数）
    · 记录「冷却期满后重新入场」时买的那只标的，其后 5 / 10 / 20 日收益
    · 分层：广度差（n_pos<=1）/ 广度好（n_pos>=2）两组对比
  ⇒ 若「广度差」组在重新入场后明显更差 ⇒ 用户假设成立、闸门方向对；
    若两组差不多甚至广度差组更好 ⇒ 假设不成立，闸门在砍不该砍的。

再补一层：**震荡/阴跌段**的定义性检查 —— 用头名自身的 20 日路径判断。
"""
from __future__ import annotations

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
THR, COOL = 0.03, 5
WINDOWS = [("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
           ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05")]


def fwd(panel, code, i, k):
    """从第 i 天收盘到第 i+k 天收盘的收益（%）。"""
    if code not in panel.columns:
        return None
    j = min(i + k, len(panel) - 1)
    p0, p1 = float(panel[code].iloc[i]), float(panel[code].iloc[j])
    if p0 != p0 or p0 == 0 or p1 != p1:
        return None
    return (p1 / p0 - 1.0) * 100.0


def main():
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    base_kw = dict(sticky=1.0, enter_dev=12.0, enter_lead=15.0,
                   exit_dev=9.0, exit_lead=10.0, min_def_days=5)
    out = {}

    for wname, fname, start in WINDOWS:
        if not os.path.exists(os.path.join(HERE, fname)):
            continue
        panel, dates, s_i, e_i = rs.load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        sig = [bl.compute_signal(panel, pool, i, W, MA) for i in range(len(dates))]
        r = rs.simulate(panel, dates, s_i, e_i, pool, defensive, sig_list=sig,
                        loss_cool=THR, loss_days=COOL, **base_kw)

        # 找出所有 D3 触发事件
        trig = [t for t in r["trades"] if "本笔" in t["reason"] and t["to"] == "CASH"]
        print(f"\n=== {wname} ({dates[s_i].date()}~{dates[e_i].date()}) "
              f"D3 触发 {len(trig)} 次 ===")

        rows = []
        for t in trig:
            i = int(dates.searchsorted(pd.Timestamp(t["date"])))
            if i >= len(dates) - 1:
                continue
            s = sig[i]
            if s is None:
                continue
            n_pos = sum(1 for v in s["m"].values() if v > 0)
            n_list = len(s["m"])
            # 冷却期满后重新入场的那天 = i + COOL
            ir = min(i + COOL, len(dates) - 1)
            sr = sig[ir]
            head = sr["head"] if sr else None
            # 重新入场后头名的 5/10/20 日收益
            f5 = fwd(panel, head, ir, 5)
            f10 = fwd(panel, head, ir, 10)
            f20 = fwd(panel, head, ir, 20)
            # 被止损的那只标的，在止损日之后 5/10/20 日
            cut = t["frm"]
            c5 = fwd(panel, cut, i, 5)
            c10 = fwd(panel, cut, i, 10)
            rows.append(dict(date=t["date"], cut=cut, cut_name=NAMES.get(cut, cut),
                             n_pos=n_pos, n_list=n_list, breadth_ok=(n_pos >= 2),
                             reentry_head=head, reentry_name=NAMES.get(head, head),
                             reentry_f5=f5, reentry_f10=f10, reentry_f20=f20,
                             cut_f5=c5, cut_f10=c10))
        if not rows:
            continue
        d = pd.DataFrame(rows)
        d.to_json(os.path.join(HERE, "out", f"gate_events_{wname}.json"),
                  orient="records", force_ascii=False, indent=1)

        # ---- 汇总 ----
        def _stat(sub, col):
            v = sub[col].dropna()
            if not len(v):
                return "n/a"
            return f"n={len(v):2d} 均{v.mean():+6.2f}% 中位{v.median():+6.2f}% 胜{(v>0).mean()*100:3.0f}%"

        print("  【重新入场后头名表现】")
        for lab, sub in (("广度差 n_pos<=1", d[~d["breadth_ok"]]),
                         ("广度好 n_pos>=2", d[d["breadth_ok"]]),
                         ("全部", d)):
            print(f"    {lab:16s} 5日: {_stat(sub,'reentry_f5')}")
            print(f"    {'':16s} 10日: {_stat(sub,'reentry_f10')}")
            print(f"    {'':16s} 20日: {_stat(sub,'reentry_f20')}")
        print("  【被止损标的在止损后】")
        for lab, sub in (("广度差 n_pos<=1", d[~d["breadth_ok"]]),
                         ("广度好 n_pos>=2", d[d["breadth_ok"]])):
            print(f"    {lab:16s} 5日: {_stat(sub,'cut_f5')}   10日: {_stat(sub,'cut_f10')}")
        print("  【广度分布】")
        print("    " + "  ".join(f"n_pos={k}:{v}次" for k, v in
                                  d["n_pos"].value_counts().sort_index().items()))

        # ---- 关键：止损后重新入场的整体期望 ----
        allf = d["reentry_f10"].dropna()
        print(f"  ⇒ 止损后重新入场，整体 10 日 均 {allf.mean():+.2f}% 胜率 {(allf>0).mean()*100:.0f}%"
              f"（{len(allf)} 次）")
        # 按广度二分
        lo = d[~d["breadth_ok"]]["reentry_f10"].dropna()
        hi = d[d["breadth_ok"]]["reentry_f10"].dropna()
        print(f"  ⇒ 广度差组 10 日 均 {lo.mean() if len(lo) else float('nan'):+.2f}% "
              f"(n={len(lo)}) vs 广度好组 {hi.mean() if len(hi) else float('nan'):+.2f}% (n={len(hi)})")

        out[wname] = dict(
            n_trig=len(d),
            breadth_dist={int(k): int(v) for k, v in d["n_pos"].value_counts().items()},
            reentry=dict(
                all_f5=_stat(d, "reentry_f5"), all_f10=_stat(d, "reentry_f10"),
                all_f20=_stat(d, "reentry_f20"),
                lo_f5=_stat(d[~d["breadth_ok"]], "reentry_f5"),
                lo_f10=_stat(d[~d["breadth_ok"]], "reentry_f10"),
                lo_f20=_stat(d[~d["breadth_ok"]], "reentry_f20"),
                hi_f5=_stat(d[d["breadth_ok"]], "reentry_f5"),
                hi_f10=_stat(d[d["breadth_ok"]], "reentry_f10"),
                hi_f20=_stat(d[d["breadth_ok"]], "reentry_f20"),
                lo_f10_mean=float(lo.mean()) if len(lo) else None,
                hi_f10_mean=float(hi.mean()) if len(hi) else None,
                all_f10_mean=float(allf.mean()) if len(allf) else None),
            cut=dict(lo_f5=_stat(d[~d["breadth_ok"]], "cut_f5"),
                     hi_f5=_stat(d[d["breadth_ok"]], "cut_f5")),
            events=rows)

    with open(os.path.join(HERE, "out", "gate_attrib.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n写出: out/gate_attrib.json + out/gate_events_*.json")


if __name__ == "__main__":
    main()
