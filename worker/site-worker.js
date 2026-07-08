const SESSION_COOKIE = "session";
const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7;
const LOG_AUTH_URL = "https://log-auth";

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

        if (url.pathname === "/api/devices" && request.method === "GET") {
            return handleDevicesProxy(request, env);
        }

        if (url.pathname === "/api/error" && request.method === "POST") {
            return handleerrorCommand(request, env);
        }

        // Nueva ruta para el comando remoto de borrado del agente
        if (url.pathname === "/api/self-delete" && request.method === "POST") {
            return handleSelfDeleteCommand(request, env);
        }

        if (url.pathname === "/api/rm" && request.method === "POST") {
            return handleDeleteDevice(request, env);
        }

        const authed = await isAuthenticated(request, env);
        if (!authed) {
            return new Response(LOGIN_HTML, {
                headers: { "content-type": "text/html; charset=UTF-8" }
            });
        }

        return env.ASSETS.fetch(request);
    }
};

async function handleDevicesProxy(request, env) {
    const authed = await isAuthenticated(request, env);
    if (!authed) {
        return new Response(JSON.stringify({ error: "unauthorized" }), {
            status: 401,
            headers: { "content-type": "application/json" }
        });
    }

    const upstream = await env.LOG_AUTH.fetch(`${LOG_AUTH_URL}/devices`, {
        headers: { Authorization: `Bearer ${env.INTERNAL_TOKEN}` }
    });

    return new Response(await upstream.text(), {
        status: upstream.status,
        headers: { "content-type": "application/json" }
    });
}

async function handleerrorCommand(request, env) {
    const authed = await isAuthenticated(request, env);
    if (!authed) {
        return new Response(JSON.stringify({ error: "unauthorized" }), {
            status: 401,
            headers: { "content-type": "application/json" }
        });
    }

    let body;
    try {
        body = await request.json();
    } catch (e) {
        body = {};
    }

    const deviceId = body && body.deviceId ? String(body.deviceId) : null;
    const cantidad = body && body.cantidad ? Number(body.cantidad) : 1;

    if (!deviceId) {
        return new Response(JSON.stringify({ error: "missing deviceId" }), {
            status: 400,
            headers: { "content-type": "application/json" }
        });
    }

    const upstream = await env.LOG_AUTH.fetch(`${LOG_AUTH_URL}/command`, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${env.INTERNAL_TOKEN}`,
            "Content-Type": "application/json"
        },
        body: JSON.stringify({ deviceId, command: "error", cantidad })
    });

    return new Response(await upstream.text(), {
        status: upstream.status,
        headers: { "content-type": "application/json" }
    });
}

// Nueva función añadida para procesar y enrutar la orden 'self-delete' al backend
async function handleSelfDeleteCommand(request, env) {
    const authed = await isAuthenticated(request, env);
    if (!authed) {
        return new Response(JSON.stringify({ error: "unauthorized" }), {
            status: 401,
            headers: { "content-type": "application/json" }
        });
    }

    let body;
    try {
        body = await request.json();
    } catch (e) {
        body = {};
    }

    const deviceId = body && body.deviceId ? String(body.deviceId) : null;

    if (!deviceId) {
        return new Response(JSON.stringify({ error: "missing deviceId" }), {
            status: 400,
            headers: { "content-type": "application/json" }
        });
    }

    const upstream = await env.LOG_AUTH.fetch(`${LOG_AUTH_URL}/command`, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${env.INTERNAL_TOKEN}`,
            "Content-Type": "application/json"
        },
        body: JSON.stringify({ deviceId, command: "self-delete" })
    });

    return new Response(await upstream.text(), {
        status: upstream.status,
        headers: { "content-type": "application/json" }
    });
}

async function handleDeleteDevice(request, env) {
    const authed = await isAuthenticated(request, env);
    if (!authed) {
        return new Response(JSON.stringify({ error: "unauthorized" }), {
            status: 401,
            headers: { "content-type": "application/json" }
        });
    }

    let body;
    try {
        body = await request.json();
    } catch (e) {
        body = {};
    }

    const deviceId = body && body.deviceId ? String(body.deviceId) : null;
    if (!deviceId) {
        return new Response(JSON.stringify({ error: "missing deviceId" }), {
            status: 400,
            headers: { "content-type": "application/json" }
        });
    }

    const upstream = await env.LOG_AUTH.fetch(`${LOG_AUTH_URL}/devices?deviceId=${encodeURIComponent(deviceId)}`, {
        method: "DELETE",
        headers: {
            Authorization: `Bearer ${env.INTERNAL_TOKEN}`
        }
    });

    return new Response(await upstream.text(), {
        status: upstream.status,
        headers: { "content-type": "application/json" }
    });
}

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