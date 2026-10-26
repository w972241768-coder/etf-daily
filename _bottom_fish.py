#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""博主「抄底模型」验证（2026-10-26）

用户的猜测：
  A. 全池动横20 < 0 → 当天卖头名、次日买排名最后一名
  B. 或：买动横20 < −30% 的最后一名
博主实操（今日）：清仓 513100、明日抄底 159967（= 倒数第二，非末位）

测试 4 个维度：
  ① 触发条件：全池负 / 最弱 ≤ −30%
  ② 选股位置：末位 / 倒数第二
  ③ 时序：当日换 / 当日空仓次日买（博主原话）
  ④ 资金：满仓 / 半仓 + 跌破 1% 补满（博主原话）

⚠️ 本地研究脚本，不入库。
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import etf_backtest_long as bl  # noqa: E402

COST = 0.0001
BASE = bl.LIVE_COMPOSITE


def one(panel, dates, pool, dfn, s_date, **kw):
    si = max(int(dates.searchsorted(pd.Timestamp(s_date), side="left")), 0)
    p = dict(BASE)
    p.update(kw)
    r = bl.backtest(panel, dates, si, len(dates) - 1, pool, dfn,
                    buy=COST, sell=COST, W=20, MA=20, value_engine=True, **p)
    st = bl.stats(r["navs"])
    return r, st


def row(tag, r, st, base):
    d = r["final"] - base[0]
    dm = (st["mdd"] - base[1]) * 100
    last = (r["held_log"][-1][1] if r["held_log"] else "?")
    return (f"{tag:38s} {r['final']:8.4f} {d:+8.4f} {st['mdd']*100:7.2f}% "
            f"{dm:+6.2f}pp {st['calmar'] or 0:5.2f} "
            f"{len(r.get('weak_events') or []):4d} {last:8s}")


def get_panel():
    """长历史 CSV + 在线数据拼到最新交易日（与日报同一路径）。"""
    hist = bl.read_panel(os.path.join(HERE, "tdx_adj_long_2019_2026.csv"))
    print(f"长历史 CSV 覆盖至 {hist.index.max():%Y-%m-%d}")
    try:
        import online_data as od
        import recent_rotations as rr
        rot, deff, watch = bl.read_pools()
        res = od.fetch_online([c for c in (rot + deff + watch) if c])
        live = res.get("df")
        if live is not None and len(live):
            panel, note = rr._extend_panel(hist, live, None)
            print(f"拼接结果：{note}")
            if panel.index.max() > hist.index.max():
                print(f"→ 已补到 {panel.index.max():%Y-%m-%d}")
            return panel
        print("⚠ 在线数据为空，退回长历史")
    except Exception as e:                       # noqa: BLE001
        print(f"⚠ 在线拼接失败（{type(e).__name__}: {e}），退回长历史")
    return hist


def main():
    panel = get_panel()
    rot, deff, _w = bl.read_pools()
    pool = [c for c in rot if c in panel.columns]
    dfn = deff[0] if deff else "512040"
    dates = panel.index
    print(f"数据区间 {dates[0].date()} → {dates[-1].date()}（共 {len(dates)} 根）")
    print(f"主选池 {len(pool)} 只")

    wins = [("主窗6.7y", "2019-12-31"), ("近5年", "2021-10-25")]
    hdr = (f"{'方案':38s} {'净值':>8} {'Δ净值':>8} {'MDD':>8} {'ΔMDD':>9} "
           f"{'Calmar':>5} {'触发':>4} {'末持仓':>8}")

    for wn, sd in wins:
        rb, sb = one(panel, dates, pool, dfn, sd)
        base = (rb["final"], sb["mdd"])
        print("\n" + "=" * 108)
        print(f"### {wn}（{sd} 起）基线 D3+P3：{base[0]:.4f} / MDD {base[1]*100:.2f}%")
        print(hdr)
        print("-" * 108)
        print(row("基线（D3+P3）", rb, sb, base))

        cases = [
            ("A1 全池负→当日换末位", dict(weak_mode="allneg", weak_rank=1, weak_delay=0)),
            ("A2 全池负→空仓,次日买末位", dict(weak_mode="allneg", weak_rank=1, weak_delay=1)),
            ("A3 全池负→当日换倒数第2", dict(weak_mode="allneg", weak_rank=2, weak_delay=0)),
            ("A4 全池负→空仓,次日买倒数第2", dict(weak_mode="allneg", weak_rank=2, weak_delay=1)),
            ("B1 末位≤-30%→换末位", dict(weak_mode="-30", weak_rank=1, weak_delay=0)),
            ("B2 末位≤-30%→换倒数第2", dict(weak_mode="-30", weak_rank=2, weak_delay=0)),
            ("B3 末位≤-20%→换末位", dict(weak_mode="-20", weak_rank=1, weak_delay=0)),
            ("A1+半仓跌破1%补满", dict(weak_mode="allneg", weak_rank=1, weak_delay=0,
                                     pos_frac=0.5, add_drop=0.01)),
            ("A1+半仓永不补", dict(weak_mode="allneg", weak_rank=1, weak_delay=0,
                                  pos_frac=0.5, add_drop=99.0)),
        ]
        for tag, kw in cases:
            r, st = one(panel, dates, pool, dfn, sd, **kw)
            print(row(tag, r, st, base))

    # ---- 触发日分布：全池负 vs 最弱≤−30% ----
    print("\n" + "=" * 108)
    print("### 触发条件统计（主窗 2019-12-31 起）")
    r_all, _ = one(panel, dates, pool, dfn, "2019-12-31", weak_mode="allneg", weak_delay=0)
    ev = r_all.get("weak_events") or []
    print(f"① 全池动横20 全为负的天数（换持触发）：{len(ev)} 次")
    if ev:
        mm = np.array([e["m"] for e in ev])
        print(f"   触发时「最弱那只」的动横20：中位 {np.median(mm):.1f}%  "
              f"最深 {mm.min():.1f}%  最浅 {mm.max():.1f}%")
        for th in (-15, -20, -25, -30):
            k = int((mm <= th).sum())
            print(f"   其中 ≤ {th}% 的：{k}/{len(mm)} = {k/len(mm)*100:.0f}%")
        from collections import Counter
        cnt = Counter(f"{e['code']}" for e in ev)
        print("   被买过的标的：", "、".join(f"{k}×{v}" for k, v in cnt.most_common(8)))


if __name__ == "__main__":
    main()
