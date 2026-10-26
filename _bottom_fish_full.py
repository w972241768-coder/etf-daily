#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抄底策略「全池负 → 当日换末位」全方位回测（2026-10-26）

在上产口径 D3+P3 之上叠加 A1，回答：能否产生正反馈？
维度：
  ① 五个窗口（长窗 8y / 主窗 6.7y / 近5y / 近3y / 近1y）
  ② 逐年对比
  ③ 47 次触发明细（买入后 20/60 日表现）
  ④ 成本敏感度（万一 / 万三 / 千一）
  ⑤ 稳健性网格（选股位置 × 买入延迟 × 触发阈值）
⚠️ 本地研究脚本，不入库。
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import etf_backtest_long as bl  # noqa: E402

BASE = bl.LIVE_COMPOSITE
A1 = dict(weak_mode="allneg", weak_rank=1, weak_delay=0)


def load():
    panel = bl.read_panel(os.path.join(HERE, "tdx_adj_long_2019_2026.csv"))
    rot, deff, _w = bl.read_pools()
    pool = [c for c in rot if c in panel.columns]
    return panel, panel.index, pool, (deff[0] if deff else "512040")


def run(panel, dates, pool, dfn, s_date, cost=0.0001, **kw):
    si = max(int(dates.searchsorted(pd.Timestamp(s_date), side="left")), 0)
    p = dict(BASE)
    p.update(kw)
    r = bl.backtest(panel, dates, si, len(dates) - 1, pool, dfn,
                    buy=cost, sell=cost, W=20, MA=20, value_engine=True, **p)
    st = bl.stats(r["navs"])
    return r, st


def line(tag, r, st, b_final, b_mdd, w=22):
    d = r["final"] - b_final
    dm = (st["mdd"] - b_mdd) * 100
    return (f"{tag:{w}s} {r['final']:8.4f} {d:+8.4f} {st['ann']*100:7.2f}% "
            f"{st['mdd']*100:7.2f}% {dm:+6.2f}pp {st['calmar'] or 0:5.2f} "
            f"{st['sharpe'] or 0:5.2f} {st['sortino'] or 0:5.2f} "
            f"{len(r['trades']):4d} {r['cash_days']:5d}")


def head(w=22):
    return (f"{'方案':{w}s} {'净值':>8} {'Δ净值':>8} {'年化':>8} "
            f"{'MDD':>8} {'ΔMDD':>9} {'Calmar':>5} {'Sharpe':>5} {'Sortino':>7} "
            f"{'换仓':>4} {'空仓':>4}")


def main():
    panel, dates, pool, dfn = load()
    print(f"数据 {dates[0]:%Y-%m-%d} → {dates[-1]:%Y-%m-%d}（{len(dates)} 根）｜ 池 {len(pool)} 只")
    print(f"生产口径 D3+P3 = {BASE}")

    # ---------- ① 多窗口 ----------
    print("\n" + "=" * 132)
    print("① 多窗口对比（基线 = 同窗口的 D3+P3，满仓、单边万一）")
    wins = [("长窗 8y", "2018-12-03"), ("主窗 6.7y", "2019-12-31"),
            ("近5年", "2021-10-25"), ("近3年", "2023-10-26"), ("近1年", "2025-10-26")]
    for wn, sd in wins:
        rb, sb = run(panel, dates, pool, dfn, sd)
        ra, sa = run(panel, dates, pool, dfn, sd, **A1)
        print(f"\n-- {wn}（{sd} 起）")
        print(head())
        print(line("基线 D3+P3", rb, sb, rb["final"], sb["mdd"]))
        print(line("+抄底(全池负→末位)", ra, sa, rb["final"], sb["mdd"]))
        print(f"{'':22s} 触发 {len(ra.get('weak_events') or [])} 次")

    # ---------- ② 逐年 ----------
    print("\n" + "=" * 132)
    print("② 逐年对比（长窗 8y，收益 % / 括号内为该年最大回撤 %）")
    rb, _ = run(panel, dates, pool, dfn, "2018-12-03")
    ra, _ = run(panel, dates, pool, dfn, "2018-12-03", **A1)
    yb = {d["year"]: d for d in bl.yearly(rb["navs"])}
    ya = {d["year"]: d for d in bl.yearly(ra["navs"])}
    print(f"{'年份':>6} {'基线收益':>9} {'基线MDD':>9} {'+抄底收益':>10} {'+抄底MDD':>10} {'差值':>9} 判定")
    print("-" * 76)
    win = 0
    for y in sorted(set(yb) | set(ya)):
        b, a = yb.get(y, {}), ya.get(y, {})
        br, ar = (b.get("ret") or 0) * 100, (a.get("ret") or 0) * 100
        bm, am = (b.get("mdd") or 0) * 100, (a.get("mdd") or 0) * 100
        dd = ar - br
        win += 1 if dd > 0.05 else 0
        flag = "✔" if dd > 0.05 else ("—" if abs(dd) <= 0.05 else "✘")
        print(f"{y:>6} {br:8.1f}% {bm:8.1f}% {ar:9.1f}% {am:9.1f}% {dd:+8.1f}pp   {flag}")
    print(f"跑赢 {win}/{len(yb)} 年")

    # ---------- ③ 触发明细 ----------
    print("\n" + "=" * 132)
    print("③ 触发明细（长窗）：买入后 20 / 60 个交易日的该标的收益")
    ra, _ = run(panel, dates, pool, dfn, "2018-12-03", **A1)
    ev = ra.get("weak_events") or []
    recs = []
    for e in ev:
        try:
            i = int(dates.searchsorted(pd.Timestamp(e["date"]), side="left"))
        except Exception:                      # noqa: BLE001
            continue
        c = e["code"]
        if c not in panel.columns:
            continue
        s = panel[c]
        p0 = s.iloc[i]
        if pd.isna(p0) or p0 == 0:
            continue
        r20 = (s.iloc[i + 20] / p0 - 1) * 100 if i + 20 < len(s) else np.nan
        r60 = (s.iloc[i + 60] / p0 - 1) * 100 if i + 60 < len(s) else np.nan
        recs.append((e["date"], c, e["name"], e["m"], r20, r60))
    if recs:
        a20 = np.array([x[4] for x in recs if x[4] == x[4]], dtype=float)
        a60 = np.array([x[5] for x in recs if x[5] == x[5]], dtype=float)
        print(f"共 {len(recs)} 次")
        print(f"  买入后 20 日：中位 {np.median(a20):+.2f}%  均值 {a20.mean():+.2f}%  "
              f"胜率 {(a20 > 0).mean()*100:.0f}%  （n={len(a20)}）")
        print(f"  买入后 60 日：中位 {np.median(a60):+.2f}%  均值 {a60.mean():+.2f}%  "
              f"胜率 {(a60 > 0).mean()*100:.0f}%  （n={len(a60)}）")
        print(f"\n{'日期':>12} {'标的':8} {'名称':16} {'动横20':>8} {'+20日':>8} {'+60日':>8}")
        print("-" * 66)
        for d, c, n, m, r20, r60 in recs[-15:]:
            print(f"{d:>12} {c:8} {n[:14]:16} {m:7.1f}% "
                  f"{(f'{r20:+.1f}%' if r20 == r20 else '—'):>8} "
                  f"{(f'{r60:+.1f}' if r60 == r60 else '—'):>8}")

    # ---------- ④ 成本敏感度 ----------
    print("\n" + "=" * 132)
    print("④ 成本敏感度（长窗 8y）")
    print(f"{'单边成本':>10} {'基线净值':>10} {'+抄底净值':>10} {'Δ净值':>9} {'基线MDD':>9} {'+抄底MDD':>9}")
    print("-" * 66)
    for c, lab in ((0.0001, "万一"), (0.0003, "万三"), (0.001, "千一")):
        rb2, sb2 = run(panel, dates, pool, dfn, "2018-12-03", cost=c)
        ra2, sa2 = run(panel, dates, pool, dfn, "2018-12-03", cost=c, **A1)
        print(f"{lab:>10} {rb2['final']:10.4f} {ra2['final']:10.4f} "
              f"{ra2['final']-rb2['final']:+9.4f} {sb2['mdd']*100:8.2f}% {sa2['mdd']*100:8.2f}%")

    # ---------- ⑤ 稳健性网格 ----------
    print("\n" + "=" * 132)
    print("⑤ 稳健性网格（长窗 8y，Δ净值 vs 基线）")
    rb3, sb3 = run(panel, dates, pool, dfn, "2018-12-03")
    print("\n-- 买第几弱（触发=全池负，当日换）")
    for rk in (1, 2, 3):
        r, s = run(panel, dates, pool, dfn, "2018-12-03",
                   weak_mode="allneg", weak_rank=rk, weak_delay=0)
        print(f"   倒数第{rk}名：净值 {r['final']:8.4f}  Δ {r['final']-rb3['final']:+7.4f}  "
              f"MDD {s['mdd']*100:6.2f}%  Calmar {s['calmar'] or 0:4.2f}")
    print("\n-- 买入延迟（触发=全池负，买末位）")
    for dl in (0, 1, 2):
        r, s = run(panel, dates, pool, dfn, "2018-12-03",
                   weak_mode="allneg", weak_rank=1, weak_delay=dl)
        print(f"   延迟 {dl} 日：净值 {r['final']:8.4f}  Δ {r['final']-rb3['final']:+7.4f}  "
              f"MDD {s['mdd']*100:6.2f}%  Calmar {s['calmar'] or 0:4.2f}")
    print("\n-- 触发阈值（买末位，当日换）")
    for md in ("allneg", "-15", "-20", "-25"):
        r, s = run(panel, dates, pool, dfn, "2018-12-03",
                   weak_mode=md, weak_rank=1, weak_delay=0)
        print(f"   {md:>7s}：净值 {r['final']:8.4f}  Δ {r['final']-rb3['final']:+7.4f}  "
              f"MDD {s['mdd']*100:6.2f}%  触发 {len(r.get('weak_events') or []):3d} 次")

    print("\n" + "=" * 132)
    print("基线(长窗) 净值 %.4f / MDD %.2f%% / Calmar %.2f" %
          (rb3["final"], sb3["mdd"] * 100, sb3["calmar"] or 0))


if __name__ == "__main__":
    main()
