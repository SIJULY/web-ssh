import os
import eventlet

eventlet.monkey_patch()

from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import SocketIO, emit, disconnect
import paramiko
import select
import io  # 用于将字符串转换为文件对象

app = Flask(__name__)
# 必须设置一个 SECRET_KEY 才能使用 Flask session
# 在生产中请使用一个长而随机的字符串
app.config['SECRET_KEY'] = os.urandom(24)
socketio = SocketIO(app, async_mode='eventlet')

# 不再有全局凭据！

# clients 字典保持不变，用于存储活动连接
clients = {}


# --- 1. 登录和会话管理 ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    """处理用户登录"""
    if request.method == 'POST':
        # 在新登录时清空旧会h会话
        session.clear()

        # 从表单中获取数据
        session['ssh_host'] = request.form['host']
        session['ssh_user'] = request.form['user']
        session['ssh_port'] = int(request.form.get('port', 22))  # 获取端口，默认为 22
        session['auth_method'] = request.form['auth_method']

        if session['auth_method'] == 'password':
            session['ssh_pass'] = request.form['password']
        elif session['auth_method'] == 'key':
            session['ssh_key'] = request.form['key_data']
            session['ssh_key_pass'] = request.form.get('key_pass', '')  # 密钥的密码（可选）

        # 登录信息存入 session 后，重定向到终端页面
        return redirect(url_for('terminal'))

    # GET 请求，只显示登录页面
    return render_template('login.html')


@app.route('/')
def terminal():
    """
    显示终端页面。
    如果用户未登录 (session 中没有 'ssh_host')，则重定向回登录页。
    """
    if 'ssh_host' not in session:
        return redirect(url_for('login'))

    # 将 index.html 重命名为 terminal.html
    return render_template('terminal.html')


@app.route('/logout')
def logout():
    """清除 session 并重定向回登录页"""
    session.clear()
    return redirect(url_for('login'))


# --- 2. SocketIO 和 SSH 逻辑 (已更新) ---

def read_from_shell(sid):
    """
    后台读取任务 (与之前基本相同)
    """
    channel = clients.get(sid, {}).get('channel')
    if not channel:
        return

    try:
        while True:
            readable, _, _ = select.select([channel], [], [], 0.1)
            if readable:
                if channel.recv_ready():
                    data = channel.recv(4096)
                    if not data:
                        break
                    socketio.emit('ssh_output', {'data': data.decode('utf-8', 'ignore')}, to=sid)

                if channel.recv_stderr_ready():
                    data = channel.recv_stderr(4096)
                    socketio.emit('ssh_output', {'data': data.decode('utf-8', 'ignore')}, to=sid)
            else:
                socketio.sleep(0.01)

    except Exception as e:
        print(f"Error in read_from_shell for {sid}: {e}")
        socketio.emit('ssh_output', {'data': f'\r\n--- Error: {e} ---\r\n'}, to=sid)
    finally:
        cleanup_connection(sid)
        socketio.emit('ssh_output', {'data': '\r\n--- SSH Connection Closed ---\r\n'}, to=sid)


def cleanup_connection(sid):
    """清理并关闭连接"""
    if sid in clients:
        try:
            clients[sid]['channel'].close()
            clients[sid]['client'].close()
        except Exception:
            pass  # 忽略关闭时可能发生的错误
        finally:
            del clients[sid]
            print(f"Client {sid} disconnected and cleaned up.")


@socketio.on('connect')
def connect():
    """
    当一个新的 WebSocket 客户端连接时触发。
    **这是执行身份验证的关键点。**
    """
    sid = request.sid
    print(f"Client {sid} connected.")

    # 1. 检查 Flask session 中是否有登录凭据
    if 'ssh_host' not in session:
        print(f"Connection for {sid} rejected: No session credentials.")
        emit('ssh_output', {'data': 'Authentication failed. No session data.\r\n'})
        socketio.disconnect(sid)  # 主动断开
        return

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        host = session['ssh_host']
        user = session['ssh_user']
        port = session['ssh_port']
        auth_method = session['auth_method']

        print(f"Attempting SSH connection for {sid} to {user}@{host}:{port} using {auth_method}")

        if auth_method == 'password':
            # --- 方案 A: 密码登录 ---
            password = session['ssh_pass']
            client.connect(host, port=port, username=user, password=password, timeout=10)

        elif auth_method == 'key':
            # --- 方案 B: 密钥登录 ---
            key_string = session['ssh_key']
            key_pass = session.get('ssh_key_pass') or None  # 如果密码为空，则为 None

            # Paramiko 需要一个 "文件型" 对象, 我们用 io.StringIO 将字符串转为文件
            key_file_obj = io.StringIO(key_string)

            # 尝试加载所有类型的密钥 (RSA, Ed25519, etc.)
            pkey = paramiko.PKey.from_private_key(key_file_obj, password=key_pass)

            client.connect(host, port=port, username=user, pkey=pkey, timeout=10)

        # 打开一个交互式 shell
        channel = client.invoke_shell(term='xterm')

        # 存储 client 和 channel
        clients[sid] = {'client': client, 'channel': channel}

        # 启动后台任务来读取 SSH 输出
        socketio.start_background_task(target=read_from_shell, sid=sid)
        emit('ssh_output', {'data': f'--- SSH Connection to {user}@{host} established ---\r\n'})

    except paramiko.AuthenticationException:
        print(f"Authentication failed for {sid}")
        emit('ssh_output',
             {'data': '\r\n--- SSH AUTHENTICATION FAILED ---\r\nCheck your username, password, or key.\r\n'})
        socketio.disconnect(sid)
    except Exception as e:
        print(f"Failed to connect {sid}: {e}")
        emit('ssh_output', {'data': f'\r\n--- SSH Connection Failed: {e} ---\r\n'})
        socketio.disconnect(sid)


@socketio.on('ssh_input')
def ssh_input(data):
    """
    当从前端的 xterm.js 收到输入时触发 (不变)。
    """
    sid = request.sid
    if sid in clients:
        try:
            clients[sid]['channel'].send(data['data'])
        except Exception as e:
            print(f"Error sending data for {sid}: {e}")
            cleanup_connection(sid)


@socketio.on('disconnect')
def on_disconnect():
    """当 WebSocket 客户端断开时触发 (不变)"""
    cleanup_connection(request.sid)


if __name__ == '__main__':
    print("Starting Flask-SocketIO server on http://127.0.0.1:5000")
    # socketio.run(app, host='127.0.0.1', port=5000, debug=True)

    #
    # --- 临时的 HTTPS 方案 (用于本地测试) ---
    #
    # 如果您想在本地快速测试 HTTPS (浏览器会警告"不安全")
    # 1. pip install pyopenssl
    # 2. 取消注释下面这行，并注释掉上面那行
    #
    # print("Starting Flask-SocketIO server on https://127.0.0.1:5000")
    # socketio.run(app, host='127.0.0.1', port=5000, debug=True, ssl_context='adhoc')
    #
    # -----------------------------------------------
    #
    # 推荐的生产方案见步骤 5
    socketio.run(app, host='127.0.0.1', port=5000, debug=True)