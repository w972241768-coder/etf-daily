"""技术指标库：为主轮动池提供「可作为风控触发」的指标计算

设计原则（针对「减回撤」这一目标）：
  · 每个指标只输出「当日值」，由扫描器自行决定阈值与方向；
  · 全部指标只用**收盘价序列**（panel 里是前复权价），避免用到未来数据；
  · 慢指标（RSI/KDJ/MACD/BOLL）天然滞后，必须在报告里标注滞后天数。

⚠️ 数据限制：主轮动池的 panel 是**收盘价单列**（前复权），没有 high/low。
   ⇒ ATR / 真实波幅 / 影线类指标**无法计算**，本轮不纳入（除非改成用 |日涨跌| 近似）。
   近似替代：用滚动标准差（已实现波动率）代表波动类指标。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------- 趋势类 ----------------
def ma_dev(px: pd.Series, n: int = 20) -> pd.Series:
    """乖离率 (%) = P/MA(n) - 1"""
    ma = px.rolling(n).mean()
    return (px / ma - 1.0) * 100.0


def ma_slope(px: pd.Series, n: int = 20, k: int = 5) -> pd.Series:
    """MA(n) 的 k 日变化率 (%) —— 趋势加速度/衰减"""
    ma = px.rolling(n).mean()
    return (ma / ma.shift(k) - 1.0) * 100.0


def roc(px: pd.Series, n: int = 20) -> pd.Series:
    """n 日动量 (%)"""
    return (px / px.shift(n) - 1.0) * 100.0


def ma_cross(px: pd.Series, fast: int = 10, slow: int = 30) -> pd.Series:
    """快慢均线差 (%) —— 正=多头排列"""
    f = px.rolling(fast).mean()
    s = px.rolling(slow).mean()
    return (f / s - 1.0) * 100.0


# ---------------- 动量/超买超卖类 ----------------
def rsi(px: pd.Series, n: int = 14) -> pd.Series:
    """相对强弱 (0~100)"""
    d = px.diff()
    up = d.clip(lower=0)
    dn = (-d).clip(lower=0)
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50.0) if ad.isna().any() else out


def kdj_j(px: pd.Series, n: int = 9, m1: int = 3, m2: int = 3) -> pd.Series:
    """KDJ 的 J 值（用收盘价近似 RSV，缺 high/low 时会有偏差）"""
    lo = px.rolling(n).min()
    hi = px.rolling(n).max()
    rsv = (px - lo) / (hi - lo).replace(0, np.nan) * 100
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
    return 3 * k - 2 * d


def macd_hist(px: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9) -> pd.Series:
    """MACD 柱 (DIF - DEA)"""
    ef = px.ewm(span=fast, adjust=False).mean()
    es = px.ewm(span=slow, adjust=False).mean()
    dif = ef - es
    dea = dif.ewm(span=sig, adjust=False).mean()
    return (dif - dea) * 100.0 / px


def macd_hist_chg(px: pd.Series, k: int = 3) -> pd.Series:
    """MACD 柱的变化量（柱缩短=多头力量衰减）"""
    return macd_hist(px).diff(k)


# ---------------- 波动/通道类 ----------------
def boll_pos(px: pd.Series, n: int = 20, k: float = 2.0) -> pd.Series:
    """布林带位置 (%)：0=下轨, 50=中轨, 100=上轨"""
    ma = px.rolling(n).mean()
    sd = px.rolling(n).std()
    lo, up = ma - k * sd, ma + k * sd
    return ((px - lo) / (up - lo).replace(0, np.nan) * 100).fillna(50.0)


def zscore(px: pd.Series, n: int = 20) -> pd.Series:
    """价格 z 分数"""
    ma = px.rolling(n).mean()
    sd = px.rolling(n).std()
    return (px - ma) / sd.replace(0, np.nan)


def vol_ratio(px: pd.Series, n: int = 20) -> pd.Series:
    """已实现波动率比 (近 n 日 / 前 n 日) —— 放大即风险抬升"""
    r = px.pct_change()
    v_now = r.rolling(n).std()
    v_prev = r.shift(n).rolling(n).std()
    return v_now / v_prev.replace(0, np.nan)


def dd_from_high(px: pd.Series, n: int = 60) -> pd.Series:
    """距 n 日最高价的回撤 (%) —— 负值"""
    hi = px.rolling(n).max()
    return (px / hi - 1.0) * 100.0


# ---------------- 成交量类（需外部传入） ----------------
def vol_momentum(vol: pd.Series, n: int = 20) -> pd.Series:
    """成交量 n 日变化率 (%)"""
    return (vol / vol.shift(n) - 1.0) * 100.0


# ---------------- 指标注册表 ----------------
# name -> (函数, 参数字典列表, 方向语义, 滞后天数估计, 描述)
REGISTRY = {
    "乖离MA20":   (ma_dev,        [dict(n=10), dict(n=20), dict(n=60)],
                    "负=超跌(可能避险)", 0, "P/MA(n)-1"),
    "MA斜率":     (ma_slope,      [dict(n=20, k=3), dict(n=20, k=5), dict(n=60, k=10)],
                    "负=趋势转弱", 3, "MA(n) 的 k 日变化率"),
    "ROC":        (roc,           [dict(n=10), dict(n=20), dict(n=60)],
                    "负=动量转弱", 0, "n 日动量"),
    "均线差":     (ma_cross,      [dict(fast=5, slow=20), dict(fast=10, slow=30),
                                   dict(fast=20, slow=60)],
                    "负=空头排列", 0, "快慢均线差"),
    "RSI":        (rsi,           [dict(n=6), dict(n=14), dict(n=28)],
                    "高=超买(可能回调)", 1, "相对强弱"),
    "KDJ_J":      (kdj_j,         [dict(n=9), dict(n=14)],
                    "高=超买 / 低=超卖", 2, "KDJ 的 J 值(收盘近似)"),
    "MACD柱":     (macd_hist,     [dict(), dict(fast=6, slow=13)],
                    "负=空头", 3, "MACD 柱"),
    "MACD柱变化": (macd_hist_chg, [dict(k=1), dict(k=3), dict(k=5)],
                    "负=多头衰减", 3, "MACD 柱的 k 日变化"),
    "布林位置":   (boll_pos,      [dict(n=20, k=2.0), dict(n=20, k=1.5), dict(n=60, k=2.0)],
                    "低=贴近下轨", 0, "布林带位置 0~100"),
    "Z分数":      (zscore,        [dict(n=20), dict(n=60)],
                    "负=偏离下轨", 0, "价格 z 分数"),
    "波动放大":   (vol_ratio,     [dict(n=10), dict(n=20)],
                    "高=波动抬升", 1, "近 n 日波动 / 前 n 日波动"),
    "距高点回撤": (dd_from_high,  [dict(n=20), dict(n=60), dict(n=120)],
                    "负=处于回撤中", 0, "距 n 日最高价回撤"),
}


def compute_indicator(px: pd.Series, name: str, params: dict) -> pd.Series:
    fn = REGISTRY[name][0]
    return fn(px, **params)
