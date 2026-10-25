# ETF 轮动日报 · 移动端网页版

手机打开链接就能看最新日报，**不需要电脑开机、不需要定时任务**。

## 原理

`app.py` 是一个零依赖（除 pandas/numpy）的 HTTP 服务：

- 启动后立刻绑定端口（毫秒级），然后**后台线程**开始生成日报
- 之后每 **5 分钟**（`SOFT_TTL`）滚动重算一次；新浪日线收盘后 15:00~16:00 才稳定，盘中也会更新
- 手机请求**只读内存缓存**，响应 ~5ms，不会被反向代理超时掐断
- 缓存过期时先返回旧页、后台顺手重算（stale-while-revalidate）
- 生成失败**不毁缓存**：保留上次成功结果，页面显示错误并 5 分钟后自动重试

## 路由

| 路径 | 作用 |
|---|---|
| `/` | 最新日报（HTML） |
| `/status` | 锚定日、缓存年龄、生成耗时、最近日志 |
| `/refresh` | 强制立即重算 |
| `/healthz` | 存活探针，返回 `ok` |

## 两种跑法

### A. 云服务器（推荐，不依赖你的电脑）

```bash
# 把整个 webapp 目录上传到服务器后
cd webapp
sudo bash deploy_server.sh
```

脚本会：装 python3 → 建 venv 装 pandas/numpy → 写 systemd 服务（开机自启、异常自动重启）
→ 起服务并监听 80 端口 → 打印 `http://服务器IP/`。

运维：`systemctl status etf-daily` / `journalctl -u etf-daily -f`

### B. 本机临时跑（同一 WiFi 下手机可访问）

```bash
cd webapp
PORT=8080 python app.py
# 手机浏览器打开 http://<本机局域网IP>:8080/
```

## 文件说明

| 文件 | 说明 |
|---|---|
| `app.py` | HTTP 服务 + 后台刷新调度 |
| `etf_rotation.py` 等 4 个 py | 日报生成逻辑（与本地一键报告同一套） |
| `tdx_adj_long_2019_2026.csv.gz` | 前复权长历史，压缩随包（启动时解压，1736KB→413KB） |
| `deploy_server.sh` | Ubuntu/Debian 一键部署 |

## 两个已踩过的坑（改代码前务必看）

1. **不要把依赖写进 `requirements.txt`**：发布平台是「先装依赖 → 再启动服务」，
   pandas+numpy 装完要 50~70 秒，而就绪探针只给 60 秒 ⇒ 服务还没起就被判不可达。
   现在改成进程秒起、`_ensure_deps()` 在后台线程里懒装。
2. **不要用标准库默认的 `server_bind`**：`HTTPServer.server_bind()` 会调
   `socket.getfqdn()` 做反向 DNS，容器里没有 PTR 记录时会阻塞 9 秒左右。
   已重写为只 bind + 记端口，实测 1ms。
