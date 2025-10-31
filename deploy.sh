#!/bin/bash

# --- 脚本配置 ---
# 您的 GitHub 仓库 URL
REPO_URL="https://github.com/SIJULY/web-ssh.git"
# 您希望将应用安装在哪个目录
APP_DIR="/opt/web-ssh"
# 内部 Gunicorn 运行的端口
APP_PORT="5000"

# --- 确保脚本以 root 权限运行 ---
if [ "$(id -u)" -ne 0 ]; then
  echo "错误：此脚本必须以 root 权限运行。"
  echo "请尝试使用: sudo ./deploy.sh"
  exit 1
fi

# --- 0. 询问域名 ---
read -p "请输入您的域名 (例如 ssh.your-domain.com): " DOMAIN_NAME
if [ -z "$DOMAIN_NAME" ]; then
    echo "错误：未提供域名。已退出。"
    exit 1
fi

echo "--- 准备在 https://$DOMAIN_NAME 上部署 Web-SSH ---"
set -e # 如果任何命令失败，立即退出

# --- 1. 安装系统依赖 (Debian/Ubuntu) ---
echo "--- 正在更新系统并安装依赖 (python, git, curl)... ---"
apt update
apt install -y python3-venv python3-pip git curl

# --- 2. 安装 Caddy (使用官方脚本) ---
echo "--- 正在安装 Caddy... ---"
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update
apt install caddy

# --- 3. 克隆或更新您的应用代码 ---
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
echo "--- 正在创建 Python 虚拟环境 (venv)... ---"
python3 -m venv venv
echo "--- 正在激活 venv 并安装 requirements.txt... ---"
# 我们不使用 'source'，而是直接调用 venv 内的 pip
./venv/bin/pip install -r requirements.txt

# --- 5. 创建 Systemd 服务 ---
echo "--- 正在创建 systemd 服务 (web-ssh.service)... ---"
# 使用 Gunicorn + eventlet 来运行
# 注意 ExecStart 指向 venv 内的 gunicorn
cat > /etc/systemd/system/web-ssh.service << EOF
[Unit]
Description=Gunicorn instance for Web-SSH
After=network.target

[Service]
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/gunicorn -w 1 -k eventlet -b 127.0.0.1:$APP_PORT app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# --- 6. 配置 Caddy ---
echo "--- 正在配置 Caddyfile... ---"
# Caddy 会自动处理 HTTPS
cat > /etc/caddy/Caddyfile << EOF
$DOMAIN_NAME {
    # 将所有流量反向代理到本地 Gunicorn 服务
    reverse_proxy 127.0.0.1:$APP_PORT
}
EOF

# --- 7. 启动服务 ---
echo "--- 正在重载 daemons 并启动服务... ---"
systemctl daemon-reload
systemctl enable --now web-ssh
systemctl restart caddy

echo "----------------------------------------------------"
echo "✅ 部署完成！"
echo ""
echo "您的 Web-SSH 应用正在 https://$DOMAIN_NAME 上运行。"
echo "请确保您的防火墙已打开 TCP 端口 80 和 443。"
echo "----------------------------------------------------"
