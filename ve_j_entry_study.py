#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""价值引擎「买入门槛加 J<20」对比研究（512040 / 512890，近 5 年）

⛔ 已废弃（2026-10-24 定论）：**J<20 买入门槛已从策略中删除，不要再加回**。
   实测结论：512040 仅 +2.38pp（6 个买点里 5 个本来 J 就 <20，差异只来自 1 个买点被推迟
   5 个交易日 ⇒ 单点偶然）；512890 **−31.24pp**（9 个买点里 4 个 J≥20，整段漏掉）。
   机制：J 是慢指标（9 日 RSV + 3 日再平滑），跌破买线是 1~2 日的快事件，等 J 磨到 20 以下
   价格已站回线上、区间关闭 ⇒ 门槛筛掉的是「跌得快、来不及超卖」的买点。
   反证：无条件延后 N 日买入越延越差；交叉实验换买线深度差值忽 +0.0238 忽 0.0000。
   本脚本保留仅作结论存档与复现用；J 现只用于**卖出侧**（见 ve_macd_study.py：+6% 且 J>80 才卖）。

用户 2026-10-23 原始需求（已证伪，勿再实现）：
  原策略：收盘 < MA200×buy 就买（进区间即买）。
  ~~新想法：进区间后，还要「当日 J 值 < 20」才买（用 KDJ 的 J 做超卖确认）。~~
  对比两者近 5 年收益，初始净值 = 1。

设计要点
--------
1) 数据：`tdx_adj_long_2019_2026.csv` **自带 open/high/low/close 四价且已前复权**
   （此前只用了 close）。J 值必须用 high/low，不能拿 close 代替——那会把
   振幅抹平、J 长期贴住 50，条件几乎失效。
2) 尾部：长历史 T0 = 2026-09-24，日常锚定日更晚 → 用在线四价补齐，并校验
   重叠末日 close 完全同价（ratio≈1）才拼，否则宁可用到 T0（防分红/拆分假跳空）。
3) 状态机（两条策略共用卖出规则，只改买入门槛）：
     原版 A：px < MA200×buy                → 持有
     J 版 B：px < MA200×buy 且 J < 20      → 持有
     卖出（两者相同）：持有中且 px > MA200×sell → 卖出
   中间区间维持上一状态；未满足买入 → 持币。
4) 收益归属（无未来函数）：用 **k-1 日收盘后的状态** 决定 k-1→k 的收益，
   与 `etf_rotation.build_ve_curve()` / `etf_backtest_long.backtest()` 同口径。
   信号当日成交、次日才吃涨跌。
5) 口径：前复权 / 零手续费 / 初始净值 1。零手续费是为了只观察「规则本身的
   择时价值」，不掺成本差异（J 版交易次数更少，含成本后优势会更明显）。

用法：
  python ve_j_entry_study.py            # 打印全部结果
  python ve_j_entry_study.py --json out/ve_j_entry.json
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

ADJ_CSV = os.path.join(PROJECT_DIR, "tdx_adj_long_2019_2026.csv")

# 两只标的各用自己那套已拍板的参数（不要把 512040 的阈值套到 512890 上）
TARGETS = [
    dict(code="512040", name="价值ETF富国",  buy=0.97, sell=1.06, ma=200),
    dict(code="512890", name="红利低波ETF华泰柏瑞", buy=1.00, sell=1.06, ma=200),
]

YEARS = 5.0
J_N, J_M1, J_M2 = 9, 3, 3      # 标准 KDJ(9,3,3)
J_ENTRY = 20.0                 # 用户指定阈值


# --------------------------------------------------------------------------- 数据
def load_ohlc(code: str) -> pd.DataFrame:
    """读长历史四价（已前复权）。返回 index=date, columns=[open,high,low,close]。"""
    df = pd.read_csv(ADJ_CSV, dtype={"code": str})
    df = df[df["code"].astype(str).str.zfill(6) == code].copy()
    if not len(df):
        raise SystemExit(f"长历史 CSV 里没有 {code}")
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    df = df.sort_values("date").drop_duplicates("date", keep="last").set_index("date")
    cols = [c for c in ("open", "high", "low", "close") if c in df.columns]
    if len(cols) < 4:
        raise SystemExit(f"{code} 缺四价字段，实际有 {cols}；J 值需要 high/low")
    return df[cols].astype(float)


def fetch_online_ohlc(code: str, datalen: int = 90):
    """在线 four-price（新浪日线，不复权）。返回 DataFrame[index=date, 四价] 或 None。"""
    try:
        import json as _json
        import online_data as od
        sym = od._market(code) + code
        txt = od._http_get(f"{od.SINA_KLINE}?symbol={sym}&scale=240&ma=no&datalen={datalen}")
        rows = _json.loads(txt)
        recs = []
        for r in rows:
            d = str(r.get("day") or "")[:10]
            try:
                o, h, l, c = (float(r["open"]), float(r["high"]),
                              float(r["low"]), float(r["close"]))
            except (KeyError, TypeError, ValueError):
                continue
            if d and c > 0 and h >= l > 0:
                recs.append((pd.Timestamp(d), o, h, l, c))
        if len(recs) < 5:
            return None
        return pd.DataFrame(recs, columns=["date", "open", "high", "low", "close"]
                            ).set_index("date").sort_index()
    except Exception:                                  # noqa: BLE001
        return None


def extend_tail(base: pd.DataFrame, code: str):
    """把在线四价接到长历史尾部。口径不一致（重叠日 close 偏离 >0.3%）则不拼。

    返回 (df, note, extended_bool)。
    """
    t0 = base.index.max()
    onl = fetch_online_ohlc(code)
    if onl is None or not len(onl):
        return base, "在线四价取数失败，只用长历史（至 %s）" % t0.strftime("%Y-%m-%d"), False
    if t0 not in onl.index:
        return base, "在线序列未覆盖长历史末日 %s，不拼接" % t0.strftime("%Y-%m-%d"), False
    ratio = float(onl.loc[t0, "close"]) / float(base.loc[t0, "close"])
    if abs(ratio - 1.0) > 3e-3:
        return base, (f"⚠ 重叠日 {t0:%Y-%m-%d} 收盘比 ratio={ratio:.5f} 偏离>0.3%，"
                      f"期间可能有分红/拆分 → 不拼接（只用长历史）"), False
    tail = onl[onl.index > t0]
    if not len(tail):
        return base, f"在线无 {t0:%Y-%m-%d} 之后数据", False
    out = pd.concat([base, tail]).sort_index()
    return out, (f"在线四价补 {len(tail)} 个交易日（{tail.index.min():%Y-%m-%d} ~ "
                 f"{tail.index.max():%Y-%m-%d}，重叠日 ratio={ratio:.6f}）"), True


# --------------------------------------------------------------------------- 指标
def kdj_j(high: pd.Series, low: pd.Series, close: pd.Series,
          n: int = J_N, m1: int = J_M1, m2: int = J_M2) -> pd.Series:
    """标准 KDJ(9,3,3) 的 J 值。K/D 初值 50（同通达信），递归平滑。"""
    llv = low.rolling(n, min_periods=1).min()
    hhv = high.rolling(n, min_periods=1).max()
    rng = hhv - llv
    # 区间为 0（一字板/停牌）时 RSV 取 50，避免除零把 J 打成 NaN 或极端值
    rsv = np.where(rng.to_numpy() > 0,
                   (close.to_numpy() - llv.to_numpy()) / rng.to_numpy() * 100.0,
                   50.0)
    k = np.empty(len(close))
    d = np.empty(len(close))
    pk, pdv = 50.0, 50.0
    for i in range(len(close)):
        pk = (m1 - 1.0) / m1 * pk + 1.0 / m1 * rsv[i]
        pdv = (m2 - 1.0) / m2 * pdv + 1.0 / m2 * pk
        k[i], d[i] = pk, pdv
    return pd.Series(3.0 * k - 2.0 * d, index=close.index, name="J")


# --------------------------------------------------------------------------- 回测
def run(df: pd.DataFrame, *, ma=200, buy=0.97, sell=1.06, use_j=False,
        j_thr=J_ENTRY, jser=None) -> dict:
    """价值引擎状态机 + 可选 J 门槛。返回净值/事件/指标（5 年窗口）。"""
    close = df["close"]
    mav = close.rolling(ma).mean()
    jv = jser.reindex(close.index) if jser is not None else None

    state, hold = [], False
    for t in close.index:
        px = float(close.loc[t])
        mv = float(mav.loc[t]) if mav.loc[t] == mav.loc[t] else None
        if mv:
            if px < mv * buy:
                ok_j = True if not use_j else bool(jv.loc[t] < j_thr)
                if ok_j:
                    hold = True
            elif hold and px > mv * sell:
                hold = False
        state.append(hold)
    st = pd.Series(state, index=close.index, dtype=bool)

    cut = close.index[-1] - pd.DateOffset(years=int(YEARS))
    w, sw = close[close.index >= cut], st[st.index >= cut]
    if len(w) < 60:
        raise SystemExit("窗口内数据不足")

    nav, navs, events, trades = 1.0, [1.0], [], []
    if bool(sw.iloc[0]):
        events.append(dict(date=w.index[0], act="HOLD", note="窗口首日已持有（买点在窗口前）"))
    entry_i = 0 if bool(sw.iloc[0]) else None
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
                        start=str(w.index[entry_i].date()), end=str(w.index[i].date()),
                        days=i - entry_i,
                        ret=float(seg.iloc[-1] / seg.iloc[0] - 1.0) * 100.0,
                        j_at_buy=(None if jv is None else
                                  float(jv.loc[w.index[entry_i]]))))
                entry_i = None
    # 未平仓的最后一段也要统计
    if entry_i is not None:
        seg = w.iloc[entry_i:]
        trades.append(dict(
            start=str(w.index[entry_i].date()), end=str(w.index[-1].date()),
            days=len(seg) - 1,
            ret=float(seg.iloc[-1] / seg.iloc[0] - 1.0) * 100.0,
            j_at_buy=(None if jv is None else float(jv.loc[w.index[entry_i]])),
            open=True))

    navs = pd.Series(navs, index=w.index)
    bh = w / float(w.iloc[0])
    yrs = (w.index[-1] - w.index[0]).days / 365.25
    daily = navs.pct_change().dropna()

    def _sharpe(r):
        sd = float(r.std())
        return float(r.mean() / sd * np.sqrt(252)) if sd > 0 else float("nan")

    mdd = float((navs / navs.cummax() - 1).min() * 100)
    cagr = (float(navs.iloc[-1]) ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0
    win = [t["ret"] for t in trades if t["ret"] is not None]
    return dict(
        dates=[d.strftime("%Y-%m-%d") for d in w.index],
        nav=[float(v) for v in navs.values],
        bh=[float(v) for v in bh.values],
        hold=[bool(v) for v in sw.values],
        events=events, trades=trades,
        d0=w.index[0], d1=w.index[-1], n=len(w), yrs=yrs,
        final=float(navs.iloc[-1]), ret=(float(navs.iloc[-1]) - 1) * 100,
        cagr=cagr, mdd=mdd, sharpe=_sharpe(daily),
        vol=float(daily.std() * np.sqrt(252) * 100),
        hold_rate=float(sw.mean() * 100),
        n_buy=sum(1 for e in events if e["act"] == "BUY"),
        n_sell=sum(1 for e in events if e["act"] == "SELL"),
        n_obs=len(trades),
        avg_ret=(float(np.mean(win)) if win else None),
        avg_days=(float(np.mean([t["days"] for t in trades])) if trades else None),
        win_rate=(float(np.mean([1.0 if r > 0 else 0.0 for r in win]) * 100) if win else None),
        bh_final=float(bh.iloc[-1]), bh_ret=(float(bh.iloc[-1]) - 1) * 100,
        bh_mdd=float((bh / bh.cummax() - 1).min() * 100),
        holding_now=bool(sw.iloc[-1]),
    )


# --------------------------------------------------------------------------- 报告
def delay_scan(df, *, ma=200, buy=0.97, sell=1.06, nmax=10):
    """对照实验：进区间后**无条件延后 N 日**再买（不看 J），卖出规则不变。

    用途：如果 B（J<20）的改善只是「等更低价」，那延后 N 日也该能复现同样改善。
    复现不了 ⇒ 说明差异另有来源（本例是单个买点被推迟的事件，见 report）。
    """
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
                    pending = 0                     # 离开区间则撤单
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
        out.append(dict(delay=delay, final=float(ns.iloc[-1]),
                        ret=(float(ns.iloc[-1]) - 1) * 100,
                        mdd=float((ns / ns.cummax() - 1).min() * 100),
                        sharpe=float(dl.mean() / sd * np.sqrt(252)) if sd > 0 else None,
                        hold_rate=float(sw.mean() * 100)))
    return out


def zone_stats(df, jser, *, ma=200, buy=0.97, thr=J_ENTRY):
    """买入区间「段落」统计：段长 vs 能否等到 J<thr。

    机制核心：J 是平滑过的慢指标（9 日 RSV + 3 日再平滑），而跌破买线常是
    1~2 日的快事件 ⇒ 区间结束时 J 还没降下来，门槛就把这次买点永久漏掉。
    """
    close = df["close"]
    mav = close.rolling(ma).mean()
    inz = close < mav * buy
    cut = close.index[-1] - pd.DateOffset(years=int(YEARS))
    grp = (inz != inz.shift()).cumsum()
    segs, miss = [], []
    for _g, seg in df[inz].groupby(grp[inz]):
        if seg.index[0] < cut:
            continue
        jj = jser.reindex(seg.index)
        item = dict(start=str(seg.index[0].date()), end=str(seg.index[-1].date()),
                    days=len(seg), jmin=float(jj.min()), jmax=float(jj.max()),
                    got=bool((jj < thr).any()))
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
                miss=miss, segs=segs)


def cross_scan(code, *, sells=1.06):
    """交叉实验：同一标的换「买线深度」，看 J<20 门槛是否仍然有效。"""
    df = load_ohlc(code)
    df, _, _ = extend_tail(df, code)
    j = kdj_j(df["high"], df["low"], df["close"])
    out = []
    for buy in (0.97, 1.00):
        a = run(df, ma=200, buy=buy, sell=sells)
        b = run(df, ma=200, buy=buy, sell=sells, use_j=True, j_thr=J_ENTRY, jser=j)
        a_j = [float(j.loc[pd.Timestamp(e["date"])]) for e in a["events"]
               if e["act"] == "BUY" and pd.Timestamp(e["date"]) in j.index]
        out.append(dict(buy=buy, a=a["final"], b=b["final"], diff=b["final"] - a["final"],
                        a_hold=a["hold_rate"], b_hold=b["hold_rate"],
                        a_buy=a["n_buy"], b_buy=b["n_buy"],
                        n_j_high=sum(1 for v in a_j if v >= J_ENTRY), n_j=len(a_j)))
    return out


def fmt(v, nd=2, pct=True, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s + ("%" if pct else "")


def print_target(tg, res):
    code, nm = tg["code"], tg["name"]
    a, b = res["A"], res["B"]
    print("\n" + "=" * 92)
    print(f"{code} {nm}　参数 buy={tg['buy']:.2f} / sell={tg['sell']:.2f} / MA{tg['ma']}")
    print(f"窗口 {a['d0']:%Y-%m-%d} ~ {a['d1']:%Y-%m-%d}（{a['n']} 个交易日 / {a['yrs']:.2f} 年）"
          f"　数据：{res['data_note']}")
    print("=" * 92)
    print(f"{'':16}{'A 进区间即买':>16}{'B 进区间+J<20':>18}{'差异(B−A)':>14}")
    rows = [
        ("期末净值", "final", 4, False, ""),
        ("区间收益", "ret", 2, True, "pp"),
        ("年化收益", "cagr", 2, True, "pp"),
        ("最大回撤", "mdd", 2, True, "pp"),
        ("年化波动", "vol", 2, True, "pp"),
        ("夏普", "sharpe", 2, False, ""),
        ("持仓占比", "hold_rate", 1, True, "pp"),
        ("买入次数", "n_buy", 0, False, ""),
        ("平均单笔", "avg_ret", 2, True, "pp"),
        ("平均持仓日", "avg_days", 1, False, ""),
        ("单笔胜率", "win_rate", 1, True, "pp"),
    ]
    for lab, key, nd, pct, unit in rows:
        va, vb = a.get(key), b.get(key)
        if key in ("mdd", "vol"):
            d = None if (va is None or vb is None) else (vb - va)
        else:
            d = None if (va is None or vb is None) else (vb - va)
        print(f"{lab:16}{fmt(va, nd, pct):>16}{fmt(vb, nd, pct):>18}"
              f"{(fmt(d, nd, pct) + unit) if d is not None else '—':>14}")
    print(f"{'同期买入持有':16}{fmt(a['bh_final'], 4, False):>16}"
          f"{'收益 ' + fmt(a['bh_ret'], 2):>18}{'回撤 ' + fmt(a['bh_mdd'], 2):>14}")
    print(f"\n  B 的 J 门槛错过的买点：{res['skipped']}")
    print("  逐笔（B 版，含买入日 J 值）：")
    for t in b["trades"]:
        jj = "—" if t.get("j_at_buy") is None else f"{t['j_at_buy']:.1f}"
        print(f"    {t['start']} → {t['end']}  {t['days']:>3}日  "
              f"{fmt(t['ret'])}  买入日J={jj}{'  [未平仓]' if t.get('open') else ''}")
    print("  逐笔（A 版）：")
    for t in a["trades"]:
        print(f"    {t['start']} → {t['end']}  {t['days']:>3}日  {fmt(t['ret'])}"
              f"{'  [未平仓]' if t.get('open') else ''}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(PROJECT_DIR, "out", "ve_j_entry.json"))
    ap.add_argument("--thr", type=float, nargs="*", default=[10, 15, 20, 25, 30],
                    help="J 阈值敏感性扫描")
    args = ap.parse_args()

    allout = {"meta": dict(adj_csv=ADJ_CSV, years=YEARS, kdj=f"KDJ({J_N},{J_M1},{J_M2})",
                           j_entry=J_ENTRY), "targets": {}}
    for tg in TARGETS:
        base = load_ohlc(tg["code"])
        df, note, ext = extend_tail(base, tg["code"])
        jser = kdj_j(df["high"], df["low"], df["close"])
        a = run(df, ma=tg["ma"], buy=tg["buy"], sell=tg["sell"], use_j=False)
        b = run(df, ma=tg["ma"], buy=tg["buy"], sell=tg["sell"], use_j=True,
                j_thr=J_ENTRY, jser=jser)
        # 扫描：不同 J 门槛（只在买入门槛上，卖出规则不变）
        sens = {}
        for thr in args.thr:
            r = run(df, ma=tg["ma"], buy=tg["buy"], sell=tg["sell"], use_j=True,
                    j_thr=float(thr), jser=jser)
            sens[str(thr)] = dict(final=r["final"], ret=r["ret"], mdd=r["mdd"],
                                  sharpe=r["sharpe"], hold_rate=r["hold_rate"],
                                  n_buy=r["n_buy"], avg_ret=r["avg_ret"])
        # J 分布（窗口内），解释门槛松紧是否合理
        cut = df.index[-1] - pd.DateOffset(years=int(YEARS))
        jw = jser[jser.index >= cut].dropna()
        jdist = dict(mean=float(jw.mean()), p10=float(jw.quantile(.10)),
                     p25=float(jw.quantile(.25)), p50=float(jw.median()),
                     p75=float(jw.quantile(.75)), p90=float(jw.quantile(.90)),
                     lt20=float((jw < J_ENTRY).mean() * 100))
        # A 版买点里有多少 J 也 <20（B 与 A 的分歧来源）
        jmap = jser
        a_buys = [e for e in a["events"] if e["act"] == "BUY"]
        a_buy_j = [float(jmap.loc[pd.Timestamp(e["date"])]) for e in a_buys
                   if pd.Timestamp(e["date"]) in jmap.index]
        res = dict(code=tg["code"], name=tg["name"], buy=tg["buy"], sell=tg["sell"],
                   ma=tg["ma"], data_note=note, extended=ext,
                   data_end=str(df.index.max().date()),
                   A=a, B=b, sens=sens, jdist=jdist,
                   a_buy_j=a_buy_j,
                   delay=delay_scan(df, ma=tg["ma"], buy=tg["buy"], sell=tg["sell"]),
                   zone=zone_stats(df, jser, ma=tg["ma"], buy=tg["buy"]),
                   cross=cross_scan(tg["code"], sells=tg["sell"]),
                   skipped=f"{sum(1 for v in a_buy_j if v >= J_ENTRY)}/{len(a_buy_j)}"
                           f"（A 的买点里 J≥20 的个数）")
        allout["targets"][tg["code"]] = res
        print_target(tg, res)
        print(f"  J 值分布（窗口内）：均值 {jdist['mean']:.1f} / p10 {jdist['p10']:.1f} / "
              f"p25 {jdist['p25']:.1f} / 中位 {jdist['p50']:.1f} / p75 {jdist['p75']:.1f} / "
              f"p90 {jdist['p90']:.1f}　J<{J_ENTRY:.0f} 天数占 {jdist['lt20']:.1f}%")
        print(f"  A 的买点 J 值：{['%.1f' % v for v in a_buy_j]}")
        print(f"  区间段落：{res['zone']['n_seg']} 段，段长中位 {res['zone']['med_days']:.0f} 日，"
              f"其中 {res['zone']['n_miss']} 段整段等不到 J<20"
              f"（这些段长中位 {res['zone']['med_days_miss'] or 0:.0f} 日）")
        print(f"  对照：无条件延后 N 日买入 → "
              + " / ".join(f"N={d['delay']}:{d['final']:.4f}" for d in res["delay"][:5]))
        print(f"  阈值扫描（买入门槛 J<N，卖出不变）：")
        for thr, s in sens.items():
            print(f"    J<{thr:>3}　净值 {s['final']:.4f}　收益 {fmt(s['ret'])}　"
                  f"回撤 {fmt(s['mdd'])}　夏普 {s['sharpe']:.2f}　"
                  f"持仓 {s['hold_rate']:.1f}%　买 {s['n_buy']} 次")

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(allout, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nJSON → {args.json}")


if __name__ == "__main__":
    main()
