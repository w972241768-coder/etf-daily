"""近 N 次「系统建议换仓」的盈亏复盘（vs 同期上证指数）。

数据源 = 已验证的腾讯前复权长历史 CSV（与 etf_backtest_long 完全同一口径），
在本报告自身的规则（动横20 排名 + 粘性带 + 过热熔断 + 价值引擎）下复算，
取最近 N 次换仓，计算每次换仓后持有至下一次换仓（或至今）的净值收益，
并与同期上证指数（000001）对比。

⚠️ 这是「按系统建议操作」的净值复算（事后、规则确定），不是预测。
   所有数字均为模型输出，非实盘成交记录。
"""
import os
import sys
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = "D:/娱乐（音乐、视频）Q-骑牛/ETF轮动战法"
# 让 skill 副本与项目副本都能 import 到回测引擎
for _p in (_HERE, PROJECT_DIR):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import etf_backtest_long as bl
    _IMPORT_ERR = ""
except Exception as _e:                       # noqa: BLE001
    bl = None
    _IMPORT_ERR = f"{type(_e).__name__}: {_e}"

CANDIDATE_ADJ = [
    os.path.join(PROJECT_DIR, "tdx_adj_long_2019_2026.csv"),
    os.path.join(_HERE, "tdx_adj_long_2019_2026.csv"),
]
BENCH_CODE = "000001"          # 上证指数
BASE_DATE = "2019-12-31"       # 与长周期回测保持一致的基点


def find_adj_csv(explicit: Optional[str] = None):
    """在候选位置里挑数据最新的前复权 CSV。返回 (path, data_end_str) 或 (None, None)。"""
    order = []
    if explicit:
        order.append(explicit)
    env = os.environ.get("ETF_ADJ_CSV")
    if env:
        order.append(env)
    order.extend(CANDIDATE_ADJ)
    best, best_end = None, ""
    for p in order:
        if not p or not os.path.exists(p):
            continue
        try:
            end = pd.read_csv(p, usecols=["date"], dtype={"date": str})["date"].max()
        except Exception:                      # noqa: BLE001
            continue
        if end > best_end:
            best, best_end = p, end
    return (best, best_end) if best else (None, None)


def _extend_panel(panel: pd.DataFrame, live_df, pivot: Optional[str]):
    """把本次运行的前复权在线数据**接到长历史尾部**，让「至今」真的到锚定日。

    ⚠️ 为什么必须补：长历史 CSV（tdx_adj_long_2019_2026.csv）的 T0 = 2026-09-24，
    而日常报告的锚定日通常更新（例 2026-10-23）。不补的话「本次换仓截止目前收益」
    实际只算到一个月前，用户看到的「至今」是假的。
    ⚠️ 口径校验：两段在重叠末日必须完全同价（ratio≈1）。若 ratio 偏离 >0.3%，
    说明期间发生了分红/拆分，在线段未按新因子复权 ⇒ 不补，宁可少算也不引入假跳空。
    返回 (panel2, note)；不补时原样返回 panel 与说明文字。
    """
    if live_df is None or not len(live_df):
        return panel, "未提供在线数据，按长历史原样计算"
    if pivot:
        end_d = pd.Timestamp(pivot)
    else:
        end_d = pd.to_datetime(live_df["date"]).max()

    tail_cut = panel.index.max()
    if end_d <= tail_cut:
        return panel, f"长历史已覆盖锚定日（{tail_cut:%Y-%m-%d}）"

    live = live_df.copy()
    live["date"] = pd.to_datetime(live["date"])
    live = live[live["date"] > tail_cut]
    if not len(live):
        return panel, f"长历史已含 {tail_cut:%Y-%m-%d} 之后数据"

    frames, notes, bad = {}, [], []
    for code, g in live.groupby("code"):
        code = str(code).zfill(6)
        if code not in panel.columns:
            continue
        g = g.sort_values("date").drop_duplicates("date", keep="last")
        s = g.set_index("date")["close"].astype(float)
        s = s[s.index <= end_d]
        if not len(s):
            continue
        # 口径校验：重叠末日两边必须同价
        base = float(panel[code].loc[tail_cut])
        ov = s[s.index == tail_cut]
        if len(ov) and base > 0:
            ratio = float(ov.iloc[0]) / base
            if abs(ratio - 1.0) > 3e-3:
                bad.append(f"{code}(r={ratio:.4f})")
                continue
        s = s[s.index > tail_cut]
        if len(s):
            frames[code] = s

    if bad:
        notes.append("⚠ 以下标的在线/长历史口径不一致，未拼接（期间可能有分红拆分）："
                     + ", ".join(bad))
    if not frames:
        return panel, ("；".join(notes) if notes else "在线段无可拼接数据") + "（按长历史原样计算）"

    add = pd.DataFrame(frames).sort_index()
    out = pd.concat([panel, add]).sort_index()
    # 长历史原有列保持不变；新出现的列（如基准指数）补齐
    notes.append(f"已用在线前复权数据补 {tail_cut:%Y-%m-%d} 之后 "
                 f"{len(add)} 个交易日（至 {add.index.max():%Y-%m-%d}，"
                 f"{len(frames)} 只标的）")
    return out, "；".join(notes)


def recent_rotation_pnl(*, pool: List[str], defensive: Optional[str], n: int = 3,
                        adj_csv: Optional[str] = None, pivot: Optional[str] = None,
                        cost: float = 0.002, W: int = 20, MA: int = 20, sticky: float = 1.0,
                        enter_dev: float = 12.0, enter_lead: float = 15.0,
                        exit_dev: float = 9.0, exit_lead: float = 10.0,
                        min_def_days: int = 5, value_engine: bool = True,
                        bench: str = BENCH_CODE, live_df=None) -> Dict:
    """复算最近 N 次建议换仓的盈亏。

    pool / defensive / 规则参数由调用方（etf_rotation）传入，保证与当日报告同源。
    live_df（可选）= 本次运行的前复权在线长表（date/code/close），用于把长历史
    补到锚定日，否则「至今」会停在长历史 T0（详见 `_extend_panel`）。
    返回 dict：成功含 rows/cum/asof 等；失败含 error 字段（调用方渲染成提示即可）。
    """
    if bl is None:
        return dict(error=f"回测引擎导入失败（{_IMPORT_ERR}）")
    path, data_end = find_adj_csv(adj_csv)
    if not path:
        return dict(error="找不到前复权长历史 CSV（tdx_adj_long_2019_2026.csv），"
                          "请先运行 fetch_adj_long.py 下载")
    try:
        panel = bl.read_panel(path)
    except Exception as e:                     # noqa: BLE001
        return dict(error=f"长历史 CSV 读取失败（{type(e).__name__}: {e}）")
    hist_end = panel.index.max()
    panel, ext_note = _extend_panel(panel, live_df, pivot)
    dates = panel.index

    # 终点：不超过锚定日（报告可能用了更新的 TDX/实时数据，长历史未必同步）
    if pivot:
        end_i = int(dates.searchsorted(pd.Timestamp(pivot), side="right") - 1)
        if end_i < W:
            return dict(error=f"锚定日 {pivot} 早于长历史可用数据")
    else:
        end_i = len(dates) - 1

    # 起点：与长周期回测一致（基点不影响最后 N 笔的相对收益，仅保证状态机暖机）
    start_i = int(dates.searchsorted(pd.Timestamp(BASE_DATE), side="left"))
    start_i = max(start_i, 0)

    pool = [c for c in pool if c in panel.columns]
    if defensive is None or defensive not in panel.columns:
        return dict(error=f"防守位 {defensive} 不在长历史数据中")
    if len(pool) < 2:
        return dict(error="主选池标的不在长历史数据中")

    # 回测状态机「第 i 日收盘信号 -> i→i+1 持有收益」，天然吃不到锚定日当天的信号。
    # 在末尾补一根与锚定日同价的虚拟 K 线（收益恒为 0），让锚定日信号被求值并记入交易日志，
    # 这样「最近一次换仓」才能与报告的「今日目标」同源同口径。
    # 副作用仅为净值多记一天 0 收益 + 若当天换仓则扣一次成本，均为真实发生的现金流。
    pad = panel.iloc[[-1]].copy()
    pad.index = [panel.index[-1] + pd.Timedelta(days=1)]
    panel_ext = pd.concat([panel, pad])
    dates_ext = panel_ext.index
    end_i_ext = len(dates_ext) - 1            # 虚拟行；range(..., end_i_ext) 恰含锚定日

    try:
        r = bl.backtest(panel_ext, dates_ext, start_i, end_i_ext, pool, defensive,
                        sticky=sticky, enter_dev=enter_dev, enter_lead=enter_lead,
                        exit_dev=exit_dev, exit_lead=exit_lead,
                        min_def_days=min_def_days, buy=cost / 2.0, sell=cost / 2.0,
                        W=W, MA=MA, value_engine=value_engine)
    except Exception as e:                     # noqa: BLE001
        return dict(error=f"回测复算失败（{type(e).__name__}: {e}）")

    trades = r["trades"]
    if not trades:
        return dict(error="回测区间内没有换仓记录")
    navs = r["navs"]
    asof = dates[end_i].strftime("%Y-%m-%d")
    have_bench = bench in panel.columns

    def _px(code: str, d: str) -> Optional[float]:
        try:
            v = panel[code].asof(pd.Timestamp(d))
        except Exception:                      # noqa: BLE001
            return None
        return None if pd.isna(v) else float(v)

    def _bench_ret(d0: str, d1: str) -> Optional[float]:
        if not have_bench:
            return None
        p0, p1 = _px(bench, d0), _px(bench, d1)
        if not p0 or not p1 or p0 == 0:
            return None
        return p1 / p0 - 1.0

    sel = trades[-n:]
    # 若最近一次换仓就发生在锚定日当天（今天刚换仓，尚无持仓收益），则往前多取一次，
    # 保证表里「近 N 次」是有完整持仓时段的 N 次，今天那次单独标为「今日刚换仓」。
    if sel and sel[-1]["date"] == asof and len(trades) > n:
        sel = trades[-(n + 1):]
    idx0 = len(trades) - len(sel)
    rows = []
    # 口径：每次换仓的收益 = 「本换仓日已扣买入成本净值」→「下一换仓日已扣卖出成本净值」，
    # 与累计收益严格可乘（累计 = 各段 (1+r) 连乘），避免「分段之和 ≠ 累计」的假账。
    # 最后一段持仓未平仓，不扣卖出成本（表中标注「未平仓」）。
    for k, t in enumerate(sel):
        d0 = t["date"]
        if k < len(sel) - 1:
            d1 = sel[k + 1]["date"]
            nav_end = float(sel[k + 1]["nav"])             # 下一换仓日已扣成本净值
        else:
            d1 = asof
            nav_end = float(navs.iloc[-1])                 # 至今（持仓未平，未扣卖出成本）
        nav_start = float(t["nav"])                          # 本换仓日已扣成本净值
        same_day = (d1 == d0)                                # 今日刚换仓：尚无持仓时段
        if same_day:
            ret = br = alpha = None                          # 无收益可展示，渲染为 —
        else:
            ret = (nav_end / nav_start - 1.0) if nav_start > 0 and nav_end == nav_end else None
            br = _bench_ret(d0, d1)
            alpha = (ret - br if ret is not None and br is not None else None)
        # 持仓天数：按长历史里的**交易日根数**计（不是自然日），与「第 N 个交易日」同口径。
        # 只在 d0..d1 都在日历内时才有意义；今日刚换仓记 0。
        days = 0
        if not same_day:
            try:
                i0 = int(dates.searchsorted(pd.Timestamp(d0), side="left"))
                i1 = int(dates.searchsorted(pd.Timestamp(d1), side="left"))
                days = max(0, i1 - i0)
            except Exception:                              # noqa: BLE001
                days = 0
        rows.append(dict(
            idx=k + 1, date=d0, end_date=d1, days=days,
            frm=t["frm"], to=t["to"],
            frm_name=t.get("frm_name") or t["frm"],
            to_name=t.get("to_name") or t["to"],
            state=t.get("state", ""), reason=t.get("reason", ""),
            ret=ret, bench=br, alpha=alpha,
            closed=(k < len(sel) - 1), same_day=same_day,
        ))

    d_first = sel[0]["date"]
    nav_first = float(sel[0]["nav"])
    nav_last = float(navs.iloc[-1])
    cum_ret = (nav_last / nav_first - 1.0) if nav_first > 0 else None
    cum_bench = _bench_ret(d_first, asof)

    # ---- 每个标的「最近一次切换至今」的涨跌幅 ----
    # 用户 2026-10-23 需求：排名表新增一列，直接看这个标的现在的这一段行情赚没赚。
    # 口径与「② 近期换仓复盘」完全一致：
    #   · 该标的最近一次被买入（CASH/其他 → 它）的日期为起点；
    #   · 若至今仍在持有 → 终点 = asof，标记 holding=True；
    #   · 若已被换出 → 终点 = 换出日，标记 holding=False（这段已经结束）。
    # 从未被持有的标的 → 返回 None（表格渲染成 —）。
    # 涨跌幅用**标的自前复权收盘价**算（不含换仓成本），与「策略收益」区分开：
    #   策略收益含双边成本、且头名可能在防守期被换成 512040/CASH。
    since = {}
    for c in panel.columns:
        last_in = None
        for t in trades:
            if t.get("to") == c:
                last_in = t["date"]
        if last_in is None:
            continue
        # 找 last_in 之后的第一笔「换成别人」的交易
        last_out = None
        for t in trades:
            if t["date"] > last_in and t.get("frm") == c:
                last_out = t["date"]
                break
        end_s = last_out or asof
        p0, p1 = _px(c, last_in), _px(c, end_s)
        ret_c = (p1 / p0 - 1.0) if (p0 and p1) else None
        n_days = 0
        try:
            i0 = int(dates.searchsorted(pd.Timestamp(last_in), side="left"))
            i1 = int(dates.searchsorted(pd.Timestamp(end_s), side="left"))
            n_days = max(0, i1 - i0)
        except Exception:                                  # noqa: BLE001
            n_days = 0
        since[c] = dict(code=c, start=last_in, end=end_s, days=n_days,
                        ret=ret_c, holding=(last_out is None))

    # ---------------- D3 单笔止损「影子组合」（仅供切换参考，不参与主策略） ----------------
    try:
        d3 = _d3_shadow(panel_ext, dates_ext, start_i, end_i_ext, pool, defensive,
                        sticky=sticky, enter_dev=enter_dev, enter_lead=enter_lead,
                        exit_dev=exit_dev, exit_lead=exit_lead,
                        min_def_days=min_def_days, cost=cost, W=W, MA=MA,
                        value_engine=value_engine, asof=asof, base_navs=navs,
                        base_trades=trades)
    except Exception as e:                     # noqa: BLE001
        d3 = dict(error=f"{type(e).__name__}: {e}")

    # ---------------- P3 追高止盈「影子组合」（独立规则：头名ROC60进95分位→空仓20日） ----------------
    p3 = None
    if d3.get("ok"):
        try:
            p3 = _p3_shadow(panel_ext, dates_ext, start_i, end_i_ext, pool, defensive,
                            sticky=sticky, enter_dev=enter_dev, enter_lead=enter_lead,
                            exit_dev=exit_dev, exit_lead=exit_lead,
                            min_def_days=min_def_days, cost=cost, W=W, MA=MA,
                            value_engine=value_engine, asof=asof, base_navs=navs,
                            base_trades=trades, d3_navs=d3["navs"])
        except Exception as e:                 # noqa: BLE001
            p3 = dict(error=f"{type(e).__name__}: {e}")

    return dict(
        rows=rows,
        cum=dict(start=d_first, end=asof, ret=cum_ret, bench=cum_bench,
                 alpha=(cum_ret - cum_bench if cum_ret is not None and cum_bench is not None else None),
                 trades=len(trades) - idx0),
        curve=_build_curve(navs, panel, asof, bench, CURVE_DAYS, trades,
                           d3_navs=(d3.get("navs") if d3.get("ok") else None),
                           p3_navs=(p3.get("navs") if p3 and p3.get("ok") else None)),
        yearly=bl.yearly(navs),
        asof=asof, data_end=data_end, hist_end=hist_end,
        ext_note=ext_note, extended=bool((hist_end is not None)
                                        and (panel.index.max() > hist_end)),
        since=since, d3=d3, p3=p3,
        adj_csv=path,
        n=n, pool_size=len(pool), defensive=defensive, cost=cost,
        have_bench=have_bench, base=BASE_DATE,
        info=(f"前复权长历史（{data_end}）"
              + (f" → 在线补至 {asof}" if len(panel) > 0 and ext_note and "已用在线" in ext_note else "")
              + f" · 基点 {BASE_DATE} · 每次换仓双边 {cost*100:.1f}%"),
    )


D3_THR = 0.03             # 单笔亏损阈值
D3_DAYS = 5               # 空仓交易日数

# P3「追高止盈」：**独立策略** —— 头名 ROC60 进入自身滚动 95 分位（动量透支）→ 空仓 20 日
# ⚠️ 与 D3 无依赖关系：不挂 loss_cool 单独跑就是「仅 P3」；两者可叠加（叠加版另列一行）
P3_Q = 0.95
P3_DAYS = 20
P3_N = 60
P3_MINP = 250

# ★ 单一事实源校验：本文件的 D3/P3 参数必须与引擎的 LIVE_COMPOSITE 完全一致。
#   2026-10-25 起 D3+P3 是**生产默认口径**，两处参数若漂移会让日报卡片与回测结论对不上。
if bl is not None:
    assert (D3_THR, D3_DAYS, P3_Q, P3_DAYS, P3_N, P3_MINP) == (
        bl.LIVE_COMPOSITE["loss_cool"], bl.LIVE_COMPOSITE["loss_days"],
        bl.LIVE_COMPOSITE["roc_q"], bl.LIVE_COMPOSITE["roc_days"],
        bl.LIVE_COMPOSITE["roc_n"], bl.LIVE_COMPOSITE["roc_min_periods"]
    ), "D3/P3 参数与 etf_backtest_long.LIVE_COMPOSITE 不一致，请只改引擎那处"


def _d3_shadow(panel, dates, start_i, end_i, pool, defensive, *, sticky, enter_dev,
               enter_lead, exit_dev, exit_lead, min_def_days, cost, W, MA,
               value_engine, asof, base_navs, base_trades) -> Dict:
    """跑一遍「亏损≥3% → 空仓 5 日」的影子回测，回答三个问题：

    ① D3 现在要不要空仓？② 若切过来，今天持仓该是什么？③ 同窗口下 D3 比现方案好多少？
    与节内主表同起点、同终点、同成本、同参数，唯一差别是 loss_cool/loss_days。
    """
    r = bl.backtest(panel, dates, start_i, end_i, pool, defensive,
                    sticky=sticky, enter_dev=enter_dev, enter_lead=enter_lead,
                    exit_dev=exit_dev, exit_lead=exit_lead, min_def_days=min_def_days,
                    buy=cost / 2.0, sell=cost / 2.0, W=W, MA=MA,
                    value_engine=value_engine, loss_cool=D3_THR, loss_days=D3_DAYS)
    navs = r["navs"]
    st = bl.stats(navs)
    sb = bl.stats(base_navs)

    held_map = {pd.Timestamp(d).strftime("%Y-%m-%d"): t for d, t in r["held_log"]}

    def _hold_at(trs, day):
        """某日收盘后的持仓 = 该日及之前最后一笔换仓的目标；无换仓则为空仓。"""
        cur = "CASH"
        for t in trs:
            if t["date"] <= day:
                cur = t["to"]
            else:
                break
        return cur

    hold = held_map.get(asof)
    hold = _hold_at(r["trades"], asof) if hold is None else hold
    base_hold = _hold_at(base_trades, asof)

    cools = r.get("cools") or []
    active = (hold == "CASH")
    last = cools[-1] if cools else None
    remain = None
    back = None
    if active and last is not None:
        try:
            i_asof = int(dates.searchsorted(pd.Timestamp(asof), side="left"))
            i_back = int(dates.searchsorted(pd.Timestamp(last["until"]), side="left"))
            remain = max(0, i_back - i_asof)
            back = last["until"]
        except Exception:                                  # noqa: BLE001
            remain = None
    cool_days = int(sum(c["days"] for c in cools))
    n_days_all = max(1, len(navs) - 1)

    return dict(
        ok=True, thr=D3_THR, days=D3_DAYS,
        final=float(navs.iloc[-1]),
        total=st["total"] * 100, mdd=st["mdd"] * 100,
        sharpe=st["sharpe"], calmar=st["calmar"],
        trades_n=len(r["trades"]), cool_n=len(cools), cool_days=cool_days,
        cash_pct=cool_days / n_days_all * 100,
        hold=hold, base_hold=base_hold,
        hold_name=("持币" if hold == "CASH" else bl.NAMES.get(hold, hold)),
        base_hold_name=("持币" if base_hold == "CASH" else bl.NAMES.get(base_hold, base_hold)),
        same=(hold == base_hold), active=bool(active), remain=remain, back=back,
        last=[dict(date=c["date"], name=c["name"], ret=c["ret"],
                   until=c["until"], days=c["days"]) for c in cools[-3:]][::-1],
        base=dict(final=float(base_navs.iloc[-1]), total=sb["total"] * 100,
                  mdd=sb["mdd"] * 100, sharpe=sb["sharpe"], calmar=sb["calmar"]),
        asof=asof,
        navs=navs,          # 供「近 5 年净值曲线」直接画 D3 曲线（同一份回测，不重算）
        yearly=bl.yearly(navs),   # 供「年度表现」双柱：现用策略 vs D3 逐年对比
    )


def _p3_shadow(panel, dates, start_i, end_i, pool, defensive, *, sticky, enter_dev,
               enter_lead, exit_dev, exit_lead, min_def_days, cost, W, MA,
               value_engine, asof, base_navs, base_trades, d3_navs) -> Dict:
    """「D3 + P3 止盈」叠加组合的影子回测（2026-10-25 用户拍板的现行组合）。

    主口径 = 两条**互相独立**的规则叠加（目标持仓优先级 D3 > P3）：
      · D3 单笔止损：某笔在换出时相对建仓价亏 ≥3% → 空仓 5 日（看盈亏）
      · P3 追高止盈：头名 ROC60 进自身滚动 95 分位 → 空仓 20 日（不看盈亏）
    另跑一遍「仅 P3（不挂 D3）」作参照，回答「单独用 P3 值多少」。
    实测（单边万一）：主窗 现用 7.0855/−21.57% → D3 9.4301/−20.07%
      → **D3+P3 11.0196/−15.12%/C2.85**；仅 P3 9.0529/−18.19%/C2.14。
    回答三个问题：① 组合现在是否空仓？② 今天该持什么？③ 比现用/D3 好多少？
    """
    kw = dict(sticky=sticky, enter_dev=enter_dev, enter_lead=enter_lead,
              exit_dev=exit_dev, exit_lead=exit_lead, min_def_days=min_def_days,
              buy=cost / 2.0, sell=cost / 2.0, W=W, MA=MA,
              value_engine=value_engine)
    # 主口径：D3 + P3 叠加
    r = bl.backtest(panel, dates, start_i, end_i, pool, defensive,
                    loss_cool=D3_THR, loss_days=D3_DAYS,
                    roc_q=P3_Q, roc_days=P3_DAYS, roc_n=P3_N,
                    roc_min_periods=P3_MINP, **kw)
    navs = r["navs"]
    st = bl.stats(navs)
    # 参照：仅 P3（不挂 D3）
    ri = bl.backtest(panel, dates, start_i, end_i, pool, defensive,
                     roc_q=P3_Q, roc_days=P3_DAYS, roc_n=P3_N,
                     roc_min_periods=P3_MINP, **kw)
    si = bl.stats(ri["navs"])
    sb = bl.stats(base_navs)         # 现用策略（基线）
    sd = bl.stats(d3_navs)           # D3 单独

    def _hold_at(trs, day):
        cur = "CASH"
        for t in trs:
            if t["date"] <= day:
                cur = t["to"]
            else:
                break
        return cur

    hold = _hold_at(r["trades"], asof)
    base_hold = _hold_at(base_trades, asof) if base_trades is not None else None
    p3_hold = _hold_at(ri["trades"], asof)
    rc = r.get("roc_cools") or []
    rci = ri.get("roc_cools") or []
    last = rc[-1] if rc else None
    active = (hold == "CASH")
    remain, back = None, None
    if active and last is not None:
        try:
            i_asof = int(dates.searchsorted(pd.Timestamp(asof), side="left"))
            i_back = int(dates.searchsorted(pd.Timestamp(last["until"]), side="left"))
            remain = max(0, i_back - i_asof)
            back = last["until"]
        except Exception:                              # noqa: BLE001
            remain = None
    cool_days = int(sum(c["days"] for c in rc))
    n_days_all = max(1, len(navs) - 1)

    def _pack(s, nv):
        return dict(final=float(nv.iloc[-1]), total=s["total"] * 100,
                    mdd=s["mdd"] * 100, sharpe=s["sharpe"], calmar=s["calmar"])

    def _hname(h):
        return "持币" if h == "CASH" else bl.NAMES.get(h, h)

    return dict(
        ok=True, q=P3_Q, days=P3_DAYS, n=P3_N,
        final=float(navs.iloc[-1]),
        total=st["total"] * 100, mdd=st["mdd"] * 100,
        sharpe=st["sharpe"], calmar=st["calmar"],
        trades_n=len(r["trades"]), roc_n=len(rc), roc_days_total=cool_days,
        cash_pct=cool_days / n_days_all * 100,
        hold=hold, base_hold=base_hold, p3_hold=p3_hold,
        hold_name=_hname(hold), base_hold_name=_hname(base_hold), 
        p3_hold_name=_hname(p3_hold),
        same_base=(hold == base_hold), active=bool(active),
        remain=remain, back=back,
        last=[dict(date=c["date"],
                   head=c.get("head"), head_name=c.get("head_name"),
                   roc=c.get("roc"), pct=c.get("pct"), m20=c.get("m20"),
                   code=c.get("code"), name=c.get("name"),
                   held_m20=c.get("held_m20"),
                   until=c["until"], days=c["days"]) for c in rc[-3:]][::-1],
        # 三列对比：现用策略 / D3+P3（主口径） / 参照 D3 / 参照 仅P3
        base=_pack(sb, base_navs), d3=_pack(sd, d3_navs),
        p3only=dict(_pack(si, ri["navs"]), roc_n=len(rci),
                    trades_n=len(ri["trades"]),
                    hold=p3_hold, hold_name=_hname(p3_hold),
                    roc_days_total=int(sum(c["days"] for c in rci))),
        asof=asof,
        navs=navs,          # 供「近 5 年净值曲线」直接画 D3+P3 曲线（同一次回测，不重算）
        yearly=bl.yearly(navs),   # 供「年度表现」三柱：现用策略 / D3 / D3+P3 逐年对比
    )


CURVE_DAYS = 1826         # 近 5 年(日历日)净值曲线窗口


def _build_curve(navs, panel, asof: str, bench: str, days: int = CURVE_DAYS,
                 trades=None, d3_navs=None, p3_navs=None):
    """近 ~5 年净值曲线数据: 策略净值 vs 上证指数 vs 纳斯达克综合指数。

    策略净值与上证来自回测所用的同一份前复权长历史(口径与第 ② 节完全一致)；
    纳斯达克综合指数在线取(Yahoo ^IXIC, 免费无密钥), 取不到则只画前两条。
    全部序列以窗口首个交易日 = 1.0 归一化, 便于直观比较相对涨跌。
    trades(可选) -> switches: 窗口内的【状态机切换】[{date, state}]——只含
      正向→防守("切防守")与防守→正向("切回轮动")两种, 正常轮动换标的不标,
      state='ROT'=切回正向(红点) / 'DEF'=切防守(绿点), 供曲线标注。
    d3_navs / p3_navs(可选) -> D3 / P3 影子组合净值，与主策略同窗口叠加显示。
    返回 dict(失败返回 None): dates / series / rets / switches / info
    """
    end_d = pd.Timestamp(asof)
    start_d = end_d - pd.Timedelta(days=days - 1)

    # 策略净值(Series, 索引为日期字符串)
    try:
        nv = navs.copy()
        nv.index = pd.to_datetime(nv.index)
        nv = nv[(nv.index >= start_d) & (nv.index <= end_d)].dropna()
    except Exception:                                # noqa: BLE001
        return None
    if len(nv) < 8:
        return None

    # 上证指数(长历史里)
    sh = None
    if bench in panel.columns:
        s = panel[bench]
        s = s[(s.index >= start_d) & (s.index <= end_d)].dropna()
        if len(s) >= 8:
            sh = s

    # 纳斯达克综合指数(在线)
    ndx = None
    ndx_note = ""
    try:
        import online_data as od
        beg_s = start_d.strftime("%Y-%m-%d")
        end_s = (end_d + pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        raw = od.fetch_us_index(od.NDX_SYMBOL, beg_s, end_s)
        if raw is not None and len(raw):
            raw = raw.set_index("date")["close"].astype(float).sort_index()
            raw = raw[(raw.index >= start_d) & (raw.index <= end_d + pd.Timedelta(days=3))]
            if len(raw) >= 5:
                ndx = raw
    except Exception as e:                            # noqa: BLE001
        ndx_note = f"纳斯达克取数失败({type(e).__name__}), 曲线只含策略与上证"

    # 对齐到策略净值的日子(上证与策略同源, 日期天然一致; 纳指按公历日对齐, 缺失日前填)
    dates = nv.index
    out = dict(nv=nv.values.astype(float))

    # D3 单笔止损影子组合（与策略同起点、同成本，只多「亏≥3% 空仓5日」一条）
    if d3_navs is not None:
        try:
            dz = d3_navs.copy()
            dz.index = pd.to_datetime(dz.index)
            dz = dz.reindex(dates).ffill().bfill()
            if int(dz.notna().sum()) >= 8:
                out["d3"] = dz.values.astype(float)
        except Exception:                          # noqa: BLE001
            pass

    # P3 追高止盈影子组合（D3 + 头名ROC60进95分位空仓20日）
    if p3_navs is not None:
        try:
            pz = p3_navs.copy()
            pz.index = pd.to_datetime(pz.index)
            pz = pz.reindex(dates).ffill().bfill()
            if int(pz.notna().sum()) >= 8:
                out["p3"] = pz.values.astype(float)
        except Exception:                          # noqa: BLE001
            pass

    if sh is not None:
        out["sh"] = sh.reindex(dates).ffill().values.astype(float)
    if ndx is not None:
        out["ndx"] = ndx.reindex(dates).ffill().values.astype(float)
        # ffill 后首部仍可能为空(美股休市日早于窗口起点), 用首个非空值回填
        arr = out["ndx"]
        if np.isnan(arr[0]):
            first_ok = int(np.argmax(~np.isnan(arr)))
            if not np.isnan(arr[first_ok]):
                arr[:first_ok] = arr[first_ok]

    def _rebase(a):
        a = np.asarray(a, dtype=float)
        ok = ~np.isnan(a)
        if not ok.any():
            return None, None
        v0 = a[np.argmax(ok)]
        return a / v0, v0

    series, rets = {}, {}
    for k, a in out.items():
        rb, _v0 = _rebase(a)
        if rb is None:
            continue
        valid = rb[~np.isnan(rb)]
        series[k] = rb
        rets[k] = float(valid[-1] / valid[0] - 1.0) if len(valid) > 1 else 0.0

    if "nv" not in series:
        return None
    # 窗口内的状态切换事件（曲线上标注切换点）。
    # ★ 2026-10-18 用户要求精简：只标「正向→防守」与「防守→正向」两种状态机切换，
    #   正向区间内的正常换标的（ROT→ROT）与防守期内价值引擎翻转不再标注，
    #   否则 5 年窗口 160+ 个圆点把曲线糊满。判定依据 = 换仓 reason 文案
    #   （"-> 切防守" / "-> 切回轮动" 由 etf_backtest_long.backtest 生成）。
    switches = []
    if trades:
        for t in trades:
            d = t.get("date")
            rs = t.get("reason") or ""
            if not d:
                continue
            if "切防守" in rs:
                st = "DEF"
            elif "切回轮动" in rs:
                st = "ROT"
            else:
                continue
            if start_d <= pd.Timestamp(d) <= end_d:
                switches.append(dict(date=d, state=st))
    info = (f"窗口 {dates[0]:%Y-%m-%d} ~ {dates[-1]:%Y-%m-%d}（{len(dates)} 个交易日, 以首日 = 1.0 归一化）"
            + ("、含 D3 单笔止损" if "d3" in series else "")
            + ("、P3 追高止盈" if "p3" in series else "")
            + ("" if ndx is not None else f" ｜ ⚠ {ndx_note}" if ndx_note else " ｜ 纳斯达克取数为空, 只含策略与上证"))
    return dict(start=dates[0].strftime("%Y-%m-%d"), end=dates[-1].strftime("%Y-%m-%d"),
                dates=[d.strftime("%Y-%m-%d") for d in dates],
                series=series, rets=rets, switches=switches,
                ndx_ok=("ndx" in series), sh_ok=("sh" in series),
                info=info)
