#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长历史前复权数据交叉校验 (反造假三道关)
================================================================================
关卡 1 · 除权日检测: 用本地通达信 .day【不复权】与腾讯【前复权】逐日算日收益,
    找出 |差值| > 1.5% 的日期 = 疑似折算/分红除息日。逐一核对已知事件:
      588170 2026-07-06 1拆3 / 513660 2026-04-21 1拆2 / 510300 2026-01-19 分红 ...
    若某标的"不复权出现暴跌、前复权没有" -> 证明前复权已修正假跳空 (数据可信)。
    反之若前复权本身出现 >20% 单日跳空 -> 警报 (可能是真实暴跌或数据错误, 需人工确认)。

关卡 2 · 与 2026 短样本交叉: tdx_adj_long 的最近 250 根 vs 已验证的
    tdx_adj_raw.csv (TDX 接口 tqFlag=1 前复权) -> 逐日逐只比对, 允许 <=0.5% 相对误差。

关卡 3 · 端点抽查: 随机抽 3 只 × 3 个日期, 交由主 agent 用 TDX 接口
    (mcp__tdx-connector__tdx_kline, tqFlag=1) 独立复核。

用法: python verify_long_data.py
"""
import os
import struct
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
LONG = os.path.join(HERE, "tdx_adj_long_2019_2026.csv")
SHORT = os.path.join("C:/Users/王旭/.workbuddy/skills/etf-roc-ladder",
                     "tdx_adj_raw.csv")
ROOT = "E:/tdx/vipdoc"

# 已知折算/分红事件 (用于关卡1 的预期核对)
KNOWN = {
    "588170": [("2026-07-06", "1拆3")],
    "513660": [("2026-04-21", "1拆2")],
    "510300": [("2026-01-19", "分红")],
}


def read_long():
    df = pd.read_csv(LONG, dtype={"code": str})
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    return df


def read_local_unadj(code: str):
    mkt = "sh" if code[0] == "5" else "sz"
    p = f"{ROOT}/{mkt}/lday/{mkt}{code}.day"
    if not os.path.exists(p):
        return None
    raw = open(p, "rb").read()
    n = len(raw) // 32
    ds, cs = [], []
    for i in range(n):
        d, _o, _h, _l, c, _amt, _vol, _ = struct.unpack(
            "<5if2i", raw[i * 32:(i + 1) * 32])
        ds.append(pd.to_datetime(str(d), format="%Y%m%d"))
        cs.append(c / 1000.0)
    return pd.Series(cs, index=pd.DatetimeIndex(ds), name=code)


def gate1(df):
    print("=" * 78)
    print("关卡 1 · 除权日检测 (不复权 vs 前复权 日收益差异)")
    print("=" * 78)
    qfq = {c: g.sort_values("date").set_index("date")["close"]
           for c, g in df.groupby("code")}
    alerts = []
    for code, qs in qfq.items():
        us = read_local_unadj(code)
        if us is None:
            print(f"  {code}: 本地 .day 缺失, 跳过")
            continue
        j = pd.concat([qs.rename("qfq"), us.rename("unadj")], axis=1).dropna()
        j = j[j.index >= "2019-01-01"]
        if len(j) < 30:
            continue
        rq = j["qfq"].pct_change() * 100
        ru = j["unadj"].pct_change() * 100
        gap = (ru - rq).abs()
        big = gap[gap > 1.5].sort_values(ascending=False)
        known = KNOWN.get(code, [])
        # 前复权自身的极端跳空 (>20%) = 真实暴跌或数据异常, 需人工看
        qfq_jump = rq[(rq.abs() > 20)]
        print(f"\n  {code}: 除权/差异日 {len(big)} 个"
              + (f"  已知事件 {known}" if known else ""))
        for d, g in big.head(6).items():
            tag = ""
            for kd, kn in known:
                if abs((d - pd.Timestamp(kd)).days) <= 5:
                    tag = f"  <== 匹配已知事件 {kd} {kn}"
            print(f"    {d.date()}  前复权日收益 {rq[d]:+7.2f}%  "
                  f"不复权 {ru[d]:+7.2f}%  差 {g:5.2f}pp{tag}")
        for d in qfq_jump.index:
            alerts.append((code, str(d.date()), float(rq[d])))
    if alerts:
        print("\n  ⚠ 前复权序列中 |日涨跌| > 20% 的日期 (需人工确认为真实行情):")
        for c, d, r in alerts:
            print(f"    {c} {d} {r:+.2f}%")
    else:
        print("\n  ✅ 前复权序列无 |日涨跌|>20% 的异常跳空")
    return alerts


def gate2(df):
    print("\n" + "=" * 78)
    print("关卡 2 · 与已验证的 2026 短样本 (TDX 接口前复权) 交叉比对")
    print("=" * 78)
    if not os.path.exists(SHORT):
        print(f"  [SKIP] 未找到 {SHORT}")
        return
    sh = pd.read_csv(SHORT, dtype={"code": str})
    sh["date"] = pd.to_datetime(sh["date"].astype(str), format="%Y%m%d")
    lf = {c: g.sort_values("date").set_index("date")["close"]
          for c, g in df.groupby("code")}
    tot = ok = 0
    worst = []
    for code, g in sh.groupby("code"):
        if code not in lf:
            continue
        a = g.set_index("date")["close"].astype(float)
        b = lf[code]
        j = pd.concat([a.rename("tdx"), b.rename("qq")], axis=1).dropna()
        rel = ((j["tdx"] - j["qq"]).abs() / j["tdx"]).max()
        n = len(j)
        tot += n
        if rel <= 0.005:
            ok += n
        else:
            worst.append((code, n, float(rel)))
        print(f"  {code}: 重叠 {n:4d} 根  最大相对差 {rel*100:.4f}%  "
              f"{'✅' if rel <= 0.005 else '❌ 超 0.5%'}")
    print(f"\n  合计 {ok}/{tot} 根在 0.5% 容差内 ({ok/max(tot,1)*100:.2f}%)")
    if worst:
        print("  ⚠ 超差标的:", worst)


def main():
    if not os.path.exists(LONG) or os.path.getsize(LONG) < 100:
        print(f"[FATAL] {LONG} 不存在或为空")
        sys.exit(1)
    df = read_long()
    print(f"长表: {len(df)} 行 / {df.code.nunique()} 只  "
          f"{df.date.min().date()} ~ {df.date.max().date()}")
    gate1(df)
    gate2(df)


if __name__ == "__main__":
    main()
