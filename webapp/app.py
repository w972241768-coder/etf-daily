#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 动横阶梯轮动 · 网页版日报服务（手机直接看）

设计要点
--------
1. **不依赖本地电脑**：整包（5 个 py + 3 个 CSV）随代码一起部署，纯在线取数
   （`etf_rotation.py` 默认在线模式，不读本地 TDX）。
2. **请求只返回缓存**：真正生成要 20~40 秒（25 只标的在线取数 + 长历史回测），
   直接放在 HTTP 请求里会被反向代理超时掐断。所以改成后台线程生成，HTTP 秒回。
3. **stale-while-revalidate**：打开页面时若缓存超过 SOFT_TTL 分钟，先返回旧页、
   同时后台重算；下次刷新就是最新。生成期间不并发（_lock + running 标记）。
4. **失败不毁缓存**：新报告体积异常（< 100KB）或无产出时，保留上一次成功结果，
   并把错误写进日志，页面顶部仍显示旧数据 + 数据截至日。

环境变量
--------
PORT         监听端口（默认 3000，部署平台会注入）
SOFT_TTL     缓存软化期（分钟，默认 5）——超过就在后台重算
HARD_TTL     缓存硬过期（分钟，默认 180）——超过且重算失败时提示"数据可能过期"
"""
from __future__ import annotations

import glob
import html
import os
import socketserver
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")
SCRIPT = os.path.join(HERE, "etf_rotation.py")
PORT = int(os.environ.get("PORT", "3000"))
SOFT_TTL = float(os.environ.get("SOFT_TTL", "5")) * 60.0
HARD_TTL = float(os.environ.get("HARD_TTL", "180")) * 60.0
LOG_MAX = 40

_state = {
    "html": None,          # 最近一次成功的报告
    "ts": 0.0,             # 报告生成完成的本地时间戳
    "pivot": None,         # 报告锚定日（YYYY-MM-DD）
    "err": None,           # 最近一次失败原因
    "running": False,      # 是否正在生成
    "gen_start": 0.0,
    "n_ok": 0,
    "deps_ok": False,      # pandas/numpy 是否已就绪
    "log": [],             # 最近几次生成的结果摘要
}
_lock = threading.Lock()


def _log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')} {msg}"
    print(line, flush=True)
    with _lock:
        _state["log"].append(line)
        del _state["log"][:-LOG_MAX]


def _latest_html() -> tuple[str | None, str | None]:
    """取 out/ 下最新的报告 + 其锚定日。"""
    files = glob.glob(os.path.join(OUT_DIR, "etf_rotation_*.html"))
    if not files:
        return None, None
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    path = files[0]
    with open(path, encoding="utf-8") as f:
        content = f.read()
    stem = os.path.basename(path)[len("etf_rotation_"):-len(".html")]
    return content, stem


def generate() -> bool:
    """跑一次回测并刷新缓存。返回是否成功。"""
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
        _state["gen_start"] = time.time()
    ok = False
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        env = dict(os.environ)
        # 单线程 BLAS：容器里多线程常因内存/核数不匹配直接 abort
        env["OPENBLAS_NUM_THREADS"] = "1"
        env["OMP_NUM_THREADS"] = "1"
        env["MPLBACKEND"] = "Agg"
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--no-open"],
            cwd=HERE, env=env, capture_output=True, text=True, timeout=900,
        )
        dt = time.time() - t0
        content, pivot = _latest_html()
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
            raise RuntimeError(f"退出码 {proc.returncode}: " + " / ".join(tail))
        if not content or len(content) < 100_000:
            raise RuntimeError(f"产出异常（{len(content or '')} 字符）")
        with _lock:
            _state.update(html=content, ts=time.time(), pivot=pivot, err=None,
                          n_ok=_state["n_ok"] + 1)
        _log(f"OK 锚定日 {pivot} 用时 {dt:.1f}s 体积 {len(content)/1024:.0f}KB")
        ok = True
    except Exception as exc:                     # noqa: BLE001
        with _lock:
            _state["err"] = f"{type(exc).__name__}: {exc}"
        _log("FAIL " + _state["err"])
        print(traceback.format_exc(), flush=True)
    finally:
        with _lock:
            _state["running"] = False
    return ok


def _ensure_deps() -> None:
    """确保 pandas/numpy 可用。

    ⚠️ 不把依赖写进 requirements.txt：平台会「先装依赖→再启动服务」，
    实测 pandas+numpy 装完要 50~70 秒，而就绪探针只给 60 秒
    ⇒ 服务还没起来就被判「不可达」，发布失败（日志里 LISTEN 0.0.0.0:3000
    已出现但探针已超时，就是这个原因）。
    改成：进程秒起绑端口 → 后台线程先把依赖装好 → 再生成报告。
    页面在依赖就绪前显示「正在准备运行环境」，不会 502。
    """
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
        return
    except ImportError:
        pass
    _log("依赖缺失，开始运行时安装 pandas/numpy（约 40~90s）")
    cmds = (
        [sys.executable, "-m", "pip", "install", "--no-input", "--user", "pandas", "numpy"],
        [sys.executable, "-m", "pip", "install", "--no-input", "pandas", "numpy"],
        [sys.executable, "-m", "pip", "install", "--no-input",
         "--break-system-packages", "pandas", "numpy"],
    )
    for args in cmds:
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=900)
            if proc.returncode == 0:
                import importlib
                importlib.invalidate_caches()
                _log("依赖安装完成: " + " ".join(args[-3:]))
                return
            _log("安装失败(rc=%d): %s" % (proc.returncode,
                                          (proc.stderr or "").strip().splitlines()[-1:]))
        except Exception as exc:                 # noqa: BLE001
            _log(f"安装异常: {type(exc).__name__}: {exc}")
    raise RuntimeError("pandas/numpy 安装失败，无法生成报告")


def _loop() -> None:
    """后台：先备依赖 → 生成一次 → 之后按软过期滚动刷新，失败则 5 分钟后重试。"""
    while True:
        try:
            if not _state["n_ok"] and not _state["deps_ok"]:
                _ensure_deps()
                _state["deps_ok"] = True
            age = time.time() - _state["ts"] if _state["ts"] else 1e9
            ttl = SOFT_TTL if _state["n_ok"] else 300.0     # 首次/失败后 5 分钟重试
            if _state["html"] is None or age > ttl:
                if not _state["running"]:
                    generate()
        except Exception as exc:                 # noqa: BLE001
            with _lock:
                _state["err"] = f"{type(exc).__name__}: {exc}"
            _log("LOOP FAIL " + _state["err"])
            print(traceback.format_exc(), flush=True)
        time.sleep(20)


_PLACEHOLDER = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF 轮动日报 · 正在生成</title>
<style>
:root{color-scheme:light}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#eef0f3;color:#202020;
font:15px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.card{background:#fff;border:1px solid #e2e5ea;border-radius:14px;padding:26px 24px;
max-width:520px;margin:20px;box-shadow:0 4px 18px rgba(16,24,40,.07)}
h1{margin:0 0 10px;font-size:18px}
.k{color:#3d57e5;font-weight:700}
.mut{color:#6c717f;font-size:13px}
.err{background:#fff4ec;border-left:3px solid #d4380d;color:#8a2c08;
padding:9px 12px;border-radius:6px;font-size:13px;margin-top:12px;word-break:break-all}
pre{font-size:11.5px;color:#6c717f;white-space:pre-wrap;margin:10px 0 0}
</style>@@REFRESH@@</head><body><div class="card">
<h1>@@TITLE@@</h1>
<div class="mut">@@BODY@@</div>@@ERRBOX@@@@LOGBOX@@</div></body></html>"""


def _placeholder() -> str:
    running = _state["running"]
    err = _state["err"]
    n_ok = _state["n_ok"]
    if not _state["deps_ok"] and not n_ok:
        title = "正在准备运行环境…"
        body = ("首次部署需要装好 pandas/numpy（约 40~90 秒），装完立刻生成第一份日报。"
                "本页每 6 秒自动刷新，不用关掉。")
        refresh = '<meta http-equiv="refresh" content="6">'
    elif running:
        el = time.time() - _state["gen_start"] if _state["gen_start"] else 0
        title = "正在生成今日日报…"
        body = (f"首次生成需要 <span class='k'>25~45 秒</span>（在线取 25 只标的 + 长历史回测），"
                f"本页每 6 秒自动刷新。已等待 {el:.0f} 秒。")
        refresh = '<meta http-equiv="refresh" content="6">'
    elif err:
        title = "本次生成失败"
        body = ("服务会自动重试（每 5 分钟一次）。若持续失败，多半是行情接口临时不可用，"
                "稍后再打开即可。")
        refresh = '<meta http-equiv="refresh" content="30">'
    else:
        title = "正在启动…"
        body = "服务刚启动，马上开始生成第一份日报。"
        refresh = '<meta http-equiv="refresh" content="5">'
    errbox = f'<div class="err">{html.escape(err)}</div>' if err else ""
    logbox = ""
    if _state["log"]:
        logbox = "<pre>" + html.escape("\n".join(_state["log"][-5:])) + "</pre>"
    out = _PLACEHOLDER
    for k, v in (("@@REFRESH@@", refresh), ("@@TITLE@@", title), ("@@BODY@@", body),
                 ("@@ERRBOX@@", errbox), ("@@LOGBOX@@", logbox)):
        out = out.replace(k, v)
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "ETFDaily/1.0"

    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8") -> None:
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:                    # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/healthz":
            self._send(200, "ok", "text/plain; charset=utf-8")
            return
        if path == "/status":
            age = int(time.time() - _state["ts"]) if _state["ts"] else None
            self._send(200, (
                f"pivot={_state['pivot']} age_sec={age} running={_state['running']} "
                f"n_ok={_state['n_ok']} err={_state['err']}\n"
                + "\n".join(_state["log"][-10:])
            ), "text/plain; charset=utf-8")
            return
        if path == "/refresh":
            if not _state["running"]:
                threading.Thread(target=generate, daemon=True).start()
            self._send(200, _placeholder())
            return
        # 默认：返回缓存
        content = _state["html"]
        if not content:
            self._send(503 if _state["err"] else 200, _placeholder())
            return
        age = time.time() - _state["ts"]
        if age > SOFT_TTL and not _state["running"]:
            # stale-while-revalidate：先把旧页给出去，后台顺手重算
            threading.Thread(target=generate, daemon=True).start()
        self._send(200, content)

    def do_HEAD(self) -> None:                   # noqa: N802
        """健康检查常用 HEAD，只回头不回体。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:      # 静音访问日志
        return


class _Server(ThreadingHTTPServer):
    """跳过 server_bind 里的 socket.getfqdn()。

    标准库 HTTPServer.server_bind() 会调 socket.getfqdn() 做反向 DNS，
    在容器里没有 PTR 记录时会阻塞 5~10 秒 —— 就绪探针等不到就会被判「不可达」。
    这里只做 bind + 记录端口，绑定在毫秒级完成。
    """
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def _ensure_csv() -> None:
    """长历史 CSV 以 .csv.gz 随包发布（1736KB → 413KB）。

    发布通道对 ~1.7MB 单文件的传输不稳定（多次 `upload socket closed`），
    压缩后上传，启动时解压回原名给 `find_adj_csv()` 用。解压 1 秒内完成。
    """
    import gzip
    import shutil
    name = "tdx_adj_long_2019_2026.csv"
    raw = os.path.join(HERE, name)
    gz = raw + ".gz"
    if os.path.exists(raw) or not os.path.exists(gz):
        return
    try:
        with gzip.open(gz, "rb") as fi, open(raw, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        _log(f"解压 {name} → {os.path.getsize(raw) / 1024:.0f}KB")
    except Exception as exc:                     # noqa: BLE001
        _log(f"解压 {name} 失败: {type(exc).__name__}: {exc}")


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    _ensure_csv()
    # 先把端口绑好（毫秒级）→ 就绪探针马上通过 → 再开始耗时的依赖安装与生成
    srv = _Server(("0.0.0.0", PORT), Handler)
    _log(f"listen 0.0.0.0:{PORT} (SOFT_TTL={SOFT_TTL/60:.0f}min HARD_TTL={HARD_TTL/60:.0f}min)")
    threading.Thread(target=_loop, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
