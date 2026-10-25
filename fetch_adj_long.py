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
OUT = os.path.join(HERE, "tdx_adj_long_2019_2026.csv")

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
}

URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
HEAD = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 分段: 每段 2 年, 段间重叠约 1 年 (腾讯单次上限 ~730 根)
SEGMENTS = [
    ("2018-12-01", "2020-12-31"),
    ("2020-01-01", "2022-12-31"),
    ("2022-01-01", "2024-12-31"),
    ("2024-01-01", "2026-12-31"),
]


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
        allrows.extend(rows)
        report.append((code, name, "OK", len(rows), rows[0][1], rows[-1][1],
                       rows[-1][5]))
        print(f"[OK] {code} {name:22s} {len(rows):5d} bars  "
              f"{rows[0][1]}~{rows[-1][1]}  last={rows[-1][5]}", flush=True)

    allrows.sort(key=lambda x: (x[0], x[1]))
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "date", "open", "high", "low", "close"])
        w.writerows(allrows)
    print(f"\nWROTE {OUT}  rows={len(allrows)}  "
          f"codes={len(set(r[0] for r in allrows))}")
    bad = [r for r in report if r[2] != "OK"]
    if bad:
        print("FAILED CODES:", bad)
        sys.exit(1)


if __name__ == "__main__":
    main()
