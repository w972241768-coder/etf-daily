#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量抓取【前复权】日线长历史 (2018-12-01 ~ 今日) -> tdx_adj_long_2019_2026.csv
================================================================================
数据源: 腾讯财经 前复权日线  web.ifzq.gtimg.cn/appstock/app/fqkline/get
        param = <market><code>,day,<beg>,<end>,<count>,qfq
        实测单次上限约 730 根 -> 按 2 年一段分段拉取, 段间重叠 389 个交易日,
        重叠区前复权价格 **逐日完全一致 (diff=0)**, 可安全拼接。

为什么不用东方财富 push2his : 2026-09-24 实测连续 RemoteDisconnected(连接被对端关闭),
    不可用; 腾讯接口稳定且分段自洽。

为什么必须用前复权: 本地通达信 .day 为不复权。2020-2026 期间发生过多次份额折算/分红除息
    (如 588170 于 2026-07-06 1拆3、513660 于 2026-04-21 1拆2、510300 于 2026-01-19 分红),
    不复权下会算出 -33% / -53% 等假暴跌, 严重污染动横20 排名与净值曲线。

交叉校验: fetch 后由 verify_long_data.py 用 (a) 本地通达信 .day 不复权做除权日检测,
    (b) 随机抽 3 只 × 3 个日期与 TDX 接口前复权逐位比对。

长表列: code,date,open,high,low,close   (date=YYYYMMDD)
"""
import csv
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("ADJ_OUT") or os.path.join(HERE, "tdx_adj_long_2019_2026.csv")

# code -> (显示名, 腾讯市场前缀)
#   sh = 5xxxxx(沪ETF) / 000xxx(上证指数) ; sz = 1xxxxx(深ETF) / 399xxx
UNIVERSE = {
    # ---- 主选池轮动 9 只 ----
    "510300": ("沪深300ETF", "sh"),
    "159967": ("创成长ETF", "sz"),
    "563300": ("中证2000ETF", "sh"),
    "518880": ("黄金ETF", "sh"),
    "513100": ("纳指ETF", "sh"),
    "588170": ("科创半导体ETF华夏", "sh"),
    "516080": ("创新药ETF易方达", "sh"),
    "561700": ("电力ETF博时", "sh"),
    "513660": ("恒生ETF华夏", "sh"),
    # ---- 防守位 ----
    "512880": ("证券ETF国泰", "sh"),
    # ---- 备选观察池 (role=watch, 14 只) ----
    "159259": ("成长ETF易方达", "sz"),
    "512200": ("房地产ETF南方", "sh"),
    "159543": ("国证2000ETF工银", "sz"),
    "159505": ("国证2000ETF博时", "sz"),
    "588000": ("科创50ETF华夏", "sh"),
    "588220": ("科创100ETF鹏华", "sh"),
    "510210": ("上证指数ETF富国", "sh"),
    "510050": ("上证50ETF华夏", "sh"),
    "159906": ("深成长ETF大成", "sz"),
    "512070": ("证券保险ETF易方达", "sh"),
    "512890": ("红利低波ETF华泰柏瑞", "sh"),
    "159957": ("创业板ETF华夏", "sz"),
    "159915": ("创业板ETF易方达", "sz"),
    "159949": ("创业板50ETF华夏", "sz"),
    # ---- 基准指数 ----
    "000001": ("上证指数", "sh"),
    "000688": ("科创50", "sh"),
    "399006": ("创业板指", "sz"),
    # ---- 防守位 (2026-09-24 起: 512040 价值ETF富国, 别名"国信价值") ----
    "512040": ("价值ETF富国", "sh"),
    # ---- 空仓载体 511010 国债ETF（🔴 2026-11-27 补）----
    # 之前清单漏了它：照 online_data 提示「重跑 fetch」会把国债列从生产 CSV 抹掉，
    # backtest 的 cash_proxy 静默退回 0 收益 ⇒ 整条净值曲线被压低（实测 −5.8%）。
    # 注意：它 role=cash，不参与排名（read_pools 只读 role 为空/defensive/watch）。
    "511010": ("国债ETF国泰", "sh"),
}

# 🔴 2026-11-27：生产 CSV 必需的列。缺任何一列 ⇒ 拒绝覆盖，防止「某个接口失败
# 先把旧文件写坏」。固定包含 9 只主选 + 防守 + 空仓载体 + 基准。
REQUIRED_CODES = {
    "510300", "159967", "563300", "518880", "513100",
    "588170", "516080", "561700", "513660",  # 主选 9
    "512040",                                  # 防守
    "511010",                                  # 空仓载体
    "000001",                                  # 基准
}

URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
HEAD = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 🔴 2026-11-27：腾讯 qfq 对**现金分红**用「减法前复权」（P−ΣD），只有拆分才等比。
# 后果是除息日之前的价格路径被压低 ⇒ 早期到后期涨幅被放大，历史净值高估 11~15%。
# 落盘前必须把这三只还原成等比复权，否则重跑本脚本会把生产 CSV 改回减法口径。
# 分红表来自 R4 核对、R5 自检（不复权价 ÷ 指数逐年中位 0.001003~0.001016）。
CASH_DIV = {
    "510300": [("2014-01-21", 0.048), ("2015-01-20", 0.035), ("2016-01-20", 0.051),
               ("2017-01-23", 0.055), ("2018-01-23", 0.046), ("2019-01-16", 0.059),
               ("2019-12-11", 0.062), ("2021-01-18", 0.072), ("2022-01-19", 0.075),
               ("2023-01-16", 0.064), ("2024-01-18", 0.069), ("2025-06-18", 0.088),
               ("2026-01-19", 0.123)],
    "512040": [("2023-12-13", 0.104)],
    "511010": [("2025-09-23", 1.45), ("2025-12-26", 0.6542), ("2026-03-25", 0.5715),
               ("2026-06-25", 0.8144), ("2026-09-18", 0.6204)],
}


def to_ratio(rows, divs):
    """rows=[(code,date,o,h,l,c)]（减法前复权）→ 现金分红标的改等比，四价同乘。"""
    import pandas as pd
    df = pd.DataFrame(rows, columns=["code", "date", "o", "h", "l", "c"])
    df["_d"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.sort_values("_d").reset_index(drop=True)
    close = df["c"].astype(float)
    s = pd.Series(0.0, index=df.index)
    for d, D in divs:
        dt = pd.Timestamp(d)
        if df["_d"].iloc[0] < dt:
            s[df["_d"] < dt] += D
    raw = close + s
    fac = pd.Series(1.0, index=df.index)
    for d, D in divs:
        dt = pd.Timestamp(d)
        mask = df["_d"] == dt
        if mask.any():
            fac[df["_d"] < dt] *= 1.0 - D / float(raw[mask].iloc[0])
    mult = (raw * fac / close).clip(lower=1e-6)
    out = []
    for i, r in df.iterrows():
        m = float(mult.iloc[i])
        out.append((r["code"], r["date"], round(r["o"] * m, 6),
                    round(r["h"] * m, 6), round(r["l"] * m, 6), round(r["c"] * m, 6)))
    return out

# 分段: 每段 2 年, 段间重叠约 1 年 (腾讯单次上限 ~730 根)
# ★ 2026-10-26：起点改为可由环境变量 ADJ_BEG 覆盖（默认 2018-12-01，行为不变），
#   输出路径由 ADJ_OUT 覆盖。用于「延长回测到 2014 年」这类历史检验，
#   避免动到生产用的那份 CSV。
_ADJ_BEG = os.environ.get("ADJ_BEG", "2018-12-01")


def _mk_segments(beg: str, end_year: int = 2026):
    out, y0 = [], int(beg[:4])
    y = y0
    while y <= end_year - 2:
        b = beg if y == y0 else f"{y}-01-01"
        out.append((b, f"{y + 2}-12-31"))
        y += 2
    return out


SEGMENTS = _mk_segments(_ADJ_BEG)


def _seg_rows(mkt: str, code: str, beg: str, end: str, tries: int = 4):
    """拉一段前复权日线, 返回 {date: (open,close,high,low)}。"""
    last = None
    param = f"{mkt}{code},day,{beg},{end},760,qfq"
    for k in range(tries):
        try:
            r = requests.get(URL, params={"param": param}, headers=HEAD, timeout=25)
            j = r.json()
            node = j["data"][f"{mkt}{code}"]
            rows = node.get("qfqday") or node.get("day")
            if not rows:
                last = "empty klines"
                time.sleep(1.0 * (k + 1))
                continue
            out = {}
            for x in rows:
                # [date, open, close, high, low, volume]
                out[x[0]] = (float(x[1]), float(x[2]), float(x[3]), float(x[4]))
            return out
        except Exception as e:                       # noqa: BLE001
            last = repr(e)
            time.sleep(1.0 * (k + 1))
    return None if last != "empty klines" else {}


def fetch(code: str, mkt: str):
    """合并全部分段 -> [(code,date,open,high,low,close)]。"""
    merged = {}
    errs = []
    for beg, end in SEGMENTS:
        seg = _seg_rows(mkt, code, beg, end)
        if seg is None:
            errs.append(f"{beg}~{end}:取数失败")
            continue
        merged.update(seg)          # 重叠区取值必须一致(已实测 diff=0)
        time.sleep(0.25)
    if not merged:
        return None, errs
    rows = []
    for d in sorted(merged):
        o, c, h, l = merged[d]
        rows.append((code, d.replace("-", ""), o, h, l, c))
    return rows, errs


def main():
    allrows, report = [], []
    for code, (name, mkt) in UNIVERSE.items():
        rows, errs = fetch(code, mkt)
        if not rows:
            report.append((code, name, "FAIL", 0, "", "", str(errs)))
            print(f"[FAIL] {code} {name}: {errs}", flush=True)
            continue
        # 🔴 2026-11-27：现金分红标的落盘前从减法前复权改等比（与生产 CSV 口径一致）
        if code in CASH_DIV:
            rows = to_ratio(rows, CASH_DIV[code])
        allrows.extend(rows)
        report.append((code, name, "OK", len(rows), rows[0][1], rows[-1][1],
                       rows[-1][5]))
        print(f"[OK] {code} {name:22s} {len(rows):5d} bars  "
              f"{rows[0][1]}~{rows[-1][1]}  last={rows[-1][5]}", flush=True)

    allrows.sort(key=lambda x: (x[0], x[1]))

    # 🔴 2026-11-27：写前硬校验 + 原子替换。旧逻辑是「先覆盖生产 CSV，再判断失败」，
    # 任何一个 codes 取数失败都会把生产文件写坏，且 backtest 缺列时静默退 0 收益。
    got = set(r[0] for r in allrows)
    missing = sorted(REQUIRED_CODES - got)
    if missing:
        print(f"[ABORT] 必需列缺失，拒绝覆盖 {OUT}: {missing}", flush=True)
        print("FAILED CODES:", [r for r in report if r[2] != "OK"], flush=True)
        sys.exit(2)
    # 同原子写入：先写临时文件 + fsync，再 os.replace。中途异常不碰生产文件。
    tmp = OUT + ".tmp"
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["code", "date", "open", "high", "low", "close"])
            w.writerows(allrows)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, OUT)
    except Exception as e:                     # noqa: BLE001
        print(f"[ABORT] 写入失败，生产文件未改动: {e}", flush=True)
        try:
            os.remove(tmp)
        except OSError:
            pass
        sys.exit(3)
    print(f"\nWROTE {OUT}  rows={len(allrows)}  "
          f"codes={len(got)}  required=OK({len(REQUIRED_CODES)})")
    bad = [r for r in report if r[2] != "OK"]
    if bad:
        # 必需列都在，非必需列失败只告警（不再写坏文件）
        print("WARN 非必需 codes 取数失败:", bad, flush=True)


if __name__ == "__main__":
    main()
