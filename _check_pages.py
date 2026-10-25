import glob
import json
import os

D = r"C:\Users\王旭\.workbuddy\projects\d-娱乐（音乐、视频）Q-骑牛-ETF轮动战法\229c2fe8-4b6a-4f8f-899e-c2371cd370de\tool-results"

for p in sorted(glob.glob(os.path.join(D, "mcp-tdx-connector-tdx_kline-*.txt")), key=os.path.getmtime)[-4:]:
    raw = open(p, encoding="utf-8", errors="replace").read()
    o, _ = json.JSONDecoder().raw_decode(raw[raw.find("{"):])
    r = o["Rows"]
    print(os.path.basename(p)[-16:], o.get("Code"), o.get("Startxh"), len(r),
          r[0]["Data"], "->", r[-1]["Data"], r[0]["Close"], r[-1]["Close"])
