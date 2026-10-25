"""不复权 vs 前复权：对动横20 排名影响的实证（可复现，供论证引用）。

口径：
  · 不复权 = 本地通达信 .day（E:/tdx/vipdoc）
  · 前复权 = 腾讯财经在线前复权日线（online_data.fetch_online）
对每个指定代码与日期，分别算 动横20 = P_t / P_{t-20} - 1，并列出差异。
同时统计「近 N 个交易日里，两者动横20 相差 >0.3pp 的天数」。
"""
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import etf_rotation as rot  # noqa: E402
import online_data as od  # noqa: E402

TDX_ROOT = rot.find_tdx_root()
FOCUS = ["588170", "513660", "510300", "563300", "513100"]
EVENTS = [("588170", "2026-07-06", "每 1 拆 3"),
          ("513660", "2026-04-21", "每 1 拆 2"),
          ("510300", "2026-01-19", "分红"),
          ("563300", "2026-09-22", "分红（据动横差推断）")]


def tdx_series(code, want=300):
    if not TDX_ROOT:
        return None
    p = rot.day_file_path(TDX_ROOT, code)
    if not p:
        return None
    rows = rot.read_day(p, rot.price_divisor(code), want)
    if not rows:
        return None
    s = pd.Series({pd.Timestamp(d): c for d, c in rows}).sort_index()
    return s


def mom20(s: pd.Series, d):
    """d 的动横20（需要 d 与其前 20 个交易日都在序列里）。"""
    if d not in s.index:
        # 取 <= d 的最后一天
        idx = s.index[s.index <= d]
        if not len(idx):
            return None
        d = idx[-1]
    pos = s.index.get_loc(d)
    if pos < 20:
        return None
    p0, p1 = float(s.iloc[pos - 20]), float(s.iloc[pos])
    if p0 <= 0:
        return None
    return (p1 / p0 - 1.0) * 100.0


def main():
    print(f"TDX root: {TDX_ROOT}")
    end = pd.Timestamp("2026-09-24")
    beg = (end - pd.Timedelta(days=900)).strftime("%Y-%m-%d")
    res = od.fetch_online(FOCUS, beg=beg, end=end.strftime("%Y-%m-%d"),
                          cache=False, cache_tag="adj_evidence")
    online = {}
    if res["df"] is not None:
        for c, g in res["df"].groupby("code"):
            online[c] = g.set_index("date")["close"].astype(float).sort_index()

    print("\n=== 除权/拆分事件日：动横20 对比 ===")
    for code, d, ev in EVENTS:
        s_raw = tdx_series(code)
        s_qfq = online.get(code)
        m_raw = mom20(s_raw, pd.Timestamp(d)) if s_raw is not None else None
        m_qfq = mom20(s_qfq, pd.Timestamp(d)) if s_qfq is not None else None
        if m_raw is None or m_qfq is None:
            print(f"{code} {d} {ev}: 数据不足（raw={m_raw}, qfq={m_qfq}）")
            continue
        print(f"{code} {d} {ev}: 不复权 {m_raw:+7.2f}%  前复权 {m_qfq:+7.2f}%  "
              f"差 {m_raw - m_qfq:+7.2f}pp")

    print("\n=== 近 400 个交易日：两口径动横20 差异 >0.3pp 的天数 ===")
    for code in FOCUS:
        s_raw, s_qfq = tdx_series(code), online.get(code)
        if s_raw is None or s_qfq is None:
            print(f"{code}: 数据不足")
            continue
        common = s_raw.index.intersection(s_qfq.index)[-400:]
        diffs, bad = [], 0
        for d in common:
            a, b = mom20(s_raw, d), mom20(s_qfq, d)
            if a is None or b is None:
                continue
            diffs.append(abs(a - b))
            if abs(a - b) > 0.3:
                bad += 1
        if diffs:
            print(f"{code}: {bad}/{len(diffs)} 天 >0.3pp ｜ 最大差 {max(diffs):.2f}pp")


if __name__ == "__main__":
    main()
