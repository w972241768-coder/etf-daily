#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量抓取【前复权】日线长历史 (2015-01-01 ~ 2026-09-24) -> tdx_adj_long_2015_2026.csv
================================================================================
与 fetch_adj_long.py 同源同逻辑 (腾讯 fqkline qfq), 仅把起点从 2018-12 提前到 2015-01。

★ 为什么终点停在 2026-09-24 而不是今天:
  2026-09-26 实测腾讯 fqkline **近期数据有错** (510300 09-16 给 4.485, 三源一致 4.55),
  day/qfqday 两路同错。故历史段交给腾讯, 09-28 之后的交易日改用新浪在线补齐
  (见 roc_param_study.build_panel 的 ratio 校验拼接)。

★ 前复权基准点随"最新交易日"漂移: 腾讯 qfq 以数据最新日为基准, 本脚本请求的最后一段
  止于 2026-09-24, 与既有 tdx_adj_long_2019_2026.csv (T0=2026-09-24) 基准一致;
  即便整体差一个常数倍也不影响回测——动横 P_t/P_{t-n}-1 与乖离 P/MA20 对整体缩放免疫。
  校验口径: 每个标的在重叠区的 ratio 序列必须 **标准差 ≈ 0** (内部自洽)。
"""
import csv
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "tdx_adj_long_2015_2026.csv")

# 只抓本次研究需要的标的: 主选池 9 只 + 防守位 + 上证指数
UNIVERSE = {
    "510300": ("沪深300ETF", "sh"),
    "159967": ("创成长ETF", "sz"),
    "563300": ("中证2000ETF", "sh"),
    "518880": ("黄金ETF", "sh"),
    "513100": ("纳指ETF", "sh"),
    "588170": ("科创半导体ETF华夏", "sh"),
    "516080": ("创新药ETF易方达", "sh"),
    "561700": ("电力ETF博时", "sh"),
    "513660": ("恒生ETF华夏", "sh"),
    "512040": ("价值ETF富国", "sh"),
    "000001": ("上证指数", "sh"),
}

URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
HEAD = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 每段 2 年(约 488 根, 远低于腾讯单次 ~730 根上限), 段间重叠 1 年
SEGMENTS = [
    ("2015-01-01", "2016-12-31"),
    ("2016-01-01", "2018-12-31"),
    ("2018-01-01", "2020-12-31"),
    ("2020-01-01", "2022-12-31"),
    ("2022-01-01", "2024-12-31"),
    ("2024-01-01", "2026-09-24"),
]


def _seg_rows(mkt, code, beg, end, tries=4):
    param = f"{mkt}{code},day,{beg},{end},760,qfq"
    last = None
    for k in range(tries):
        try:
            r = requests.get(URL, params={"param": param}, headers=HEAD, timeout=25)
            j = r.json()
            node = j["data"][f"{mkt}{code}"]
            rows = node.get("qfqday") or node.get("day")
            if not rows:
                last = "empty"
                time.sleep(1.0 * (k + 1))
                continue
            return {x[0]: (float(x[1]), float(x[2]), float(x[3]), float(x[4]))
                    for x in rows}
        except Exception as e:                       # noqa: BLE001
            last = repr(e)
            time.sleep(1.0 * (k + 1))
    return None if last != "empty" else {}


def fetch(code, mkt):
    merged, errs, overlaps = {}, [], []
    prev = None
    for beg, end in SEGMENTS:
        seg = _seg_rows(mkt, code, beg, end)
        if seg is None:
            errs.append(f"{beg}~{end} 失败")
            prev = None
            continue
        if prev:
            common = sorted(set(prev) & set(seg))
            if common:
                diffs = [abs(prev[d][1] - seg[d][1]) for d in common]
                overlaps.append((beg, len(common), max(diffs)))
        merged.update(seg)
        prev = seg
        time.sleep(0.25)
    if not merged:
        return None, errs, overlaps
    rows = [(code, d.replace("-", ""), merged[d][0], merged[d][2], merged[d][3],
             merged[d][1]) for d in sorted(merged)]
    return rows, errs, overlaps


def main():
    allrows, report = [], []
    for code, (name, mkt) in UNIVERSE.items():
        rows, errs, ov = fetch(code, mkt)
        if not rows:
            print(f"[FAIL] {code} {name}: {errs}", flush=True)
            report.append((code, name, "FAIL", 0, "", "", ""))
            continue
        mx = max((o[2] for o in ov), default=0.0)
        allrows.extend(rows)
        report.append((code, name, "OK", len(rows), rows[0][1], rows[-1][1],
                       rows[-1][5]))
        print(f"[OK] {code} {name:22s} {len(rows):5d} bars  "
              f"{rows[0][1]}~{rows[-1][1]}  last={rows[-1][5]:.4f}  "
              f"段间重叠最大差={mx:.6f}", flush=True)

    allrows.sort(key=lambda x: (x[0], x[1]))
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "date", "open", "high", "low", "close"])
        w.writerows(allrows)
    print(f"\nWROTE {OUT}  rows={len(allrows)}  codes={len(set(r[0] for r in allrows))}")
    if any(r[2] != "OK" for r in report):
        print("FAILED:", [r[0] for r in report if r[2] != "OK"])
        sys.exit(1)


if __name__ == "__main__":
    main()
