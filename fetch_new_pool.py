"""抓取 4 只新 ETF 的前复权长历史并合并成独立 CSV（2026-10-24）

新池子（用户指定）：515880 通信ETF国泰 / 159516 半导体设备ETF国泰 /
588200 科创芯片ETF嘉实 / 159530 机器人ETF易方达 + 513100 纳指ETF（已有）。
复用 fetch_adj_long.py 的腾讯 fqkline 抓取逻辑（项目已验证口径）。
输出: tdx_adj_new_2019_2026.csv（列与既有长历史一致: code,date,open,high,low,close）
"""
from __future__ import annotations

import csv
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fetch_adj_long as F      # noqa: E402

NEW = {
    "515880": ("通信ETF国泰", "sh"),
    "159516": ("半导体设备ETF国泰", "sz"),
    "588200": ("科创芯片ETF嘉实", "sh"),
    "159530": ("机器人ETF易方达", "sz"),
}
OUT = os.path.join(HERE, "tdx_adj_new_2019_2026.csv")


def main():
    rows_all = []
    for code, (name, mkt) in NEW.items():
        rows, errs = F.fetch(code, mkt)
        if not rows:
            print(f"[FAIL] {code} {name}: {errs}")
            continue
        rows_all.extend(rows)
        print(f"[OK] {code} {name:16s} {len(rows):5d} bars  "
              f"{rows[0][1]}~{rows[-1][1]}  last={rows[-1][5]}")
        time.sleep(0.3)

    # 追加 513100（纳指ETF）+ 512040（防守位）—— 从既有长历史取，保证同源同口径
    src = os.path.join(HERE, "tdx_adj_long_2019_2026.csv")
    want = {"513100", "512040"}
    if os.path.exists(src):
        with open(src, encoding="utf-8") as f:
            rd = csv.DictReader(f)
            for r in rd:
                if str(int(float(r["code"]))) in want:
                    rows_all.append((str(int(float(r["code"]))), r["date"],
                                     float(r["open"]), float(r["high"]),
                                     float(r["low"]), float(r["close"])))
        print(f"[OK] 513100 纳指ETF + 512040 防守位（取自既有长历史，保证同源）")

    if not rows_all:
        print("无任何数据，退出")
        sys.exit(1)

    rows_all.sort(key=lambda x: (x[0], x[1]))
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "date", "open", "high", "low", "close"])
        w.writerows(rows_all)
    codes = sorted(set(r[0] for r in rows_all))
    print(f"\nWROTE {OUT}  rows={len(rows_all)}  codes={len(codes)} -> {codes}")
    for c in codes:
        sub = [r for r in rows_all if r[0] == c]
        print(f"  {c}: {sub[0][1]} ~ {sub[-1][1]}  ({len(sub)} 根)")


if __name__ == "__main__":
    main()
