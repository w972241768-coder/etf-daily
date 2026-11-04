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
    "511010": "国债ETF", "511880": "银华日利",
}
DEFENSIVE = "512040"
BENCH = ["000001", "000688", "399006", "513100"]
VE_MA = 200
VE_BUY = 0.97
VE_SELL = 1.06

# ============================================================================
# ★ 生产默认执行口径（2026-10-25 用户拍板升级为正式默认）
#   现用策略 + D3 单笔止损 + P3 追高止盈 的叠加组合。
#   backtest() 的默认参数**故意保持全关**：它代表「未叠加的基础策略」，
#   是参照基准；生产日报由调用层显式传入 LIVE_COMPOSITE，两者不可混。
#   唯一事实源在此 —— 改参数只改这里，禁止在调用处硬编码数字。
# ============================================================================
LIVE_COMPOSITE = dict(
    loss_cool=0.03,          # D3：换出时该笔亏 ≥3% ⇒
    loss_days=5,             #      空仓 5 个交易日
    roc_q=0.95,              # P3：头名 ROC60 进自身滚动 95 分位 ⇒
    roc_days=20,             #      空仓 20 个交易日
    roc_n=60,
    roc_min_periods=250,
    # ---- 抄底模块（2026-10-26 用户拍板接入）----
    # 触发：主选池**全部**动横20 为负（零参数，不用选阈值）
    # 动作：当日换持**排名最后一名**（不是倒数第二 —— 实测买倒数第二为负）
    # 池子：可用标的 < weak_min_pool 时不做（小池子「最弱」恒为同一只，会连买暴跌品）
    # 冷却：weak_cd = 0 **不启用**。2026-10-26 实测「同标的 20 日不重复」在各窗口都是负向
    #   （2019-12 起 +2.21→+0.81、2018-12 起 +2.88→+0.91），因为连买常常正是好机会
    #   （2022-09 连买 516080 四次，每次之后 20 日约 +11%）。用户当日晚拍板删除该条件。
    weak_mode="allneg",
    weak_rank=1,
    weak_delay=0,
    weak_cd=0,
    weak_min_pool=5,
    # ---- 抄底离场规则（2026-10-28 用户拍板接入 E3）----
    # 抄底买入后进入「锁定期」：期内 target 锁定该标的、**D3/P3 都不介入**，
    # 直到下列任一条件成立才换回当日头名：
    #   ① 收盘回到自身 MA20 上方（weak_exit_ma20）
    #   ② 持满 10 个交易日（weak_exit_days）
    # 实测（2016-10-26~2026-10-28，主选池 9 只，单边万一）：
    #   无锁定（旧口径）18.872 / −23.37% / Calmar 1.47 / 抄底 52 次
    #   E3（本口径）    20.897 / −23.36% / Calmar 1.53 / 抄底 17 次  → +2.025
    # 离场分布：13 次回 MA20、3 次满 10 日；持有中位 6 日、单笔中位 +2.52%、胜率 75%
    # ⚠️ 已知取舍（用户 2026-10-28 确认接受）：
    #   · 抄底那笔亏到 −3% 也不止损了（17 笔里 5 笔曾破 −3%，扛完后平均 −1.5%）；
    #   · E3 **没有「转正」出口** ⇒ 池内已有标的转正时仍继续持有（17 笔里 11 笔遇到）；
    #   · 回撤一点没改善（−23.36% vs −23.37%），那个 −23% 是 2026-10 单笔造成，与卖法无关；
    #   · 最大单笔从 −6.62% 改善到 −3.83%（不再割在跳空最低点）。
    # 改回旧口径：删掉下面两行即可（weak_exit_days / weak_exit_ma20）。
    weak_exit_ma20=True,
    weak_exit_days=10,
    # ---- 抄底「波动率闸门」（2026-10-31 用户拍板接入）----
    # 触发前多问一句：末位标的的 60 日年化波动率是否 <= weak_max_vol(%)？超了就不抄底。
    # 动机：抄底买「跌最狠的那只」，往往就是「波动最大的那只」——
    #   17 笔抄底里 588170（2026-10-26）60 日波动率 58.5%，是唯一 >50% 的一笔，
    #   也是唯一造成两位数亏损的一笔（10-30 已 -15.93%）。
    # 实测（单边万一，四窗口 Calmar）：13.3年 关抄底1.56/现行0.92/<=40%1.63/<=45%1.69；
    #   12.8年 1.61/0.95/1.69/1.75；8年 2.29/1.36/2.43/2.54；10年 1.81/1.07/1.92/2.00。
    #   MDD：关抄底/<=40%/<=45% 全部 -17.92%；现行 -31.01%。
    # ★ 43%~58% 是同一片高原（15pp 宽）—— 只有 588170(58.5%) 超过 50%，
    #   次高是 2021-03-29 的 45.0%，所以 45 不是拟合出来的尖点。
    # ★ IS 段（2016-10~2021-10）与不设闸门**逐位相同**（Calmar 1.58=1.58，抄底 10 次=10 次），
    #   只作用于 OOS 那一笔（OOS 2021-11~2026-10：1.32 -> 2.58）。
    #   ⚠️「IS 零影响」是**从未被检验**，不是通过检验。
    # ★ 交叉验证：改用相对口径「标的波动率/当日池内中位数」，588170 是 2.77 倍
    #   （17 笔唯一 >2.0，次高 1.95）⇒ 绝对 45% 与相对 2.0 倍给出同一取舍。
    # ★★ 2026-11-04 由 45.0 上调到 50.0（用户拍板）：
    #   触发原因：11-04 创成长 159967 成为抄底末位，12:00 盘中 vol60=44.1% 放行买入，
    #     15:35 收盘 vol60 升到 **48.7%** 反被 45% 拦下 ⇒ 同一天两个版本给出相反建议，
    #     而推高波动率的正是下午那根大阳线 —— 「大涨」被当成「危险」误杀。
    #   为什么上调无代价：45/50/55 三档回测**逐位相同**（全窗 38.2355 / MDD −18.60% / C1.77、
    #     抄底 16 次 / 拦截 3 次），因为 12.8 年被拦的只有 588170 那 3 天（58.3~59.1%）。
    #     ⇒ 45~55 是等价区间，取 50 是为了给 45~50% 的标的留缓冲（距失效边界 58.3% 还有 8.3pp）。
    #   ⚠️ 断崖在 58.3%：阈值一旦 ≥59.1% 就放行 588170，MDD 立刻从 −18.60% 崩回 −31.07%。
    #   ⚠️ 不要改成「相对自身中位倍数」：588170 中位 42.6%，58.8% 时倍数仅 1.38，
    #     相对口径会把它放进来（而它正是 −15.93% 那笔）；创成长 47.5% 却是 1.58 倍会被拦。
    # 关掉：删掉本行即可（默认 0 = 关闭，逐位等价已验证 maxdiff 0.0）。
    # ★★ 2026-11-04 再改：总波动 50% → **下行波动 45%**（用户拍板，40→45 为容错）。
    #   动机：11-04 创成长 159967 因下午大阳线把总波动从 44.1% 推到 48.7% 被误拦，
    #     「大涨」被当成「危险」。下行口径（半方差，只统计负收益日）把上涨波动剔除：
    #     588170 那 3 天 下行 46.2/48.1/46.1%（自身 90 分位）、159967 当天 下行 38.4%。
    #   ✅ 历史零变化：下行 35/40/45 与总波动 50 在四窗口、IS/OOS、拦截明细上**逐位相同**
    #     （全窗 38.2355 / MDD −18.60% / C1.77 / 抄底 16 / 拦 588170 那 3 天）。
    #     ⇒ 这不是「改善」，是「修正度量」：价值在余量与「不受大涨污染」。
    # ★ 40→45 的取舍（用户 11-04 拍板）：两档历史等价，但
    #   · **放行侧余量** 1.6pp → **6.6pp**（159967 今天 38.4%），大涨推高波动时更不容易误拦；
    #   · **拦截侧余量** 6.1pp → 1.1pp（断崖 46.1% = 588170 那三天的最低下行波动）。
    #   ⇒ 45 是「宁可多放、不可误杀」的选择；⚠️ 若未来出现下行 45~46% 的抄底标的会被放行，
    #     而历史上没有这个区间的样本（所以回测看不到差异）。**上限绝不能到 48.1%**。
    #   ⚠️ 半方差必须用 clip(upper=0)：where(r<0, 0.0) 会把 NaN 变成 0，中位数会被拉成 0.0%。
    weak_max_vol=45.0,
    weak_vol_mode="downside",
    # ---- 空仓期持国债（2026-11-01 用户拍板接入）----
    # 动机：全样本 545 日（17.5%）空仓，现金零收益是纯浪费。
    # 实测（单边万1，生产 CSV 2018-12 起）：全窗 18.5947→19.7361（+6.14%）C2.52→2.57；
    #   近7年 13.6256→14.4772（+6.25%）C2.63→2.69；主窗 13.8259→14.6901（+6.25%）C2.64→2.70。
    #   2014 CSV 四窗口 +6.20%~+8.24%；IS/OOS 双正（1.60→1.67 / 2.62→2.69）；分年 9 正 3 负。
    # ★ 零参数：只是一个标的代码，没有任何阈值 ⇒ 无过拟合空间。
    # ★ 空仓 57 段最短 2 日 > 成本临界 2.0 日，无碎段磨损；最差段 −1.04%、胜率 70.2%。
    # ⚠️ 必须保证 511010 在长历史 CSV 里（etf_pool.csv role=cash 只用于在线取数，
    #    不参与排名）；缺列时 backtest 会静默退回 0 收益现金。
    cash_proxy="511010",
)


def read_pools() -> tuple:
    """从 etf_pool.csv 读取 (主选池轮动, 防守, 备选观察池) —— 唯一配置入口。"""
    df = pd.read_csv(POOL_CSV, dtype={"code": str}, encoding="utf-8-sig")
    df["role"] = df["role"].fillna("").astype(str).str.strip()
    df["code"] = df["code"].str.strip()
    rot = df.loc[df["role"] == "", "code"].tolist()
    deff = df.loc[df["role"] == "defensive", "code"].tolist()
    watch = df.loc[df["role"] == "watch", "code"].tolist()
    return rot, deff, watch


def read_panel(path: str, field: str = "close") -> pd.DataFrame:
    """读长表 CSV → 宽表面板。field 可选 close / open（2026-11-01 加 open，供成交时点审计）。"""
    df = pd.read_csv(path, dtype={"code": str})
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    if field not in df.columns:
        raise KeyError(f"CSV 无 {field} 列（现有列：{list(df.columns)}）")
    frames = OrderedDict()
    for code, g in df.groupby("code"):
        g = g.sort_values("date").drop_duplicates("date", keep="last")
        frames[code] = g.set_index("date")[field].astype(float)
    panel = pd.DataFrame(frames).sort_index()
    return panel


def compute_signal(panel: pd.DataFrame, pool: List[str], i: int,
                   W: int = 20, MA: int = 20) -> Optional[dict]:
    """第 i 日收盘信号。上市不足 / 数据缺失的标的自动被跳过 (动态池)。

    ⛔ 2026-11-01 已证伪并删除的变体：排名改用「20日对数价格斜率×R²」或
    「动横×胜率」。实测四窗口终值 −55%~−70%、Calmar 腰斩、大肉少 5~6 笔
    （44→39）—— R² 惩罚的正是脉冲式暴涨，而那是大肉的唯一来源。勿再实现。
    """
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
             weak_mode: Optional[str] = None, weak_rank: int = 1,
             weak_delay: int = 0, weak_cd: int = 0, weak_min_pool: int = 0,
             weak_first_d3: bool = False,
             weak_ma20_entry: bool = False,
             weak_max_vol: float = 0.0,
             # ---- 动量买入的绝对波动率闸门（2026-11-03 研究，默认 0 = 完全关闭）----
             # weak_max_vol 只管「抄底买入」；动量排名买入此前**没有任何波动率约束**。
             # 2026-08-17 以 vol60=90.9% 买入 588170、08-31 又以 90.0% 买回，两次都踩在
             # 科创半导体暴跌起点 ⇒ mom_max_vol 给「换入新头名」补上同一道闸。
             mom_max_vol: float = 0.0,
             mom_fallback: str = "cash",   # "cash"=转入空仓 | "hold"=保持原仓不动
             # ---- 目标波动率配权（2026-11-04 研究，默认 0 = 完全关闭）----
             # 机构 CTA 的 Inverse-Vol Sizing：w = min(100%, σ_target / σ60)，
             # 余下 1−w 持 cash_proxy（国债）。与 0/1 硬闸门相比没有「84% 满仓 / 86% 空仓」
             # 的阈值悬崖 —— 波动越高仓位越低，是连续的。
             # ⚠️ 与 pos_frac（半仓补仓）互斥，不要同时开。
             vol_target: float = 0.0,
             # 配权重算频率（2026-11-04 对照实验）：
             #   "lock"  = 建仓日按当日 σ60 定 w，持有期内锁定（零调仓成本，默认）
             #   "daily" = 每个交易日按当日 σ60 重算 w，按 |Δw|×(buy+sell) 扣调仓摩擦
             vol_rebal: str = "lock",
             # daily 模式下的「调仓死区」：|Δw| 小于该值就不动（默认 0 = 每次变都调）。
             # 目的是滤掉低波动期的无效微调 —— 波动率每天只变千分之几，
             # 为 0.3% 的仓位变化来回买卖纯属磨损。
             vol_rebal_min: float = 0.0,
             # ---- D3 回场门（2026-11-04 研究，默认关）----
             # 背景：2026-08-31 那次，588170 刚在暴跌中被 D3 换出，5 天后冷却期满，
             #   因 ROC20 仍含 15~20 天前的暴涨阳线而排第 1，系统买回正在下跌的它 ⇒ −6.55%。
             # 思路：回场当日除「ROC20 第一」外，还要求头名站上自身 MA20（或 ROC5>0），
             #   否则视为「前浪余威」，不顺延买它。
             # ⚠️ 样本极小（18 次 D3 回场里满足条件的只有个位数），过拟合风险高，
             #    必须先过全样本 + IS/OOS 再谈接入。
             d3_gate: str = "",          # ""=关 | "ma20"=要求 P>MA20 | "roc5"=要求 ROC5>0
             d3_gate_fb: str = "next",   # "next"=顺延买次名 | "cash"=继续持债
             # 抄底闸门用哪种波动率（2026-11-04 研究）：
             #   "total"     = 总波动率（现行口径，std）
             #   "downside"  = 下行波动率（半方差，只统计负收益日）
             # 动机：11-04 创成长因下午大涨把总波动率从 44.1% 推到 48.7% 而被误拦 ——
             #   绝对总波动率把「上涨波动」也当风险。下行口径能把两者分开：
             #   588170 那 3 天 下行 46~48%（自身 90 分位）、159967 今天 下行仅 37%。
             weak_vol_mode: str = "total",

             weak_exit_ma20: bool = False, weak_exit_days: int = 0,
             weak_exit_pos: bool = False,
             pos_frac: Optional[float] = None,
             add_drop: float = 0.01,
             # 成交时点审计（2026-11-01 专家第四轮）：
             #   None/False = 现状「T 日收盘出信号、T 日收盘成交」（close→close）
             #   传 open 面板 = 「T 日收盘出信号、T+1 开盘成交」（open[i+1]→close[i+1]）
             panel_open: Optional[pd.DataFrame] = None,
             # 空仓期替代载体（2026-11-01 研究用）：None = 现金零收益（现状）。
             # 传标的代码（如 "511010" 国债 / "511880" 货基）⇒ target=="CASH" 时
             # 按该标的日收益计息。默认 None ⇒ 逐位等价。
             cash_proxy: Optional[str] = None) -> dict:
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
    pos_w = 1.0                          # 当前实际投入比例（仅 pos_frac 开启时可能 <1）
    w_prev: Optional[float] = None        # 上一日的目标仓位（vol_rebal="daily" 用）
    vol_rebal_n = 0                       # 每日重算模式下实际发生的调仓次数
    vol_rebal_cost = 0.0                  # 这些调仓累计扣掉的摩擦成本（小数）
    pending_weak: Optional[str] = None    # 抄底模型的「次日买入」待执行标的
    weak_events: List[dict] = []          # 抄底触发明细（研究用）
    weak_skips: List[dict] = []           # 被波动率闸门拦下的抄底（日报展示用，2026-10-31）
    # 波动率闸门（2026-10-31）：仅在开启时预计算 60 日年化波动率矩阵，默认零开销。
    # 2026-11-03：mom_max_vol（动量买入闸门）也需要同一张矩阵 ⇒ 一并触发预计算。
    mom_skips: List[dict] = []            # 被动量波动率闸门拦下的换入（研究用）
    # ★ 2026-11-04 修（专家审查指出）：原来只算一份 _vol60 供四处共用，
    #   LIVE_COMPOSITE 设 weak_vol_mode="downside" 后，mom_max_vol / vol_target /
    #   vol_rebal 三个实验参数会**静默改用下行波动率** ⇒ 回测失真。
    #   现在拆成两份：抄底闸门用 _vol60_weak（跟随 weak_vol_mode），其余恒用 _vol60_total。
    _vol60_total = None                   # 总波动率（std）
    _vol60_weak = None                    # 抄底专用（total / downside）
    if (weak_max_vol and weak_max_vol > 0) or (mom_max_vol and mom_max_vol > 0) \
            or (vol_target and vol_target > 0):
        try:
            _ret60 = panel[pool].pct_change(fill_method=None)
            _vol60_total = _ret60.rolling(60).std() * (252 ** 0.5) * 100
            if weak_vol_mode == "downside":
                # 下行波动率 = 半方差开方，只统计负收益日；正收益压成 0。
                # ⚠️ 必须用 clip(upper=0) 而不是 where(r<0, 0.0)：后者会把 NaN 也变成 0，
                #    早期数据不足时半方差恒为 0、中位数被拉成 0.0%（2026-11-04 踩过）。
                _dn = _ret60.clip(upper=0.0)
                _vol60_weak = np.sqrt((_dn ** 2).rolling(60).mean()) * (252 ** 0.5) * 100
            else:
                _vol60_weak = _vol60_total
        except Exception:                      # noqa: BLE001
            _vol60_total = _vol60_weak = None
    weak_cd_map: dict = {}                # 标的 -> 上次抄底日索引（冷却用）
    # 抄底「持仓锁定」状态（2026-10-28 研究）：建仓后锁定该标的，
    # 直到 weak_exit_ma20 / weak_exit_days / weak_exit_pos 任一成立才释放。
    weak_hold: Optional[str] = None
    weak_hold_i: int = -1
    weak_entry_px: Optional[float] = None
    weak_exit_log: List[dict] = []        # 抄底退出事件明细（研究用）
    cool_until = -1                      # 冷却到期日（含），仅 loss_cool 开启时用
    cools: List[dict] = []               # 冷却事件明细
    cool_days_n = 0
    d3_gate_hits = 0                     # D3 回场门拦下的次数（研究用）
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
            # ★ 2026-10-28 抄底「持仓锁定」模式（研究）：
            #   进场可加「末位须在 MA20 下方」；离场改为【回MA20上方 / 满N日 / 轮动转正】任一。
            #   一旦抄底买入即进入锁定期，期内 target 锁定该标的、**D3/P3 不介入**
            #   —— 否则 D3 的 3% 会在第 2 天就把它清掉，永远走不到 MA20 / 满N日 那两条出口。
            _wlock = bool(weak_mode is not None
                          and (weak_exit_ma20 or (weak_exit_days or 0) > 0
                               or weak_exit_pos))
            _wt = None
            if _wlock and weak_hold is not None:
                _ex = None
                _c = weak_hold
                if _c not in sig["m"]:                 # 已不可交易 -> 强制退出
                    _ex = "标的不再可交易"
                elif weak_exit_ma20:
                    _dv = sig["dev"].get(_c)
                    if _dv is not None and _dv >= 0:
                        _ex = "回MA20上方"
                if _ex is None and weak_exit_days and (i - weak_hold_i) >= weak_exit_days:
                    _ex = f"满{weak_exit_days}个交易日"
                if _ex is None and weak_exit_pos:
                    if weak_mode == "allneg":
                        _pos_ok = not all(v < 0 for v in sig["m"].values())
                    else:
                        try:
                            _pos_ok = min(sig["m"].values()) > float(weak_mode)
                        except (TypeError, ValueError):
                            _pos_ok = False
                    if _pos_ok:
                        _ex = "轮动转正"
                if _ex:
                    _pnl = None
                    if (weak_entry_px and _c in panel.columns
                            and not pd.isna(panel[_c].iloc[i])):
                        _pnl = round((float(panel[_c].iloc[i])
                                      / float(weak_entry_px) - 1.0) * 100, 2)
                    weak_exit_log.append(dict(
                        date=str(dates[i].date()), code=_c,
                        name=NAMES.get(_c, _c), reason=_ex,
                        days=int(i - weak_hold_i), ret=_pnl))
                    weak_hold = None
                    weak_entry_px = None
                else:
                    _wt = _c                          # 继续锁定持有
            # ★ weak_first_d3：先算「抄底今天会买什么」，再判 D3 的「是否真的换出」。
            #   否则 D3 会按「头名接管」的假设判定换出，而抄底本来会让它继续持有
            #   （2026-10-27：全池仍负、末位还是 588170，D3 却按头名 513100 触发了止损）。
            if _wt is None and weak_first_d3 and weak_mode is not None and sig["rank"]:
                _wrk = sig["rank"]
                _ww = _wrk[-weak_rank] if len(_wrk) >= weak_rank else None
                _wp_ok = len(_wrk) >= max(1, weak_min_pool)
                _wcd_ok = (_ww is not None
                           and weak_cd_map.get(_ww, -10 ** 6) + weak_cd <= i)
                _wtrig = False
                if weak_mode == "allneg":
                    _wtrig = all(v < 0 for v in sig["m"].values())
                else:
                    try:
                        _wtrig = min(sig["m"].values()) <= float(weak_mode)
                    except (TypeError, ValueError):
                        _wtrig = False
                if _wtrig and _ww and _wp_ok and _wcd_ok:
                    _wt = _ww
            if loss_cool is not None:
                # ★★ 已知优先级约定（2026-10-31 用户指出后确认为「有意为之」，不是 bug）
                #   判据 `_chk != held` 用的是**有效头名**（已过粘性带）。于是存在这条路径：
                #     有效头名 == 当前持仓（如 10-26：561700 只领先 0.18pp < 粘性带 1.0pp
                #     ⇒ 判平手、保留前日链式 513100）⇒ D3 认为「没换出」而跳过；
                #     紧接着抄底把 target 改成末位 588170 ⇒ **实际换出了，但 D3 已经判完**。
                #   ⇒ 结论：**「抄底造成的换出」不经过 D3 检查**（抄底优先于 D3 的换出判定）。
                #   为什么保留这个行为（实测，单边万1，近10年）：
                #     · 现状（抄底优先）       21.9836 / −17.92% / Calmar 2.03
                #     · 改成 D3 优先（weak_first_d3=True + D3 置空仓后不被抄底覆盖）
                #                              19.1506 / −17.94% / Calmar 1.92  ⇒ −13%
                #   机制：「全池动横20 全负」时原持仓大概率也在亏 ⇒ D3 优先会让抄底
                #     16 次全部被拦（实测触发数 16 → 0），等于废掉抄底模块。
                #   ⚠️ 这是「靠规则缝隙赚钱」：抄底那 15 次换出的原持仓亏损不受 D3 约束，
                #     恰好保住了抄底的收益；而最坏的一次（588170，−15.93%）已由
                #     `weak_max_vol=45` 波动率闸门挡在门外 ⇒ 风险端有兜底、收益端保留。
                #   ⚠️ 边界：若抄底标的波动率 <45% 被放行，仍会出现「换出绕过 D3」。
                #   🔧 若要改成 D3 优先：`weak_first_d3=True` + 在下方 `if _wt is not None:`
                #     分支加 `if target != "CASH":` 保护（两处都要改，只改一处会被覆盖）。
                _chk = _wt if _wt else target
                if i < cool_until:                       # 冷却中 -> 继续空仓
                    target = "CASH"
                    note_cool = f" | 冷却中(至 {dates[cool_until].date()})"
                elif (_chk != held and held != "CASH" and entry_px
                      and held in panel.columns):
                    # ⚠️ 2026-10-27 已实测并删除「抄底持仓豁免/放宽 D3」：
                    #   豁免 D3（一直持有到最后一名换人）长窗 15.2553（-1.84）、
                    #   主窗 11.5336（-1.39）、近5年 5.3954（-0.65），全面变差；
                    #   放宽到 5%/8%/12% 三者结果**完全相同**（都是 15.2553）——
                    #   说明抄底标的亏起来是连续跳空，5~12% 之间没有任何缓冲带，
                    #   「再等等」不存在任何阈值上的救赎。⇒ D3 的 3% 必须照管抄底持仓。
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
            # ---- D3 回场门（2026-11-04 研究）：冷却期满当日，新头名若「仍在 MA20 下方」
            #      或「近 5 日仍下跌」，视为前浪余威未消，不顺延买它。
            #      ⚠️ 只在 i == cool_until 这一天生效；换仓日已由上面的 D3 触发分支处理。
            if (d3_gate and i == cool_until and target != "CASH"
                    and target in panel.columns):
                _gp = panel[target].iloc[i]
                _gok = True
                try:
                    if d3_gate == "ma20":
                        _gma = panel[target].rolling(MA).mean().iloc[i]
                        _gok = (not pd.isna(_gp)) and (not pd.isna(_gma)) \
                            and float(_gp) > float(_gma)
                    elif d3_gate == "roc5":
                        if i >= 5:
                            _g5 = panel[target].iloc[i - 5]
                            _gok = (not pd.isna(_gp)) and (not pd.isna(_g5)) \
                                and float(_gp) > float(_g5)
                        else:
                            _gok = True
                except Exception:                  # noqa: BLE001
                    _gok = True
                if not _gok:
                    _g_rk = sig["rank"]
                    if d3_gate_fb == "cash" or len(_g_rk) < 2:
                        target = "CASH"
                        note_cool = " | 回场门:头名未站上均线 → 继续持债"
                    else:
                        target = _g_rk[1]
                        note_cool = (f" | 回场门:头名 {_g_rk[0]} 未站上均线"
                                     f" → 顺延 {_g_rk[1]}")
                    d3_gate_hits += 1
            # ---- P3 追高止盈：头名 ROC(n) 进自身滚动 q 分位 -> 空仓 roc_days 日 ----
            # 与 D3 独立判定、独立计时；目标持仓优先级 D3 > P3（D3 已置 CASH 时不再覆盖）
            if roc_q is not None and _wt is None and held != "CASH" \
                    and i >= roc_until and bool(roc_hold.iloc[i]):
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
            if roc_q is not None and _wt is None and i < roc_until \
                    and target != "CASH":
                target = "CASH"
                if not note_cool:
                    note_cool = f" | P3 空仓中(至 {dates[roc_until].date()})"
            # ---- 抄底模型（可选·研究用）：池内转弱 → 换持最弱 / 次日买入 ----
            # weak_mode = "allneg"（全池动横20 均为负）或 数值字符串（最弱者 ≤ 该阈值，如 "-30"）
            # weak_rank = 1 买最后一名 / 2 买倒数第二名（模拟博主选股）
            # weak_delay = 0 当日换持 / 1 当日清仓、次日再买（博主原话「今天清仓、明天抄底」）
            if _wt is not None:
                # 抄底持仓锁定中（2026-10-28）：继续持有，不再重新选标的
                target = _wt
                note_cool = ((note_cool + f" | 抄底持有{_wt}") if note_cool
                             else f" | 抄底持有{_wt}")
            elif weak_mode is not None:
                _rk = sig["rank"]
                _weak = _rk[-weak_rank] if len(_rk) >= weak_rank else None
                # 保险①：池内可用标的太少时不做抄底（小池子最弱永远是同一只，会连买暴跌品）
                # 保险②：同一标的抄底后 weak_cd 个交易日内不再抄它
                _cd_ok = (_weak is not None
                          and weak_cd_map.get(_weak, -10 ** 6) + weak_cd <= i)
                _pool_ok = len(_rk) >= max(1, weak_min_pool)
                # 保险③（2026-10-28 新增）：末位须在**自身 MA20 下方**才抄底
                #   —— 「跌得深」不等于「超跌」，站上 MA20 的最弱往往只是横盘偏弱。
                _ma_ok = True
                if weak_ma20_entry and _weak is not None:
                    _dve = sig["dev"].get(_weak)
                    _ma_ok = (_dve is not None and _dve < 0)
                # ⚠️ 2026-10-28 已实测并删除「末位动横20 ≤ 阈值」这一前提（用户提议 -15%）：
                #   近10年 20.8975 → 19.6209（−1.28），5/5 窗口全负、三种成本全负；
                #   抄底次数 17 → 4（样本不足）；被滤掉的 13 笔合计 +22.15%、胜率 69%，
                #   不是烂机会而是「小赚但值得做」的机会。阈值邻域还是锯齿
                #   （−12 给 +1.69、−10 给 −0.64、−15 给 −1.28），没有稳定高原。
                #   ⇒ 「跌得深才抄」在 9 只池子上不成立，抄底的价值在「次数 × 小胜率」。
                # ⚠️ 2026-10-29 已实测并删除「估值百分位闸门」（weak_gate / weak_px_pct）：
                #   用户提议「估值百分位 <40% 才抄底」。两个口径都测了，都是负向：
                #   · 沪深300 加权 PE 5年百分位 <40%：20.8975 → 18.2394（−2.66），5 窗口 4 负 1 正
                #   · 末位自身价格 5年分位 <40%：19.9780（−0.92），5 窗口 3 负 2 正
                #   反直觉的机制：PE 百分位 <40% 的 7 笔抄底胜率仅 57%、合计 +16.48%；
                #   ≥40% 的 9 笔胜率 89%、合计 +33.10% —— **估值低的抄底反而更差**。
                #   原因：沪深300 估值是大盘蓝筹口径，抄底买的是高波动成长股，两者不同步；
                #   且「全池动横20 全负」本身已隐含市场低迷，估值条件重复且滞后（月度 vs 日频）。
                #   注：所有闸门都把 MDD 修到 −17.92%，但那是排除了 2026-10-26 单笔（PE 76.7%），
                #   样本 1 次，属运气而非规律。
                _val_ok = True
                _roc_ok = True
                # 波动率闸门：末位 60 日年化波动率须 <= weak_max_vol
                _vol_ok = True
                if _vol60_weak is not None and _weak is not None:
                    try:
                        _vv = _vol60_weak.at[dates[i], _weak]
                        _vol_ok = bool(pd.notna(_vv) and float(_vv) <= weak_max_vol)
                    except Exception:              # noqa: BLE001
                        _vol_ok = False
                if pending_weak is not None:
                    if target != "CASH":
                        target = pending_weak
                        note_cool = ((note_cool + " | 抄底次日买入") if note_cool
                                     else " | 抄底次日买入")
                    pending_weak = None
                else:
                    _trig = False
                    if weak_mode == "allneg":
                        _trig = all(v < 0 for v in sig["m"].values())
                    else:
                        try:
                            _trig = min(sig["m"].values()) <= float(weak_mode)
                        except (TypeError, ValueError):
                            _trig = False
                    if (_trig and _weak and target != "CASH" and _cd_ok
                            and _pool_ok and _ma_ok and _roc_ok and _val_ok
                            and _vol_ok):
                        _vev = None
                        if _vol60_weak is not None:
                            try:
                                _vev = float(_vol60_weak.at[dates[i], _weak])
                            except Exception:        # noqa: BLE001
                                _vev = None
                        weak_events.append(dict(
                            date=str(dates[i].date()), code=_weak,
                            name=NAMES.get(_weak, _weak),
                            m=round(float(sig["m"][_weak]), 2),
                            vol=(round(_vev, 1)
                                 if (_vev is not None and _vev == _vev) else None)))
                        weak_cd_map[_weak] = i
                        if weak_delay == 0:
                            target = _weak
                            if _wlock:                 # 进入锁定期（2026-10-28）
                                weak_hold = _weak
                                weak_hold_i = i
                                weak_entry_px = (
                                    float(panel[_weak].iloc[i])
                                    if (_weak in panel.columns
                                        and not pd.isna(panel[_weak].iloc[i]))
                                    else None)
                            note_cool = ((note_cool + f" | 抄底{_weak}") if note_cool
                                         else f" | 抄底{_weak}")
                        else:
                            target = "CASH"
                            pending_weak = _weak
                            note_cool = ((note_cool + f" | 池内转弱→空仓,次日买{_weak}")
                                         if note_cool else
                                         f" | 池内转弱→空仓,次日买{_weak}")
                    elif (_trig and _weak and weak_max_vol and weak_max_vol > 0
                          and not _vol_ok and _cd_ok and _pool_ok and _ma_ok
                          and _roc_ok and _val_ok):
                        # 波动率闸门拦下（2026-10-31）：记录以便日报说明「今天为什么没抄」
                        _vskip = None
                        if _vol60_weak is not None:
                            try:
                                _vskip = float(_vol60_weak.at[dates[i], _weak])
                            except Exception:        # noqa: BLE001
                                _vskip = None
                        weak_skips.append(dict(
                            date=str(dates[i].date()), code=_weak,
                            name=NAMES.get(_weak, _weak),
                            vol=(round(_vskip, 1)
                                 if (_vskip is not None and _vskip == _vskip)
                                 else None),
                            limit=float(weak_max_vol)))
            # ⚠️ 2026-10-27 已实测并删除「抄底持仓的独立止损/止盈」：
            #   · 独立止损 8%/10%/12%/15% ⇒ 净值与基线**完全一致**（换仓笔数也一样），
            #     因为 D3 的 3% 换出检查已经先一步拦下，更宽的止损永远轮不到触发；
            #   · 独立止盈 10%/15%/20%/30% ⇒ 同样完全一致，抄底持仓持有期太短，从未涨到；
            #   · 只有 5% 止损微正（+0.17），属噪声量级。
            #   ⇒ 抄底不需要自己的止盈止损，退出交给 D3 即可（见下方 weak_first_d3 注释）。
            # ---- 广度闸门（可选）：池内动横20 全为负 -> 强制空仓，恢复才回场 ----
            if gate_mask is not None and bool(gate_mask.iloc[i]):
                gate_days_n += 1
                if target != "CASH":
                    gate_trig_n += 1
                    note_cool = ((note_cool + " | 全池动横20为负→空仓")
                                 if note_cool else " | 全池动横20为负→空仓")
                target = "CASH"
        else:
            # 切防守期：抄底锁定状态失效（防守期不做抄底）
            weak_hold = None
            weak_entry_px = None
            ve_ok = (not value_engine) or bool(ve_hold.loc[dates[i]])
            target = defensive if ve_ok else "CASH"
            if not ve_ok:
                reason = reason + " | 价值引擎未触发 -> 持币"
        # ---- 动量买入的绝对波动率闸门（2026-11-03 研究，默认关闭 ⇒ 逐位等价）----
        # 只在「ROT 状态下要换入一只新的非防守标的」时生效；继续持有/抄底/防守均不干预。
        if (mom_max_vol and mom_max_vol > 0 and state == "ROT"
                and target != "CASH" and target != held
                and target != weak_hold and target != defensive):
            _mv = None
            if _vol60_total is not None:
                try:
                    _mv = float(_vol60_total.at[dates[i], target])
                except Exception:                      # noqa: BLE001
                    _mv = None
            if _mv is not None and _mv == _mv and _mv > mom_max_vol:
                mom_skips.append(dict(
                    date=str(dates[i].date()), code=target,
                    name=NAMES.get(target, target),
                    vol=round(_mv, 1), limit=float(mom_max_vol)))
                if mom_fallback == "hold" and held != "CASH":
                    target = held
                else:
                    target = "CASH"
                note_cool = ((note_cool + f" | 头名波动{_mv:.0f}%>{mom_max_vol:.0f}%不追")
                             if note_cool else
                             f" | 头名波动{_mv:.0f}%>{mom_max_vol:.0f}%不追")
        reason = (reason or "池内头名变更(粘性带外)") + note_cool

        switched = (target != held)
        if switched:
            c = buy + sell                       # 每次换仓按双边计 (2026 版口径)
            # ⓘ 关于 cash_proxy 的成本口径（2026-11-01 核实，勿再改）：
            #   引擎对「换入/换出 CASH」都扣 buy+sell。
            #   · 国债口径：卖股票(sell)+买国债(buy) = 准确；
            #   · 现金口径：实际只需 sell，被高估了一次 buy ⇒ 基线偏保守。
            #   两者用同一假设 ⇒ 对比公平，且国债结果含真实成本，不需要额外补扣。
            nav *= (1.0 - c)
            # 2026-11-03：CASH 段实际持 cash_proxy（国债），名称如实写出来，
            #   否则第 ② 节会出现「买入：持币」却带着 -0.18% 收益的矛盾。
            _cash_nm = (f"{cash_proxy} 国债(空仓)" if cash_proxy else "持币")
            trades.append(dict(date=str(dates[i].date()), frm=held, to=target,
                               frm_name=(_cash_nm if held == "CASH"
                                         else NAMES.get(held, held)),
                               to_name=(_cash_nm if target == "CASH"
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
            # 新建仓时设定投入比例：pos_frac=None ⇒ 满仓（与旧版逐位一致）
            pos_w = (pos_frac if (pos_frac is not None and target != "CASH")
                     else 1.0)
            # ★ 目标波动率配权（2026-11-04）：w = min(1, σ_target / σ60)，建仓日锁定。
            #   只在换仓那一刻定 w、持有期内不变 ⇒ 零额外调仓成本（与实盘一致：
            #   不会为了把 w 从 0.62 调到 0.64 而每天买卖）。
            if (vol_target and vol_target > 0 and target != "CASH"
                    and _vol60_total is not None):
                try:
                    _vt = float(_vol60_total.at[dates[i], target])
                except Exception:                  # noqa: BLE001
                    _vt = None
                if _vt is not None and _vt == _vt and _vt > 0:
                    pos_w = min(1.0, float(vol_target) / _vt)
        ret = 0.0
        # ★★ 成交时点（2026-11-01 二次修正）：买卖必须同一时刻。
        # 旧实现只算新仓 open_T1→close_T1，漏了旧仓 close_T→open_T1 的隔夜跳空
        # ⇒ 等于「用昨日收盘卖旧、今日开盘买新」，时间穿越，虚增收益（曾误报 +12%）。
        # 正确：旧仓先持到 open_T1（吃隔夜跳空），再换新仓；换到 CASH 时只剩隔夜段。
        if panel_open is not None and switched:
            _rs = 0.0
            if held != "CASH" and held in panel.columns \
                    and held in panel_open.columns:
                _po = panel_open[held].iloc[i + 1]
                _pc = panel[held].iloc[i]
                if not pd.isna(_po) and not pd.isna(_pc) and _pc != 0:
                    _rs = _po / _pc - 1.0
            ret = _rs
            if target != "CASH" and target in panel.columns \
                    and target in panel_open.columns:
                p0 = panel_open[target].iloc[i + 1]
                p1 = panel[target].iloc[i + 1]
                if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                    ret = (1.0 + _rs) * (p1 / p0) - 1.0
        elif target in panel.columns:
            p0 = panel[target].iloc[i]
            p1 = panel[target].iloc[i + 1]
            if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                ret = p1 / p0 - 1.0
        elif cash_proxy and cash_proxy in panel.columns:
            # ★ 空仓期改持替代载体（2026-11-01）：CASH 段按该标的日收益计息。
            #   默认 cash_proxy=None ⇒ 不进此分支，ret 保持 0（与旧版逐位一致）。
            # ⛔ 2026-11-02 已证伪并删除：「J(T) 超阈值 ⇒ 当日不持国债」闸门。
            #   实测（主窗，单边万1）：J<=70/80/85/90/95 全部负（−0.76%~−2.80%）；
            #   只有 J<=100/105 微正（+0.21%/+0.47%），但 **IS 正、OOS 负**（近3年 −0.33%），
            #   且 7 年只拦下 25~54 天，扣掉换手成本后归零。勿再实现。
            #   机制：国债大跌日的 J 中位只有 21~31（J>80 占 0%），**闸门根本挡不住大跌**；
            #   全历史 J>100 的 191 天日均 +0.0644%/胜率 83.8%，J 越高反而越好（趋势性）。
            p0 = panel[cash_proxy].iloc[i]
            p1 = panel[cash_proxy].iloc[i + 1]
            if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                ret = p1 / p0 - 1.0
        # 半仓补仓（可选·研究用）：持仓期收盘跌破建仓价 add_drop ⇒ 次日补满
        # 与博主的「再低于首次买入价 1% 以上，次日午盘再买半仓」同构。
        if (pos_frac is not None and 0.0 < pos_w < 1.0
                and held != "CASH" and held in panel.columns and entry_px):
            _apx = panel[held].iloc[i]
            if not pd.isna(_apx) and _apx / entry_px - 1.0 <= -add_drop:
                pos_w = 1.0
        # ★ 每日重算模式（2026-11-04 对照）：每个交易日按**当日** σ60 重算 w，
        #   并按 |Δw|×(buy+sell) 扣调仓摩擦（真实调仓要买卖两边，按双边计）。
        #   换仓日已由上面的 switched 分支定好 pos_w 且已扣 buy+sell，这里不重复扣。
        if (vol_rebal == "daily" and vol_target and vol_target > 0
                and target != "CASH" and _vol60_total is not None):
            try:
                _vd = float(_vol60_total.at[dates[i], target])
            except Exception:                      # noqa: BLE001
                _vd = None
            if _vd is not None and _vd == _vd and _vd > 0:
                _w_new = min(1.0, float(vol_target) / _vd)
                _band = max(1e-6, float(vol_rebal_min or 0.0))
                if (not switched) and w_prev is not None:
                    _dw = abs(_w_new - w_prev)
                    if _dw > _band:
                        nav *= (1.0 - _dw * (buy + sell))
                        vol_rebal_n += 1
                        vol_rebal_cost += _dw * (buy + sell)
                        pos_w = _w_new
                    else:
                        pos_w = w_prev        # 落在死区内 ⇒ 维持原仓位，不产生交易
                else:
                    pos_w = _w_new
            w_prev = pos_w
        # ★ 部分仓位收益合成（2026-11-04）：w 投 ETF、(1−w) 持国债。
        #   ⚠️ 必须用临时变量 _w_eff，**不能改 pos_w 本身** —— pos_w 是「当前仓位状态」，
        #      持有期内要一直保留（lock 模式锁定、daily 模式作下一日的 w_prev）。
        #      第一版写成 `pos_w = 1.0` 合成完就清零，导致 lock 实际变成
        #      「只在建仓当天缩仓、次日就恢复满仓」，与设计完全不符（2026-11-04 自查发现）。
        _w_eff = pos_w
        if (pos_w < 1.0 and target != "CASH"
                and cash_proxy and cash_proxy in panel.columns):
            _q0 = panel[cash_proxy].iloc[i]
            _q1 = panel[cash_proxy].iloc[i + 1]
            if not pd.isna(_q0) and not pd.isna(_q1) and _q0 != 0:
                ret = pos_w * ret + (1.0 - pos_w) * (float(_q1) / float(_q0) - 1.0)
                _w_eff = 1.0                   # 已合成完毕，下面不再乘
        nav *= (1.0 + _w_eff * ret)
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
                weak_events=weak_events, weak_exit_log=weak_exit_log,
                # 2026-10-28：暴露「当前是否处于抄底锁定期」——日报要像 D3 一样
                # 显示抄底现在处在什么状态（第几天 / 距离场还差什么）。
                weak_hold=weak_hold, weak_hold_i=int(weak_hold_i),
                weak_entry_px=weak_entry_px,
                gate_days=gate_days_n, gate_trig=gate_trig_n,
                weak_skips=weak_skips,
                mom_skips=mom_skips,
                vol_rebal_n=int(vol_rebal_n),
                vol_rebal_cost=float(vol_rebal_cost),
                d3_gate_hits=int(d3_gate_hits))


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
