// 1. 初始化 Socket.IO
// 它会自动连接到提供此页面的服务器
// (注意: 如果使用 Caddy/Nginx，它会正确处理 HTTPS -> HTTP 的转换)
const socket = io();

// 2. 初始化 xterm.js
const term = new Terminal({
    cursorBlink: true,
    fontFamily: 'Courier-new, courier, monospace',
    fontSize: 14,
    theme: {
        background: '#000000',
        foreground: '#00FF00' // 绿色字体
    }
});

// 3. 加载 Fit 插件
const fitAddon = new FitAddon.FitAddon();
term.loadAddon(fitAddon);

// 4. 将 xterm 附加到 HTML 元素
term.open(document.getElementById('terminal'));

// 5. 使终端适应容器大小
fitAddon.fit();

// ------ 核心逻辑：数据双向绑定 ------

// 6. [前端 -> 后端] (不变)
term.onData((data) => {
    socket.emit('ssh_input', { 'data': data });
});

// 7. [后端 -> 前端] (不变)
socket.on('ssh_output', (data) => {
    term.write(data.data);
});

// 8. 处理连接/断开事件
socket.on('connect', () => {
    term.write('--- WebSocket Connected ---\r\n');
    term.write('--- Authenticating SSH via Session... ---\r\n');
});

socket.on('disconnect', (reason) => {
    term.write(`\r\n--- WebSocket Disconnected (Reason: ${reason}) ---\r\n`);

    // *** 新增逻辑 ***
    // 如果是服务器主动断开 (通常意味着认证失败或 session 过期)
    // 2 秒后自动重定向到登录页面
    if (reason === 'io server disconnect') {
        term.write('Authentication failed or session expired. Redirecting to login...\r\n');
        setTimeout(() => {
            window.location.href = '/login'; // 重定向到登录页
        }, 2000);
    }
});

// (可选) 窗口大小调整时更新终端
window.addEventListener('resize', () => {
    fitAddon.fit();
});