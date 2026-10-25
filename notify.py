#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 轮动日报 ·「仅在状态切换当天提醒」

设计原则：**不换仓不打扰**。
  平时（持仓没变、没触发任何规则）只打印一行「无变化」然后退出，什么都不发；
  只有下面 6 类事件发生时才推送一条短消息。

用法：
  python notify.py --state docs/state.json                 # 与仓库上一版比对
  python notify.py --state docs/state.json --dry-run        # 只打印不推送
  python notify.py --state docs/state.json --prev x.json    # 指定旧快照（本地调试）

推送渠道（全部免费，配哪个用哪个，都不配就只打印日志）：
  PUSHPLUS_TOKEN   微信 pushplus（https://www.pushplus.plus 微信扫码取 token）
  SERVERCHAN_KEY   Server酱（https://sct.ftqq.com 微信扫码取 SendKey）
  BARK_URL         Bark（iOS，形如 https://api.day.app/你的key）
  WEBHOOK_URL      任意自建 webhook，POST JSON {"text": "..."}
  邮件             SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / MAIL_TO（可选 SMTP_TLS=0）

⚠️ 本脚本任何失败都不应阻塞日报生成 —— 调用方按「提醒失败但日报成功」处理。
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime

try:                                   # Windows GBK 控制台下打印中文不崩
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001
    pass

SITE = "https://w972241768-coder.github.io/etf-daily/"


# --------------------------------------------------------------------------- 读快照
def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                  # noqa: BLE001
        return None


def load_prev_from_git(path_in_repo="docs/state.json"):
    """从 HEAD 里取上一版快照。

    ⚠️ 必须在 workflow 的「提交」步骤**之前**调用：此时 HEAD 仍是上一次的提交，
       而工作区里的 state.json 已经是刚生成的新版。
    """
    try:
        out = subprocess.run(["git", "show", f"HEAD:{path_in_repo}"],
                             capture_output=True, text=True, encoding="utf-8",
                             timeout=30)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return json.loads(out.stdout)
    except Exception:                  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- 事件判定
def _fmt_hold(code, name):
    if not code:
        return "—"
    if code == "CASH":
        return "空仓持币"
    return f"{code} {name or ''}".strip()


def diff_events(old, new):
    """返回 [(标题, 明细行列表)]。无变化返回 []。"""
    ev = []
    if not old:
        return ev                        # 首次运行：只建立基线，不提醒

    # ① 执行口径持仓变化（最高优先级）
    if old.get("hold_default") != new.get("hold_default"):
        ev.append(("持仓变更",
                   [f"{_fmt_hold(old.get('hold_default'), old.get('hold_default_name'))}"
                    f" → {_fmt_hold(new.get('hold_default'), new.get('hold_default_name'))}"]))

    # ② 过热熔断状态翻转
    if bool(old.get("overheat")) != bool(new.get("overheat")):
        ev.append(("过热熔断" + ("触发 → 切防守" if new.get("overheat") else "解除 → 切回轮动"), []))

    # ③ P3 追高止盈 新触发
    if new.get("p3_active") and not old.get("p3_active"):
        _r = new.get("p3_remain")
        ev.append(("P3 追高止盈触发 → 空仓",
                   [f"头名 ROC60 进入自身历史 95 分位"
                    + (f"，还剩 {_r} 个交易日" if _r else "")
                    + (f"，约 {new.get('p3_back')} 回场" if new.get("p3_back") else "")]))

    # ④ 512040 价值引擎穿越买线（默认 −3%）
    _o, _n = old.get("def_dev"), new.get("def_dev")
    _bl = new.get("def_buy_pct")
    if _o is not None and _n is not None and _bl is not None:
        if _o >= _bl > _n:
            ev.append((f"{new.get('def_code')} 跌破 MA200×0.97 → 价值引擎买点",
                       [f"偏离 {_o:+.2f}% → {_n:+.2f}%（买线 {_bl:+.1f}%）"]))
        elif _n >= _bl > _o:
            ev.append((f"{new.get('def_code')} 收复 MA200×0.97 → 买点失效",
                       [f"偏离 {_o:+.2f}% → {_n:+.2f}%（买线 {_bl:+.1f}%）"]))

    # ⑤ 512890 策略二区间切换（买区 / 卖区 / 中间区）
    if old.get("ve2_zone") != new.get("ve2_zone") and new.get("ve2_zone") in ("buy", "sell"):
        _z = {"buy": "回踩 MA200 买区", "sell": "冲上 +6% 卖区"}[new["ve2_zone"]]
        ev.append((f"{new.get('ve2_code')} 进入{_z}",
                   [f"偏离 {new.get('ve2_dev'):+.2f}% ｜ {new.get('ve2_action')}"]
                   if new.get("ve2_dev") is not None else []))

    return ev


def build_message(ev, new):
    asof = new.get("asof", "")
    head = f"【ETF轮动·{'＋'.join(t for t, _ in ev)}】{asof}"
    lines = [head]
    for _t, det in ev:
        lines.extend("· " + d for d in det if d)
    nav, cal = new.get("nav_default"), new.get("calmar_default")
    if nav:
        lines.append(f"组合净值 {nav:.4f}" + (f" ｜ Calmar {cal:.2f}" if cal else ""))
    lines.append(f"今日应持：{_fmt_hold(new.get('hold_default'), new.get('hold_default_name'))}")
    lines.append(SITE)
    return "\n".join(lines)


# --------------------------------------------------------------------------- 推送渠道
def _post(url, data, headers=None, timeout=15):
    req = urllib.request.Request(
        url, data=data, headers=headers or {},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")[:300]


def send_pushplus(text, title):
    tok = os.environ.get("PUSHPLUS_TOKEN")
    if not tok:
        return None
    body = json.dumps({"token": tok, "title": title, "content":
                       text.replace("\n", "<br>"), "template": "txt"}).encode()
    return _post("https://www.pushplus.plus/send", body,
                 {"Content-Type": "application/json"})


def send_serverchan(text, title):
    key = os.environ.get("SERVERCHAN_KEY")
    if not key:
        return None
    body = urllib.parse.urlencode({"title": title, "desp": text}).encode()
    return _post(f"https://sctapi.ftqq.com/{key}.send", body)


def send_bark(text, title):
    base = os.environ.get("BARK_URL")
    if not base:
        return None
    url = f"{base.rstrip('/')}/{urllib.parse.quote(title)}/{urllib.parse.quote(text)}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.read().decode("utf-8", "replace")[:300]


def send_webhook(text, title):
    url = os.environ.get("WEBHOOK_URL")
    if not url:
        return None
    return _post(url, json.dumps({"title": title, "text": text},
                                 ensure_ascii=False).encode(),
                 {"Content-Type": "application/json"})


def send_mail(text, title):
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    to = os.environ.get("MAIL_TO")
    if not (host and user and pwd and to):
        return None
    import smtplib
    from email.message import EmailMessage
    from email.header import Header
    port = int(os.environ.get("SMTP_PORT", "465"))
    msg = EmailMessage()
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = user
    msg["To"] = to
    msg.set_content(text)
    tls = os.environ.get("SMTP_TLS", "1") != "0"
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20) as s:
            s.login(user, pwd)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            if tls:
                s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
    return "mail sent"


def dispatch(text, title):
    """按顺序尝试所有已配置的渠道，返回 [(渠道名, 结果)]。"""
    done = []
    for name, fn in (("pushplus", send_pushplus), ("serverchan", send_serverchan),
                     ("bark", send_bark), ("webhook", send_webhook),
                     ("mail", send_mail)):
        try:
            r = fn(text, title)
        except Exception as e:                    # noqa: BLE001
            done.append((name, f"ERROR {type(e).__name__}: {e}"))
            continue
        if r is not None:
            done.append((name, "ok"))
    return done


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="仅在状态切换当天推送提醒")
    ap.add_argument("--state", default="docs/state.json")
    ap.add_argument("--prev", default=None, help="旧快照文件（默认从 git HEAD 取）")
    ap.add_argument("--prev-ref", default="docs/state.json",
                    help="在 git 里的路径（默认 docs/state.json）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    new = load_json(a.state)
    if not new:
        print(f"[notify] 读不到新快照 {a.state}，跳过")
        return 0

    old = load_json(a.prev) if a.prev else load_prev_from_git(a.prev_ref)
    if not old:
        print(f"[notify] 无上一版快照（首次运行）→ 仅建立基线，不提醒。asof={new.get('asof')}")
        return 0

    # 同一天重复生成（比如手动多跑一次）不该重复提醒
    if str(old.get("asof")) == str(new.get("asof")):
        print(f"[notify] 锚定日未变（{new.get('asof')}）→ 无变化，不打扰")
        return 0

    ev = diff_events(old, new)
    if not ev:
        print(f"[notify] {new.get('asof')} 无状态切换 → 安静（持仓 "
              f"{new.get('hold_default')}）")
        return 0

    msg = build_message(ev, new)
    print("[notify] 检测到状态切换：")
    print(msg)

    if a.dry_run:
        print("[notify] --dry-run：不推送")
        return 0

    res = dispatch(msg, f"ETF轮动 {new.get('asof')}")
    if not res:
        print("[notify] ⚠ 未配置任何推送渠道（PUSHPLUS_TOKEN / SERVERCHAN_KEY / "
              "BARK_URL / WEBHOOK_URL / SMTP_*）→ 仅打印")
    for n, r in res:
        print(f"[notify] {n}: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
