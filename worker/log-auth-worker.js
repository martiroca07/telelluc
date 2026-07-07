const ONLINE_WINDOW_MS = 45 * 1000;

export default {
    async fetch(request, env) {
        const url = new URL(request.url);

        if (url.pathname === "/heartbeat" && request.method === "POST") {
            return handleHeartbeat(request, env);
        }

        if (url.pathname === "/devices" && request.method === "GET") {
            return handleDevices(request, env);
        }

        if (url.pathname === "/command" && request.method === "POST") {
            return handleCommandQueue(request, env);
        }

        if (url.pathname === "/command" && request.method === "GET") {
            return handleCommandDequeue(request, env);
        }

        if (url.pathname === "/devices" && request.method === "DELETE") {
            return handleDeleteDevice(request, env);
        }

        return new Response("Not found", { status: 404 });
    }
};

function checkBearer(request, expected) {
    const auth = request.headers.get("Authorization") || "";
    const match = auth.match(/^Bearer (.+)$/);
    return !!match && !!expected && match[1] === expected;
}

async function handleHeartbeat(request, env) {
    if (!checkBearer(request, env.AGENT_TOKEN)) {
        return new Response("Unauthorized", { status: 401 });
    }

    let body;
    try {
        body = await request.json();
    } catch (e) {
        return new Response("Bad Request", { status: 400 });
    }

    const hostname = body && body.hostname ? String(body.hostname).slice(0, 100) : null;
    if (!hostname) {
        return new Response("Missing hostname", { status: 400 });
    }

    const ip = request.headers.get("cf-connecting-ip") || "unknown";
    const key = `device:${hostname}`;

    const existingRaw = await env.DEVICES_KV.get(key);
    let record;
    if (existingRaw) {
        record = JSON.parse(existingRaw);
        record.ip = ip;
        record.lastSeen = Date.now();
    } else {
        const id = await nextId(env);
        record = { id, hostname, ip, lastSeen: Date.now() };
    }

    await env.DEVICES_KV.put(key, JSON.stringify(record));
    return new Response(JSON.stringify({ ok: true, id: record.id }), {
        headers: { "content-type": "application/json" }
    });
}

async function nextId(env) {
    const raw = await env.DEVICES_KV.get("next_id");
    const current = raw ? parseInt(raw, 10) : 0;
    const next = current + 1;
    await env.DEVICES_KV.put("next_id", String(next));
    return next;
}

async function handleDevices(request, env) {
    if (!checkBearer(request, env.INTERNAL_TOKEN)) {
        return new Response("Unauthorized", { status: 401 });
    }

    const list = await env.DEVICES_KV.list({ prefix: "device:" });
    const devices = [];
    for (const entry of list.keys) {
        const raw = await env.DEVICES_KV.get(entry.name);
        if (!raw) continue;
        const record = JSON.parse(raw);
        devices.push({
            id: record.id,
            hostname: record.hostname,
            ip: record.ip,
            lastSeen: record.lastSeen,
            online: Date.now() - record.lastSeen < ONLINE_WINDOW_MS
        });
    }
    devices.sort((a, b) => a.id - b.id);

    return new Response(JSON.stringify({ devices }), {
        headers: { "content-type": "application/json" }
    });
}

async function handleCommandQueue(request, env) {
    if (!checkBearer(request, env.INTERNAL_TOKEN)) {
        return new Response("Unauthorized", { status: 401 });
    }

    let body;
    try {
        body = await request.json();
    } catch (e) {
        return new Response("Bad Request", { status: 400 });
    }

    const deviceId = body && body.deviceId ? String(body.deviceId) : null;
    const command = body && body.command ? String(body.command) : null;
    if (!deviceId || !command) {
        return new Response("Missing deviceId or command", { status: 400 });
    }

    const key = `command:${deviceId}`;
    await env.DEVICES_KV.put(key, JSON.stringify({ command, timestamp: Date.now() }), {
        expirationTtl: 300
    });

    return new Response(JSON.stringify({ ok: true }), {
        headers: { "content-type": "application/json" }
    });
}

async function handleCommandDequeue(request, env) {
    if (!checkBearer(request, env.AGENT_TOKEN)) {
        return new Response("Unauthorized", { status: 401 });
    }

    const url = new URL(request.url);
    const deviceId = url.searchParams.get("deviceId");
    if (!deviceId) {
        return new Response("Missing deviceId", { status: 400 });
    }

    const key = `command:${deviceId}`;
    const raw = await env.DEVICES_KV.get(key);
    if (!raw) {
        return new Response(JSON.stringify({ command: null }), {
            headers: { "content-type": "application/json" }
        });
    }

    await env.DEVICES_KV.delete(key);
    const cmd = JSON.parse(raw);
    return new Response(JSON.stringify({ command: cmd.command }), {
        headers: { "content-type": "application/json" }
    });
}

async function handleDeleteDevice(request, env) {
    if (!checkBearer(request, env.INTERNAL_TOKEN)) {
        return new Response("Unauthorized", { status: 401 });
    }

    const url = new URL(request.url);
    const deviceId = url.searchParams.get("deviceId");
    if (!deviceId) {
        return new Response("Missing deviceId", { status: 400 });
    }

    const list = await env.DEVICES_KV.list({ prefix: "device:" });
    let found = false;
    for (const entry of list.keys) {
        const raw = await env.DEVICES_KV.get(entry.name);
        if (!raw) continue;
        const record = JSON.parse(raw);
        if (String(record.id) === String(deviceId)) {
            await env.DEVICES_KV.delete(entry.name);
            found = true;
            break;
        }
    }

    if (!found) {
        return new Response(JSON.stringify({ error: "device not found" }), {
            status: 404,
            headers: { "content-type": "application/json" }
        });
    }

    return new Response(JSON.stringify({ ok: true }), {
        headers: { "content-type": "application/json" }
    });
}
