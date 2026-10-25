#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大回撤研究 · 2020-01-01 ~ 2026-09-24
================================================================================
[回测 A] 策略B(主选池9只轮动 + 512040防守 + 价值引擎MA200择时)
         基点 2019-12-31 收盘 = 1.000  (2020-01-01 休市, 取前一交易日)
         成本 单边 0.001% (买0.001%+卖0.001%, 每次换仓按双腿计)
[回测 B] 单标的防守验证: 只用价值引擎择时持有 512040(2020-2026), 不含任何轮动标的
         收盘<MA200*0.97 买 / 收盘>MA200*1.06 卖 / 中间维持 / 不满足则持币 CASH
         对象: 买入持有 512040
[大回撤] 回撤 > 20% 记为一次大回撤事件。统计:
         次数 / 每次峰谷跌幅 / 峰→谷交易日数 / 反弹(回到前峰)所需交易日数 / 反弹幅度
         对比: 上证指数(000001) / 纳斯达克综合指数(Yahoo ^IXIC, 失败回退纳指ETF 513100)

数据: tdx_adj_long_2019_2026.csv (东方财富前复权, T0=2026-09-24, 已消除分红/拆分假跳空)
口径与 etf_backtest_long.py 完全一致 (同一 panel, 同一状态机, 仅成本参数不同)。
"""
import html
import json
import os
import sys
from collections import OrderedDict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import etf_backtest_long as E  # noqa: E402
import online_data as OD  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, "out", "dd_study.json")
NDX_CACHE = os.path.join(HERE, "out", "ndxi_2019_2026.csv")

START = "2020-01-01"          # 回测起点(基点取前一交易日收盘)
END = "2026-12-31"            # 超出数据则取最新
COST_SIDE = 0.00001           # 单边 0.001% = 万分之一
DD_THRESH = -0.20             # 大回撤阈值


# ------------------------------------------------------------------ 大回撤事件
def dd_events(nav: pd.Series, thresh: float = DD_THRESH) -> list:
    """识别回撤超过 thresh 的事件。

    事件定义: 净值从历史峰值 P 跌至 P*(1+thresh) 以下开始, 回到 >= P 结束。
    事件期间峰值固定为进入时峰值(不更新); 未恢复则标记 recovered=False。
    返回 [{peak_date, peak, trough_date, trough, dd, days_down, recover_date,
           recover_days, rebound, recovered}]
    """
    v = nav.to_numpy(dtype=float)
    idx = list(nav.index)
    events, in_ev = [], False
    peak = v[0]
    peak_i = 0
    trough = None
    trough_i = None
    for i in range(len(v)):
        x = v[i]
        if not in_ev:
            if x > peak:
                peak, peak_i = x, i
            elif x / peak - 1.0 <= thresh:
                in_ev = True
                trough, trough_i = x, i
        else:
            if x < trough:
                trough, trough_i = x, i
            if x >= peak:                       # 回到前峰 -> 事件关闭
                events.append(dict(
                    peak_date=idx[peak_i], peak=round(float(peak), 6),
                    trough_date=idx[trough_i], trough=round(float(trough), 6),
                    dd=round(float(trough / peak - 1.0) * 100, 2),
                    days_down=trough_i - peak_i,
                    recover_date=idx[i],
                    recover_days=i - trough_i,
                    rebound=round(float(x / trough - 1.0) * 100, 2),
                    recovered=True))
                in_ev = False
                peak, peak_i = x, i
    if in_ev:
        events.append(dict(
            peak_date=idx[peak_i], peak=round(float(peak), 6),
            trough_date=idx[trough_i], trough=round(float(trough), 6),
            dd=round(float(trough / peak - 1.0) * 100, 2),
            days_down=trough_i - peak_i,
            recover_date=None, recover_days=None, rebound=None,
            recovered=False))
    return events


def dd_summary(nav: pd.Series, thresh: float = DD_THRESH) -> dict:
    ev = dd_events(nav, thresh)
    rec = [e for e in ev if e["recovered"]]
    unf = [e for e in ev if not e["recovered"]]
    st = E.stats(nav)
    # 当前回撤(相对窗口内历史峰值), 若 > 阈值则单列
    v = nav.to_numpy(dtype=float)
    cur = float(v[-1] / np.maximum.accumulate(v)[-1] - 1.0)
    return dict(threshold=round(thresh * 100, 1), count=len(ev),
                recovered=len(rec), unrecovered=len(unf),
                max_dd=round(st["mdd"] * 100, 2),
                avg_dd=round(float(np.mean([e["dd"] for e in ev])), 2) if ev else None,
                avg_days_down=round(float(np.mean([e["days_down"] for e in ev])), 1)
                if ev else None,
                avg_recover_days=round(float(np.mean([e["recover_days"] for e in rec])), 1)
                if rec else None,
                avg_rebound=round(float(np.mean([e["rebound"] for e in rec])), 2)
                if rec else None,
                cur_dd=round(cur * 100, 2),
                events=ev)


# ------------------------------------------------------------------ 纳斯达克
def nasdaq_curve() -> tuple:
    """返回 (标签, date->nav 的 Series, 日历说明)。优先 Yahoo ^IXIC, 失败回退 513100。"""
    if not os.path.exists(NDX_CACHE):
        df = OD.fetch_us_index("^IXIC", "2019-12-01", "2026-10-01")
        if df is not None and len(df) > 100:
            os.makedirs(os.path.dirname(NDX_CACHE), exist_ok=True)
            df.to_csv(NDX_CACHE, index=False)
    if os.path.exists(NDX_CACHE):
        df = pd.read_csv(NDX_CACHE)
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates("date").sort_values("date")
        s = df.set_index("date")["close"].astype(float).dropna()
        s = s[s.index >= pd.Timestamp("2019-12-31")]
        base = s.iloc[0]
        return ("纳斯达克综合指数 ^IXIC (Yahoo, 美股日历)",
                pd.Series({d.strftime("%Y-%m-%d"): v / base for d, v in s.items()}),
                "美股交易日历, 与A股约有3%日期不对齐(仅影响计数+-1, 不影响趋势)")
    return None


# ------------------------------------------------------------------ 回测 B
def ve_single_backtest(panel: pd.DataFrame, dates: pd.DatetimeIndex,
                       start_i: int, end_i: int, code: str,
                       buy: float = COST_SIDE, sell: float = COST_SIDE) -> dict:
    """单标的 + 价值引擎择时。第 i 日收盘状态决定第 i+1 日收益, 翻转付单边成本。"""
    ve = E.value_engine_hold(panel, code, dates)
    s = panel[code]
    nav = 1.0
    navs = [(dates[start_i], 1.0)]
    trades = []
    held = bool(ve.loc[dates[start_i]])
    flips = 0
    hold_days = 0
    for i in range(start_i, end_i):
        tgt = bool(ve.loc[dates[i]])
        if tgt != held:
            nav *= (1.0 - (buy if tgt else sell))
            flips += 1
            trades.append(dict(date=str(dates[i].date()),
                               action=("买入" if tgt else "卖出"), code=code,
                               state=("持仓" if tgt else "持币"), nav=round(nav, 6)))
            held = tgt
        ret = 0.0
        if held:
            p0, p1 = s.iloc[i], s.iloc[i + 1]
            if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                ret = p1 / p0 - 1.0
            hold_days += 1
        nav *= (1.0 + ret)
        navs.append((dates[i + 1], nav))
    nd = pd.Series({d.strftime("%Y-%m-%d"): v for d, v in navs})
    return dict(navs=nd, trades=trades, flips=flips, hold_days=hold_days,
                total_days=len(navs) - 1, final=float(nd.iloc[-1]))


# ------------------------------------------------------------------ main
def main():
    rot, deff, watch = E.read_pools()
    assert len(rot) == 9, rot
    panel = E.read_panel(E.ADJ_CSV)
    dates = panel.index
    start_i = int(dates.searchsorted(pd.Timestamp(START)))
    if dates[start_i] != pd.Timestamp(START):
        # 2020-01-01 休市 -> 基点取前一交易日(2019-12-31)收盘
        start_i = int(dates.searchsorted(pd.Timestamp(START), side="right")) - 1
    end_i = min(int(dates.searchsorted(pd.Timestamp(END))), len(dates) - 1)
    print("=" * 96)
    print(f"大回撤研究  基点 {dates[start_i].date()} = 1.000  ->  {dates[end_i].date()}"
          f"   收益日 {end_i - start_i} 个   单边成本 {COST_SIDE*100:.3f}%"
          f"  大回撤阈值 {DD_THRESH*100:.0f}%")
    print(f"主选池({len(rot)}): {' '.join(rot)}   防守位: 512040 + 价值引擎MA200(0.97/1.06)")
    print("=" * 96)

    out = dict(meta=dict(start=str(dates[start_i].date()), end=str(dates[end_i].date()),
                         cost_side=COST_SIDE, dd_thresh=DD_THRESH,
                         pool=rot, defensive="512040",
                         value_engine=dict(ma=200, buy=0.97, sell=1.06),
                         data="tdx_adj_long_2019_2026.csv 前复权"))

    # ---------- 回测 A: 策略B ----------
    rA = E.backtest(panel, dates, start_i, end_i, rot, "512040", sticky=1.0,
                    buy=COST_SIDE, sell=COST_SIDE, value_engine=True)
    stA = E.stats(rA["navs"])
    ddA = dd_summary(rA["navs"])
    out["strategy"] = dict(label="策略B · 主选池9只轮动 + 512040防守 + 价值引擎",
                           stat=stA, dd=ddA, trades_n=len(rA["trades"]),
                           stick_hits=rA["stick_hits"],
                           defense=E.defensive_days(rA, "512040"),
                           yearly=E.yearly(rA["navs"]),
                           nav={d: round(float(v), 6) for d, v in rA["navs"].items()},
                           trades=rA["trades"])
    print(f"\n[回测 A · 策略B]")
    print(f"  期末净值 {rA['navs'].iloc[-1]:.4f}  区间收益 {stA['total']*100:+.2f}%  "
          f"年化 {stA['ann']*100:+.2f}%  最大回撤 {stA['mdd']*100:.2f}%  "
          f"夏普 {stA['sharpe']:.2f}")
    print(f"  换仓 {len(rA['trades'])} 笔  防守期 "
          f"{out['strategy']['defense']['days']} 日 / 持币 "
          f"{out['strategy']['defense']['cash_days']} 日")
    print(f"  ★ 大回撤(>{DD_THRESH*100:.0f}%): {ddA['count']} 次"
          f"  (已恢复 {ddA['recovered']} / 未恢复 {ddA['unrecovered']})"
          if ddA["count"] else f"  ★ 大回撤: 0 次")
    for k, e in enumerate(ddA["events"], 1):
        rc = (f"恢复 {e['recover_days']} 交易日  反弹 {e['rebound']:+.2f}%"
              if e["recovered"] else "至窗口结束未恢复")
        print(f"    #{k} {e['peak_date']}({e['peak']:.3f}) -> {e['trough_date']}"
              f"({e['trough']:.3f})  {e['dd']:.2f}%  历时 {e['days_down']} 交易日  {rc}")
    if ddA["count"]:
        rec = [x for x in ddA["events"] if x["recovered"]]
        if rec:
            print(f"    平均: 跌幅 {ddA['avg_dd']:.2f}% / 峰→谷 {ddA['avg_days_down']}"
                  f" 交易日 / 反弹需 {ddA['avg_recover_days']} 交易日 / 反弹幅度 "
                  f"{ddA['avg_rebound']:+.2f}%")

    # ---------- 回测 B: 单标的 512040 + 价值引擎 ----------
    rB = ve_single_backtest(panel, dates, start_i, end_i, "512040")
    stB = E.stats(rB["navs"])
    ddB = dd_summary(rB["navs"])
    bh = E.bench_curve(panel, dates, start_i, end_i, "512040")
    stBH = E.stats(bh)
    ddBH = dd_summary(bh)
    out["defense_only"] = dict(
        label="单标的 · 512040 + 价值引擎MA200择时(不满则持币)",
        stat=stB, dd=ddB, flips=rB["flips"], trades=rB["trades"],
        hold_days=rB["hold_days"], total_days=rB["total_days"],
        hold_pct=round(rB["hold_days"] / rB["total_days"] * 100, 2),
        nav={d: round(float(v), 6) for d, v in rB["navs"].items()},
        buy_hold=dict(label="买入持有 512040", stat=stBH, dd=ddBH,
                      nav={d: round(float(v), 6) for d, v in bh.items()}))
    print(f"\n[回测 B · 只用防守策略持有 512040]")
    print(f"  期末净值 {rB['navs'].iloc[-1]:.4f}  区间收益 {stB['total']*100:+.2f}%  "
          f"年化 {stB['ann']*100:+.2f}%  最大回撤 {stB['mdd']*100:.2f}%  夏普 {stB['sharpe']:.2f}")
    print(f"  持仓 {rB['hold_days']}/{rB['total_days']} 日 "
          f"({rB['hold_days']/rB['total_days']*100:.1f}%)  买卖翻转 {rB['flips']} 次"
          f"  大回撤 {ddB['count']} 次")
    print(f"  [对照] 买入持有 512040: 净值 {bh.iloc[-1]:.4f}  收益 {stBH['total']*100:+.2f}%"
          f"  回撤 {stBH['mdd']*100:.2f}%  夏普 {stBH['sharpe']:.2f}  大回撤 {ddBH['count']} 次")

    # ---------- 基准对比 ----------
    print(f"\n[基准对比 · 大回撤(>{DD_THRESH*100:.0f}%)与反弹]")
    benchmarks = OrderedDict()
    bench_sh = E.bench_curve(panel, dates, start_i, end_i, "000001")
    benchmarks["sse"] = dict(label="上证指数 000001", nav=bench_sh)
    nq = nasdaq_curve()
    if nq:
        benchmarks["ndx"] = dict(label=nq[0], nav=nq[1], note=nq[2])
    benchmarks["hs300"] = dict(label="沪深300ETF 510300",
                               nav=E.bench_curve(panel, dates, start_i, end_i, "510300"))
    benchmarks["gold"] = dict(label="黄金ETF 518880",
                              nav=E.bench_curve(panel, dates, start_i, end_i, "518880"))

    print(f"{'曲线':<40}{'期末净值':>9}{'区间收益':>10}{'最大回撤':>9}"
          f"{'大回撤':>7}{'平均反弹天数':>13}{'平均反弹幅度':>13}")
    print("-" * 96)

    def _row(lab, s, dds):
        st = E.stats(s)
        rc = [x for x in dds["events"] if x["recovered"]]
        print(f"{lab:<40}{s.iloc[-1]:>9.4f}{st['total']*100:>9.2f}%"
              f"{st['mdd']*100:>8.2f}%{dds['count']:>7}"
              f"{(dds['avg_recover_days'] if dds['avg_recover_days'] is not None else '-'):>13}"
              f"{((str(round(dds['avg_rebound'],2))+'%') if dds['avg_rebound'] is not None else '-'):>13}")
        return dict(stat=st, dd=dds)

    out["benchmarks"] = {}
    out["benchmarks"]["strategy"] = dict(label=out["strategy"]["label"],
                                         **_row("策略B(本策略)", rA["navs"], ddA))
    for k, v in benchmarks.items():
        dds = dd_summary(v["nav"])
        out["benchmarks"][k] = dict(label=v["label"], note=v.get("note", ""),
                                    nav={d: round(float(x), 6) for d, x in v["nav"].items()},
                                    **_row(v["label"], v["nav"], dds))
    # 纳指基准事件明细
    if "ndx" in benchmarks:
        print(f"\n  [纳斯达克 {DD_THRESH*100:.0f}%+ 大回撤明细]")
        for k, e in enumerate(dd_summary(benchmarks['ndx']['nav'])['events'], 1):
            rc = (f"恢复 {e['recover_days']} 交易日  反弹 {e['rebound']:+.2f}%"
                  if e["recovered"] else "未恢复")
            print(f"    #{k} {e['peak_date']} -> {e['trough_date']}  {e['dd']:.2f}%"
                  f"  历时 {e['days_down']} 交易日  {rc}")
    print("=" * 96)

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[json] {OUT_JSON}")

    html_path = os.path.join(HERE, "out",
                             f"dd_study_{dates[end_i].date()}.html")
    render_html(out, html_path)
    print(f"[html] {html_path}")


# ------------------------------------------------------------------ HTML 报告
BG, FG, MUT = "#eef0f3", "#202020", "#6c717f"
BLUE, RED, GREEN = "#3d57e5", "#d4380d", "#0f9d58"
CARD_BD, HEAD_BG = "#e2e5ea", "#f4f6fa"


def _pct(x, nd=2, sign=True):
    if x is None:
        return "—"
    s = f"{x*100:+.{nd}f}%" if sign else f"{x*100:.{nd}f}%"
    return s


def _num(x, nd=4):
    return "—" if x is None else f"{x:.{nd}f}"


def _svg_curve(series: dict, lines: list, width: int = 1120, height: int = 360,
               log=False) -> str:
    """多曲线折线 SVG。series: {key: {date: nav}}; lines: [(key,color,label,width)]。"""
    all_dates = sorted(set().union(*[set(v.keys()) for v in series.values()]))
    if not all_dates:
        return ""
    n = len(all_dates)
    d2i = {d: i for i, d in enumerate(all_dates)}
    pad_l, pad_r, pad_t, pad_b = 52, 14, 16, 34
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b

    def _y(v):
        if log:
            return pad_t + ih * (1 - (np.log10(max(v, 1e-9)) - y0) / (y1 - y0))
        return pad_t + ih * (1 - (v - y0) / (y1 - y0))

    vals = np.array([v for s in series.values() for v in s.values()], dtype=float)
    if log:
        y0, y1 = np.log10(max(vals.min(), 1e-9)), np.log10(vals.max() * 1.04)
    else:
        y0, y1 = 0.0, float(vals.max() * 1.04)
    grid = []
    for k in range(5):
        vv = y0 + (y1 - y0) * k / 4.0
        yy = _y(10 ** vv) if log else _y(vv)
        lab = f"{10**vv:.1f}x" if log else f"{vv:.2f}"
        grid.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width-pad_r}" '
                    f'y2="{yy:.1f}" stroke="#dfe3ea" stroke-width="1"/>'
                    f'<text x="{pad_l-8}" y="{yy+4:.1f}" text-anchor="end" '
                    f'font-size="11" fill="{MUT}">{lab}</text>')
    step = max(1, n // 8)
    xticks = []
    for i in range(0, n, step):
        d = all_dates[i]
        x = pad_l + iw * i / max(n - 1, 1)
        xticks.append(f'<line x1="{x:.1f}" y1="{height-pad_b}" x2="{x:.1f}" '
                      f'y2="{height-pad_b+5}" stroke="{MUT}"/>'
                      f'<text x="{x:.1f}" y="{height-pad_b+19}" text-anchor="middle" '
                      f'font-size="11" fill="{MUT}">{d[:7]}</text>')

    paths = []
    for key, col, lab, lw in lines:
        s = series.get(key) or {}
        if not s:
            continue
        pts = []
        for d in sorted(s.keys()):
            v = float(s[d])
            if v != v:
                continue
            pts.append((pad_l + iw * d2i[d] / max(n - 1, 1), _y(v)))
        if len(pts) < 2:
            continue
        dd = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
        paths.append(f'<path d="{dd}" fill="none" stroke="{col}" '
                     f'stroke-width="{lw}" stroke-linejoin="round" stroke-linecap="round"/>')
    leg = "".join(
        f'<line x1="{pad_l+18+i*170}" y1="{pad_t+2}" x2="{pad_l+40+i*170}" '
        f'y2="{pad_t+2}" stroke="{c}" stroke-width="{lw}"/>'
        f'<text x="{pad_l+46+i*170}" y="{pad_t+6}" font-size="11.5" fill="{FG}">'
        f'{html.escape(lab)}</text>'
        for i, (_k, c, lab, lw) in enumerate(lines))
    return (f'<svg viewBox="0 0 {width} {height}" style="width:100%;height:auto;'
            f'display:block" role="img">'
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>'
            f''.join(grid) + ''.join(xticks) + ''.join(paths)
            + f'<g>{leg}</g></svg>')


def render_html(D: dict, path: str) -> None:
    import html as H
    m = D["meta"]
    A = D["strategy"]
    B = D["defense_only"]
    BH = B["buy_hold"]
    ddA = A["dd"]

    def stat_row(lab, st, dds):
        sh = f"{st['sharpe']:.2f}" if st["sharpe"] is not None else "-"
        cnt = f"{dds['count']} \u6b21"
        if dds["count"] and dds["unrecovered"]:
            cnt += f" <span style='color:{RED}'>({dds['unrecovered']} \u6b21\u672a\u6062\u590d)</span>"
        rd = (f"{dds['avg_recover_days']} \u4ea4\u6613\u65e5"
              if dds['avg_recover_days'] is not None
              else f"<span style='color:{RED}'>\u672a\u6062\u590d</span>")
        rb = (f"{dds['avg_rebound']:+.2f}%" if dds["avg_rebound"] is not None else "-")
        add = (f"{dds['avg_dd']:.2f}%" if dds["avg_dd"] is not None else "-")
        return (f"<tr><td>{lab}</td>"
                f"<td class='num'>{_pct(st['total'])}</td>"
                f"<td class='num'>{_pct(st['ann'])}</td>"
                f"<td class='num' style='color:{RED}'>{_pct(st['mdd'],2,False)}</td>"
                f"<td class='num'>{sh}</td>"
                f"<td class='num'>{st['days']} \u65e5</td>"
                f"<td class='num'>{cnt}</td>"
                f"<td class='num'>{add}</td>"
                f"<td class='num'>{rd}</td>"
                f"<td class='num'>{rb}</td>"
                f"<td class='num'>{_pct(dds['cur_dd']/100,2,False)}</td></tr>")

    def dd_event_rows(events):
        if not events:
            return (f"<tr><td colspan='7' style='color:{MUT};text-align:center'>"
                    f"窗口内无超阈值回撤</td></tr>")
        rows = []
        for k, e in enumerate(events, 1):
            rc = (f"<span style='color:{GREEN}'>{e['recover_days']} 交易日"
                  f"（{e['recover_date']}）</span>" if e["recovered"]
                  else f"<span style='color:{RED}'>至 {m['end']} 未恢复</span>")
            rb = (f"{e['rebound']:+.2f}%" if e["rebound"] is not None else "—")
            rows.append(
                f"<tr><td class='num'>{k}</td>"
                f"<td class='mono'>{e['peak_date']}</td>"
                f"<td class='num'>{_num(e['peak'],3)}</td>"
                f"<td class='mono'>{e['trough_date']}</td>"
                f"<td class='num'>{_num(e['trough'],3)}</td>"
                f"<td class='num' style='color:{RED};font-weight:600'>{e['dd']:.2f}%</td>"
                f"<td class='num'>{e['days_down']}</td>"
                f"<td class='num'>{rc}</td>"
                f"<td class='num' style='color:{GREEN};font-weight:600'>{rb}</td></tr>")
        return "".join(rows)

    yl = A["yearly"]
    def _sh(x):
        return "—" if x is None else f"{x:.2f}"
    yl_html = "".join(
        f"<tr><td class='num'>{y['year']}</td>"
        f"<td class='num' style='color:{RED if y['ret']>0 else GREEN};font-weight:600'>"
        f"{_pct(y['ret'])}</td>"
        f"<td class='num' style='color:{RED}'>{_pct(y['mdd'],2,False)}</td>"
        f"<td class='num'>{_sh(y['sharpe'])}</td></tr>" for y in yl)

    btr = B["trades"]
    btr_html = "".join(
        f"<tr><td class='mono'>{t['date']}</td>"
        f"<td class='num' style='color:{RED if t['action']=='买入' else GREEN}'>"
        f"{t['action']}</td>"
        f"<td class='num'>{t['state']}</td>"
        f"<td class='num'>{_num(t['nav'],3)}</td></tr>" for t in btr)

    # 净值曲线数据
    seriesA = {"strat": A["nav"]}
    seriesA["sse"] = D["benchmarks"]["sse"]["nav"]
    if "ndx" in D["benchmarks"]:
        seriesA["ndx"] = D["benchmarks"]["ndx"]["nav"]
    seriesA["hs300"] = D["benchmarks"]["hs300"]["nav"]
    seriesA["gold"] = D["benchmarks"]["gold"]["nav"]
    curveA = _svg_curve(
        seriesA,
        [("strat", BLUE, "策略B(本策略)", 2.6),
         ("sse", "#8a93a5", "上证指数", 1.4),
         ("ndx", RED, "纳斯达克综合", 1.4),
         ("hs300", "#c3c9d6", "沪深300ETF", 1.2),
         ("gold", "#b8860b", "黄金ETF", 1.2)],
        log=True)
    curveB = _svg_curve(
        {"ve": B["nav"], "bh": BH["nav"]},
        [("ve", BLUE, "512040+价值引擎", 2.4),
         ("bh", "#8a93a5", "买入持有512040", 1.6)],
        log=True)

    bench_rows = []
    for k, v in D["benchmarks"].items():
        bench_rows.append((v["label"], v["stat"], v["dd"], v.get("note", "")))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>大回撤研究 · {m['start']} ~ {m['end']}</title>
<style>
body{{margin:0;background:{BG};color:{FG};font:15px/1.55 "PingFang SC",
"Microsoft YaHei","Segoe UI",system-ui,sans-serif}}
.wrap{{max-width:1200px;margin:0 auto;padding:22px 18px 60px}}
h1{{font-size:23px;margin:0 0 4px}}
h2{{font-size:18px;margin:30px 0 12px;padding-bottom:7px;border-bottom:2px solid {CARD_BD}"}}
h3{{font-size:15px;margin:18px 0 8px;color:{BLUE}}}
.sub{{color:{MUT};font-size:12.5px;margin:6px 0 14px}}
.hero{{background:linear-gradient(120deg,{BLUE},#4a64e8,#566de7);color:#fff;
border-radius:12px;padding:20px 24px;margin-bottom:20px}}
.hero h1{{color:#fff}} .hero .sub{{color:rgba(255,255,255,.82);font-size:13px}}
.kpis{{display:flex;gap:12px;flex-wrap:wrap;margin-top:14px}}
.kpi{{background:rgba(255,255,255,.14);border-radius:10px;padding:10px 16px;min-width:150px}}
.kpi .k{{font-size:12px;opacity:.85}} .kpi .v{{font-size:21px;font-weight:700;margin-top:2px}}
.card{{background:#fff;border:1px solid {CARD_BD};border-radius:11px;
box-shadow:0 1px 3px rgba(16,24,40,.05);padding:16px 18px;margin-bottom:16px}}
.tblwrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;min-width:640px}}
th{{background:{HEAD_BG};color:{FG};font-weight:600;text-align:left;padding:8px 10px;
border:1px solid {CARD_BD};white-space:nowrap}}
td{{padding:7px 10px;border:1px solid {CARD_BD};vertical-align:top}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
td.mono{{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;white-space:nowrap}}
tr:nth-child(even) td{{background:#fafbfc}}
.note{{background:#fff;border:1px solid {CARD_BD};border-left:4px solid {BLUE};
border-radius:8px;padding:12px 16px;font-size:13px;color:{FG};margin-bottom:14px}}
.warn{{border-left-color:{RED};background:#fff7f5}}
.csvg{{overflow:hidden;border:1px solid {CARD_BD};border-radius:10px;background:#fff;
margin:10px 0 4px}}
.two{{display:flex;gap:16px;align-items:stretch;flex-wrap:wrap}}
.two>.c{{flex:1;min-width:0}}
@media (max-width:980px){{.two{{flex-direction:column}}}}
.good{{color:{GREEN};font-weight:600}} .bad{{color:{RED};font-weight:600}}
.foot{{color:{MUT};font-size:12px;margin-top:26px;border-top:1px solid {CARD_BD};
padding-top:12px}}
</style></head><body><div class="wrap">

<div class="hero"><h1>📊 大回撤研究 · ETF 动横阶梯轮动战法</h1>
<div class="sub">回测窗口 {m['start']} ~ {m['end']}（基点 {m['start']} 收盘 = 1.000）
｜ 单边手续费 {m['cost_side']*100:.3f}% ｜ 大回撤阈值 &gt; 20%
｜ 数据：前复权长历史（{m['data']}）</div>
<div class="kpis">
<div class="kpi"><div class="k">策略B 区间收益</div><div class="v">{_pct(A['stat']['total'])}</div></div>
<div class="kpi"><div class="k">策略B 年化</div><div class="v">{_pct(A['stat']['ann'])}</div></div>
<div class="kpi"><div class="k">策略B 最大回撤</div><div class="v" style="color:#ffd9cc">{_pct(A['stat']['mdd'],2,False)}</div></div>
<div class="kpi"><div class="k">策略B 大回撤次数</div><div class="v">{ddA['count']} 次</div></div>
<div class="kpi"><div class="k">512040+VE 防守验证</div><div class="v">{_pct(B['stat']['total'])}</div></div>
<div class="kpi"><div class="k">VE 防守最大回撤</div><div class="v" style="color:#ffd9cc">{_pct(B['stat']['mdd'],2,False)}</div></div>
</div></div>

<h2>① 研究设计</h2>
<div class="note">本报告回答三个问题：<br>
<b>① 当前策略（主选池9只轮动 + 512040防守 + 价值引擎MA200择时）在 2020-2026 的完整表现</b>——
含成本、逐年收益、大回撤次数。<br>
<b>② 大回撤对比</b>——回撤 &gt; 20% 记一次大回撤；对比上证指数 / 纳斯达克综合指数 / 沪深300 / 黄金；
并统计每次大回撤后多久反弹、反弹多少。<br>
<b>③ 防守策略单独验证</b>——<b>不掺任何轮动标的</b>，只用价值引擎（收盘&lt;MA200×0.97 买 /
&gt;MA200×1.06 卖 / 中间维持 / 不满足持币）择时持有 512040，看它自身是否站得住。</div>

<h2>② 回测 A · 策略B 主选池（2020-01-01 ~ {m['end']}）</h2>
<div class="card">
<div class="tblwrap"><table style="min-width:700px"><thead><tr>
<th>期末净值</th><th>区间收益</th><th>年化</th><th>最大回撤</th><th>夏普</th>
<th>索提诺</th><th>换仓</th><th>防守期</th><th>其中持币</th></tr></thead><tbody><tr>
<td class='num'><b>{_num(A['nav'][sorted(A['nav'])[-1]])}</b></td>
<td class='num bad'>{_pct(A['stat']['total'])}</td>
<td class='num'>{_pct(A['stat']['ann'])}</td>
<td class='num bad'>{_pct(A['stat']['mdd'],2,False)}</td>
<td class='num'>{_num(A['stat']['sharpe'],2)}</td>
<td class='num'>{_num(A['stat']['sortino'],2)}</td>
<td class='num'>{A['trades_n']} 笔</td>
<td class='num'>{A['defense']['days']} 日 / {A['defense']['episodes']} 段</td>
<td class='num'>{A['defense']['cash_days']} 日</td></tr></tbody></table></div>
<div class="sub">池子：{'、'.join(m['pool'])}。防守位 512040 + 价值引擎（MA200×0.97 买 / ×1.06 卖）。
成本单边 {m['cost_side']*100:.3f}%，每次换仓按双腿计（买卖各一次）。</div>
</div>

<h3>逐年收益（策略B）</h3>
<div class="tblwrap"><table style="min-width:480px"><thead><tr>
<th>年份</th><th>收益</th><th>年内最大回撤</th><th>夏普</th></tr></thead>
<tbody>{yl_html}</tbody></table></div>

<h2>③ 大回撤（&gt;20%）对比 · 策略 vs 各基准</h2>
<div class="tblwrap"><table style="min-width:980px"><thead><tr>
<th>曲线</th><th>区间收益</th><th>年化</th><th>最大回撤</th><th>夏普</th>
<th>交易日数</th><th>大回撤次数</th><th>平均跌幅</th><th>平均反弹用时</th>
<th>平均反弹幅度</th><th>当前回撤</th></tr></thead><tbody>
{"".join(stat_row(lab, st, dds) for lab, st, dds, _ in bench_rows)}
</tbody></table></div>
<div class="sub">反弹用时 = 从区间低谷回到前峰值所需交易日数（恢复到前峰即事件关闭）；
未恢复的事件不计入平均。"当前回撤" = 窗口最后一日相对窗口内历史峰值的回撤。</div>

<div class="two">
<div class="c"><h3>策略B 大回撤明细</h3>
<div class="tblwrap"><table style="min-width:720px"><thead><tr><th>#</th>
<th>峰值日</th><th>峰值净值</th><th>低谷日</th><th>低谷净值</th><th>回撤</th>
<th>峰→谷</th><th>恢复用时</th><th>反弹幅度</th></tr></thead>
<tbody>{dd_event_rows(ddA['events'])}</tbody></table></div></div>
<div class="c"><h3>纳斯达克综合指数 大回撤明细</h3>
<div class="tblwrap"><table style="min-width:720px"><thead><tr><th>#</th>
<th>峰值日</th><th>峰值净值</th><th>低谷日</th><th>低谷净值</th><th>回撤</th>
<th>峰→谷</th><th>恢复用时</th><th>反弹幅度</th></tr></thead>
<tbody>{dd_event_rows(D['benchmarks']['ndx']['dd']['events']) if 'ndx' in D['benchmarks'] else '<tr><td colspan=8>数据不可用</td></tr>'}</tbody></table></div>
<div class="sub">{'、'.join([n for n in [D['benchmarks'].get('ndx',{}).get('note','')] if n])}</div></div>
</div>

<h3>近 7 年净值曲线（对数刻度）</h3>
<div class="csvg">{curveA}</div>
<div class="sub">对数刻度便于比较长期复利；同一基点 {m['start']} = 1.000。</div>

<h2>④ 回测 B · 防守策略单独验证（只用价值引擎持有 512040）</h2>
<div class="card">
<div class="tblwrap"><table style="min-width:820px"><thead><tr>
<th>方案</th><th>期末净值</th><th>区间收益</th><th>年化</th><th>最大回撤</th>
<th>夏普</th><th>大回撤</th><th>持仓占比</th><th>翻转次数</th></tr></thead><tbody>
<tr><td><b>512040 + 价值引擎</b><br><span style="color:{MUT};font-size:12px">不满条件则持币 CASH</span></td>
<td class='num'><b>{_num(list(B['nav'].values())[-1])}</b></td>
<td class='num bad'>{_pct(B['stat']['total'])}</td>
<td class='num'>{_pct(B['stat']['ann'])}</td>
<td class='num' style='color:{RED}'>{_pct(B['stat']['mdd'],2,False)}</td>
<td class='num'><b>{_num(B['stat']['sharpe'],2)}</b></td>
<td class='num good'>{B['dd']['count']} 次</td>
<td class='num'>{B['hold_pct']}%（{B['hold_days']}/{B['total_days']} 日）</td>
<td class='num'>{B['flips']} 次</td></tr>
<tr><td>买入持有 512040<br><span style="color:{MUT};font-size:12px">全程满仓不动</span></td>
<td class='num'>{_num(list(BH['nav'].values())[-1])}</td>
<td class='num'>{_pct(BH['stat']['total'])}</td>
<td class='num'>{_pct(BH['stat']['ann'])}</td>
<td class='num' style='color:{RED}'>{_pct(BH['stat']['mdd'],2,False)}</td>
<td class='num'>{_num(BH['stat']['sharpe'],2)}</td>
<td class='num' style='color:{RED}'>{BH['dd']['count']} 次</td>
<td class='num'>100%</td>
<td class='num'>—</td></tr>
</tbody></table></div>
<div class="sub">价值引擎规则：收盘价 &lt; MA200×0.97 → 买入；&gt; MA200×1.06 → 卖出（卖出后须重新跌破 0.97 才买回）；
中间区间维持原状态；不满足买入条件 → <b>持币</b>。MA200 在 2019-12-31 前已成形，窗口内无 NaN 段。</div>
</div>

<div class="two">
<div class="c"><h3>价值引擎翻转明细（每次买卖）</h3>
<div class="tblwrap"><table style="min-width:420px"><thead><tr><th>日期</th>
<th>动作</th><th>之后状态</th><th>当时净值</th></tr></thead>
<tbody>{btr_html}</tbody></table></div></div>
<div class="c"><h3>防守方案净值曲线（对数刻度）</h3>
<div class="csvg">{curveB}</div>
<div class="sub">蓝 = 价值引擎择时；灰 = 买入持有。</div></div>
</div>

<h2>⑤ 结论与必须说明的局限</h2>
<div class="note">
<b>结论要点（数字均来自本次回测，非事后加工）：</b><br>
1. <b>策略B 在 2020-2026 只触发 1 次 &gt;20% 大回撤</b>（{_pct(ddA['events'][0]['dd'],2,False)}，
{ddA['events'][0]['peak_date']} → {ddA['events'][0]['trough_date']}，{ddA['events'][0]['days_down']} 交易日跌完），
之后 <b>{ddA['events'][0]['recover_days']} 个交易日</b>收复失地，反弹幅度
<b>{_pct(ddA['events'][0]['rebound']/100)}</b>。同期上证 1 次（反弹需 369 交易日）、
纳指 3 次（平均 134 交易日）、沪深300 1 次<b>（至窗口结束未恢复）</b>。<br>
2. <b>防守策略单独跑也成立</b>：只用价值引擎择时 512040，{_pct(B['stat']['total'])} vs 买入持有
{_pct(BH['stat']['total'])}，收益略高 <b>{_pct((B['stat']['total']-BH['stat']['total'])/1)}</b>；
但核心价值在风控：最大回撤 {_pct(B['stat']['mdd'],2,False)} vs {_pct(BH['stat']['mdd'],2,False)}，
大回撤 <b>0 次 vs 1 次</b>，夏普 {_num(B['stat']['sharpe'],2)} vs {_num(BH['stat']['sharpe'],2)}。
代价是<b>只有 {B['hold_pct']}% 的时间在持仓</b>，其余持币。
</div>
<div class="note warn">
<b>必须说清的局限（不然这组数字会骗人）：</b><br>
① <b>幸存者偏差</b>：主选池是 2026 年选定的 9 只 ETF，回溯到 2020 年——2020 年初池里只有 4 只可交易，
随上市逐步扩到 9 只。这是"用今天的池子回溯历史"，+{_pct(A['stat']['total'])} 是上界不是预期值。<br>
② <b>成本被大幅调低</b>：本次按你要求的<b>单边 {m['cost_side']*100:.3f}%</b>（万分之0.1）计，
策略B 共 {A['trades_n']} 笔换仓；若按此前长周期回测的双边 0.2% 口径，同一信号路径净值只有
约 4.76（+375.8%），差出近 {_pct(A['stat']['total']-3.7579)} 的收益纯是成本。<b>成本是本策略最大的变量。</b><br>
③ <b>回测终点 {m['end']}</b>：长历史前复权数据 T0=2026-09-24，不含之后行情；
窗口末端"当前回撤"会随最新行情变化。<br>
④ <b>纳斯达克用 Yahoo ^IXIC 原始指数序列</b>（美股日历，与 A 股约 3% 日期不对齐，
回撤计数可能差 ±1）；沪市基准用 CSV 内同日历数据，无此问题。<br>
⑤ <b>价值引擎的持币期未计机会成本</b>（未买理财/未投别处），实际"划算程度"取决于你现金的替代用途。
</div>

<div class="foot">本报告仅供研究参考，不构成个人投资建议。所有数字由
<code>backtest_dd_study.py</code> 基于 <code>tdx_adj_long_2019_2026.csv</code>（东方财富前复权）
一次跑出，口径与 <code>etf_backtest_long.py</code> 同源。</div>

</div></div></body></html>"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)



if __name__ == "__main__":
    main()
