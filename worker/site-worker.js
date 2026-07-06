// Cloudflare Worker for the "telelluc" site.
//
// Real server-side auth gate: unauthenticated visitors are served ONLY
// the LOGIN_HTML (a plain Debian-tty style login prompt). The full
// terminal (APP_HTML, with all its CSS/JS) is never sent to the browser
// until a valid session cookie is presented.
//
// Required secrets (set via Cloudflare dashboard -> Settings -> Variables
// and Secrets, as "Encrypted"):
//   AUTH_USER   - the login username
//   AUTH_PASS   - the login password
//   AUTH_SECRET - random long string used to sign session cookies
//
// None of those values are stored in this file, so it's safe to commit
// this script to a public repo.

const SESSION_COOKIE = "session";
const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7; // 7 days

export default {
    async fetch(request, env) {
        const url = new URL(request.url);

        if (url.pathname === "/login" && request.method === "POST") {
            return handleLogin(request, env);
        }

        if (url.pathname === "/logout") {
            return new Response(null, {
                status: 302,
                headers: {
                    Location: "/",
                    "Set-Cookie": `${SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`
                }
            });
        }

        const authed = await isAuthenticated(request, env);
        return new Response(authed ? APP_HTML : LOGIN_HTML, {
            headers: { "content-type": "text/html; charset=UTF-8" }
        });
    }
};

async function handleLogin(request, env) {
    let body;
    try {
        body = await request.json();
    } catch (e) {
        return new Response(JSON.stringify({ ok: false }), {
            status: 400,
            headers: { "content-type": "application/json" }
        });
    }

    const providedUser = body && body.user;
    const providedPass = body && body.pass;

    if (providedUser !== env.AUTH_USER || providedPass !== env.AUTH_PASS) {
        return new Response(JSON.stringify({ ok: false }), {
            status: 401,
            headers: { "content-type": "application/json" }
        });
    }

    const token = await makeSessionToken(env.AUTH_SECRET);
    return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: {
            "content-type": "application/json",
            "Set-Cookie": `${SESSION_COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${SESSION_MAX_AGE_SECONDS}`
        }
    });
}

async function isAuthenticated(request, env) {
    const cookieHeader = request.headers.get("Cookie") || "";
    const match = cookieHeader.match(new RegExp(`(?:^|; )${SESSION_COOKIE}=([^;]+)`));
    if (!match) return false;
    return verifySessionToken(match[1], env.AUTH_SECRET);
}

async function makeSessionToken(secret) {
    const expiry = Date.now() + SESSION_MAX_AGE_SECONDS * 1000;
    const payload = String(expiry);
    const sig = await hmac(payload, secret);
    return `${payload}.${sig}`;
}

async function verifySessionToken(token, secret) {
    const parts = token.split(".");
    if (parts.length !== 2) return false;
    const [payload, sig] = parts;
    if (!payload || !sig) return false;
    if (Number(payload) < Date.now()) return false;
    const expectedSig = await hmac(payload, secret);
    return timingSafeEqual(sig, expectedSig);
}

async function hmac(message, secret) {
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey(
        "raw",
        enc.encode(secret),
        { name: "HMAC", hash: "SHA-256" },
        false,
        ["sign"]
    );
    const sigBuffer = await crypto.subtle.sign("HMAC", key, enc.encode(message));
    return Array.from(new Uint8Array(sigBuffer))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
}

function timingSafeEqual(a, b) {
    if (a.length !== b.length) return false;
    let result = 0;
    for (let i = 0; i < a.length; i++) {
        result |= a.charCodeAt(i) ^ b.charCodeAt(i);
    }
    return result === 0;
}

const LOGIN_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>terminal</title>
    <style>
        :root {
            --bg: #000000;
            --fg: #eeeeee;
            --dim: #888888;
        }
        * { box-sizing: border-box; }
        html, body {
            height: 100%;
            margin: 0;
            overflow: hidden;
            background: var(--bg);
            color: var(--fg);
            font-family: "DejaVu Sans Mono", "Cascadia Code", "Fira Code", "Consolas", "Courier New", monospace;
            font-size: 15px;
        }
        #login-screen {
            position: fixed;
            inset: 0;
            display: flex;
            flex-direction: column;
            background: var(--bg);
            color: var(--fg);
            padding: 16px 20px;
            cursor: text;
        }
        #login-output { flex: 1; }
        #login-input-bar { display: flex; align-items: baseline; }
        #login-prompt-label { flex-shrink: 0; }
        #login-typed { white-space: pre-wrap; word-break: break-word; }
        .line { white-space: pre-wrap; word-break: break-word; }
        #hidden-input { position: absolute; opacity: 0; pointer-events: none; }
        .cursor {
            display: inline-block;
            width: 0.55em;
            height: 1.05em;
            background: var(--fg);
            vertical-align: text-bottom;
            animation: blink 1s steps(1) infinite;
            margin-left: 1px;
        }
        @keyframes blink {
            0%, 49% { opacity: 1; }
            50%, 100% { opacity: 0; }
        }
    </style>
</head>
<body>
    <div id="login-screen">
        <div id="login-output"></div>
        <div id="login-input-bar">
            <span id="login-prompt-label"></span>
            <span id="login-typed"></span><span class="cursor"></span>
        </div>
    </div>
    <input id="hidden-input" autocomplete="off" autocapitalize="off" spellcheck="false" />

    <script>
        var loginScreen = document.getElementById('login-screen');
        var loginOutput = document.getElementById('login-output');
        var loginPromptLabel = document.getElementById('login-prompt-label');
        var loginTyped = document.getElementById('login-typed');
        var hiddenInput = document.getElementById('hidden-input');

        var loginStage = 'username';
        var pendingUsername = '';

        function loginPrint(text) {
            var div = document.createElement('div');
            div.className = 'line';
            div.textContent = text || '';
            loginOutput.appendChild(div);
        }

        function setLoginPrompt(text) {
            loginPromptLabel.textContent = text;
        }

        function resetLogin() {
            loginStage = 'username';
            pendingUsername = '';
            setLoginPrompt('telelluc login: ');
        }

        function handleLoginEnter(value) {
            if (loginStage === 'username') {
                pendingUsername = value;
                loginTyped.textContent = '';
                loginStage = 'password';
                setLoginPrompt('Password: ');
                return;
            }
            var password = value;
            loginTyped.textContent = '';
            setLoginPrompt('');
            fetch('/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user: pendingUsername, pass: password })
            }).then(function (resp) {
                if (resp.ok) {
                    location.reload();
                    return;
                }
                loginPrint('');
                loginPrint('Login incorrect');
                resetLogin();
            }).catch(function () {
                loginPrint('');
                loginPrint('Login incorrect');
                resetLogin();
            });
        }

        hiddenInput.addEventListener('input', function () {
            loginTyped.textContent = loginStage === 'password' ? '' : hiddenInput.value;
        });

        hiddenInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                var value = hiddenInput.value;
                hiddenInput.value = '';
                loginTyped.textContent = '';
                handleLoginEnter(value);
            }
        });

        loginScreen.addEventListener('click', function () {
            hiddenInput.focus();
        });

        window.addEventListener('load', function () {
            hiddenInput.focus();
            resetLogin();
        });
    </script>
</body>
</html>
`;

const APP_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>terminal</title>
    <style>
        :root {
            --bg: #000000;
            --fg: #eeeeee;
            --dim: #888888;
            --user-color: #4e9a06;
            --path-color: #3465a4;
            --error: #ff5555;
            --suggest-bg: #1a1a1a;
            --suggest-active-bg: #3465a4;
        }

        * {
            box-sizing: border-box;
        }

        html, body {
            height: 100%;
            margin: 0;
            overflow: hidden;
            background: var(--bg);
            color: var(--fg);
            font-family: "DejaVu Sans Mono", "Cascadia Code", "Fira Code", "Consolas", "Courier New", monospace;
            font-size: 15px;
        }

        #terminal {
            height: 100vh;
            width: 100vw;
            display: flex;
            flex-direction: column;
            cursor: text;
        }

        #corner-banner {
            position: fixed;
            top: 10px;
            right: 18px;
            z-index: 10;
            margin: 0;
            font-family: "Cascadia Code", "Fira Code", "Consolas", "Courier New", monospace;
            font-size: 11px;
            font-weight: bold;
            line-height: 1.15;
            white-space: pre;
            pointer-events: none;
            user-select: none;
            background-image: linear-gradient(90deg, #ff0000, #ff9900, #ffee00, #33ff00, #00c3ff, #7700ff, #ff0000);
            background-size: 300% auto;
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
            animation: rainbow-move 4s linear infinite;
        }

        #corner-banner.hidden {
            display: none;
        }

        @keyframes rainbow-move {
            0% { background-position: 0% 50%; }
            100% { background-position: 300% 50%; }
        }

        #output-wrap {
            flex: 1;
            min-height: 0;
            overflow-y: auto;
            padding: 40px 20px 0;
            line-height: 1.45;
        }

        #output-wrap::-webkit-scrollbar {
            width: 10px;
        }

        #output-wrap::-webkit-scrollbar-thumb {
            background: #2a2a2a;
            border-radius: 6px;
        }

        .line {
            white-space: pre-wrap;
            word-break: break-word;
        }

        .prompt-line {
            display: flex;
            align-items: baseline;
        }

        #bottom-bar {
            position: relative;
            flex-shrink: 0;
        }

        #input-bar {
            padding: 8px 20px 16px;
            line-height: 1.45;
        }

        .prompt {
            flex-shrink: 0;
            margin-right: 8px;
        }

        .p-user {
            color: var(--user-color);
            font-weight: bold;
        }

        .p-path {
            color: var(--path-color);
            font-weight: bold;
        }

        .p-sep {
            color: var(--fg);
        }

        .dim {
            color: var(--dim);
        }

        .error {
            color: var(--error);
        }

        #suggestions {
            display: none;
            position: absolute;
            bottom: 100%;
            left: 0;
            right: 0;
            gap: 6px;
            flex-wrap: wrap;
            padding: 6px 20px;
            background: var(--bg);
        }

        #suggestions.visible {
            display: flex;
        }

        #suggestions .suggestion {
            background: var(--suggest-bg);
            color: var(--fg);
            padding: 2px 10px;
            border-radius: 4px;
        }

        #suggestions .suggestion.active {
            background: var(--suggest-active-bg);
            color: #ffffff;
        }

        #input-line {
            flex: 1;
            position: relative;
            min-height: 1.3em;
        }

        #hidden-input {
            position: absolute;
            opacity: 0;
            pointer-events: none;
        }

        #typed {
            white-space: pre-wrap;
            word-break: break-word;
        }

        .cursor {
            display: inline-block;
            width: 0.55em;
            height: 1.05em;
            background: var(--fg);
            vertical-align: text-bottom;
            animation: blink 1s steps(1) infinite;
            margin-left: 1px;
        }

        @keyframes blink {
            0%, 49% { opacity: 1; }
            50%, 100% { opacity: 0; }
        }
    </style>
</head>
<body>
    <pre id="corner-banner"> _       _      _ _
| |_ ___| | ___| | |_   _  ___
| __/ _ \\ |/ _ \\ | | | | |/ __|
| ||  __/ |  __/ | | |_| | (__
 \\__\\___|_|\\___|_|_|\\__,_|\\___| </pre>
    <div id="terminal">
        <div id="output-wrap">
            <div id="output"></div>
        </div>
        <div id="bottom-bar">
            <div id="suggestions"></div>
            <div class="prompt-line" id="input-bar">
                <span class="prompt" id="prompt-label"></span>
                <div id="input-line"><span id="typed"></span><span class="cursor"></span></div>
            </div>
        </div>
    </div>

    <input id="hidden-input" autocomplete="off" autocapitalize="off" spellcheck="false" />

    <script>
        var terminal = document.getElementById('terminal');
        var outputWrap = document.getElementById('output-wrap');
        var output = document.getElementById('output');
        var typed = document.getElementById('typed');
        var hiddenInput = document.getElementById('hidden-input');
        var promptLabel = document.getElementById('prompt-label');
        var suggestionsBar = document.getElementById('suggestions');
        var cornerBanner = document.getElementById('corner-banner');

        var host = 'telelluc';
        var user = 'user';
        var history = [];
        var historyIndex = -1;
        var lang = 'en';
        var bannerEnabled = true;

        var strings = {
            en: {
                welcome: 'Welcome. Type "help" to see the available commands.',
                helpHeader: 'Available commands:',
                helpLines: [
                    'help          show this help',
                    'echo [text]   repeat the text',
                    'date          show the current date and time',
                    'whoami        show the current user',
                    'clear / cls   clear the screen',
                    'showbanner    toggle the rainbow "telelluc" text (top-right)',
                    'language      switch the interface language (English/Español)',
                    'crash         trigger the local telelluc.py error popup',
                    'logout        end the session'
                ],
                notFound: function (cmd) { return 'command not found: ' + cmd; },
                tryHelp: 'type "help" to see the available commands',
                langSwitched: 'Language set to English.',
                crashFailed: 'Could not reach telelluc.py — is it running?'
            },
            es: {
                welcome: 'Bienvenido. Escribí "help" para ver los comandos disponibles.',
                helpHeader: 'Comandos disponibles:',
                helpLines: [
                    'help          muestra esta ayuda',
                    'echo [texto]  repite el texto',
                    'date          muestra la fecha y hora actual',
                    'whoami        muestra el usuario actual',
                    'clear / cls   limpia la pantalla',
                    'showbanner    activa/desactiva el texto "telelluc" (arcoíris, arriba a la derecha)',
                    'language      cambia el idioma de la interfaz (English/Español)',
                    'crash         dispara el error emergente de telelluc.py',
                    'logout        cierra la sesión'
                ],
                notFound: function (cmd) { return 'comando no encontrado: ' + cmd; },
                tryHelp: 'escribí "help" para ver los comandos disponibles',
                langSwitched: 'Idioma cambiado a Español.',
                crashFailed: 'No se pudo contactar a telelluc.py — ¿está corriendo?'
            }
        };

        function t() {
            return strings[lang];
        }

        function updatePrompt() {
            promptLabel.innerHTML =
                '<span class="p-user">' + user + '@' + host + '</span>' +
                '<span class="p-sep">:</span>' +
                '<span class="p-path">~</span>' +
                '<span class="p-sep">$</span>';
        }

        function print(text, className) {
            var div = document.createElement('div');
            div.className = 'line' + (className ? ' ' + className : '');
            div.textContent = text || '';
            output.appendChild(div);
        }

        function scrollToBottom() {
            outputWrap.scrollTop = outputWrap.scrollHeight;
        }

        var commands = {
            help: function () {
                print(t().helpHeader);
                t().helpLines.forEach(function (line) { print('  ' + line); });
            },
            showbanner: function () {
                bannerEnabled = !bannerEnabled;
                cornerBanner.classList.toggle('hidden', !bannerEnabled);
            },
            echo: function (args) {
                print(args.join(' '));
            },
            date: function () {
                var now = new Date();
                print(lang === 'es' ? now.toLocaleString('es-ES') : now.toLocaleString('en-US'));
            },
            whoami: function () {
                print(user);
            },
            clear: function () {
                output.innerHTML = '';
            },
            language: function () {
                lang = lang === 'en' ? 'es' : 'en';
                print(t().langSwitched, 'dim');
            },
            crash: function () {
                fetch('http://127.0.0.1:5005/crash').catch(function () {
                    print(t().crashFailed, 'error');
                });
            },
            logout: function () {
                window.location.href = '/logout';
            }
        };
        commands.cls = commands.clear;

        var commandNames = Object.keys(commands).sort();

        function runCommand(raw) {
            var trimmed = raw.trim();
            if (trimmed.length > 0) {
                history.push(trimmed);
            }
            historyIndex = history.length;

            var echoLine = document.createElement('div');
            echoLine.className = 'prompt-line';
            var p = document.createElement('span');
            p.className = 'prompt';
            p.innerHTML = promptLabel.innerHTML;
            echoLine.appendChild(p);
            var cmdText = document.createElement('span');
            cmdText.textContent = raw;
            echoLine.appendChild(cmdText);
            output.appendChild(echoLine);

            if (trimmed.length === 0) return;

            var parts = trimmed.split(/\\s+/);
            var cmd = parts[0];
            var args = parts.slice(1);
            var fn = commands[cmd.toLowerCase()];
            if (fn) {
                fn(args);
            } else {
                print(t().notFound(cmd), 'error');
                print(t().tryHelp, 'dim');
            }
        }

        function refreshTyped() {
            typed.textContent = hiddenInput.value;
        }

        var suggestions = [];
        var suggestionIndex = 0;

        function computeSuggestions(value) {
            var v = value.toLowerCase();
            if (!v) return [];
            return commandNames.filter(function (name) { return name.indexOf(v) === 0; });
        }

        function renderSuggestions() {
            suggestionsBar.innerHTML = '';
            if (suggestions.length === 0) {
                suggestionsBar.classList.remove('visible');
                return;
            }
            suggestions.forEach(function (name, i) {
                var span = document.createElement('span');
                span.className = 'suggestion' + (i === suggestionIndex ? ' active' : '');
                span.textContent = name;
                suggestionsBar.appendChild(span);
            });
            suggestionsBar.classList.add('visible');
        }

        function updateSuggestionsFromInput() {
            suggestions = computeSuggestions(hiddenInput.value);
            suggestionIndex = 0;
            renderSuggestions();
        }

        hiddenInput.addEventListener('input', function () {
            refreshTyped();
            updateSuggestionsFromInput();
            scrollToBottom();
        });

        hiddenInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                var value = hiddenInput.value;
                hiddenInput.value = '';
                refreshTyped();
                suggestions = [];
                renderSuggestions();
                runCommand(value);
                scrollToBottom();
            } else if (e.key === 'Tab') {
                e.preventDefault();
                if (suggestions.length === 0) return;
                hiddenInput.value = suggestions[suggestionIndex];
                refreshTyped();
                updateSuggestionsFromInput();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (suggestions.length > 0) {
                    suggestionIndex = (suggestionIndex - 1 + suggestions.length) % suggestions.length;
                    renderSuggestions();
                    return;
                }
                if (history.length === 0) return;
                historyIndex = Math.max(0, historyIndex - 1);
                hiddenInput.value = history[historyIndex] || '';
                refreshTyped();
            } else if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (suggestions.length > 0) {
                    suggestionIndex = (suggestionIndex + 1) % suggestions.length;
                    renderSuggestions();
                    return;
                }
                if (history.length === 0) return;
                historyIndex = Math.min(history.length, historyIndex + 1);
                hiddenInput.value = history[historyIndex] || '';
                refreshTyped();
            } else if (e.key === 'Escape') {
                suggestions = [];
                renderSuggestions();
            }
        });

        terminal.addEventListener('click', function () {
            hiddenInput.focus();
        });

        window.addEventListener('load', function () {
            hiddenInput.focus();
            updatePrompt();
            print(t().welcome, 'dim');
        });
    </script>
</body>
</html>
`;
