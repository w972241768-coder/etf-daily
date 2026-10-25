#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ROC 动量阶梯轮动核算器  (ETF 轮动战法 · 骑牛/DMI 体系延伸)
================================================================

口径 (与「调仓预测·动横阶梯」网页图 footer 自述一致, 非本人编造):
  1) 动横(n) = P_t / P_{t-n} - 1          # n 日 ROC / 时间序列动量
  2) 阶梯推演: 假设未来价格冻结, 则未来第 k 个交易日的「动横20」
              = 今日的 动横(20-k)          # 即窗口向历史滑移, 只读真实价
  3) 排名: 跨 N 只 ETF 按 动横20 降序, 取 头名/次名, 领先 = 头名 - 次名 (pp)
  4) 乖离: P_t / MA20(t) - 1,  MA20 = mean( P_{t-19} .. P_t )
  5) 今日涨跌: P_t / P_{t-1} - 1          # 锚定日当日涨跌幅
  6) 过热熔断:  乖离 > 12%  或  领先 > 15pp  -> 触发防守
  7) 证券ETF国泰 (默认 512880): 防守标的, 不参与轮动排名, 仅列示判断强弱
  8) 粘性带: 某列 头名-次名 领先 < sticky_lead (默认 1.0pp) 视为平手,
      保留前一列头名、不标换仓 -> 压缩噪音换仓、降换手

输入格式 (长表 CSV, UTF-8):
  date, code, close, name, role
  2026-09-22, 513100, 2.316, 纳指ETF,
  2026-09-22, 512880, 1.069, 证券ETF国泰, defensive
  ...
  - role 可省; 标 'defensive' 的自动排除出排名。
  - 每个 code 至少要有 (window+1) 个交易日的数据 (默认 21 天)。

用法:
  python roc_ladder.py --csv data.csv
  python roc_ladder.py --csv data.csv --date 2026-09-22 --window 20 \
                       --defensive 512880 --overheat-dev 12 --overheat-lead 15 \
                       --sticky-lead 1.0
"""

import argparse
import sys
from dataclasses import dataclass
from typing import Dict, List, Tuple

import pandas as pd
import numpy as np


@dataclass
class SnapRow:
    code: str
    name: str
    close: float
    momentum: Dict[int, float]   # {n: 动横(n) 百分比}
    dev: float                   # 乖离 百分比
    role: str                    # '' 普通 / 'defensive' 防守 / 'watch' 备选观察(均不参与排名)
    chg1: float = float("nan")   # 锚定日当日涨跌幅 百分比 (vs 上一交易日收盘)
    ma200: float = float("nan")  # 200 日均线 (仅防守位需要; 数据不足为 nan)
    ve_ratio: float = float("nan")  # 收盘 / MA200 - 1, 百分比。价值引擎判据用
    ve_buy: float = -3.0         # 买入线: 低于 MA200 3 个点
    ve_sell: float = 6.0         # 卖出线: 高于 MA200 6 个点
    ve_state: str = "未知"       # '可买入' / '应持有' / '应卖出' / '数据不足'


# role 取值约定:
#   ''         -> 主选池轮动标的 (参与排名 / 换仓判定)
#   'defensive'-> 防守标的 (不参与排名, 仅列示)
#   'watch'    -> 备选观察池 (绝不参与调仓判定, 只在报告末尾单独列示)
ROLE_DEFENSIVE = "defensive"
ROLE_WATCH = "watch"

VE_MA = 200
VE_BUY_F = 0.97      # 收盘 < MA200×0.97 -> 干(买)
VE_SELL_F = 1.06     # 收盘 > MA200×1.06 -> 跑(卖)


def value_engine_state(closes: np.ndarray, buy_f: float = VE_BUY_F,
                       sell_f: float = VE_SELL_F, ma: int = VE_MA):
    """博主「价值引擎」择时状态机 (与 etf_backtest_2026.py / etf_backtest_long.py 同源)。

    closes: 按时间升序、最新在末尾的收盘价序列 (需至少 ma 根)。
    返回 (ma_value, ratio_pct, state):
      ratio_pct = 收盘/MA200 - 1, 百分比
      state     = '应卖出' / '可买入' / '应持有'(区间内维持) / '数据不足'
    状态机语义: 卖出后必须重新跌破买线才买回, 中间区间维持上一状态。
    """
    if closes is None or len(closes) < ma:
        return float("nan"), float("nan"), "数据不足"
    ma_v = float(closes[-ma:].mean())
    p = float(closes[-1])
    ratio = (p / ma_v - 1.0) * 100.0 if ma_v else float("nan")
    # 复算整段状态, 保证"卖出后需重新跌破才买回"的记忆性
    hold = False
    for i in range(ma - 1, len(closes)):
        m = float(closes[i - ma + 1:i + 1].mean())
        if m <= 0:
            continue
        pi = float(closes[i])
        if (not hold) and pi < m * buy_f:
            hold = True
        elif hold and pi > m * sell_f:
            hold = False
    if ratio != ratio:
        return ma_v, ratio, "数据不足"
    if hold:
        state = "应卖出" if ratio > (sell_f - 1.0) * 100.0 else "应持有"
    else:
        state = "可买入" if ratio < (buy_f - 1.0) * 100.0 else "应持有"
    return ma_v, ratio, state


def split_pools(snap: List[SnapRow]):
    """把快照拆成 (主选池轮动, 防守, 备选观察池)。

    这是"主选池 vs 备选池"分组的唯一事实来源 —— 任何调用方都不要自己写
    `role != 'defensive'` 这类过滤, 否则会把 watch 标的混进排名与换仓判定。
    """
    rot = [r for r in snap if r.role not in (ROLE_DEFENSIVE, ROLE_WATCH)]
    dfn = [r for r in snap if r.role == ROLE_DEFENSIVE]
    wat = [r for r in snap if r.role == ROLE_WATCH]
    return rot, dfn, wat


def load_panel(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"code": str})
    df["code"] = df["code"].astype(str).str.strip()
    df["date"] = pd.to_datetime(df["date"])
    if "role" not in df.columns:
        df["role"] = ""
    if "name" not in df.columns:
        df["name"] = ""
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    return df


def build_snapshot(df: pd.DataFrame,
                   pivot: str,
                   window: int,
                   defensive: set) -> List[SnapRow]:
    pivot_ts = pd.Timestamp(pivot)
    rows: List[SnapRow] = []
    for code, sub in df.groupby("code"):
        sub = sub.drop_duplicates("date").sort_values("date")
        upto = sub[sub["date"] <= pivot_ts]
        upto = upto.tail(window + 1)
        if len(upto) < window + 1:
            sys.stderr.write(
                f"[warn] {code} 数据仅 {len(upto)} 天, 不足 {window+1} 天, "
                f"动横({window}) 无法计算\n")
        closes = upto["close"].to_numpy(dtype=float)  # 最新在末尾
        P_t = closes[-1]
        names = sub["name"].iloc[-1] if len(sub) else code
        role = ROLE_DEFENSIVE if code in defensive else (
            str(sub["role"].iloc[-1]) if sub["role"].iloc[-1] else "")
        momentum = {}
        for n in range(window, 0, -1):          # 动横20 .. 动横1
            base = closes[-1 - n]
            momentum[n] = (P_t / base - 1.0) * 100.0
        ma20 = closes[-window:].mean()
        dev = (P_t / ma20 - 1.0) * 100.0
        chg1 = ((closes[-1] / closes[-2] - 1.0) * 100.0) if len(closes) >= 2 else float("nan")
        # 防守位额外算 MA200 价值引擎 (需要 200 根历史, 单独取; 不影响主选池排序)
        ma200 = ratio200 = float("nan")
        ve_state = "不适用"
        if code in defensive:
            hist = sub[sub["date"] <= pivot_ts]["close"].to_numpy(dtype=float)
            ma200, ratio200, ve_state = value_engine_state(hist)
        rows.append(SnapRow(code=code, name=names, close=float(P_t),
                            momentum=momentum, dev=dev, role=role,
                            chg1=float(chg1), ma200=ma200, ve_ratio=ratio200,
                            ve_state=ve_state))
    # 顺序: 主选池(按 动横20 降序) -> 防守 -> 备选观察池(按 动横20 降序)
    # 注意: 'watch' 绝不进入主选池排名, 否则会污染头名/换仓日判定。
    key = (lambda r: r.momentum.get(window, -9e9))
    ranked = sorted([r for r in rows if r.role not in (ROLE_DEFENSIVE, ROLE_WATCH)],
                    key=key, reverse=True)
    defended = [r for r in rows if r.role == ROLE_DEFENSIVE]
    watched = sorted([r for r in rows if r.role == ROLE_WATCH],
                     key=key, reverse=True)
    return ranked + defended + watched


def analyze_ladder(ranked: List[SnapRow], window: int, sticky_lead: float):
    """逐列头名 + 粘性带 + 换仓日判定 (单一事实来源, 控制台与HTML共用)。

    返回 (cols, steps, swap_days, tie_days):
      cols      : 列号序列 [window, window-1, ..., 1]
      steps     : 每列 dict(idx, n, head, second, lead, tied, eff_head, held, is_swap)
      swap_days : [(第几日, 动横n, 旧头名, 新头名), ...]
      tie_days  : 被粘性带抹平(保留原头名)的列数
    """
    cols = list(range(window, 0, -1))
    steps = []
    prev_head = None
    swap_days: List[Tuple] = []
    tie_days = 0
    for i, n in enumerate(cols):
        col_rank = sorted(ranked, key=lambda r: r.momentum.get(n, -9e9), reverse=True)
        h = col_rank[0]
        s = col_rank[1] if len(col_rank) > 1 else None
        lead = (h.momentum[n] - s.momentum[n]) if s else float("nan")
        tied = (s is not None) and (lead < sticky_lead)
        eff_head = (prev_head if (tied and prev_head is not None) else h.code)
        held = bool(tied and prev_head is not None and prev_head != h.code)
        is_swap = bool(prev_head is not None and prev_head != eff_head)
        steps.append(dict(idx=i + 1, n=n, head=h, second=s, lead=lead, tied=tied,
                          eff_head=eff_head, held=held, is_swap=is_swap))
        if held:
            tie_days += 1
        if is_swap:
            swap_days.append((i + 1, n, prev_head, eff_head))
        prev_head = eff_head
    return cols, steps, swap_days, tie_days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--date", default=None, help="锚定日 YYYY-MM-DD, 默认取数据最大日")
    ap.add_argument("--window", type=int, default=20)
    ap.add_argument("--defensive", default="512040",
                    help="逗号分隔的防守标的代码, 不参与轮动排名")
    ap.add_argument("--overheat-dev", type=float, default=12.0, help="乖离过热线(%%)")
    ap.add_argument("--overheat-lead", type=float, default=15.0, help="领先过热线(pp)")
    ap.add_argument("--sticky-lead", type=float, default=1.0,
                    help="粘性带阈值(pp): 列内领先 < 该值视为平手, 保留前一头名不换仓")
    a = ap.parse_args()

    df = load_panel(a.csv)
    pivot = a.date or df["date"].max().strftime("%Y-%m-%d")
    defensive = set(x for x in a.defensive.split(",") if x)

    snap = build_snapshot(df, pivot, a.window, defensive)
    ranked, defended, watched = split_pools(snap)
    W = a.window

    head1 = ranked[0]
    head2 = ranked[1] if len(ranked) > 1 else None
    lead = (head1.momentum[W] - head2.momentum[W]) if head2 else float("nan")
    overheated = (head1.dev > a.overheat_dev) or (lead > a.overheat_lead)
    cols, steps, swap_days, tie_days = analyze_ladder(ranked, W, a.sticky_lead)

    # ---------- 打印 ----------
    print("=" * 78)
    print(f"锚定日 {pivot}   窗口 {W}   防守: {', '.join(sorted(defensive))}"
          f"   粘性带: 领先<{a.sticky_lead:.1f}pp 视为平手")
    print(f"主选池 {len(ranked)} 只 (参与调仓) ｜ 备选观察池 {len(watched)} 只 (不参与调仓)")
    print("=" * 78)

    header = (f"{'代码':<8}{'名称':<14}" + "".join(f"{f'横{n:>2}':>9}" for n in cols)
              + f"{'现价':>9}{'今日涨跌':>10}")

    def _print_ladder(rows, title):
        print("\n" + title)
        print(header)
        for r in rows:
            flag = f"  ({r.role})" if r.role else ""
            line = f"{r.code:<8}{(r.name or '-')[:12]:<14}"
            for n in cols:
                line += f"{r.momentum.get(n, float('nan')):>9.2f}"
            line += f"{r.close:>9.3f}{r.chg1:>10.2f}"
            print(line + flag)

    # 排名表 (动横 ladder) —— 末尾附 现价 / 今日涨跌
    _print_ladder(ranked + defended,
                  "【主选池】各 ETF 动横阶梯 "
                  "(%, 第n列 = 冻结价格下未来第 W-n 个交易日的动横20):")

    # 逐列头名/换仓日 (粘性带: 列内领先 < sticky_lead 视为平手, 保留前一头名)
    print("\n逐列头名 (按该列动横降序取 #1; 领先 < 粘性带阈值视为平手, 标注换仓日):")
    for st in steps:
        h, s = st["head"], st["second"]
        note = ""
        if st["held"]:
            note = (f"  (平手带: 领先{st['lead']:+.2f}<{a.sticky_lead:.1f}, "
                    f"保留 {st['eff_head']})")
        elif st["tied"]:
            note = "  (平手带内)"
        swap = "  <== 换仓日" if st["is_swap"] else ""
        print(f"  第{st['idx']:02d}日 (动横{st['n']:<2d}): 头名 {h.code} {h.name} "
              f"{h.momentum[st['n']]:+.2f}   次名 {s.code if s else '-'} "
              f"{(s.momentum[st['n']] if s else float('nan')):+.2f}   "
              f"领先 {st['lead']:+.2f}pp{swap}{note}")

    print("\n" + "-" * 78)
    print(f"【今日】目标 = {head1.code} {head1.name}  现价 {head1.close:.3f} "
          f"(今日 {head1.chg1:+.2f}%)  动横{W} {head1.momentum[W]:+.2f}%, "
          f"次名 {head2.code} {head2.name} {head2.momentum[W]:+.2f}%, 领先 {lead:+.2f}pp")
    if lead < a.sticky_lead:
        print(f"【粘性带】今日领先 {lead:+.2f}pp < {a.sticky_lead:.1f}pp 阈值 -> "
              f"视为平手, 若已持有次名则不换仓; 空仓则按头名建仓。")
    else:
        print(f"【粘性带】今日领先 {lead:+.2f}pp >= {a.sticky_lead:.1f}pp 阈值 -> "
              f"正常换仓, 无粘性过滤。")
    print(f"【乖离】头名 {head1.dev:+.2f}%  过热线 {a.overheat_dev:.0f}%  "
          f"-> {'★ 触发过热熔断, 建议防守' if overheated else '未触发过热, 正常轮动'}")
    if len(swap_days):
        d, n, old, new = swap_days[0]
        print(f"【最近换仓日】第 {d} 个交易日 (动横{n}): {old} -> {new}")
        print(f"【换仓统计】未来 {W} 日内粘性带后换仓 {len(swap_days)} 次 "
              f"(被粘性带抹平的平手列 {tie_days} 列)")
    else:
        print("【换仓】未来 20 日内头名不换人 "
              f"(被粘性带抹平的平手列 {tie_days} 列)")
    print("-" * 78)
    print(f"提示: 粘性带阈值 {a.sticky_lead:.1f}pp, 领先低于该值的换仓信号被过滤; "
          "建议叠加趋势过滤再行动。")

    # ---------- 备选观察池 (放最后, 不干扰主池筛选) ----------
    if watched:
        print("\n" + "=" * 78)
        print(f"【备选观察池】{len(watched)} 只 —— 不参与调仓, 仅供更替候选参考")
        print("=" * 78)
        wh1 = watched[0]
        wh2 = watched[1] if len(watched) > 1 else None
        wlead = (wh1.momentum[W] - wh2.momentum[W]) if wh2 else float("nan")
        gap = wh1.momentum[W] - head1.momentum[W]
        print(f"备选头名 {wh1.code} {wh1.name} 动横{W} {wh1.momentum[W]:+.2f}%   "
              f"次名 {wh2.code if wh2 else '-'} "
              f"{wh2.momentum[W] if wh2 else float('nan'):+.2f}%   领先 {wlead:+.2f}pp")
        print(f"对照主选池头名 {head1.code} {head1.name} {head1.momentum[W]:+.2f}%  ->  "
              f"备选头名{'高出' if gap > 0 else '低于'} {abs(gap):.2f}pp"
              + ("   ★ 出现更替候选" if gap > 0 else ""))
        _print_ladder(watched, "备选观察池 动横阶梯:")


if __name__ == "__main__":
    main()
