"""在线日线数据源（免费、无需密钥）。

================================================================
数据源取舍（2026-09-26 实测论证，可复现见 adj_evidence.py）
================================================================
1) 新浪 getKLineData（不复权）：与本地通达信 .day **逐日完全一致**（3 位小数级），
   另有 Yahoo（510300.SS 等）第三方复核一致。→ 本机可稳定访问，作为主数据源。
2) 腾讯 fqkline（前复权）：2026-09-26 起该接口对近期日期曾返回错误价格
   （510300 差 0.3~1.4%、588170 差 4~6%）→ 故**不用于本地本机**；但腾讯行情是
   全局 CDN，境外（GitHub 云端 runner）可稳定访问，且只提供前复权价、与长历史 CSV
   同口径，故作为**云端兜底源**：当新浪对境外 IP 被拦时自动切换，按长历史 T0 基准
   对齐缩放后与回测完全同口径（实测 510300 腾讯前复权=新浪前复权=4.178）。
3) 东方财富 push2his：本机 IP 被限流（RemoteDisconnected / 502），不可用。

【三级容错】fetch_online 实际策略：① 快速探测新浪是否可达 → 可达走新浪（不复权×F）；
   ② 新浪整体不可达（典型：GitHub 云端境外 IP）→ 兜底腾讯（前复权×T0 对齐）；
   ③ 两者皆失败 → 返回 df=None，由调用方回退仓库历史 CSV（不崩溃，但数据滞后）。

【前复权校正】新浪给的是不复权价。真正的「前复权」= 不复权价 ÷ 复权因子 F(t)，
F(t) 只在分红/拆分日变化。已核验的长历史 CSV（tdx_adj_long_2019_2026.csv，
抓取时经 verify_long_data.py 与通达信逐项校验）提供截至 T0 的前复权序列，
于是 F(T0) = 不复权(T0) / 前复权(T0)，把在线不复权序列整体除以 F(T0) 即得
与长历史同口径的前复权序列；只要 T0 之后没有新的分红/拆分，结果严格正确
（新事件会在下次更新长历史时被吸收；T0 过期时本模块会明确告警）。
"""
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "out")
TIMEOUT = 15
WORKERS = 8
RETRIES = 2
KLINE_BARS = 480          # 新浪取约 480 根日线（>2 年，够 MA200 与动横窗口）
ADJ_CANDIDATES = [
    os.path.join(HERE, "tdx_adj_long_2019_2026.csv"),
    os.path.join(os.path.dirname(HERE), "tdx_adj_long_2019_2026.csv"),
    r"D:/娱乐（音乐、视频）Q-骑牛/ETF轮动战法/tdx_adj_long_2019_2026.csv",
]

# ------------------------------------------------- 新浪不复权日线（主源）
SINA_KLINE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
              "/CN_MarketData.getKLineData")
SINA_QUOTE = "https://hq.sinajs.cn/list="

# ------------------------------------------------- 腾讯行情（云端可达，作为新浪兜底）
# 新浪对境外 IP 常限流/拦截（GitHub 云端 runner 即典型受害者）；腾讯行情为全局 CDN，
# 境外可稳定访问。腾讯 fqkline 仅提供【前复权】价（不复权端点已废弃），与长历史 CSV
# 同为前复权口径，故取数后按长历史 T0 基准做一次对齐缩放即可与现有回测完全一致。
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_QUOTE = "https://qt.gtimg.cn/q="

# ------------------------------------------------- 中证指数日线（防守位指数口径）
# 512040 价值ETF富国 / 价值100ETF富国 跟踪【中证国信价值指数 931052.CSI】。
# 用户 2026-10-20 需求：指数跌破 MA200×0.97 往往早于基金跌破，需要同时看两个口径。
CSINDEX_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
CSINDEX_CACHE = os.path.join(HERE, "out", "csindex_{code}.csv")



def _market(code: str) -> str:
    return "sh" if code.startswith(("5", "6", "9")) else "sz"


def _http_get(url: str, timeout: int = TIMEOUT, gbk: bool = False,
              referer: str = "https://finance.sina.com.cn"):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("gbk", "replace") if gbk else r.read().decode("utf-8", "replace")


def fetch_sina_kline(code: str, datalen: int = KLINE_BARS):
    """新浪不复权日线。返回 (code, DataFrame[date, close]) 或 (code, None)。"""
    sym = _market(code) + code
    last_err = None
    for _ in range(RETRIES + 1):
        try:
            txt = _http_get(f"{SINA_KLINE}?symbol={sym}&scale=240&ma=no&datalen={datalen}")
            rows = json.loads(txt)
            if not rows:
                return code, None
            recs = []
            for r in rows:
                d = str(r.get("day") or "")[:10]
                c = float(r.get("close"))
                if d and c == c and c > 0:
                    recs.append((pd.Timestamp(d), c))
            if len(recs) < 30:
                return code, None
            return code, pd.DataFrame(recs, columns=["date", "close"])
        except Exception as e:                       # noqa: BLE001
            last_err = e
            time.sleep(0.5)
    print(f"[ONLINE] {code} 新浪取数失败: {last_err}")
    return code, None


def fetch_sina_kline_sh000001(datalen: int = 90):
    """上证指数（sh000001）日线，返回长表 DataFrame[date, code, close]。

    ★ 必须单独走这个函数，不能用 `fetch_online(["000001"])`：
      `_market("000001")` 只按首位数字判断，会返回 "sz" ⇒ 取到 **平安银行**，
      再除以长历史里对上证算出的复权因子，点位看着像大盘（约 3800）实则完全是另一只票。
      2026-10-23 排查「② 近期换仓复盘 同期上证」时踩到，实测 sh000001=3690 而
      sz000001 复权后≈3888，肉眼无法分辨，属于极危险的静默错值。
    指数无分红拆分，不做任何复权。数据源：新浪优先，不可达时兜底腾讯。
    """
    last_err = None
    if _sina_reachable("sh000001"):
        for _ in range(RETRIES + 1):
            try:
                txt = _http_get(f"{SINA_KLINE}?symbol=sh000001&scale=240&ma=no&datalen={datalen}")
                rows = json.loads(txt)
                if not rows:
                    break
                recs = []
                for r in rows:
                    d = str(r.get("day") or "")[:10]
                    try:
                        c = float(r.get("close"))
                    except (TypeError, ValueError):
                        continue
                    if d and c == c and c > 0:
                        recs.append((pd.Timestamp(d), "000001", c))
                if len(recs) < 5:
                    break
                return pd.DataFrame(recs, columns=["date", "code", "close"])
            except Exception as e:                       # noqa: BLE001
                last_err = e
                time.sleep(0.5)
        print(f"[ONLINE] sh000001 新浪取数失败: {last_err}")
    # 腾讯兜底（前复权；指数无分红，前复权==不复权）
    return fetch_tencent_kline_sh000001(datalen)


def sina_quotes_ex(codes):
    """新浪实时行情快照（GBK）。返回 {code: dict(px=最新价, date=行情日期, prev=昨收)}。

    ★ 关键用途：A 股收盘后新浪日线接口通常要等到 16:00 前后才发布当日 K 线，
    15:00~16:00 的「空窗期」内日线最后一根仍是昨日；此时快照的「最新价」就是
    当日收盘价（快照行情日期 field[30] 可用于校验是否为今日）。
    """
    try:
        syms = ",".join(_market(c) + c for c in codes if c)
        txt = _http_get(SINA_QUOTE + syms, gbk=True)
        out = {}
        for line in txt.split("\n"):
            if "=" not in line or '"' not in line:
                continue
            key, val = line.split("=", 1)
            code = key.strip().split("_")[-1][2:]
            if len(code) != 6:
                continue
            p = val.strip().strip(";").strip('"').split(",")
            if len(p) < 4:
                continue
            try:
                px = float(p[3])
            except ValueError:
                continue
            if px > 0:
                d = str(p[30]) if len(p) > 30 else ""
                prev = None
                try:
                    prev = float(p[2])
                except ValueError:
                    pass
                out[code] = dict(px=px, date=d[:10], prev=prev)
        return out
    except Exception:                                # noqa: BLE001
        return {}


def sina_quotes(codes):
    """新浪实时行情（盘中快照，GBK）。返回 {code: price}，失败返回空 dict。"""
    q = sina_quotes_ex(codes)
    return {c: v["px"] for c, v in q.items() if v.get("px")}


def _trade_session_now():
    """当前北京时间是否在 A 股交易时段（用于判断最后一根是否为盘中实时）。"""
    bj = pd.Timestamp.now(tz="UTC").tz_convert("Asia/Shanghai")
    if bj.weekday() >= 5:
        return False, bj
    hm = bj.hour * 60 + bj.minute
    return (9 * 60 + 25 <= hm <= 15 * 60 + 5), bj


def fetch_online_raw(codes, *, datalen: int = KLINE_BARS):
    """并发取新浪不复权日线。返回 dict(df, ok, fails, lastdate, info)。"""
    codes = [c for c in codes if c]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        raw = list(ex.map(lambda c: fetch_sina_kline(c, datalen), codes))
    fails = [c for c, d in raw if d is None]
    ok_n = len(raw) - len(fails)
    recs, lastdate = [], ""
    for c, d in raw:
        if d is None:
            continue
        for _, row in d.iterrows():
            recs.append((row["date"], c, row["close"]))
        lastdate = max(lastdate, str(d["date"].max().date()))
    info = [f"[ONLINE] 新浪不复权日线: {ok_n}/{len(codes)} 只成功, {len(recs)} 根, "
            f"最新 {lastdate}"]
    if fails:
        info.append(f"[ONLINE] 失败 {len(fails)} 只: {','.join(fails)}")
    df = None
    if recs:
        df = pd.DataFrame(recs, columns=["date", "code", "close"])
        df["code"] = df["code"].astype(str)
    return dict(df=df, ok=f"{ok_n}/{len(codes)}", fails=fails,
                lastdate=lastdate, info=info)


# ------------------------------------------------- 腾讯行情（新浪兜底源）
def _tencent_sym(code: str) -> str:
    """腾讯行情对上证指数 000001 必须显式 sh 前缀（_market 会误判为 sz）。"""
    return "sh000001" if code == "000001" else (_market(code) + code)


def _src_label(family: str) -> str:
    """数据源中文标签（写进报告页眉）。"""
    return "腾讯财经" if family == "tencent" else "新浪财经"


def _sina_reachable(sample_sym: str = "sh510300", timeout: int = 8) -> bool:
    """快速探测新浪是否可达（云端境外 IP 常被拦）。sample_sym 需带市场前缀。"""
    try:
        txt = _http_get(f"{SINA_KLINE}?symbol={sample_sym}&scale=240&ma=no&datalen=3",
                        timeout=timeout)
        rows = json.loads(txt)
        return bool(rows)
    except Exception:                                # noqa: BLE001
        return False


def fetch_tencent_kline(code: str, datalen: int = KLINE_BARS):
    """腾讯前复权日K。返回 (code, DataFrame[date, close]) 或 (code, None)。"""
    sym = _tencent_sym(code)
    url = f"{TENCENT_KLINE}?param={sym},day,,,{datalen},qfq"
    last_err = None
    for _ in range(RETRIES + 1):
        try:
            txt = _http_get(url, referer="https://gu.qq.com/")
            j = json.loads(txt)
            node = (j.get("data") or {}).get(sym)
            if not node:
                return code, None
            rows = node.get("qfqday") or node.get("day")
            if not rows:
                return code, None
            recs = []
            for r in rows:
                if len(r) < 6:
                    continue
                d = str(r[0])[:10]
                try:
                    c = float(r[2])          # [date, open, close, high, low, volume]
                except (TypeError, ValueError):
                    continue
                if d and c == c and c > 0:
                    recs.append((pd.Timestamp(d), c))
            if len(recs) < 30:
                return code, None
            return code, pd.DataFrame(recs, columns=["date", "close"])
        except Exception as e:                       # noqa: BLE001
            last_err = e
            time.sleep(0.5)
    print(f"[ONLINE] {code} 腾讯取数失败: {last_err}")
    return code, None


def fetch_tencent_kline_sh000001(datalen: int = 90):
    """上证指数（sh000001）前复权日线，返回长表 DataFrame[date, code, close]。"""
    last_err = None
    for _ in range(RETRIES + 1):
        try:
            url = f"{TENCENT_KLINE}?param=sh000001,day,,,{datalen},qfq"
            txt = _http_get(url, referer="https://gu.qq.com/")
            j = json.loads(txt)
            node = (j.get("data") or {}).get("sh000001")
            if not node:
                return None
            rows = node.get("qfqday") or node.get("day")
            if not rows:
                return None
            recs = []
            for r in rows:
                if len(r) < 6:
                    continue
                d = str(r[0])[:10]
                try:
                    c = float(r[2])
                except (TypeError, ValueError):
                    continue
                if d and c == c and c > 0:
                    recs.append((pd.Timestamp(d), "000001", c))
            if len(recs) < 5:
                return None
            return pd.DataFrame(recs, columns=["date", "code", "close"])
        except Exception as e:                       # noqa: BLE001
            last_err = e
            time.sleep(0.5)
    print(f"[ONLINE] sh000001 腾讯取数失败: {last_err}")
    return None


def fetch_tencent_raw(codes, *, datalen: int = KLINE_BARS):
    """并发取腾讯前复权日线。返回 dict(df, ok, fails, lastdate, info)。"""
    codes = [c for c in codes if c]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        raw = list(ex.map(lambda c: fetch_tencent_kline(c, datalen), codes))
    fails = [c for c, d in raw if d is None]
    ok_n = len(raw) - len(fails)
    recs, lastdate = [], ""
    for c, d in raw:
        if d is None:
            continue
        for _, row in d.iterrows():
            recs.append((row["date"], c, row["close"]))
        lastdate = max(lastdate, str(d["date"].max().date()))
    info = [f"[ONLINE] 腾讯前复权日线: {ok_n}/{len(codes)} 只成功, {len(recs)} 根, "
            f"最新 {lastdate}"]
    if fails:
        info.append(f"[ONLINE] 失败 {len(fails)} 只: {','.join(fails)}")
    df = None
    if recs:
        df = pd.DataFrame(recs, columns=["date", "code", "close"])
        df["code"] = df["code"].astype(str)
    return dict(df=df, ok=f"{ok_n}/{len(codes)}", fails=fails,
                lastdate=lastdate, info=info)


def tencent_quotes_ex(codes):
    """腾讯实时行情快照（GBK）。返回 {code: dict(px, date, prev)}。

    字段(~分隔): [3]=最新价 [4]=昨收 [30]=行情时间戳 YYYYMMDDHHMMSS。
    """
    try:
        syms = ",".join(_tencent_sym(c) for c in codes if c)
        txt = _http_get(TENCENT_QUOTE + syms, gbk=True, referer="https://gu.qq.com/")
        out = {}
        for line in txt.split("\n"):
            if "=" not in line or '"' not in line:
                continue
            key, val = line.split("=", 1)
            sym = key.strip().split("_")[-1]        # sh510300
            code = sym[2:] if len(sym) >= 8 else ""
            if len(code) != 6:
                continue
            p = val.strip().strip(";").strip('"').split("~")
            if len(p) < 6:
                continue
            try:
                px = float(p[3])
            except ValueError:
                continue
            if px <= 0:
                continue
            d = ""
            if len(p) > 30 and p[30]:
                ts = p[30]
                if len(ts) >= 8:
                    d = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
            try:
                prev = float(p[4])
            except ValueError:
                prev = None
            out[code] = dict(px=px, date=d, prev=prev)
        return out
    except Exception:                                # noqa: BLE001
        return {}


def tencent_quotes(codes):
    """腾讯实时行情（盘中快照，GBK）。返回 {code: price}，失败返回空 dict。"""
    q = tencent_quotes_ex(codes)
    return {c: v["px"] for c, v in q.items() if v.get("px")}


# ------------------------------------------------- 前复权校正
def find_adj_csv(explicit: str = None):
    """挑数据最新的已核验前复权长历史 CSV。返回 (path, lastdate_str) 或 (None, None)。"""
    order = []
    if explicit:
        order.append(explicit)
    env = os.environ.get("ETF_ADJ_CSV")
    if env:
        order.append(env)
    order.extend(ADJ_CANDIDATES)
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


def _load_adj_base(path: str):
    """长历史 -> {code: pd.Series(date, close)}（前复权）。"""
    try:
        df = pd.read_csv(path, dtype={"code": str})
        df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
        out = {}
        for c, g in df.groupby("code"):
            out[c] = g.sort_values("date").set_index("date")["close"].astype(float)
        return out
    except Exception:                                # noqa: BLE001
        return None


def fetch_online(codes, *, adj_csv=None, datalen: int = KLINE_BARS,
                 cache=True, cache_tag=None, live_quote=True):
    """在线日线（默认 = 新浪不复权 × 前复权因子校正）。

    返回 dict:
      df      : 长表 (date, code, close) —— 已按前复权因子校正
      ok      : 成功只数 / 总数
      fails   : 失败代码列表
      src     : 数据源标签（写进报告页眉）
      live    : 最后一根是否为盘中实时快照
      lastdate: 数据最新日期
      factors : {code: 复权因子}（=1 表示无近期分红/拆分）
      info    : 诊断信息行
    """
    codes = [c for c in codes if c]
    if not codes:
        return dict(df=None, ok="0/0", fails=[], src="在线(空池)", live=False,
                    lastdate=None, factors={}, info=["[ONLINE] 池为空"])
    # ---- 数据源选择：新浪（本地可达）优先；整体不可达时兜底腾讯（云端可达）----
    sample_sym = _tencent_sym(codes[0])
    if _sina_reachable(sample_sym):
        res = fetch_online_raw(codes, datalen=datalen)
        family = "sina"
    else:
        res = dict(df=None, ok="0/0", fails=list(codes), lastdate="",
                   info=["[ONLINE] 新浪不可达（疑似境外 IP 被拦），改用腾讯兜底"])
        family = None
    info = list(res["info"])
    if res["df"] is None:
        tres = fetch_tencent_raw(codes, datalen=datalen)
        if tres["df"] is not None:
            res = tres
            family = "tencent"
        else:
            return dict(df=None, ok=res["ok"], fails=(res["fails"] + tres["fails"]),
                        src="新浪+腾讯 在线均失败", live=False, lastdate=None,
                        factors={}, info=info + tres["info"])

    # ---- 前复权因子：F(T0) = 不复权(T0) / 前复权(T0)，T0 = 与长历史的最后共有时 ----
    adj_path, adj_end = find_adj_csv(adj_csv)
    base = _load_adj_base(adj_path) if adj_path else None
    mult, factors, adj_note_codes = {}, {}, []
    if base:
        raw_by = {c: g.sort_values("date").set_index("date")["close"].astype(float)
                  for c, g in res["df"].groupby("code")}
        for c in codes:
            s_raw = raw_by.get(c)
            s_q = base.get(c)
            m, repF = 1.0, 1.0
            if s_raw is not None and s_q is not None and len(s_raw) and len(s_q):
                common = s_raw.index.intersection(s_q.index)
                if len(common):
                    t0 = common[-1]
                    q0, r0 = float(s_q.loc[t0]), float(s_raw.loc[t0])
                    if q0 > 0 and r0 > 0:
                        if family == "sina":
                            m, repF = q0 / r0, r0 / q0   # 不复权→前复权: 乘 adj/raw
                        else:
                            m, repF = q0 / r0, q0 / r0    # 前复权对齐长历史基准(T0)
            mult[c], factors[c] = m, repF
            if abs(repF - 1.0) > 1e-6:
                adj_note_codes.append(f"{c}(F={repF:.4f})")
        if adj_note_codes:
            info.append(f"[ONLINE] 已按前复权因子对齐长历史基准(T0={adj_end}): "
                        f"{', '.join(adj_note_codes)}")
        # 长历史新鲜度：T0 距今超过 35 天则新事件可能未被吸收
        if adj_end:
            end_d = pd.to_datetime(str(adj_end))
            gap = res["df"]["date"].max() - end_d
            if gap > pd.Timedelta(days=35):
                info.append(f"[ONLINE] ⚠ 前复权长历史({adj_end})已过期 {gap.days} 天，"
                            f"期间若有新的分红/拆分，动横口径会有偏差；"
                            f"建议恢复后重跑 fetch_adj_long.py")
    else:
        mult = {c: 1.0 for c in codes}
        factors = {c: 1.0 for c in codes}
        info.append("[ONLINE] ⚠ 未找到已核验的前复权长历史 CSV，本次按原始口径"
                    "（分红/拆分窗口内动横会有偏差）")

    df = res["df"].copy()
    df["_m"] = df["code"].map(mult).astype(float)
    df["close"] = df["close"] * df["_m"]
    df = df.drop(columns=["_m"])

    # ---- 盘中实时 / 收盘后空窗期补全 ----
    in_session, bj = _trade_session_now()
    today_str = bj.strftime("%Y-%m-%d")
    lastdate = res["lastdate"]
    live = False
    appended = False
    if live_quote:
        q_ex = (sina_quotes_ex if family == "sina" else tencent_quotes_ex)(codes)
        q = {c: v["px"] for c, v in q_ex.items() if v.get("px")}
        qdate = {c: (v.get("date") or "") for c, v in q_ex.items()}
        # 机制1：交易时段内 + 末根已是今日 → 覆盖为盘中实时
        if in_session and lastdate == today_str and q:
            n_upd = 0
            for c, px in q.items():
                m_ = (df["code"] == c) & (df["date"] == pd.Timestamp(today_str))
                if m_.any():
                    df.loc[m_, "close"] = px * mult.get(c, 1.0)
                    n_upd += 1
            if n_upd:
                live = True
                info.append(f"[ONLINE] 盘中实时：{_src_label(family)}行情快照覆盖 "
                            f"{n_upd} 只当日收盘价")
        if in_session and lastdate == today_str:
            live = True
        # 机制2：收盘后空窗期（末根仍非今日，但已开盘）→ 用快照补今日K线，需快照日期==今日
        bj_hm = bj.hour * 60 + bj.minute
        market_opened = (bj.weekday() < 5 and bj_hm >= 9 * 60 + 25)
        if (not live) and lastdate != today_str and market_opened and q_ex:
            new_rows, n_upd = [], 0
            for c in codes:
                v = q_ex.get(c)
                if not v or qdate.get(c) != today_str:
                    continue
                px = float(v["px"])
                if not (px == px and px > 0):
                    continue
                fc = mult.get(c, 1.0)
                m_ = (df["code"] == c) & (df["date"] == pd.Timestamp(today_str))
                if m_.any():
                    df.loc[m_, "close"] = px * fc
                else:
                    new_rows.append((pd.Timestamp(today_str), c, px * fc))
                n_upd += 1
            if new_rows:
                df = pd.concat([df, pd.DataFrame(new_rows,
                                                 columns=["date", "code", "close"])],
                               ignore_index=True)
            if n_upd:
                lastdate = today_str
                if in_session:
                    live = True
                    info.append(f"[ONLINE] 盘中实时：{_src_label(family)}日线尚未生成当日K线，"
                                f"已用行情快照最新价补 {n_upd} 只标的的当日K线")
                else:
                    appended = True
                    info.append(f"[ONLINE] 收盘后空窗期补全：{_src_label(family)}日线尚未发布当日K线，"
                                f"已用行情快照收盘价补 {n_upd} 只标的的当日K线")

    # 当日缓存（供失败回退时复用）
    if cache and df is not None:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tag = cache_tag or today_str
            df.to_csv(os.path.join(CACHE_DIR, f"online_adj_{tag}.csv"),
                      index=False, encoding="utf-8-sig")
        except Exception as e:                       # noqa: BLE001
            info.append(f"[ONLINE] 缓存写入失败(忽略): {e}")

    src = (f"{_src_label(family)} 日线(在线, 前复权校正, {'盘中实时' if live else ('已收盘·快照补全' if appended else '已收盘')})"
           if base else f"{_src_label(family)} 日线(在线, 原始口径)")
    return dict(df=df, ok=res["ok"], fails=res["fails"], src=src, live=live,
                lastdate=lastdate, factors=factors, info=info)


def load_cache(tag=None):
    """读取当日在线缓存(失败回退用)。"""
    if tag is None:
        bj = pd.Timestamp.now(tz="UTC").tz_convert("Asia/Shanghai")
        tag = bj.strftime("%Y-%m-%d")
    p = os.path.join(CACHE_DIR, f"online_adj_{tag}.csv")
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_csv(p, dtype={"code": str})
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:                                # noqa: BLE001
        return None


# ------------------------------------------------- 美股指数（净值曲线对比用）
NDX_SYMBOL = "^IXIC"         # 纳斯达克综合指数(Yahoo)
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart"


def fetch_us_index(symbol: str = NDX_SYMBOL, beg: str = None, end: str = None):
    """美股指数日线(Yahoo Finance chart 接口, 免费无密钥)。返回 DataFrame(date, close) 或 None。

    Yahoo 的 ^IXIC = 纳斯达克综合指数。时间戳为 UTC, 转成日期后与 A 股交易日按公历日对齐
    （美股收盘日与北京日期基本同日，少数跨日夜盘差一天；仅作曲线对比、不参与信号计算）。
    """
    import time as _time
    try:
        t1 = int(_time.mktime(_time.strptime(beg, "%Y-%m-%d")))
        t2 = int(_time.mktime(_time.strptime(end, "%Y-%m-%d")) + 86400)
    except Exception:                                # noqa: BLE001
        return None
    url = (f"{YAHOO_URL}/{symbol}?period1={t1}&period2={t2}&interval=1d")
    try:
        import urllib.request as _ur
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(req, timeout=20) as r:
            import json as _json
            d = _json.load(r)
        res = (d.get("chart") or {}).get("result") or []
        if not res:
            return None
        item = res[0]
        ts = item.get("timestamp") or []
        closes = (item.get("indicators") or {}).get("quote", [{}])[0].get("close")
        if not ts or not closes:
            return None
        recs = [(pd.Timestamp(t, unit="s", tz="UTC").date(), c)
                for t, c in zip(ts, closes) if c is not None]
        df = pd.DataFrame(recs, columns=["date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:                                # noqa: BLE001
        return None

# ------------------------------------------------- 中证指数日线（指数口径 MA200）
def fetch_csindex_daily(code: str = "931052", beg: str = "2024-01-01",
                        end: str = None, timeout: int = 20):
    """中证指数日线（如 931052 中证国信价值）。

    源优先中证指数官网(csindex.com.cn, 官方、含 OHLC)，失败回退东方财富
    (secid=2.<code>, 中证指数市场号 2)。返回 DataFrame(date, close)，失败返回 None。
    结果缓存到 out/csindex_<code>.csv：断网时回退缓存（并标注）。
    """
    import datetime as _dt
    if not end:
        end = _dt.date.today().strftime("%Y%m%d")
    b = str(beg).replace("-", "")[:8]
    e = str(end).replace("-", "")[:8]
    recs = None
    # --- 1) 中证指数官网 ---
    try:
        import urllib.request as _ur
        import urllib.parse as _up
        q = _up.urlencode({"indexCode": code, "startDate": b, "endDate": e})
        req = _ur.Request(f"{CSINDEX_URL}?{q}",
                          headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        rows = (j or {}).get("data") or []
        out = []
        for it in rows:
            d = str(it.get("tradeDate") or "")[:8]
            c = it.get("close")
            if len(d) == 8 and c is not None:
                out.append((pd.Timestamp(f"{d[:4]}-{d[4:6]}-{d[6:]}"), float(c)))
        if len(out) >= 60:
            recs = out
    except Exception:                                # noqa: BLE001
        recs = None
    # --- 2) 东方财富兜底 ---
    if recs is None:
        try:
            import urllib.request as _ur
            url = (f"{EM_KLINE_URL}?secid=2.{code}&fields1=f1,f2,f3&fields2=f51,f53"
                   f"&klt=101&fqt=0&beg={b}&end={e}")
            req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _ur.urlopen(req, timeout=timeout) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            kl = ((j or {}).get("data") or {}).get("klines") or []
            out = []
            for line in kl:
                parts = str(line).split(",")
                if len(parts) >= 2:
                    out.append((pd.Timestamp(parts[0]), float(parts[1])))
            if len(out) >= 60:
                recs = out
        except Exception:                            # noqa: BLE001
            recs = None
    cache = CSINDEX_CACHE.format(code=code)
    if recs:
        df = pd.DataFrame(recs, columns=["date", "close"]).drop_duplicates("date")
        df = df.sort_values("date").reset_index(drop=True)
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            df.assign(date=df["date"].dt.strftime("%Y-%m-%d")).to_csv(
                cache, index=False)
        except Exception:                            # noqa: BLE001
            pass
        return df
    # --- 3) 断网回退缓存 ---
    try:
        if os.path.exists(cache):
            df = pd.read_csv(cache)
            df["date"] = pd.to_datetime(df["date"])
            return df.sort_values("date").reset_index(drop=True)
    except Exception:                                # noqa: BLE001
        pass
    return None


def index_ma_dev(code: str = "931052", ma: int = 200, beg: str = "2024-01-01"):
    """指数相对自身 MA200 的偏离。返回 dict(code, date, close, ma, dev) 或 None。"""
    df = fetch_csindex_daily(code, beg)
    if df is None or len(df) < ma:
        return None
    s = df.set_index("date")["close"].astype(float).dropna()
    m = s.rolling(ma).mean()
    if pd.isna(m.iloc[-1]):
        return None
    return dict(code=code, date=s.index[-1].strftime("%Y-%m-%d"),
                close=float(s.iloc[-1]), ma=float(m.iloc[-1]),
                dev=float(s.iloc[-1] / m.iloc[-1] - 1.0) * 100.0,
                bars=int(len(s)))

