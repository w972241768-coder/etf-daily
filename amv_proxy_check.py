"""0AMV（指南针活跃市值）代理可行性验证（2026-10-25）

背景：用户想用 0AMV 的日涨幅波段做择时（+4% 转多 / -2.3% 转空）。
0AMV = Σ(个股活跃流通股本 × 当日价)，其中「活跃流通股本 = 流通股本 - 死筹」，
死筹判定基于筹码分布（持仓>120 交易日无换手等），阈值是指南针专有、不公开。

本脚本检验：能否用「全市场成交额加权平均涨幅」近似 0AMV 的日涨幅？
数据：本地 TDX 全市场 .day（vipdoc/{sh,sz}/lday，含 OHLC + amount + volume）。

结论（已实测）：**不能**。
  2026-07-01 → 2026-10-16，真实 0AMV 268,357 亿 → 141,897 亿（-47.1%），
  代理同期仅 -5.50%，差 8.6 倍 ⇒ 成交额加权涨幅无法替代 0AMV。

根因：0AMV 是**存量市值**，跌的时候还叠加「活筹→死筹」的活性切换（额外缩水），
涨的时候叠加「死筹→活筹」（额外膨胀）；加权涨幅只捕捉价格，抓不到活性切换。
"""
import glob
import time

import numpy as np
import pandas as pd

DT = np.dtype([('date', '<i4'), ('open', '<i4'), ('high', '<i4'), ('low', '<i4'),
               ('close', '<i4'), ('amount', '<f4'), ('vol', '<i4'), ('res', '<i4')])
PATS = ['E:/tdx/vipdoc/sh/lday/sh6*.day', 'E:/tdx/vipdoc/sz/lday/sz00*.day',
        'E:/tdx/vipdoc/sz/lday/sz30*.day']


def build(bars: int = 520):
    files = []
    for p in PATS:
        files += glob.glob(p)
    D, W, WP = [], [], []
    for p in files:
        try:
            raw = open(p, 'rb').read()
        except Exception:                      # noqa: BLE001
            continue
        n = len(raw) // 32
        if n < 30:
            continue
        a = np.frombuffer(raw, dtype=DT)[max(0, n - bars):]
        d = a['date'].astype(np.int64)
        c = a['close'].astype(np.float64) / 100.0
        am = a['amount'].astype(np.float64)
        ok = (c > 0) & (am > 0)
        if ok.sum() < 30:
            continue
        pct = np.zeros_like(c)
        pct[1:] = c[1:] / c[:-1] - 1.0
        m = ok[1:] & ok[:-1]
        if m.sum() == 0:
            continue
        D.append(d[1:][m]); W.append(am[1:][m]); WP.append(am[1:][m] * pct[1:][m])
    df = pd.DataFrame({'date': np.concatenate(D), 'w': np.concatenate(W),
                       'wp': np.concatenate(WP)})
    g = df.groupby('date').agg(w=('w', 'sum'), wp=('wp', 'sum'))
    g['pct'] = g['wp'] / g['w'] * 100.0
    g['n'] = df.groupby('date').size()
    return g[g['n'] >= 2000], len(files)


if __name__ == '__main__':
    t0 = time.time()
    g, nf = build()
    print(f'个股文件 {nf} ｜ 有效交易日 {len(g)} ｜ {g.index.min()}~{g.index.max()} '
          f'｜ {time.time()-t0:.1f}s')
    sub = g.loc[(g.index >= 20260702) & (g.index <= 20261016), 'pct']
    cum = (1 + sub / 100).prod() - 1
    print(f'代理同期累计 {cum*100:+.2f}%   真实 0AMV -47.10%   差距 {abs(-47.10/cum/100):.1f}x')
    print(f'日涨幅 均值 {g["pct"].mean():+.3f}% 标准差 {g["pct"].std():.2f}%')
    g.to_csv('out/amv_proxy.csv', encoding='utf-8-sig')
