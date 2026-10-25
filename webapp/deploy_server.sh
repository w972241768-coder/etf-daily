#!/usr/bin/env bash
# ============================================================================
# ETF 轮动日报 · 云服务器一键部署（Ubuntu / Debian）
#
# 用法（在服务器上，把整个 webapp 目录上传后）：
#   cd webapp
#   sudo bash deploy_server.sh
#
# 做完后手机浏览器直接开：  http://<你的服务器公网IP>/
# 常用运维：
#   systemctl status etf-daily     # 看状态
#   journalctl -u etf-daily -f     # 看日志
#   systemctl restart etf-daily    # 重启
#
# 说明：服务自己每 5 分钟重算一次日报，手机打开即最新，不需要 cron。
# ============================================================================
set -euo pipefail

APP_DIR="/opt/etf-daily"
SVC="etf-daily"
PORT="${PORT:-80}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> 1/6 安装 Python 与依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv tzdata curl

echo "==> 2/6 建虚拟环境并装 pandas/numpy（隔离系统 Python，避免 externally-managed 报错）"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$HERE"/. "$APP_DIR"/
rm -rf "$APP_DIR/out" "$APP_DIR/__pycache__"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q pandas numpy

echo "==> 3/6 生成 systemd 服务"
cat > "/etc/systemd/system/${SVC}.service" <<UNIT
[Unit]
Description=ETF 动横阶梯轮动日报（移动端网页）
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
Environment=PORT=${PORT}
Environment=OPENBLAS_NUM_THREADS=1
Environment=OMP_NUM_THREADS=1
Environment=MPLBACKEND=Agg
Environment=TZ=Asia/Shanghai
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/app.py
Restart=always
RestartSec=5
# 允许非 root 用户绑定 80 端口
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
UNIT

echo "==> 4/6 启动服务"
systemctl daemon-reload
systemctl enable "$SVC" >/dev/null 2>&1
systemctl restart "$SVC"

echo "==> 5/6 等待首次生成（约 40~90 秒）"
for i in $(seq 1 30); do
    sleep 3
    if curl -fsS -m 5 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
        echo "    端口已就绪（${i}0 秒内）"
        break
    fi
done

echo "==> 6/6 状态"
sleep 1
curl -s -m 20 "http://127.0.0.1:${PORT}/status" || true
echo ""
IP="$(curl -s -m 8 https://api.ipify.org || echo '<服务器公网IP>')"
echo "============================================================"
echo " 手机浏览器打开：  http://${IP}/"
if [ "$PORT" != "80" ]; then
    echo " （当前端口 ${PORT}，请用 http://${IP}:${PORT}/，并放行安全组）"
fi
echo ""
echo " 首次若显示「正在准备运行环境」，等 60~90 秒再刷新即可。"
echo " 建议：手机浏览器菜单 → 添加到主屏幕，图标点开就是全屏日报。"
echo "============================================================"
