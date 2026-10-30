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
                        bench: str = BENCH_CODE, live_df=None,
                        mine_since: Optional[str] = None,
                        mine_capital: Optional[float] = None) -> Dict:
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

    def _mk_since(trs):
        """按给定交易序列，算每个标的「最近一次被买入 → 至今 / 换出日」的涨跌。

        ★ 2026-10-29 修复（用户审阅指出）：排名表「切换至今」必须与**主口径**
          （D3+P3+抄底）一致。原来这里固定用基础轮动的 trades，导致抄底换持后
          排名表仍显示「513100 持仓 18 日」，而 ② 节已写「10-26 已换出 → 588170」，
          两处数字直接打架。故改成传参，基础口径与主口径各算一份。
        """
        out = {}
        for c in panel.columns:
            last_in = None
            for t in trs:
                if t.get("to") == c:
                    last_in = t["date"]
            if last_in is None:
                continue
            # 找 last_in 之后的第一笔「换成别人」的交易
            last_out = None
            for t in trs:
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
            except Exception:                              # noqa: BLE001
                n_days = 0
            out[c] = dict(code=c, start=last_in, end=end_s, days=n_days,
                          ret=ret_c, holding=(last_out is None))
        return out

    since = _mk_since(trades)

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
                            base_trades=trades, d3_navs=d3["navs"], n_rows=n)
        except Exception as e:                 # noqa: BLE001
            p3 = dict(error=f"{type(e).__name__}: {e}")

    # 主口径（D3+P3+抄底）的「切换至今」——排名表优先用这一份，保证与 ② 节同口径
    since_live = None
    if p3 and p3.get("ok") and p3.get("trades"):
        try:
            since_live = _mk_since(p3["trades"])
        except Exception:                      # noqa: BLE001
            since_live = None

    # ---- 「我的实盘跟踪」（2026-10-26 用户需求）----
    # 从用户正式启用日（my_since.json 的 since）起，按**当前生产口径**（D3+P3+抄底）
    # 理论复算。没配置就不显示。⚠️ 是「若每次严格按信号以收盘价成交」的理论值，
    # 不含滑点与手动干预，与真实成交可能有差。
    mine = None
    if mine_since:
        try:
            _si = max(0, int(dates_ext.searchsorted(pd.Timestamp(mine_since), side="left")))
            if end_i_ext - _si >= 2:
                _kwm = dict(buy=cost / 2.0, sell=cost / 2.0, W=W, MA=MA,
                            value_engine=value_engine, sticky=sticky,
                            enter_dev=enter_dev, enter_lead=enter_lead,
                            exit_dev=exit_dev, exit_lead=exit_lead,
                            min_def_days=min_def_days)
                _kwm.update(dict(bl.LIVE_COMPOSITE))
                _rm = bl.backtest(panel_ext, dates_ext, _si, end_i_ext,
                                  pool, defensive, **_kwm)
                _nv = _rm["navs"]
                _sm = bl.stats(_nv)
                _cur = "CASH"
                for _t in _rm["trades"]:
                    if _t["date"] <= asof:
                        _cur = _t["to"]
                mine = dict(
                    since=str(dates_ext[_si].date()), asof=asof,
                    days_td=len(_nv), final=float(_nv.iloc[-1]),
                    total=_sm["total"] * 100, ann=_sm["ann"] * 100,
                    mdd=_sm["mdd"] * 100, sharpe=_sm["sharpe"],
                    calmar=_sm["calmar"], trades_n=len(_rm["trades"]),
                    weak_n=len(_rm.get("weak_events") or []),
                    hold=_cur,
                    hold_name=("持币" if _cur == "CASH" else bl.NAMES.get(_cur, _cur)),
                    capital=mine_capital,
                )
            else:
                mine = dict(error=f"起始日 {mine_since} 距最新交易日不足 3 个交易日")
        except Exception as e:                    # noqa: BLE001
            mine = dict(error=f"{type(e).__name__}: {e}")

    # ---- 净值曲线的「持仓色带」（2026-10-29 用户需求，参考博主的持仓条）----
    # 用**主口径**（D3+P3+抄底）的换仓序列铺成逐日持仓，供曲线下方色带使用。
    _cv = _build_curve(navs, panel, asof, bench, CURVE_DAYS, trades,
                       d3_navs=(d3.get("navs") if d3.get("ok") else None),
                       p3_navs=(p3.get("navs") if p3 and p3.get("ok") else None))
    if _cv:
        try:
            _cv["holds"] = _hold_band(
                _cv["dates"],
                (p3.get("trades") if (p3 and p3.get("ok")) else trades))
            _cv["hold_names"] = {c: bl.NAMES.get(c, c)
                                 for c in set(_cv["holds"]) if c and c != "CASH"}
        except Exception:                          # noqa: BLE001
            pass

    return dict(
        rows=rows,
        cum=dict(start=d_first, end=asof, ret=cum_ret, bench=cum_bench,
                 alpha=(cum_ret - cum_bench if cum_ret is not None and cum_bench is not None else None),
                 trades=len(trades) - idx0),
        curve=_cv,
        yearly=bl.yearly(navs),
        asof=asof, data_end=data_end, hist_end=hist_end,
        ext_note=ext_note, extended=bool((hist_end is not None)
                                        and (panel.index.max() > hist_end)),
        since=since, since_live=since_live, d3=d3, p3=p3, mine=mine,
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


def _win_stats(r, navs) -> Dict:
    """四套胜率与盈亏比（2026-10-30 用户要求加进日报回测卡）。

    口径与 make_compare_report.perf_stats 完全一致，避免两处数字打架：
      · 交易胜率 = 盈利笔数 / 总笔数（每笔 = 一次换仓到下一次换仓，含持币段）
      · 盈亏比   = 盈利笔收益总和 / |亏损笔收益总和|
      · 月/年/日胜率 = 对应周期收益 > 0 的占比
    ⚠️ 每笔收益由 trades[i].nav → trades[i+1].nav 推出（nav 已扣换仓成本）。
    """
    tr = (r or {}).get("trades") or []
    rets = []
    for i in range(len(tr) - 1):
        try:
            a, b = float(tr[i]["nav"]), float(tr[i + 1]["nav"])
        except Exception:                        # noqa: BLE001
            continue
        if a:
            rets.append((b / a - 1.0) * 100)
    rets = np.array(rets) if rets else np.array([0.0])
    _n = navs.copy()
    if not isinstance(_n.index, pd.DatetimeIndex):
        try:
            _n.index = pd.to_datetime(_n.index)
        except Exception:                        # noqa: BLE001
            return {}
    mret = _n.resample("ME").last().pct_change().dropna() * 100
    yret = _n.resample("YE").last().pct_change().dropna() * 100
    dret = _n.pct_change().dropna() * 100
    npos = int((rets > 0).sum())
    nneg = int((rets < 0).sum())
    gain = float(rets[rets > 0].sum())
    loss = abs(float(rets[rets < 0].sum()))
    return dict(
        n=int(len(rets)), n_win=npos, n_loss=nneg,
        win=float((rets > 0).mean() * 100),
        pf=(gain / loss if loss > 0 else float("nan")),
        avg_gain=(gain / npos if npos else 0.0),
        avg_loss=(loss / nneg if nneg else 0.0),
        m_win=float((mret > 0).mean() * 100) if len(mret) else 0.0,
        y_win=float((yret > 0).mean() * 100) if len(yret) else 0.0,
        d_win=float((dret > 0).mean() * 100) if len(dret) else 0.0,
    )


def _p3_shadow(panel, dates, start_i, end_i, pool, defensive, *, sticky, enter_dev,
               enter_lead, exit_dev, exit_lead, min_def_days, cost, W, MA,
               value_engine, asof, base_navs, base_trades, d3_navs,
               n_rows: int = 3) -> Dict:
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
    # 主口径：D3 + P3 叠加 + 抄底模块（抄底参数同样来自 LIVE_COMPOSITE，唯一事实源）
    r = bl.backtest(panel, dates, start_i, end_i, pool, defensive,
                    loss_cool=D3_THR, loss_days=D3_DAYS,
                    roc_q=P3_Q, roc_days=P3_DAYS, roc_n=P3_N,
                    roc_min_periods=P3_MINP,
                    weak_mode=bl.LIVE_COMPOSITE.get("weak_mode"),
                    weak_rank=bl.LIVE_COMPOSITE.get("weak_rank", 1),
                    weak_delay=bl.LIVE_COMPOSITE.get("weak_delay", 0),
                    weak_cd=bl.LIVE_COMPOSITE.get("weak_cd", 0),
                    weak_min_pool=bl.LIVE_COMPOSITE.get("weak_min_pool", 0),
                    # 抄底离场（2026-10-28 E3 接入）：锁定期 + 满N日/回MA20
                    weak_exit_ma20=bl.LIVE_COMPOSITE.get("weak_exit_ma20", False),
                    weak_exit_days=bl.LIVE_COMPOSITE.get("weak_exit_days", 0),
                    **kw)
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
    # ---- 空仓原因判定（2026-10-27 修复）：hold==CASH 时区分是 D3 还是 P3 ----
    # 之前 d3state 一律显示 P3 的回场日，D3 触发的空仓会被错标成 P3（用户今天撞上）。
    cash_src = cash_reason = cash_until = cash_ret = cash_note = None
    if hold == "CASH":
        # ★ 虚拟行检测：panel_ext 末尾补了一根「锚定日+1 自然日」的同价假K线
        #   （见 recent_rotation_pnl 的 pad），D3/P3 的 until 若被 min() 截到这根
        #   假K线上，显示出来的回场日就是假的（如 10-28）。此时改用文字近似。
        _pad_date = str(dates[-1].date())
        _cl = r.get("cools") or []
        if _cl and str(_cl[-1].get("until") or "") >= str(asof):
            cash_src = "D3"
            cash_reason = (f"{_cl[-1].get('name','')}（{_cl[-1].get('code','')}）"
                           f"换出时亏 {_cl[-1].get('ret',0):+.2f}% ≥ 3%")
            cash_ret = _cl[-1].get("ret")
            if str(_cl[-1].get("until") or "") == _pad_date:
                cash_note = f"约 {D3_DAYS} 个交易日后（以每日报告更新为准）"
            else:
                cash_until = _cl[-1].get("until")
        else:
            _rc = r.get("roc_cools") or []
            if _rc and str(_rc[-1].get("until") or "") >= str(asof):
                cash_src = "P3"
                cash_reason = "头名 ROC60 进自身历史 95 分位（动量透支）"
                if str(_rc[-1].get("until") or "") == _pad_date:
                    cash_note = f"约 {P3_DAYS} 个交易日后（以每日报告更新为准）"
                else:
                    cash_until = _rc[-1].get("until")
    # ---- 空仓已持续几个交易日（2026-10-28 用户需求：空仓期要显示已空仓几天）----
    cash_days = None
    if hold == "CASH":
        for _t in reversed(r["trades"]):
            if _t.get("to") == "CASH":
                try:
                    _i0 = int(dates.searchsorted(pd.Timestamp(_t["date"]), side="left"))
                    _i1 = int(dates.searchsorted(pd.Timestamp(asof), side="left"))
                    cash_days = max(0, _i1 - _i0)
                except Exception:                      # noqa: BLE001
                    cash_days = None
                break

    # ---- 抄底锁定状态（2026-10-28 用户需求：抄底也要像 D3 一样显示「现在处在什么状态」）----
    # 引擎返回 weak_hold / weak_hold_i / weak_entry_px；这里换算成人类可读的进度：
    #   已持有几天、距「满 N 日」还差几天、当前乖离多少（回 MA20 上方即离场）、浮盈亏。
    weak_state = None
    _wh = r.get("weak_hold")
    if _wh and _wh != "CASH" and _wh in panel.columns:
        try:
            _i0 = int(r.get("weak_hold_i") or 0)
            # ⚠️ end_i 可能指向 panel 末尾补的「虚拟行」（锚定日+1 自然日、同价），
            #   直接用会多算 1 天。按 asof 精确落位。
            _i1 = int(dates.searchsorted(pd.Timestamp(asof), side="right")) - 1
            _i1 = max(_i0, min(_i1, len(dates) - 1))
            _days = max(0, _i1 - _i0)
            _px = panel[_wh].iloc[_i1]
            _px = float(_px) if not pd.isna(_px) else None
            _ep = r.get("weak_entry_px")
            # 乖离 = 收盘 / MA20 − 1（与引擎 sig["dev"] 同口径）
            _win = panel[_wh].iloc[max(0, _i1 - MA + 1):_i1 + 1]
            _dev = None
            if len(_win) == MA and not _win.isna().any() and _px:
                _dev = (_px / float(_win.mean()) - 1.0) * 100
            _lim = int(bl.LIVE_COMPOSITE.get("weak_exit_days", 0) or 0)
            weak_state = dict(
                code=_wh, name=bl.NAMES.get(_wh, _wh),
                entry_date=str(dates[_i0].date()),
                days=_days,
                limit=_lim,
                remain=(max(0, _lim - _days) if _lim else None),
                dev=(round(_dev, 2) if _dev is not None else None),
                px=_px, entry_px=(float(_ep) if _ep else None),
                pnl=(round((_px / float(_ep) - 1.0) * 100, 2)
                     if (_px and _ep) else None),
                use_ma20=bool(bl.LIVE_COMPOSITE.get("weak_exit_ma20")))
        except Exception:                          # noqa: BLE001
            weak_state = None

    # ---- 主口径的「近 N 次换仓复盘」（2026-10-28 用户需求）----
    # 原来这张表用的是**基础策略**的换仓，所以抄底买入（10-23）和 D3 止损空仓（10-27）
    # 都看不到，用户以为它「不更新」。改成按主口径（D3+P3+抄底）复算，口径与本页一致。
    def _bench_ret(d0, d1):
        """同期上证收益。d1 超出数据末行时（如盘中/今日）退回最近可用日，避免整段缺失。"""
        if BENCH_CODE not in panel.columns:
            return None
        try:
            _s = panel[BENCH_CODE]
            _i0 = int(panel.index.searchsorted(pd.Timestamp(d0), side="left"))
            _i1 = int(panel.index.searchsorted(pd.Timestamp(d1), side="left"))
            _i0 = min(_i0, len(_s) - 1)
            _i1 = min(_i1, len(_s) - 1)
            if _i1 <= _i0:
                return None
            a, b = float(_s.iloc[_i0]), float(_s.iloc[_i1])
            if not a or pd.isna(a) or pd.isna(b):
                return None
            return b / a - 1.0
        except Exception:                              # noqa: BLE001
            return None

    _sel = (r["trades"] or [])[-max(1, n_rows):]
    p3_rows = []
    for _k, _t in enumerate(_sel):
        _d0 = _t["date"]
        if _k < len(_sel) - 1:
            _d1 = _sel[_k + 1]["date"]
            _nav_end = float(_sel[_k + 1]["nav"])
        else:
            _d1 = asof
            _nav_end = float(navs.iloc[-1])
        _nav_start = float(_t["nav"])
        _same = (_d1 == _d0)
        # ★ 2026-10-28：空仓段（换到 CASH）不参与「同期上证 / 超额」比较 ——
        #   空仓是 D3/P3 规则强制的冷却，净值不变，拿现金去比指数会显示成
        #   「跑输大盘」，属误导（实测 10-28 空仓段：策略 -0.00% vs 上证 +0.91%）。
        _is_cash = (str(_t.get("to") or "") == "CASH")
        if _same:
            _ret = _br = _al = None
        else:
            _ret = (_nav_end / _nav_start - 1.0) if _nav_start > 0 else None
            if _is_cash:
                _br = _al = None
            else:
                _br = _bench_ret(_d0, _d1)
                _al = (_ret - _br) if (_ret is not None and _br is not None) else None
        _days = 0
        if not _same:
            try:
                _i0 = int(dates.searchsorted(pd.Timestamp(_d0), side="left"))
                _i1 = int(dates.searchsorted(pd.Timestamp(_d1), side="left"))
                _days = max(0, _i1 - _i0)
            except Exception:                          # noqa: BLE001
                _days = 0
        p3_rows.append(dict(
            idx=_k + 1, date=_d0, end_date=_d1, days=_days,
            frm=_t.get("frm"), to=_t.get("to"),
            frm_name=_t.get("frm_name") or _t.get("frm"),
            to_name=_t.get("to_name") or _t.get("to"),
            ret=_ret, bench=_br, alpha=_al, cash=_is_cash,
            reason=_t.get("reason") or "",
            open=(_k == len(_sel) - 1)))

    # 昨日持仓（= 今天开盘时手里实际有的）：
    # 主口径可能昨天刚抄底换过仓，与基础轮动的持仓不同，
    # 操作表「当前持 X」必须用它，否则会显示成基础轮动的持仓。
    prev_hold = None
    _pv = [t for t in r["trades"] if str(t.get("date") or "") < str(asof)]
    if _pv:
        prev_hold = _pv[-1].get("to")
    # ---- 抄底模块（2026-10-26 接入）：当前持仓是否由抄底产生 + 最近几次触发 ----
    _we = r.get("weak_events") or []
    _lt = None
    for _x in r["trades"]:
        if _x["date"] <= asof:
            _lt = _x
        else:
            break
    weak_active = bool(_lt and "抄底" in str(_lt.get("reason") or ""))
    weak_last = _we[-1] if _we else None
    rc = r.get("roc_cools") or []
    rci = ri.get("roc_cools") or []

    # ---- 单次 P3 事件的表现（2026-10-29 用户要求：每次事件都要有收益 + 同期上证对比）----
    def _ev_perf(c):
        """返回该次事件的三个数：
          hold_ret   = 被止盈那一笔的持仓段收益（策略净值口径，已含双边成本）
          bench_hold = 同持仓段的上证涨跌（同一窗口）
          bench_cool = 空仓期（触发日→回场日）的上证涨跌 —— P3 到底躲过了什么
        """
        _d0, _nav0, _nav1 = None, None, None
        for _t in r["trades"]:
            if _t["date"] > c["date"]:
                break
            if _t.get("to") and _t["to"] != "CASH":
                _d0, _nav0 = _t["date"], float(_t["nav"])
            if _t["date"] == c["date"]:
                _nav1 = float(_t["nav"])
        _hold = ((_nav1 / _nav0 - 1.0) * 100) if (_nav0 and _nav1) else None
        _bh = _bench_ret(_d0, c["date"]) if _d0 else None
        _bc = _bench_ret(c["date"], c["until"])
        _bhv = (_bh * 100) if _bh is not None else None
        return dict(hold_ret=_hold, bench_hold=_bhv,
                    bench_cool=(_bc * 100 if _bc is not None else None),
                    excess=((_hold - _bhv) if (_hold is not None and _bhv is not None) else None))

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

    # ---- 近 5 年「策略体检」（2026-10-27 用户需求）----
    # 五个指标：近5年最大回撤 / 当前回撤 / 近5年最大波段涨幅 / 当前波段涨幅 /
    #           最近一笔持仓收益在近5年所有持仓段中的百分位
    _nv = navs.copy()
    try:
        _nv.index = pd.to_datetime(_nv.index)
    except Exception:                          # noqa: BLE001
        pass
    _d5 = _nv[_nv.index >= pd.Timestamp("2021-10-25")]
    if len(_d5) < 20:
        _d5 = _nv
    _peak = _d5.cummax()
    _mdd5 = float((_d5 / _peak - 1.0).min()) * 100            # 标准最大回撤（跨段，保留作参照）
    _dd_now = (float(_d5.iloc[-1]) / float(_peak.iloc[-1]) - 1.0) * 100

    # ★ 2026-10-27 用户纠正：要的是「单段」波段（波谷→波峰 / 波峰→波谷），
    #   不是「从起点/最近低点的累计涨幅」。改用 ZigZag（5% 阈值）识别波段：
    #   净值从高点回撤 ≥5% 确认一个峰；从低点反弹 ≥5% 确认一个谷。
    def _zigzag(vals, thr=0.05):
        piv = []
        trend, hi_i, lo_i = 0, 0, 0
        hi_v = lo_v = float(vals[0])
        for _i in range(len(vals)):
            _v = float(vals[_i])
            if trend >= 0:
                if _v > hi_v:
                    hi_i, hi_v = _i, _v
                if _v <= hi_v * (1 - thr):
                    piv.append((hi_i, hi_v, "H"))
                    trend, lo_i, lo_v = -1, _i, _v
            if trend <= 0:
                if _v < lo_v:
                    lo_i, lo_v = _i, _v
                if _v >= lo_v * (1 + thr):
                    piv.append((lo_i, lo_v, "L"))
                    trend, hi_i, hi_v = 1, _i, _v
        piv.append((hi_i, hi_v, "H") if trend >= 0 else (lo_i, lo_v, "L"))
        return piv

    _piv = _zigzag(_d5.values, 0.05)
    _ups, _downs = [], []
    for _k in range(1, len(_piv)):
        _a, _b = _piv[_k - 1], _piv[_k]
        # ★ 2026-10-29 用户要求：每个体检指标下面要标「是哪一段时间」，
        #   故每段都带上起止日期（谷日/峰日），取极值时一并取出。
        if _a[2] == "L" and _b[2] == "H":
            _ups.append(((_b[1] / _a[1] - 1.0) * 100,
                         str(_d5.index[_a[0]].date()), str(_d5.index[_b[0]].date())))
        elif _a[2] == "H" and _b[2] == "L":
            _downs.append(((_b[1] / _a[1] - 1.0) * 100,
                           str(_d5.index[_a[0]].date()), str(_d5.index[_b[0]].date())))
    _hs = [p for p in _piv if p[2] == "H"]
    _ls = [p for p in _piv if p[2] == "L"]
    _lh = _hs[-1] if _hs else None
    _ll = _ls[-1] if _ls else None
    _now5 = float(_d5.iloc[-1])
    # ★ 2026-10-29：当前波段的方向必须看**最后一个拐点是峰还是谷**。
    #   旧写法只要价格低于最后一个峰就报「下跌中」，于是在「刚从谷底反弹」的日子
    #   会同时显示「最大回撤段已结束于 10-27」和「当前下跌中」，自相矛盾。
    _last_is_low = bool(_ll) and (not _lh or _ll[0] > _lh[0])
    _cur_dd = (((_now5 / _lh[1] - 1.0) * 100)
               if (not _last_is_low and _lh and _now5 < _lh[1]) else 0.0)
    _cur_up = (((_now5 / _ll[1] - 1.0) * 100)
               if (_last_is_low and _ll and _now5 > _ll[1]) else 0.0)
    _seg_up = max(_ups, key=lambda x: x[0]) if _ups else None
    _seg_dn = min(_downs, key=lambda x: x[0]) if _downs else None
    _seg_max_up = _seg_up[0] if _seg_up else None
    _seg_max_dn = _seg_dn[0] if _seg_dn else None
    _peak_date = str(_d5.index[_lh[0]].date()) if _lh else None
    _trough_date = str(_d5.index[_ll[0]].date()) if _ll else None
    # 每笔持仓段收益（用换仓时点的净值差近似，含成本）
    _seg = []
    for _k in range(len(r["trades"]) - 1):
        _a, _b = float(r["trades"][_k]["nav"]), float(r["trades"][_k + 1]["nav"])
        if _a:
            _seg.append((_b / _a - 1.0) * 100)
    # 「当前持仓阶段」= 最后一笔非 CASH 换仓至今（若已清仓，则等于该笔已实现收益）
    _seg_nav = None
    _hold_since = None
    for _t in reversed(r["trades"]):
        if _t.get("to") and _t["to"] != "CASH":
            _seg_nav = float(_t["nav"])
            _hold_since = str(_t.get("date") or "")
            break
    _hold_ret = ((float(navs.iloc[-1]) / _seg_nav - 1.0) * 100
                 if _seg_nav else None)
    if _hold_ret is not None:
        _seg.append(_hold_ret)
    _hold_pct = None
    if _seg and _hold_ret is not None:
        _hold_pct = sum(1 for _x in _seg if _x < _hold_ret) / len(_seg) * 100
    metrics = dict(mdd5=_mdd5, dd_now=_dd_now,
                   seg_max_up=_seg_max_up, seg_max_dn=_seg_max_dn,
                   # 每个极值段的起止日（2026-10-29 用户要求：数据下方标明时间段）
                   seg_up_from=(_seg_up[1] if _seg_up else None),
                   seg_up_to=(_seg_up[2] if _seg_up else None),
                   seg_dn_from=(_seg_dn[1] if _seg_dn else None),
                   seg_dn_to=(_seg_dn[2] if _seg_dn else None),
                   hold_since=_hold_since,
                   cur_dd=_cur_dd, cur_up=_cur_up,
                   peak_date=_peak_date, trough_date=_trough_date,
                   piv_n=len(_piv), up_n=len(_ups), dn_n=len(_downs),
                   hold_ret=_hold_ret, hold_pct=_hold_pct, seg_n=len(_seg),
                   since=str(_d5.index[0].date()), days=len(_d5))

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
        # 抄底模块：weak_active=今天这笔持仓是否由抄底规则产生；
        # weak_last=最近一次抄底（date/code/name/m）；weak_n=窗口内总触发次数
        weak_active=weak_active, weak_last=weak_last, weak_n=len(_we),
        weak_cd=bl.LIVE_COMPOSITE.get("weak_cd", 0),
        weak_min_pool=bl.LIVE_COMPOSITE.get("weak_min_pool", 0),
        cash_days=cash_days, rows=p3_rows,
        trades=r["trades"],          # 供排名表「切换至今」按主口径复算（2026-10-29）
        cash_src=cash_src, cash_reason=cash_reason,
        cash_until=cash_until, cash_ret=cash_ret, cash_note=cash_note,
        weak_state=weak_state,
        prev_hold=prev_hold,
        prev_hold_name=("持币" if prev_hold == "CASH"
                        else (bl.NAMES.get(prev_hold, prev_hold) if prev_hold else None)),
        last=[dict(date=c["date"],
                   head=c.get("head"), head_name=c.get("head_name"),
                   roc=c.get("roc"), pct=c.get("pct"), m20=c.get("m20"),
                   code=c.get("code"), name=c.get("name"),
                   held_m20=c.get("held_m20"),
                   until=c["until"], days=c["days"],
                   **_ev_perf(c)) for c in rc[-3:]][::-1],
        # 三列对比：现用策略 / D3+P3（主口径） / 参照 D3 / 参照 仅P3
        metrics=metrics,
        base=_pack(sb, base_navs), d3=_pack(sd, d3_navs),
        p3only=dict(_pack(si, ri["navs"]), roc_n=len(rci),
                    trades_n=len(ri["trades"]),
                    hold=p3_hold, hold_name=_hname(p3_hold),
                    roc_days_total=int(sum(c["days"] for c in rci))),
        asof=asof,
        navs=navs,          # 供「近 5 年净值曲线」直接画 D3+P3 曲线（同一次回测，不重算）
        yearly=bl.yearly(navs),   # 供「年度表现」三柱：现用策略 / D3 / D3+P3 逐年对比
        wr=_win_stats(r, navs),   # 四套胜率（2026-10-30 用户要求进日报回测卡）
    )


CURVE_DAYS = 1826         # 兼容保留（旧签名参数）；窗口实际固定 2020-01-01 起


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
    # ★ 2026-10-29 用户要求：5 年净值曲线固定从 2020-01-01 起算
    #   （不再用「今天往前推 1826 天」的滚动窗口，起点每年漂移、跨年对比不一致）。
    start_d = pd.Timestamp("2020-01-01")
    if end_d <= start_d:                      # 数据早于 2020 时兜底（正常不会发生）
        start_d = end_d - pd.Timedelta(days=1825)

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


def _hold_band(dates, trs):
    """把换仓序列铺成「每个交易日手里持有什么」，供净值曲线下方的持仓色带。

    dates : 交易日字符串列表（与曲线同窗口、同长度）
    trs   : 换仓列表 [{date, frm, to, ...}]，按时间升序（传主口径 p3["trades"]）
    返回  : 同长度列表，元素为标的代码；"CASH" 表示空仓。
    用途  : 2026-10-29 用户参考博主的持仓条，要求在「近 2 个月放大」下方也看到
            每一段持有什么、哪几天是空仓。
    """
    if not dates:
        return []
    trs = [t for t in (trs or []) if t.get("date")]
    cur = (trs[0].get("frm") if trs else "CASH") or "CASH"
    out, j = [], 0
    for d in dates:
        while j < len(trs) and str(trs[j]["date"]) <= str(d):
            cur = trs[j].get("to") or "CASH"
            j += 1
        out.append(cur)
    return out
