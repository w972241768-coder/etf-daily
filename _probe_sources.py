import json
import requests

H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn",
}


def probe(name, url, headers=None, enc=None):
    try:
        r = requests.get(url, headers=headers or H, timeout=15)
        t = r.content.decode(enc or "utf-8", errors="replace")
        print(f"[{name}] {r.status_code} len={len(t)}")
        print("   head:", t[:180].replace("\n", " "))
    except Exception as e:
        print(f"[{name}] FAIL {type(e).__name__}: {e}")


# 1. 东财主域 + 备用域
for h in ["push2his.eastmoney.com", "88.push2his.eastmoney.com", "48.push2his.eastmoney.com"]:
    probe(
        f"em-{h}",
        f"https://{h}/api/qt/stock/kline/get?secid=1.510300&klt=101&fqt=1"
        f"&beg=20190101&end=20500101&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        f"&ut=fa5fd1943c7b386f172d6893dbfba10b",
    )

# 2. 新浪日K（不复权，全历史）
probe(
    "sina-kline",
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData?symbol=sh510300&scale=240&ma=no&datalen=2000",
)

# 3. 腾讯前复权
probe(
    "tx-fqk",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    "?param=sh510300,day,2019-01-01,2026-12-31,3000,qfq",
)

# 4. 网易
probe(
    "163-kline",
    "https://quotes.money.163.com/service/chddata.html"
    "?code=0510300&start=20190101&end=20260924&fields=TCLOSE",
    enc="gbk",
)
