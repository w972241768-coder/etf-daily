#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
孤立缺失 K 线修补 (反造假的最后一道防线) —— 幂等版
================================================================================
腾讯前复权源存在极少数【孤立空洞】(单个交易日在有前有后的序列中缺失)。
典型: 513100 @2022-01-13、159915 @2021-02-08、510210 @2021-01-22、
      512070 @2021-09-10、512890 @2021-10-22、159505 @2024-12-31/2025-10-16、
      513660 @2019-05-31/06-26/07-25。
这些不是停牌 (同日其它 ETF 均有数据), 属数据源缺行。

修补策略 (按优先级, 全部留痕到 out/patched_points.csv):
  1) 向腾讯源【换窗口单独重抓该标的】, 命中则补真值 (method=REFETCH);
  2) 仍缺 -> 相邻两根 K 线做【对数线性插值】(几何插值, 与复权序列性质一致),
     method=INTERP(p0->p1);
  3) 上市初期的前置缺口 (NaN 出现在首个有效日之前) 一律不补 —— 那是"尚未上市",
     回测动态池会自动跳过, 补了反而是造假。

★ 本脚本【幂等】: 修补值以 (code, date) 为键 upsert, 可反复运行不会重复追加行,
  也不会把已有行的 date 写空。
"""
import csv
import os

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
LONG = os.path.join(HERE, "tdx_adj_long_2019_2026.csv")
PATCH_LOG = os.path.join(HERE, "out", "patched_points.csv")

URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
HEAD = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
MKT = {"5": "sh", "1": "sz", "0": "sz", "3": "sz"}


def refetch(code: str, beg: str, end: str, tries: int = 3):
    mkt = MKT[code[0]]
    for _ in range(tries):
        try:
            r = requests.get(URL, params={"param": f"{mkt}{code},day,{beg},{end},760,qfq"},
                             headers=HEAD, timeout=25)
            node = r.json()["data"][f"{mkt}{code}"]
            rows = node.get("qfqday") or node.get("day")
            return {x[0]: (float(x[1]), float(x[2]), float(x[3]), float(x[4]))
                    for x in rows}
        except Exception:                                # noqa: BLE001
            continue
    return {}


def main():
    df = pd.read_csv(LONG, dtype={"code": str, "date": str})
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    assert df["date"].notna().all(), "原始长表存在无法解析的 date, 请先重抓"
    df = df.sort_values(["code", "date"]).reset_index(drop=True)

    panel = df.pivot_table(index="date", columns="code", values="close").sort_index()
    plan = {}                       # (code, Timestamp) -> (value, method)

    for code in panel.columns:
        s = panel[code]
        valid = s.dropna()
        if valid.empty:
            continue
        first_valid = valid.index[0]
        tail = s.loc[first_valid:]
        holes = tail[tail.isna()].index
        if len(holes) == 0:
            continue
        lo = (holes.min() - pd.Timedelta(days=40)).strftime("%Y-%m-%d")
        hi = (holes.max() + pd.Timedelta(days=40)).strftime("%Y-%m-%d")
        got = refetch(code, lo, hi)
        n_refetch = n_interp = 0
        for d in holes:
            key = d.strftime("%Y-%m-%d")
            if key in got:
                o, c, h, l = got[key]
                plan[(code, d)] = (c, "REFETCH")
                n_refetch += 1
            else:
                before = s.loc[:d].dropna()
                after = s.loc[d:].dropna()
                if before.empty or after.empty:
                    continue
                p0, p1 = before.iloc[-1], after.iloc[0]
                d0, d1 = before.index[-1], after.index[0]
                w = (d - d0).days / max((d1 - d0).days, 1)
                v = float(p0 * (p1 / p0) ** w)        # 几何插值
                plan[(code, d)] = (v, f"INTERP({p0:.4f}->{p1:.4f})")
                n_interp += 1
        if plan:
            print(f"[PATCH] {code}: 重抓补回 {n_refetch} 根, 插值补 {n_interp} 根")

    if not plan:
        print("[PATCH] 无需修补")
        return

    # ---- upsert 修补值 ----
    added = updated = 0
    for (code, d), (v, how) in plan.items():
        m = (df["code"] == code) & (df["date"] == d)
        if m.any():
            df.loc[m, "close"] = v
            updated += int(m.sum())
        else:
            df.loc[len(df)] = [code, d, v, v, v, v]
            added += 1
        print(f"         {d.date()}  {code}  close={v:.6f}  [{how}]")

    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    df["date"] = df["date"].dt.strftime("%Y%m%d").astype(str)
    df.to_csv(LONG, index=False, encoding="utf-8")

    log = [dict(code=c, date=d.strftime("%Y-%m-%d"), close=round(v, 6), method=h)
           for (c, d), (v, h) in sorted(plan.items(), key=lambda x: (x[0][0], x[0][1]))]
    os.makedirs(os.path.dirname(PATCH_LOG), exist_ok=True)
    with open(PATCH_LOG, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["code", "date", "close", "method"])
        w.writeheader()
        w.writerows(log)
    print(f"\n修补 {len(plan)} 根 (更新已有行 {updated} / 新增行 {added}) -> {PATCH_LOG}")


if __name__ == "__main__":
    main()
