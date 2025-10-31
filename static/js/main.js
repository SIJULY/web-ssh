// 1. 初始化 Socket.IO
// (它会自动连接到提供此页面的服务器)
const socket = io();

// 2. 初始化 xterm.js
const term = new Terminal({
    cursorBlink: true,
    fontFamily: 'Courier-new, courier, monospace',
    fontSize: 16,
    theme: {
        background: '#000000',
        foreground: '#00FF00' // 绿色字体
    }
});

// 3. 加载 Fit 插件 (用于自适应大小)
const fitAddon = new FitAddon.FitAddon();
term.loadAddon(fitAddon);

// 4. 将 xterm 附加到 HTML 元素
term.open(document.getElementById('terminal'));

// 5. 使终端适应容器大小
fitAddon.fit();

// ------ 核心逻辑：数据双向绑定 ------

// 6. [前端 -> 后端]
// 当用户在 xterm 中键入时 (onData)
// 将数据通过 WebSocket (socket.emit) 发送到后端
term.onData((data) => {
    socket.emit('ssh_input', { 'data': data });
});

// 7. [后端 -> 前端]
// 当从后端收到 WebSocket 消息时 (socket.on)
// 将数据写入 xterm (term.write)
socket.on('ssh_output', (data) => {
    term.write(data.data);
});

// 8. 处理连接/断开事件
socket.on('connect', () => {
    // WebSocket 连接成功后，我们必须立即发送 'init_terminal' 事件
    // 我们从 terminal.html 中定义的全局变量 SERVER_ID 获取要连接的服务器
    
    if (typeof SERVER_ID !== 'undefined') {
        term.write('--- WebSocket Connected ---\r\n');
        term.write(`--- Initializing terminal for Server ID: ${SERVER_ID} ---\r\n`);
        
        // 【全新】发送初始化事件，告诉后端我们要连哪台服务器
        socket.emit('init_terminal', { 'server_id': SERVER_ID });
        
    } else {
        term.write('--- ERROR: SERVER_ID is not defined. Cannot initialize terminal. ---\r\n');
    }
});

socket.on('disconnect', (reason) => {
    term.write(`\r\n\r\n--- WebSocket Disconnected (Reason: ${reason}) ---\r\n`);
    // 如果是服务器主动断开 (例如认证失败)
    if (reason === 'io server disconnect') {
        term.write('--- Server connection failed or was rejected. ---\r\AN');
    }
});

// 9. (可选) 窗口大小调整时更新终端
window.addEventListener('resize', () => {
    fitAddon.fit();
    // TODO: 也可以将新的 cols/rows 发送到后端
    // 以便 paramiko 的 channel.resize_pty() 可以更新 PTY 大小
});
