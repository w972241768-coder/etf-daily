# -*- coding: utf-8 -*-
"""规则对比研究：512890 自定义卖出线 vs 512040 原策略。

主对比（用户指定）：
  A  512890  买入=回踩 MA200（1.00）  卖出=超 MA200 +3%（1.03）
  B  512040  买入=MA200−3%（0.97）    卖出=超 MA200 +6%（1.06）

参照组（用于说明 A 的卖出线为何拖累）：
  A1 512890  买入=回踩 MA200（1.00）  卖出=超 MA200 +6%（1.06）
  A0 512890  原策略 −3%/+6%

口径：前复权、零手续费、初始净值 1；收益按 k−1 日状态结算（无未来函数）。
数据：长历史前复权 CSV 打底 + 在线补尾部（⚠️ 在线序列对 512890 有 2021-10-25 −51% 假跳空）。
"""
import os
import sys
import json

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import online_data as od
from etf_rotation import build_ve_curve

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)

# key: (code, buy, sell)
CONFIGS = {
    "A":  ("512890", 1.00, 1.03),   # 用户指定：回踩买 / +3% 卖
    "B":  ("512040", 0.97, 1.06),   # 原策略：−3% 买 / +6% 卖
    "A1": ("512890", 1.00, 1.06),   # 参照：回踩买 / +6% 卖
    "A0": ("512890", 0.97, 1.06),   # 参照：原参数
}
LABELS = {
    "A":  "512890 回踩买 / +3% 卖",
    "B":  "512040 −3% 买 / +6% 卖（原策略）",
    "A1": "512890 回踩买 / +6% 卖",
    "A0": "512890 −3% 买 / +6% 卖（原参数）",
}


def metrics(vc):
    nav = pd.Series(vc["nav"], index=pd.to_datetime(vc["dates"]))
    r = nav.pct_change().dropna()
    vol = float(r.std() * np.sqrt(252) * 100) if len(r) else 0.0
    m = dict(vc)
    m["vol"] = vol
    m["sharpe"] = float((vc["cagr"] - 2.0) / vol) if vol else 0.0
    m["calmar"] = float(vc["cagr"] / abs(vc["mdd"])) if vc["mdd"] else 0.0
    return m


def trades(vc):
    """把 events 配成逐笔交易（买入日 → 卖出日，盈亏 %，持有天数）。"""
    nav = pd.Series(vc["nav"], index=pd.to_datetime(vc["dates"]))
    rows, entry = [], None
    for e in vc["events"]:
        d = pd.Timestamp(e["date"])
        if e["act"] == "BUY":
            entry = d
        elif e["act"] == "SELL" and entry is not None:
            seg = nav[(nav.index >= entry) & (nav.index <= d)]
            if len(seg) > 1:
                rows.append(dict(buy=str(entry.date()), sell=str(d.date()),
                                 days=int((d - entry).days),
                                 pnl=float((seg.iloc[-1] / seg.iloc[0] - 1) * 100)))
            entry = None
    if entry is not None:
        seg = nav[nav.index >= entry]
        if len(seg) > 1:
            rows.append(dict(buy=str(entry.date()), sell="持有中",
                             days=int((nav.index[-1] - entry).days),
                             pnl=float((seg.iloc[-1] / seg.iloc[0] - 1) * 100)))
    return rows


def yearly_ret(vc):
    nav = pd.Series(vc["nav"], index=pd.to_datetime(vc["dates"]))
    bh = pd.Series(vc["bh"], index=pd.to_datetime(vc["dates"]))
    out = {}
    for y, chunk in nav.groupby(nav.index.year):
        prev = nav[nav.index.year < y]
        prevb = bh[bh.index.year < y]
        base = float(prev.iloc[-1]) if len(prev) else 1.0
        bbase = float(prevb.iloc[-1]) if len(prevb) else 1.0
        out[y] = dict(ret=(float(chunk.iloc[-1]) / base - 1) * 100,
                      bh=(float(chunk.iloc[-1]) / bbase - 1) * 100)
    return out


def sellline_scan(df, code, buy=1.00):
    """固定买线，扫卖线，量化「卖出线越紧越吃亏」。"""
    out = {}
    for sell in (1.03, 1.04, 1.05, 1.06, 1.08, 1.10, 1.12, 1.15):
        vc = build_ve_curve(df, code, buy=buy, sell=sell, years=5)
        if vc:
            out[sell] = dict(nav=vc["final"], ret=vc["ret"], cagr=vc["cagr"],
                             mdd=vc["mdd"], hold=vc["hold_rate"],
                             n_buy=vc["n_buy"], n_sell=vc["n_sell"])
    return out


def main():
    codes = sorted({c for c, _, _ in CONFIGS.values()})
    res = od.fetch_online(codes, datalen=1600)
    df = res["df"]

    ms, vcs, tr, yr = {}, {}, {}, {}
    for k, (code, buy, sell) in CONFIGS.items():
        vc = build_ve_curve(df, code, buy=buy, sell=sell, years=5)
        if vc is None:
            print(f"[FAIL] {k} {code} 数据不足")
            continue
        vcs[k] = vc
        ms[k] = metrics(vc)
        tr[k] = trades(vc)
        yr[k] = yearly_ret(vc)
        m = ms[k]
        print(f"[{k}] {code} 买{buy:.2f}/卖{sell:.2f}  "
              f"{vc['d0']:%Y-%m-%d}~{vc['d1']:%Y-%m-%d} ({vc['n']}日)")
        print(f"    净值 {m['final']:.4f} ({m['ret']:+.2f}%) | 年化 {m['cagr']:+.2f}% | "
              f"MDD {m['mdd']:.2f}% | 波动 {m['vol']:.2f}% | 夏普 {m['sharpe']:.2f} | "
              f"Calmar {m['calmar']:.2f}")
        print(f"    持仓 {m['hold_rate']:.1f}% | 买 {m['n_buy']} 卖 {m['n_sell']} | "
              f"持有 {vc['bh_final']:.4f} ({vc['bh_ret']:+.2f}%) MDD {vc['bh_mdd']:.2f}%")

    print("\n逐笔交易（A 用户指定规则 vs B 原策略）")
    for k in ("A", "B"):
        print(f"  --- {LABELS[k]} ---")
        for t in tr[k]:
            tag = "  ← 持有中" if t["sell"] == "持有中" else ""
            print(f"    {t['buy']} → {t['sell']}  {t['days']:>4}日  {t['pnl']:+7.2f}%{tag}")
        if tr[k]:
            avg = np.mean([t["pnl"] for t in tr[k]])
            print(f"    合计 {len(tr[k])} 笔｜平均单笔 {avg:+.2f}%｜"
                  f"平均持有 {np.mean([t['days'] for t in tr[k]]):.0f} 日")

    scan = sellline_scan(df, "512890", buy=1.00)
    print("\n512890 卖线扫描（买线固定=回踩 MA200）")
    for s, v in scan.items():
        print(f"  卖 +{(s-1)*100:>4.0f}%  净值 {v['nav']:.4f}  年化 {v['cagr']:+6.2f}%  "
              f"MDD {v['mdd']:>7.2f}%  持仓 {v['hold']:>5.1f}%  买{v['n_buy']}卖{v['n_sell']}")

    payload = dict(
        window=dict(d0=str(vcs["A"]["d0"].date()), d1=str(vcs["A"]["d1"].date())),
        configs={k: dict(label=LABELS[k], code=c, buy=b, sell=s)
                 for k, (c, b, s) in CONFIGS.items()},
        metrics={k: {kk: (str(pd.Timestamp(vv).date()) if isinstance(vv, pd.Timestamp) else vv)
                     for kk, vv in m.items()
                     if kk not in ("dates", "nav", "bh", "hold", "events", "d0", "d1")}
                 for k, m in ms.items()},
        series={k: dict(dates=v["dates"], nav=v["nav"], bh=v["bh"], hold=v["hold"],
                        events=[dict(date=str(pd.Timestamp(e["date"]).date()), act=e["act"])
                                for e in v["events"]])
                for k, v in vcs.items()},
        trades=tr,
        yearly={k: {str(y): v for y, v in rows.items()} for k, rows in yr.items()},
        scan={f"{k}": v for k, v in scan.items()},
    )
    p = os.path.join(OUT, "ve_rule_compare.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\n[saved] {p}")
    return payload


if __name__ == "__main__":
    main()
