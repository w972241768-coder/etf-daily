# -*- coding: utf-8 -*-
"""2026-11-27：把生产 CSV 的现金分红从「减法前复权」改成「等比复权」，生成新长表。

背景（R4-F0，主控独立复核）：腾讯 qfq 对现金分红是减法（P−ΣD），只有拆分等比。
后果是除息**之前**的价格路径被压低 ⇒ 早期到后期的涨幅被放大，历史净值整体高估
11~15%。修法：还原不复权 Praw = Pa + Σ(其后分红)，再按 Π(1−D/P除息日收盘) 等比缩放。

⚠️ 本脚本**不覆盖**生产 CSV，默认写 `_ratio.csv`。确认数值后由人工替换。
分红表来自 out/review_dip5_1126/r5_lib.py::DIV（r4-data 核对，r5 自检通过）。

用法：
  python rebuild_ratio_csv.py                     # 写 tdx_adj_long_2019_2026_ratio.csv
  ADJ_BEG=2013-01-01 ADJ_OUT=... python ...       # 长历史文件同理（若分红表覆盖）
"""
import os
import sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.stdout.reconfigure(encoding="utf-8")

SRC = os.environ.get("ADJ_SRC") or os.path.join(HERE, "tdx_adj_long_2019_2026.csv")
OUT = os.environ.get("ADJ_OUT") or os.path.join(HERE, "tdx_adj_long_2019_2026_ratio.csv")

# 除息日 -> 每份现金分红（与 r5_lib.DIV 逐字一致）
DIV = {
    "510300": [("2014-01-21", 0.048), ("2015-01-20", 0.035), ("2016-01-20", 0.051),
               ("2017-01-23", 0.055), ("2018-01-23", 0.046), ("2019-01-16", 0.059),
               ("2019-12-11", 0.062), ("2021-01-18", 0.072), ("2022-01-19", 0.075),
               ("2023-01-16", 0.064), ("2024-01-18", 0.069), ("2025-06-18", 0.088),
               ("2026-01-19", 0.123)],
    "512040": [("2023-12-13", 0.104)],
    "511010": [("2025-09-23", 1.45), ("2025-12-26", 0.6542), ("2026-03-25", 0.5715),
               ("2026-06-25", 0.8144), ("2026-09-18", 0.6204)],
}


def to_ratio_mult(close: pd.Series, divs) -> pd.Series:
    """返回该股票每个日期的乘性因子（Pa -> 等比复权价）。 OHLC 同乘。"""
    s = pd.Series(0.0, index=close.index)
    for d, D in divs:
        if close.index[0] < pd.Timestamp(d):
            s[close.index < pd.Timestamp(d)] += D
    raw = close + s                       # 还原不复权
    fac = pd.Series(1.0, index=close.index)
    for d, D in divs:
        if pd.Timestamp(d) in close.index:
            fac[close.index < pd.Timestamp(d)] *= (1.0 - D / raw.loc[pd.Timestamp(d)])
    return raw * fac / close              # 乘性因子


def main():
    df = pd.read_csv(SRC, dtype={"code": str})
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    changed = []
    for code, divs in DIV.items():
        sub = df["code"] == code
        if not sub.any():
            continue
        idx = df.index[sub]
        g = df.loc[idx].sort_values("date").set_index("date")
        close = g["close"].astype(float)
        m = to_ratio_mult(close, divs)
        for col in ("open", "high", "low", "close"):
            df.loc[idx, col] = (g[col].astype(float) * m).reindex(
                df.loc[idx, "date"].values).values
        changed.append((code, len(idx), float(m.iloc[0]), float(m.iloc[-1])))
    df["date"] = df["date"].dt.strftime("%Y%m%d")
    tmp = OUT + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, OUT)
    print(f"WROTE {OUT} rows={len(df)} codes={df.code.nunique()}")
    for code, n, m0, m1 in changed:
        print(f"  {code}: {n} bars, 首日因子 {m0:.4f}, 末日因子 {m1:.4f}")


if __name__ == "__main__":
    main()
