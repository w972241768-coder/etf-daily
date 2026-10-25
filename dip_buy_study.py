#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抄底策略研究 · 截图模块还原 + 合入 ROC 轮动 + 2020-2026 回测
================================================================================
[截图语义] 参考博主面板新增模块：
    「抄底监控 → 轮动持正, 未触发」
    「需头名能量脉冲转负空仓后才启动抄底」
  解读（与已还原的秋收计划1024体系同源对齐）：
    - 正常态 = 轮动持正（持有头名）
    - 头名「能量脉冲」转负 → 清仓空仓，同时启动抄底监控
    - 空仓期间满足抄底条件 → 买回（回到轮动持正）
  ★ [MISSING] 博主未公开「能量脉冲」公式与抄底触发条件——本地资料库(131个mp4+指标docx)
    检索无果，仅有截图一句描述。本研究的实现是【语义推测】：
      能量脉冲 ≈ 头名动横20（博主信号台把动横20叫「动量惯性」，是同源动量指标，
                  转负即动量消失，与已还原的"头名动横20>0才持有"规则一脉相承）
      抄底条件  ≈ 空仓期间头名乖离(P/MA20-1)一度跌破超跌阈值(默认-3%)，
                  之后收复 MA20（乖离回到 >0）才买回——"跌透了的反弹"才接，
                  不满足就一直持币等（这是与原版"动横转正即买回"的关键差异）。
  所有假设均在报告第①节显式标注，结论只回答"这个语义下的抄底模块有没有增量"。

[版本矩阵]
  V0  现行 ROC 轮动策略（ROC20 + 过热熔断 + 512040价值引擎防守，无抄底模块）= 基准
  V1  V0 + 抄底模块（超跌阈值 -3%，收复MA20买回）
  V1a V0 + 抄底模块（阈值 -2%）   —— 阈值敏感性
  V1b V0 + 抄底模块（阈值 -5%）
  V2  V0 + 抄底模块（-3% + 双确认：收复MA20 且 动横20>0 才买回）

[回测口径] 与 2026-10-07 研究一致：窗口 2020-01-01（基点取 2019-12-31 收盘=1.000）
  ~ 2026-09-30；单边手续费万1（0.0001）；初始净值 1；前复权长历史 + 在线补尾部；
  基准 = 上证指数 000001 / 纳斯达克综合 ^IXIC。状态机：过热熔断 DEF 优先于抄底模块。
"""
import html as _html
import json
import os
import sys
from collections import OrderedDict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import etf_backtest_long as E  # noqa: E402
import online_data as OD  # noqa: E402
from roc_param_study import (build_panel, dd_summary, mdd_window,  # noqa: E402
                             nasdaq_from, svg_curve, svg_mdd_bars)
from backtest_dd_study import dd_summary as _dds  # noqa: E402,F401  (保持依赖可见)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, "out", "dip_study.json")
DEF_CODE = "512040"
COST_SIDE = 0.0001
START = "2020-01-01"
NAMES = E.NAMES


# ------------------------------------------------------------------ 抄底回测
def backtest_dip(panel, dates, start_i, end_i, pool, defensive, *,
                 sticky=1.0, enter_dev=12.0, enter_lead=15.0,
                 exit_dev=9.0, exit_lead=10.0, min_def_days=5,
                 buy=COST_SIDE, sell=COST_SIDE, W=20, MA=20,
                 value_engine=True, dip_thresh=-3.0, dual_confirm=False,
                 allow_escape=True):
    """ROC 轮动 + 过热熔断(512040+VE) + 抄底模块。

    在 etf_backtest_long.backtest 的状态机上叠加：
      ROT 态下若粘性后头名的动横20 < 0 → 清仓持币(dip_armed)，启动抄底监控；
      dip_armed 期间照常计算排名，头名乖离一度 <= dip_thresh 记 dip_hit；
      dip_hit 后头名乖离回到 >0(收复MA20) [且 dual_confirm 时要求动横20>0] → 买回头名。
    DEF(过热熔断)优先级高于抄底模块；DEF 切回时若头名动横<0 直接进入抄底空仓。
    """
    state = "ROT"
    def_days = 0
    held = "CASH"
    nav = 1.0
    navs = [(dates[start_i], 1.0)]
    trades = []
    prev_eff = None
    ve_hold = value_engine_hold_local(panel, defensive, dates) if value_engine else None
    dip_armed = False
    dip_hit = False
    dip_enter = None            # 进入抄底空仓的日期
    dip_spans = []              # [enter, exit or None]
    dip_hits = 0                # 完成买回的抄底次数
    stick_hits = 0

    for i in range(start_i, end_i):
        sig = E.compute_signal(panel, pool, i, W, MA)
        if sig is None:
            ret = 0.0
            if held != "CASH" and held in panel.columns:
                p0, p1 = panel[held].iloc[i], panel[held].iloc[i + 1]
                if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                    ret = p1 / p0 - 1.0
            nav *= (1.0 + ret)
            navs.append((dates[i + 1], nav))
            continue

        raw_head = sig["head"]
        eff_head = raw_head
        tied = False
        if prev_eff is not None and prev_eff not in sig["rank"]:
            prev_eff = None
        if sticky > 0 and prev_eff is not None and sig["lead"] == sig["lead"] \
                and sig["lead"] < sticky:
            eff_head = prev_eff
            tied = True
            stick_hits += 1
        dev_eff = sig["dev"].get(eff_head, float("nan"))
        mom_eff = sig["m"].get(eff_head, float("nan"))

        prev_state = state
        reason = ""
        # ---- 过热熔断 (优先级最高) ----
        if state == "ROT":
            if (dev_eff == dev_eff and dev_eff > enter_dev) or \
               (sig["lead"] == sig["lead"] and sig["lead"] > enter_lead):
                state = "DEF"
                def_days = 0
                reason = (f"过热(乖离{dev_eff:+.2f}%>{enter_dev:.0f}%"
                          if (dev_eff == dev_eff and dev_eff > enter_dev)
                          else f"过热(领先{sig['lead']:+.2f}pp>{enter_lead:.0f}pp") \
                         + " -> 切防守"
        elif state == "DEF":
            def_days += 1
            if (dev_eff == dev_eff and dev_eff < exit_dev) and \
               (sig["lead"] == sig["lead"] and sig["lead"] < exit_lead) and \
               def_days >= min_def_days:
                state = "ROT"
                def_days = 0
                reason = (f"降温(乖离{dev_eff:+.2f}%<{exit_dev:.0f}% 且 "
                          f"领先{sig['lead']:+.2f}pp<{exit_lead:.0f}pp) 且防守满 "
                          f"{min_def_days} 日 -> 切回轮动")

        # ---- 目标持仓 ----
        if state == "DEF":
            ve_ok = (not value_engine) or bool(ve_hold.loc[dates[i]])
            target = defensive if ve_ok else "CASH"
            if not ve_ok:
                reason = reason + " | 价值引擎未触发 -> 持币"
        elif dip_armed:
            # ---- 抄底监控 (轮动态 + 已空仓) ----
            head_dev = sig["dev"].get(eff_head, float("nan"))
            if not dip_hit and head_dev == head_dev and head_dev <= dip_thresh:
                dip_hit = True                      # 记录"已超跌"
            buyback = (dip_hit and head_dev == head_dev and head_dev > 0
                       and (not dual_confirm or mom_eff > 0))
            escaped = False
            if (allow_escape and not dip_hit and mom_eff == mom_eff
                    and mom_eff > 0 and head_dev == head_dev and head_dev > 0):
                escaped = True          # 没跌透市场就恢复了 -> 承认踏空, 回到轮动
            if buyback:
                dip_armed = dip_hit = False
                if dip_enter:
                    dip_spans.append([dip_enter, str(dates[i].date())])
                    dip_enter = None
                dip_hits += 1
                target = eff_head
                reason = (f"抄底触发(超跌{dip_thresh:.0f}%后收复MA20"
                          + ("且动横转正" if dual_confirm else "")
                          + f") -> 买回 {eff_head}")
            elif escaped:
                dip_armed = dip_hit = False
                if dip_enter:
                    dip_spans.append([dip_enter, str(dates[i].date())])
                    dip_enter = None
                target = eff_head
                reason = "抄底未触发(未跌透)但动量已恢复 -> 回到轮动持正"
            else:
                target = "CASH"
                reason = (f"抄底监控中(头名乖离{head_dev:+.2f}%"
                          + (", 已超跌待收复" if dip_hit else ", 未到超跌阈值")
                          + ") -> 继续持币")
        else:
            # ---- 正常轮动 + 新增: 头名能量脉冲(动横20)转负 → 空仓 ----
            if mom_eff == mom_eff and mom_eff < 0:
                dip_armed = True
                dip_hit = False
                dip_enter = str(dates[i].date())
                target = "CASH"
                reason = f"头名能量脉冲转负(动横{mom_eff:+.2f}%<0) -> 空仓启动抄底"
            else:
                target = eff_head
        reason = reason or "池内头名变更(粘性带外)"

        switched = (target != held)
        if switched:
            c = buy + sell
            nav *= (1.0 - c)
            trades.append(dict(date=str(dates[i].date()),
                               frm=held, to=target,
                               frm_name=("空仓" if held == "CASH" else NAMES.get(held, held)),
                               to_name=("持币" if target == "CASH" else NAMES.get(target, target)),
                               state=state, reason=reason, tie=bool(tied),
                               nav=round(nav, 6)))
        ret = 0.0
        if target in panel.columns:
            p0, p1 = panel[target].iloc[i], panel[target].iloc[i + 1]
            if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                ret = p1 / p0 - 1.0
        nav *= (1.0 + ret)
        held = target
        prev_eff = eff_head
        navs.append((dates[i + 1], nav))

    if dip_enter:
        dip_spans.append([dip_enter, None])
    nd = pd.Series({d.strftime("%Y-%m-%d"): v for d, v in navs})
    cash_days = int(sum(1 for _d, t in
                        [(x, None) for x in []] )) if False else None
    return dict(navs=nd, trades=trades, stick_hits=stick_hits,
                final=float(nd.iloc[-1]),
                dip_spans=dip_spans, dip_hits=dip_hits,
                dip_open=sum(1 for s in dip_spans if s[1] is None))


def value_engine_hold_local(panel, code, dates):
    return E.value_engine_hold(panel, code, dates)


# ------------------------------------------------------------------ main
def main():
    rot, deff, watch = E.read_pools()
    assert len(rot) == 9, rot
    panel, notes, lastdate = build_panel(
        os.path.join(HERE, "tdx_adj_long_2015_2026.csv"))
    dates = panel.index
    for n in notes:
        print("[data]", n)
    start_i = int(dates.searchsorted(pd.Timestamp(START), side="right")) - 1
    if start_i < 0:
        start_i = 0
    end_i = len(dates) - 1
    base_d = dates[start_i]
    print("=" * 96)
    print(f"抄底策略研究 · 合入 ROC 轮动")
    print(f"窗口 {base_d.date()} = 1.000 -> {dates[end_i].date()}   "
          f"收益日 {end_i-start_i} 个   单边成本 {COST_SIDE*100:.3f}%")
    print("=" * 96)

    versions = OrderedDict([
        ("V0", dict(label="V0 现行 ROC 轮动（无抄底）", dip=None)),
        ("V1", dict(label="V1 抄底-3%无逃生(纯字面)", dip=-3.0, dual=False, esc=False)),
        ("V2", dict(label="V2 抄底-3%双确认无逃生", dip=-3.0, dual=True, esc=False)),
        ("V3", dict(label="V3 抄底-3%+逃生(推荐语义)", dip=-3.0, dual=False, esc=True)),
        ("V3b", dict(label="V3b 抄底-5%+逃生(敏感性)", dip=-5.0, dual=False, esc=True)),
    ])
    runs = OrderedDict()
    print(f"{'版本':<44}{'净值':>9}{'收益':>11}{'年化':>9}{'最大回撤':>9}"
          f"{'夏普':>7}{'换仓':>6}{'大回撤':>7}{'抄底段':>7}{'空仓日':>7}")
    print("-" * 96)
    for k, v in versions.items():
        if v["dip"] is None:
            r = E.backtest(panel, dates, start_i, end_i, rot, DEF_CODE,
                           sticky=1.0, buy=COST_SIDE, sell=COST_SIDE,
                           value_engine=True, W=20, MA=20)
            spans, hits, opening = [], 0, 0
        else:
            r = backtest_dip(panel, dates, start_i, end_i, rot, DEF_CODE,
                             sticky=1.0, buy=COST_SIDE, sell=COST_SIDE,
                             value_engine=True, W=20, MA=20,
                             dip_thresh=v["dip"], dual_confirm=v["dual"],
                             allow_escape=v.get("esc", True))
            spans, hits, opening = r["dip_spans"], r["dip_hits"], r["dip_open"]
        st = E.stats(r["navs"])
        dds = dd_summary(r["navs"])
        cash_days = int(sum(1 for t in r["trades"] if t["to"] == "CASH"
                            and False) or 0)  # 占位
        # 空仓交易日 = held_log 不在长回测里; 用 trades 推: 每段持币天数另算
        runs[k] = dict(label=v["label"], stat=st, dd=dds,
                       final=float(r["navs"].iloc[-1]),
                       trades_n=len(r["trades"]),
                       yearly=E.yearly(r["navs"]),
                       mdd_win=list(mdd_window(r["navs"])),
                       dip_spans=spans, dip_hits=hits,
                       dip_open=opening,
                       nav={d: round(float(x), 6) for d, x in r["navs"].items()},
                       trades=r["trades"])
        print(f"{v['label']:<44}{r['navs'].iloc[-1]:>9.4f}{st['total']*100:>10.2f}%"
              f"{st['ann']*100:>8.2f}%{st['mdd']*100:>8.2f}%{st['sharpe']:>7.2f}"
              f"{len(r['trades']):>6}{dds['count']:>7}{len(spans):>7}", flush=True)

    # 空仓天数统计（各版本持有 CASH 的交易日数）
    for k, r in runs.items():
        tr = sorted(r["trades"], key=lambda t: t["date"])
        cash = 0
        prev_t = "CASH"
        di = {d: i for i, d in
              enumerate([str(x.date()) for x in dates[start_i:end_i + 1]])}
        # 从 nav 序列推: 每天持仓 = 上一笔 trade 的 to
        held_map = {}
        cur = "CASH"
        ti = 0
        tl = tr
        for d in sorted(r["nav"].keys()):
            while ti < len(tl) and tl[ti]["date"] <= d:
                cur = tl[ti]["to"]
                ti += 1
            if cur == "CASH":
                cash += 1
        r["cash_days"] = cash
        r["cash_pct"] = round(cash / len(r["nav"]) * 100, 1)
        dip_tags = sum(1 for t in tl if "能量脉冲" in (t.get("reason") or ""))
        r["dip_enters"] = dip_tags
        print(f"    {k}: 空仓 {cash} 日 ({r['cash_pct']}%)  抄底空仓启动 {dip_tags} 次")

    # ---- 基准 ----
    bench = OrderedDict()
    s = E.bench_curve(panel, dates, start_i, end_i, "000001")
    bench["sse"] = dict(label="上证指数 000001", final=float(s.iloc[-1]),
                        stat=E.stats(s), dd=dd_summary(s),
                        nav={d: round(float(v), 6) for d, v in s.items()})
    nq = nasdaq_from(base_d, "2015")
    if nq:
        sn = nq[1]
        sn = sn[sn.index >= base_d.strftime("%Y-%m-%d")]
        sn = sn / float(sn.iloc[0])
        bench["ndx"] = dict(label=nq[0], final=float(sn.iloc[-1]),
                            stat=E.stats(sn), dd=dd_summary(sn),
                            nav={d: round(float(v), 6) for d, v in sn.items()})
    print("-" * 96)
    for k, b in bench.items():
        print(f"{b['label']:<44}{b['final']:>9.4f}{b['stat']['total']*100:>10.2f}%"
              f"{b['stat']['ann']*100:>8.2f}%{b['stat']['mdd']*100:>8.2f}%")

    # ---- HTML ----
    hp = os.path.join(HERE, "out", f"抄底策略研究_{base_d.date()}_{dates[end_i].date()}.html")
    meta = dict(start=str(base_d.date()), end=str(dates[end_i].date()),
                days=int(end_i - start_i), cost=COST_SIDE, pool=rot,
                defensive=DEF_CODE, notes=notes, lastdate=lastdate)
    # ---- 动态结论 ----
    v0, v1, v3 = runs["V0"], runs["V1"], runs["V3"]
    d_ret = (v3["stat"]["total"] - v0["stat"]["total"]) * 100
    d_mdd = (v3["stat"]["mdd"] - v0["stat"]["mdd"]) * 100
    conclusion = (
        f"1. <b>纯字面实现是灾难性的负增量</b>：V1/V2（无逃生通道）空仓占比 "
        f"{v1['cash_pct']}%，收益只有 {v1['stat']['total']*100:+.2f}%"
        f"（V0 为 {v0['stat']['total']*100:+.2f}%，差 "
        f"{abs(v1['stat']['total']-v0['stat']['total'])*100:.0f}pp）——"
        f"「头名动横&lt;0 即空仓」在 9 只轮动池里触发过于频繁，"
        f"没有逃生通道时策略退化成长期持币等崩盘，直接否掉。<br>"
        f"2. <b>补上逃生通道（V3）后仍为负增量</b>：收益 {v3['stat']['total']*100:+.2f}%"
        f" vs V0 {v0['stat']['total']*100:+.2f}%（<b>差 {d_ret:+.0f}pp</b>），"
        f"最大回撤 {v3['stat']['mdd']*100:.2f}% vs {v0['stat']['mdd']*100:.2f}%"
        f"（{d_mdd:+.2f}pp，反而更深），大回撤 {v3['dd']['count']} 次 vs "
        f"{v0['dd']['count']} 次，换仓 {v3['trades_n']} 笔 vs {v0['trades_n']} 笔，"
        f"空仓 {v3['cash_days']} 日 vs {v0['cash_days']} 日。病根：动量崩溃→空仓→买回"
        f"这一来一回的择时损耗是系统性负贡献——买回点（超跌后收复MA20）常在反弹"
        f"走出一段之后，接的是反弹后半程；而 V0 的过热熔断只在「过热」这一种极端"
        f"状态下离场，其余时间让利润奔跑。<br>"
        f"3. <b>对基准</b>：含抄底模块的版本仍大幅跑赢上证（{bench['sse']['stat']['total']*100:+.2f}%），"
        f"但全面落后于 V0，也落后于纳指（{bench.get('ndx',{}).get('stat',{}).get('total',0)*100:+.2f}%）。<br>"
        f"4. <b>建议</b>：在拿到博主「能量脉冲」原始定义之前，<b>不建议把抄底模块合入现行策略</b>——"
        f"现行策略的过热熔断 + 价值引擎防守已覆盖风险侧；抄底是左侧择时，"
        f"与动量轮动的右侧逻辑内在冲突。脚本与全部参数已保留（<code>dip_buy_study.py</code>），"
        f"日后拿到原始定义可直接重验。"
    )
    render_html(dict(meta=meta, runs=runs, bench=bench, conclusion=conclusion), hp)
    out = dict(meta=meta, runs={k: {kk: vv for kk, vv in r.items()}
                               for k, r in runs.items()},
               bench=bench)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[json] {OUT_JSON}")
    print(f"[html] {hp}")


def render_html(D, path):
    m = D["meta"]
    runs, bench = D["runs"], D["bench"]
    BLUE, RED, GREEN, MUT = "#3d57e5", "#d4380d", "#0f9d58", "#6c717f"
    VCOL = {"V0": "#8a93a5", "V1": "#b9c2d8", "V2": "#9aa8f0",
            "V3": "#3d57e5", "V3b": "#7a3fd0"}

    rows = []
    for k, r in runs.items():
        st, dds, mw = r["stat"], r["dd"], r["mdd_win"]
        col = VCOL.get(k, BLUE)
        rows.append(
            f"<tr><td style='font-weight:700;color:{col}'>{_html.escape(r['label'])}</td>"
            f"<td class='num'><b>{r['final']:.4f}</b></td>"
            f"<td class='num' style='color:{RED if st['total']>0 else GREEN};font-weight:600'>"
            f"{st['total']*100:+.2f}%</td>"
            f"<td class='num'>{st['ann']*100:+.2f}%</td>"
            f"<td class='num' style='color:{RED};font-weight:600'>{st['mdd']*100:.2f}%</td>"
            f"<td class='num'>{mw[0]} → {mw[1]}</td>"
            f"<td class='num'>{st['sharpe']:.2f}</td>"
            f"<td class='num'>{st['sortino']:.2f}</td>"
            f"<td class='num'>{r['trades_n']}</td>"
            f"<td class='num'>{r['cash_days']} ({r['cash_pct']}%)</td>"
            f"<td class='num'>{r['dip_enters']}</td>"
            f"<td class='num'>{r['dip_hits']}</td>"
            f"<td class='num'>{dds['count']}</td></tr>")
    for k, b in bench.items():
        st = b["stat"]
        rows.append(
            f"<tr style='background:#f7f8fa'><td style='font-weight:600'>"
            f"{_html.escape(b['label'])}</td>"
            f"<td class='num'>{b['final']:.4f}</td>"
            f"<td class='num'>{st['total']*100:+.2f}%</td>"
            f"<td class='num'>{st['ann']*100:+.2f}%</td>"
            f"<td class='num' style='color:{RED}'>{st['mdd']*100:.2f}%</td>"
            f"<td class='num'>—</td>"
            f"<td class='num'>{st['sharpe']:.2f}</td>"
            f"<td class='num'>{st['sortino']:.2f}</td>"
            f"<td class='num'>—</td><td class='num'>—</td>"
            f"<td class='num'>—</td><td class='num'>—</td>"
            f"<td class='num'>{b['dd']['count']}</td></tr>")

    series = {k: r["nav"] for k, r in runs.items()}
    series["sse"] = bench["sse"]["nav"]
    if "ndx" in bench:
        series["ndx"] = bench["ndx"]["nav"]
    lines = [(k, VCOL.get(k, BLUE), ("V0 现行(基准)" if k == "V0" else k),
              (2.8 if k == "V3" else (1.7 if k != "V0" else 1.6)),
              ("5 3" if k == "V0" else "")) for k in runs]
    lines += [("sse", "#8a93a5", "上证指数", 1.4, "5 3"),
              ("ndx", RED, "纳斯达克综合", 1.4, "5 3")]
    curve = svg_curve(series, lines)
    bars = svg_mdd_bars([(r["label"].split(" ")[0], r["stat"]["mdd"] * 100,
                          VCOL.get(k, BLUE)) for k, r in runs.items()]
                        + [("上证", bench["sse"]["stat"]["mdd"] * 100, "#c3c9d6")]
                        + ([("纳指", bench["ndx"]["stat"]["mdd"] * 100, "#e8b7ad")]
                           if "ndx" in bench else []))

    # 抄底明细（V3 主实现）
    v1 = runs["V3"]
    span_rows = "".join(
        f"<tr><td class='mono'>{s[0]}</td>"
        f"<td class='mono'>{s[1] or f'<span style=\"color:{RED}\">未买回(持有中)</span>'}</td></tr>"
        for s in v1["dip_spans"])

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>抄底策略研究 · 合入 ROC 轮动（{m['start']} ~ {m['end']}）</title>
<style>
body{{margin:0;background:#eef0f3;color:#202020;font:15px/1.55 "PingFang SC",
"Microsoft YaHei","Segoe UI",system-ui,sans-serif}}
.wrap{{max-width:1200px;margin:0 auto;padding:22px 18px 60px}}
h2{{font-size:18px;margin:28px 0 12px;padding-bottom:7px;border-bottom:2px solid #e2e5ea}}
h3{{font-size:15px;margin:18px 0 8px;color:#3d57e5}}
.sub{{color:{MUT};font-size:12.5px;margin:6px 0 14px}}
.hero{{background:linear-gradient(120deg,#3d57e5,#4a64e8,#566de7);color:#fff;
border-radius:12px;padding:20px 24px;margin-bottom:20px}}
.hero h1{{font-size:23px;margin:0 0 4px;color:#fff}}
.hero .sub{{color:rgba(255,255,255,.85);font-size:13px}}
.tblwrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px;min-width:980px}}
th{{background:#f4f6fa;font-weight:600;text-align:left;padding:8px 9px;
border:1px solid #e2e5ea;white-space:nowrap}}
td{{padding:7px 9px;border:1px solid #e2e5ea}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
td.mono{{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;white-space:nowrap}}
tr:nth-child(even) td{{background:#fafbfc}}
.note{{background:#fff;border:1px solid #e2e5ea;border-left:4px solid #3d57e5;
border-radius:8px;padding:12px 16px;font-size:13px;margin-bottom:14px}}
.warn{{border-left-color:{RED};background:#fff7f5}}
.csvg{{overflow:hidden;border:1px solid #e2e5ea;border-radius:10px;background:#fff;
margin:10px 0 4px}}
.foot{{color:{MUT};font-size:12px;margin-top:26px;border-top:1px solid #e2e5ea;padding-top:12px}}
</style></head><body><div class="wrap">

<div class="hero"><h1>🎯 抄底策略研究 · 合入 ROC 轮动</h1>
<div class="sub">截图模块还原 + 可回测实现 + {m['start']} ~ {m['end']} 对比回测
｜ 主轮动池 9 只 ｜ 防守 512040 + 价值引擎 ｜ 单边手续费万1 ｜ 基点 {m['start']} 收盘 = 1.000
｜ 数据至 {m['lastdate']}</div></div>

<h2>① 截图模块解读与实现假设（先读这个）</h2>
<div class="note">
<b>截图原文：</b>「抄底监控 → 轮动持正，未触发」「需头名能量脉冲转负空仓后才启动抄底」<br>
<b>语义还原（与已验证的秋收计划1024体系对齐）：</b>正常态 = 轮动持正（持有头名）；
头名「能量脉冲」转负 → <b>清仓空仓</b>，同时启动抄底监控；空仓期间满足抄底条件 → 买回。<br>
<b>★ [MISSING]：</b>「能量脉冲」公式与抄底触发条件博主<b>未公开</b>（本地 131 个教学视频 +
指标 docx 检索无果，仅此一句截图）。以下为<b>语义推测实现</b>，不是博主原版：<br>
・能量脉冲 ≈ <b>头名动横20</b>（博主把动横20叫「动量惯性」，转负 = 动量消失，
与已还原的"头名动横20&gt;0 才持有"规则同源，这是最自洽的解读）；<br>
・抄底条件 ≈ 空仓期间头名乖离(P/MA20−1) 一度跌破超跌阈值（默认 −3%），
之后<b>收复 MA20（乖离回到 &gt;0）才买回</b>——"跌透了的反弹"才接，
与原版"动横转正即买回"的关键差异是：不跌透不买。<br>
本报告回答的是：<b>这个语义下的抄底模块，加进现行策略后有没有增量</b>。
若日后拿到博主的原始公式，需按其定义重验。
</div>

<h2>② 五个版本对比（{m['start']} ~ {m['end']}，{m['days']} 个交易日）</h2>
<div class="tblwrap"><table><thead><tr>
<th>版本</th><th>期末净值</th><th>区间收益</th><th>年化</th><th>最大回撤</th>
<th>最深回撤区间</th><th>夏普</th><th>索提诺</th><th>换仓</th><th>空仓日(占比)</th>
<th>抄底空仓启动</th><th>抄底买回完成</th><th>大回撤</th></tr></thead><tbody>
{"".join(rows)}
</tbody></table></div>
<div class="sub">「抄底空仓启动」= 头名动横20 转负触发空仓的次数；「抄底买回完成」=
其中走完"超跌→收复"全流程买回的次数；大回撤 = 回撤 &gt; 20% 的次数。</div>

<h2>③ 净值走势对比（对数刻度）</h2>
<div class="csvg">{curve}</div>
<div class="sub">V1 = 主实现（深蓝加粗）；V0 现行策略为灰色虚线基准。</div>

<h2>④ 最大回撤对比</h2>
<div class="csvg">{bars}</div>

<h2>⑤ V3 的抄底空仓区间明细</h2>
<div class="tblwrap"><table style="min-width:420px"><thead><tr><th>空仓开始</th>
<th>买回日</th></tr></thead><tbody>{span_rows}</tbody></table></div>
<div class="sub">空仓开始 = 头名动横20 转负当日收盘清仓；买回日 = 超跌后收复 MA20 当日收盘买入。</div>

<h2>⑥ 结论与局限</h2>
<div class="note" id="concl">
{D['conclusion']}
</div>
<div class="note warn">
<b>必须说清的局限：</b><br>
① <b>「能量脉冲」与抄底条件是推测实现</b> [假设]——博主未公开定义，本报告只验证该语义下
模块的增量；若与博主原版公式不同，结论不能直接套到博主策略上。<br>
② <b>超跌阈值 −2%/−3%/−5% 是外生参数</b>，非网格优选；若换成别的值结论可能变，
这正是该类规则脆弱性的体现。<br>
③ <b>幸存者偏差</b>：主轮动池为 2026 年选定，回溯 2020 年（当年只有 5 只可交易）。<br>
④ <b>成本口径</b>：单边万1（0.01%）。抄底模块会增加换仓次数，若按双边 0.2% 口径
其增量会被成本显著侵蚀。<br>
⑤ 窗口终点 {m['end']}（长历史 T0=2026-09-24 + 在线补齐至最近交易日）。
</div>

<div class="foot">本报告仅供研究参考，不构成个人投资建议。由 <code>dip_buy_study.py</code>
一次跑出，状态机与 <code>etf_backtest_long.py</code> 同源（仅叠加抄底模块）。</div>
</div></body></html>"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
