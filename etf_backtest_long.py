#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF 轮动战法 · 策略B 长周期回测 (2020-2026)
================================================================================
起点 : 2019-12-31 收盘 = 1.000  (2020-01-01 为休市日, 取前一交易日收盘作基点)
终点 : 数据源最新交易日
成本 : 双边 0.2% (买入 0.1% + 卖出 0.1%, 每次换仓按双腿计 —— 与 2026 版口径一致)
数据 : 东方财富【前复权】日线 (fqt=1), tdx_adj_long_2019_2026.csv
       已消除份额折算 / 分红除息假跳空。

策略B 规则 (与 2026 版 etf_backtest_2026.py 逐条一致, 参数一个未改):
  1) 头名 = 动横20 (P_t/P_{t-20}-1) 最大者
  2) 粘性带: 领先 < 1.0pp 视为平手 -> 保留前一日头名 (降换手)
  3) 切防守: 头名乖离 > 12%  或  领先 > 15pp
  4) 切回轮动: 乖离 < 9%  且  领先 < 10pp  且 已防守满 5 个交易日
  5) 防守标的 = 512040 价值ETF富国 (2026-09-24 由 512880 证券ETF国泰 替换)
  6) 防守期叠加「价值引擎」择时 (--ve 1, 博主原版):
       收盘 < MA200×0.97 -> 买 512040 ; 收盘 > MA200×1.06 -> 卖
     不满足 -> 持币 CASH。★ 这打破了原策略B「始终满仓」的设计, 属设计变更。
  7) 成交时点: 第 i 日收盘算信号 -> 同日收盘换仓 -> 第 i+1 日起计收益

三组独立回测 (池子不同, 规则完全相同):
  主选池组 = 9 只轮动  (510300 159967 563300 518880 513100 588170 516080 561700 513660)
  备选池组 = 14 只     (etf_pool.csv 中 role=watch 且不在主选池的标的)
  参照组   = 主+备 23 只 (仅作参考, 不是策略B)

★ 动态池 (survivorship / 上市日期处理):
  标的必须已上市且有完整 21 根K线 (P_t 与 P_{t-20} 均有效) 才进入当日排名。
  因此 2020 年初主选池实际只有 4 只可交易, 随 ETF 陆续上市逐步扩到 9 只。
  这是"用今天的池子回溯历史"的必然结果, 报告中逐年披露可用标的数, 不做任何事后美化。
"""
import argparse
import json
import os
from collections import Counter, OrderedDict
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ADJ_CSV = os.path.join(HERE, "tdx_adj_long_2019_2026.csv")
POOL_CSV = os.path.join(HERE, "etf_pool.csv")

# ---- 名称表 (与 etf_pool.csv 一致; 报告用) ----
NAMES = {
    "510300": "沪深300ETF", "159967": "创成长ETF", "563300": "中证2000ETF",
    "518880": "黄金ETF", "513100": "纳指ETF", "588170": "科创半导体ETF华夏",
    "516080": "创新药ETF易方达", "561700": "电力ETF博时", "513660": "恒生ETF华夏",
    "512880": "证券ETF国泰", "159259": "成长ETF易方达", "512200": "房地产ETF南方",
    "159543": "国证2000ETF工银", "159505": "国证2000ETF博时", "588000": "科创50ETF华夏",
    "588220": "科创100ETF鹏华", "510210": "上证指数ETF富国", "510050": "上证50ETF华夏",
    "159906": "深成长ETF大成", "512070": "证券保险ETF易方达",
    "512890": "红利低波ETF华泰柏瑞", "159957": "创业板ETF华夏",
    "159915": "创业板ETF易方达", "159949": "创业板50ETF华夏",
    "512040": "价值ETF富国", "000001": "上证指数", "000688": "科创50", "399006": "创业板指",
}
DEFENSIVE = "512040"
BENCH = ["000001", "000688", "399006", "513100"]
VE_MA = 200
VE_BUY = 0.97
VE_SELL = 1.06


def read_pools() -> tuple:
    """从 etf_pool.csv 读取 (主选池轮动, 防守, 备选观察池) —— 唯一配置入口。"""
    df = pd.read_csv(POOL_CSV, dtype={"code": str}, encoding="utf-8-sig")
    df["role"] = df["role"].fillna("").astype(str).str.strip()
    df["code"] = df["code"].str.strip()
    rot = df.loc[df["role"] == "", "code"].tolist()
    deff = df.loc[df["role"] == "defensive", "code"].tolist()
    watch = df.loc[df["role"] == "watch", "code"].tolist()
    return rot, deff, watch


def read_panel(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"code": str})
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    frames = OrderedDict()
    for code, g in df.groupby("code"):
        g = g.sort_values("date").drop_duplicates("date", keep="last")
        frames[code] = g.set_index("date")["close"].astype(float)
    panel = pd.DataFrame(frames).sort_index()
    return panel


def compute_signal(panel: pd.DataFrame, pool: List[str], i: int,
                   W: int = 20, MA: int = 20) -> Optional[dict]:
    """第 i 日收盘信号。上市不足 / 数据缺失的标的自动被跳过 (动态池)。"""
    if i - W < 0 or i - MA + 1 < 0:
        return None
    m: Dict[str, float] = {}
    dev: Dict[str, float] = {}
    for c in pool:
        if c not in panel.columns:
            continue
        s = panel[c]
        try:
            p_t = s.iloc[i]
            p_base = s.iloc[i - W]
        except Exception:                    # noqa: BLE001
            continue
        if pd.isna(p_t) or pd.isna(p_base) or p_base == 0:
            continue
        m[c] = (p_t / p_base - 1.0) * 100.0
        w = s.iloc[i - MA + 1:i + 1]
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
                      ma: int = VE_MA, buy_f: float = VE_BUY,
                      sell_f: float = VE_SELL) -> pd.Series:
    """博主「价值引擎」择时 (逻辑逐行移植自已验证的 etf_backtest_2026.py)。

    收盘价 < MA200×97%  -> 买入持有
    收盘价 > MA200×106% -> 卖出 (此后再跌需重新跌破 97% 才买回 = 状态机式)
    两者之间            -> 维持原持仓状态
    MA200 尚未成形 (NaN) 段沿用此前状态。
    """
    s = panel[code]
    ma200 = s.rolling(ma).mean()
    hold = False
    out = {}
    for d in dates:
        try:
            p = s.loc[d]
            m = ma200.loc[d]
        except KeyError:
            out[d] = False
            continue
        if pd.isna(p) or pd.isna(m):
            out[d] = hold
            continue
        if (not hold) and p < m * buy_f:
            hold = True
        elif hold and p > m * sell_f:
            hold = False
        out[d] = hold
    return pd.Series(out)


def roc_head_state(panel: pd.DataFrame, pool: List[str], dates: pd.DatetimeIndex,
                   W: int = 20, n: int = 60, q: float = 0.95,
                   min_periods: int = 250):
    """头名 ROC(n) 的「是否触发 / 当日分位 / 当日 ROC / 当日阈值」四件套。

    头名 = 当日动横 W 最高的标的；取其 n 日动量 ROC(n)，与**该序列自身的滚动扩张 q 分位**比较
    （只用历史，无前视偏差）。分位 = 当日值在自身扩张历史中的百分位（0~100）。

    返回 (hold: bool Series, pct: float Series, roc: float Series, thr: float Series)。
    分位与阈值同源同口径，因此「pct >= q*100」与「hold == True」严格等价。
    """
    empty = (pd.Series(False, index=dates), pd.Series(np.nan, index=dates),
             pd.Series(np.nan, index=dates), pd.Series(np.nan, index=dates))
    mom = {c: (panel[c] / panel[c].shift(W) - 1.0) * 100.0
           for c in pool if c in panel.columns}
    if not mom:
        return empty
    mom_df = pd.DataFrame(mom)
    # 取每行动横最大的列名（=当日头名）。
    # ⚠ 不用 mom_df.idxmax(axis=1)：pandas 对「全 NA 行」的行为已废弃警告、
    #   3.0 起直接抛 ValueError（回测前 W 行必然全 NA）。这里手写等价实现：
    #   跳过 NaN 取最大值所在列（并列取首个），全 NA 行返回 NaN —— 与 2.x 语义一致，
    #   且不随 pandas 版本变化。
    _mat = mom_df.to_numpy(dtype="float64", copy=True)
    _cols = mom_df.columns.to_numpy()
    _allna = np.isnan(_mat).all(axis=1)
    _arg = np.where(np.isnan(_mat), -np.inf, _mat).argmax(axis=1)
    head_codes = pd.Series(np.where(_allna, np.nan, _cols[_arg].astype(object)),
                           index=mom_df.index)
    roc_df = pd.DataFrame({c: (panel[c] / panel[c].shift(n) - 1.0) * 100.0
                           for c in mom_df.columns if c in panel.columns})
    if roc_df.empty:
        return empty
    stacked = roc_df.stack(future_stack=True)
    hv = stacked.reindex(pd.MultiIndex.from_arrays(
        [head_codes.index, head_codes.values])).reset_index(level=1, drop=True)
    hv.index.name = roc_df.index.name
    hv = hv.reindex(roc_df.index)
    exp = hv.expanding(min_periods=min_periods)
    thr = exp.quantile(q)
    pct = exp.rank(pct=True) * 100.0          # 当日值在自身扩张历史中的百分位
    hold = (hv >= thr)
    return (hold.reindex(dates).fillna(False).astype(bool),
            pct.reindex(dates), hv.reindex(dates), thr.reindex(dates))


def roc_head_hold(panel: pd.DataFrame, pool: List[str], dates: pd.DatetimeIndex,
                  W: int = 20, n: int = 60, q: float = 0.95,
                  min_periods: int = 250) -> pd.Series:
    """「头名 ROC(n) 进入自身滚动 q 分位」的布尔序列 —— P3 追高止盈的触发信号。

    机制（2026-10-24 验证）：
      头名 = 当日动横 W 最高的标的；取其 n 日动量 ROC(n)；
      若 >= 该序列的**滚动扩张 q 分位**（只用历史，无前视偏差）⇒ 触发。
    实测该信号触发后头名未来 20 日中位 -4.11%、胜率仅 37%（动量透支）。
    """
    return roc_head_state(panel, pool, dates, W, n, q, min_periods)[0]


def backtest(panel: pd.DataFrame, dates: pd.DatetimeIndex, start_i: int, end_i: int,
             pool: List[str], defensive: str, *, sticky: float = 1.0,
             enter_dev: float = 12.0, enter_lead: float = 15.0,
             exit_dev: float = 9.0, exit_lead: float = 10.0,
             min_def_days: int = 5, buy: float = 0.001, sell: float = 0.001,
             W: int = 20, MA: int = 20, value_engine: bool = True,
             loss_cool: Optional[float] = None, loss_days: int = 0,
             roc_q: Optional[float] = None, roc_days: int = 20,
             roc_n: int = 60, roc_min_periods: int = 250,
             gate_mask: Optional[pd.Series] = None,
             sig_list: Optional[list] = None,
             pos_scale: Optional[pd.Series] = None,
             pos_exempt: Optional[set] = None) -> dict:
    """策略B 状态机 (ROT / DEF)。value_engine=True 时防守期叠加价值引擎择时,
    不满足条件则持币 CASH —— 这会打破策略B「始终满仓」的设计, 属设计变更非缺陷。

    loss_cool / loss_days：可选的「亏损冷却」(D3)。默认 None = 完全关闭、行为与旧版逐位一致。
    开启后：轮动期里若某笔在换出时相对建仓价亏损 >= loss_cool，则不立刻买入新头名，
    改为空仓 loss_days 个交易日，之后按当日头名恢复轮动。只改「卖出后多久回来」，不改卖出条件。

    roc_q / roc_days：可选的「追高止盈」(P3)。默认 None = 完全关闭。
    开启后：头名 ROC(roc_n) 进入自身滚动 roc_q 分位（动量透支）⇒ 空仓 roc_days 个交易日，
    之后按当日头名恢复轮动。与 D3 可叠加：两个冷却各自独立计时，谁先到期谁先解除，
    目标持仓优先级 D3 > P3（与 roc_cash_* 研究引擎逐位一致）。
    ⚠️ P3 是**独立规则**，不依赖 D3：不设 loss_cool 单独开 roc_q 即「仅 P3」。

    gate_mask：可选的「广度闸门」，pd.Series（与 dates 对齐的布尔）。默认 None = 关闭。
    开启后：轮动期中该日为 True ⇒ 强制空仓（CASH），直到该日转为 False 才按当日头名恢复。
    仅改「能不能在场」，不改选股与卖出条件；与 D3/P3 可叠加，目标持仓优先级 D3 > P3 > 闸门。

    sig_list：可选的预计算信号列表（compute_signal 的结果，按日期索引）。默认 None =
    每步现场计算。研究型脚本传入可省 ~10 倍耗时；传入与不传结果逐位一致。
    """
    state = "ROT"
    def_days = 0
    held = "CASH"
    nav = 1.0
    navs = [(dates[start_i], 1.0)]
    trades: List[dict] = []
    prev_eff = None
    held_log: List[tuple] = []
    avail_log: List[tuple] = []
    head_log: List[tuple] = []
    stick_hits = 0
    ve_hold = (value_engine_hold(panel, defensive, dates)
               if value_engine else None)
    entry_px: Optional[float] = None     # 当前持仓的建仓价（收盘口径）
    cool_until = -1                      # 冷却到期日（含），仅 loss_cool 开启时用
    cools: List[dict] = []               # 冷却事件明细
    cool_days_n = 0
    roc_hold = roc_pct = roc_val = None
    if roc_q is not None:
        roc_hold, roc_pct, roc_val, _roc_thr = roc_head_state(
            panel, pool, dates, W, roc_n, roc_q, roc_min_periods)
    roc_until = -1                       # P3 空仓到期日（含），仅 roc_q 开启时用
    roc_cools: List[dict] = []           # P3 触发事件明细
    roc_days_n = 0
    gate_days_n = 0                      # 广度闸门生效天数（仅 gate_mask 开启时用）
    gate_trig_n = 0                      # 闸门新致空仓的次数

    for i in range(start_i, end_i):
        sig = (sig_list[i] if sig_list is not None
               else compute_signal(panel, pool, i, W, MA))
        if sig is None:                        # 数据不足 -> 维持现有持仓
            ret = 0.0
            if held != "CASH" and held in panel.columns:
                p0, p1 = panel[held].iloc[i], panel[held].iloc[i + 1]
                if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                    ret = p1 / p0 - 1.0
            nav *= (1.0 + ret)
            avail_log.append((dates[i + 1], 0))
            head_log.append((dates[i + 1], None))
            held_log.append((dates[i + 1], held))
            navs.append((dates[i + 1], nav))
            continue

        raw_head = sig["head"]
        eff_head = raw_head
        tied = False
        # 粘性带只在"前一头名今天仍可交易(仍在排名池内)"时才生效;
        # 若该标的当日已不可用(如尚未上市/数据缺失)则必须放弃粘性, 直接用真实头名。
        if prev_eff is not None and prev_eff not in sig["rank"]:
            prev_eff = None
        if sticky > 0 and prev_eff is not None and sig["lead"] == sig["lead"] \
                and sig["lead"] < sticky:
            eff_head = prev_eff
            tied = True
            stick_hits += 1
        m_head = sig["m"][raw_head]
        dev_eff = sig["dev"].get(eff_head, float("nan"))

        prev_state = state
        reason = ""
        if state == "ROT":
            if (dev_eff == dev_eff and dev_eff > enter_dev) or \
               (sig["lead"] == sig["lead"] and sig["lead"] > enter_lead):
                state = "DEF"
                def_days = 0
                reason = (f"过热(乖离{dev_eff:+.2f}%>{enter_dev:.0f}%"
                          if (dev_eff == dev_eff and dev_eff > enter_dev)
                          else f"过热(领先{sig['lead']:+.2f}pp>{enter_lead:.0f}pp") \
                         + " -> 切防守"
        elif state == "DEF":
            def_days += 1
            if (dev_eff == dev_eff and dev_eff < exit_dev) and \
               (sig["lead"] == sig["lead"] and sig["lead"] < exit_lead) and \
               def_days >= min_def_days:
                state = "ROT"
                def_days = 0
                reason = (f"降温(乖离{dev_eff:+.2f}%<{exit_dev:.0f}% 且 "
                          f"领先{sig['lead']:+.2f}pp<{exit_lead:.0f}pp) 且防守满 "
                          f"{min_def_days} 日 -> 切回轮动")
        # 目标持仓: ROT -> 粘性后头名; DEF -> 价值引擎满足则持防守位, 否则持币
        note_cool = ""
        if state == "ROT":
            target = eff_head
            if loss_cool is not None:
                if i < cool_until:                       # 冷却中 -> 继续空仓
                    target = "CASH"
                    note_cool = f" | 冷却中(至 {dates[cool_until].date()})"
                elif (target != held and held != "CASH" and entry_px
                      and held in panel.columns):
                    _px = panel[held].iloc[i]
                    if not pd.isna(_px) and _px / entry_px - 1.0 <= -loss_cool:
                        _cr = float(_px) / entry_px - 1.0
                        target = "CASH"
                        cool_until = min(i + loss_days, len(dates) - 1)
                        cool_days_n += 1
                        cools.append(dict(
                            date=str(dates[i].date()), code=held,
                            name=NAMES.get(held, held), ret=round(_cr * 100, 2),
                            until=str(dates[cool_until].date()),
                            days=int(np.searchsorted(dates, dates[cool_until]) - i)))
            # ---- P3 追高止盈：头名 ROC(n) 进自身滚动 q 分位 -> 空仓 roc_days 日 ----
            # 与 D3 独立判定、独立计时；目标持仓优先级 D3 > P3（D3 已置 CASH 时不再覆盖）
            if roc_q is not None and held != "CASH" and i >= roc_until \
                    and bool(roc_hold.iloc[i]):
                roc_until = min(i + roc_days, len(dates) - 1)
                roc_days_n += 1
                # ⚠️ 三处口径必须分清（2026-10-25 修）：
                #   · 触发依据 = **当日动横 W 排名第 1 名**（raw_head）的 ROC(n) 进 95 分位；
                #   · 当日卖出 = **当时实际持仓**（held，可能是更早买入、尚未换仓的那只）；
                #   · 旧版把 roc 写成 sig["m"][held]（那是动横20 且是被持仓的），
                #     导致卡片「ROC60」列显示的其实是持仓的动横20，已修。
                _rv = float(roc_val.iloc[i]) if roc_val is not None else float("nan")
                _pv = float(roc_pct.iloc[i]) if roc_pct is not None else float("nan")
                _hm = sig["m"].get(raw_head, float("nan"))
                _pm = sig["m"].get(held, float("nan"))
                roc_cools.append(dict(
                    date=str(dates[i].date()),
                    head=raw_head, head_name=NAMES.get(raw_head, raw_head),
                    roc=(round(_rv, 2) if _rv == _rv else None),
                    pct=(round(_pv, 1) if _pv == _pv else None),
                    m20=(round(float(_hm), 2) if _hm == _hm else None),
                    code=held, name=NAMES.get(held, held),
                    held_m20=(round(float(_pm), 2) if _pm == _pm else None),
                    until=str(dates[roc_until].date()),
                    days=int(np.searchsorted(dates, dates[roc_until]) - i)))
                if target != "CASH":
                    note_cool = (f" | P3 追高止盈(头名ROC{roc_n} 进历史"
                                 f"{roc_q*100:.0f}分位) → 空仓{roc_days}日")
            if roc_q is not None and i < roc_until and target != "CASH":
                target = "CASH"
                if not note_cool:
                    note_cool = f" | P3 空仓中(至 {dates[roc_until].date()})"
            # ---- 广度闸门（可选）：池内动横20 全为负 -> 强制空仓，恢复才回场 ----
            if gate_mask is not None and bool(gate_mask.iloc[i]):
                gate_days_n += 1
                if target != "CASH":
                    gate_trig_n += 1
                    note_cool = ((note_cool + " | 全池动横20为负→空仓")
                                 if note_cool else " | 全池动横20为负→空仓")
                target = "CASH"
        else:
            ve_ok = (not value_engine) or bool(ve_hold.loc[dates[i]])
            target = defensive if ve_ok else "CASH"
            if not ve_ok:
                reason = reason + " | 价值引擎未触发 -> 持币"
        reason = (reason or "池内头名变更(粘性带外)") + note_cool

        switched = (target != held)
        if switched:
            c = buy + sell                       # 每次换仓按双边计 (2026 版口径)
            nav *= (1.0 - c)
            trades.append(dict(date=str(dates[i].date()), frm=held, to=target,
                               frm_name=("空仓" if held == "CASH"
                                         else NAMES.get(held, held)),
                               to_name=("持币" if target == "CASH"
                                        else NAMES.get(target, target)),
                               state=state, reason=reason, tie=bool(tied),
                               lead=(None if sig["lead"] != sig["lead"]
                                     else round(sig["lead"], 2)),
                               m_head=round(m_head, 2),
                               dev=(None if dev_eff != dev_eff
                                    else round(dev_eff, 2)),
                               nav=round(nav, 6)))
            entry_px = (float(panel[target].iloc[i])
                        if (target in panel.columns
                            and not pd.isna(panel[target].iloc[i])) else None)
        ret = 0.0
        if target in panel.columns:
            p0, p1 = panel[target].iloc[i], panel[target].iloc[i + 1]
            if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                ret = p1 / p0 - 1.0
        # 仓位系数（可选）：pos_scale=None 时恒为 1.0，与旧版逐位一致。
        # 未投入的部分按现金处理（0 收益），用于「多头全仓 / 空头半仓」这类闸门。
        _w = 1.0 if pos_scale is None else float(pos_scale.iloc[i])
        if _w != _w:                              # NaN 兜底
            _w = 1.0
        # 豁免名单：持这些标的时不打折（用于「空头只减 A 股、放过黄金/纳指」这类变体）
        if pos_exempt and target in pos_exempt:
            _w = 1.0
        nav *= (1.0 + _w * ret)
        held = target
        prev_eff = eff_head
        avail_log.append((dates[i + 1], len(sig["rank"])))
        head_log.append((dates[i + 1], eff_head))
        held_log.append((dates[i + 1], target))
        navs.append((dates[i + 1], nav))

    nd = pd.Series({d.strftime("%Y-%m-%d"): v for d, v in navs})
    return dict(navs=nd, trades=trades, held_log=held_log,
                avail_log=avail_log, head_log=head_log,
                stick_hits=stick_hits, final=float(nd.iloc[-1]),
                cash_days=int(sum(1 for _d, t in held_log if t == "CASH")),
                cools=cools, cool_n=cool_days_n,
                roc_cools=roc_cools, roc_n=roc_days_n,
                gate_days=gate_days_n, gate_trig=gate_trig_n)


def bench_curve(panel: pd.DataFrame, dates: pd.DatetimeIndex, start_i: int,
                end_i: int, code: str) -> pd.Series:
    """买入持有基准。基点取窗口内第一个【有效】收盘价 (防 nan 传染)。"""
    s = panel[code]
    base_i = None
    for i in range(start_i, end_i + 1):
        v = s.iloc[i]
        if v == v and v != 0:
            base_i = i
            break
    if base_i is None:
        return pd.Series(dtype=float)
    base = s.iloc[base_i]
    out = {}
    for i in range(start_i, end_i + 1):
        v = s.iloc[i]
        # 数据缺失日 -> 沿用上一有效净值 (不产生假跳空), 并在报告中披露基准起始日
        out[dates[i].strftime("%Y-%m-%d")] = float(v / base) if v == v else None
    ser = pd.Series(out)
    return ser.ffill()


def stats(nav: pd.Series) -> dict:
    v = nav.to_numpy(dtype=float)
    ret = v[-1] / v[0] - 1.0
    n = len(v) - 1
    ann = (v[-1] / v[0]) ** (244.0 / n) - 1.0 if n > 0 else float("nan")
    peak = np.maximum.accumulate(v)
    mdd = float((v / peak - 1.0).min())
    daily = np.diff(v) / v[:-1]
    vol = float(np.std(daily, ddof=1) * np.sqrt(244)) if len(daily) > 1 else float("nan")
    neg = daily[daily < 0]
    dvol = float(np.std(neg, ddof=1) * np.sqrt(244)) if len(neg) > 1 else float("nan")
    sh = ann / vol if vol == vol and vol not in (0, None) else float("nan")
    so = ann / dvol if dvol == dvol and dvol not in (0, None) else float("nan")
    return dict(total=float(ret), ann=float(ann), mdd=mdd, vol=vol,
                sharpe=(float(sh) if sh == sh else None),
                sortino=(float(so) if so == so else None), days=n,
                calmar=(float(ann / abs(mdd)) if mdd < 0 and ann == ann else None))


def yearly(nav: pd.Series) -> List[dict]:
    out = []
    for yr, g in nav.groupby(nav.index.str[:4]):
        st = stats(g)
        out.append(dict(year=yr, start=float(g.iloc[0]), end=float(g.iloc[-1]),
                        ret=float(g.iloc[-1] / g.iloc[0] - 1.0), mdd=st["mdd"],
                        sharpe=st["sharpe"], days=st["days"]))
    return out


def held_summary(r: dict) -> List[dict]:
    c = Counter(t for _d, t in r["held_log"])
    tot = sum(c.values()) or 1
    return [dict(code=k, name=NAMES.get(k, k), days=n, pct=round(n / tot * 100, 2))
            for k, n in c.most_common()]


def defensive_days(r: dict, defensive: str = DEFENSIVE) -> dict:
    """防守期交易日数 + 每次防守段长度。防守期含「持防守位」与「持币」两种。"""
    runs, cur = [], 0
    cash_runs, ccur = [], 0
    for _d, t in r["held_log"]:
        if t == defensive or t == "CASH":
            cur += 1
            if t == "CASH":
                ccur += 1
            elif ccur:
                cash_runs.append(ccur)
                ccur = 0
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    if ccur:
        cash_runs.append(ccur)
    return dict(days=int(sum(runs)), episodes=len(runs),
                longest=int(max(runs)) if runs else 0,
                cash_days=int(sum(cash_runs)), cash_episodes=len(cash_runs),
                cash_longest=int(max(cash_runs)) if cash_runs else 0)


def availability(panel: pd.DataFrame, pool: List[str], dates: pd.DatetimeIndex,
                 start_i: int, end_i: int, W: int = 20) -> List[dict]:
    """逐年披露: 当年首个交易日可用的轮动标的数 / 年末可用数 / 年内新增上市标的。"""
    out = []
    win = dates[start_i:end_i + 1]
    yrs = sorted(set(d.strftime("%Y") for d in win))
    for y in yrs:
        idx = [i for i in range(start_i, end_i + 1) if dates[i].strftime("%Y") == y]
        if not idx:
            continue
        avail_first, avail_last, added = [], [], []
        for c in pool:
            s = panel[c]
            ok_first = (not pd.isna(s.iloc[idx[0]])) and \
                       (not pd.isna(s.iloc[max(idx[0] - W, 0)])) and idx[0] - W >= 0
            ok_last = (not pd.isna(s.iloc[idx[-1]])) and \
                      (not pd.isna(s.iloc[idx[-1] - W]))
            if ok_first:
                avail_first.append(c)
            if ok_last:
                avail_last.append(c)
            if ok_last and not ok_first:
                added.append(c)
        out.append(dict(year=y, n_first=len(avail_first), n_last=len(avail_last),
                        added=added,
                        names_last=[NAMES.get(c, c) for c in avail_last]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-12-31", help="基点收盘 (=1.000)")
    ap.add_argument("--end", default="2026-12-31", help="终点(超出数据则取最新)")
    ap.add_argument("--cost", type=float, default=0.002)
    ap.add_argument("--json", default=os.path.join(HERE, "out", "backtest_long.json"))
    ap.add_argument("--ve", type=int, default=1,
                    help="1=防守期叠加价值引擎MA200择时(不满则持币) 0=满仓持防守位")
    ap.add_argument("--def", dest="def_code", default=DEFENSIVE,
                    help=f"覆盖防守标的 (默认 {DEFENSIVE}; 传 512880 可复现旧基线)")
    a = ap.parse_args()
    use_ve = bool(a.ve)

    rot, deff, watch = read_pools()
    def_code = a.def_code
    panel = read_panel(ADJ_CSV)
    if def_code not in panel.columns:
        raise SystemExit(f"防守标的 {def_code} 不在 {os.path.basename(ADJ_CSV)} 中")
    watch_only = [c for c in watch if c not in rot and c not in deff and c != def_code]
    dates = panel.index
    start_i = int(dates.searchsorted(pd.Timestamp(a.start)))
    end_i = int(dates.searchsorted(pd.Timestamp(a.end)))
    end_i = min(end_i, len(dates) - 1)
    if dates[start_i] != pd.Timestamp(a.start):
        print(f"[warn] 起点 {a.start} 非交易日 -> 实际用 {dates[start_i].date()}")
    print(f"数据区间 : {dates[0].date()} ~ {dates[-1].date()}  共 {len(dates)} 根K线")
    print(f"回测窗口 : {dates[start_i].date()} = 1.000  ->  "
          f"{dates[end_i].date()}   收益日 {end_i - start_i} 个")
    print(f"主选池({len(rot)}) : {' '.join(rot)}")
    print(f"备选池({len(watch_only)}) : {' '.join(watch_only)}")

    buy = sell = a.cost / 2.0
    groups = OrderedDict()
    groups["main"] = ("策略B · 主选池(9只)", rot)
    groups["watch"] = (f"策略B · 备选池({len(watch_only)}只)", watch_only)
    groups["combo"] = (f"参照组 · 主+备({len(rot)+len(watch_only)}只)", rot + watch_only)

    results, curves, all_trades, all_held = {}, {}, {}, {}
    for key, (label, pool) in groups.items():
        r = backtest(panel, dates, start_i, end_i, pool, def_code,
                     sticky=1.0, buy=buy, sell=sell, value_engine=use_ve)
        st = stats(r["navs"])
        results[key] = dict(label=label, pool=pool, stat=st,
                            yearly=yearly(r["navs"]),
                            trades_n=len(r["trades"]), stick_hits=r["stick_hits"],
                            held=held_summary(r),
                            defense=defensive_days(r, def_code),
                            availability=availability(panel, pool, dates,
                                                      start_i, end_i),
                            last=dates[end_i].strftime("%Y-%m-%d"))
        curves[key] = r["navs"]
        all_trades[key] = r["trades"]
        all_held[key] = r["held_log"]
        print(f"\n[{label}]")
        print(f"  期末净值 {r['navs'].iloc[-1]:.4f}  区间收益 {st['total']*100:+.2f}%  "
              f"年化 {st['ann']*100:+.2f}%  最大回撤 {st['mdd']*100:.2f}%  "
              f"夏普 {st['sharpe']:.2f}  索提诺 {st['sortino']:.2f}")
        dfn = results[key]["defense"]
        print(f"  换仓 {len(r['trades'])} 笔  粘性带生效 {r['stick_hits']} 日  "
              f"防守期 {dfn['days']} 日 / {dfn['episodes']} 段 / 最长 {dfn['longest']} 日")
        print(f"  其中持币(价值引擎未触发) {dfn['cash_days']} 日 / "
              f"{dfn['cash_episodes']} 段 / 最长 {dfn['cash_longest']} 日  "
              f"占防守期 {dfn['cash_days']/dfn['days']*100:.1f}%" if dfn['days'] else "")

    for c in BENCH:
        curves["bench_" + c] = bench_curve(panel, dates, start_i, end_i, c)

    hdr = f"{'曲线':<26}{'期末净值':>10}{'区间收益':>11}{'年化':>10}{'最大回撤':>10}{'夏普':>7}"
    print("\n" + "=" * 78)
    print(hdr)
    print("-" * 78)
    for k, s in list(curves.items()):
        st = stats(s)
        nm = (results[k]["label"] if k in results
              else f"基准 {NAMES.get(k[6:], k[6:])}")
        sh = f"{st['sharpe']:.2f}" if st["sharpe"] is not None else "-"
        print(f"{nm:<26}{s.iloc[-1]:>10.4f}{st['total']*100:>10.2f}%"
              f"{st['ann']*100:>9.2f}%{st['mdd']*100:>9.2f}%{sh:>7}")
    print("=" * 78)

    # ---- 逐年绩效 (主选池 / 备选池 / 基准) ----
    print("\n【逐年收益】(年初->年末, 相对上一年末净值)")
    ykeys = [k for k in list(results) ] + ["bench_" + c for c in BENCH]
    ylabels = {**{k: results[k]["label"] for k in results},
               **{"bench_" + c: "基准 " + NAMES.get(c, c) for c in BENCH}}
    yrs = sorted({y["year"] for k in list(results) for y in results[k]["yearly"]})
    print(f"{'曲线':<26}" + "".join(f"{y:>10}" for y in yrs))
    for k in ykeys:
        ym = {y["year"]: y for y in (results[k]["yearly"] if k in results
                                     else yearly(curves[k]))}
        cells = []
        for y in yrs:
            cells.append(f"{ym[y]['ret']*100:>9.1f}%" if y in ym else f"{'-':>10}")
        print(f"{ylabels[k]:<26}" + "".join(cells))

    data_prov = {c: dict(name=NAMES.get(c, c),
                         first=(None if panel[c].first_valid_index() is None
                                else str(panel[c].first_valid_index().date())),
                         last=(None if panel[c].last_valid_index() is None
                               else str(panel[c].last_valid_index().date())),
                         n=int(panel[c].notna().sum()))
                 for c in panel.columns}

    out = dict(
        dates=[d.strftime("%Y-%m-%d") for d in dates[start_i:end_i + 1]],
        nav={k: [round(float(x), 6) for x in s.tolist()] for k, s in curves.items()},
        groups={k: dict(label=v["label"], pool=v["pool"], stat=v["stat"],
                        yearly=v["yearly"], trades_n=v["trades_n"],
                        stick_hits=v["stick_hits"], held=v["held"],
                        defense=v["defense"], availability=v["availability"],
                        last=v["last"])
                for k, v in results.items()},
        trades=all_trades,
        meta=dict(start=str(dates[start_i].date()), end=str(dates[end_i].date()),
                  data_start=str(dates[0].date()), data_end=str(dates[-1].date()),
                  cost=a.cost, cost_mode="每次换仓按双边计(买0.1%+卖0.1%)",
                  window_days=end_i - start_i + 1,
                  strategy=dict(W=20, MA=20, sticky=1.0, enter_dev=12.0,
                                enter_lead=15.0, exit_dev=9.0, exit_lead=10.0,
                                min_def_days=5, allow_cash=use_ve,
                                value_engine=use_ve,
                                ve_ma=VE_MA, ve_buy=VE_BUY, ve_sell=VE_SELL,
                                defensive=def_code),
                  defensive=def_code,
                  names={c: NAMES.get(c, c) for c in NAMES},
                  data_provenance=data_prov,
                  adj_csv=os.path.basename(ADJ_CSV)),
    )
    os.makedirs(os.path.dirname(a.json), exist_ok=True)
    with open(a.json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[json] {a.json}")


if __name__ == "__main__":
    main()
