#!/bin/bash

# --- 脚本配置 ---
REPO_URL="https://github.com/SIJULY/web-ssh.git"
APP_DIR="/opt/web-ssh"
APP_PORT="5000"

# --- 确保脚本以 root 权限运行 ---
if [ "$(id -u)" -ne 0 ]; then
  echo "错误：此脚本必须以 root 权限运行。"
  echo "请尝试使用: sudo ./deploy.sh"
  exit 1
fi

# --- 0. 询问域名 ---
read -p "请输入您的域名 (例如 ssh.sijuly.nyc.mn): " DOMAIN_NAME
if [ -z "$DOMAIN_NAME" ]; then
    echo "错误：未提供域名。已退出。"
    exit 1
fi

echo "--- 准备在 https://$DOMAIN_NAME 上部署 Web-SSH (集成到现有 Caddy) ---"
set -e

# --- 1. 安装系统依赖 (Debian/Ubuntu) ---
echo "--- 正在更新系统并安装依赖 (python, git)... ---"
apt update
apt install -y python3-venv python3-pip git docker.io

# --- 2. 动态查找现有的 Docker Caddy ---
echo "--- 正在动态查找正在运行的 Caddy 容器... ---"

# (动态步骤 A: 查找第一个基于 caddy 镜像的容器 ID)
CADDY_CONTAINER_ID=$(docker ps -q --filter "ancestor=caddy" | head -n 1)
if [ -z "$CADDY_CONTAINER_ID" ]; then
    echo "错误：找不到任何正在运行的 'caddy' 容器。"
    echo "此脚本需要一个已在 Docker 中运行的 Caddy 实例。"
    exit 1
fi
echo "--- 找到 Caddy 容器: $CADDY_CONTAINER_ID ---"

# (动态步骤 B: 查找 Caddyfile 在宿主机上的路径)
# 我们假设 Caddyfile 在容器内被挂载为 /etc/caddy/Caddyfile
HOST_CADDYFILE_PATH=$(docker inspect -f '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{end}}' "$CADDY_CONTAINER_ID" | grep -oE "[^ ]+ -> /etc/caddy/Caddyfile" | awk '{print $1}')
if [ -z "$HOST_CADDYFILE_PATH" ]; then
    echo "错误：找到了 Caddy 容器 ($CADDY_CONTAINER_ID)，但找不到挂载到 /etc/caddy/Caddyfile 的 Caddyfile。"
    exit 1
fi
echo "--- 找到 Caddyfile 路径: $HOST_CADDYFILE_PATH ---"

# (动态步骤 C: 查找 Caddy 容器的网关 IP)
NETWORK_NAME=$(docker inspect -f '{{json .NetworkSettings.Networks}}' "$CADDY_CONTAINER_ID" | grep -oE '"[^"]+"' | head -n 1 | tr -d '"')
GATEWAY_IP=$(docker inspect -f "{{.NetworkSettings.Networks.$NETWORK_NAME.Gateway}}" "$CADDY_CONTAINER_ID")
if [ -z "$GATEWAY_IP" ]; then
    echo "错误：找不到 Caddy 容器 ($CADDY_CONTAINER_ID) 的网络网关 IP。"
    exit 1
fi
echo "--- 找到网关 IP: $GATEWAY_IP ---"


# --- 3. 克隆或更新您的应用代码 ---
# (与之前相同)
echo "--- 正在从 GitHub 克隆项目到 $APP_DIR... ---"
if [ -d "$APP_DIR" ]; then
    echo "目录已存在，正在拉取最新代码..."
    cd "$APP_DIR"
    git pull origin main
else
    echo "正在克隆新仓库..."
    git clone "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

# --- 4. 设置 Python 虚拟环境并安装依赖 ---
# (与之前相同)
echo "--- 正在创建 Python 虚拟环境 (venv)... ---"
python3 -m venv venv
echo "--- 正在激活 venv 并安装 requirements.txt... ---"
./venv/bin/pip install -r requirements.txt

# --- 5. 创建 Systemd 服务 (使用 0.0.0.0) ---
# (与之前相同)
echo "--- 正在创建 systemd 服务 (web-ssh.service)... ---"
cat > /etc/systemd/system/web-ssh.service << EOF
[Unit]
Description=Gunicorn instance for Web-SSH
After=network.target

[Service]
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/gunicorn -w 1 -k eventlet -b 0.0.0.0:$APP_PORT app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# --- 6. 动态配置 Caddyfile ---
echo "--- 正在配置现有的 Docker Caddyfile ($HOST_CADDYFILE_PATH)... ---"

# (使用动态找到的路径)
if grep -q "$DOMAIN_NAME" "$HOST_CADDYFILE_PATH"; then
    echo "--- Caddy 配置已存在，跳过添加。 ---"
else
    echo "--- 正在追加 (Append) 新的配置到 Caddyfile... ---"
    # (使用动态找到的 IP 和 WebSocket 配置)
    cat >> "$HOST_CADDYFILE_PATH" << EOF

# --- Web-SSH (由此脚本自动添加) ---
$DOMAIN_NAME {
    reverse_proxy $GATEWAY_IP:$APP_PORT {
        header_up Connection {>Connection}
        header_up Upgrade {>Upgrade}
    }
}
EOF
fi

# --- 7. 启动服务 (使用动态找到的容器 ID) ---
echo "--- 正在启动 web-ssh (systemd) 服务... ---"
systemctl daemon-reload
systemctl enable --now web-ssh

# (在 Oracle Cloud 上，我们必须为 5000 端口打开防火墙)
echo "--- 正在为 5000 端口配置 iptables 防火墙... ---"
iptables -I DOCKER-USER -d "$GATEWAY_IP" -p tcp --dport "$APP_PORT" -j ACCEPT || echo "iptables 规则添加失败，可能已存在。"

echo "--- 正在重载 Docker Caddy ($CADDY_CONTAINER_ID) 配置... ---"
# (使用动态找到的 ID)
docker exec "$CADDY_CONTAINER_ID" caddy reload --config /etc/caddy/Caddyfile

echo "----------------------------------------------------"
echo "✅ 部署完成！"
echo ""
echo "您的 Web-SSH 应用正在 https://$DOMAIN_NAME 上运行。"
echo "----------------------------------------------------"
