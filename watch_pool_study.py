"""备选池（watch）三策略回测对比（2026-10-24）

用户诉求：
  「基于备用轮动池，再做一下基础、D3、P3 策略的主窗与长窗的回测，
    我想看一下策略是不是适用别的池子。初始净值为 1，单边交易手续费万一。」

口径：
  · 初始净值 1（bl.backtest 本身从 1 起）
  · 单边万一 ⇒ buy = sell = 0.0001（双边 0.02%）
  · 防守位仍为 512040（defensive，不参与排名）
  · 备选池两个版本：
      W14 = watch 剔除 512880（512880 是原主选池成员后被降级，同口径对比须剔除）
      W15 = watch 全 15 只（含 512880）
  · 同时重跑主选池 9 只作同表对照

⚠️ 项目铁律：凡按年份/期间切分，必须先打印各年可用标的数做 sanity check
   （备选池多只标的是 2021 年后上市，长窗 2015 起时可用数很少，会影响可比性）。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import etf_backtest_long as bl      # noqa: E402
from rot_stop_study import load_win  # noqa: E402

COST = 0.0001                       # 单边万一
BASE = dict(sticky=1.0, enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
            exit_lead=10.0, min_def_days=5, W=20, MA=20, value_engine=True)
WINDOWS = [
    ("主窗", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
]
# 长窗：主选池用 2015 起的长历史；备选池受上市时间限制最早只能到 2018-12-03
# （备选池多只 2023~2025 年才上市，2015-2018 数据不存在）。
# 为保可比，主选池同时跑 2018-12-03 起，两池可在同起点对比。
LONG_MAIN = ("长窗", "tdx_adj_long_2015_2026.csv", "2015-01-05")
LONG_SAME = ("长窗(同起点)", "tdx_adj_long_2019_2026.csv", "2018-12-03")
SCHEMES = [
    ("基线", dict()),
    ("D3", dict(loss_cool=0.03, loss_days=5)),
    ("P3", dict(loss_cool=0.03, loss_days=5, roc_q=0.95, roc_days=20,
                roc_n=60, roc_min_periods=250)),
]


def avail_by_year(panel, pool, dates, s_i):
    """各年可用标的数（sanity check）"""
    cols = [c for c in pool if c in panel.columns]
    out = {}
    for i in range(s_i, len(dates)):
        y = dates[i].year
        n = sum(1 for c in cols if not pd.isna(panel[c].iloc[i]))
        out.setdefault(y, []).append(n)
    return {y: int(np.mean(v)) for y, v in sorted(out.items())}


def yearly_ret(nav):
    df = pd.DataFrame({"v": nav.to_numpy(float)}, index=pd.to_datetime(nav.index))
    out = {}
    for y, g in df.groupby(df.index.year):
        v = g["v"].to_numpy()
        peak = np.maximum.accumulate(v)
        out[y] = dict(ret=(v[-1] / v[0] - 1) * 100,
                      mdd=float((v / peak - 1).min() * 100))
    return out


def main():
    rot, deff, watch = bl.read_pools()
    defensive = deff[0]
    watch_no880 = [c for c in watch if c != "512880"]

    POOLS = [
        ("主选池 9 只", rot),
        ("备选池 14 只（剔除512880）", watch_no880),
        ("备选池 15 只（全含）", watch),
    ]

    res = {"meta": dict(cost=COST, pools=[(n, len(p)) for n, p in POOLS]),
           "cases": [], "avail": []}

    for pname, pool in POOLS:
        # 主选池跑「主窗 + 2015 起步长窗 + 2018 同起点」；备选池跑「主窗 + 2018 同起点」
        if pname.startswith("主选池"):
            wins = WINDOWS + [LONG_MAIN, LONG_SAME]
        else:
            wins = WINDOWS + [LONG_SAME]
        for wname, fname, start in wins:
            if not os.path.exists(os.path.join(HERE, fname)):
                continue
            panel, dates, s_i, e_i = load_win(fname, start)
            use = [c for c in pool if c in panel.columns]
            av = avail_by_year(panel, use, dates, s_i)
            if not use or sum(av.values()) == 0:
                print(f"\n{'='*104}")
                print(f"【{pname}】窗口 {wname}：⚠️ 该长历史 CSV 不含本池任何标的，跳过")
                print(f"{'='*104}")
                continue
            print(f"\n{'='*104}")
            print(f"【{pname}】窗口 {wname}  {dates[s_i].date()} ~ {dates[e_i].date()}  "
                  f"单边万一 · 初始净值 1 · 池内 {len(use)}/{len(pool)} 只可交易")
            print(f"  各年可用标的数: " + "  ".join(f"{y}:{n}" for y, n in av.items()))
            print(f"{'='*104}")
            print(f"  {'方案':<6}{'净值':>10}{'区间收益':>12}{'年化':>9}{'MDD':>9}"
                  f"{'Calmar':>8}{'夏普':>7}{'换仓':>6}{'空仓日':>7}{'持仓集中度TOP3':>18}")
            for sname, kw in SCHEMES:
                r = bl.backtest(panel, dates, s_i, e_i, use, defensive,
                                buy=COST, sell=COST, **BASE, **kw)
                st = bl.stats(r["navs"])
                if st["calmar"] is None or st["sharpe"] is None:
                    print(f"  {sname:<6}（该池/窗口无有效样本，跳过）")
                    continue
                # 持仓集中度
                held = pd.Series([t for _d, t in r["held_log"]])
                held = held[held != "CASH"]
                conc = held.value_counts(normalize=True).head(3) * 100
                conc_txt = "/".join(f"{bl.NAMES.get(c,c)[:4]}{v:.0f}%" for c, v in conc.items())
                print(f"  {sname:<6}{float(r['navs'].iloc[-1]):>10.4f}"
                      f"{st['total']*100:>11.2f}%{st['ann']*100:>8.2f}%"
                      f"{st['mdd']*100:>8.2f}%{st['calmar']:>8.2f}{st['sharpe']:>7.2f}"
                      f"{len(r['trades']):>6}"
                      f"{int(sum(1 for _d,t in r['held_log'] if t=='CASH')):>7}"
                      f"{conc_txt:>18}")
                yr = yearly_ret(r["navs"])
                res["cases"].append(dict(
                    pool=pname, win=wname, scheme=sname,
                    final=float(r["navs"].iloc[-1]), total=st["total"] * 100,
                    ann=st["ann"] * 100, mdd=st["mdd"] * 100,
                    sharpe=st["sharpe"], calmar=st["calmar"],
                    trades=len(r["trades"]),
                    cash_days=int(sum(1 for _d, t in r["held_log"] if t == "CASH")),
                    n_pool=len(use), start=str(dates[s_i].date()),
                    end=str(dates[e_i].date()),
                    yearly={y: v for y, v in yr.items()},
                    conc={c: float(v) for c, v in conc.items()}))
            res["avail"].append(dict(pool=pname, win=wname, avail=av))
            # 逐年收益对比（三个方案）
            print(f"\n    逐年收益（基线/D3/P3）：")
            rj = {}
            for sname, kw in SCHEMES:
                r = bl.backtest(panel, dates, s_i, e_i, use, defensive,
                                buy=COST, sell=COST, **BASE, **kw)
                rj[sname] = yearly_ret(r["navs"])
            years = sorted(rj["基线"].keys())
            for y in years:
                b = rj["基线"][y]
                d = rj["D3"].get(y, {})
                p = rj["P3"].get(y, {})
                print(f"      {y}: 收益 {b['ret']:+7.1f}%/{d.get('ret',0):+7.1f}%/"
                      f"{p.get('ret',0):+7.1f}%   MDD {b['mdd']:6.1f}%/{d.get('mdd',0):6.1f}%/"
                      f"{p.get('mdd',0):6.1f}%")

    def _clean(o):
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(v) for v in o]
        if isinstance(o, (np.floating, np.integer)):
            o = o.item()
        if isinstance(o, float) and o != o:
            return None
        return o

    with open(os.path.join(HERE, "out", "watch_pool_compare.json"), "w", encoding="utf-8") as f:
        json.dump(_clean(res), f, ensure_ascii=False, indent=1)
    print("\n写出: out/watch_pool_compare.json")


if __name__ == "__main__":
    main()
