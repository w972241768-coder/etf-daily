"""动横指标择时研究（2026-10-24 晚）—— 诊断 + 预测力检验 + 回测

用户核心洞察（原话）：
  「最近这一段时间的回撤，不管是基础策略、D3、P3 都是没有避免。我认为的原因是，
    在震荡下行时，ROC60 一定不会到历史 95 分位的，所有的都在下跌，就不可能出现上涨的情况。
    怎么能通过动横指标判断出来目前并不适合持有、适合空仓，
    那么怎么又通过动横指标判断出来目前适合重新进场，避免错过一大波的涨幅。」

⇒ 要回答三个问题：
  Q1 P3 在最近这段深回撤里是不是真的没触发？（验证用户前提）
  Q2 用**动横20 本身**（不是 ROC60）能不能识别「不适合持有」？
     候选信号：头名动横20 的绝对值（<=0/5%/10%）或自身滚动分位（<=10/20/30 分位）
  Q3 同一指标能不能识别「适合重新进场」？（恢复信号）
     候选：头名动横20 回到滚动 50/60 分位，或绝对值回到 >=5%/10%

⚠️ 已证伪、本轮不再重复：广度止损（横截面转弱=避险格局，三次复现失败）。
   本轮只用**头名自身的时间序列位置**，与 P3 的 ROC60 高分位形成互补（高分位止盈 / 低分位避险）。
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

W, MA = 20, 20
WINDOWS = [("main", "tdx_adj_long_2019_2026.csv", "2019-12-31"),
           ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05")]
COST = 0.0001                       # 单边万一
BASE = dict(sticky=1.0, enter_dev=12.0, enter_lead=15.0, exit_dev=9.0,
            exit_lead=10.0, min_def_days=5, W=W, MA=MA, value_engine=True)


def head_mom_series(panel, pool, dates):
    """头名动横20 序列（每日动横20 最高的那个标的的动横20）"""
    mom_df = pd.DataFrame({c: (panel[c] / panel[c].shift(W) - 1.0) * 100.0
                           for c in pool if c in panel.columns})
    head_codes = mom_df.idxmax(axis=1)
    st = mom_df.stack(future_stack=True)
    hv = st.reindex(pd.MultiIndex.from_arrays(
        [head_codes.index, head_codes.values])).reset_index(level=1, drop=True)
    hv.index.name = mom_df.index.name
    return hv.reindex(mom_df.index)


def fwd_ret(panel, code, i, k):
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
    out = {"q1_p3_in_dd": [], "q2_exit": [], "q3_reentry": [], "bt": []}

    for wname, fname, start in WINDOWS:
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        hv = head_mom_series(panel, pool, dates)
        sig_all = [bl.compute_signal(panel, pool, i, W, MA) for i in range(len(dates))]
        roc_on = bl.roc_head_hold(panel, pool, dates, W, 60, 0.95, 250)

        print(f"\n{'#'*94}\n# 窗口 {wname}  {dates[s_i].date()} ~ {dates[e_i].date()}\n{'#'*94}")

        # ---------- Q1: P3 在深回撤段有没有触发 ----------
        b0 = bl.backtest(panel, dates, s_i, e_i, pool, defensive, **BASE)
        rp = bl.backtest(panel, dates, s_i, e_i, pool, defensive, **BASE,
                         loss_cool=0.03, loss_days=5, roc_q=0.95, roc_days=20,
                         roc_n=60, roc_min_periods=250)
        nav0, navp = b0["navs"], rp["navs"]

        def mdd_seg(nav):
            v = nav.to_numpy(float)
            peak = np.maximum.accumulate(v)
            dd = v / peak - 1.0
            i = int(np.argmin(dd))
            j0 = int(np.argmax(v[:i + 1]))
            return nav.index[j0], nav.index[i], float(dd[i] * 100)

        s0, e0, m0 = mdd_seg(nav0)
        sp, ep, mp = mdd_seg(navp)
        print(f"\n【Q1】深回撤段（验证「震荡下行时 ROC60 到不了 95 分位」）")
        print(f"  基线 MDD 段: {s0} → {e0}  {m0:.2f}%")
        print(f"  P3   MDD 段: {sp} → {ep}  {mp:.2f}%")
        # 在基线 MDD 段内，P3 触发过几次？
        s0s, e0s = str(s0)[:10], str(e0)[:10]
        seg = [d for d in roc_on.index if s0s <= str(d)[:10] <= e0s]
        trig_in_seg = [d for d in seg if bool(roc_on.loc[d])]
        print(f"  基线 MDD 段内共 {len(seg)} 个交易日，P3 触发 {len(trig_in_seg)} 次")
        # 该段内头名动横20 与 ROC60 的分布
        m_seg = (hv.index >= s0s) & (hv.index <= e0s)
        hv_seg = hv[m_seg]
        print(f"  段内头名动横20: 均 {hv_seg.mean():+.2f}%  最低 {hv_seg.min():+.2f}%  "
              f"最高 {hv_seg.max():+.2f}%  为正当数 {(hv_seg > 0).sum()}/{len(hv_seg)}")
        roc60_seg = pd.Series([sig_all[i]['m'][sig_all[i]['head']] for i in
                               range(len(dates)) if s0 <= dates[i].strftime('%Y-%m-%d') <= e0
                               and sig_all[i] is not None], dtype=float)
        # 用 60 日动量重算段内头名 ROC60
        roc60_seg = []
        for i in range(len(dates)):
            d = dates[i].strftime("%Y-%m-%d")
            if not (s0s <= d <= e0s) or sig_all[i] is None:
                continue
            h = sig_all[i]["head"]
            roc60_seg.append(fwd_ret(panel, h, i, 60))
        roc60_seg = pd.Series([x for x in roc60_seg if x is not None], dtype=float)
        if len(roc60_seg):
            print(f"  段内头名 ROC60: 均 {roc60_seg.mean():+.2f}%  最高 {roc60_seg.max():+.2f}% "
                  f"(95 分位阈值约为 {hv.expanding(min_periods=250).quantile(0.95).iloc[-1]:.2f}% 的动横口径)")
        out["q1_p3_in_dd"].append(dict(
            win=wname, seg=[s0, e0, m0], p3_mdd_seg=[sp, ep, mp],
            n_days=len(seg), p3_trig_in_seg=len(trig_in_seg),
            hv_mean=float(hv_seg.mean()), hv_min=float(hv_seg.min()),
            hv_max=float(hv_seg.max()), hv_pos=int((hv_seg > 0).sum()),
            roc60_mean=float(roc60_seg.mean()) if len(roc60_seg) else None,
            roc60_max=float(roc60_seg.max()) if len(roc60_seg) else None))

        # ---------- Q2: 头名动横20 低位 → 未来表现（该不该空仓） ----------
        print(f"\n【Q2】头名动横20 低位 → 未来 5/10/20 日表现（判断「不适合持有」）")
        thr_lo = hv.expanding(min_periods=250).quantile(0.10)
        thr_lo20 = hv.expanding(min_periods=250).quantile(0.20)
        thr_lo30 = hv.expanding(min_periods=250).quantile(0.30)
        rows = []
        for lab, mask in (
            ("动横20 <= 0", (hv <= 0)),
            ("动横20 <= 5%", (hv <= 5)),
            ("动横20 <= 10%", (hv <= 10)),
            ("<= 自身滚动 10 分位", (hv <= thr_lo)),
            ("<= 自身滚动 20 分位", (hv <= thr_lo20)),
            ("<= 自身滚动 30 分位", (hv <= thr_lo30)),
        ):
            mm = mask.fillna(False).reindex(dates).fillna(False)
            idx = [i for i in range(s_i, len(dates) - 21) if bool(mm.iloc[i])
                   and sig_all[i] is not None]
            if len(idx) < 20:
                continue
            f5 = [fwd_ret(panel, sig_all[i]["head"], i, 5) for i in idx]
            f10 = [fwd_ret(panel, sig_all[i]["head"], i, 10) for i in idx]
            f20 = [fwd_ret(panel, sig_all[i]["head"], i, 20) for i in idx]
            f5 = [x for x in f5 if x is not None]
            f10 = [x for x in f10 if x is not None]
            f20 = [x for x in f20 if x is not None]
            print(f"  {lab:<22} n={len(idx):>4}  "
                  f"5日均{np.mean(f5):+6.2f}%/{np.median(f5):+6.2f}%  "
                  f"10日均{np.mean(f10):+6.2f}%/{np.median(f10):+6.2f}%  "
                  f"20日均{np.mean(f20):+6.2f}%/{np.median(f20):+6.2f}%  "
                  f"20日胜{(np.mean([x>0 for x in f20]))*100:3.0f}%")
            rows.append(dict(label=lab, n=len(idx),
                             f5_mean=float(np.mean(f5)), f5_med=float(np.median(f5)),
                             f10_mean=float(np.mean(f10)), f10_med=float(np.median(f10)),
                             f20_mean=float(np.mean(f20)), f20_med=float(np.median(f20)),
                             f20_win=float(np.mean([x > 0 for x in f20]) * 100)))
        # 全样本基准
        allf20 = [fwd_ret(panel, sig_all[i]["head"], i, 20)
                  for i in range(s_i, len(dates) - 21) if sig_all[i] is not None]
        allf20 = [x for x in allf20 if x is not None]
        print(f"  {'（全样本基准）':<22} n={len(allf20):>4}  "
              f"20日均{np.mean(allf20):+6.2f}%/{np.median(allf20):+6.2f}%  "
              f"胜{np.mean([x>0 for x in allf20])*100:3.0f}%")
        out["q2_exit"].append(dict(win=wname, rows=rows,
                                  all_f20_mean=float(np.mean(allf20)),
                                  all_f20_med=float(np.median(allf20)),
                                  all_f20_win=float(np.mean([x > 0 for x in allf20]) * 100)))

        # ---------- Q3: 恢复信号 → 未来表现（该不该进场） ----------
        print(f"\n【Q3】头名动横20 从低位恢复 → 未来表现（判断「适合重新进场」）")
        # 定义「刚从低位上来」：当日动横20 >= 阈值 且 前一日 < 阈值（穿越）
        for lab, lvl, hi in (("上穿 0%", 0, None), ("上穿 5%", 5, None),
                             ("上穿 10%", 10, None)):
            cross = (hv >= lvl) & (hv.shift(1) < lvl)
            mm = cross.fillna(False).reindex(dates).fillna(False)
            idx = [i for i in range(s_i, len(dates) - 21) if bool(mm.iloc[i])
                   and sig_all[i] is not None]
            if len(idx) < 15:
                print(f"  {lab:<22} 样本不足 ({len(idx)})")
                continue
            f5 = [x for x in (fwd_ret(panel, sig_all[i]["head"], i, 5) for i in idx) if x]
            f10 = [x for x in (fwd_ret(panel, sig_all[i]["head"], i, 10) for i in idx) if x]
            f20 = [x for x in (fwd_ret(panel, sig_all[i]["head"], i, 20) for i in idx) if x]
            print(f"  {lab:<22} n={len(idx):>4}  "
                  f"5日均{np.mean(f5):+6.2f}%  10日均{np.mean(f10):+6.2f}%  "
                  f"20日均{np.mean(f20):+6.2f}%/{np.median(f20):+6.2f}%  "
                  f"20日胜{np.mean([x>0 for x in f20])*100:3.0f}%")
        # 滚动分位恢复
        thr_mid = hv.expanding(min_periods=250).quantile(0.50)
        for lab, lvl in (("上穿自身滚动 50 分位", None),):
            cross = (hv >= thr_mid) & (hv.shift(1) < thr_mid)
            mm = cross.fillna(False).reindex(dates).fillna(False)
            idx = [i for i in range(s_i, len(dates) - 21) if bool(mm.iloc[i])
                   and sig_all[i] is not None]
            if len(idx) >= 15:
                f5 = [x for x in (fwd_ret(panel, sig_all[i]["head"], i, 5) for i in idx) if x]
                f10 = [x for x in (fwd_ret(panel, sig_all[i]["head"], i, 10) for i in idx) if x]
                f20 = [x for x in (fwd_ret(panel, sig_all[i]["head"], i, 20) for i in idx) if x]
                print(f"  {lab:<22} n={len(idx):>4}  "
                      f"5日均{np.mean(f5):+6.2f}%  10日均{np.mean(f10):+6.2f}%  "
                      f"20日均{np.mean(f20):+6.2f}%/{np.median(f20):+6.2f}%  "
                      f"20日胜{np.mean([x>0 for x in f20])*100:3.0f}%")

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

    with open(os.path.join(HERE, "out", "mom_timing_diag.json"), "w", encoding="utf-8") as f:
        json.dump(_clean(out), f, ensure_ascii=False, indent=1)
    print("\n写出: out/mom_timing_diag.json")


if __name__ == "__main__":
    main()
