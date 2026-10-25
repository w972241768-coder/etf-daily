#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
独立审计复算 · ETF 轮动策略B 防守改造A/B/C 消融
================================================================================
审计原则(硬约束):
  * 不 import 主 agent 的 etf_backtest_long.py / compare_defense.py
  * 只读原始数据 tdx_adj_long_2019_2026.csv + etf_pool.csv
  * 状态机 / 价值引擎 / 成本口径 全部独立重写
  * 实现路径刻意与主引擎不同(向量化预计算动量与乖离, 而非逐日切片循环),
    以便形成真正的交叉校验, 而不是复制粘贴

被审计对象声称的口径(用于对标, 不作为事实):
  窗口 2019-12-31=1.000 -> 2026-09-24, 1633 收益日, 双边成本 0.2%
  A 512880满仓 3.4374 | B 512040满仓 3.7447 | C 512040+价值引擎 4.7579

输出:
  out/audit_defense_recheck.json   独立复算结果(A/B/C 逐日净值 + 关键统计)
  stdout                           逐项审计对账
"""
import json
import math
import os
from collections import OrderedDict, Counter

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ADJ_CSV = os.path.join(HERE, "tdx_adj_long_2019_2026.csv")
POOL_CSV = os.path.join(HERE, "etf_pool.csv")
OUT_JSON = os.path.join(HERE, "out", "audit_defense_recheck.json")

# ---- 策略B 参数(取自任务书钉死的规则, 独立实现) ----
W = 20          # 动横20
MA = 20         # 乖离基准均线
STICKY = 1.0    # 粘性带: 领先 < 1.0pp 视为平手
ENTER_DEV, ENTER_LEAD = 12.0, 15.0
EXIT_DEV, EXIT_LEAD = 9.0, 10.0
MIN_DEF_DAYS = 5
COST = 0.002    # 每次换仓双腿(买0.1%+卖0.1%)
VE_MA, VE_BUY, VE_SELL = 200, 0.97, 1.06
CASH = "CASH"

NAMES = {
    "510300": "沪深300ETF", "159967": "创成长ETF", "563300": "中证2000ETF",
    "518880": "黄金ETF", "513100": "纳指ETF", "588170": "科创半导体ETF华夏",
    "516080": "创新药ETF易方达", "561700": "电力ETF博时", "513660": "恒生ETF华夏",
    "512880": "证券ETF国泰", "159259": "成长ETF易方达", "512200": "房地产ETF南方",
    "159543": "国证2000ETF工银", "159505": "国证2000ETF博时", "588000": "科创50ETF华夏",
    "588220": "科创100ETF鹏华", "510210": "上证指数ETF富国", "510050": "上证50ETF华夏",
    "159906": "深成长ETF大成", "512070": "证券保险ETF易方达",
    "512890": "红利低波ETF华泰柏瑞", "159957": "创业板ETF华夏",
    "159915": "创业板ETF易方达", "159949": "创业板50ETF华夏",
    "512040": "价值ETF富国", "000001": "上证指数", "000688": "科创50", "399006": "创业板指",
}

AUDIT = []      # 审计结论收集


def log(item, verdict, detail):
    AUDIT.append(dict(item=item, verdict=verdict, detail=detail))
    print(f"[{verdict:<4}] {item}\n        {detail}")


# ============================================================================
# 1. 独立数据装载
# ============================================================================
def load():
    df = pd.read_csv(ADJ_CSV, dtype={"code": str})
    df["code"] = df["code"].str.strip()
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    n_raw = len(df)
    df = df.sort_values(["code", "date"]).drop_duplicates(["code", "date"], keep="last")
    n_dedup = len(df)
    closes, ohlc = OrderedDict(), OrderedDict()
    for code, g in df.groupby("code", sort=False):
        g = g.sort_values("date")
        closes[code] = g.set_index("date")["close"].astype(float)
        ohlc[code] = g.set_index("date")[["open", "high", "low", "close"]].astype(float)
    panel = pd.DataFrame(closes).sort_index()
    return df, panel, ohlc, n_raw, n_dedup


def load_pools():
    p = pd.read_csv(POOL_CSV, dtype={"code": str}, encoding="utf-8-sig")
    p["code"] = p["code"].str.strip()
    p["role"] = p["role"].fillna("").astype(str).str.strip()
    rot = p.loc[p["role"] == "", "code"].tolist()
    deff = p.loc[p["role"] == "defensive", "code"].tolist()
    watch = p.loc[p["role"] == "watch", "code"].tolist()
    return rot, deff, watch


# ============================================================================
# 2. 独立向量化预计算(与主引擎的逐日切片循环实现路径不同)
# ============================================================================
_PRE_CACHE = {}


def precompute(panel, pool):
    """一次性算出动横 m[i,c] 与乖离 dev[i,c] (NaN 表示不可用)。
    动量与乖离用前缀和实现(全向量化), 与主引擎的逐日 pandas 切片循环路径不同。"""
    key = tuple(pool)
    if key in _PRE_CACHE:
        return _PRE_CACHE[key]
    cols = [c for c in pool if c in panel.columns]
    px = panel[cols].to_numpy(dtype=float)                # (T, C)
    T, Cn = px.shape
    mom = np.full((T, Cn), np.nan)
    dev = np.full((T, Cn), np.nan)
    for j in range(Cn):
        s = px[:, j]
        # 动横: P_t / P_{t-20} - 1
        with np.errstate(invalid="ignore", divide="ignore"):
            mom[W:, j] = s[W:] / s[:-W] - 1.0
        # 乖离: 20日均线(窗口内任一 NaN 则不可用) —— 用长度 T+1 的前缀和
        cs = np.concatenate([[0.0], np.cumsum(np.where(np.isnan(s), 0.0, s))])
        cbad = np.concatenate([[0], np.cumsum(np.isnan(s).astype(int))])
        for i in range(MA - 1, T):
            a, b = i - MA + 1, i + 1
            if cbad[b] == cbad[a] and s[i] == s[i]:
                dev[i, j] = s[i] / ((cs[b] - cs[a]) / MA) - 1.0
    res = (cols, mom * 100.0, dev * 100.0, px)
    _PRE_CACHE[key] = res
    return res


def value_engine(panel, code, dates, ma=VE_MA, buy_f=VE_BUY, sell_f=VE_SELL):
    """价值引擎: 独立重写的状态机。
    NaN(MA未成形/价格缺失)段沿用此前状态; 卖出后需重新跌破买线才买回。"""
    s = panel[code]
    ma_series = s.rolling(ma, min_periods=ma).mean()
    hold = False
    out = {}
    for d in dates:
        if d not in s.index:
            out[d] = False
            continue
        p = s.loc[d]
        m = ma_series.loc[d]
        if pd.isna(p) or pd.isna(m):
            out[d] = hold
            continue
        if not hold:
            if p < m * buy_f:
                hold = True
        else:
            if p > m * sell_f:
                hold = False
        out[d] = hold
    return pd.Series(out, dtype=bool)


# ============================================================================
# 3. 独立状态机
# ============================================================================
def run(panel, dates, start_i, end_i, pool, defensive, use_ve,
        buy=0.001, sell=0.001):
    cols, mom, dev, px = precompute(panel, pool)
    colidx = {c: j for j, c in enumerate(cols)}
    ve = value_engine(panel, defensive, dates) if use_ve else None
    T = len(dates)

    state = "ROT"
    def_days = 0
    held = CASH
    prev_eff = None
    nav = 1.0
    navs = [(dates[start_i], 1.0)]
    held_log, ret_log, nav_after_cost = [], [], []
    trades, stick_hits = [], 0
    px_all = panel  # 用全panel 取持仓收益

    for i in range(start_i, end_i):
        row = mom[i]
        valid = ~np.isnan(row)
        if not valid.any():
            ret = 0.0
            if held != CASH and held in px_all.columns:
                p0 = px_all[held].iloc[i]
                p1 = px_all[held].iloc[i + 1]
                if p0 == p0 and p1 == p1 and p0 != 0:
                    ret = p1 / p0 - 1.0
            nav *= (1.0 + ret)
            held_log.append((dates[i + 1], held))
            ret_log.append((dates[i + 1], held, ret))
            navs.append((dates[i + 1], nav))
            continue

        cand = [(row[j], c) for c, j in colidx.items() if valid[j]]
        cand.sort(key=lambda x: (-x[0], x[1]))
        rank = [c for _v, c in cand]
        m_of = {c: v for v, c in cand}
        raw_head = rank[0]
        lead = (m_of[raw_head] - m_of[rank[1]]) if len(rank) > 1 else float("nan")

        # 粘性带: 锚点 = 链式 prev_eff, 且必须仍在今日排名池内
        eff_head = raw_head
        tied = False
        if prev_eff is not None and prev_eff not in rank:
            prev_eff = None
        if prev_eff is not None and lead == lead and lead < STICKY:
            eff_head = prev_eff
            tied = True
            stick_hits += 1
        dev_eff = dev[i, colidx[eff_head]]

        if state == "ROT":
            if (dev_eff == dev_eff and dev_eff > ENTER_DEV) or \
               (lead == lead and lead > ENTER_LEAD):
                state, def_days = "DEF", 0
        else:
            def_days += 1
            if (dev_eff == dev_eff and dev_eff < EXIT_DEV) and \
               (lead == lead and lead < EXIT_LEAD) and def_days >= MIN_DEF_DAYS:
                state, def_days = "ROT", 0

        if state == "ROT":
            target = eff_head
        else:
            target = defensive if (not use_ve or bool(ve.loc[dates[i]])) else CASH

        switched = target != held
        if switched:
            nav *= (1.0 - (buy + sell))
            trades.append(dict(date=str(dates[i].date()), frm=held, to=target,
                               state=state, tie=bool(tied),
                               lead=(None if lead != lead else round(lead, 2)),
                               dev=(None if dev_eff != dev_eff else round(dev_eff, 2)),
                               nav=round(nav, 6)))
        ret = 0.0
        if target in px_all.columns:
            p0 = px_all[target].iloc[i]
            p1 = px_all[target].iloc[i + 1]
            if p0 == p0 and p1 == p1 and p0 != 0:
                ret = p1 / p0 - 1.0
        nav *= (1.0 + ret)
        held = target
        prev_eff = eff_head
        held_log.append((dates[i + 1], target))
        ret_log.append((dates[i + 1], target, ret))
        navs.append((dates[i + 1], nav))

    navser = pd.Series({d.strftime("%Y-%m-%d"): float(v) for d, v in navs})
    return dict(navs=navser, trades=trades, held_log=held_log, ret_log=ret_log,
                stick_hits=stick_hits,
                cash_days=sum(1 for _d, t in held_log if t == CASH))


def bench(panel, dates, start_i, end_i, code):
    s = panel[code]
    base_i = next((i for i in range(start_i, end_i + 1)
                   if s.iloc[i] == s.iloc[i] and s.iloc[i] != 0), None)
    if base_i is None:
        return pd.Series(dtype=float)
    base = s.iloc[base_i]
    out = {dates[i].strftime("%Y-%m-%d"):
           (float(s.iloc[i] / base) if s.iloc[i] == s.iloc[i] else None)
           for i in range(start_i, end_i + 1)}
    return pd.Series(out).ffill()


def stats(nav):
    v = nav.to_numpy(dtype=float)
    ret = v[-1] / v[0] - 1.0
    n = len(v) - 1
    ann = (v[-1] / v[0]) ** (244.0 / n) - 1.0 if n > 0 else float("nan")
    peak = np.maximum.accumulate(v)
    mdd = float((v / peak - 1.0).min())
    daily = np.diff(v) / v[:-1]
    vol = float(np.std(daily, ddof=1) * np.sqrt(244))
    neg = daily[daily < 0]
    dvol = float(np.std(neg, ddof=1) * np.sqrt(244)) if len(neg) > 1 else float("nan")
    sh = ann / vol if vol == vol and vol > 0 else float("nan")
    so = ann / dvol if dvol == dvol and dvol > 0 else float("nan")
    return dict(total=float(ret), ann=float(ann), mdd=mdd, vol=vol,
                sharpe=(float(sh) if sh == sh else None),
                sortino=(float(so) if so == so else None), days=int(n))


def yearly_chain(nav):
    """链式年度收益: 上一年末净值 -> 本年末净值 (真实年度收益)。"""
    out = []
    prev = None
    for yr, g in nav.groupby(nav.index.str[:4]):
        start = g.iloc[0]
        end = g.iloc[-1]
        base = prev if prev is not None else start
        out.append(dict(year=yr, ret=float(end / base - 1.0),
                        ret_naive=float(end / start - 1.0),
                        end=float(end), days=int(len(g) - 1)))
        prev = end
    return out


def defense_runs(held_log, defensive):
    runs, cur = [], 0
    cash_runs, ccur = [], 0
    for _d, t in held_log:
        if t == defensive or t == CASH:
            cur += 1
            if t == CASH:
                ccur += 1
            elif ccur:
                cash_runs.append(ccur)
                ccur = 0
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    if ccur:
        cash_runs.append(ccur)
    return dict(days=int(sum(runs)), episodes=len(runs),
                longest=int(max(runs)) if runs else 0,
                cash_days=int(sum(cash_runs)), cash_episodes=len(cash_runs),
                cash_longest=int(max(cash_runs)) if cash_runs else 0)


def held_cash_outside_defense(held_log, defensive):
    """检查是否存在"防守期外的空仓"。防守期由状态机决定, 但这里用持仓口径反查:
    CASH 只能出现在 state==DEF 的日子。返回所有 CASH 日的日期列表。"""
    return [d for d, t in held_log if t == CASH]


# ============================================================================
# 4. 数据防造假抽查
# ============================================================================
def data_audit(ohlc, code="512040"):
    g = ohlc[code].dropna(subset=["close"])
    first, last = g.index[0], g.index[-1]
    n = len(g)
    # 交易日历基准: 取全市场(000001) 的日期序列
    cal = ohlc["000001"].index
    cal = cal[(cal >= first) & (cal <= last)]
    missing = sorted(set(cal) - set(g.index))
    nonpos = g.index[(g["close"] <= 0)].tolist()
    ohlc_bad = g.index[((g["high"] < g[["open", "close", "low"]].max(axis=1) - 1e-9) |
                        (g["low"] > g[["open", "close", "high"]].min(axis=1) + 1e-9))].tolist()
    r = g["close"].pct_change().dropna()
    big = r[(r > 0.20) | (r < -0.20)]
    top10 = r.abs().sort_values(ascending=False).head(10)
    return dict(code=code, n_bars=n, first=str(first.date()), last=str(last.date()),
                calendar_bars=len(cal), internal_gaps=[str(d.date()) for d in missing],
                n_gaps=len(missing), nonpos=[str(d.date()) for d in nonpos],
                ohlc_inconsistent=[str(d.date()) for d in ohlc_bad],
                max_up=float(r.max()), max_down=float(r.min()),
                max_up_date=str(r.idxmax().date()), max_down_date=str(r.idxmin().date()),
                n_abs_gt20=int(len(big)),
                big_moves=[(str(d.date()), round(float(v) * 100, 2)) for d, v in big.items()],
                top10_abs=[(str(d.date()), round(float(v) * 100, 2)) for d, v in top10.items()],
                ma200_first_valid=(None if pd.isna(g["close"].rolling(200).mean().dropna().iloc[0])
                                   else str(g["close"].rolling(200).mean().dropna().index[0].date())))


def ma200_spotcheck(panel, code, dates, picks):
    s = panel[code]
    ma = s.rolling(200).mean()
    hold, res = False, []
    seq = []
    for d in dates:
        p, m = s.loc[d], ma.loc[d]
        if pd.isna(p) or pd.isna(m):
            st = hold
        else:
            if not hold and p < m * VE_BUY:
                hold = True
            elif hold and p > m * VE_SELL:
                hold = False
            st = hold
        seq.append((d, p, m, st))
    for d in picks:
        i = dates.get_loc(d)
        if i < 199:
            res.append(dict(date=str(d.date()), note="MA200 未成形"))
            continue
        w = s.iloc[i - 199:i + 1]
        p, m = s.iloc[i], w.mean()
        naive = float(np.mean(w.to_numpy(dtype=float)))
        res.append(dict(date=str(d.date()), close=round(float(p), 4),
                        ma200=round(float(m), 6),
                        ma200_numpy_crosscheck=round(naive, 6),
                        ma200_diff=abs(float(m) - naive),
                        buy_line=round(float(m) * VE_BUY, 4),
                        sell_line=round(float(m) * VE_SELL, 4),
                        ratio=round(float(p) / float(m), 6),
                        engine_hold=bool(seq[i][3])))
    return res


# ============================================================================
# MAIN
# ============================================================================
def main():
    print("=" * 96)
    print("独立审计复算 · 策略B 防守改造 A/B/C  (不 import 主引擎)")
    print("=" * 96)
    df, panel, ohlc, n_raw, n_dedup = load()
    rot, deff, watch = load_pools()
    dates = panel.index
    start_i = int(dates.searchsorted(pd.Timestamp("2019-12-31")))
    end_i = min(int(dates.searchsorted(pd.Timestamp("2026-12-31"))), len(dates) - 1)
    win_days = end_i - start_i
    print(f"数据: {len(df)} 行(去重前 {n_raw}) {dates[0].date()} ~ {dates[-1].date()} "
          f"共 {len(dates)} 个交易日, {panel.shape[1]} 只标的")
    print(f"窗口: {dates[start_i].date()} = 1.000 -> {dates[end_i].date()}  收益日 {win_days}")
    print(f"主选池({len(rot)}): {' '.join(rot)}")
    print(f"etf_pool defensive={deff}  watch({len(watch)})\n")

    watch14 = [c for c in watch if c not in rot and c not in deff and c != "512880"]
    assert len(rot) == 9, rot
    assert len(watch14) == 14, watch14
    groups = OrderedDict([
        ("main", ("主选池(9只)", rot)),
        ("watch", ("备选池(14只)", watch14)),
        ("combo", ("参照组·主+备(23只)", rot + watch14)),
    ])
    SCEN = OrderedDict([
        ("A_old_512880_full", dict(defensive="512880", use_ve=False, label="A 512880 满仓")),
        ("B_512040_full", dict(defensive="512040", use_ve=False, label="B 512040 满仓")),
        ("C_new_512040_ve", dict(defensive="512040", use_ve=True, label="C 512040+价值引擎")),
    ])

    # ---------- 任务 2: 粘性带锚点 / 过热乖离 约定核对 ----------
    # 变体测试: 故意改坏任一约定, 看 A 组能否复现 3.4374
    def run_variant(panel, dates, si, ei, pool, deff_code, use_ve,
                    anchor="prev_eff", devsrc="eff"):
        cols, mom, dev, _px = precompute(panel, pool)
        colidx = {c: j for j, c in enumerate(cols)}
        ve = value_engine(panel, deff_code, dates) if use_ve else None
        state, def_days, held, prev_eff, nav = "ROT", 0, CASH, None, 1.0
        for i in range(si, ei):
            row = mom[i]
            if np.isnan(row).all():
                continue
            cand = sorted([(row[j], c) for c, j in colidx.items() if row[j] == row[j]],
                          key=lambda x: (-x[0], x[1]))
            rank = [c for _v, c in cand]
            m_of = {c: v for v, c in cand}
            raw_head = rank[0]
            lead = (m_of[raw_head] - m_of[rank[1]]) if len(rank) > 1 else float("nan")
            if prev_eff is not None and prev_eff not in rank:
                prev_eff = None
            eff_head = prev_eff if (anchor == "prev_eff" and prev_eff is not None
                                    and lead == lead and lead < STICKY) else raw_head
            dsrc = eff_head if devsrc == "eff" else raw_head
            dev_eff = dev[i, colidx[dsrc]]
            if state == "ROT":
                if (dev_eff == dev_eff and dev_eff > ENTER_DEV) or \
                   (lead == lead and lead > ENTER_LEAD):
                    state, def_days = "DEF", 0
            else:
                def_days += 1
                if (dev_eff == dev_eff and dev_eff < EXIT_DEV) and \
                   (lead == lead and lead < EXIT_LEAD) and def_days >= MIN_DEF_DAYS:
                    state, def_days = "ROT", 0
            target = eff_head if state == "ROT" else (
                deff_code if (not use_ve or bool(ve.loc[dates[i]])) else CASH)
            if target != held:
                nav *= (1 - COST)
            r = 0.0
            if target in panel.columns:
                p0, p1 = panel[target].iloc[i], panel[target].iloc[i + 1]
                if p0 == p0 and p1 == p1 and p0 != 0:
                    r = p1 / p0 - 1.0
            nav *= (1 + r)
            held = target
            prev_eff = eff_head
        return nav

    v_anchor = run_variant(panel, dates, start_i, end_i, rot, "512880", False,
                           anchor="raw_head", devsrc="eff")
    v_dev = run_variant(panel, dates, start_i, end_i, rot, "512880", False,
                        anchor="prev_eff", devsrc="raw")
    v_base = run_variant(panel, dates, start_i, end_i, rot, "512880", False)
    print("\n" + "-" * 96)
    print("【任务2】策略B 两条实现约定 · 变体测试 (A组 512880 满仓, 主选池)")
    print(f"  基准 (链式prev_eff + dev(eff_head)) : {v_base:.4f}")
    print(f"  改坏① 锚点改 raw_head              : {v_anchor:.4f}  (差 {v_anchor-v_base:+.4f})")
    print(f"  改坏② 乖离改 dev(raw_head)         : {v_dev:.4f}  (差 {v_dev-v_base:+.4f})")
    print("-" * 96)

    # ---------- 跑全部 3 池 × 3 场景 ----------
    results, curves, holdlogs = {}, {}, {}
    for gname, (glabel, pool) in groups.items():
        results[gname] = dict(label=glabel, pool=pool, runs={})
        for sk, sv in SCEN.items():
            r = run(panel, dates, start_i, end_i, pool, sv["defensive"], sv["use_ve"])
            st = stats(r["navs"])
            dfn = defense_runs(r["held_log"], sv["defensive"])
            cash_dates = held_cash_outside_defense(r["held_log"], sv["defensive"])
            hc = Counter(t for _d, t in r["held_log"])
            results[gname]["runs"][sk] = dict(
                label=sv["label"], defensive=sv["defensive"],
                value_engine=sv["use_ve"], final=float(r["navs"].iloc[-1]),
                stat=st, trades_n=len(r["trades"]), stick_hits=r["stick_hits"],
                defense=dfn, held=[(k, n) for k, n in hc.most_common()],
                yearly=yearly_chain(r["navs"]),
                cash_date_first_last=(cash_dates[0].strftime("%Y-%m-%d"),
                                      cash_dates[-1].strftime("%Y-%m-%d")) if cash_dates else None,
                cash_n=len(cash_dates),
                trades=r["trades"],
                nav={d: round(float(v), 6) for d, v in r["navs"].items()})
            curves[(gname, sk)] = r["navs"]
            holdlogs[(gname, sk)] = r["held_log"]
        results[gname]["bench_def"] = {}
        for bc in ("512040", "512880"):
            cv = bench(panel, dates, start_i, end_i, bc)
            results[gname]["bench_def"][bc] = dict(name=NAMES.get(bc, bc),
                                                   stat=stats(cv), final=float(cv.iloc[-1]),
                                                   nav={d: round(float(v), 6)
                                                        for d, v in cv.items()})

    # ---------- 价值引擎自身 ----------
    ve = value_engine(panel, "512040", dates)
    ve_win = ve.loc[dates[start_i]:dates[end_i]]
    flips = int((ve_win.astype(int).diff().fillna(0) != 0).sum())
    ve_info = dict(code="512040", ma=VE_MA, buy=VE_BUY, sell=VE_SELL,
                   hold_days_in_window=int(ve_win.sum()),
                   total_days_in_window=int(len(ve_win)),
                   hold_pct=round(float(ve_win.sum()) / len(ve_win) * 100, 2),
                   state_flips=flips,
                   ma200_first_valid=str(panel["512040"].rolling(200).mean()
                                         .dropna().index[0].date()),
                   series={d.strftime("%Y-%m-%d"): bool(v)
                           for d, v in ve.loc[dates[start_i]:dates[end_i]].items()})

    # ---------- 打印对账表 ----------
    # 主 agent 声称值: 优先从其输出 defense_ablation.json 读取(仅作"声称"一侧并列,
    # 绝不作为复算输入); 缺失则回落到任务书给出的主选池数字。
    ABL = os.path.join(HERE, "out", "defense_ablation.json")
    claim_by_group = {g: {} for g in groups}
    claim_src = "任务书"
    if os.path.exists(ABL):
        with open(ABL, "r", encoding="utf-8") as f:
            _abl = json.load(f)
        claim_src = os.path.basename(ABL)
        for g in groups:
            runs = _abl.get("results", {}).get(g, {}).get("runs", {})
            for sk in SCEN:
                if sk in runs:
                    r = runs[sk]
                    claim_by_group[g][sk] = dict(
                        final=r["nav"][list(r["nav"].keys())[-1]],
                        total=r["stat"]["total"] * 100, mdd=r["stat"]["mdd"] * 100,
                        sharpe=r["stat"]["sharpe"], trades=r["trades_n"],
                        dfn=r["defense"]["days"], cash=r["defense"]["cash_days"])
        _ve = _abl.get("value_engine", {})
        claim_ve = dict(hold=_ve.get("hold_days_in_window"),
                        total=_ve.get("total_days_in_window"),
                        pct=_ve.get("hold_pct"), flips=_ve.get("state_flips"))
    else:
        claim_ve = dict(hold=529, total=1634, pct=32.4, flips=15)
    CLAIM_BOOK = {
        "A_old_512880_full": dict(final=3.4374, total=243.74, mdd=-27.42, sharpe=0.74,
                                  trades=218, dfn=161, cash=0),
        "B_512040_full": dict(final=3.7447, total=274.47, mdd=-28.28, sharpe=0.85,
                              trades=218, dfn=161, cash=0),
        "C_new_512040_ve": dict(final=4.7579, total=375.79, mdd=-23.38, sharpe=1.06,
                                trades=221, dfn=161, cash=127),
    }
    for g in groups:
        for sk in SCEN:
            claim_by_group[g].setdefault(sk, CLAIM_BOOK[sk])

    print("\n" + "=" * 110)
    print(f"【任务1】独立复算 vs 主 agent 声称 (声称来源: {claim_src})")
    print("=" * 110)
    cmp_rows = []
    for gname, (glabel, _p) in groups.items():
        print(f"\n【{glabel}】")
        hdr = (f"{'场景':<22}{'期末净值(我)':>13}{'声称':>10}{'残差':>11}"
               f"{'收益(我)':>12}{'声称':>10}{'回撤(我)':>11}{'声称':>9}"
               f"{'夏普(我)':>10}{'声称':>7}{'换仓(我/声称)':>15}"
               f"{'防守日(我/声称)':>16}{'持币(我/声称)':>15}")
        print(hdr)
        print("-" * len(hdr))
        for sk in SCEN:
            R = results[gname]["runs"][sk]
            st, dfn = R["stat"], R["defense"]
            cl = claim_by_group[gname][sk]
            sh = st["sharpe"] if st["sharpe"] is not None else float("nan")
            csh = cl["sharpe"] if cl["sharpe"] is not None else float("nan")
            print(f"{sk:<22}{R['final']:>13.6f}{cl['final']:>10.4f}"
                  f"{R['final']-cl['final']:>+11.6f}"
                  f"{st['total']*100:>11.4f}%{cl['total']:>9.2f}%"
                  f"{st['mdd']*100:>10.4f}%{cl['mdd']:>8.2f}%"
                  f"{sh:>10.4f}{csh:>7.2f}"
                  f"{R['trades_n']:>8}/{cl['trades']:<6}"
                  f"{dfn['days']:>9}/{cl['dfn']:<6}"
                  f"{dfn['cash_days']:>8}/{cl['cash']:<6}")
            cmp_rows.append(dict(group=gname, scen=sk, mine_final=R["final"],
                                 claim_final=cl["final"],
                                 resid=R["final"] - cl["final"],
                                 resid_pct=(R["final"] - cl["final"]) / cl["final"] * 100,
                                 mine_total=st["total"] * 100, claim_total=cl["total"],
                                 mine_mdd=st["mdd"] * 100, claim_mdd=cl["mdd"],
                                 mine_sharpe=sh, claim_sharpe=csh,
                                 mine_trades=R["trades_n"], claim_trades=cl["trades"],
                                 mine_def=dfn["days"], claim_def=cl["dfn"],
                                 mine_cash=dfn["cash_days"], claim_cash=cl["cash"]))
        b = results[gname]["bench_def"]
        for bc in ("512040", "512880"):
            print(f"  [基准] 买入持有 {bc} {NAMES[bc]}: 期末 {b[bc]['final']:.4f} "
                  f"收益 {b[bc]['stat']['total']*100:+.2f}% 回撤 {b[bc]['stat']['mdd']*100:.2f}%")

    max_resid = max(abs(r["resid"]) for r in cmp_rows)
    max_resid_pct = max(abs(r["resid_pct"]) for r in cmp_rows)
    print("\n" + "=" * 110)
    print(f"【任务1 总结】9 组(3池x 3场景)全部比对完成")
    print(f"  期末净值最大绝对残差 {max_resid:.8f}  最大相对残差 {max_resid_pct:.6f}%")
    print(f"  (四舍五入到 4 位小数后是否全部一致: "
          f"{all(round(r['mine_final'],4)==round(r['claim_final'],4) for r in cmp_rows)})")
    print("=" * 110)

    print("\n" + "=" * 96)
    print("【价值引擎自身】512040 窗口内 '该买' 状态")
    print(f"  我: {ve_info['hold_days_in_window']}/{ve_info['total_days_in_window']} 日 = "
          f"{ve_info['hold_pct']}%  翻转 {ve_info['state_flips']} 次  "
          f"| 声称: {claim_ve['hold']}/{claim_ve['total']} = {claim_ve['pct']}%, "
          f"翻转 {claim_ve['flips']}")
    print("=" * 96)

    # ---------- 任务 4: 持币日 127 深查 ----------
    print("\n" + "=" * 96)
    print("【任务4】持币日核查")
    print("=" * 96)
    cash_checks = []
    for gname in groups:
        for sk in ("A_old_512880_full", "B_512040_full", "C_new_512040_ve"):
            R = results[gname]["runs"][sk]
            dfn = R["defense"]
            # 持币日是否全部落在防守期: 用 A 组的防守期日集合做"防守期"定义
            a_def = set()
            if gname == "main" or True:
                ref = holdlogs[(gname, "A_old_512880_full")]
                a_def = set(d for d, t in ref if t in ("512880", "512040", CASH))
            cashd = set(d for d, t in holdlogs[(gname, sk)] if t == CASH)
            outside = sorted(cashd - a_def)
            # 持币日收益是否恒为 0
            rl = holdlogs[(gname, sk)]
            # 重新取 ret_log
            cash_ret_nonzero = 0
            if sk == "C_new_512040_ve":
                rr = run(panel, dates, start_i, end_i, groups[gname][1],
                         SCEN[sk]["defensive"], SCEN[sk]["use_ve"])
                for d, t, ret in rr["ret_log"]:
                    if t == CASH and abs(ret) > 1e-15:
                        cash_ret_nonzero += 1
            info = dict(group=gname, scen=sk, defense_days=dfn["days"],
                        defense_episodes=dfn["episodes"],
                        cash_days=dfn["cash_days"], cash_episodes=dfn["cash_episodes"],
                        cash_longest=dfn["cash_longest"],
                        cash_outside_defense=len(outside),
                        cash_outside_dates=[d.strftime("%Y-%m-%d") for d in outside][:20],
                        held_defensive_days=dfn["days"] - dfn["cash_days"],
                        cash_ret_nonzero=cash_ret_nonzero)
            cash_checks.append(info)
            print(f"  {gname:<6}{sk:<22} 防守期 {dfn['days']:>4}日/{dfn['episodes']:>3}段 "
                  f"| 持防守位 {dfn['days']-dfn['cash_days']:>4}日 "
                  f"| 持币 {dfn['cash_days']:>4}日/{dfn['cash_episodes']:>3}段/最长{dfn['cash_longest']:>3} "
                  f"| 防守期外空仓 {len(outside):>3}日 | 持币日非零收益 {cash_ret_nonzero}")

    # ---------- 任务 7: 归因 ----------
    print("\n" + "=" * 96)
    print("【任务7】归因核查")
    print("=" * 96)
    attribution = []
    for gname in groups:
        a = results[gname]["runs"]["A_old_512880_full"]
        b = results[gname]["runs"]["B_512040_full"]
        c = results[gname]["runs"]["C_new_512040_ve"]
        d1 = (b["final"] / a["final"] - 1) * 100
        d2 = (c["final"] / b["final"] - 1) * 100
        d1pp = (b["stat"]["total"] - a["stat"]["total"]) * 100
        d2pp = (c["stat"]["total"] - b["stat"]["total"]) * 100
        # C/B 增量是否 100% 来自"跳过持币日收益" —— 用 ret_log 精确重建
        rr_c = run(panel, dates, start_i, end_i, groups[gname][1], "512040", True)
        rr_b = run(panel, dates, start_i, end_i, groups[gname][1], "512040", False)
        ratio = 1.0
        n_cash = 0
        for (_d, _t, rc), (_d2, _t2, rb) in zip(rr_c["ret_log"], rr_b["ret_log"]):
            if _t == CASH:
                n_cash += 1
                ratio *= (1.0 / (1.0 + rb))
        n_cost_diff = c["trades_n"] - b["trades_n"]
        pred = (ratio * (1 - 0.002) ** n_cost_diff - 1) * 100
        attribution.append(dict(group=gname,
                                swap_rel_pct=d1, swap_pp=d1pp,
                                ve_rel_pct=d2, ve_pp=d2pp,
                                cash_skip_ratio_pct=(ratio - 1) * 100,
                                predicted_c_vs_b_pct=pred,
                                actual_c_vs_b_pct=d2,
                                unexplained=d2 - pred,
                                extra_trades_c_vs_b=n_cost_diff,
                                claim_swap_pp=30.73, claim_ve_pp=101.32))
        print(f"  {gname:<6} 换标的 B/A 相对 {d1:+.2f}% (绝对 {d1pp:+.2f}pp) "
              f"| 价值引擎 C/B 相对 {d2:+.2f}% (绝对 {d2pp:+.2f}pp)")
        print(f"         C/B 增量分解: 跳过{ n_cash}个持币日的512040收益 -> {ratio:.6f} "
              f"({(ratio-1)*100:+.2f}%) ; 多付成本 {n_cost_diff}次换仓 "
              f"-> 预测 {(ratio*(1-0.002)**n_cost_diff-1)*100:+.2f}% ; 实测 {d2:+.2f}% ; "
              f"残差 {d2-pred:+.4f}pp")

    # ---------- 任务 5: 数据抽查 ----------
    print("\n" + "=" * 96)
    print("【任务5】数据防造假抽查 · 512040")
    print("=" * 96)
    da = data_audit(ohlc, "512040")
    print(f"  根数 {da['n_bars']}  首 {da['first']}  末 {da['last']}  "
          f"(同期000001交易日历 {da['calendar_bars']})")
    print(f"  内部缺口 {da['n_gaps']} 天 {da['internal_gaps'][:10]}")
    print(f"  非正价格 {len(da['nonpos'])}  OHLC不一致 {len(da['ohlc_inconsistent'])}")
    print(f"  最大单日涨 {da['max_up']*100:+.2f}% ({da['max_up_date']})  "
          f"最大单日跌 {da['max_down']*100:+.2f}% ({da['max_down_date']})")
    print(f"  |日涨跌|>20% 的天数: {da['n_abs_gt20']} -> {da['big_moves']}")
    print(f"  绝对涨跌幅 Top10: {da['top10_abs']}")
    print(f"  MA200 首个有效日: {da['ma200_first_valid']}")

    # 与 512880 / 其他对比, 看是否 512040 独有异常
    dup_detail = {}
    for c2 in ("512880", "510300", "000001"):
        d2_ = data_audit(ohlc, c2)
        dup_detail[c2] = dict(n=d2_["n_bars"], max_up=d2_["max_up"],
                              max_down=d2_["max_down"], gaps=d2_["n_gaps"],
                              nonpos=len(d2_["nonpos"]),
                              ohlc_bad=len(d2_["ohlc_inconsistent"]))
    print(f"  对照: {dup_detail}")

    rng = np.random.default_rng(20260924)
    cand = [d for d in dates[start_i:end_i + 1] if d >= pd.Timestamp("2020-06-01")]
    picks = sorted(rng.choice(cand, size=3, replace=False).tolist())
    spots = ma200_spotcheck(panel, "512040", dates, picks)
    print(f"\n  随机抽 3 个交易日 (seed=20260924): {[d.strftime('%Y-%m-%d') for d in picks]}")
    for sp in spots:
        print(f"    {sp['date']} 收盘 {sp.get('close')}  MA200={sp.get('ma200')} "
              f"(numpy交叉校验 {sp.get('ma200_numpy_crosscheck')}, 差 {sp.get('ma200_diff'):.2e})  "
              f"买线 {sp.get('buy_line')} 卖线 {sp.get('sell_line')}  "
              f"收盘/MA200={sp.get('ratio')}  引擎该买={sp.get('engine_hold')}")

    # ---------- 任务 8: 分年度 ----------
    print("\n" + "=" * 96)
    print("【任务8】分年度收益对比 (链式: 上一年末 -> 本年末)")
    print("=" * 96)
    yrs = sorted({y["year"] for y in results["main"]["runs"]["C_new_512040_ve"]["yearly"]})
    print(f"{'场景':<22}" + "".join(f"{y:>10}" for y in yrs))
    print("-" * (22 + 10 * len(yrs)))
    yearly_tbl = {}
    for gname in groups:
        for sk in SCEN:
            ym = {y["year"]: y["ret"] for y in results[gname]["runs"][sk]["yearly"]}
            yearly_tbl[(gname, sk)] = ym
            if gname == "main":
                print(f"{sk:<22}" + "".join(f"{ym.get(y, float('nan'))*100:>9.1f}%" for y in yrs))
    print("\n  主选池 逐场景年度超额 (B-A / C-B / C-A):")
    for gname in ("main",):
        print(f"  {'年':<8}{'A':>9}{'B':>9}{'C':>9}{'B-A':>9}{'C-B':>9}{'C-A':>9}")
        for y in yrs:
            a = yearly_tbl[(gname, "A_old_512880_full")].get(y)
            b = yearly_tbl[(gname, "B_512040_full")].get(y)
            c = yearly_tbl[(gname, "C_new_512040_ve")].get(y)
            if a is None or b is None or c is None:
                continue
            print(f"  {y:<8}{a*100:>8.1f}%{b*100:>8.1f}%{c*100:>8.1f}%"
                  f"{(b-a)*100:>8.1f}pp{(c-b)*100:>8.1f}pp{(c-a)*100:>8.1f}pp")

    # ---------- 成本口径核查 ----------
    print("\n" + "=" * 96)
    print("【任务3】成本口径核查")
    print("=" * 96)
    cost_probe = {}
    for gname in groups:
        for sk in SCEN:
            R = results[gname]["runs"][sk]
            n = R["trades_n"]
            drag = 1 - (1 - COST) ** n
            cost_probe[f"{gname}/{sk}"] = dict(
                trades=n, cash_entries=sum(1 for t in R["trades"] if t["to"] == CASH),
                cash_exits=sum(1 for t in R["trades"] if t["frm"] == CASH),
                def_entries=sum(1 for t in R["trades"] if t["frm"] != CASH
                                and t["to"] == "512040"),
                drag_pct=drag * 100)
    for k, v in cost_probe.items():
        print(f"  {k:<34} 换仓 {v['trades']:>4} 次 (进CASH {v['cash_entries']}, 出CASH {v['cash_exits']})"
              f"  累计成本拖累 {v['drag_pct']:.2f}%")
    # 零成本对照
    zero = {}
    for sk in SCEN:
        r0 = run(panel, dates, start_i, end_i, rot, SCEN[sk]["defensive"],
                 SCEN[sk]["use_ve"], buy=0.0, sell=0.0)
        zero[sk] = float(r0["navs"].iloc[-1])
    print("  零成本对照(主选池): " + "  ".join(
        f"{sk.split('_')[0]}={zero[sk]:.4f}" for sk in SCEN))
    print(f"  成本模型核验: C-A 零成本 {zero['C_new_512040_ve']-zero['A_old_512880_full']:+.4f} "
          f"-> 0.2%成本下 {results['main']['runs']['C_new_512040_ve']['final']-results['main']['runs']['A_old_512880_full']['final']:+.4f}")

    # ---------- 逐日对账: 净值序列 + 持仓/换仓 ----------
    print("\n" + "=" * 96)
    print("【补充】逐日净值序列对账 + 换仓明细对账 (vs defense_ablation.json)")
    print("=" * 96)
    daily_recon = {}
    with open(ABL, "r", encoding="utf-8") as f:
        _abl2 = json.load(f)
    for gname in groups:
        for sk in SCEN:
            ref = _abl2["results"][gname]["runs"][sk]
            mine = results[gname]["runs"][sk]["nav"]
            refn = ref["nav"]
            keys = sorted(set(mine) | set(refn))
            dmax, darg, nmiss, nbig = 0.0, None, 0, 0
            for d in keys:
                a = mine.get(d)
                b = refn.get(d)
                if a is None or b is None:
                    nmiss += 1
                    continue
                d0 = abs(a - b)
                if d0 > dmax:
                    dmax, darg = d0, d
                if d0 > 5e-6:
                    nbig += 1
            # 换仓明细对账
            rt = [(t["date"], t["frm"], t["to"]) for t in ref["trades"]]
            mt = [(t["date"], t["frm"], t["to"]) for t in results[gname]["runs"][sk]["trades"]]
            trade_mismatch = sum(1 for x, y in zip(rt, mt) if x != y) + abs(len(rt) - len(mt))
            daily_recon[f"{gname}|{sk}"] = dict(
                nav_points=len(keys), nav_missing=nmiss,
                max_abs_diff=dmax, max_diff_date=darg,
                n_days_diff_gt_5e6=nbig,
                claim_trades=len(rt), my_trades=len(mt),
                trade_list_mismatch=trade_mismatch,
                verdict=("逐日净值完全一致(四舍五入口径)" if dmax <= 5e-6 and trade_mismatch == 0
                         else "存在差异"))
            print(f"  {gname:<6}{sk:<22} 净值点 {len(keys):>5}  最大日残差 {dmax:.2e}"
                  f" ({darg})  超5e-6天数 {nbig:>3}  换仓 {len(mt)}/{len(rt)}  "
                  f"明细不符 {trade_mismatch:>3}  -> {daily_recon[f'{gname}|{sk}']['verdict']}")

    # ---------- 成交时点口径验证 ----------
    print("\n" + "=" * 96)
    print("【任务3 补充】成交时点口径验证: 第i日收盘算信号 -> 同日收盘换仓 -> 第i+1日起计收益")
    print("=" * 96)
    # 手工追踪主选池 A 组前若干笔, 验证 (换仓日, 计收益区间) 与 nav 增量自洽
    rr_a = run(panel, dates, start_i, end_i, rot, "512880", False)
    timing_rows = []
    idx = {d: i for i, d in enumerate(dates)}
    for t in rr_a["trades"][:5]:
        d0 = pd.Timestamp(t["date"])
        i0 = idx[d0]
        held_next = [x for x in rr_a["held_log"] if x[0] == dates[i0 + 1]]
        # nav 在换仓日之后的第一日才体现收益
        nav_before = rr_a["navs"].iloc[i0 - start_i]
        nav_after = rr_a["navs"].iloc[i0 + 1 - start_i]
        tgt = t["to"]
        p0 = panel[tgt].iloc[i0]
        p1 = panel[tgt].iloc[i0 + 1]
        eret = p1 / p0 - 1.0
        recomposed = nav_after / (nav_before * (1 - 0.002))
        timing_rows.append(dict(date=t["date"], frm=t["frm"], to=tgt,
                                nav_before=float(nav_before), nav_after=float(nav_after),
                                nav_recorded_after_cost=float(t["nav"]),
                                cost=0.002,
                                expected_return=eret,
                                nav_recomposed=float(recomposed),
                                diff=float(recomposed - (1 + eret)),
                                holds_on_next_day=held_next[0][1] if held_next else None))
        print(f"  换仓 {t['date']} {t['frm']}->{tgt}: nav前 {nav_before:.6f} "
              f"扣成本后 {t['nav']:.6f}  次日nav {nav_after:.6f}")
        print(f"      复算 nav_next/(nav_before*0.998) = {recomposed:.9f}  "
              f"vs 1+r = {1+eret:.9f}  差 {recomposed-(1+eret):+.2e}  "
              f"i+1日实际持仓={held_next[0][1] if held_next else '-'}")
    max_tim = max(abs(r["diff"]) for r in timing_rows)
    print(f"  -> 时点口径自洽性: 最大差 {max_tim:.2e} (应为 0, 表示 i+1 日收益确由 i->i+1 收盘价计算)")

    # ---------- 任务4 强化: state==DEF 日志 ----------
    # 重新实现带 state 日志的 run以取得 DEF 日集合
    def run_with_state(panel, dates, si, ei, pool, deff_code, use_ve):
        r = run(panel, dates, si, ei, pool, deff_code, use_ve)
        # 由 C 组 trades 中的 state 字段 + 持仓日志重建 DEF 日
        return r

    print("\n" + "=" * 96)
    print("【任务4 强化】state==DEF 日志 vs 持币日 (直接用状态机 state 判定)")
    print("=" * 96)
    # 独立重建 DEF 日: 用与 run 相同的状态机, 记录 state
    def def_days_set(pool, deff_code):
        cols, mom, dev, _ = precompute(panel, pool)
        colidx = {c: j for j, c in enumerate(cols)}
        state, def_days, prev_eff = "ROT", 0, None
        out = []
        for i in range(start_i, end_i):
            row = mom[i]
            if np.isnan(row).all():
                out.append((dates[i + 1], state))
                continue
            cand = sorted([(row[j], c) for c, j in colidx.items() if row[j] == row[j]],
                          key=lambda x: (-x[0], x[1]))
            rank = [c for _v, c in cand]
            m_of = {c: v for v, c in cand}
            raw_head = rank[0]
            lead = (m_of[raw_head] - m_of[rank[1]]) if len(rank) > 1 else float("nan")
            if prev_eff is not None and prev_eff not in rank:
                prev_eff = None
            eff_head = prev_eff if (prev_eff is not None and lead == lead
                                    and lead < STICKY) else raw_head
            dev_eff = dev[i, colidx[eff_head]]
            if state == "ROT":
                if (dev_eff == dev_eff and dev_eff > ENTER_DEV) or \
                   (lead == lead and lead > ENTER_LEAD):
                    state, def_days = "DEF", 0
            else:
                def_days += 1
                if (dev_eff == dev_eff and dev_eff < EXIT_DEV) and \
                   (lead == lead and lead < EXIT_LEAD) and def_days >= MIN_DEF_DAYS:
                    state, def_days = "ROT", 0
            out.append((dates[i + 1], state))
            prev_eff = eff_head
        return out

    state_checks = {}
    for gname, (_gl, pool) in groups.items():
        st_log = def_days_set(pool, "512040")
        defset = set(d for d, s in st_log if s == "DEF")
        for sk in ("A_old_512880_full", "C_new_512040_ve"):
            hl = holdlogs[(gname, sk)]
            cashd = set(d for d, t in hl if t == CASH)
            outside = sorted(cashd - defset)
            # 防守期内持币日的收益是否恒 0
            zero_ret = 0
            if sk == "C_new_512040_ve":
                rc = run(panel, dates, start_i, end_i, pool, "512040", True)
                for d, t, ret in rc["ret_log"]:
                    if t == CASH and abs(ret) > 1e-15:
                        zero_ret += 1
            info = dict(group=gname, scen=sk, state_def_days=len(defset),
                        cash_days=len(cashd),
                        cash_outside_state_DEF=len(outside),
                        cash_outside_dates=[d.strftime("%Y-%m-%d") for d in outside][:10],
                        cash_days_nonzero_return=zero_ret,
                        def_days_with_defensive=len(defset & set(
                            d for d, t in hl if t not in (CASH,))),
                        )
            state_checks[f"{gname}|{sk}"] = info
            print(f"  {gname:<6}{sk:<22} state==DEF {len(defset):>4}日 | 持币 {len(cashd):>4}日 "
                  f"| 防守期外空仓 {len(outside):>3}日 | 持币日非零收益 {zero_ret:>3}日 "
                  f"| 防守期内持防守位 {info['def_days_with_defensive']:>4}日")

    # ---------- 任务8 附加: 单一时期驱动 / 防守期归因 ----------
    print("\n" + "=" * 96)
    print("【任务8 附加】防守期内各年贡献分解 (主选池)")
    print("=" * 96)
    rc = run(panel, dates, start_i, end_i, rot, "512040", True)
    rb = run(panel, dates, start_i, end_i, rot, "512040", False)
    ra = run(panel, dates, start_i, end_i, rot, "512880", False)
    yr_cash, yr_cash_skip, yr_defdays = {}, {}, {}
    for (d, t, ret), (_d2, t2, retb) in zip(rc["ret_log"], rb["ret_log"]):
        y = str(d.year)
        yr_cash.setdefault(y, 0)
        yr_defdays.setdefault(y, 0)
        if t == CASH:
            yr_cash[y] = yr_cash.get(y, 0) + 1
            yr_cash_skip[y] = yr_cash_skip.get(y, 0.0) + math.log(1.0 + retb)
        yr_defdays[y] = yr_defdays.get(y, 0) + (1 if t in ("512040", "CASH") else 0)
    print(f"  {'年':<8}{'C持币日':>9}{'该年防守期':>11}{'跳过收益(复利)':>15}{'A':>9}{'B':>9}{'C':>9}{'C-A':>9}")
    for y in sorted(set(y["year"] for y in results["main"]["runs"]["C_new_512040_ve"]["yearly"])):
        a = yearly_tbl[("main", "A_old_512880_full")].get(y)
        b = yearly_tbl[("main", "B_512040_full")].get(y)
        c = yearly_tbl[("main", "C_new_512040_ve")].get(y)
        if a is None:
            continue
        print(f"  {y:<8}{yr_cash.get(y,0):>9}{yr_defdays.get(y,0):>11}"
              f"{(math.exp(yr_cash_skip.get(y,0.0))-1)*100:>14.2f}%"
              f"{a*100:>8.1f}%{b*100:>8.1f}%{c*100:>8.1f}%{(c-a)*100:>8.1f}pp")
    # 防守期按年分布
    st_log = def_days_set(rot, "512040")
    yr_def = Counter(d.year for d, s in st_log if s == "DEF")
    print(f"  各年 state==DEF 天数: {dict(sorted(yr_def.items()))}")
    yr_cash2 = Counter(d.year for d, t in holdlogs[("main", "C_new_512040_ve")] if t == CASH)
    print(f"  各年 CASH 天数      : {dict(sorted(yr_cash2.items()))}")

    # ---------- 任务8 附加: 价值引擎参数敏感性 (过拟合检验) ----------
    print("\n" + "=" * 96)
    print("【任务8 附加】价值引擎参数敏感性 (主选池 C 组, 过拟合/参数稳健性检验)")
    print("=" * 96)

    def run_ve_param(pool, deff_code, buy_f, sell_f, ma):
        cols, mom, dev, _ = precompute(panel, pool)
        colidx = {c: j for j, c in enumerate(cols)}
        s = panel[deff_code]
        ma_s = s.rolling(ma, min_periods=ma).mean()
        ve = {}
        hold = False
        for d in dates:
            p, mm = s.loc[d], ma_s.loc[d]
            if pd.isna(p) or pd.isna(mm):
                ve[d] = hold
                continue
            if not hold and p < mm * buy_f:
                hold = True
            elif hold and p > mm * sell_f:
                hold = False
            ve[d] = hold
        state, def_days, held, prev_eff, nav = "ROT", 0, CASH, None, 1.0
        for i in range(start_i, end_i):
            row = mom[i]
            if np.isnan(row).all():
                continue
            cand = sorted([(row[j], c) for c, j in colidx.items() if row[j] == row[j]],
                          key=lambda x: (-x[0], x[1]))
            rank = [c for _v, c in cand]
            m_of = {c: v for v, c in cand}
            raw_head = rank[0]
            lead = (m_of[raw_head] - m_of[rank[1]]) if len(rank) > 1 else float("nan")
            if prev_eff is not None and prev_eff not in rank:
                prev_eff = None
            eff_head = prev_eff if (prev_eff is not None and lead == lead
                                    and lead < STICKY) else raw_head
            dev_eff = dev[i, colidx[eff_head]]
            if state == "ROT":
                if (dev_eff == dev_eff and dev_eff > ENTER_DEV) or \
                   (lead == lead and lead > ENTER_LEAD):
                    state, def_days = "DEF", 0
            else:
                def_days += 1
                if (dev_eff == dev_eff and dev_eff < EXIT_DEV) and \
                   (lead == lead and lead < EXIT_LEAD) and def_days >= MIN_DEF_DAYS:
                    state, def_days = "ROT", 0
            target = eff_head if state == "ROT" else (
                deff_code if ve.get(dates[i], False) else CASH)
            if target != held:
                nav *= (1 - 0.002)
            r0 = 0.0
            if target in panel.columns:
                p0, p1 = panel[target].iloc[i], panel[target].iloc[i + 1]
                if p0 == p0 and p1 == p1 and p0 != 0:
                    r0 = p1 / p0 - 1.0
            nav *= (1 + r0)
            held = target
            prev_eff = eff_head
        return nav

    print(f"  {'buy_f/sell_f':<14}{'ma=200':>10}" +
          "".join(f"{'ma=' + str(m):>10}" for m in (100, 150, 250)))
    sens = {}
    for bf, sf in ((0.97, 1.06), (0.95, 1.05), (0.98, 1.05), (0.97, 1.08),
                   (0.99, 1.02), (0.90, 1.10)):
        cells = [run_ve_param(rot, "512040", bf, sf, 200)]
        for m in (100, 150, 250):
            cells.append(run_ve_param(rot, "512040", bf, sf, m))
        sens[f"{bf}/{sf}"] = cells
        print(f"  {bf:.2f}/{sf:.2f}      " + "".join(f"{c:>10.4f}" for c in cells))
    # 最优参数
    allc = [(v, k, i) for k, vs in sens.items() for i, v in enumerate(vs)]
    best = max(allc)
    print(f"  网格最优: {best[1]} 第{best[2]}列(0=ma200) 净值 {best[0]:.4f} "
          f"vs 实际参数 0.97/1.06+ma200 = {sens['0.97/1.06'][0]:.4f}  "
          f"差 {best[0]-sens['0.97/1.06'][0]:+.4f}")

    # ---------- 任务8 附加: 事后选股检验(全候选防守位扫描) ----------
    print("\n" + "=" * 96)
    print("【任务8 附加】事后选股检验: 把所有备选标的都当防守位跑一遍 (主选池)")
    print("=" * 96)
    cands = sorted(set(rot) | set(watch14) | {"512880", "512040"})
    cands = [c for c in cands if c in panel.columns]
    scan = []
    for c in cands:
        r_full = run(panel, dates, start_i, end_i, rot, c, False)
        r_ve = run(panel, dates, start_i, end_i, rot, c, True)
        scan.append((c, NAMES.get(c, c), float(r_full["navs"].iloc[-1]),
                     float(r_ve["navs"].iloc[-1]),
                     stats(r_ve["navs"])["mdd"] * 100))
    scan.sort(key=lambda x: -x[3])
    print(f"  {'代码':<9}{'名称':<20}{'满仓期末':>11}{'VE期末':>10}{'VE回撤%':>10}{'VE排名':>8}")
    rank512040 = [i for i, x in enumerate(scan) if x[0] == "512040"][0] + 1
    for i, (c, nm, fv, cv, md) in enumerate(scan):
        mark = "  <== 512040" if c == "512040" else ("  <== 旧防守位" if c == "512880" else "")
        print(f"  {c:<9}{nm:<20}{fv:>11.4f}{cv:>10.4f}{md:>10.2f}{i+1:>8}{mark}")
    n_better = sum(1 for x in scan if x[3] > scan[rank512040 - 1][3])
    print(f"  -> 512040 在 {len(scan)} 个候选防守位中 VE 净值排名 {rank512040}/{len(scan)}, "
          f"有 {n_better} 个候选优于它")

    # ---------- 任务8 附加: 空仓机会成本 ----------
    print("\n" + "=" * 96)
    print("【任务8 附加】空仓机会成本 (CASH 按 0 收益计, 未给任何利息)")
    print("=" * 96)
    occ = {}
    for gname, (_gl, pool) in groups.items():
        n_cash = sum(1 for _d, t in holdlogs[(gname, "C_new_512040_ve")] if t == CASH)
        base = results[gname]["runs"]["C_new_512040_ve"]["final"]
        row = dict(cash_days=n_cash, final_cash0=base)
        for yld in (0.015, 0.02, 0.025):
            f = base * (1 + yld) ** (n_cash / 244.0)
            row[f"final_at_{int(yld*1000)/10}pct"] = f
            row[f"delta_pp_at_{int(yld*1000)/10}pct"] = (f - base) * 100
        # 相对 B 的领先是否被抹平
        b = results[gname]["runs"]["B_512040_full"]["final"]
        row["lead_over_B_cash0_pp"] = (base / b - 1) * 100
        row["lead_over_B_at_2pct_pp"] = (row["final_at_2.0pct"] / b - 1) * 100
        occ[gname] = row
        print(f"  {gname:<6} 持币 {n_cash:>4}日 | C期末(CASH=0) {base:.4f} "
              f"| 持币按1.5%年化 {row['final_at_1.5pct']:.4f} "
              f"| 按2.0% {row['final_at_2.0pct']:.4f} "
              f"| 按2.5% {row['final_at_2.5pct']:.4f}")
        print(f"         相对B领先: CASH=0 时 {row['lead_over_B_cash0_pp']:+.2f}pp -> "
              f"2%年化时 {row['lead_over_B_at_2pct_pp']:+.2f}pp")

    # ---------- 落盘 ----------
    out = dict(
        auditor="independent recheck (no import of etf_backtest_long.py)",
        data=dict(csv=os.path.basename(ADJ_CSV), rows=int(n_raw), rows_after_dedup=int(n_dedup),
                  n_codes=int(panel.shape[1]),
                  data_start=str(dates[0].date()), data_end=str(dates[-1].date()),
                  bars=int(len(dates))),
        window=dict(start=str(dates[start_i].date()), end=str(dates[end_i].date()),
                    days=int(win_days), cost=COST,
                    cost_mode="每次换仓按双腿(买0.1%+卖0.1%), 含进出CASH"),
        timing="第i日收盘算信号 -> 同日收盘换仓 -> 第i+1日起计收益",
        strategy=dict(W=W, MA=MA, sticky=STICKY, enter_dev=ENTER_DEV,
                      enter_lead=ENTER_LEAD, exit_dev=EXIT_DEV, exit_lead=EXIT_LEAD,
                      min_def_days=MIN_DEF_DAYS, ve_ma=VE_MA, ve_buy=VE_BUY,
                      ve_sell=VE_SELL, allow_cash=True,
                      sticky_anchor="链式prev_eff", overheat_dev="dev(eff_head)"),
        variant_test=dict(base=v_base, broken_anchor_raw_head=v_anchor,
                          broken_dev_raw_head=v_dev,
                          claim_A=3.4374),
        scenarios={k: dict(defensive=v["defensive"], value_engine=v["use_ve"],
                           label=v["label"]) for k, v in SCEN.items()},
        groups={g: dict(label=results[g]["label"], pool=results[g]["pool"],
                        runs={sk: {kk: vv for kk, vv in results[g]["runs"][sk].items()}
                              for sk in SCEN},
                        bench_def=results[g]["bench_def"])
                for g in groups},
        nav={f"{g}|{sk}": {d: round(float(v), 8) for d, v in curves[(g, sk)].items()}
             for g in groups for sk in SCEN},
        value_engine=ve_info,
        cash_checks=cash_checks,
        attribution=attribution,
        data_audit=dict(x512040=da, comparables=dup_detail),
        ma200_spotcheck=spots,
        yearly={f"{g}|{sk}": yearly_tbl[(g, sk)] for g in groups for sk in SCEN},
        cost_probe=cost_probe,
        zero_cost=zero,
        comparison_vs_claim=cmp_rows,
        daily_reconciliation=daily_recon,
        timing_rows=timing_rows,
        state_checks=state_checks,
        ve_sensitivity={k: v for k, v in sens.items()},
        ve_sens_best=dict(param=best[1], col=best[2], nav=best[0],
                          actual_param_nav=sens["0.97/1.06"][0]),
        defensive_asset_scan=[dict(code=c, name=nm, full_final=fv, ve_final=cv,
                                   ve_mdd=md) for c, nm, fv, cv, md in scan],
        defensive_asset_scan_rank_512040=rank512040,
        defensive_asset_scan_n=len(scan),
        defensive_asset_n_better_than_512040=n_better,
        yearly_def_cash=dict(def_days_by_year=dict(sorted(yr_def.items())),
                              cash_days_by_year=dict(sorted(yr_cash2.items())),
                              cash_skip_log_by_year={k: v for k, v in
                                                     sorted(yr_cash_skip.items())},
                              def_days_by_year_from_heldlog=yr_defdays),
        cash_opportunity_cost=occ,
        max_abs_resid_nav=max_resid,
        max_rel_resid_pct=max_resid_pct,
        audit_items=AUDIT,
    )
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[json] {OUT_JSON}")

    # 供报告阶段使用的中间量
    np.save(os.path.join(HERE, "out", "_audit_curve_main.npy"),
            np.array([[curves[("main", sk)].iloc[-1] for sk in SCEN]]))
    return out


if __name__ == "__main__":
    main()
