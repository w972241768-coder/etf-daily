# -*- coding: utf-8 -*-
"""解析 TDX tdx_kline 工具存盘的 txt 结果 -> 长表 CSV (code,date,open,high,low,close)"""
import glob, json, os, sys, csv

TOOL_DIR = r"C:\Users\王旭\.workbuddy\projects\d-娱乐（音乐、视频）Q-骑牛-ETF轮动战法\229c2fe8-4b6a-4f8f-899e-c2371cd370de\tool-results"
OUT = r"D:\娱乐（音乐、视频）Q-骑牛\ETF轮动战法\tdx_adj_raw.csv"


def parse_one(path):
    s = open(path, encoding="utf-8", errors="replace").read()
    i = s.find("{")
    if i < 0:
        return None, None
    obj, _ = json.JSONDecoder().raw_decode(s[i:])
    code = str(obj.get("Code"))
    rows = obj.get("Rows", [])
    out = []
    for r in rows:
        out.append((code, r["Data"], float(r["Open"]), float(r["High"]),
                    float(r["Low"]), float(r["Close"])))
    return code, out


def main():
    files = sorted(glob.glob(os.path.join(TOOL_DIR, "mcp-tdx-connector-tdx_kline-*.txt")))
    data = {}
    for f in files:
        code, rows = parse_one(f)
        if code is None:
            print("SKIP", os.path.basename(f)); continue
        # 同一 code 多份文件时保留【行数最多】的那份 (更长历史)
        if code in data and len(data[code][0]) >= len(rows):
            continue
        data[code] = (rows, os.path.basename(f))
    data = {k: v[0] for k, v in data.items()}
    for code, rows in data.items():
        print(f"{code}: {len(rows)} rows  {rows[0][1]}~{rows[-1][1]}  last_close={rows[-1][5]}")
    # write long csv, sorted
    allrows = []
    for code, rows in data.items():
        allrows.extend(rows)
    allrows.sort(key=lambda x: (x[0], x[1]))
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "date", "open", "high", "low", "close"])
        w.writerows(allrows)
    print("WROTE", OUT, len(allrows), "rows,", len(data), "codes")


if __name__ == "__main__":
    main()
