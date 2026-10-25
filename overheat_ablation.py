"""深挖：关掉过热熔断后 D3+P3 为何反而更好（2026-10-24）

发现（price_stop_study.py）：
  主窗：基线+D3+P3 = 11.0196/−15.12%/C2.85 ；**关过热+D3+P3 = 11.1824/−19.71%/C2.20**
  长窗：基线+D3+P3 = 20.0195/−17.61%/C1.66 ；**关过热+D3+P3 = 26.9621/−19.71%/C1.65**
  ⇒ 关掉过热后长窗净值 +35%，Calmar 持平；主窗净值略升、MDD 略差。

必须验证：
  ① 逐年拆解，确认不是某一年偶然
  ② MDD 段对比（关过热后回撤段在哪）
  ③ 机制解释：过热熔断「切防守 512040」在 D3+P3 存在时是否成了拖累
     —— 检查过热触发后 512040 的表现 vs 空仓/继续持有头名
  ④ 邻域稳定性：enter_dev/enter_lead 阈值变化是否成片
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

COST = 0.0001
W, MA = 20, 20
WINDOWS = [("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
           ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05")]
BASE = dict(sticky=1.0, enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
            exit_lead=10.0, min_def_days=5, buy=COST, sell=COST,
            W=W, MA=MA, value_engine=True)
P3KW = dict(loss_cool=0.03, loss_days=5, roc_q=0.95, roc_days=20, roc_n=60,
            roc_min_periods=250)


def stats(nav):
    st = bl.stats(nav)
    return dict(final=float(nav.iloc[-1]), total=st["total"] * 100,
                mdd=st["mdd"] * 100, sharpe=st["sharpe"], calmar=st["calmar"])


def yearly(nav):
    df = pd.DataFrame({"v": nav.to_numpy(float)}, index=pd.to_datetime(nav.index))
    out = {}
    for y, g in df.groupby(df.index.year):
        v = g["v"].to_numpy()
        peak = np.maximum.accumulate(v)
        out[y] = dict(ret=(v[-1] / v[0] - 1) * 100,
                      mdd=float((v / peak - 1).min() * 100))
    return out


def mdd_seg(nav):
    v = nav.to_numpy(float)
    peak = np.maximum.accumulate(v)
    dd = v / peak - 1.0
    i = int(np.argmin(dd))
    j0 = int(np.argmax(v[:i + 1]))
    return nav.index[j0], nav.index[i], float(dd[i] * 100)


def fwd(panel, code, i, k):
    if code not in panel.columns:
        return None
    j = min(i + k, len(panel) - 1)
    p0, p1 = float(panel[code].iloc[i]), float(panel[code].iloc[j])
    if p0 != p0 or p0 == 0 or p1 != p1:
        return None
    return (p1 / p0 - 1.0) * 100.0


def main():
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    out = {"cases": [], "mech": [], "nbr": []}

    for wname, fname, start in WINDOWS:
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        sig_all = [bl.compute_signal(panel, pool, i, W, MA) for i in range(len(dates))]
        print(f"\n{'#'*100}\n# 窗口 {wname}  {dates[s_i].date()} ~ {dates[e_i].date()}\n{'#'*100}")

        # 两个方案
        kw_on = dict(BASE)
        kw_on.update(P3KW)
        kw_off = dict(BASE)
        kw_off.update(P3KW)
        kw_off.update(enter_dev=999.0, enter_lead=999.0)
        r_on = bl.backtest(panel, dates, s_i, e_i, pool, defensive, **kw_on)
        r_off = bl.backtest(panel, dates, s_i, e_i, pool, defensive, **kw_off)
        s_on, s_off = stats(r_on["navs"]), stats(r_off["navs"])
        print(f"\n  D3+P3（含过热）: {s_on['final']:.4f} / {s_on['mdd']:.2f}% / C{s_on['calmar']:.2f}"
              f" / 夏普{s_on['sharpe']:.2f} / 换仓{len(r_on['trades'])}")
        print(f"  D3+P3（关过热）: {s_off['final']:.4f} / {s_off['mdd']:.2f}% / C{s_off['calmar']:.2f}"
              f" / 夏普{s_off['sharpe']:.2f} / 换仓{len(r_off['trades'])}")
        print(f"  ⇒ 差：净值 {s_off['final']-s_on['final']:+.4f}  "
              f"MDD {s_off['mdd']-s_on['mdd']:+.2f}pp  Calmar {s_off['calmar']-s_on['calmar']:+.2f}")

        # ① 逐年
        yon, yoff = yearly(r_on["navs"]), yearly(r_off["navs"])
        print(f"\n  【① 逐年对比】")
        print(f"  {'年':>6}{'含过热收益':>12}{'关过热收益':>12}{'差':>10}"
              f"{'含过热MDD':>12}{'关过热MDD':>12}{'差':>10}")
        for y in sorted(yon):
            a, b = yon[y], yoff[y]
            print(f"  {y:>6}{a['ret']:>+11.1f}%{b['ret']:>+11.1f}%{b['ret']-a['ret']:>+9.1f}pp"
                  f"{a['mdd']:>11.1f}%{b['mdd']:>11.1f}%{b['mdd']-a['mdd']:>+9.1f}pp")
        out["cases"].append(dict(win=wname, on=s_on, off=s_off,
                                 yearly=dict(on=yon, off=yoff),
                                 on_trades=len(r_on["trades"]),
                                 off_trades=len(r_off["trades"])))

        # ② MDD 段
        so, eo, mo = mdd_seg(r_on["navs"])
        sf, ef, mf = mdd_seg(r_off["navs"])
        print(f"\n  【② MDD 段】含过热: {so} → {eo}  {mo:.2f}%")
        print(f"              关过热: {sf} → {ef}  {mf:.2f}%")

        # ③ 机制：过热触发后，512040 与「当时头名」谁更好
        print(f"\n  【③ 机制】过热熔断触发后：防守位 512040 vs 当时头名 vs 空仓")
        over_dates = [t for t in r_on["trades"] if "过热" in (t.get("reason") or "")]
        rows = []
        for t in over_dates:
            i = int(dates.searchsorted(pd.Timestamp(t["date"]), side="left"))
            if i >= len(dates) - 21 or sig_all[i] is None:
                continue
            h = sig_all[i]["head"]
            f_head20 = fwd(panel, h, i, 20)
            f_def20 = fwd(panel, defensive, i, 20)
            if f_head20 is None or f_def20 is None:
                continue
            rows.append(dict(date=t["date"], head=h, head_name=bl.NAMES.get(h, h),
                             f_head20=f_head20, f_def20=f_def20))
        if rows:
            dr = pd.DataFrame(rows)
            print(f"    过热触发 {len(dr)} 次，其后 20 日：")
            print(f"      当时头名 均 {dr.f_head20.mean():+.2f}%  中位 {dr.f_head20.median():+.2f}%"
                  f"  胜率 {(dr.f_head20>0).mean()*100:.0f}%")
            print(f"      防守512040 均 {dr.f_def20.mean():+.2f}%  中位 {dr.f_def20.median():+.2f}%"
                  f"  胜率 {(dr.f_def20>0).mean()*100:.0f}%")
            better = (dr.f_def20 > dr.f_head20).mean() * 100
            print(f"      ⇒ 切防守更好的比例: {better:.0f}%（100% 说明过热熔断总是对的）")
            out["mech"].append(dict(win=wname, n=len(dr),
                                    head_mean=float(dr.f_head20.mean()),
                                    head_med=float(dr.f_head20.median()),
                                    def_mean=float(dr.f_def20.mean()),
                                    def_med=float(dr.f_def20.median()),
                                    def_better_pct=float(better),
                                    head_win=float((dr.f_head20 > 0).mean() * 100),
                                    def_win=float((dr.f_def20 > 0).mean() * 100),
                                    rows=rows))

        # ④ 邻域稳定性：过热阈值扫描
        print(f"\n  【④ 邻域稳定性】过热阈值扫描（叠加 D3+P3）")
        for ed, el in ((6.0, 8.0), (9.0, 12.0), (12.0, 15.0), (15.0, 20.0),
                       (20.0, 25.0), (999.0, 999.0)):
            kw = dict(BASE)
            kw.update(P3KW)
            kw.update(enter_dev=ed, enter_lead=el)
            r = bl.backtest(panel, dates, s_i, e_i, pool, defensive, **kw)
            st = stats(r["navs"])
            lab = "关过热" if ed > 500 else f"乖离{ed:.0f}%/领先{el:.0f}pp"
            print(f"    {lab:<20} final={st['final']:>9.4f} mdd={st['mdd']:>7.2f}% "
                  f"calmar={st['calmar']:>5.2f} sharpe={st['sharpe']:>5.2f} "
                  f"换仓{len(r['trades']):>4}")
            out["nbr"].append(dict(win=wname, enter_dev=ed, enter_lead=el,
                                   final=st["final"], mdd=st["mdd"],
                                   calmar=st["calmar"], sharpe=st["sharpe"],
                                   trades=len(r["trades"])))

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

    with open(os.path.join(HERE, "out", "overheat_ablation.json"), "w", encoding="utf-8") as f:
        json.dump(_clean(out), f, ensure_ascii=False, indent=1)
    print("\n写出: out/overheat_ablation.json")


if __name__ == "__main__":
    main()
