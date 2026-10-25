# -*- coding: utf-8 -*-
"""512890 红利低波 vs 512040 价值ETF —— 同一价值引擎规则下的近 5 年对比研究。

规则（与报告「防守位操作指南」完全一致）：
  · 收盘 < MA200×0.97  → 买入 / 持有
  · 收盘 > MA200×1.06  → 卖出 / 持币
  · 中间区间            → 维持上一状态
  · 未满足买点          → 持币（不追高）
口径：前复权、零手续费、初始净值 1；收益按 k-1 日状态结算（无未来函数）。

⚠️ 数据陷阱（两只都中）：
  新浪在线日线未还原份额拆分 —— 512890 于 2021-10-22 做 1:2 拆分（1.6004→0.8002），
  56 在线序列在 2021-10-25 出现 −51.13% 假跳空；512040 于 2023-10-11 有 −50.13%。
  fetch_online 报 factors=1.0，常数因子修不掉序列中段事件。
  ⇒ 必须以长历史前复权 CSV 打底，在线只补尾部。
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

CODES = [
    ("512890", "红利低波ETF", "华泰柏瑞中证红利低波动"),
    ("512040", "价值ETF富国", "富国中证国信价值"),
]


def sensitivity(pairs=None):
    """同一规则换买线/卖线，看两只标的的净值与持仓占比（仅用长历史 CSV，窗口截止 CSV 末日）。"""
    hist = pd.read_csv(os.path.join(HERE, "tdx_adj_long_2019_2026.csv"), parse_dates=["date"])
    pairs = pairs or [(0.97, 1.06), (0.98, 1.06), (0.99, 1.06), (1.00, 1.06),
                      (0.97, 1.10), (0.98, 1.10), (0.99, 1.10), (1.00, 1.10)]

    def run(code, buy, sell, ma=200, years=5):
        h = hist[hist["code"].astype(str).str.zfill(6) == code].sort_values("date")
        s = h.set_index("date")["close"].astype(float)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        mav = s.rolling(ma).mean()
        hold, st = False, []
        for t in s.index:
            px = float(s.loc[t])
            mv = float(mav.loc[t]) if mav.loc[t] == mav.loc[t] else None
            if mv:
                if px < mv * buy:
                    hold = True
                elif hold and px > mv * sell:
                    hold = False
            st.append(hold)
        st = pd.Series(st, index=s.index, dtype=bool)
        cut = s.index[-1] - pd.DateOffset(years=years)
        w, sw = s[s.index >= cut], st[s.index >= cut]
        nav = 1.0
        for i in range(1, len(w)):
            if bool(sw.iloc[i - 1]):
                nav *= float(w.iloc[i]) / float(w.iloc[i - 1])
        return float(nav), float(sw.mean() * 100)

    out = {}
    for buy, sell in pairs:
        a_nav, a_hold = run("512890", buy, sell)
        b_nav, b_hold = run("512040", buy, sell)
        out[(buy, sell)] = dict(a_nav=a_nav, a_hold=a_hold, b_nav=b_nav, b_hold=b_hold)
    return out


def main():
    res = od.fetch_online([c for c, _, _ in CODES], datalen=1600)
    df = res["df"]
    vcs = {}
    for code, _, _ in CODES:
        vc = build_ve_curve(df, code, years=5)
        if vc is None:
            print(f"[FAIL] {code} 数据不足")
            continue
        vcs[code] = vc
        print(f"[{code}] {vc['d0']:%Y-%m-%d}~{vc['d1']:%Y-%m-%d} ({vc['n']} 日) "
              f"净值 {vc['final']:.4f} ({vc['ret']:+.2f}%) | 年化 {vc['cagr']:+.2f}% | "
              f"MDD {vc['mdd']:.2f}% | 持仓 {vc['hold_rate']:.1f}% | "
              f"买 {vc['n_buy']} 卖 {vc['n_sell']} | 买入持有 {vc['bh_final']:.4f} "
              f"({vc['bh_ret']:+.2f}%) MDD {vc['bh_mdd']:.2f}%")
        print(f"    数据: {'; '.join(vc['notes'])}")
        for e in vc["events"]:
            print(f"    {pd.Timestamp(e['date']):%Y-%m-%d} {e['act']}")

    # ---------- 逐年收益 ----------
    yearly = {}
    for code, vc in vcs.items():
        nav = pd.Series(vc["nav"], index=pd.to_datetime(vc["dates"]))
        bh = pd.Series(vc["bh"], index=pd.to_datetime(vc["dates"]))
        rows = {}
        for y, chunk in nav.groupby(nav.index.year):
            if y == nav.index[0].year:
                base, bbase = 1.0, 1.0
            else:
                prev = nav[nav.index.year < y]
                base = float(prev.iloc[-1]) if len(prev) else 1.0
                prevb = bh[bh.index.year < y]
                bbase = float(prevb.iloc[-1]) if len(prevb) else 1.0
            rows[y] = dict(ret=(float(chunk.iloc[-1]) / base - 1) * 100,
                           bh=(float(chunk.iloc[-1]) / bbase - 1) * 100)
        yearly[code] = rows

    print("\n逐年收益（策略 vs 买入持有）")
    for y in range(2021, 2027):
        line = f"  {y}  "
        for code, _, _ in CODES:
            r = yearly.get(code, {}).get(y)
            line += (f"{code}: {r['ret']:+7.2f}%（BH {r['bh']:+7.2f}%）  "
                     if r else f"{code}: {'—':>7}  ")
        print(line)

    payload = dict(
        window=dict(d0=str(vcs[CODES[0][0]]["d0"].date()),
                    d1=str(vcs[CODES[0][0]]["d1"].date())),
        metrics={c: {k: (str(pd.Timestamp(v).date()) if isinstance(v, pd.Timestamp) else v)
                     for k, v in vc.items()
                     if k not in ("dates", "nav", "bh", "hold", "events", "d0", "d1")}
                 for c, vc in vcs.items()},
        series={c: dict(dates=vc["dates"], nav=vc["nav"], bh=vc["bh"], hold=vc["hold"],
                        events=[dict(date=str(pd.Timestamp(e["date"]).date()),
                                     act=e["act"]) for e in vc["events"]])
                for c, vc in vcs.items()},
        yearly={c: {str(k): v for k, v in rows.items()} for c, rows in yearly.items()},
        names={c: n for c, n, _ in CODES},
        idx_names={c: i for c, _, i in CODES},
    )
    p = os.path.join(OUT, "ve_512890_vs_512040.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\n[saved] {p}")
    return payload


if __name__ == "__main__":
    main()
