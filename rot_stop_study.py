"""主轮动池「提前止损」研究（2026-10-24）

用户设想：池内 9 只里只有头名动横20 为正、其余全负时，作为止损信号（控回撤）。
本脚本在同一状态机上挂多种止损钩子，全部关闭时须与 etf_backtest_long.backtest()
逐点一致（回归断言），再逐个打开比较。

钩子（互相独立，可组合）：
  breadth_thr / breadth_to / breadth_recover : 广度止损（4.2 节，用户方案）
  sl_pct / sl_to      : 入场价止损（相对建仓价回撤 X%）
  trail_pct           : 移动止损（相对建仓后最高收盘回撤 X%）
  ma_exit             : 跌破持仓标的 MA20 离场
  mom_exit            : 头名动横20 转负离场
离场后进入 STOP 状态，持 stop_to（CASH / DEF），回归条件见 _cleared()。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import etf_backtest_long as bl  # noqa: E402

W = 20
MA = 20
NAMES = bl.NAMES


def _cum_hit(hist, win, thr, mode="compound", prefix=False):
    """判断「近 N 笔累计亏损」是否触发。hist 为已平仓收益（时间序，末位=最近）。

    prefix=False：必须凑满 win 笔才判，用整窗累计（旧口径 E）。
    prefix=True ：最近 1..win 笔的**任一前缀**累计 <= -thr 即触发 —— 对应用户 2026-10-24 口径
                  「单笔超 3% 就不用等后面，两笔超 3% 也不用等，第三笔加上前两笔超 3% 才停」。
    返回 (是否触发, 用于展示的累计值)。
    """
    if not hist:
        return False, None

    def _agg(seg):
        return (sum(seg) if mode == "sum"
                else float(np.prod([1.0 + x for x in seg]) - 1.0))

    if prefix:
        best = None
        for k in range(1, min(win, len(hist)) + 1):
            v = _agg(hist[-k:])
            if best is None or v < best:
                best = v
            if v <= -thr:
                return True, v
        return False, best
    if len(hist) < win:
        return False, None
    v = _agg(hist[-win:])
    return (v <= -thr), v


def simulate(panel, dates, start_i, end_i, pool, defensive, *,
             sticky=1.0, enter_dev=12.0, enter_lead=15.0,
             exit_dev=9.0, exit_lead=10.0, min_def_days=5,
             buy=0.001, sell=0.001, value_engine=True,
             breadth_thr=None, breadth_recover=None,
             sl_pct=None, trail_pct=None,
             ma_exit=False, mom_exit=False,
             stop_to="DEF", cool=0, max_out=252,
             min_hold=0, confirm=False,
             loss_cool=None, loss_days=0, sig_list=None,
             loss3_cool=None, loss3_days=0, loss3_reset=True,
             loss3_window=3, loss3_mode="compound", loss3_prefix=False,
             loss3_all=False,
             gate_breadth=None, gate_recover=None, gate_max_days=0,
             rand_p=None, seed=0):
    """带止损钩子的策略状态机。所有钩子关闭 == bl.backtest()。"""
    rng = np.random.default_rng(seed)
    state = "ROT"                       # ROT / DEF / STOP
    def_days = 0
    held = "CASH"
    nav = 1.0
    navs = [(dates[start_i], 1.0)]
    trades = []
    prev_eff = None
    held_log, avail_log, head_log = [], [], []
    stick_hits = 0
    ve_hold = (bl.value_engine_hold(panel, defensive, dates) if value_engine else None)
    ma_s = {c: panel[c].rolling(MA).mean() for c in panel.columns}
    stops = []                          # 止损事件明细
    stop_days = 0
    stop_clear = 0
    stop_head = None
    entry_px = None
    peak_px = None
    held_days = 0                       # 当前持仓已持有交易日
    prev_raw_head = None
    cool_until = -1                     # 单笔亏损后冷却到期日（含）
    cool3_until = -1                    # 近 N 笔累计亏损后冷却到期日（含）
    closed_rets = []                    # 已平仓的轮动笔收益（按时间序）
    cool3_hits = 0                      # 累计冷却的天数
    cool3_trig = 0                      # 累计冷却的触发次数
    suppressed = 0                      # 被"最小持有期/确认"抑制掉的换仓次数
    cool_hits = 0
    gate_armed = False                   # 广度闸门是否上膛（仅由 D3 止损触发上膛）
    gate_start = -1                      # 上膛日（用于统计闸门持续天数）
    gate_hits = 0                        # 闸门生效（改为持币）的天数

    for i in range(start_i, end_i):
        sig = (sig_list[i] if sig_list is not None
               else bl.compute_signal(panel, pool, i, W, MA))
        if sig is None:
            ret = 0.0
            if held != "CASH" and held in panel.columns:
                p0, p1 = panel[held].iloc[i], panel[held].iloc[i + 1]
                if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                    ret = p1 / p0 - 1.0
            nav *= (1.0 + ret)
            avail_log.append((dates[i + 1], 0))
            head_log.append((dates[i + 1], None))
            held_log.append((dates[i + 1], held))
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
        m_head = sig["m"][raw_head]
        dev_eff = sig["dev"].get(eff_head, float("nan"))
        n_pos = sum(1 for v in sig["m"].values() if v > 0)
        n_list = len(sig["m"])

        reason = ""
        cooled_now = False
        # ---------------- 状态迁移 ----------------
        if state == "ROT":
            over = ((dev_eff == dev_eff and dev_eff > enter_dev) or
                    (sig["lead"] == sig["lead"] and sig["lead"] > enter_lead))
            if over:
                state, def_days = "DEF", 0
                reason = (f"过热(乖离{dev_eff:+.2f}%>{enter_dev:.0f}%"
                          if (dev_eff == dev_eff and dev_eff > enter_dev)
                          else f"过热(领先{sig['lead']:+.2f}pp>{enter_lead:.0f}pp") + " -> 切防守"
            else:
                trig = _stop_trigger(i, held, eff_head, entry_px, peak_px, n_pos,
                                     sig, panel, ma_s, breadth_thr, sl_pct,
                                     trail_pct, ma_exit, mom_exit, state)
                if not trig and rand_p is not None and held != "CASH" \
                        and rng.random() < rand_p:
                    trig = "随机止损(频率对照基准)"
                if trig:
                    state, stop_days, stop_clear = "STOP", 0, 0
                    stop_head = eff_head
                    reason = trig
        elif state == "DEF":
            def_days += 1
            if (dev_eff == dev_eff and dev_eff < exit_dev) and \
               (sig["lead"] == sig["lead"] and sig["lead"] < exit_lead) and \
               def_days >= min_def_days:
                state, def_days = "ROT", 0
                reason = (f"降温(乖离{dev_eff:+.2f}%<{exit_dev:.0f}% 且 "
                          f"领先{sig['lead']:+.2f}pp<{exit_lead:.0f}pp) 且防守满 "
                          f"{min_def_days} 日 -> 切回轮动")
        else:                            # STOP
            stop_days += 1
            if stop_days >= max_out:
                state, reason = "ROT", f"止损期已达上限 {max_out} 日 -> 强制回归轮动"
            elif stop_days >= cool and _cleared(i, eff_head, sig, n_pos, panel, ma_s,
                                                breadth_thr, breadth_recover,
                                                sl_pct, trail_pct, ma_exit,
                                                mom_exit, stop_head):
                state, reason = "ROT", f"止损条件解除（{stop_days} 日）-> 回轮动"

        # ---------------- 目标持仓 ----------------
        if state == "ROT":
            target = eff_head
            note_ve = ""
            # —— 反锯齿 A：最小持有期内不许换仓 ——
            if (min_hold and held != "CASH" and eff_head != held
                    and held != defensive and held_days < min_hold):
                target = held
                note_ve = f" | 最小持有 {min_hold} 日未满({held_days}日)，暂不换"
                suppressed += 1
            # —— 反锯齿 B：新头名须连续两日蝉联 ——
            if (confirm and held != "CASH" and held != defensive
                    and target == eff_head and eff_head != held
                    and prev_raw_head != raw_head):
                target = held
                note_ve = " | 新头名未连续两日蝉联，暂不换"
                suppressed += 1
            # —— 反锯齿 C：上一笔亏损后空仓冷却 ——
            if loss_cool is not None and i < cool_until:
                target = "CASH"
                note_ve = (f" | 上一笔亏损超 {loss_cool*100:.0f}%，"
                           f"空仓冷却至 {dates[cool_until].date()}")
                cool_hits += 1
            # 本次要换出的这一笔若亏超阈值 -> 改成空仓冷却
            elif (loss_cool is not None and target != held and held != "CASH"
                    and entry_px and held in panel.columns):
                _px = float(panel[held].iloc[i])
                if _px == _px and _px / entry_px - 1.0 <= -loss_cool:
                    target = "CASH"
                    cool_until = min(i + loss_days, len(dates) - 1)
                    cool_hits += 1
                    # 广度闸门上膛：只有「本笔亏超阈值」才允许闸门生效
                    if gate_breadth is not None:
                        gate_armed = True
                        gate_start = i
                    note_ve = (f" | 本笔 {(_px/entry_px-1)*100:+.2f}% 亏超 "
                               f"{loss_cool*100:.0f}%，空仓冷却 {loss_days} 日")
            # —— 反锯齿 E：止损后「广度闸门」——
            #   前提：已由 D3 止损上膛（gate_armed），且冷却期已满（i >= cool_until）。
            #   若此刻池内动横20 为正的只数 <= gate_breadth → 继续持币（不重新入场），
            #   直到广度恢复到 gate_recover 才解除闸门；gate_max_days 为闸门最长持续天数（0=不限）。
            if (gate_breadth is not None and gate_armed and state == "ROT"
                    and loss_cool is not None and i >= cool_until):
                _need = gate_recover if gate_recover is not None else gate_breadth + 1
                if n_pos < _need:
                    _over = (gate_max_days > 0 and (i - gate_start) >= gate_max_days)
                    if not _over:
                        target = "CASH"
                        gate_hits += 1
                        note_ve = (f" | 广度闸门：冷却期满但池内仅 {n_pos}/{n_list} 只"
                                   f"动横20 为正(< {_need}) → 继续持币")
                    else:
                        gate_armed = False
                        note_ve = (f" | 广度闸门已持续 {gate_max_days} 日 → 解除，重新入场")
                else:
                    gate_armed = False
                    note_ve = (f" | 广度恢复({n_pos}/{n_list} 只动横20 为正 >= {_need})"
                               f" → 闸门解除，重新入场")
            # —— 反锯齿 D：近 N 笔累计亏损后空仓冷却 ——
            if loss3_cool is not None:
                if i < cool3_until:
                    target = "CASH"
                    note_ve = (f" | 近 {loss3_window} 笔累计亏损超 "
                               f"{loss3_cool*100:.0f}%，空仓冷却至 {dates[cool3_until].date()}")
                    cool3_hits += 1
                elif (target != held and held != "CASH" and held != defensive
                      and entry_px and held in panel.columns):
                    _px3 = float(panel[held].iloc[i])
                    if _px3 == _px3:
                        _r = _px3 / entry_px - 1.0
                        _hist = list(closed_rets[-(loss3_window - 1):]) + [_r] \
                            if loss3_window > 1 else [_r]
                        _hit3, _cum = _cum_hit(_hist, loss3_window, loss3_cool,
                                               loss3_mode, loss3_prefix)
                        if _hit3:
                            target = "CASH"
                            cool3_until = min(i + loss3_days, len(dates) - 1)
                            cool3_hits += 1
                            cool3_trig += 1
                            cooled_now = True
                            note_ve = (f" | 近 {loss3_window} 笔累计 {_cum*100:+.2f}% "
                                       f"亏超 {loss3_cool*100:.0f}%，空仓冷却 {loss3_days} 日")
        elif state == "DEF":
            ve_ok = (not value_engine) or bool(ve_hold.loc[dates[i]])
            target = defensive if ve_ok else "CASH"
            note_ve = "" if ve_ok else " | 价值引擎未触发 -> 持币"
        else:
            if stop_to == "CASH":
                target = "CASH"
            else:
                ve_ok = (not value_engine) or bool(ve_hold.loc[dates[i]])
                target = defensive if ve_ok else "CASH"
            note_ve = ""
        if not reason:
            reason = "池内头名变更(粘性带外)" if state == "ROT" else ""

        switched = (target != held)
        if switched:
            c = buy + sell
            nav *= (1.0 - c)
            if state == "STOP":
                stops.append(dict(date=str(dates[i].date()), head=stop_head,
                                  frm=held, to=target, reason=reason))
            trades.append(dict(date=str(dates[i].date()), frm=held, to=target,
                               frm_name=("空仓" if held == "CASH" else NAMES.get(held, held)),
                               to_name=("持币" if target == "CASH" else NAMES.get(target, target)),
                               state=state, reason=reason + note_ve, tie=bool(tied),
                               lead=(None if sig["lead"] != sig["lead"] else round(sig["lead"], 2)),
                               m_head=round(m_head, 2),
                               dev=(None if dev_eff != dev_eff else round(dev_eff, 2)),
                               nav=round(nav, 6)))
            # 记录本笔已平仓收益（供「近 N 笔累计亏损」判断使用）
            # loss3_all=False（默认）只记 ROT 状态内的换仓；True 则连「切防守/止损」那几次也算进窗口。
            if (held != "CASH" and held != defensive and entry_px
                    and held in panel.columns
                    and (state == "ROT" or loss3_all)):
                _c_ = float(panel[held].iloc[i])
                if _c_ == _c_:
                    if cooled_now and loss3_reset:
                        closed_rets = []            # 触发后重置窗口，不把本笔计入
                    else:
                        closed_rets.append(_c_ / entry_px - 1.0)
                        closed_rets = closed_rets[-max(4, loss3_window):]
            # 每次换仓重置建仓价基准（用于入场价/移动止损）
            entry_px = (float(panel[target].iloc[i])
                        if target in panel.columns and not pd.isna(panel[target].iloc[i])
                        else None)
            peak_px = entry_px

        ret = 0.0
        if target in panel.columns:
            p0, p1 = panel[target].iloc[i], panel[target].iloc[i + 1]
            if not pd.isna(p0) and not pd.isna(p1) and p0 != 0:
                ret = p1 / p0 - 1.0
        nav *= (1.0 + ret)
        held = target
        if switched:
            held_days = 0
        else:
            held_days += 1
        # 维护峰值（用于移动止损）
        if held != "CASH" and held in panel.columns:
            px = float(panel[held].iloc[i + 1])
            if px == px:
                peak_px = px if peak_px is None or px > peak_px else peak_px
        prev_eff = eff_head
        prev_raw_head = raw_head
        avail_log.append((dates[i + 1], n_list))
        head_log.append((dates[i + 1], eff_head))
        held_log.append((dates[i + 1], target))
        navs.append((dates[i + 1], nav))

    nd = pd.Series({d.strftime("%Y-%m-%d"): v for d, v in navs})
    return dict(navs=nd, trades=trades, held_log=held_log, head_log=head_log,
                avail_log=avail_log, stick_hits=stick_hits, final=float(nd.iloc[-1]),
                stops=stops, suppressed=suppressed, cool_hits=cool_hits,
                cool3_hits=cool3_hits, cool3_trig=cool3_trig,
                gate_hits=gate_hits, gate_armed=gate_armed,
                cash_days=int(sum(1 for _d, t in held_log if t == "CASH")))


def _stop_trigger(i, held, eff_head, entry_px, peak_px, n_pos, sig, panel, ma_s,
                  breadth_thr, sl_pct, trail_pct, ma_exit, mom_exit, state):
    """ROT 状态下是否触发止损。优先级：广度 → 入场价 → 移动 → MA20 → 动量。"""
    if state != "ROT":
        return ""
    if breadth_thr is not None and n_pos <= breadth_thr:
        return f"广度止损(仅 {n_pos}/{len(sig['m'])} 只动横20 为正 <= {breadth_thr})"
    if held in panel.columns and entry_px:
        px = float(panel[held].iloc[i])
        if px == px:
            if sl_pct is not None and px / entry_px - 1.0 <= -sl_pct:
                return (f"入场价止损(现价 {px:.3f} 较建仓 {entry_px:.3f} "
                        f"{(px/entry_px-1)*100:+.2f}% <= -{sl_pct*100:.0f}%)")
            if trail_pct is not None and peak_px:
                if px / peak_px - 1.0 <= -trail_pct:
                    return (f"移动止损(现价 {px:.3f} 较建仓后最高 {peak_px:.3f} "
                            f"{(px/peak_px-1)*100:+.2f}% <= -{trail_pct*100:.0f}%)")
    if ma_exit and held in panel.columns:
        px = float(panel[held].iloc[i]); mv = float(ma_s[held].iloc[i])
        if px == px and mv == mv and px < mv:
            return f"跌破 MA{MA}({px:.3f} < {mv:.3f})"
    if mom_exit and sig["m"].get(eff_head, 1) <= 0:
        return f"头名动横{W}转负({sig['m'][eff_head]:+.2f}%)"
    return ""


def _cleared(i, eff_head, sig, n_pos, panel, ma_s, breadth_thr, breadth_recover,
             sl_pct, trail_pct, ma_exit, mom_exit, stop_head):
    """STOP 状态下止损条件是否解除。"""
    if breadth_thr is not None:
        need = breadth_recover if breadth_recover is not None else breadth_thr + 2
        return n_pos >= need
    if sl_pct is not None or trail_pct is not None:
        # 价格类止损：只有换帅（新头名）才算解除，否则同一标的会立刻重入再触发
        return eff_head != stop_head
    if ma_exit:
        if eff_head not in panel.columns:
            return True
        px = float(panel[eff_head].iloc[i]); mv = float(ma_s[eff_head].iloc[i])
        return not (px == px and mv == mv and px < mv)
    if mom_exit:
        return sig["m"].get(eff_head, 1) > 0
    return True


# --------------------------------------------------------------------------- 统计
def trade_stats(r, panel, dates):
    """逐笔：建仓/离场日、持有天数、区间收益、期间最大不利偏移(MAE)、最大有利(MFE)"""
    out = []
    log = r["held_log"]
    prev = "CASH"
    start = None
    for d, t in log:
        if t != prev:
            if start is not None and prev != "CASH":
                out.append(_one(panel, prev, start, d))
            start = d if t != "CASH" else None
            prev = t
    if start is not None and prev != "CASH":
        out.append(_one(panel, prev, start, log[-1][0]))
    return out


def _one(panel, code, d0, d1):
    if code not in panel.columns:
        return dict(code=code, start=str(d0)[:10], end=str(d1)[:10], days=0,
                    ret=np.nan, mae=np.nan, mfe=np.nan)
    seg = panel[code].loc[d0:d1].dropna()
    if len(seg) < 2:
        return dict(code=code, start=str(d0)[:10], end=str(d1)[:10], days=len(seg),
                    ret=np.nan, mae=np.nan, mfe=np.nan)
    p0 = float(seg.iloc[0])
    return dict(code=code, name=NAMES.get(code, code), start=str(d0)[:10],
                end=str(d1)[:10], days=len(seg) - 1,
                ret=(float(seg.iloc[-1]) / p0 - 1.0) * 100,
                mae=(float(seg.min()) / p0 - 1.0) * 100,
                mfe=(float(seg.max()) / p0 - 1.0) * 100)


def summarize(nav, name=""):
    st = bl.stats(nav)
    return dict(name=name, final=float(nav.iloc[-1]), total=st["total"] * 100,
                ann=st["ann"] * 100, mdd=st["mdd"] * 100,
                sharpe=st["sharpe"], sortino=st["sortino"],
                calmar=st["calmar"], vol=st["vol"] * 100 if st["vol"] == st["vol"] else None)


# --------------------------------------------------------------------------- 诊断
def breadth_frame(sig_list, start_i, end_i, panel):
    """逐日广度表：头名、广度、头名未来 5/10/20 日收益、未来 20 日最大回撤。"""
    rows = []
    for i in range(start_i, end_i + 1):
        sig = sig_list.get(i)
        if not sig:
            continue
        head = sig["head"]
        n_pos = sum(1 for v in sig["m"].values() if v > 0)
        rec = dict(date=str(panel.index[i].date()), head=head, breadth=n_pos,
                   n_list=len(sig["m"]), m_head=sig["m"][head])
        p0 = float(panel[head].iloc[i])
        for h in (5, 10, 20):
            j = min(i + h, end_i)
            p1 = float(panel[head].iloc[j])
            rec[f"fwd{h}"] = ((p1 / p0 - 1) * 100
                              if (p0 == p0 and p1 == p1 and p0) else np.nan)
            seg = panel[head].iloc[i:j + 1].dropna()
            rec[f"mdd{h}"] = ((float(seg.min()) / p0 - 1) * 100) if len(seg) and p0 == p0 else np.nan
        rows.append(rec)
    return pd.DataFrame(rows)


def breadth_table(df):
    out = []
    for b, g in df.groupby("breadth"):
        out.append(dict(breadth=int(b), n=len(g), share=len(g) / len(df) * 100,
                        fwd5=float(g["fwd5"].mean()), fwd10=float(g["fwd10"].mean()),
                        fwd20=float(g["fwd20"].mean()), mdd20=float(g["mdd20"].mean()),
                        win20=float((g["fwd20"] > 0).mean() * 100),
                        m_head=float(g["m_head"].mean())))
    out.append(dict(breadth=-1, n=len(df), share=100.0,
                    fwd5=float(df["fwd5"].mean()), fwd10=float(df["fwd10"].mean()),
                    fwd20=float(df["fwd20"].mean()), mdd20=float(df["mdd20"].mean()),
                    win20=float((df["fwd20"] > 0).mean() * 100),
                    m_head=float(df["m_head"].mean())))
    return out


def trade_table(trades):
    d = pd.DataFrame([t for t in trades if t["ret"] == t["ret"]])
    if not len(d):
        return [], {}
    d["bucket"] = pd.cut(d["days"], [-1, 2, 5, 10, 20, 40, 9999],
                         labels=["≤2日", "3~5日", "6~10日", "11~20日", "21~40日", ">40日"])
    rows = []
    for b, g in d.groupby("bucket", observed=True):
        rows.append(dict(bucket=str(b), n=len(g), avg=float(g["ret"].mean()),
                         med=float(g["ret"].median()),
                         win=float((g["ret"] > 0).mean() * 100),
                         mae=float(g["mae"].mean())))
    meta = dict(n=len(d), avg=float(d["ret"].mean()), med=float(d["ret"].median()),
                win=float((d["ret"] > 0).mean() * 100),
                loss_n=int((d["ret"] < 0).sum()),
                loss_avg=float(d.loc[d["ret"] < 0, "ret"].mean()) if (d["ret"] < 0).any() else None,
                deep=int((d["mae"] < -5).sum()),
                deep_recover=int(((d["mae"] < -5) & (d["ret"] > 0)).sum()),
                deep_recover_avg=(float(d.loc[(d["mae"] < -5) & (d["ret"] > 0), "ret"].mean())
                                  if ((d["mae"] < -5) & (d["ret"] > 0)).any() else None),
                max_loss=float(d["ret"].min()), max_gain=float(d["ret"].max()))
    return rows, meta


def dd_episodes(nav, held_log, n=3):
    v = nav.to_numpy(float)
    peak = np.maximum.accumulate(v)
    dd = v / peak - 1
    key = {pd.Timestamp(d).strftime("%Y-%m-%d"): t for d, t in held_log}
    eps, in_dd, start = [], False, 0
    for k in range(len(v)):
        if dd[k] < 0 and not in_dd:
            in_dd, start = True, k
        elif dd[k] >= 0 and in_dd:
            in_dd = False
            j = start + int(np.argmin(dd[start:k + 1]))
            eps.append((start, j, k))
    if in_dd:
        j = start + int(np.argmin(dd[start:]))
        eps.append((start, j, len(v) - 1))
    eps.sort(key=lambda e: dd[e[1]])
    out = []
    for a, j, b in eps[:n]:
        hold = pd.Series([key.get(x, "?") for x in nav.index[a:b + 1]])
        out.append(dict(start=str(nav.index[a]), trough=str(nav.index[j]),
                        end=str(nav.index[b]),
                        depth=float(dd[j] * 100), days=b - a,
                        hold=[dict(code=c, name=NAMES.get(c, c), days=int(k))
                              for c, k in hold.value_counts().items()]))
    return out


def counterfactual(panel, stops, n=20):
    """止损之后，被止损的那个标的未来 n 日怎么走？正数说明止损砍错了。"""
    out = []
    for s in stops:
        code = s["head"]
        if code not in panel.columns:
            continue
        ts = pd.Timestamp(s["date"])
        try:
            i = panel.index.get_loc(ts)
        except KeyError:
            continue
        j = min(i + n, len(panel) - 1)
        p0 = float(panel[code].iloc[i]); p1 = float(panel[code].iloc[j])
        if p0 != p0 or p1 != p1 or not p0:
            continue
        out.append((p1 / p0 - 1) * 100)
    if not out:
        return dict(n=0)
    a = np.array(out)
    return dict(n=len(a), avg=float(a.mean()), med=float(np.median(a)),
                pos=float((a > 0).mean() * 100), max=float(a.max()), min=float(a.min()))


# --------------------------------------------------------------------------- 主流程
WINDOWS = [
    ("main", "tdx_adj_long_2019_2026.csv", "2019-12-31",
     "主窗口 2019-12-31 ~ 2026-09-24"),
    ("long", "tdx_adj_long_2015_2026.csv", "2015-01-05",
     "长窗口 2015-01-05 ~ 2026-09-24"),
]


def load_win(fname, start):
    panel = bl.read_panel(os.path.join(HERE, fname))
    dates = panel.index
    s_i = int(dates.searchsorted(pd.Timestamp(start)))
    e_i = len(dates) - 1
    return panel, dates, s_i, e_i


def run_one(panel, dates, s_i, e_i, pool, defensive, sig_list, cost=0.002, **kw):
    r = simulate(panel, dates, s_i, e_i, pool, defensive,
                 buy=cost / 2, sell=cost / 2, sig_list=sig_list, **kw)
    st = summarize(r["navs"])
    st.update(trades_n=len(r["trades"]), stops_n=len(r["stops"]),
              suppressed=r["suppressed"], cool_hits=r["cool_hits"],
              cash_pct=r["cash_days"] / len(r["navs"]) * 100)
    return r, st


def stop_durations(r, dates):
    """STOP 段平均长度（交易日）：STOP 进场那笔 -> 下一笔换仓的间隔。"""
    ts = [pd.Timestamp(x["date"]) for x in r["trades"]]
    gaps = []
    for k, t in enumerate(r["trades"]):
        if t["state"] == "STOP":
            if k + 1 < len(ts):
                gaps.append(int(np.searchsorted(dates, ts[k + 1]) - np.searchsorted(dates, ts[k])))
            else:
                gaps.append(0)
    return (float(np.mean(gaps)) if gaps else 0.0), gaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(HERE, "out", "rot_stop.json"))
    ap.add_argument("--window", default="main", choices=["main", "long", "both"])
    a = ap.parse_args()

    rot, deff, watch = bl.read_pools()
    defensive = deff[0]
    wins = [w for w in WINDOWS if a.window in (w[0], "both")]

    out = dict(meta=dict(windows=[w[3] for w in wins], pool=rot, defensive=defensive,
                         cost=0.002, strategy="ROC 轮动 + 各类提前止损", date="2026-10-24"),
               targets={}, scans={}, diag={})

    for key, fname, start, label in wins:
        panel, dates, s_i, e_i = load_win(fname, start)
        sig_list = {i: bl.compute_signal(panel, rot, i, W, MA) for i in range(s_i, e_i)}
        print(f"\n{'='*78}\n{label}  数据到 {dates[e_i].date()}  {e_i - s_i} 个收益日")
        res = {}

        # --- 基线 ---
        r0, s0 = run_one(panel, dates, s_i, e_i, rot, defensive, sig_list)
        res["A"] = dict(label="A 基线（不加上任何止损）", kw={}, stat=s0)
        print(f"  A 基线        净值 {s0['final']:.4f}  收益 {s0['total']:+.2f}%  "
              f"MDD {s0['mdd']:.2f}%  夏普 {s0['sharpe']:.2f}  换仓 {s0['trades_n']}")

        # --- 广度止损扫描 ---
        print("\n  【广度止损】只有头名动横20 为正 / 更宽的口径 → 切走")
        print(f"    {'阈值':<16}{'去向':<8}{'回归':<8}{'净值':>9}{'收益%':>10}"
              f"{'MDD%':>9}{'夏普':>7}{'Calmar':>8}{'止损次':>7}{'换仓':>6}")
        br_scan = []
        for thr in (0, 1, 2, 3):
            for to in ("DEF", "CASH"):
                for rec in (thr + 2, 4, 6):
                    if rec <= thr:
                        continue
                    kw = dict(breadth_thr=thr, breadth_recover=rec, stop_to=to)
                    r, s = run_one(panel, dates, s_i, e_i, rot, defensive, sig_list, **kw)
                    br_scan.append(dict(thr=thr, to=to, rec=rec, stat=s, kw=kw))
                    print(f"    ≤{thr} 只为正{'':<8}{to:<8}≥{rec:<7}{s['final']:>9.4f}"
                          f"{s['total']:>+10.2f}{s['mdd']:>9.2f}"
                          f"{(s['sharpe'] or 0):>7.2f}{(s['calmar'] or 0):>8.2f}"
                          f"{s['stops_n']:>7}{s['trades_n']:>6}")
        out["scans"].setdefault(key, {})["breadth"] = br_scan

        # --- 价格类止损 ---
        print("\n  【价格类止损】入场价回撤 / 移动止损 / 破 MA20 / 动量转负")
        print(f"    {'方案':<22}{'净值':>9}{'收益%':>10}{'MDD%':>9}{'夏普':>7}"
              f"{'Calmar':>8}{'止损次':>7}{'换仓':>6}")
        pr_scan = []
        variants = [("入场价 −3%", dict(sl_pct=0.03)), ("入场价 −5%", dict(sl_pct=0.05)),
                    ("入场价 −8%", dict(sl_pct=0.08)), ("入场价 −10%", dict(sl_pct=0.10)),
                    ("入场价 −12%", dict(sl_pct=0.12)),
                    ("移动止损 −6%", dict(trail_pct=0.06)), ("移动止损 −8%", dict(trail_pct=0.08)),
                    ("移动止损 −10%", dict(trail_pct=0.10)), ("移动止损 −15%", dict(trail_pct=0.15)),
                    ("跌破 MA20", dict(ma_exit=True)),
                    ("动横20 转负", dict(mom_exit=True))]
        for lab, base_kw in variants:
            for to in ("CASH", "DEF"):
                kw = dict(base_kw, stop_to=to, cool=5)
                r, s = run_one(panel, dates, s_i, e_i, rot, defensive, sig_list, **kw)
                pr_scan.append(dict(label=f"{lab}→{to}", to=to, stat=s, kw=kw,
                                    cf=counterfactual(panel, r["stops"])))
                print(f"    {lab+f'→{to}':<22}{s['final']:>9.4f}{s['total']:>+10.2f}"
                      f"{s['mdd']:>9.2f}{(s['sharpe'] or 0):>7.2f}"
                      f"{(s['calmar'] or 0):>8.2f}{s['stops_n']:>7}{s['trades_n']:>6}")
        out["scans"][key]["price"] = pr_scan

        # --- 反锯齿族 ---
        print("\n  【反锯齿】不砍亏损，改砍「换仓太勤」")
        print(f"    {'方案':<24}{'净值':>9}{'收益%':>10}{'MDD%':>9}{'夏普':>7}"
              f"{'Calmar':>8}{'抑制':>7}{'换仓':>6}")
        an_scan = []
        for mh in (3, 5, 8, 10, 12, 15, 20, 25):
            kw = dict(min_hold=mh)
            r, s = run_one(panel, dates, s_i, e_i, rot, defensive, sig_list, **kw)
            an_scan.append(dict(label=f"最小持有 {mh} 日", kind="min_hold", param=mh,
                                stat=s, kw=kw))
            print(f"    {f'最小持有 {mh} 日':<24}{s['final']:>9.4f}{s['total']:>+10.2f}"
                  f"{s['mdd']:>9.2f}{(s['sharpe'] or 0):>7.2f}{(s['calmar'] or 0):>8.2f}"
                  f"{s['suppressed']:>7}{s['trades_n']:>6}")
        for lc in (0.015, 0.02, 0.025, 0.03, 0.04, 0.05):
            for ld in (5, 10, 20):
                kw = dict(loss_cool=lc, loss_days=ld)
                r, s = run_one(panel, dates, s_i, e_i, rot, defensive, sig_list, **kw)
                an_scan.append(dict(label=f"亏损>{lc*100:.1f}% 空仓 {ld} 日", kind="loss_cool",
                                    param=[lc, ld], stat=s, kw=kw))
                print(f"    {f'亏损>{lc*100:.1f}% 空仓{ld}日':<24}{s['final']:>9.4f}"
                      f"{s['total']:>+10.2f}{s['mdd']:>9.2f}{(s['sharpe'] or 0):>7.2f}"
                      f"{(s['calmar'] or 0):>8.2f}{s['cool_hits']:>7}{s['trades_n']:>6}")
        kw = dict(confirm=True)
        r, s = run_one(panel, dates, s_i, e_i, rot, defensive, sig_list, **kw)
        an_scan.append(dict(label="新头名须连 2 日蝉联", kind="confirm", param=2, stat=s, kw=kw))
        print(f"    {'新头名须连2日蝉联':<24}{s['final']:>9.4f}{s['total']:>+10.2f}"
              f"{s['mdd']:>9.2f}{(s['sharpe'] or 0):>7.2f}{(s['calmar'] or 0):>8.2f}"
              f"{s['suppressed']:>7}{s['trades_n']:>6}")
        out["scans"][key]["anti"] = an_scan

        # --- 诊断 ---
        bf = breadth_frame(sig_list, s_i, e_i, panel)
        tr_rows, tr_meta = trade_table(trade_stats(r0, panel, dates))
        dur, gaps = stop_durations(run_one(panel, dates, s_i, e_i, rot, defensive,
                                           sig_list, breadth_thr=1, breadth_recover=3)[0], dates)
        out["diag"][key] = dict(label=label,
                                breadth=breadth_table(bf),
                                trade_bucket=tr_rows, trade_meta=tr_meta,
                                dd=dd_episodes(r0["navs"], r0["held_log"]),
                                stop_dur_def=dur)
        print(f"\n  【诊断】逐笔 {tr_meta['n']} 笔  平均 {tr_meta['avg']:+.2f}%  "
              f"中位 {tr_meta['med']:+.2f}%  亏损笔均 {tr_meta['loss_avg']:+.2f}%")
        for row in tr_rows:
            print(f"    {row['bucket']:<8} n={row['n']:>3}  平均 {row['avg']:+6.2f}%  "
                  f"中位 {row['med']:+6.2f}%  胜率 {row['win']:5.1f}%")
        print(f"    MAE<-5% 的 {tr_meta['deep']} 笔中，最终回正 {tr_meta['deep_recover']} 笔"
              f"（平均 {tr_meta['deep_recover_avg'] or 0:+.2f}%）"
              f" ⇒ 止损会砍在这里")
        for e in out["diag"][key]["dd"]:
            hold = " / ".join(f"{h['name'][:8]}×{h['days']}" for h in e["hold"][:4])
            print(f"    回撤 {e['depth']:+.2f}%  {e['start']}~{e['end']}（{e['days']} 日） {hold}")

        # --- 逐年 + 分段稳定性 ---
        CAND = [("A 基线", {}),
                ("B1 广度≤1→防守", dict(breadth_thr=1, breadth_recover=3)),
                ("C2 入场价止损 −8%", dict(sl_pct=0.08, stop_to="CASH", cool=5)),
                ("D2 最小持有 10 日", dict(min_hold=10)),
                ("D4 亏损>2.5% 空仓 5 日", dict(loss_cool=0.025, loss_days=5)),
                ("D3 亏损>3% 空仓 5 日", dict(loss_cool=0.03, loss_days=5))]
        yr_map, sub_map = {}, {}
        mids = None
        curves = {}
        for tag, kw in CAND:
            r, s = run_one(panel, dates, s_i, e_i, rot, defensive, sig_list, **kw)
            yr_map[tag] = {x["year"]: x["ret"] * 100 for x in bl.yearly(r["navs"])}
            _nv = r["navs"]
            idx = list(range(0, len(_nv), 3))
            if idx[-1] != len(_nv) - 1:
                idx.append(len(_nv) - 1)
            curves[tag] = dict(d=[_nv.index[i] for i in idx],
                               v=[round(float(_nv.iloc[i]), 5) for i in idx])
            if mids is None:
                dts = pd.to_datetime(r["navs"].index)
                mids = (dts[0] + (dts[-1] - dts[0]) / 2).strftime("%Y-%m-%d")
            segs = {}
            for nm, lo, hi in (("前半段", None, mids), ("后半段", mids, None)):
                ss = r["navs"]
                sel = ss if lo is None else ss[ss.index >= lo]
                if hi is not None:
                    sel = sel[sel.index <= hi]
                st = bl.stats(sel)
                segs[nm] = dict(final=float(sel.iloc[-1] / sel.iloc[0]),
                                total=st["total"] * 100, mdd=st["mdd"] * 100,
                                sharpe=st["sharpe"])
            sub_map[tag] = segs
        out["scans"][key]["yearly"] = yr_map
        out["scans"][key]["sub"] = dict(mid=mids, data=sub_map)
        out["scans"][key]["curves"] = curves
        print("\n  【逐年收益】")
        yrs = sorted({y for v in yr_map.values() for y in v})
        print("    " + "".join(f"{y:>9}" for y in yrs))
        for tag, _ in CAND:
            print(f"    {tag:<20}" + "".join(
                (f"{yr_map[tag][y]:>+8.1f}%" if y in yr_map[tag] else f"{'-':>9}")
                for y in yrs))
        print(f"  【分段稳定性】切点 {mids}")
        for tag, _ in CAND:
            a1, a2 = sub_map[tag]["前半段"], sub_map[tag]["后半段"]
            print(f"    {tag:<20} 前半 {a1['total']:+8.2f}%（MDD {a1['mdd']:6.2f}%）"
                  f"  后半 {a2['total']:+8.2f}%（MDD {a2['mdd']:6.2f}%）")

        # --- 亏损冷却参数曲面 ---
        surf_t, surf_d = (0.02, 0.025, 0.03, 0.035, 0.04), (3, 4, 5, 6, 7)
        surf = []
        for lc in surf_t:
            row = []
            for ld in surf_d:
                r, s = run_one(panel, dates, s_i, e_i, rot, defensive, sig_list,
                               loss_cool=lc, loss_days=ld)
                row.append(s["final"])
            surf.append(row)
        out["scans"][key]["surface"] = dict(thr=list(surf_t), days=list(surf_d),
                                            vals=surf, base=s0["final"])
        print("\n  【亏损冷却曲面】行=亏损阈值 列=空仓天数（基线 %.4f）" % s0["final"])
        print("    " + "".join(f"{f'{d}日':>9}" for d in surf_d))
        for lc, row in zip(surf_t, surf):
            print(f"   >{lc*100:>4.1f}% " + "".join(f"{v:>9.4f}" for v in row))

        # --- 成本敏感性 ---
        print("\n  【成本敏感性】每次换仓双边合计")
        cost_rows = []
        for cost in (0.001, 0.002, 0.003):
            for tag, kw in (("A", {}),
                            ("B1 广度≤1→防守", dict(breadth_thr=1, breadth_recover=3)),
                            ("D3 亏损>2%空仓5日", dict(loss_cool=0.02, loss_days=5)),
                            ("D2 最小持有10日", dict(min_hold=10))):
                r, s = run_one(panel, dates, s_i, e_i, rot, defensive, sig_list,
                               cost=cost, **kw)
                cost_rows.append(dict(cost=cost, tag=tag, final=s["final"],
                                      total=s["total"], mdd=s["mdd"],
                                      sharpe=s["sharpe"]))
                print(f"    成本 {cost*100:.1f}%  {tag:<18} 净值 {s['final']:.4f}  "
                      f"MDD {s['mdd']:.2f}%")
        out["scans"][key]["cost"] = cost_rows

        # --- 随机频率对照（针对广度止损）---
        rot_days = sum(1 for _d, t in r0["held_log"] if t not in ("CASH",))
        _ev = next(x for x in br_scan if x["thr"] == 1 and x["to"] == "DEF" and x["rec"] == 3)
        p_est = min(0.5, _ev["stat"]["stops_n"] / max(rot_days, 1))
        print(f"\n  【随机频率对照】广度≤1 触发 {_ev['stat']['stops_n']} 次 / 持仓 {rot_days} 日"
              f" → 随机触发概率 {p_est*100:.2f}%/日，暂停 {dur:.1f} 日")
        rand_finals = []
        for sd in range(30):
            r, s = run_one(panel, dates, s_i, e_i, rot, defensive, sig_list,
                           rand_p=p_est, seed=sd, cool=max(1, int(round(dur))), stop_to="DEF")
            rand_finals.append(s["final"])
        arr = np.array(rand_finals)
        real = next(x for x in br_scan if x["thr"] == 1 and x["to"] == "DEF"
                    and x["rec"] == 3)
        pct = float((arr < real["stat"]["final"]).mean() * 100)
        print(f"    随机版净值 p05 {np.percentile(arr,5):.4f} 中位 {np.median(arr):.4f} "
              f"p95 {np.percentile(arr,95):.4f} ｜ 真实广度止损 {real['stat']['final']:.4f} "
              f"落在 {pct:.1f}% 分位")
        out["scans"][key]["rand"] = dict(p=p_est, dur=dur, finals=[float(x) for x in arr],
                                         p05=float(np.percentile(arr, 5)),
                                         p50=float(np.median(arr)),
                                         p95=float(np.percentile(arr, 95)),
                                         real=real["stat"]["final"], pctile=pct)

        # --- 亏损冷却的随机对照（同频率、同暂停长度的空仓）---
        rl = {}
        for tag, kw in (("D4 亏损>2.5%", dict(loss_cool=0.025, loss_days=5)),
                        ("D3 亏损>3%", dict(loss_cool=0.03, loss_days=5))):
            rr, ss = run_one(panel, dates, s_i, e_i, rot, defensive, sig_list, **kw)
            ev = len([t for t in rr["trades"] if "亏超" in t["reason"]])
            pp = ev / max(rot_days, 1)
            fs = []
            for sd in range(40):
                _r, _s = run_one(panel, dates, s_i, e_i, rot, defensive, sig_list,
                                 rand_p=pp, seed=sd, cool=5, stop_to="CASH")
                fs.append(_s["final"])
            a2 = np.array(fs)
            base_f = s0["final"]
            rl[tag] = dict(events=ev, p=pp, real=ss["final"], real_mdd=ss["mdd"],
                           base=base_f, p05=float(np.percentile(a2, 5)),
                           p50=float(np.median(a2)), p95=float(np.percentile(a2, 95)),
                           pctile=float((a2 < ss["final"]).mean() * 100),
                           beat_base=float((a2 < base_f).mean() * 100))
            print(f"    {tag}: {ev} 次 / {rot_days} 日 ｜ 真实 {ss['final']:.4f} vs 基线 "
                  f"{base_f:.4f} ｜ 随机中位 {np.median(a2):.4f} p95 {np.percentile(a2,95):.4f}"
                  f" → 分位 {rl[tag]['pctile']:.1f}%")
        out["scans"][key]["rand_loss"] = rl

        out["targets"][key] = dict(label=label, baseline=s0,
                                   trades=trade_stats(r0, panel, dates))

    os.makedirs(os.path.dirname(a.json), exist_ok=True)
    with open(a.json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[json] {a.json}")


if __name__ == "__main__":
    main()
