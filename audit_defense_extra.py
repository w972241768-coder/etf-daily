#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计补充实验 (独立, 不 import 主引擎):
  E1 插值点敏感性 —— 把 out/patched_points.csv 里窗口内的 7 个几何插值点
     全部还原为 NaN(模拟"未修补"的原始数据源),重跑 A/B/C, 验证结论稳健性。
  E2 纯价值引擎独立回测 —— 只用价值引擎空仓/持 512040, 不叠加轮动状态机,
     检验 +101.32pp 到底来自"择时能力"还是"防守期恰好低吸"。
  E3 买入持有 512040 + 价值引擎 的独立净值, 与策略C 剥离。
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_defense_recheck as A   # 复用已审计的独立实现(非主引擎)

PANEL_CSV = os.path.join(HERE, "tdx_adj_long_2019_2026.csv")
PATCHED = os.path.join(HERE, "out", "patched_points.csv")


def load_panel():
    df = pd.read_csv(PANEL_CSV, dtype={"code": str})
    df["code"] = df["code"].str.strip()
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    from collections import OrderedDict
    frames = OrderedDict()
    for code, g in df.groupby("code", sort=False):
        g = g.sort_values("date").drop_duplicates("date", keep="last")
        frames[code] = g.set_index("date")["close"].astype(float)
    return pd.DataFrame(frames).sort_index()


def main():
    rot, deff, watch = A.load_pools()
    watch14 = [c for c in watch if c not in rot and c not in deff and c != "512880"]
    groups = {"main": rot, "watch": watch14, "combo": rot + watch14}
    SCEN = {"A": ("512880", False), "B": ("512040", False), "C": ("512040", True)}

    base_panel = load_panel()
    dates = base_panel.index
    si = int(dates.searchsorted(pd.Timestamp("2019-12-31")))
    ei = min(int(dates.searchsorted(pd.Timestamp("2026-12-31"))), len(dates) - 1)

    # ---------- E1 插值点敏感性 ----------
    pts = pd.read_csv(PATCHED, dtype={"code": str})
    inwin = [(r.code, pd.Timestamp(r.date)) for _, r in pts.iterrows()
             if str(r.date) >= "2019-12-31"]
    print("=" * 92)
    print("E1 插值点敏感性: 剔除窗口内 %d 个几何插值点后重跑" % len(inwin))
    print("=" * 92)
    p2 = base_panel.copy()
    for c, d in inwin:
        p2.loc[d, c] = np.nan
    print(f"  剔除点: {inwin}")
    res = {}
    for gname, pool in groups.items():
        for sk, (dc, ve) in SCEN.items():
            A._PRE_CACHE.clear()
            r0 = A.run(base_panel, dates, si, ei, pool, dc, ve)
            A._PRE_CACHE.clear()
            r1 = A.run(p2, dates, si, ei, pool, dc, ve)
            res[f"{gname}|{sk}"] = (float(r0["navs"].iloc[-1]), float(r1["navs"].iloc[-1]))
    print(f"\n  {'组':<28}{'含插值(主引擎)':>16}{'剔除插值':>14}{'差额':>12}{'相对':>10}")
    for k, (a, b) in res.items():
        print(f"  {k:<28}{a:>16.4f}{b:>14.4f}{b-a:>+12.4f}{(b/a-1)*100:>+9.2f}%")
    e1_ok = all(abs(b - a) < 0.02 for a, b in res.values())
    print(f"  -> 结论稳健性: {'稳健(全部变动<2%)' if e1_ok else '存在敏感项(变动>=2%)'}")

    # ---------- E2 纯价值引擎独立回测 ----------
    print("\n" + "=" * 92)
    print("E2 纯价值引擎独立回测 (无轮动状态机, 满仓期外全空仓 vs 全程持512040)")
    print("=" * 92)
    s = base_panel["512040"]

    def ma(n):
        return s.rolling(n, min_periods=n).mean()

    def ve_series(buy_f, sell_f, m):
        mm = ma(m)
        hold, out = False, {}
        for d in dates:
            p, v = s.loc[d], mm.loc[d]
            if pd.isna(p) or pd.isna(v):
                out[d] = hold
                continue
            if not hold and p < v * buy_f:
                hold = True
            elif hold and p > v * sell_f:
                hold = False
            out[d] = hold
        return pd.Series(out)

    ve = ve_series(0.97, 1.06, 200)
    ve_w = ve.loc[dates[si]:dates[ei]]
    n_hold = int(ve_w.sum())
    n = ei - si                 # 收益日数 = 1633
    n_pts = n + 1               # 净值点数 = 1634
    # 满仓 512040
    full = s.loc[dates[si]:dates[ei]].ffill()
    nav_full = float(full.iloc[-1] / full.iloc[0])

    def pure_ve_nav(start_at_first_state):
        """纯价值引擎净值。

        ⚠️ 2026-10-20 修正：原实现用 **当日** 收盘信号去赚 **当日** 收益
        （`h = ve.loc[dates[k]]` 配 `p0,p1 = s.iloc[k-1], s.iloc[k]`），
        等于收盘才知道信号、却已经吃到当天涨跌 = 未来函数，
        把净值压成 1.4785（碰上触发日多是大跌日、卖出日多是大涨日，双侧吃亏）。
        正确口径：第 k-1 日收盘状态决定 (k-1)→k 的收益，
        与 `etf_backtest_long.backtest()` / `backtest_dd_study.ve_single_backtest()` 一致。
        修正后（单边 0.2%）净值 2.66 量级；单边万 1 为 2.7132。
        """
        nav, held = 1.0, (bool(ve.loc[dates[si]]) if start_at_first_state else False)
        if start_at_first_state:
            nav *= (1 - 0.002)
        for k in range(si + 1, ei + 1):
            h = bool(ve.loc[dates[k - 1]])
            if h != held:
                nav *= (1 - 0.002)
                held = h
            if held:
                p0, p1 = s.iloc[k - 1], s.iloc[k]
                if p0 == p0 and p1 == p1 and p0 != 0:
                    nav *= p1 / p0
        return nav

    nav = pure_ve_nav(True)
    navs = [1.0 * (1 - 0.002)]
    held = bool(ve.loc[dates[si]])
    cum = navs[0]
    for k in range(si + 1, ei + 1):
        h = bool(ve.loc[dates[k - 1]])
        if h != held:
            cum *= (1 - 0.002)
            held = h
        if held:
            p0, p1 = s.iloc[k - 1], s.iloc[k]
            if p0 == p0 and p1 == p1 and p0 != 0:
                cum *= p1 / p0
        navs.append(cum)
    flips = int((ve_w.astype(int).diff().fillna(0) != 0).sum())
    print(f"  窗口 {dates[si].date()} ~ {dates[ei].date()}  收益日 {n}")
    print(f"  买入持有 512040            : 期末 {nav_full:.4f}  ({nav_full*100-100:+.2f}%)")
    print(f"  纯价值引擎(0.97/1.06/200) : 期末 {nav:.4f}  ({nav*100-100:+.2f}%)  "
          f"持仓 {n_hold}/{n_pts} 日 = {n_hold/n_pts*100:.1f}%  状态翻转 {flips} 次")
    print(f"  -> 纯价值引擎相对买入持有: {(nav/nav_full-1)*100:+.2f}% (相对)")
    arr = np.array(navs)
    mdd = float((arr / np.maximum.accumulate(arr) - 1).min())
    print(f"  纯价值引擎最大回撤: {mdd*100:.2f}%")

    # ---------- E3 拆解: 策略C 的超额来自哪 ----------
    print("\n" + "=" * 92)
    print("E3 归因拆解: C 相对 B 的增量 = 跳过防守期内 512040 的下跌")
    print("=" * 92)
    A._PRE_CACHE.clear()
    rc = A.run(base_panel, dates, si, ei, rot, "512040", True)
    A._PRE_CACHE.clear()
    rb = A.run(base_panel, dates, si, ei, rot, "512040", False)
    cash_log = [(d, t, ret) for d, t, ret in rc["ret_log"] if t == A.CASH]
    rb_map = {d: ret for d, t, ret in rb["ret_log"]}
    skipped = [(d, rb_map[d]) for d, t, ret in cash_log]
    n = len(skipped)
    prod = float(np.prod([1.0 + r for _d, r in skipped]))
    logsum = sum(math.log(1.0 + r) for _d, r in skipped)
    win = sum(1 for _d, r in skipped if r > 0)
    print(f"  持币 {n} 日, 其中 512040 当日上涨 {win} 日 ({win/n*100:.1f}%), 下跌 {n-win} 日")
    print(f"  这 {n} 日 512040 的累计收益(复利) : {(prod-1)*100:+.2f}%")
    print(f"  几何平均日收益                   : {(math.exp(logsum/n)-1)*100:+.4f}%")
    print(f"  年化(244日)                      : {(math.exp(logsum/n*244)-1)*100:+.2f}%")
    print(f"  -> 避开的正是 512040 的下跌段 => 增量 {'合理' if prod < 1 else '异常(避开的却是上涨段!)'}")
    # 剔除最大单日贡献
    srt = sorted(skipped, key=lambda x: x[1])
    print(f"  最差 5 日: {[(d.strftime('%Y-%m-%d'), round(r*100,2)) for d, r in srt[:5]]}")
    print(f"  最好 5 日: {[(d.strftime('%Y-%m-%d'), round(r*100,2)) for d, r in srt[-5:]]}")
    # 贡献集中度: 前5大盈利日贡献占比
    gains = sorted([r for _d, r in skipped if r > 0], reverse=True)
    tot_neg = sum(r for _d, r in skipped if r < 0)
    print(f"  负收益合计 {tot_neg*100:+.2f}% (分散在 {sum(1 for _d, r in skipped if r<0)} 日)")
    print(f"  正收益合计 {sum(gains)*100:+.2f}% (分散在 {len(gains)} 日)")
    print(f"  最大单日盈利日贡献 {gains[0]*100:+.2f}%, 占全部正收益 "
          f"{gains[0]/sum(gains)*100:.1f}%")

    out = dict(e1_patch_sensitivity={k: dict(with_patch=a, without_patch=b)
                                     for k, (a, b) in res.items()},
               e1_robust=e1_ok, e1_points=[[c, str(d.date())] for c, d in inwin],
               e2_pure_value_engine=dict(buy_hold_final=nav_full,
                                         pure_ve_final=nav,
                                         hold_days=n_hold, total_days=n_pts,
                                         return_days_window=n_pts - 1,
                                         hold_pct=round(n_hold / n_pts * 100, 2),
                                         flips=flips, mdd=mdd,
                                         rel_vs_buyhold_pct=(nav / nav_full - 1) * 100),
               e3_cash_skip=dict(n_cash=n, up_days=win,
                                 cum_512040_ret_pct=(prod - 1) * 100,
                                 geo_avg_daily_pct=(math.exp(logsum / n) - 1) * 100,
                                 annualized_pct=(math.exp(logsum / n * 244) - 1) * 100,
                                 worst5=[(d.strftime("%Y-%m-%d"), round(r * 100, 2))
                                         for d, r in srt[:5]],
                                 best5=[(d.strftime("%Y-%m-%d"), round(r * 100, 2))
                                        for d, r in srt[-5:]],
                                 neg_sum_pct=tot_neg * 100,
                                 pos_sum_pct=sum(gains) * 100,
                                 top1_gain_pct=gains[0] * 100,
                                 top1_share_pct=gains[0] / sum(gains) * 100))
    p = os.path.join(HERE, "out", "audit_defense_extra.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[json] {p}")


if __name__ == "__main__":
    main()
