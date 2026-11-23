#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 zgnb.top「活跃市值」日 K 的最新值，写成前端可直接读取的 amv_live.json。

数据通道
    GET https://www.zgnb.top/api/v1/market/amv/dashboard?key=market
    Header: Authorization: Bearer $ZGNB_TOKEN
    返回 current: {state, days_in_state, amv_yi, pct, close, trade_date, updated}

凭据
    环境变量 ZGNB_TOKEN（GitHub Actions 里存为仓库 Secrets: ZGNB_TOKEN）。
    绝不写入源码、不写进页面、不打印到日志。

输出格式（docs/amv_live.json）
    成功: {"ok":true,"value":..., "change_pct":..., "state":"多头"/"空头",
           "trade_date":"...","updated_at":"...","source":"zgnb.top","fetched_at":"..."}
    失败: {"ok":false,"error":"...","source":"zgnb.top","fetched_at":"..."}

用法
    ZGNB_TOKEN=xxx python fetch_amv.py --out docs/amv_live.json
    python fetch_amv.py --dry-run                 # 只检查凭据与参数，不写文件
    python fetch_amv.py --selftest --out /tmp/x.json   # 不联网，用 mock 验证写入逻辑

异常一律转成 ok:false + 明确 error 文本，不让 CI 因异常而中途失败，
这样页面端能显示「数据异常 + 重试」而不是永远停在「连接中」。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("缺少 requests：请用 venv 的 python（C:/Users/王旭/.workbuddy/binaries/python/envs/default/Scripts/python.exe）")

BASE = "https://www.zgnb.top"
ENDPOINT = BASE + "/api/v1/market/amv/dashboard"
KEY = "market"
TIMEOUT = 15


def beijing_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=8)


def payload_error(msg: str) -> dict:
    return {"ok": False, "error": msg, "source": "zgnb.top",
            "fetched_at": beijing_now().strftime("%Y-%m-%d %H:%M:%S")}


def fetch(token: str) -> dict:
    """联网取数，成功返回数据 dict，任何异常返回 payload_error()。"""
    if not token or not token.strip():
        return payload_error("未配置 ZGNB_TOKEN")

    try:
        r = requests.get(
            ENDPOINT,
            params={"key": KEY},
            headers={"Authorization": "Bearer " + token.strip(),
                     "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.Timeout:
        return payload_error("连接 zgnb.top 超时")
    except requests.RequestException as e:
        # 不打印 e 的完整内容，避免某些库把请求头（含 token）带进异常文本
        return payload_error("网络连接失败：" + type(e).__name__)

    if r.status_code == 401:
        return payload_error("登录态失效，请更新 ZGNB_TOKEN")
    if r.status_code == 403:
        return payload_error("无访问权限（账号可能被限流或降级）")
    if r.status_code == 429:
        return payload_error("请求被限流")
    if r.status_code != 200:
        return payload_error("HTTP " + str(r.status_code))

    try:
        body = r.json()
    except Exception:
        return payload_error("返回内容不是有效 JSON")

    cur = (body or {}).get("current") if isinstance(body, dict) else None
    if not isinstance(cur, dict):
        return payload_error("返回结构变化：缺少 current")

    raw_val = cur.get("amv_yi")
    try:
        val = float(raw_val)
    except (TypeError, ValueError):
        return payload_error("活跃市值数值异常")
    # 亿元量级：A 股活跃市值历史区间约几万~二十几万亿；超出即视为口径变化
    if not (0 < val < 1_000_000):
        return payload_error("活跃市值超出合理范围")

    pct = None
    if cur.get("pct") is not None:
        try:
            pct = round(float(cur["pct"]), 2)
        except (TypeError, ValueError):
            pct = None

    state_num = cur.get("state")
    try:
        state = "多头" if int(state_num) == 1 else "空头"
    except (TypeError, ValueError):
        state = ""

    return {
        "ok": True,
        "value": round(val, 1),
        "change_pct": pct,
        "state": state,
        "trade_date": str(cur.get("trade_date") or ""),
        "updated_at": str(cur.get("updated") or ""),
        "source": "zgnb.top",
        "fetched_at": beijing_now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def atomic_write(path: Path, data: dict) -> None:
    """原子写：避免 CI 中途失败留下半个 JSON，让前端拿到解析错误。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".amv_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    ap = argparse.ArgumentParser(description="拉取 zgnb.top 活跃市值 → amv_live.json")
    ap.add_argument("--out", default="docs/amv_live.json")
    ap.add_argument("--dry-run", action="store_true", help="只检查环境与凭据，不写文件")
    ap.add_argument("--selftest", action="store_true", help="不联网，用 mock 验证写入逻辑")
    args = ap.parse_args()

    if args.selftest:
        mock = {"ok": True, "value": 136935.6, "change_pct": 2.34, "state": "多头",
                "trade_date": "2026-11-23", "updated_at": "2026-11-23 11:30:00",
                "source": "zgnb.top", "fetched_at": beijing_now().strftime("%Y-%m-%d %H:%M:%S")}
        atomic_write(Path(args.out), mock)
        print("[selftest] 已写入 mock 数据 →", args.out)
        return 0

    token = os.environ.get("ZGNB_TOKEN", "")
    if args.dry_run:
        print("[dry-run] ZGNB_TOKEN:", "已配置" if token.strip() else "未配置")
        print("[dry-run] 输出路径:", args.out)
        return 0

    data = fetch(token)
    out_path = Path(args.out)
    # 连续失败且错误原因不变时不改 fetched_at，保证文件内容稳定 → CI 不会反复空提交
    if not data.get("ok") and out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            if (not prev.get("ok")) and prev.get("error") == data.get("error"):
                data["fetched_at"] = prev.get("fetched_at", data["fetched_at"])
        except Exception:
            pass
    old_text = out_path.read_text(encoding="utf-8") if out_path.exists() else None
    atomic_write(out_path, data)
    if old_text == out_path.read_text(encoding="utf-8"):
        print("[AMV] 内容无变化，跳过写入")
        return 0
    if data.get("ok"):
        print(f"[AMV] {data['trade_date']} {data['value']:,.1f} 亿 "
              f"({data['change_pct']:+.2f}%) {data['state']} → {args.out}")
    else:
        # 失败也写文件并正常退出：让页面显示明确错误，CI 不中断
        print("[AMV] 取数失败：" + data.get("error", "未知错误") + f" → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
