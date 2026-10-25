"""中证2000（932000）双均线「多空闸门」研究（2026-10-25）

背景：用户想用指南针 0AMV 做择时（多头全仓 / 空头半仓），但 0AMV 数据无法获取
（指南针专有、文件加密）。退而用用户自己提的替代方案：

    站上 MA53          -> 多头区间
    多头中首次跌破 MA19 -> 空头区间
    （空头只有在重新站上 MA53 才转多）

闸门：多头 -> 仓位 1.0；空头 -> 仓位 half（测 0.0 / 0.3 / 0.5 / 0.7）

对照维度：
  · 载体：基线 / D3 / P3(纯) / D3+P3
  · 窗口：主窗 2019-12-31 起、长窗 2015-01-05 起
  · 成本：单边万一（用户口径），初始净值 1
  · 随机同频：把「半仓日」打散到同样天数，看真实闸门是否优于随机
口径说明：932000 历史序列为中证指数公司回溯数据（指数 2023-08 发布），
  走势形态可用，但存在事后编制偏差，结论按"机制验证"看待。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import etf_backtest_long as bl     # noqa: E402
from rot_stop_study import load_win  # noqa: E402

COST = 0.0001                      # 单边万一
BASE = dict(sticky=1.0, enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
            exit_lead=10.0, W=20, MA=20, value_engine=True)
KITS = {
    "基线": {},
    "D3": dict(loss_cool=0.03, loss_days=5),
    "P3": dict(roc_q=0.95, roc_days=20, roc_n=60, roc_min_periods=250),
    "D3+P3": dict(loss_cool=0.03, loss_days=5,
                  roc_q=0.95, roc_days=20, roc_n=60, roc_min_periods=250),
}
WINDOWS = [("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
           ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05")]
IDX = "932000"


def zz_regime(close: pd.Series, fast: int = 19, slow: int = 53) -> pd.Series:
    """站上 MA53 = 多头；多头中首次跌破 MA19 = 空头。返回 1.0/0.0。"""
    mf = close.rolling(fast).mean()
    ms = close.rolling(slow).mean()
    st, out = 0, []
    for c, f, s in zip(close.values, mf.values, ms.values):
        if c == c and s == s:
            if c > s:
                st = 1
            elif st == 1 and f == f and c < f:
                st = 0
        out.append(float(st))
    return pd.Series(out, index=close.index, dtype=float)


def build_regime(dates: pd.DatetimeIndex) -> pd.Series:
    import online_data as od
    d = od.fetch_csindex_daily(IDX, beg="2014-01-01")
    s = d.set_index("date")["close"].astype(float).sort_index()
    reg = zz_regime(s)
    # 对齐到回测交易日；缺失（指数与 ETF 节假日差异）沿用前一日状态
    return reg.reindex(dates, method="ffill").fillna(0.0)


def stats_of(nav: pd.Series) -> dict:
    st = bl.stats(nav)
    total = (float(nav.iloc[-1]) - 1.0) * 100.0
    return dict(final=float(nav.iloc[-1]), total=total,
                mdd=float(st["mdd"]) * 100.0, sharpe=float(st["sharpe"]),
                calmar=float(st["calmar"]))


def year_ret(nav: pd.Series) -> dict:
    out = {}
    for d in bl.yearly(nav):
        out[str(d["year"])] = dict(ret=float(d["ret"]) * 100.0,
                                   mdd=float(d["mdd"]) * 100.0)
    return out


def main() -> None:
    res = {"grid": [], "rand": [], "yearly": [], "signal": {}}
    for wname, fname, start in WINDOWS:
        panel, dates, s_i, e_i = load_win(os.path.join(HERE, fname), start)
        rot, deff, _ = bl.read_pools()
        pool = [c for c in rot if c in panel.columns]
        sig_all = [bl.compute_signal(panel, pool, i, 20, 20) for i in range(len(dates))]
        reg = build_regime(dates)

        # ---------- 信号本身的信息量 ----------
        r = reg.iloc[s_i:e_i]
        sh = panel["510300"].iloc[s_i:e_i] if "510300" in panel.columns else None
        zz = None
        import online_data as od
        _d = od.fetch_csindex_daily(IDX, beg="2014-01-01")
        _s = _d.set_index("date")["close"].astype(float).sort_index()
        _r = _s.pct_change().reindex(dates, method="ffill")
        zz = _r.iloc[s_i:e_i]
        bull_n = int((r == 1).sum())
        bear_n = int((r == 0).sum())
        sw = int((r.diff().fillna(0) != 0).sum())
        res["signal"][wname] = dict(
            bull=bull_n, bear=bear_n, switch=sw,
            bull_pct=bull_n / max(1, len(r)) * 100,
            zz_bull=float(zz[r.values == 1].mean() * 100) if bull_n else None,
            zz_bear=float(zz[r.values == 0].mean() * 100) if bear_n else None,
            sh_bull=(float(sh.pct_change()[r.values == 1].mean() * 100)
                     if sh is not None and bull_n else None),
            sh_bear=(float(sh.pct_change()[r.values == 0].mean() * 100)
                     if sh is not None and bear_n else None),
        )
        print(f"\n=== {wname} 信号：多头 {bull_n} 日({bull_n/len(r)*100:.0f}%) / "
              f"空头 {bear_n} 日 ｜ 切换 {sw} 次 ===")
        print(f"  中证2000 日均：多头 {res['signal'][wname]['zz_bull']:+.3f}%  "
              f"空头 {res['signal'][wname]['zz_bear']:+.3f}%")
        print(f"  上证  日均：多头 {res['signal'][wname]['sh_bull']:+.3f}%  "
              f"空头 {res['signal'][wname]['sh_bear']:+.3f}%")

        # ---------- 回测网格 ----------
        for kname, kit in KITS.items():
            kw = dict(buy=COST, sell=COST, **BASE, **kit)
            r_no = bl.backtest(panel, dates, s_i, e_i, pool, deff[0],
                               sig_list=sig_all, **kw)
            s_no = stats_of(r_no["navs"])
            row_base = dict(win=wname, kit=kname, half=None, **s_no,
                            trades=len(r_no["trades"]))
            res["grid"].append(row_base)
            print(f"\n  [{kname}] 无闸门 final={s_no['final']:.4f} "
                  f"MDD={s_no['mdd']:.2f}% Calmar={s_no['calmar']:.2f}")
            for half in (0.0, 0.3, 0.5, 0.7):
                ps = reg.map({1.0: 1.0, 0.0: float(half)})
                rr = bl.backtest(panel, dates, s_i, e_i, pool, deff[0],
                                 sig_list=sig_all, pos_scale=ps, **kw)
                st = stats_of(rr["navs"])
                res["grid"].append(dict(win=wname, kit=kname, half=half, **st,
                                        trades=len(rr["trades"])))
                print(f"    空头{half:.1f}仓 final={st['final']:.4f} "
                      f"MDD={st['mdd']:6.2f}% Calmar={st['calmar']:.2f} "
                      f"| vs 无闸门: final {st['final']-s_no['final']:+.4f} "
                      f"MDD {st['mdd']-s_no['mdd']:+.2f}pp")
                if kname == "D3+P3":
                    res["yearly"].append(dict(win=wname, half=half,
                                              y=year_ret(rr["navs"])))
            if kname == "D3+P3":
                res["yearly"].append(dict(win=wname, half=None,
                                          y=year_ret(r_no["navs"])))
                # ---- 归因：空头期策略到底赚不赚、持的是什么 ----
                from collections import Counter
                hl = dict(r_no["held_log"])
                rts = r_no["navs"].pct_change()
                mkey = pd.Series(reg.values == 0,
                                 index=dates.strftime("%Y-%m-%d"))
                m = mkey.reindex(rts.index).fillna(False).astype(bool)
                bear_r, bull_r = rts[m], rts[~m]
                cb = Counter(hl[d] for d in rts.index[m] if d in hl)
                tot_b = sum(cb.values()) or 1
                top = [(k, round(v / tot_b * 100, 1)) for k, v in cb.most_common(4)]
                res["attrib"] = res.get("attrib", {})
                res["attrib"][wname] = dict(
                    bear_days=int(len(bear_r)), bell_days=int(len(bull_r)),
                    bear_mean=float(bear_r.mean() * 100) if len(bear_r) else None,
                    bull_mean=float(bull_r.mean() * 100) if len(bull_r) else None,
                    bear_win=float((bear_r > 0).mean() * 100) if len(bear_r) else None,
                    bull_win=float((bull_r > 0).mean() * 100) if len(bull_r) else None,
                    bear_hold_top=top)
                print(f"\n  [归因] {wname} 空头期策略日均 {bear_r.mean()*100:+.3f}% "
                      f"(胜 {(bear_r>0).mean()*100:.0f}%) ｜ 多头期 {bull_r.mean()*100:+.3f}% "
                      f"(胜 {(bull_r>0).mean()*100:.0f}%)")
                print(f"         空头期持仓 TOP: {top}")

        # ---------- 随机同频对照（D3+P3，半仓 0.5）----------
        bw = reg.map({1.0: 1.0, 0.0: 0.5}).iloc[s_i:e_i].values
        n_bear = int((bw < 1.0).sum())
        finals, mdds = [], []
        rng = np.random.default_rng(20261025)
        for _ in range(60):
            idx = rng.choice(len(bw), size=n_bear, replace=False) + s_i
            arr = np.ones(len(dates))
            arr[idx] = 0.5
            ps = pd.Series(arr, index=range(len(dates)))
            rr = bl.backtest(panel, dates, s_i, e_i, pool, deff[0],
                             sig_list=sig_all, pos_scale=ps,
                             buy=COST, sell=COST, **BASE, **KITS["D3+P3"])
            st = stats_of(rr["navs"])
            finals.append(st["final"]); mdds.append(st["mdd"])
        real = [g for g in res["grid"] if g["win"] == wname
                and g["kit"] == "D3+P3" and g["half"] == 0.5][0]
        res["rand"].append(dict(win=wname, n_bear=n_bear,
                                real_final=real["final"], real_mdd=real["mdd"],
                                med_final=float(np.median(finals)),
                                p95_final=float(np.percentile(finals, 95)),
                                beat_final=float((np.array(finals) < real["final"]).mean() * 100),
                                med_mdd=float(np.median(mdds)),
                                beat_mdd=float((np.array(mdds) < real["mdd"]).mean() * 100)))
        print(f"\n  [随机同频] 半仓日 {n_bear} 天 × 60 次：final 中位 "
              f"{np.median(finals):.4f}（真实 {real['final']:.4f}，优于随机 "
              f"{res['rand'][-1]['beat_final']:.0f}%）｜ MDD 中位 "
              f"{np.median(mdds):.2f}%（真实 {real['mdd']:.2f}%）")

    with open(os.path.join(HERE, "out", "zz2000_gate.json"), "w",
              encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("\n已存 out/zz2000_gate.json")


if __name__ == "__main__":
    main()
