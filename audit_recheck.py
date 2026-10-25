# -*- coding: utf-8 -*-
"""
audit_recheck.py — ETF 轮动策略B 独立复核脚本（审计员自建，从零实现）

设计原则（循环论证防护）:
  * 本文件不 import、不读取、不复用主 agent 的任何脚本/函数。
  * 全部逻辑依据任务书给出的规则文本独立重写。
  * 显式实现 look-ahead 防护: 信号只用 <= i 日数据, 收益用 i+1/i。
  * 提供截断重算(truncation test)作为未来函数的实证检验。

输出: 控制台审计明细 + out/audit_recheck.json (供报告引用)
"""
import json
import os
import sys
import urllib.request

import numpy as np
import pandas as pd

CSV = "tdx_adj_long_2019_2026.csv"
POOL_CSV = "etf_pool.csv"
OUTDIR = "out"
CAL_CODE = "000001"          # 上证指数, 作为交易日历
START = "2019-12-31"          # 净值起算日 (nav=1.000)
MOM_N = 20                   # 动横20: P_t / P_{t-20} - 1, 索引差 20 (非19)
MA_N = 20                    # 乖离基准 MA20
STICKY_PP = 1.0              # 粘性带: 领先度 < 1.0pp 视为平手
DEV_IN_PP = 12.0             # 切防守: 头名乖离 > 12%
DEV_LEAD_PP = 15.0           # 切防守: 领先度 > 15pp
BACK_DEV_PP = 9.0            # 切回: 乖离 < 9%
BACK_LEAD_PP = 10.0          # 切回: 领先度 < 10pp
BACK_MIN_DAYS = 5            # 切回: 已防守满 5 个交易日
COST_SWITCH = 0.002          # 每次换仓双边成本 0.2%
DEFENSIVE = "512880"
TD_YEARS = 244               # 年化/Sharpe 年化因子

MAIN9 = ["510300", "159967", "563300", "518880", "513100",
         "588170", "516080", "561700", "513660"]
WATCH14 = ["159259", "512200", "159543", "159505", "588000", "588220",
           "510210", "510050", "159906", "512070", "512890", "159957",
           "159915", "159949"]


# ----------------------------------------------------------------------------
# 数据装载
# ----------------------------------------------------------------------------
def load_close():
    raw = pd.read_csv(CSV, dtype={"code": str, "date": str})
    raw = raw.drop_duplicates(subset=["code", "date"], keep="first")
    raw["d"] = pd.to_datetime(raw["date"], format="%Y%m%d")
    cal = pd.DatetimeIndex(sorted(raw.loc[raw["code"] == CAL_CODE, "d"].unique()))
    close = raw.pivot(index="d", columns="code", values="close").reindex(cal)
    return raw, cal, close


# ----------------------------------------------------------------------------
# 指标构造 —— 注意索引差严格为 20
# ----------------------------------------------------------------------------
def build_indicators(close, n_mom=MOM_N, n_ma=MA_N):
    """返回 mom(n_mom), dev(乖离), ma(n_ma)。

    mom.loc[t] = close.loc[t] / close.shift(n_mom).loc[t] - 1
    ma.loc[t]  = close.rolling(n_ma).mean().loc[t]      (含当日)
    dev.loc[t] = close.loc[t] / ma.loc[t] - 1
    """
    mom = close / close.shift(n_mom) - 1.0
    ma = close.rolling(n_ma, min_periods=n_ma).mean()
    dev = close / ma - 1.0
    return mom, ma, dev


# ----------------------------------------------------------------------------
# 策略B 状态机 (完全独立实现)
# ----------------------------------------------------------------------------
def run_strategyB(close, mom, dev, pool, defensive=DEFENSIVE,
                  start=START, cost=COST_SWITCH, charge_initial_cost=True,
                  sticky_mode="prevhead", label=""):
    """在 pool 内轮动, 越界切 defensive。

    时序约定(显式, 防未来函数):
      信号日 t  : 只使用 close/mom/dev 在 t 及之前的数据
      成交      : t 日收盘换仓, 当日扣成本
      收益日t+1 : 收益 = close[t+1, 持仓] / close[t, 持仓] - 1

    sticky_mode:
      "prevhead" : 规则2 字面口径 —— 保留"前一日的头名"(默认, 本审计主口径)
      "prevhold" : 保留"前一日的持仓"(链式口径, 敏感性对照)
    """
    dates = close.index
    pool = list(pool)
    i0 = dates.searchsorted(pd.Timestamp(start))

    momp = mom[pool]
    devp = dev[pool]

    nav = {}
    hold = None
    in_def = False
    def_days = 0            # 已防守交易日数 (进入防守当日起计 1)
    prev_head = None        # 前一日的头名(粘性带用)
    sticky_days = 0        # 粘性带实际阻止换仓的信号日数
    def_entries = 0
    def_run = 0
    def_longest = 0
    switches = []           # (date, old, new, reason)
    forced_entry_done = False
    rows = []
    prev_nav = 1.0

    def rank_at(i):
        """第 i 日(信号日)排名。返回 [(code, mom_pct, dev_pct), ...] 降序。
        动态池: 当日有有效收盘 且 20个交易日前(索引差20)也有有效收盘。
        """
        s = momp.iloc[i].dropna()
        if len(s) == 0:
            return []
        s = s.sort_values(ascending=False)
        out = []
        for c in s.index:
            dv = devp.at[dates[i], c]
            out.append((c, float(s[c]) * 100.0,
                        (float(dv) * 100.0) if pd.notna(dv) else None))
        return out

    i = i0
    while i < len(dates) - 1:
        d = dates[i]
        rk = rank_at(i)
        head = rk[0] if rk else None
        second = rk[1] if len(rk) > 1 else None
        lead_pp = (head[1] - second[1]) if (head and second) else None

        old = hold
        new = hold
        reason = "hold"

        if head is None:
            reason = "no_rank"
            new = hold
        else:
            h_dev = head[2]
            if in_def:
                # ---- 防守态: 三条件同时满足才切回轮动 ----
                ok_dev = (h_dev is not None) and (h_dev < BACK_DEV_PP)
                ok_lead = (lead_pp is not None) and (lead_pp < BACK_LEAD_PP)
                ok_days = def_days >= BACK_MIN_DAYS
                if ok_dev and ok_lead and ok_days:
                    new = head[0]
                    reason = "back_to_rotation"
                    in_def = False
                else:
                    reason = "stay_defensive"
                    new = defensive
            else:
                # ---- 轮动态: 先判切防守(硬风控), 再判粘性带 ----
                trigger_dev = (h_dev is not None) and (h_dev > DEV_IN_PP)
                trigger_lead = (lead_pp is not None) and (lead_pp > DEV_LEAD_PP)
                if trigger_dev or trigger_lead:
                    new = defensive
                    reason = "to_defensive(乖离)" if trigger_dev else "to_defensive(领先度)"
                elif lead_pp is not None and lead_pp < STICKY_PP:
                    # 粘性带: 领先度不足 1pp, 不换仓
                    if sticky_mode == "prevhead":
                        cand = prev_head
                    else:
                        cand = hold
                    if cand is not None and cand != head[0]:
                        sticky_days += 1
                        reason = "sticky_keep(%s->%s)" % (head[0], cand)
                    new = cand if cand is not None else head[0]
                    if new is None:
                        new = head[0]
                else:
                    new = head[0]
                    reason = "rotate_to_head"

        # 防守计数
        if in_def:
            def_run += 1
            def_longest = max(def_longest, def_run)
        if new == defensive and not in_def and old != defensive:
            in_def = True
            def_days = 1
            def_run = 1
            def_entries += 1
            def_longest = max(def_longest, 1)
        elif in_def and new == defensive:
            def_days += 1
        if in_def and new != defensive:
            in_def = False
            def_days = 0
            def_run = 0
        switched = (old != new)
        # 初始建仓
        initial = (old is None)
        if initial and charge_initial_cost:
            switched = True
        if switched:
            switches.append((d.strftime("%Y-%m-%d"), old, new, reason))
        hold = new
        prev_head = head[0] if head is not None else prev_head

        # ---- 收益日 i+1: 只用 i 与 i+1 的收盘价 ----
        j = i + 1
        p0 = close.at[dates[i], hold]
        p1 = close.at[dates[j], hold]
        if pd.isna(p0) or pd.isna(p1) or p0 == 0:
            ret = 0.0
            bad = True
        else:
            ret = p1 / p0 - 1.0
            bad = False
        c = cost if switched else 0.0
        # nav[起算日] = 1.000; 此后逐日: nav = nav_prev * (1+ret) * (1-cost)
        nav[dates[j]] = (1.0 + ret) * (1.0 - c) if j == i0 + 1 \
            else prev_nav * (1.0 + ret) * (1.0 - c)
        prev_nav = nav[dates[j]]
        rows.append(dict(
            signal_date=dates[i].strftime("%Y-%m-%d"),
            ret_date=dates[j].strftime("%Y-%m-%d"),
            hold=hold, prev=old, reason=reason, switched=bool(switched),
            cost=c, ret=ret, lead_pp=lead_pp,
            head=head[0] if head else None,
            head_mom=head[1] if head else None,
            head_dev=head[2] if head else None,
            second=second[0] if second else None,
            second_mom=second[1] if second else None,
            bad_price=bad,
        ))

        i += 1

    navser = pd.Series(nav).dropna().sort_index()
    return dict(label=label, nav=navser, rows=rows, switches=switches,
                sticky_days=sticky_days, def_entries=def_entries,
                def_longest=def_longest,
                def_days=sum(1 for r in rows if r["hold"] == defensive))


# ----------------------------------------------------------------------------
# 绩效指标
# ----------------------------------------------------------------------------
def perf(nav, td_years=TD_YEARS):
    nav = nav.dropna()
    r = nav.pct_change().dropna()
    n_ret = len(r)
    final = float(nav.iloc[-1])
    years = n_ret / td_years
    ann = final ** (1.0 / years) - 1.0 if years > 0 else float("nan")
    dd = nav / nav.cummax() - 1.0
    mdd = float(dd.min())
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(td_years)) if r.std(ddof=1) > 0 else float("nan")
    yearly = {}
    for y, g in nav.groupby(nav.index.year):
        prev = 1.0 if y == nav.index[0].year else float(nav[nav.index < g.index[0]].iloc[-1])
        yearly[int(y)] = float(g.iloc[-1] / prev - 1.0)
    return dict(final=final, total=final - 1.0, years=years, ann=ann, mdd=mdd,
                sharpe=sharpe, n_ret=n_ret, n_nav=len(nav),
                first=str(nav.index[0].date()), last=str(nav.index[-1].date()),
                yearly=yearly)


# ----------------------------------------------------------------------------
# 未来函数检验: 截断重算
# ----------------------------------------------------------------------------
def truncation_test(close, pool, cut_dates):
    """把数据在 cut 日截断后重跑, 与全量跑的前段决策逐日比对。
    若存在未来函数, 截断后决策会改变。"""
    mom, ma, dev = build_indicators(close)
    full = run_strategyB(close, mom, dev, pool, label="full")
    full_rows = {r["signal_date"]: r for r in full["rows"]}
    out = []
    for cd in cut_dates:
        cl = close.loc[:pd.Timestamp(cd)]
        mom, ma, dev = build_indicators(cl)
        t = run_strategyB(cl, mom, dev, pool, label="trunc" + cd)
        cmp_rows = {r["signal_date"]: r for r in t["rows"]}
        dates_common = sorted(set(full_rows) & set(cmp_rows))
        diff = []
        for d in dates_common:
            a, b = full_rows[d], cmp_rows[d]
            if a["hold"] != b["hold"] or a["reason"] != b["reason"]:
                diff.append((d, a["hold"], b["hold"], a["reason"], b["reason"]))
        out.append(dict(cut=cd, compared=len(dates_common), diffs=len(diff),
                        sample=diff[:5]))
    return out


# ----------------------------------------------------------------------------
# 净值守恒反算
# ----------------------------------------------------------------------------
def nav_conservation(nav, rows, start_date):
    """nav[i] == nav[i-1] * (1+ret_i) * (1-cost_i) 逐日反算"""
    nav = nav.dropna()
    errs = []
    prev = 1.0
    for r in rows:
        exp = prev * (1.0 + r["ret"]) * (1.0 - r["cost"])
        got = float(nav.loc[pd.Timestamp(r["ret_date"])])
        errs.append(abs(exp - got))
        prev = got
    return dict(max_err=float(max(errs)) if errs else 0.0,
                final_recon=prev, final_nav=float(nav.iloc[-1]))


# ----------------------------------------------------------------------------
# 腾讯财经前复权抽查
# ----------------------------------------------------------------------------
def tencent_qfq(code, d_from, d_to):
    """腾讯前复权接口单次最多返回约 640 条, 按年度分窗抓取后合并。"""
    mkt = "sh" if code[0] == "5" else "sz"
    out = {}
    y0, y1 = int(d_from[:4]), int(d_to[:4])
    for yr in range(y0, y1 + 1):
        url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
               "param=%s%s,day,%d-01-01,%d-12-31,700,qfq" % (mkt, code, yr, yr))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            js = json.loads(resp.read().decode("utf-8", "ignore"))
        node = js["data"].get(mkt + code) or {}
        kl = node.get("qfqday") or node.get("day") or []
        for r in kl:
            out[r[0]] = float(r[2])
    return out


def spot_check(raw, close, samples):
    res = []
    for code, d1, d2 in samples:
        entry = dict(code=code, dates=[d1, d2], csv=[], api=[], status="")
        for ds in (d1, d2):
            c = close.at[pd.Timestamp(ds), code]
            entry["csv"].append(None if pd.isna(c) else round(float(c), 4))
        try:
            api_map = tencent_qfq(code, "2018-12-01", "2026-12-31")
            for ds in (d1, d2):
                a = api_map.get(ds)
                entry["api"].append(None if a is None else round(a, 4))
            entry["api_days"] = len(api_map)
            diffs = []
            for k, ds in enumerate((d1, d2)):
                a, b = entry["csv"][k], entry["api"][k]
                if a is None or b is None:
                    diffs.append(None)
                else:
                    rel = abs(a - b) / b
                    diffs.append(round(rel, 6))
            entry["rel_diff"] = diffs
            entry["ok"] = all(x is not None and x < 0.005 for x in diffs)
            entry["status"] = "ok" if entry["ok"] else "mismatch"
        except Exception as e:  # 网络不可用等
            entry["status"] = "api_error: %s" % type(e).__name__
            entry["err"] = str(e)[:200]
        res.append(entry)
    return res


# ----------------------------------------------------------------------------
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    raw, cal, close = load_close()

    mom, ma, dev = build_indicators(close)
    R = {}

    # 动态池有效性
    R["cal"] = dict(n=len(cal), first=str(cal[0].date()), last=str(cal[-1].date()))

    runs = {}
    for lab, pool in (("main9", MAIN9), ("watch14", WATCH14),
                      ("main+watch23", MAIN9 + WATCH14)):
        for cost, tag in ((COST_SWITCH, "cost"), (0.0, "nocost")):
            runs["%s@%s" % (lab, tag)] = run_strategyB(
                close, mom, dev, pool, cost=cost,
                charge_initial_cost=(tag == "cost"), label="%s@%s" % (lab, tag))
        # 初始建仓不计成本变体
        runs["%s@cost_noinit" % lab] = run_strategyB(
            close, mom, dev, pool, cost=COST_SWITCH,
            charge_initial_cost=False, label="%s@cost_noinit" % lab)
        # 无防守机制变体(备选池口径排查用)
        runs["%s@cost_nodef" % lab] = run_strategyB(
            close, mom, dev, pool, defensive=None, cost=COST_SWITCH,
            label="%s@cost_nodef" % lab) if False else None
        # 粘性带口径敏感性: prevhold = 保留前一日"持仓"
        runs["%s@cost_prevhold" % lab] = run_strategyB(
            close, mom, dev, pool, cost=COST_SWITCH, sticky_mode="prevhold",
            label="%s@cost_prevhold" % lab)

    perf_all = {k: perf(v["nav"]) for k, v in runs.items() if v is not None}
    R["perf"] = perf_all

    # 成本拖累
    for lab in ("main9", "watch14", "main+watch23"):
        a = perf_all["%s@cost" % lab]["final"]
        b = perf_all["%s@nocost" % lab]["final"]
        R.setdefault("cost_drag", {})[lab] = dict(
            final_cost=a, final_nocost=b, diff=b - a,
            switches=len(runs["%s@cost" % lab]["switches"]),
            switches_noinit=len(runs["%s@cost_noinit" % lab]["switches"]),
            log_drag=float(np.log(b) - np.log(a)))

    # 净值守恒
    R["conservation"] = {
        lab: nav_conservation(runs["%s@cost" % lab]["nav"],
                              runs["%s@cost" % lab]["rows"], START)
        for lab in ("main9", "watch14", "main+watch23")}

    # 策略统计
    R["stats"] = {
        lab: dict(switches=len(runs["%s@cost" % lab]["switches"]),
                  switches_prevhold=len(runs["%s@cost_prevhold" % lab]["switches"]),
                  sticky_days=runs["%s@cost" % lab]["sticky_days"],
                  def_entries=runs["%s@cost" % lab]["def_entries"],
                  def_longest=runs["%s@cost" % lab]["def_longest"],
                  def_days=runs["%s@cost" % lab]["def_days"],
                  def_days_prevhold=runs["%s@cost_prevhold" % lab]["def_days"],
                  def_entries_prevhold=runs["%s@cost_prevhold" % lab]["def_entries"],
                  def_longest_prevhold=runs["%s@cost_prevhold" % lab]["def_longest"])
        for lab in ("main9", "watch14", "main+watch23")}

    # 2020-01-02 动态池
    i2020 = cal.searchsorted(pd.Timestamp("2020-01-02"))
    elig = {}
    for lab, pool in (("main9", MAIN9), ("watch14", WATCH14),
                      ("main+watch23", MAIN9 + WATCH14)):
        s = mom[pool].loc[cal[i2020 - 1]].dropna()   # 信号日 = 2019-12-31
        elig[lab] = dict(n=len(s), codes=sorted(s.index.tolist()))
    R["pool_20200102"] = dict(signal_date="2019-12-31", ret_date="2020-01-02",
                              eligible=elig)

    # 逐日明细抽样: 抽 3 个换仓日
    sw = runs["main9@cost"]["switches"]
    picks = [sw[0], sw[len(sw) // 2], sw[-1]]
    detail = []
    rmap = {r["signal_date"]: r for r in runs["main9@cost"]["rows"]}
    for d, o, n, why in picks:
        r = rmap[d]
        i = cal.searchsorted(pd.Timestamp(d))
        full_rank = mom[MAIN9].iloc[i].dropna().sort_values(ascending=False)
        detail.append(dict(
            date=d, old=o, new=n, reason=why,
            rank=[(c, round(float(v) * 100, 4)) for c, v in full_rank.items()],
            lead_pp=None if r["lead_pp"] is None else round(r["lead_pp"], 4),
            head=r["head"], head_dev=None if r["head_dev"] is None else round(r["head_dev"], 4),
            ma20=None if pd.isna(ma.at[cal[i], r["head"]]) else round(float(ma.at[cal[i], r["head"]]), 4),
            close_t=None if pd.isna(close.at[cal[i], r["head"]]) else round(float(close.at[cal[i], r["head"]]), 4),
            dev_of_top5={c: (None if pd.isna(dev.at[cal[i], c]) else round(dev.at[cal[i], c] * 100, 4))
                         for c in full_rank.index[:5]}))
    R["switch_samples"] = detail

    # 粘性带"生效日"口径: 领先度 < 1.0pp 的信号日数 (含防守期)
    df9 = pd.DataFrame(runs["main9@cost"]["rows"])
    R["sticky_active_days"] = dict(
        lead_lt_1pp_all=int(((df9["lead_pp"].notna()) & (df9["lead_pp"] < 1.0)).sum()),
        lead_lt_1pp_notdef=int(((df9["lead_pp"].notna()) & (df9["lead_pp"] < 1.0)
                                 & (df9["hold"] != DEFENSIVE)).sum()),
        sticky_blocked=int(
            df9["reason"].astype(str).str.startswith("sticky_keep").sum()))

    # 未来函数: 截断重算
    R["lookahead"] = dict(
        note="信号仅用 <=i 日数据; 收益用 close[i+1]/close[i]. 截断重算验证决策不变。",
        test=truncation_test(close, MAIN9,
                             ["2020-06-30", "2022-06-30", "2024-06-28", "2026-03-31"]))

    # 数据真实性抽查
    R["spot"] = spot_check(raw, close, [
        ("510300", "2020-03-05", "2021-06-15"),
        ("518880", "2020-07-01", "2022-09-20"),
        ("159915", "2019-12-31", "2023-04-18"),
    ])

    # 逐日明细落盘
    for lab in ("main9", "watch14", "main+watch23"):
        pd.DataFrame(runs["%s@cost" % lab]["rows"]).to_csv(
            os.path.join(OUTDIR, "audit_daily_%s.csv" % lab),
            index=False, encoding="utf-8-sig")
        pd.DataFrame(runs["%s@nocost" % lab]["nav"].rename("nav")).to_csv(
            os.path.join(OUTDIR, "audit_nav_%s.csv" % lab),
            encoding="utf-8-sig")

    # 独立重算: 只用落盘的逐日明细(持仓+日期) 与原始收盘价, 全新代码路径重建净值
    R["nav_rebuild"] = {}
    for lab in ("main9", "watch14", "main+watch23"):
        d = pd.read_csv(os.path.join(OUTDIR, "audit_daily_%s.csv" % lab),
                        dtype={"hold": str, "prev": str})
        d["hold"] = d["hold"].str.strip()
        px = close
        v, series = 1.0, []
        for _, row in d.iterrows():
            sd = pd.Timestamp(row["signal_date"])
            rd = pd.Timestamp(row["ret_date"])
            c0 = px.at[sd, row["hold"]]
            c1 = px.at[rd, row["hold"]]
            rr = 0.0 if (pd.isna(c0) or pd.isna(c1) or c0 == 0) else c1 / c0 - 1.0
            cc = 0.002 if bool(row["switched"]) else 0.0
            v *= (1.0 + rr) * (1.0 - cc)
            series.append(v)
        orig = runs["%s@cost" % lab]["nav"].dropna().values
        diff = float(np.max(np.abs(np.array(series) - orig)))
        R["nav_rebuild"][lab] = dict(max_abs_diff=diff,
                                     rebuilt_final=float(series[-1]),
                                     original_final=float(orig[-1]),
                                     n=len(series),
                                     ret_mismatch=int((
                                         np.abs(d["ret"].values
                                                - np.array([
                                                    0.0 if (pd.isna(px.at[pd.Timestamp(a), h])
                                                            or pd.isna(px.at[pd.Timestamp(b), h])
                                                            or px.at[pd.Timestamp(a), h] == 0)
                                                    else px.at[pd.Timestamp(b), h]
                                                    / px.at[pd.Timestamp(a), h] - 1.0
                                                    for a, b, h in zip(d["signal_date"],
                                                                        d["ret_date"],
                                                                        d["hold"])]))
                                         > 1e-12).sum()))

    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(x) for x in o]
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, float) and not np.isfinite(o):
            return None
        return o

    with open(os.path.join(OUTDIR, "audit_recheck.json"), "w", encoding="utf-8") as f:
        json.dump(clean(R), f, ensure_ascii=False, indent=1, default=str)

    # ---- 控制台摘要 ----
    print("=" * 78)
    print("PERFORMANCE (annualized/sharpe factor=%d)" % TD_YEARS)
    for k in ("main9@cost", "main9@cost_noinit", "main9@nocost",
              "main9@cost_prevhold",
              "watch14@cost", "watch14@nocost", "watch14@cost_prevhold",
              "main+watch23@cost", "main+watch23@cost_prevhold"):
        p = perf_all[k]
        print("%-22s final=%.4f  ann=%.4f  mdd=%.4f  sharpe=%.3f  nret=%d"
              % (k, p["final"], p["ann"], p["mdd"], p["sharpe"], p["n_ret"]))
    print("\nYEARLY main9:", {k: round(v, 4) for k, v in perf_all["main9@cost"]["yearly"].items()})
    print("\nSTATS:", json.dumps(R["stats"], ensure_ascii=False))
    print("\nSTICKY DAYS:", json.dumps(R["sticky_active_days"], ensure_ascii=False))
    print("\nYEARLY prevhold:", {k: round(v, 4) for k, v in
                                  perf_all["main9@cost_prevhold"]["yearly"].items()})
    print("\nCOST DRAG:", json.dumps(R["cost_drag"], ensure_ascii=False, indent=1))
    print("\nCONSERVATION:", json.dumps(R["conservation"], ensure_ascii=False))
    print("\n2020-01-02 eligible:", json.dumps(R["pool_20200102"], ensure_ascii=False))
    print("\nLOOKAHEAD:", json.dumps(R["lookahead"]["test"], ensure_ascii=False))
    print("\nSPOT:", json.dumps(R["spot"], ensure_ascii=False, indent=1))
    print("\nSWITCH SAMPLES:", json.dumps(R["switch_samples"], ensure_ascii=False, indent=1))
    print("\nwrote", os.path.join(OUTDIR, "audit_recheck.json"))


if __name__ == "__main__":
    main()
