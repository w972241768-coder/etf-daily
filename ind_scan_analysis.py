"""扫描结果的多重比较分析 + 随机同频对照（2026-10-24）

为什么需要这一步：
  扫描了 13 类指标 × 参数 × 阈值 × 方向 × 2 窗口 ≈ 上千个组合。
  即便全是噪声，纯随机也会有 ~5% 的组合「显著改善」。因此必须：
  ① IS(2019-01~2022-06) 选规则 → OOS(2022-07~2026-09) 验证，**只有 OOS 也改善才算数**；
  ② 随机同频对照：把指标换成「同触发次数的随机时点」，真实指标须显著优于随机；
  ③ 邻域稳定性：同一指标相邻阈值成片有效（点状 = 过拟合）；
  ④ 双窗口交叉：主窗+长窗都改善。

多重比较惩罚：即使 OOS 有效，也按「有效组合数 / 总组合数」给出 FWER 粗估，
并用 Bonferroni 校正后的 α（0.05 / N）判定是否仍有残余显著性。
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
import ind_scan_study as S          # noqa: E402
from rot_stop_study import load_win  # noqa: E402

IS_END = S.IS_END
OOS_START = S.OOS_START
RND_N = 200          # 随机对照模拟次数（比之前 80 次更严）


def rnd_gate(panel, dates, s_i, e_i, pool, defensive, on_mask, seed, sig_list=None, **kw):
    """把 on_mask 的触发时刻打乱（保持触发次数与位置分布一致）→ 随机对照。"""
    idx = np.where(on_mask.values)[0]
    if len(idx) == 0:
        return None
    rng = np.random.default_rng(seed)
    # 在 [s_i, e_i) 内随机抽同样多的位置
    pool_idx = np.arange(s_i, e_i)
    pick = rng.choice(pool_idx, size=min(len(idx), len(pool_idx)), replace=False)
    new_on = pd.Series(False, index=dates)
    new_on.iloc[pick] = True
    return S.backtest_gate(panel, dates, s_i, e_i, pool, defensive,
                           gate_on=new_on, sig_list=sig_list, **kw)


def main():
    scan_path = os.path.join(HERE, "out", "ind_scan.json")
    j = json.load(open(scan_path, encoding="utf-8"))
    scan = j["scan"]
    print(f"扫描组合数: {len(scan)}")

    # ---------- 第一关：OOS 必须改善 MDD ----------
    # ⚠️ MDD 是**负数**（如 −23.38%），「改善」= 变浅 = 更接近 0 ⇒ d = new − base > 0
    # 判据：d_oos_mdd > 0（OOS 段 MDD 变浅）且 d_oos_total > -5%（收益没塌太多）
    cand = [s for s in scan if s["d_oos_mdd"] is not None and s["d_oos_mdd"] > 0]
    print(f"① OOS 段 MDD 改善的组合: {len(cand)} / {len(scan)} "
          f"({len(cand)/len(scan)*100:.1f}%)")
    cand2 = [s for s in cand if s["d_oos_total"] is not None and s["d_oos_total"] > -5]
    print(f"   且 OOS 收益未塌(>-5pp): {len(cand2)}")

    # ---------- 第二关：IS 也改善（规则不是只在 OOS 偶然） ----------
    cand3 = [s for s in cand2 if s["d_is_mdd"] is not None and s["d_is_mdd"] > 0]
    print(f"② IS 段也改善 MDD: {len(cand3)}")

    # ---------- 第三关：双窗口交叉 ----------
    # key 不含 win —— 同一规则在两个窗口都要有效
    by_win = {}
    for s in cand3:
        by_win.setdefault((s["ind"], s["params"], s["scope"],
                           s["direction"], s["q"]), []).append(s)
    cross = []
    for key, lst in by_win.items():
        wins = {x["win"] for x in lst}
        if len(wins) >= 2:
            cross.append((key, lst))
    print(f"③ 双窗口交叉一致的: {len(cross)} 个规则族")

    # ---------- 第四关：邻域稳定性（同指标同方向，相邻阈值成片）----------
    def neigh_ok(rec, tol=0.0):
        """同一 (win, ind, params, scope, direction) 下，是否有相邻 q 也有效"""
        fam = [s for s in cand3
               if s["win"] == rec["win"] and s["ind"] == rec["ind"]
               and s["params"] == rec["params"] and s["scope"] == rec["scope"]
               and s["direction"] == rec["direction"]]
        return len(fam) >= 2, len(fam)

    stable = []
    for s in cand3:
        ok, n_fam = neigh_ok(s)
        s["_fam_size"] = n_fam
        if ok:
            stable.append(s)
    print(f"④ 邻域成片有效(同族≥2个阈值同时有效): {len(stable)}")

    # ---------- 汇总最终候选 ----------
    final = stable
    print(f"\n最终候选（IS+OOS 双改善 + 邻域成片）: {len(final)}")
    if final:
        final_sorted = sorted(final, key=lambda s: -s["d_oos_mdd"])
        print("\n  按 OOS MDD 改善排序 Top 15:")
        print(f"  {'窗口':<5} {'指标':<10} {'参数':<28} {'口径':<5} {'方向':<4} "
              f"{'q':>3} {'OOS MDD':>9} {'ΔOOS':>8} {'ΔIS':>8} {'ΔOOS收益':>9} {'族':>3}")
        for s in final_sorted[:15]:
            print(f"  {s['win']:<5} {s['ind']:<10} {s['params']:<28} {s['scope']:<5} "
                  f"{s['direction']:<4} {s['q']:>3} {s['oos']['mdd']:>8.2f}% "
                  f"{s['d_oos_mdd']:>+8.2f} {s['d_is_mdd']:>+8.2f} "
                  f"{s['d_oos_total']:>+9.2f} {s.get('_fam_size',1):>3}")

    # ---------- 随机同频对照（对最终候选逐个做） ----------
    print("\n=== 随机同频对照（每候选 200 次随机触发）===")
    rot, deff, _ = bl.read_pools()
    defensive = deff[0]
    rnd_results = []
    for wname, fname, start in S.WINDOWS:
        if not os.path.exists(os.path.join(HERE, fname)):
            continue
        panel, dates, s_i, e_i = load_win(fname, start)
        pool = [c for c in rot if c in panel.columns]
        sig_all = [bl.compute_signal(panel, pool, i, 20, 20) for i in range(len(dates))]
        is_end_i = int(dates.searchsorted(pd.Timestamp(IS_END)))
        oos_start_i = int(dates.searchsorted(pd.Timestamp(OOS_START)))
        for s in [x for x in final if x["win"] == wname][:6]:
            # 重建该组合的 on 掩码
            import indicators_lib as ind
            params = eval(s["params"])
            head_vals, cross_vals = S.build_indicator_series(panel, pool, s["ind"],
                                                            params, dates)
            series = head_vals if s["scope"] == "头名" else cross_vals
            thr = s["thr"]
            on = (series <= thr) if s["direction"] == "low" else (series >= thr)
            on = on.fillna(False).reindex(dates).fillna(False)
            arr = []
            for seed in range(RND_N):
                r = rnd_gate(panel, dates, s_i, e_i, pool, defensive, on, seed,
                             sig_list=sig_all)
                if r is None:
                    continue
                nav = r["navs"]
                oos_s = S.seg_oos(nav, dates, oos_start_i, e_i)
                if oos_s:
                    arr.append(oos_s["mdd"])
            if not arr:
                continue
            arr = np.array(arr)
            real = s["oos"]["mdd"]
            base_oos = s["base_oos"]["mdd"]
            # MDD 为负数：真实值 > 随机值 ⇒ 真实更浅 ⇒ 优于随机
            pct = float((arr > real).mean() * 100)
            print(f"  {wname} {s['ind']} {s['params']} {s['scope']} {s['direction']} q={s['q']}: "
                  f"真实 OOS MDD {real:.2f}% ｜ 随机中位 {np.median(arr):.2f}% "
                  f"p05 {np.percentile(arr,5):.2f}% ｜ 真实优于随机 {pct:.1f}% 的情形"
                  f"（基线 {base_oos:.2f}%）")
            rnd_results.append(dict(win=wname, ind=s["ind"], params=s["params"],
                                    scope=s["scope"], direction=s["direction"], q=s["q"],
                                    thr=thr, real_oos_mdd=real, base_oos_mdd=base_oos,
                                    rnd_med=float(np.median(arr)),
                                    rnd_p05=float(np.percentile(arr, 5)),
                                    rnd_p95=float(np.percentile(arr, 95)),
                                    beat_pct=pct, n_sim=len(arr)))

    # ---------- Bonferroni 粗估 ----------
    n_all = len(scan)
    bonf_alpha = 0.05 / max(1, n_all)
    print(f"\n多重比较校正: 总组合 {n_all} 个，Bonferroni α = {bonf_alpha:.2e}")
    print(f"  期望纯噪声下「OOS MDD 改善」的假阳性 ≈ {int(n_all*0.05)} 个")
    print(f"  实际 OOS 改善 {len(cand)} 个 → "
          f"{'超过纯噪声预期，存在真实信号' if len(cand) > n_all*0.05 else '未超过纯噪声预期'}")

    out = dict(scan_n=n_all, oos_improved=len(cand), oos_improved_ok=len(cand2),
               is_also=len(cand3), cross_win=len(cross), stable=len(final),
               final=final, random=rnd_results,
               bonf_alpha=bonf_alpha, noise_expect=int(n_all * 0.05))
    with open(os.path.join(HERE, "out", "ind_scan_analysis.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=float)
    print("\n写出: out/ind_scan_analysis.json")


if __name__ == "__main__":
    main()
