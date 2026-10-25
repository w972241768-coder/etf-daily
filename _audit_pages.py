# -*- coding: utf-8 -*-
"""审计 tool-results 下已落盘的 tdx_kline 分页：每 code 的页、覆盖区间、连续性。"""
import glob, json, os, collections

D = r"C:\Users\王旭\.workbuddy\projects\d-娱乐（音乐、视频）Q-骑牛-ETF轮动战法\229c2fe8-4b6a-4f8f-899e-c2371cd370de\tool-results"

pages = collections.defaultdict(list)
for p in glob.glob(os.path.join(D, "mcp-tdx-connector-tdx_kline-*.txt")):
    try:
        raw = open(p, encoding="utf-8", errors="replace").read()
        i = raw.find("{")
        if i < 0:
            continue
        o, _ = json.JSONDecoder().raw_decode(raw[i:])
    except Exception as e:
        print("PARSE_FAIL", os.path.basename(p), e)
        continue
    rows = o.get("Rows") or []
    if not rows:
        continue
    code = str(o.get("Code") or "")
    sx = o.get("Startxh")
    # 归一化 startxh：None 视作 0
    try:
        sxi = int(sx) if sx not in (None, "", "None") else 0
    except Exception:
        sxi = 0
    dates = [str(r.get("Data")) for r in rows]
    tq = o.get("AttachInfo") or ""
    pages[code].append({"file": os.path.basename(p), "sx": sxi, "n": len(rows),
                        "d0": dates[0], "d1": dates[-1], "period": o.get("Period"),
                        "tq": str(tq)[:40]})

print("codes:", len(pages))
for code in sorted(pages):
    ps = sorted(pages[code], key=lambda x: -x["sx"])  # 0, 700, 1400 顺序 = 倒序时间
    # 只打印前复权且步长 700 的页
    good = [x for x in ps if x["sx"] in (0, 700, 1400) and x["n"] >= 700]
    tag = "QFQ700" if good else "old"
    print(f"\n== {code}  [{tag}] all_pages={len(ps)} good={len(good)}")
    for x in ps:
        print(f"   sx={x['sx']:>5} n={x['n']:>4} {x['d0']}~{x['d1']} {os.path.basename(x['file'])[-14:]}")
