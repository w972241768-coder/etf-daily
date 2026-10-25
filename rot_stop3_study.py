"""「近 N 笔累计亏损 → 空仓 5 日」对比研究（2026-10-24 v2）

用户口径（04:10 原话）：
  「最近 3 次换仓累计亏损超 3% 后（如果单笔超 3% 就没必要等后面了，如果两笔超 3% 也就没必要
    等后面了，如果买入第三笔加上前两笔超 3% 也停），空仓 5 日，再次买入排名第一的。」

⇒ 触发条件 = 最近 1 / 2 / 3 笔的**任一前缀**累计 ≤ −3%（`loss3_prefix=True, window=3`）。
  注意：这天然包含 D3（单笔 ≥3%）作为 k=1 的特例，所以 EP 的触发次数必然 ≥ D3。

⚠️ v1（03:40 的旧产物）只判「凑满 3 笔的整窗累计」（`loss3_prefix=False`），
   漏掉了「单笔超 3% 立即停」这一半，两者结论方向不同。本版把两个口径都跑，并标明区别。
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
COOL = 5          # 空仓天数（用户指定）
THR = 0.03        # 累计亏损阈值
WIN = 3           # 回看笔数


def run(panel, dates, s_i, e_i, pool, defensive, sig, cost=0.002, **kw):
    r = rs.simulate(panel, dates, s_i, e_i, pool, defensive,
                    buy=cost / 2, sell=cost / 2, sig_list=sig, **kw)
    st = rs.summarize(r["navs"])
    tr = r["trades"]
    n_single = len([t for t in tr if "本笔" in t["reason"] and t["to"] == "CASH"])
    n_cum = len([t for t in tr if "笔累计" in t["reason"] and t["to"] == "CASH"])
    st.update(trades_n=len(tr), single_trig=n_single, cum_trig=n_cum,
              trig=n_single + n_cum, cool_days=r["cool3_hits"] + r["cool_hits"],
              cash_pct=r["cash_days"] / len(r["navs"]) * 100)
    return r, st


def curve_of(nav, step=3):
    idx = list(range(0, len(nav), step))
    if idx[-1] != len(nav) - 1:
        idx.append(len(nav) - 1)
    return dict(d=[nav.index[i] for i in idx],
                v=[round(float(nav.iloc[i]), 5) for i in idx])


def trigger_study(panel, dates, sig, r, key):
    """触发时：头名此前 5 日已涨多少、此后 5 日又涨多少（判断是不是按在反弹起点）。"""
    ev = [t for t in r["trades"] if key in t["reason"] and t["to"] == "CASH"]
    fw, prev = [], []
    for t in ev:
        i = panel.index.get_loc(pd.Timestamp(t["date"]))
        s_ = sig.get(i)
        if not s_:
            continue
        h = s_["head"]
        p0 = float(panel[h].iloc[i])
        j = min(i + 5, len(panel) - 1)
        p1 = float(panel[h].iloc[j])
        if p0 == p0 and p0:
            fw.append((p1 / p0 - 1) * 100)
            if i >= 5:
                p5 = float(panel[h].iloc[i - 5])
                if p5 == p5 and p5:
                    prev.append((p0 / p5 - 1) * 100)

    def agg(a):
        if not a:
            return dict(n=0)
        a = np.array(a)
        return dict(n=len(a), avg=float(a.mean()), med=float(np.median(a)),
                    pos=float((a > 0).mean() * 100))
    return dict(n=len(ev), fwd5=agg(fw), prior5=agg(prev))


def seg_stats(nav, lo, hi):
    x = nav[(nav.index >= lo) & (nav.index <= hi)]
    if len(x) < 3:
        return dict(total=None, mdd=None)
    st = bl.stats(x)
    return dict(total=st["total"] * 100, mdd=st["mdd"] * 100,
                sharpe=st["sharpe"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(HERE, "out", "rot_stop3.json"))
    ap.add_argument("--sims", type=int, default=80, help="随机对照模拟次数")
    a = ap.parse_args()

    rot, deff, _ = bl.read_pools()
    defensive = deff[0]

    CORE = [
        ("A",   "A 基线（不加减速）", {}),
        ("D3",  "D3 单笔亏损≥3% → 空仓5日", dict(loss_cool=THR, loss_days=COOL)),
        ("EP",  f"EP 近{WIN}笔任一前缀≥3% → 空仓5日（你的口径）",
         dict(loss3_cool=THR, loss3_days=COOL, loss3_window=WIN, loss3_prefix=True)),
        ("EP2", f"EP2 同上但不重置窗口",
         dict(loss3_cool=THR, loss3_days=COOL, loss3_window=WIN, loss3_prefix=True,
              loss3_reset=False)),
        ("E",   f"E 近{WIN}笔整窗累计≥3%（旧口径·非你的规则）",
         dict(loss3_cool=THR, loss3_days=COOL, loss3_window=WIN, loss3_prefix=False)),
    ]

    out = dict(meta=dict(date="2026-10-24", version="v2",
                         cost=0.002, pool=rot, defensive=defensive,
                         rule=dict(window=WIN, thr=THR, cool=COOL, prefix=True),
                         note="EP 触发 = 最近 1/2/3 笔任一前缀累计 ≤ −3%；"
                              "E（v1）只判满 3 笔整窗，不含单笔即时触发"),
               win={}, core={}, scan={}, diag={})

    for key, fname, start, label in rs.WINDOWS:
        panel, dates, s_i, e_i = rs.load_win(fname, start)
        sig = {i: bl.compute_signal(panel, rot, i, W, MA) for i in range(s_i, e_i)}
        print(f"\n{'=' * 92}\n{label}   {e_i - s_i} 个收益日")
        core, res = {}, {}
        print(f"  {'方案':<34}{'净值':>9}{'收益%':>10}{'年化%':>9}{'MDD%':>9}"
              f"{'夏普':>7}{'Calmar':>8}{'触发':>6}{'空仓%':>7}{'换仓':>6}")
        for tag, lab, kw in CORE:
            r, s = run(panel, dates, s_i, e_i, rot, defensive, sig, **kw)
            res[tag] = r
            core[tag] = dict(label=lab, kw={k: v for k, v in kw.items()}, stat=s,
                             curve=curve_of(r["navs"]),
                             dd=rs.dd_episodes(r["navs"], r["held_log"], n=3))
            print(f"  {lab:<34}{s['final']:>9.4f}{s['total']:>+10.2f}{s['ann']:>+9.2f}"
                  f"{s['mdd']:>9.2f}{(s['sharpe'] or 0):>7.2f}{(s['calmar'] or 0):>8.2f}"
                  f"{s['trig']:>6}{s['cash_pct']:>6.1f}%{s['trades_n']:>6}")

        # ---- 触发时点特征 ----
        print("\n  【触发时点】头名此前 5 日 / 之后 5 日")
        for tag in ("D3", "EP"):
            ts = trigger_study(panel, dates, sig, res[tag],
                               "本笔" if tag == "D3" else "笔累计")
            core[tag]["trig_study"] = ts
            print(f"    {tag:<4} n={ts['n']:>3}  前 5 日 {ts['prior5'].get('avg', 0):+6.2f}%"
                  f"  后 5 日 {ts['fwd5'].get('avg', 0):+6.2f}%"
                  f"（正比例 {ts['fwd5'].get('pos', 0):.0f}%）")

        # ---- 扫描 S1：窗口 × 阈值 × 是否重置（前缀口径）----
        print("\n  【S1】前缀口径：近 N 笔 × 阈值 × 是否重置（空仓 5 日）")
        print(f"    {'近N笔':<8}{'阈值':<8}{'重置':<6}{'净值':>9}{'收益%':>10}{'MDD%':>9}{'触发':>6}")
        s1 = []
        for win in (2, 3, 4, 5):
            for thr in (0.02, 0.025, 0.03, 0.035, 0.04):
                for reset in (True, False):
                    _r, s = run(panel, dates, s_i, e_i, rot, defensive, sig,
                                loss3_cool=thr, loss3_days=COOL, loss3_window=win,
                                loss3_prefix=True, loss3_reset=reset)
                    s1.append(dict(win=win, thr=thr, reset=reset, stat=s))
                    print(f"    {win:<8}{thr*100:>5.1f}%  {str(reset):<6}{s['final']:>9.4f}"
                          f"{s['total']:>+10.2f}{s['mdd']:>9.2f}{s['trig']:>6}")

        # ---- 扫描 S2：空仓天数 ----
        print("  【S2】EP 的空仓天数")
        s2 = []
        for dd_ in (3, 4, 5, 6, 7, 10):
            _r, s = run(panel, dates, s_i, e_i, rot, defensive, sig,
                        loss3_cool=THR, loss3_days=dd_, loss3_window=WIN,
                        loss3_prefix=True)
            s2.append(dict(days=dd_, stat=s))
            print(f"    {dd_} 日{'':<18}{s['final']:>9.4f}{s['total']:>+10.2f}"
                  f"{s['mdd']:>9.2f}{s['trig']:>6}")

        # ---- 扫描 S3：旧口径（整窗）对照 ----
        print("  【S3】旧口径（只判满 N 笔整窗，不含单笔即时触发）")
        s3 = []
        for win in (2, 3, 4):
            for thr in (0.02, 0.03, 0.04):
                _r, s = run(panel, dates, s_i, e_i, rot, defensive, sig,
                            loss3_cool=thr, loss3_days=COOL, loss3_window=win,
                            loss3_prefix=False)
                s3.append(dict(win=win, thr=thr, stat=s))
                print(f"    近{win}笔 ≥{thr*100:>4.1f}%{'':<10}{s['final']:>9.4f}"
                      f"{s['total']:>+10.2f}{s['mdd']:>9.2f}{s['trig']:>6}")

        # ---- 成本 ----
        cost_rows = []
        for c in (0.001, 0.002, 0.003):
            row = {}
            for tag, _lab, kw in CORE:
                _r, s = run(panel, dates, s_i, e_i, rot, defensive, sig, cost=c, **kw)
                row[tag] = dict(final=s["final"], mdd=s["mdd"], total=s["total"])
            cost_rows.append(dict(cost=c, **row))
        out["scan"].setdefault(key, {})["cost"] = cost_rows
        print("  【成本】" + "  ".join(
            f"{c['cost'] * 100:.1f}%: A {c['A']['final']:.3f}/D3 {c['D3']['final']:.3f}"
            f"/EP {c['EP']['final']:.3f}" for c in cost_rows))

        # ---- 逐年 ----
        yr = {}
        for tag, _lab, kw in CORE:
            yr[tag] = {x["year"]: x["ret"] * 100 for x in bl.yearly(res[tag]["navs"])}
        out["scan"].setdefault(key, {})["yearly"] = yr
        yrs = sorted({y for v in yr.values() for y in v})
        print("\n  【逐年】")
        print("    " + "".join(f"{y:>9}" for y in yrs))
        for tag, _l, _k in CORE:
            print(f"    {tag:<6}" + "".join(
                (f"{yr[tag][y]:>+8.1f}%" if y in yr[tag] else f"{'-':>9}") for y in yrs))

        # ---- 分段：2015-2018（长窗独有）与 2019-2026（两窗共同）----
        SEGS = [("2015-2018", "2015-01-05", "2019-12-30"),
                ("2019-2026", "2019-12-31", "2026-09-24")]
        seg = {}
        for tag, _lab, kw in CORE:
            seg[tag] = {nm: seg_stats(res[tag]["navs"], lo, hi)
                        for nm, lo, hi in SEGS}
        out["scan"].setdefault(key, {})["seg"] = dict(
            defs=[dict(name=n, lo=l, hi=h) for n, l, h in SEGS], data=seg)
        print("\n  【分段】两窗共同的只有 2019-2026 段")
        for tag, _l, _k in CORE:
            s19 = seg[tag]["2019-2026"]
            s15 = seg[tag]["2015-2018"] if fname.startswith("tdx_adj_long_2015") else None
            line = f"    {tag:<6} 2019-2026 {s19['total']:+8.2f}% (MDD {s19['mdd']:6.2f}%)"
            if s15 and s15["total"] is not None:
                line += f"  ｜ 2015-2018 {s15['total']:+8.2f}% (MDD {s15['mdd']:6.2f}%)"
            print(line)

        # ---- 随机频率对照（用真实触发次数）----
        rnd = {}
        for tag in ("D3", "EP"):
            s_real = core[tag]["stat"]
            rot_days = sum(1 for _d, t in res[tag]["held_log"] if t != "CASH")
            p = s_real["trig"] / max(rot_days, 1)
            fs = []
            for sd in range(a.sims):
                _r, _s = run(panel, dates, s_i, e_i, rot, defensive, sig,
                             rand_p=p, seed=sd, cool=COOL, stop_to="CASH")
                fs.append(_s["final"])
            arr = np.array(fs)
            rnd[tag] = dict(events=s_real["trig"], p=p, real=s_real["final"],
                            real_mdd=s_real["mdd"], base=core["A"]["stat"]["final"],
                            p05=float(np.percentile(arr, 5)),
                            p50=float(np.median(arr)),
                            p95=float(np.percentile(arr, 95)),
                            pctile=float((arr < s_real["final"]).mean() * 100),
                            beat_base=float((arr < core["A"]["stat"]["final"]).mean() * 100))
            print(f"  【随机对照 {tag}】{s_real['trig']} 次 / {rot_days} 日 → p={p*100:.2f}%/日"
                  f" ｜ 真实 {s_real['final']:.4f} vs 随机中位 {np.median(arr):.4f}"
                  f" p95 {np.percentile(arr, 95):.4f} → {rnd[tag]['pctile']:.1f}% 分位")
        out["scan"].setdefault(key, {})["rand"] = rnd

        out["win"][key] = dict(label=label, curve_step=3)
        out["core"][key] = core
        out["scan"][key].update(dict(s1=s1, s2=s2, s3=s3))

    os.makedirs(os.path.dirname(a.json), exist_ok=True)
    with open(a.json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[json] {a.json}")


if __name__ == "__main__":
    main()
