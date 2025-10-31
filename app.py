import os
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, session, redirect, url_for, flash
from flask_socketio import SocketIO, emit, disconnect
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
import paramiko
import select
import io

# --- 1. 初始化应用 ---
app = Flask(__name__)

# 为 Flask session 和加密设置一个强密钥。
# 在生产中，这应该从环境变量中读取！
app.config['SECRET_KEY'] = 'a-very-complex-and-long-random-secret-key-for-panel'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///servers.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode='eventlet')

# 为 Flask-Login 设置登录管理器
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # 如果未登录，重定向到 /login
login_manager.login_message = '请先登录以访问此页面。'

# --- 2. 数据库模型 ---

# 面板用户 (登录面板的人)
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# 存储的 VPS/服务器
class Server(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    host = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, default=22)
    username = db.Column(db.String(100), nullable=False)
    
    # 我们将加密存储凭据
    encrypted_password = db.Column(db.String(512)) # 存储密码
    encrypted_private_key = db.Column(db.Text)    # 存储私钥
    
    # 关联到创建它的用户
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('servers', lazy=True))

# --- 3. 加密工具 ---
# (这是一个简化的实现。理想情况下，密钥管理应该更复杂)
# 使用 SECRET_KEY 的一部分作为加密密钥 (确保它足够长)
# **警告**: 如果 SECRET_KEY 改变, 所有加密数据将无法解密！
# 我们需要一个 32 字节的 key
key = app.config['SECRET_KEY'][:32].encode('utf-8').ljust(32, b'\0')
cipher_suite = Fernet(key)

def encrypt_data(data):
    if not data:
        return None
    return cipher_suite.encrypt(data.encode('utf-8')).decode('utf-8')

def decrypt_data(encrypted_data):
    if not encrypted_data:
        return None
    try:
        return cipher_suite.decrypt(encrypted_data.encode('utf-8')).decode('utf-8')
    except Exception:
        return None # 如果解密失败

# --- 4. 面板认证路由 (登录/注册/注销) ---

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('无效的用户名或密码。', 'danger')
            
    return render_template('panel_login.html') # 注意：这是新的登录页

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    # 在生产中，您可能希望禁用此路由，或只允许第一个用户注册
    if User.query.count() > 0:
        flash('注册已禁用。', 'info')
        return redirect(url_for('login'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if User.query.filter_by(username=username).first():
            flash('用户名已存在。', 'danger')
            return redirect(url_for('register'))
            
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        
        flash('注册成功！请登录。', 'success')
        return redirect(url_for('login'))
        
    return render_template('panel_register.html') # 注意：这是新的注册页

# --- 5. 服务器管理 (CRUD) 路由 ---

@app.route('/')
@login_required
def dashboard():
    """
    主仪表盘，显示当前用户的所有服务器。
    """
    servers = Server.query.filter_by(user_id=current_user.id).all()
    return render_template('dashboard.html', servers=servers)

@app.route('/add_server', methods=['GET', 'POST'])
@login_required
def add_server():
    if request.method == 'POST':
        name = request.form['name']
        host = request.form['host']
        port = request.form.get('port', 22, type=int)
        username = request.form['username']
        auth_method = request.form['auth_method']
        
        encrypted_pass = None
        encrypted_key = None
        
        if auth_method == 'password':
            encrypted_pass = encrypt_data(request.form['password'])
        elif auth_method == 'key':
            encrypted_key = encrypt_data(request.form['key_data'])
        
        new_server = Server(
            name=name,
            host=host,
            port=port,
            username=username,
            encrypted_password=encrypted_pass,
            encrypted_private_key=encrypted_key,
            user_id=current_user.id
        )
        db.session.add(new_server)
        db.session.commit()
        
        flash(f'服务器 "{name}" 添加成功！', 'success')
        return redirect(url_for('dashboard'))
        
    return render_template('server_form.html', title='添加新服务器')

@app.route('/edit_server/<int:server_id>', methods=['GET', 'POST'])
@login_required
def edit_server(server_id):
    server = Server.query.get_or_404(server_id)
    if server.user_id != current_user.id:
        return "Unauthorized", 403 # 确保用户只能编辑自己的服务器

    if request.method == 'POST':
        server.name = request.form['name']
        server.host = request.form['host']
        server.port = request.form.get('port', 22, type=int)
        server.username = request.form['username']
        auth_method = request.form['auth_method']
        
        if auth_method == 'password':
            password = request.form['password']
            # 只有在提供了新密码时才更新
            if password:
                server.encrypted_password = encrypt_data(password)
            server.encrypted_private_key = None # 清除密钥
        elif auth_method == 'key':
            key_data = request.form['key_data']
            # 只有在提供了新密钥时才更新
            if key_data:
                server.encrypted_private_key = encrypt_data(key_data)
            server.encrypted_password = None # 清除密码
        
        db.session.commit()
        flash(f'服务器 "{server.name}" 更新成功！', 'success')
        return redirect(url_for('dashboard'))
        
    # GET 请求，解密数据以便在表单中显示（用于提示，非明文）
    password_exists = bool(decrypt_data(server.encrypted_password))
    key_exists = bool(decrypt_data(server.encrypted_private_key))
        
    return render_template('server_form.html', title='编辑服务器', server=server, password_exists=password_exists, key_exists=key_exists)

@app.route('/delete_server/<int:server_id>', methods=['POST'])
@login_required
def delete_server(server_id):
    server = Server.query.get_or_404(server_id)
    if server.user_id != current_user.id:
        return "Unauthorized", 403
    
    name = server.name
    db.session.delete(server)
    db.session.commit()
    flash(f'服务器 "{name}" 已删除。', 'success')
    return redirect(url_for('dashboard'))

# --- 6. 终端页面路由 ---

@app.route('/terminal/<int:server_id>')
@login_required
def terminal(server_id):
    """
    提供终端页面。
    我们只传递 server_id, JS 将通过 WebSocket 发送它。
    """
    server = Server.query.get_or_404(server_id)
    if server.user_id != current_user.id:
        return "Unauthorized", 403
        
    # 我们不再使用 Flask session 传递凭据
    # 我们只渲染页面
    return render_template('terminal.html', server_name=server.name)

# --- 7. 全新的 Socket.IO 逻辑 ---

# 用来存储每个用户 (sid) 的 SSH client 和 channel
clients = {}

def read_from_shell(sid):
    """后台读取任务 (与之前相同)"""
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
    """清理并关闭连接 (与之前相同)"""
    if sid in clients:
        try:
            clients[sid]['channel'].close()
            clients[sid]['client'].close()
        except Exception:
            pass
        finally:
            del clients[sid]
            print(f"Client {sid} disconnected and cleaned up.")

@socketio.on('connect')
def connect():
    """
    当一个新的 WebSocket 客户端连接时触发。
    我们现在什么都不做，等待客户端发送 'init' 事件。
    """
    print(f"Client {request.sid} connected, awaiting init...")
    emit('ssh_output', {'data': '--- WebSocket Connected ---\r\n--- Please initialize terminal ---\r\n'})

@socketio.on('init_terminal')
def init_terminal(data):
    """
    【全新】客户端发送它想要连接的 server_id
    """
    sid = request.sid
    server_id = data.get('server_id')
    
    if not server_id:
        emit('ssh_output', {'data': 'Error: Missing server_id.\r\n'})
        return

    # 检查用户是否已登录 (通过 Flask-Login 的 current_user)
    if not current_user.is_authenticated:
        emit('ssh_output', {'data': 'Error: Authentication required.\r\n'})
        disconnect(sid)
        return

    # 从数据库中查找服务器
    server = Server.query.get(server_id)
    
    # 检查服务器是否存在以及是否属于当前用户
    if not server or server.user_id != current_user.id:
        emit('ssh_output', {'data': 'Error: Server not found or unauthorized.\r\n'})
        disconnect(sid)
        return

    print(f"Client {sid} initializing for server {server.name} ({server.host})")
    
    # --- 建立 Paramiko 连接 ---
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        host = server.host
        port = server.port
        user = server.username
        
        # 解密凭据
        password = decrypt_data(server.encrypted_password)
        key_string = decrypt_data(server.encrypted_private_key)
        
        pkey = None
        if key_string:
            # (注意: 我们简化了, 假设私钥没有密码)
            key_file_obj = io.StringIO(key_string)
            pkey = paramiko.PKey.from_private_key(key_file_obj)
        
        if pkey:
            client.connect(host, port=port, username=user, pkey=pkey, timeout=10)
        elif password:
            client.connect(host, port=port, username=user, password=password, timeout=10)
        else:
            emit('ssh_output', {'data': f'Error: No valid credentials found for server {server.name}.\r\n'})
            disconnect(sid)
            return

        channel = client.invoke_shell(term='xterm')
        clients[sid] = {'client': client, 'channel': channel}
        
        socketio.start_background_task(target=read_from_shell, sid=sid)
        emit('ssh_output', {'data': f'--- SSH Connection to {user}@{host} established ---\r\n'})

    except paramiko.AuthenticationException:
        print(f"Authentication failed for {sid}")
        emit('ssh_output', {'data': '\r\n--- SSH AUTHENTICATION FAILED ---\r\nCheck your saved credentials.\r\n'})
        disconnect(sid)
    except Exception as e:
        print(f"Failed to connect {sid}: {e}")
        emit('ssh_output', {'data': f'\r\n--- SSH Connection Failed: {e} ---\r\n'})
        disconnect(sid)


@socketio.on('ssh_input')
def ssh_input(data):
    """ (与之前相同) """
    sid = request.sid
    if sid in clients:
        try:
            clients[sid]['channel'].send(data['data'])
        except Exception as e:
            print(f"Error sending data for {sid}: {e}")
            cleanup_connection(sid)

@socketio.on('disconnect')
def on_disconnect():
    """ (与之前相同) """
    cleanup_connection(request.sid)

# --- 8. 初始化数据库 ---
def init_db():
    """
    一个辅助函数，在第一次运行时创建数据库和表。
    """
    print("Initializing database...")
    db.create_all()
    
    # (可选) 检查是否没有用户，并提示创建第一个用户
    if User.query.count() == 0:
        print("-----------------------------------------")
        print("No users found.")
        print("Please register the first admin user via")
        print("the /register page in your browser.")
        print("-----------------------------------------")

if __name__ == '__main__':
    # 我们需要 app context 来创建表
    with app.app_context():
        init_db()
        
    print("Starting Flask-SocketIO server on http://127.0.0.1:5000")
    socketio.run(app, host='127.0.0.1', port=5000, debug=True, allow_unsafe_werkzeug=True)
