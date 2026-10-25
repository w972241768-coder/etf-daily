#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF 轮动战法 · 2026 年内回测引擎 (双策略对照)
================================================================
窗口 : 2025-12-31 收盘 = 1.000  ->  2026-09-23
成本 : 双边 0.2% (卖出侧 0.1% + 买入侧 0.1%)
数据 : ETF 用 TDX 接口【前复权】日线 (tdx_adj_raw.csv, tqFlag=1) —— 已修正
       份额折算(588170 1拆3 / 513660 1拆2) 与分红除息(510300) 的假跳空;
       指数(上证/科创50/创业板指) 用本地 E:/tdx/vipdoc/*/lday/*.day 不复权。
基准 : 513100 纳指ETF / 000001 上证 / 000688 科创50 / 399006 创业板指

策略A = 博主【秋收计划1024】原版「超简最强轮动(含过热保护)」
  轮动池 : 510300 159967 563300 518880 513100
  防守位 : 512040 国信价值
  规则   :
    1) 头名 = 动横20 最大; 领先 = 头名 - 次名
    2) 持有条件: 头名动横20 > 0, 否则空仓            <- 全文唯一的"空仓"机制
    3) 切防守 : 头名乖离 > 12% 或 领先 > 15pp (任一)
    4) 切回轮动: 乖离 < 9% 且 领先 < 10pp 且 已防守满 5 交易日 (同时)
    5) 防守期持有 512040
  [注] 原文另附 512040「200日线×97%买 / ×106%卖」微调, 解读有歧义,
       主口径未叠加该微调 (报告已披露)。
  [验证] 2026-01-01~09-23 复算 vs 博主信号台自报: 忽略成本 41.56% vs 41.56%,
         单边0.1% 35.60% vs 35.71%, 双边0.2% 29.88% vs 30.09% -> 逐档吻合。

策略B = 现池「ETF轮动战法」(用户拍板维持的主策略)
  轮动池 : 9 只 (510300 159967 563300 518880 513100 588170 516080 561700 513660)
  防守   : 512880 证券ETF国泰
  规则   :
    1) 头名 = 动横20 最大
    2) 粘性带: 领先 < 1.0pp -> 保留前一日头名 (降换手)
    3) 切防守: 头名乖离 > 12% 或 领先 > 15pp
    4) 切回  : 乖离 < 9% 且 领先 < 10pp 且 已防守满 5 交易日
    5) 无空仓 (始终满仓: 轮动头名 或 防守标的)

备选观察池 (WATCH_POOL, 2026-09-23 起, 源自博主「其他更替 ETF」面板):
  19 只重要 ETF, **不参与调仓**, 只跟踪 动横20..动横1 动量阶梯,
  作为主池的"潜在更替标的"监控 (阶梯列 k = 今日动横(21-k) = 未来第 k-1 交易日的
  动横20 推演, 假设未来价格冻结)。

执行时点假设 (与截图"收盘价净值"一致):
  第 i 日收盘算信号 -> 同日收盘价成交(换仓成本当日扣) -> 第 i+1 日起按新持仓计收益。
  即: 换仓日标注为信号日; 空仓日收益记 0。
"""

import argparse
import json
import os
import struct
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = "E:/tdx/vipdoc"

# code: (market, scale, 显示名)
UNIVERSE = {
    "510300": ("sh", 1000, "沪深300ETF"),
    "159967": ("sz", 1000, "创成长ETF"),
    "563300": ("sh", 1000, "中证2000ETF"),
    "518880": ("sh", 1000, "黄金ETF"),
    "513100": ("sh", 1000, "纳指ETF"),
    "512040": ("sh", 1000, "国信价值"),
    "512190": ("sh", 1000, "凤凰50"),
    "512880": ("sh", 1000, "证券ETF国泰"),
    "588170": ("sh", 1000, "科创半导体ETF华夏"),
    "516080": ("sh", 1000, "创新药ETF易方达"),
    "561700": ("sh", 1000, "电力ETF博时"),
    "513660": ("sh", 1000, "恒生ETF华夏"),
    # ---- 备选观察池 (不参与回测, 仅动量阶梯监控) ----
    "159259": ("sz", 1000, "成长ETF易方达"),
    "512200": ("sh", 1000, "房地产ETF南方"),
    "159543": ("sz", 1000, "国证2000ETF工银"),
    "159505": ("sz", 1000, "国证2000ETF博时"),
    "588000": ("sh", 1000, "科创50ETF华夏"),
    "588220": ("sh", 1000, "科创100ETF鹏华"),
    "510210": ("sh", 1000, "上证指数ETF富国"),
    "510050": ("sh", 1000, "上证50ETF华夏"),
    "159906": ("sz", 1000, "深成长ETF大成"),
    "512070": ("sh", 1000, "证券保险ETF易方达"),
    "512890": ("sh", 1000, "红利低波ETF华泰柏瑞"),
    "159957": ("sz", 1000, "创业板ETF华夏"),
    "159915": ("sz", 1000, "创业板ETF易方达"),
    "159949": ("sz", 1000, "创业板50ETF华夏"),
    "000001": ("sh", 100, "上证指数"),
    "000688": ("sh", 100, "科创50"),
    "399006": ("sz", 100, "创业板指"),
}

POOL_A = ["510300", "159967", "563300", "518880", "513100"]
POOL_B = ["510300", "159967", "563300", "518880", "513100",
          "588170", "516080", "561700", "513660"]
DEF_A = "512040"
DEF_B = "512880"
# 备选观察池 19 只 (顺序 = 博主面板; 前端按动横20 动态排序)
WATCH_POOL = ["159259", "512200", "159543", "159505", "588000", "588220",
              "588170", "510210", "510050", "516080", "159906", "512070",
              "512880", "512890", "159957", "159915", "561700", "159949",
              "513660"]


_HERE = os.path.dirname(os.path.abspath(__file__))
_ADJ_CAND = [os.environ.get("ETF_ADJ_CSV", ""),
             os.path.join(_HERE, "tdx_adj_raw.csv"),
             os.path.join(_HERE, "..", "tdx_adj_raw.csv"),
             "D:/娱乐（音乐、视频）Q-骑牛/ETF轮动战法/tdx_adj_raw.csv"]
ADJ_CSV = next((p for p in _ADJ_CAND if p and os.path.exists(p)), _ADJ_CAND[-1])


def _read_local_days(code: str) -> pd.Series:
    """读本地通达信 .day (不复权), 用于指数 (指数无复权问题)。"""
    mkt, scale, _name = UNIVERSE[code]
    p = f"{ROOT}/{mkt}/lday/{mkt}{code}.day"
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    raw = open(p, "rb").read()
    n = len(raw) // 32
    dates, closes = [], []
    for i in range(n):
        d, _o, _h, _l, c, _amt, _vol, _ = struct.unpack(
            "<5if2i", raw[i * 32:(i + 1) * 32])
        dates.append(d)
        closes.append(c / scale)
    return pd.Series(closes, index=pd.to_datetime([str(x) for x in dates],
                                                  format="%Y%m%d"))


def read_panel(read_from: str = "2024-01-01") -> pd.DataFrame:
    """返回宽表 (index=date, columns=code, values=close)。

    ETF  : 用 TDX 接口【前复权】序列 (tdx_adj_raw.csv) —— 已修正份额折算(588170/513660)
           与分红除息(510300) 的假跳空, 是净值回测的正确口径。
    指数 : 用本地 .day 不复权 (指数无折算/分红问题)。
    """
    frames: Dict[str, pd.Series] = {}
    adj = pd.read_csv(ADJ_CSV, dtype={"code": str})
    adj["date"] = pd.to_datetime(adj["date"], format="%Y%m%d")
    for code, g in adj.groupby("code"):
        frames[code] = g.sort_values("date").set_index("date")["close"]
    for code, (_m, _s, name) in UNIVERSE.items():
        if code in frames:
            continue
        try:
            frames[code] = _read_local_days(code)
        except FileNotFoundError:
            print(f"[warn] MISSING {code} ({name})")
    panel = pd.DataFrame(frames).sort_index()
    panel = panel[panel.index >= pd.Timestamp(read_from)]
    return panel


def compute_signal(panel: pd.DataFrame, pool: List[str], i: int,
                   W: int = 20, MA: int = 20) -> Optional[dict]:
    """第 i 日收盘信号: 动横20 排名 / 头名 / 次名 / 领先 / 头名乖离。"""
    if i - W < 0 or i - MA + 1 < 0:
        return None
    m: Dict[str, float] = {}
    dev: Dict[str, float] = {}
    for c in pool:
        try:
            p_t = panel[c].iloc[i]
            p_base = panel[c].iloc[i - W]
        except Exception:
            continue
        if pd.isna(p_t) or pd.isna(p_base) or p_base == 0:
            continue
        m[c] = (p_t / p_base - 1.0) * 100.0
        w = panel[c].iloc[i - MA + 1:i + 1]
        if len(w) == MA and not w.isna().any():
            dev[c] = (p_t / w.mean() - 1.0) * 100.0
    if not m:
        return None
    rank = sorted(m.keys(), key=lambda c: m[c], reverse=True)
    head = rank[0]
    second = rank[1] if len(rank) > 1 else None
    lead = (m[head] - m[second]) if second else float("nan")
    return dict(m=m, dev=dev, rank=rank, head=head, second=second, lead=lead)


def value_engine_hold(panel: pd.DataFrame, code: str, dates: pd.DatetimeIndex,
                      ma: int = 200, buy_f: float = 0.97,
                      sell_f: float = 1.06) -> "pd.Series":
    """博主「价值引擎」择时: 收盘价 < MA200×97% -> 买入; > MA200×106% -> 卖出。
    返回逐日 [是否持仓] 布尔序列 (NaN MA 段视为不持仓)。"""
    s = panel[code]
    ma200 = s.rolling(ma).mean()
    hold = False
    out = {}
    for d in dates:
        try:
            p = s.loc[d]; m = ma200.loc[d]
        except KeyError:
            out[d] = False; continue
        if pd.isna(p) or pd.isna(m):
            out[d] = hold; continue
        if (not hold) and p < m * buy_f:
            hold = True
        elif hold and p > m * sell_f:
            hold = False
        out[d] = hold
    return pd.Series(out)


def switch_cost(frm: str, to: str, buy: float = 0.001, sell: float = 0.001,
                flat: bool = False) -> float:
    """换仓成本。flat=True 时每次换仓都按买入+卖出双腿计 (对齐博主信号台口径);
    flat=False 时只计实际发生的一条腿 (进/出空仓只算 0.1%)。"""
    if flat:
        return (0.0 if to == "CASH" and frm == "CASH" else buy + sell)
    c = 0.0
    if frm != "CASH":
        c += sell
    if to != "CASH":
        c += buy
    return c


def backtest(panel: pd.DataFrame, dates: pd.DatetimeIndex,
             start_i: int, end_i: int, pool: List[str], defensive: str,
             *, allow_cash: bool, hold_thresh: float = 5.0, sticky: float = 0.0,
             enter_dev: float = 12.0, enter_lead: float = 15.0,
             exit_dev: float = 9.0, exit_lead: float = 10.0,
             min_def_days: int = 5, buy: float = 0.001, sell: float = 0.001,
             W: int = 20, MA: int = 20, value_engine: bool = False,
             flat_cost: bool = False) -> dict:
    state = "CASH"          # ROT / DEF / CASH
    def_days = 0
    held = "CASH"
    nav = 1.0
    navs = [(dates[start_i], 1.0)]
    trades: List[dict] = []
    prev_eff = None
    cash_days = 0
    held_log: List[tuple] = []
    ve_hold = (value_engine_hold(panel, defensive, dates)
               if value_engine else None)

    for i in range(start_i, end_i):
        sig = compute_signal(panel, pool, i, W, MA)
        if sig is None:                       # 数据不足 -> 维持现有持仓
            ret = 0.0
            if held != "CASH":
                p0, p1 = panel[held].iloc[i], panel[held].iloc[i + 1]
                if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                    ret = p1 / p0 - 1.0
            nav *= (1.0 + ret)
            if held == "CASH":
                cash_days += 1
            held_log.append((dates[i + 1], held))
            navs.append((dates[i + 1], nav))
            continue
        raw_head = sig["head"]
        # ---- 粘性带: 领先不足则沿用前一日头名 ----
        eff_head = raw_head
        tied = False
        if sticky > 0 and prev_eff is not None and sig["lead"] < sticky:
            eff_head = prev_eff
            tied = True
        m_head = sig["m"][raw_head]
        dev_eff = sig["dev"].get(eff_head, float("nan"))

        # ---- 状态机 ----
        prev_state = state
        reason = ""
        if state == "ROT":
            if allow_cash and m_head <= hold_thresh:
                state = "CASH"
                reason = f"头名动横{m_head:+.2f}% ≤ {hold_thresh:.0f}% → 空仓"
            elif dev_eff > enter_dev or sig["lead"] > enter_lead:
                state = "DEF"
                def_days = 0
                reason = (f"过热(乖离{dev_eff:+.2f}%>{enter_dev:.0f}%"
                          if dev_eff > enter_dev else
                          f"过热(领先{sig['lead']:+.2f}pp>{enter_lead:.0f}pp") + " → 切防守"
        elif state == "DEF":
            def_days += 1
            if (dev_eff < exit_dev and sig["lead"] < exit_lead
                    and def_days >= min_def_days):
                state = "ROT"
                def_days = 0
                reason = (f"降温(乖离{dev_eff:+.2f}%<{exit_dev:.0f}% 且 "
                          f"领先{sig['lead']:+.2f}pp<{exit_lead:.0f}pp) 且防守满 "
                          f"{min_def_days} 日 → 切回轮动")
        else:  # CASH
            if m_head > hold_thresh:
                state = "ROT"
                reason = f"头名动横{m_head:+.2f}% > {hold_thresh:.0f}% → 建仓头名"

        # ---- 目标持仓 ----
        if state == "ROT":
            target = eff_head
        elif state == "DEF":
            # 博主口径: 防守端 = 价值引擎持仓中 -> 512040; 否则持币空仓
            ve_ok = (not value_engine) or bool(ve_hold.loc[dates[i]])
            target = defensive if ve_ok else "CASH"
            if not ve_ok:
                reason = reason.replace(" → 切防守", "") + " → 切防守, 但价值引擎空仓 → 持币"
        else:
            target = "CASH"

        # ---- 换仓成本 (信号日收盘成交) ----
        switched = (target != held)
        if switched:
            c = switch_cost(held, target, buy, sell, flat=flat_cost)
            nav *= (1.0 - c)
            trades.append(dict(date=str(dates[i].date()),
                               frm=held, to=target,
                               frm_name=("空仓" if held == "CASH"
                                         else UNIVERSE[held][2]),
                               to_name=("空仓" if target == "CASH"
                                        else UNIVERSE[target][2]),
                               state=state, reason=reason or "持仓调整",
                               tie=bool(tied), nav=round(nav, 6)))

        # ---- 第 i+1 日收益 ----
        ret = 0.0
        if target != "CASH":
            p0, p1 = panel[target].iloc[i], panel[target].iloc[i + 1]
            if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                ret = p1 / p0 - 1.0
        nav *= (1.0 + ret)
        if target == "CASH":
            cash_days += 1
        held = target
        prev_eff = eff_head
        held_log.append((dates[i + 1], target))
        navs.append((dates[i + 1], nav))

    nd = pd.Series({d.strftime("%Y-%m-%d"): v for d, v in navs})
    # ---- 空仓区间 (按实际交易日计数) ----
    ranges: List[list] = []
    run = None
    for d, tgt in held_log:
        if tgt == "CASH":
            if run is None:
                run = [d, d, 1]
            else:
                run[1] = d
                run[2] += 1
        elif run is not None:
            ranges.append(run)
            run = None
    if run is not None:
        ranges.append(run)
    cash_ranges = [dict(start=str(a.date()), end=str(b.date()), days=n,
                        ret=0.0) for a, b, n in ranges]
    assert sum(x["days"] for x in cash_ranges) == cash_days, "空仓日核对失败"
    return dict(navs=nd, trades=trades, cash_days=cash_days,
                cash_ranges=cash_ranges, held_log=held_log,
                final=float(nd.iloc[-1]))


def bench_curve(panel: pd.DataFrame, dates: pd.DatetimeIndex,
                start_i: int, end_i: int, code: str) -> pd.Series:
    base = panel[code].iloc[start_i]
    out = {}
    for i in range(start_i, end_i + 1):
        out[dates[i].strftime("%Y-%m-%d")] = float(panel[code].iloc[i] / base)
    return pd.Series(out)


def stats(nav: pd.Series) -> dict:
    v = nav.to_numpy(dtype=float)
    ret = v[-1] / v[0] - 1.0
    n = len(v) - 1
    ann = (v[-1] / v[0]) ** (244.0 / n) - 1.0 if n > 0 else float("nan")
    peak = np.maximum.accumulate(v)
    dd = (v / peak - 1.0)
    mdd = float(dd.min())
    daily = np.diff(v) / v[:-1]
    vol = float(np.std(daily, ddof=1) * np.sqrt(244)) if len(daily) > 1 else float("nan")
    neg = daily[daily < 0]
    dvol = float(np.std(neg, ddof=1) * np.sqrt(244)) if len(neg) > 1 else float("nan")
    sharpe = (ann / vol) if vol and vol == vol and vol != 0 else float("nan")
    sortino = (ann / dvol) if dvol and dvol == dvol and dvol != 0 else float("nan")
    return dict(total=float(ret), ann=float(ann), mdd=mdd, vol=vol,
                sharpe=float(sharpe) if sharpe == sharpe else None,
                sortino=float(sortino) if sortino == sortino else None,
                days=n)


def _held_summary(r: dict) -> dict:
    """统计各标的持有交易日数与占比。"""
    from collections import Counter
    c = Counter(t for _d, t in r["held_log"])
    tot = sum(c.values()) or 1
    out = []
    for code, n in c.most_common():
        nm = "空仓(货币/现金)" if code == "CASH" else UNIVERSE[code][2]
        out.append(dict(code=code, name=nm, days=n,
                        pct=round(n / tot * 100, 1)))
    return out


def watch_ladder(panel: pd.DataFrame, dates: pd.DatetimeIndex, end_i: int,
                 watch: Optional[List[str]] = None, W: int = 20) -> dict:
    """备选观察池「动横20 — 动横1」动量阶梯 (锚定日快照, 不参与调仓)。

    第 j 列 (j=0..19, 列名 20..1) = 锚定日的 动横(20-j)；
    含义: 假设未来价格冻结, 动横(k) 列就是未来第 (20-k) 个交易日的 动横20 推演
    —— 与博主「其他更替 ETF · 动横20—动横1 动量阶梯」面板同构。
    """
    watch = watch or WATCH_POOL
    codes = [c for c in watch if c in panel.columns]
    t = dates[end_i]
    ladder: Dict[str, list] = {}
    price: Dict[str, float] = {}
    chg1d: Dict[str, Optional[float]] = {}
    for c in codes:
        s = panel[c].loc[:t].dropna()
        if len(s) < W + 1:
            continue
        pt = float(s.iloc[-1])
        # 动横(k) = P_t / P_{t-k} - 1;  t-k = 往前第 k 个交易日 = iloc[-(k+1)]
        ladder[c] = [round((pt / float(s.iloc[-(k + 1)]) - 1.0) * 100.0, 2)
                     for k in range(W, 0, -1)]
        price[c] = round(pt, 3)
        chg1d[c] = (round((pt / float(s.iloc[-2]) - 1.0) * 100.0, 2)
                    if len(s) >= 2 else None)
    codes = [c for c in codes if c in ladder]
    m20 = {c: ladder[c][0] for c in codes}
    rank = sorted(codes, key=lambda c: m20[c], reverse=True)
    head, second = rank[0], rank[1]
    col_heads = {str(W - j): max(codes, key=lambda c: ladder[c][j])
                 for j in range(W)}
    return dict(anchor=str(t.date()), codes=codes,
                names={c: UNIVERSE[c][2] for c in codes},
                ladder=ladder, price=price, chg1d=chg1d, m20=m20,
                rank=rank, head_now=head, head_roc=m20[head],
                second_now=second, second_roc=m20[second],
                lead_now=round(m20[head] - m20[second], 2),
                col_heads=col_heads,
                in_main={c: (c in POOL_B or c == DEF_B) for c in codes},
                W=W)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-12-31", help="起点(收盘=1.000)")
    ap.add_argument("--end", default="2026-09-23", help="终点")
    ap.add_argument("--cost", type=float, default=0.002, help="双边成本(默认0.002)")
    ap.add_argument("--json", default=None, help="结果 JSON 输出路径")
    ap.add_argument("--hold-thresh", type=float, default=0.0,
                    help="空仓阈值: 头名动横20 <= 该值则空仓 (博主口径=0)")
    ap.add_argument("--ve", type=int, default=1,
                    help="策略A是否启用价值引擎择时(512040 MA200×97%%/106%%): 1=启用 0=关闭")
    ap.add_argument("--grid", action="store_true",
                    help="参数标定模式: 网格对比博主自报 2026至今 三档成本")
    ap.add_argument("--leg", action="store_true",
                    help="成本按实际发生腿计 (默认按博主口径: 每次换仓双腿)")
    ap.add_argument("--adj-csv", default=None, help="前复权数据 CSV 路径")
    ap.add_argument("--sticky", type=float, default=1.0,
                    help="策略B 粘性带阈值(pp): 领先 < 该值视为平手, 保留前一头名不换仓 (默认1.0)")
    ap.add_argument("--sticky-scan", action="store_true",
                    help="粘性带敏感性扫描: 0 / 0.25 / 0.5 / 0.75 / 1.0 / 1.5 / 2.0 / 3.0")
    a = ap.parse_args()
    if a.adj_csv:
        global ADJ_CSV
        ADJ_CSV = a.adj_csv

    panel = read_panel("2025-06-01")
    dates = panel.index
    start_i = int(dates.searchsorted(pd.Timestamp(a.start)))
    end_i = int(dates.searchsorted(pd.Timestamp(a.end)))
    if dates[start_i] != pd.Timestamp(a.start):
        print(f"[warn] 起点 {a.start} 非交易日, 用 {dates[start_i].date()}")
    print(f"数据区间: {dates[0].date()} ~ {dates[-1].date()}  共 {len(dates)} 条")
    print(f"回测窗口: {dates[start_i].date()} ~ {dates[end_i].date()}  "
          f"共 {end_i - start_i + 1} 个交易日 (含起点) / {end_i - start_i} 个收益日")

    buy = sell = a.cost / 2.0
    flat = not a.leg

    if a.grid:
        print("\n【参数标定】策略A 对齐博主信号台自报 2026至今收益")
        print("博主自报: 忽略成本 +41.56% / 单边0.1% +35.71% / 双边0.2% +30.09%\n")
        for ht in (0.0, 5.0):
            for ve in (1, 0):
                row = []
                for cost in (0.0, 0.001, 0.002):
                    r = backtest(panel, dates, start_i, end_i, POOL_A, DEF_A,
                                 allow_cash=True, hold_thresh=ht, sticky=0.0,
                                 buy=cost / 2, sell=cost / 2,
                                 value_engine=bool(ve), flat_cost=(cost > 0))
                    row.append(stats(r["navs"])["total"] * 100)
                print(f"hold_thresh={ht:.0f} 价值引擎={'开' if ve else '关'}  "
                      f"成本0%:{row[0]:>7.2f}%  0.1%:{row[1]:>7.2f}%  "
                      f"0.2%:{row[2]:>7.2f}%   换仓{len(r['trades'])}笔 空仓{r['cash_days']}日")
        print("\n(命中判定: 与博主 41.56/35.71/30.09 同时接近的即为正解)")
        return

    if a.sticky_scan:
        print("\n【粘性带敏感性】策略B(现池) —— 领先 < 阈值 视为平手, 保留前一头名、不换仓")
        print(f"{'粘性带':>9}{'期末净值':>11}{'区间收益':>10}{'年化':>9}"
              f"{'最大回撤':>10}{'夏普':>7}{'换仓':>6}{'vs 1.0pp':>10}")
        print("-" * 74)
        res = {}
        for st in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
            r = backtest(panel, dates, start_i, end_i, POOL_B, DEF_B,
                         allow_cash=False, hold_thresh=a.hold_thresh, sticky=st,
                         buy=buy, sell=sell, flat_cost=flat)
            res[st] = (r, stats(r["navs"]))
        ref_n = len(res[1.0][0]["trades"])
        for st, (r, s) in res.items():
            n = len(r["trades"])
            mark = "  ← 当前" if abs(st - 1.0) < 1e-9 else ""
            print(f"{st:>8.2f}pp{r['navs'].iloc[-1]:>11.4f}{s['total']*100:>9.2f}%"
                  f"{s['ann']*100:>8.2f}%{s['mdd']*100:>9.2f}%{s['sharpe']:>7.2f}"
                  f"{n:>6}{n - ref_n:>+10d}{mark}")
        print("(口径: 2025-12-31=1.000 → 2026-09-23, 178个交易日, 双边0.2%; "
              "策略B 始终满仓, 防守位 512880)")
        return

    resA = backtest(panel, dates, start_i, end_i, POOL_A, DEF_A,
                    allow_cash=True, hold_thresh=a.hold_thresh, sticky=0.0,
                    buy=buy, sell=sell, value_engine=bool(a.ve),
                    flat_cost=flat)
    resB = backtest(panel, dates, start_i, end_i, POOL_B, DEF_B,
                    allow_cash=False, hold_thresh=a.hold_thresh, sticky=a.sticky,
                    buy=buy, sell=sell, flat_cost=flat)
    watch = watch_ladder(panel, dates, end_i)

    benches = {c: bench_curve(panel, dates, start_i, end_i, c)
               for c in ["513100", "000001", "000688", "399006"]}

    print("\n" + "=" * 92)
    print(f"{'曲线':<22}{'期末净值':>11}{'区间收益':>10}{'年化':>9}{'最大回撤':>10}"
          f"{'夏普':>7}{'换仓':>6}{'空仓日':>7}")
    print("-" * 92)
    rows = [("策略A·博主原版", resA["navs"], len(resA["trades"]), resA["cash_days"]),
            ("策略B·现池(维持)", resB["navs"], len(resB["trades"]), resB["cash_days"]),
            ("纳指ETF 513100", benches["513100"], None, None),
            ("上证指数 000001", benches["000001"], None, None),
            ("科创50 000688", benches["000688"], None, None),
            ("创业板指 399006", benches["399006"], None, None)]
    for nm, s, nt, cd in rows:
        st = stats(s)
        sh = f"{st['sharpe']:.2f}" if st["sharpe"] is not None else "-"
        print(f"{nm:<22}{s.iloc[-1]:>11.4f}{st['total']*100:>9.2f}%"
              f"{st['ann']*100:>8.2f}%{st['mdd']*100:>9.2f}%{sh:>7}"
              f"{(nt if nt is not None else '-'):>6}{(cd if cd is not None else '-'):>7}")
    print("=" * 92)
    print(f"\n备选观察池头名(动横20): {UNIVERSE[watch['head_now']][2]}"
          f"({watch['head_now']}) {watch['head_roc']:+.2f}%  "
          f"次名 {UNIVERSE[watch['second_now']][2]}"
          f"({watch['second_now']}) {watch['second_roc']:+.2f}%  "
          f"领先 {watch['lead_now']:.2f}pp  | 明日推演头名: "
          f"{UNIVERSE[watch['col_heads']['19']][2]}({watch['col_heads']['19']})")

    for tag, r in [("策略A(博主原版)", resA), ("策略B(现池)", resB)]:
        print(f"\n【{tag}】操作明细 (共 {len(r['trades'])} 笔换仓, "
              f"空仓 {r['cash_days']} 个交易日):")
        for t in r["trades"]:
            print(f"  {t['date']}  {t['frm_name']}({t['frm']}) → "
                  f"{t['to_name']}({t['to']})   [{t['state']}]  {t['reason']}")
        if r["cash_ranges"]:
            print(f"  空仓区间: " + "; ".join(
                f"{x['start']}~{x['end']}({x['days']}个交易日, 收益记0)"
                for x in r["cash_ranges"]))
        else:
            print("  空仓区间: 无 (全程满仓)")

    if a.json:
        # 诊断数据: 9只池逐日排名 / 头名天数 (供报告"某只为何没持仓"章节)
        roc9 = pd.DataFrame({c: panel[c].pct_change(20) * 100.0
                             for c in POOL_B}).loc[dates[start_i:end_i + 1]]
        rank9 = roc9.rank(axis=1, ascending=False)
        _arr = roc9.to_numpy(dtype=float)
        _srt = np.sort(_arr, axis=1)
        _ord = np.argsort(_arr, axis=1)
        lead_B = pd.Series(_srt[:, -1] - _srt[:, -2], index=roc9.index)
        second_name_B = [str(roc9.columns[_ord[i, -2]]) for i in range(len(roc9))]
        thermo_B = roc9.max(axis=1)

        def _jl(s):
            return [round(float(x), 4) if x == x else None for x in s.tolist()]

        out = dict(
            dates=[d.strftime("%Y-%m-%d") for d in dates[start_i:end_i + 1]],
            nav_A=[round(x, 6) for x in resA["navs"].tolist()],
            nav_B=[round(x, 6) for x in resB["navs"].tolist()],
            bench={c: [round(x, 6) for x in benches[c].tolist()]
                   for c in benches},
            trades_A=resA["trades"], trades_B=resB["trades"],
            stat_A=stats(resA["navs"]), stat_B=stats(resB["navs"]),
            stat_bench={c: stats(benches[c]) for c in benches},
            cash_days_A=resA["cash_days"], cash_days_B=resB["cash_days"],
            cash_ranges_A=resA["cash_ranges"], cash_ranges_B=resB["cash_ranges"],
            held_days_A=_held_summary(resA), held_days_B=_held_summary(resB),
            watch=watch,
            diag=dict(head_days_B={c: int((rank9[c] == 1).sum()) for c in POOL_B},
                      rank_513100=[int(x) if x == x else None
                                   for x in rank9["513100"].tolist()],
                      roc_513100=_jl(roc9["513100"]),
                      lead_B=_jl(lead_B),
                      second_name_B=second_name_B,
                      thermo_B=_jl(thermo_B)),
            meta=dict(start=str(dates[start_i].date()), end=str(dates[end_i].date()),
                      cost=a.cost, cost_mode=("flat(博主口径,每次换仓双边)"
                                              if flat else "leg(按实际发生腿)"),
                      window_days=end_i - start_i + 1,
                      hold_thresh_A=a.hold_thresh, value_engine_A=bool(a.ve),
                      pool_A=POOL_A, pool_B=POOL_B, def_A=DEF_A, def_B=DEF_B,
                      watch_pool=WATCH_POOL,
                      names={c: UNIVERSE[c][2] for c in UNIVERSE}),
        )
        os.makedirs(os.path.dirname(a.json), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\n[json] 已写 {a.json}")


if __name__ == "__main__":
    main()
