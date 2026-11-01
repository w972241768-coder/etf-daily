#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 CSV 尾部补齐到最新交易日（curl + 腾讯前复权，按重叠日 ratio 缩放）。

⚠️ 本地研究脚本，不入库。

背景：online_data.fetch_online 在本机 Python 侧被拦（requests 拿不到数据），
      但 curl 可以。所以用 subprocess 调 curl 取数，再按重叠日比值缩放对齐。
"""
import io
import json
import os
import subprocess
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def qfq_day(code: str, n: int = 60) -> pd.DataFrame:
    """腾讯前复权日线。返回 DataFrame[date, open, close, high, low, vol]。

    ⚠️ 000001 是**上证指数**，不是深市平安银行 —— 必须走 sh000001 且**不复权**
    （指数无复权概念，CSV 里存的是真实点位 3600~3700 一带）。
    2026-11-01 修复：原实现按「5 开头=sh、其余=sz」判断，会把 000001 打到
    sz000001（平安银行 ~11 元），量级差 300 倍，补数会污染基准列。
    """
    is_index = (code == "000001")
    mkt = "sh" if (is_index or code[0] == "5") else "sz"
    suffix = "" if is_index else "qfq"
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={mkt}{code},day,,,{n},{suffix}")
    out = subprocess.run(
        ["curl", "-s", "-m", "20", url, "-H", "Referer: https://gu.qq.com"],
        capture_output=True)
    try:
        j = json.loads(out.stdout.decode("utf-8", "ignore"))
    except Exception:                                  # noqa: BLE001
        return pd.DataFrame()
    node = (j.get("data") or {}).get(f"{mkt}{code}") or {}
    rows = node.get("qfqday") or node.get("day") or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([r[:6] for r in rows],
                      columns=["date", "open", "close", "high", "low", "vol"])
    for c in ("open", "close", "high", "low", "vol"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    return df


def check_gap(csv_path: str) -> None:
    """断层哨兵（2026-11-01）：000001 落后于整体末日 ⇒ ② 节上证/超额会出现「-」「—」。

    放在 patch() 开头跑，这样即使本次「无需补数」或取数失败，也能发现历史遗留断层。
    """
    d = pd.read_csv(csv_path, dtype={"code": str})
    d["date"] = pd.to_datetime(d["date"].astype(str), format="%Y%m%d")
    _b = d[d["code"] == "000001"]["date"]
    if _b.empty:
        print("  ⚠️ 警告：CSV 里没有 000001（上证指数），② 节上证/超额会全空")
        return
    _bmax, _amax = _b.max(), d["date"].max()
    if _bmax != _amax:
        print(f"  ⚠️ 断层：000001 末日 {_bmax.date()} 落后于整体末日 {_amax.date()}"
              f" —— ② 节的「同期上证 / 超额」会在缺口日显示「-」「—」")


def patch(csv_path: str, codes) -> None:
    check_gap(csv_path)
    old = pd.read_csv(csv_path, dtype={"code": str})
    old["date"] = pd.to_datetime(old["date"].astype(str), format="%Y%m%d")
    last = old["date"].max()
    print(f"{os.path.basename(csv_path)}: 末行 {last.date()}")
    add = []
    for code in codes:
        d = qfq_day(code)
        if d.empty:
            print(f"  {code}: 取数失败")
            continue
        have = old[old["code"] == code].sort_values("date")
        if have.empty:
            continue
        # 用重叠的最后一个交易日求缩放比（CSV 复权基准 vs 腾讯前复权基准）
        base_row = have.iloc[-1]
        match = d[d["date"] == base_row["date"]]
        if match.empty:
            print(f"  {code}: 无重叠日，跳过")
            continue
        ratio = float(base_row["close"]) / float(match.iloc[0]["close"])
        # ⚠️ 必须按**该标的自己的末行**往后补，不能用全局 last ——
        #   否则某个标的单独落后（如 000001 停在 10-28、ETF 已到 10-30）时会被判「无需补数」。
        new = d[d["date"] > base_row["date"]].copy()
        if new.empty:
            continue
        for c in ("open", "close", "high", "low"):
            new[c] = (new[c] * ratio).round(4)
        new["code"] = code
        add.append(new[["code", "date", "open", "high", "low", "close"]])
        print(f"  {code}: 补 {len(new)} 根  ratio={ratio:.6f}  "
              f"{new['date'].min().date()}~{new['date'].max().date()}")
    if not add:
        print("  无需补数")
        return
    tail = pd.concat(add, ignore_index=True)
    out = pd.concat([old, tail], ignore_index=True).sort_values(["date", "code"])
    out["date"] = out["date"].dt.strftime("%Y%m%d")
    out = out[["code", "date", "open", "high", "low", "close"]]
    with io.open(csv_path, "w", encoding="utf-8", newline="\n") as f:
        out.to_csv(f, index=False)
    print(f"  写回 {len(out)} 行，末日 {out['date'].max()}")
    check_gap(csv_path)


def main():
    import etf_backtest_long as bl
    rot, deff, watch = bl.read_pools()
    # ⚠️ 必须带 000001（上证指数）：② 节「同期上证 / 超额」要用它。
    #    2026-11-01 用户指出：之前只补 pool 里的 ETF，导致 000001 停在 10-28，
    #    10-29 / 10-30 两行的上证与超额变成「-」「—」。
    codes = list(dict.fromkeys(list(rot) + list(deff) + list(watch) + ["000001"]))
    for name in ("tdx_adj_long_2014_2026.csv", "tdx_adj_long_2019_2026.csv"):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            patch(p, codes)
            print()


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main()
