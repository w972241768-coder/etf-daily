#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MACD 柱买入择时 + J 卖出择时 的对比研究（512040 / 512890，近 5 年）

⛔ 两项均已证伪（2026-10-24），**不要再加进策略**：
   · **MACD 柱转强买入** —— 512040 1.9916 → 1.3221（6 个买点无一当天柱在缩短，5 个整段等不到，
     买入次数 6→2）；512890 2.1010 → 2.0463。根因：砸出买点的正是让柱子「绿柱加长」的下跌。
   · **J>80 卖出** —— 512040 五个卖出日 J 全 >80（96.9/92.5/106.5/115.6/96.2），条件形同虚设，
     净值与不改**逐位相同**；512890 仅 +0.78pp（来自一次卖出晚一天）。
   本脚本保留仅作结论存档与复现。卖出侧的另一种尝试见 `ve_macd_sell_study.py`（柱翻绿才卖）。

用户 2026-10-24 原始需求：
  ① 买入：进入买入区间后，**以 MACD 柱转强的那一天买入**
     —— 绿柱（柱<0）缩短、或红柱（柱>0）加长，两者在数学上是同一个条件：
        **今日柱子比昨日更「正」**，即 hist[t] > hist[t−1]。
  ② 卖出：从「涨到 +6%」改成「涨到 +6% 且 J>80 才卖」。

为了能判断「到底是谁在起作用」，拆成 4 组（单变量归因）：
  A  原版         买入：进区间即买        卖出：px > MA200×sell
  B  只加 MACD    买入：进区间 + 柱转强    卖出：px > MA200×sell
  C  只改卖出     买入：进区间即买        卖出：px > MA200×sell 且 J>80
  D  两个都改     买入：进区间 + 柱转强    卖出：px > MA200×sell 且 J>80   ← 用户完整方案

口径：前复权 / 零手续费 / 初始净值 1 / 近 5 年；
     收益按「k−1 日收盘后的状态」决定 k−1→k 涨跌（信号当日成交、次日吃涨跌，无未来函数）。
     A 组净值必须与日报 `build_ve_curve()` 逐点一致（脚本内有断言）。

指标：
  MACD(12,26,9)：DIF=EMA12−EMA26，DEA=EMA9(DIF)，柱=(DIF−DEA)×2（通达信口径）。
  ⚠️ 柱子的绝对值尺度（是否 ×2）不影响「变长/缩短」判断，因为只是乘常数。

用法：
  python ve_macd_study.py
  python ve_macd_study.py --json out/ve_macd.json
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = "D:/娱乐（音乐、视频）Q-骑牛/ETF轮动战法"
for _p in (_HERE, PROJECT_DIR):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

import ve_j_entry_study as st          # 复用：数据加载 / 尾部拼接 / KDJ

YEARS = 5.0
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
J_SELL = 80.0

TARGETS = [
    dict(code="512040", name="价值ETF富国", buy=0.97, sell=1.06, ma=200),
    dict(code="512890", name="红利低波ETF华泰柏瑞", buy=1.00, sell=1.06, ma=200),
]


# --------------------------------------------------------------------------- 指标
def macd_hist(close: pd.Series, fast=MACD_FAST, slow=MACD_SLOW, sig=MACD_SIG) -> pd.Series:
    """MACD 柱（通达信口径：(DIF−DEA)×2）。"""
    ef = close.ewm(span=fast, adjust=False).mean()
    es = close.ewm(span=slow, adjust=False).mean()
    dif = ef - es
    dea = dif.ewm(span=sig, adjust=False).mean()
    return ((dif - dea) * 2.0).rename("hist")


def hist_rising(hist: pd.Series) -> pd.Series:
    """柱子「转强」= 今日比昨日更靠正方向。

    同时覆盖用户说的两种情形：
      · 绿柱缩短：hist 从 −5 → −3（增加）✔
      · 红柱加长：hist 从 +3 → +5（增加）✔
    反过来（红柱缩短、绿柱加长）为 False。
    首日 NaN 记为 False（不产生信号）。
    """
    d = hist.diff()
    return (d > 0).fillna(False).rename("rising")


# --------------------------------------------------------------------------- 回测
def run(df, *, ma=200, buy=0.97, sell=1.06, hist=None, jser=None,
        use_macd=False, use_j_sell=False, j_thr=J_SELL) -> dict:
    """价值引擎状态机（可选 MACD 买入门槛 / J 卖出门槛）。"""
    close = df["close"]
    mav = close.rolling(ma).mean()
    rising = hist_rising(hist) if hist is not None else None

    state, hold = [], False
    for t in close.index:
        px = float(close.loc[t])
        mv = float(mav.loc[t]) if mav.loc[t] == mav.loc[t] else None
        if mv:
            if hold:
                if px > mv * sell:
                    ok = True
                    if use_j_sell and jser is not None:
                        jv = jser.loc[t]
                        # J 不满足 > 80 时**不卖**，继续持有（等下一次同时满足）
                        ok = bool(jv == jv and jv > j_thr)
                    if ok:
                        hold = False
            elif px < mv * buy:
                okb = True
                if use_macd and rising is not None:
                    okb = bool(rising.loc[t])
                if okb:
                    hold = True
        state.append(hold)
    stt = pd.Series(state, index=close.index, dtype=bool)

    cut = close.index[-1] - pd.DateOffset(years=int(YEARS))
    w, sw = close[close.index >= cut], stt[stt.index >= cut]
    if len(w) < 60:
        raise SystemExit("窗口内数据不足")

    nav, navs, events, trades = 1.0, [1.0], [], []
    entry_i = 0 if bool(sw.iloc[0]) else None
    if bool(sw.iloc[0]):
        events.append(dict(date=w.index[0], act="HOLD", note="窗口首日已持有"))
    for i in range(1, len(w)):
        if bool(sw.iloc[i - 1]):
            nav *= float(w.iloc[i]) / float(w.iloc[i - 1])
        navs.append(nav)
        if bool(sw.iloc[i]) != bool(sw.iloc[i - 1]):
            if bool(sw.iloc[i]):
                events.append(dict(date=w.index[i], act="BUY", note=""))
                entry_i = i
            else:
                events.append(dict(date=w.index[i], act="SELL", note=""))
                if entry_i is not None:
                    seg = w.iloc[entry_i:i + 1]
                    trades.append(dict(
                        start=str(w.index[entry_i].date()),
                        end=str(w.index[i].date()), days=i - entry_i,
                        ret=float(seg.iloc[-1] / seg.iloc[0] - 1.0) * 100.0,
                        hist_at_buy=(None if hist is None else
                                     round(float(hist.loc[w.index[entry_i]]), 4)),
                        j_at_buy=(None if jser is None else
                                  round(float(jser.loc[w.index[entry_i]]), 1))))
                entry_i = None
    if entry_i is not None:
        seg = w.iloc[entry_i:]
        trades.append(dict(
            start=str(w.index[entry_i].date()), end=str(w.index[-1].date()),
            days=len(seg) - 1,
            ret=float(seg.iloc[-1] / seg.iloc[0] - 1.0) * 100.0,
            hist_at_buy=(None if hist is None else
                         round(float(hist.loc[w.index[entry_i]]), 4)),
            j_at_buy=(None if jser is None else
                      round(float(jser.loc[w.index[entry_i]]), 1)),
            open=True))

    navs = pd.Series(navs, index=w.index)
    bh = w / float(w.iloc[0])
    yrs = (w.index[-1] - w.index[0]).days / 365.25
    daily = navs.pct_change().dropna()
    sd = float(daily.std())
    win = [t["ret"] for t in trades]
    mdd = float((navs / navs.cummax() - 1).min() * 100)
    return dict(
        dates=[d.strftime("%Y-%m-%d") for d in w.index],
        nav=[float(v) for v in navs.values],
        bh=[float(v) for v in bh.values],
        hold=[bool(v) for v in sw.values],
        events=events, trades=trades,
        d0=w.index[0], d1=w.index[-1], n=len(w), yrs=yrs,
        final=float(navs.iloc[-1]), ret=(float(navs.iloc[-1]) - 1) * 100,
        cagr=(float(navs.iloc[-1]) ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0,
        mdd=mdd, sharpe=float(daily.mean() / sd * np.sqrt(252)) if sd > 0 else None,
        calmar=((float(navs.iloc[-1]) ** (1 / yrs) - 1) * 100 / abs(mdd)) if mdd else None,
        vol=float(sd * np.sqrt(252) * 100),
        hold_rate=float(sw.mean() * 100),
        n_buy=sum(1 for e in events if e["act"] == "BUY"),
        n_sell=sum(1 for e in events if e["act"] == "SELL"),
        avg_ret=(float(np.mean(win)) if win else None),
        avg_days=(float(np.mean([t["days"] for t in trades])) if trades else None),
        win_rate=(float(np.mean([1.0 if r > 0 else 0.0 for r in win]) * 100) if win else None),
        bh_final=float(bh.iloc[-1]), bh_ret=(float(bh.iloc[-1]) - 1) * 100,
        bh_mdd=float((bh / bh.cummax() - 1).min() * 100),
        holding_now=bool(sw.iloc[-1]),
    )


def zone_stats(df, hist, *, ma=200, buy=0.97):
    """买入区间段落统计：段长 vs 能否等到「柱子转强」。

    与 J 门槛同构的风险：柱子转强也是「要等」的条件，段太短就等不到。
    """
    close = df["close"]
    mav = close.rolling(ma).mean()
    inz = close < mav * buy
    rising = hist_rising(hist)
    cut = close.index[-1] - pd.DateOffset(years=int(YEARS))
    grp = (inz != inz.shift()).cumsum()
    segs, miss = [], []
    for _g, seg in df[inz].groupby(grp[inz]):
        if seg.index[0] < cut:
            continue
        rr = rising.reindex(seg.index)
        item = dict(start=str(seg.index[0].date()), end=str(seg.index[-1].date()),
                    days=len(seg), got=bool(rr.any()))
        segs.append(item)
        if not item["got"]:
            miss.append(item)
    lens = [s["days"] for s in segs]
    return dict(n_seg=len(segs),
                med_days=float(pd.Series(lens).median()) if lens else None,
                max_days=(max(lens) if lens else None),
                n_miss=len(miss),
                med_days_miss=(float(pd.Series([m["days"] for m in miss]).median())
                               if miss else None),
                miss=miss)


def delay_scan(df, *, ma=200, buy=0.97, sell=1.06, nmax=10):
    """对照：进区间后无条件延后 N 日买（不看任何指标），确认 MACD 不是「晚买」的同义词。"""
    close = df["close"]
    mav = close.rolling(ma).mean()
    out = []
    for delay in range(nmax + 1):
        state, hold, pending = [], False, 0
        for t in close.index:
            px = float(close.loc[t])
            mv = float(mav.loc[t]) if mav.loc[t] == mav.loc[t] else None
            if mv:
                if hold:
                    if px > mv * sell:
                        hold = False
                elif px < mv * buy:
                    if delay == 0:
                        hold = True
                    elif pending == 0:
                        pending = delay
                    else:
                        pending -= 1
                        if pending == 0:
                            hold = True
                else:
                    pending = 0
            state.append(hold)
        stt = pd.Series(state, index=close.index, dtype=bool)
        cut = close.index[-1] - pd.DateOffset(years=int(YEARS))
        w, sw = close[close.index >= cut], stt[stt.index >= cut]
        nav, navs = 1.0, [1.0]
        for i in range(1, len(w)):
            if bool(sw.iloc[i - 1]):
                nav *= float(w.iloc[i]) / float(w.iloc[i - 1])
            navs.append(nav)
        ns = pd.Series(navs, index=w.index)
        dl = ns.pct_change().dropna()
        sdd = float(dl.std())
        out.append(dict(delay=delay, final=float(ns.iloc[-1]),
                        ret=(float(ns.iloc[-1]) - 1) * 100,
                        hold_rate=float(sw.mean() * 100),
                        sharpe=float(dl.mean() / sdd * np.sqrt(252)) if sdd > 0 else None))
    return out


def buy_hist_state(df, hist, res_a, *, ma=200, buy=0.97, nmax=60):
    """盘查 A 组每个买点当天的 MACD 柱状态，以及在**仍处买入区间内**等到「转强」要几天。

    这是本轮最关键的一条诊断：如果买点当天柱子普遍在「加长（更负）」、且区间内等不到转强，
    就说明「柱转强」这个门槛与「跌破买线」这个触发条件在时间上天然错位 ——
    因为把价格打到买线以下的，正是那段让柱子不断加长的下跌。
    """
    close = df["close"]
    mav = close.rolling(ma).mean()
    rising = hist_rising(hist)
    out = []
    for t in res_a["trades"]:
        d = pd.Timestamp(t["start"])
        if d not in close.index:
            continue
        i = int(df.index.get_loc(d))
        h0 = float(hist.iloc[i])
        h1 = float(hist.iloc[i - 1]) if i > 0 else float("nan")
        if h1 != h1:
            direction = "—"
        elif h0 > h1:
            direction = "缩短↑"
        elif h0 < h1:
            direction = "加长↓"
        else:
            direction = "持平"
        # 只在「仍在买入区间」的连续天数里往后找第一次柱转强
        waited, found = None, None
        for k in range(1, nmax + 1):
            if i + k >= len(df.index):
                break
            nx = df.index[i + k]
            mvn = mav.loc[nx]
            if not (mvn == mvn and float(close.loc[nx]) < float(mvn) * buy):
                break                              # 离开区间 → 停止等待
            if bool(rising.loc[nx]):
                waited, found = k, str(nx.date())
                break
        out.append(dict(date=t["start"], hist=h0, prev=h1, direction=direction,
                        waited=waited, found=found, ret=round(t["ret"], 2),
                        days=t["days"], open=bool(t.get("open"))))
    return out


def sell_j_state(df, jser, res_a, *, ma=200, sell=1.06):
    """盘查 A 组每个「卖出信号日」的 J 值 —— 用来判断「+6% 且 J>80」是否形同虚设。"""
    close = df["close"]
    mav = close.rolling(ma).mean()
    out = []
    for t in res_a["trades"]:
        if t.get("open"):
            continue
        d = pd.Timestamp(t["end"])
        if d not in df.index:
            continue
        jv = float(jser.loc[d]) if d in jser.index else float("nan")
        # 「越过卖线幅度」= 收盘 /(MA200×sell) − 1。+0.01% 表示刚踩线就卖，不是"只涨了 0.01%"。
        over = (float(close.loc[d]) / (float(mav.loc[d]) * sell) - 1.0) * 100.0
        out.append(dict(date=t["end"], j=jv, passed=bool(jv == jv and jv > 80.0),
                        over=over, ret=round(t["ret"], 2)))
    return out


def fallback_run(df, *, ma=200, buy=0.97, sell=1.06, hist=None, jser=None,
                 use_j_sell=False, j_thr=J_SELL):
    """兜底变体：区间内等不到「柱转强」时，在**区间末日**（明天就回线上）买入，不漏整段。

    用途：把「漏掉」这个成分单独量化出来，看 MACD 的损害有多少来自「等不到」。
    """
    close = df["close"]
    mav = close.rolling(ma).mean()
    rising = hist_rising(hist) if hist is not None else None
    idx = list(close.index)
    state, hold = [], False
    for i, t in enumerate(idx):
        px = float(close.loc[t])
        mv = float(mav.loc[t]) if mav.loc[t] == mav.loc[t] else None
        if mv:
            if hold:
                if px > mv * sell:
                    ok = True
                    if use_j_sell and jser is not None:
                        jv = jser.loc[t]
                        ok = bool(jv == jv and jv > j_thr)
                    if ok:
                        hold = False
            elif px < mv * buy:
                okb = bool(rising.loc[t]) if rising is not None else True
                if not okb and i + 1 < len(idx):
                    mvn = mav.loc[idx[i + 1]]
                    if mvn == mvn and float(close.loc[idx[i + 1]]) >= float(mvn) * buy:
                        okb = True                 # 明天就出区间 → 今天兜底买
                if okb:
                    hold = True
        state.append(hold)
    stt = pd.Series(state, index=close.index, dtype=bool)
    cut = close.index[-1] - pd.DateOffset(years=int(YEARS))
    w, sw = close[close.index >= cut], stt[stt.index >= cut]
    nav, navs = 1.0, [1.0]
    for i in range(1, len(w)):
        if bool(sw.iloc[i - 1]):
            nav *= float(w.iloc[i]) / float(w.iloc[i - 1])
        navs.append(nav)
    ns = pd.Series(navs, index=w.index)
    dl = ns.pct_change().dropna()
    sd = float(dl.std())
    return dict(final=float(ns.iloc[-1]), ret=(float(ns.iloc[-1]) - 1) * 100,
                mdd=float((ns / ns.cummax() - 1).min() * 100),
                sharpe=float(dl.mean() / sd * np.sqrt(252)) if sd > 0 else None,
                hold_rate=float(sw.mean() * 100),
                n_buy=int((sw.astype(int).diff().fillna(0) == 1).sum()))


def fmt(v, nd=2, pct=True, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s + ("%" if pct else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(PROJECT_DIR, "out", "ve_macd.json"))
    ap.add_argument("--j-scan", type=float, nargs="*", default=[70, 75, 80, 85, 90])
    args = ap.parse_args()

    allout = dict(meta=dict(adj_csv=st.ADJ_CSV, years=YEARS,
                            macd=f"MACD({MACD_FAST},{MACD_SLOW},{MACD_SIG})·柱=(DIF-DEA)×2",
                            j_sell=J_SELL, cost="零手续费", window_note="两只标的同窗口"),
                  targets={})

    for tg in TARGETS:
        code, buy, sell, ma = tg["code"], tg["buy"], tg["sell"], tg["ma"]
        base = st.load_ohlc(code)
        df, note, ext = st.extend_tail(base, code)
        hist = macd_hist(df["close"])
        jser = st.kdj_j(df["high"], df["low"], df["close"])

        A = run(df, ma=ma, buy=buy, sell=sell)
        B = run(df, ma=ma, buy=buy, sell=sell, hist=hist, use_macd=True)
        C = run(df, ma=ma, buy=buy, sell=sell, jser=jser, use_j_sell=True, j_thr=J_SELL)
        D = run(df, ma=ma, buy=buy, sell=sell, hist=hist, jser=jser,
                use_macd=True, use_j_sell=True, j_thr=J_SELL)

        # 卖出 J 阈值扫描：**买入侧固定在「原版（进区间即买）」**。
        # 若挂在 MACD 买入下，卖出只剩 2 次且 J 都 >80，各阈值结果完全相同，
        # 扫描表会变成一排一样的数字、毫无信息量 —— 这样也才能单独观察卖出侧的贡献。
        jscan = {}
        for thr in args.j_scan:
            r = run(df, ma=ma, buy=buy, sell=sell, jser=jser,
                    use_macd=False, use_j_sell=True, j_thr=float(thr))
            _k = (str(int(thr)) if float(thr).is_integer() else str(thr))
            jscan[_k] = dict(final=r["final"], ret=r["ret"], mdd=r["mdd"],
                             sharpe=r["sharpe"], hold_rate=r["hold_rate"],
                             n_buy=r["n_buy"], n_sell=r["n_sell"],
                             avg_ret=r["avg_ret"])

        cut = df.index[-1] - pd.DateOffset(years=int(YEARS))
        hw = hist[hist.index >= cut].dropna()
        jw = jser[jser.index >= cut].dropna()
        rising_w = hist_rising(hist)[hist.index >= cut]

        res = dict(code=code, name=tg["name"], buy=buy, sell=sell, ma=ma,
                   data_note=note, extended=ext, data_end=str(df.index.max().date()),
                   A=A, B=B, C=C, D=D, jscan=jscan,
                   zone=zone_stats(df, hist, ma=ma, buy=buy),
                   delay=delay_scan(df, ma=ma, buy=buy, sell=sell),
                   buy_state=buy_hist_state(df, hist, A, ma=ma, buy=buy),
                   sell_state=sell_j_state(df, jser, A, ma=ma, sell=sell),
                   fallback=dict(F=fallback_run(df, ma=ma, buy=buy, sell=sell, hist=hist),
                                 G=fallback_run(df, ma=ma, buy=buy, sell=sell, hist=hist,
                                                jser=jser, use_j_sell=True)),
                   hist_dist=dict(mean=float(hw.mean()), p10=float(hw.quantile(.10)),
                                  p50=float(hw.median()), p90=float(hw.quantile(.90)),
                                  neg=float((hw < 0).mean() * 100)),
                   rising_rate=float(rising_w.mean() * 100),
                   j_gt80=float((jw > J_SELL).mean() * 100))
        allout["targets"][code] = res

        # ---------------- 控制台输出 ----------------
        print("\n" + "=" * 96)
        print(f"{code} {tg['name']}　buy={buy:.2f} / sell={sell:.2f} / MA{ma}"
              f"　窗口 {A['d0']:%Y-%m-%d} ~ {A['d1']:%Y-%m-%d}（{A['n']} 日 / {A['yrs']:.2f} 年）")
        print(f"  数据：{note}")
        print("=" * 96)
        print(f"{'':22}{'A 原版':>13}{'B 只加MACD':>14}{'C 只改卖出':>14}{'D 两个都改':>14}")
        for lab, key, nd, pct in (("期末净值", "final", 4, False),
                                  ("区间收益", "ret", 2, True),
                                  ("年化收益", "cagr", 2, True),
                                  ("最大回撤", "mdd", 2, True),
                                  ("夏普", "sharpe", 2, False),
                                  ("Calmar", "calmar", 2, False),
                                  ("持仓占比", "hold_rate", 1, True),
                                  ("买入次数", "n_buy", 0, False),
                                  ("卖出次数", "n_sell", 0, False),
                                  ("平均单笔", "avg_ret", 2, True),
                                  ("平均持仓日", "avg_days", 1, False)):
            vals = []
            for R in (A, B, C, D):
                v = R.get(key)
                vals.append(fmt(v, nd, pct) if pct or key in ("sharpe", "calmar")
                            else f"{v:.{nd}f}")
            print(f"{lab:22}" + "".join(f"{v:>13}" for v in vals))
        print(f"\n  MACD 柱分布（窗口内）：均值 {res['hist_dist']['mean']:.4f} / "
              f"p10 {res['hist_dist']['p10']:.4f} / 中位 {res['hist_dist']['p50']:.4f} / "
              f"p90 {res['hist_dist']['p90']:.4f}；绿柱占 {res['hist_dist']['neg']:.1f}% 天数")
        print(f"  柱子「转强」的天数占 {res['rising_rate']:.1f}%；"
              f"J>80 的天数占 {res['j_gt80']:.1f}%")
        z = res["zone"]
        print(f"  买入区间：{z['n_seg']} 段，段长中位 {z['med_days']:.0f} 日，最长 {z['max_days']} 日；"
              f"其中 {z['n_miss']} 段整段等不到「柱转强」"
              f"（段长中位 {(z['med_days_miss'] or 0):.0f} 日）")
        print(f"  对照：无条件延后 N 日买入 → "
              + " / ".join(f"N={d['delay']}:{d['final']:.4f}" for d in res["delay"][:5]))
        print(f"  D 组逐笔（含买入日 MACD 柱、卖出日 J）：")
        for t in D["trades"]:
            jb = "—" if t.get("j_at_buy") is None else f"{t['j_at_buy']:.1f}"
            hb = "—" if t.get("hist_at_buy") is None else f"{t['hist_at_buy']:+.4f}"
            print(f"    {t['start']} → {t['end']}  {t['days']:>3}日  "
                  f"{fmt(t['ret'])}  买入柱={hb} 买入J={jb}"
                  f"{'  [未平仓]' if t.get('open') else ''}")
        print(f"  卖出 J 阈值扫描（买入固定 = 原版 进区间即买，只变卖出侧）：")
        for thr, s in jscan.items():
            print(f"    J>{thr:<3}　净值 {s['final']:.4f}　收益 {fmt(s['ret'])}　"
                  f"回撤 {fmt(s['mdd'])}　夏普 {s['sharpe']:.2f}　"
                  f"持仓 {s['hold_rate']:.1f}%　买 {s['n_buy']} 卖 {s['n_sell']}")

        print(f"  买点当天柱状态：")
        for b in res["buy_state"]:
            w = "—" if b["waited"] is None else f"{b['waited']}日"
            print(f"    {b['date']}  柱 {b['hist']:+.4f}（前 {b['prev']:+.4f}）"
                  f"{b['direction']}  等到转强 {w}"
                  f"{'  [区间内未等到]' if b['waited'] is None else ' → ' + b['found']}")
        n_ok = sum(1 for b in res["buy_state"] if b["direction"] == "缩短↑")
        print(f"    ⇒ {len(res['buy_state'])} 个买点里只有 {n_ok} 个当天柱已在缩短；"
              f"{sum(1 for b in res['buy_state'] if b['waited'] is None)} 个在剩余区间内等不到转强")
        print(f"  卖出日 J 值：")
        for s in res["sell_state"]:
            print(f"    卖出 {s['date']}  超出卖线 {s['over']:+.2f}%  J={s['j']:.1f}  "
                  f"J>80 {'是' if s['passed'] else '❌否'}")
        ns = sum(1 for s in res["sell_state"] if s["passed"])
        print(f"    ⇒ {ns}/{len(res['sell_state'])} 个卖出日 J 已 >80（涨 6% 自然超买）")
        fb = res["fallback"]
        print(f"  兜底变体（等不到就区间末日买）：F {fb['F']['final']:.4f}"
              f"（持仓 {fb['F']['hold_rate']:.1f}%，买 {fb['F']['n_buy']}）　"
              f"G(加J>80卖) {fb['G']['final']:.4f}")

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(allout, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nJSON → {args.json}")


if __name__ == "__main__":
    main()
