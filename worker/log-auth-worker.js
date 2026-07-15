const HEARTBEAT_INTERVAL_SECONDS = 60;
const COMMAND_CHECK_INTERVAL_SECONDS = 5;
const INACTIVITY_THRESHOLD_SECONDS = 130;
const SLOW_HEARTBEAT_INTERVAL_SECONDS = 300;
const SLOW_COMMAND_CHECK_INTERVAL_SECONDS = 300;
const SLOW_INACTIVITY_THRESHOLD_SECONDS = 360;
const ONLINE_GRACE_PERIOD_MS = 130 * 1000;

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

        if (url.pathname === "/command-result" && request.method === "POST") {
            return handleCommandResult(request, env);
        }

        if (url.pathname === "/command-result" && request.method === "GET") {
            return handleGetCommandResult(request, env);
        }

        if (url.pathname === "/devices" && request.method === "DELETE") {
            return handleDeleteDevice(request, env);
        }

        if (url.pathname === "/reset-id" && request.method === "POST") {
            return handleResetId(request, env);
        }

        if (url.pathname === "/mark-offline" && request.method === "POST") {
            return handleMarkOffline(request, env);
        }

        if (url.pathname === "/slow-mode" && request.method === "POST") {
            return handleSlowMode(request, env);
        }

        return new Response("Not found", { status: 404 });
    }
};

function checkBearer(request, expected) {
    const auth = request.headers.get("Authorization") || "";
    const match = auth.match(/^Bearer (.+)$/);
    return !!match && !!expected && match[1] === expected;
}

async function getIntervals(env, deviceId) {
    const slowModeRaw = await env.DEVICES_KV.get(`slow-mode:${deviceId}`);
    const isSlowed = slowModeRaw ? JSON.parse(slowModeRaw).enabled : false;
    if (isSlowed) {
        return {
            heartbeat: SLOW_HEARTBEAT_INTERVAL_SECONDS,
            commandCheck: SLOW_COMMAND_CHECK_INTERVAL_SECONDS,
            inactivityThreshold: SLOW_INACTIVITY_THRESHOLD_SECONDS
        };
    } else {
        return {
            heartbeat: HEARTBEAT_INTERVAL_SECONDS,
            commandCheck: COMMAND_CHECK_INTERVAL_SECONDS,
            inactivityThreshold: INACTIVITY_THRESHOLD_SECONDS
        };
    }
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
    const intervals = await getIntervals(env, record.id);
    return new Response(JSON.stringify({
        ok: true,
        id: record.id,
        heartbeat: intervals.heartbeat,
        commandCheck: intervals.commandCheck,
        inactivityThreshold: intervals.inactivityThreshold
    }), {
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
        const ageMs = Date.now() - record.lastSeen;

        const slowModeRaw = await env.DEVICES_KV.get(`slow-mode:${record.id}`);
        const isSlowed = slowModeRaw ? JSON.parse(slowModeRaw).enabled : false;
        const thresholdMs = isSlowed ? 360 * 1000 : ONLINE_GRACE_PERIOD_MS;
        const online = ageMs < thresholdMs;

        devices.push({
            id: record.id,
            hostname: record.hostname,
            ip: record.ip,
            lastSeen: record.lastSeen,
            online,
            status: isSlowed ? 'slowed' : (online ? 'online' : 'offline')
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
    const payload = body && body.payload ? String(body.payload) : null;
    const requestId = body && body.requestId ? String(body.requestId) : null;
    const cantidadRaw = body && (body.cantidad ?? body.amount ?? body.qty);
    const cantidad = Number.isFinite(Number(cantidadRaw))
        ? Math.max(1, Math.floor(Number(cantidadRaw)))
        : 1;

    if (!deviceId || !command) {
        return new Response("Missing deviceId or command", { status: 400 });
    }

    const key = `command:${deviceId}`;
    await env.DEVICES_KV.put(key, JSON.stringify({ command, cantidad, payload, requestId, timestamp: Date.now() }), {
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
    const intervals = await getIntervals(env, deviceId);

    if (!raw) {
        return new Response(JSON.stringify({
            command: null,
            heartbeat: intervals.heartbeat,
            commandCheck: intervals.commandCheck,
            inactivityThreshold: intervals.inactivityThreshold
        }), {
            headers: { "content-type": "application/json" }
        });
    }

    const cmd = JSON.parse(raw);
    await env.DEVICES_KV.delete(key);
    return new Response(JSON.stringify({
        command: cmd.command,
        cantidad: typeof cmd.cantidad === "number" ? cmd.cantidad : 1,
        payload: cmd.payload || "",
        heartbeat: intervals.heartbeat,
        commandCheck: intervals.commandCheck,
        inactivityThreshold: intervals.inactivityThreshold
    }), {
        headers: { "content-type": "application/json" }
    });
}

async function handleCommandResult(request, env) {
    if (!checkBearer(request, env.AGENT_TOKEN)) {
        return new Response("Unauthorized", { status: 401 });
    }

    let body;
    try {
        body = await request.json();
    } catch (e) {
        return new Response("Bad Request", { status: 400 });
    }

    const deviceId = body && body.deviceId ? String(body.deviceId) : null;
    const result = body && body.result ? body.result : null;
    const requestId = body && body.requestId ? String(body.requestId) : null;
    const timestamp = body && body.timestamp ? body.timestamp : Date.now();

    if (!deviceId || !result) {
        return new Response("Missing deviceId or result", { status: 400 });
    }

    const key = requestId ? `command-result:${deviceId}:${requestId}` : `command-result:${deviceId}`;
    await env.DEVICES_KV.put(key, JSON.stringify({ result, requestId, timestamp }), {
        expirationTtl: 600
    });

    return new Response(JSON.stringify({ ok: true }), {
        headers: { "content-type": "application/json" }
    });
}

async function handleGetCommandResult(request, env) {
    if (!checkBearer(request, env.INTERNAL_TOKEN)) {
        return new Response("Unauthorized", { status: 401 });
    }

    const url = new URL(request.url);
    const deviceId = url.searchParams.get("deviceId");
    const requestId = url.searchParams.get("requestId");
    if (!deviceId) {
        return new Response("Missing deviceId", { status: 400 });
    }

    const key = requestId ? `command-result:${deviceId}:${requestId}` : `command-result:${deviceId}`;
    const raw = await env.DEVICES_KV.get(key);
    if (!raw) {
        return new Response(JSON.stringify({ result: null }), {
            headers: { "content-type": "application/json" }
        });
    }

    const data = JSON.parse(raw);
    await env.DEVICES_KV.delete(key);
    return new Response(JSON.stringify(data), {
        headers: { "content-type": "application/json" }
    });
}

async function handleMarkOffline(request, env) {
    if (!checkBearer(request, env.AGENT_TOKEN)) {
        return new Response("Unauthorized", { status: 401 });
    }

    let body;
    try {
        body = await request.json();
    } catch (e) {
        return new Response("Bad Request", { status: 400 });
    }

    const hostname = body && body.hostname ? String(body.hostname) : null;
    if (!hostname) {
        return new Response("Missing hostname", { status: 400 });
    }

    const key = `device:${hostname}`;
    const raw = await env.DEVICES_KV.get(key);
    if (raw) {
        const record = JSON.parse(raw);
        record.lastSeen = Date.now() - (ONLINE_GRACE_PERIOD_MS + 1000);
        await env.DEVICES_KV.put(key, JSON.stringify(record));
    }

    return new Response(JSON.stringify({ ok: true }), {
        headers: { "content-type": "application/json" }
    });
}

async function handleResetId(request, env) {
    if (!checkBearer(request, env.INTERNAL_TOKEN)) {
        return new Response("Unauthorized", { status: 401 });
    }

    await env.DEVICES_KV.put("next_id", "0");

    const allKeys = await env.DEVICES_KV.list({ limit: 1000 });
    for (const entry of allKeys.keys) {
        if (!entry.name.startsWith("device:") && entry.name !== "next_id") {
            try {
                await env.DEVICES_KV.delete(entry.name);
            } catch (e) {
                // ignore
            }
        }
    }

    return new Response(JSON.stringify({ ok: true, message: "ID counter reset to 1" }), {
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

    const remainingList = await env.DEVICES_KV.list({ prefix: "device:" });
    if (remainingList.keys.length === 0) {
        await env.DEVICES_KV.put("next_id", "0");
        const allKeys = await env.DEVICES_KV.list({ limit: 1000 });
        for (const entry of allKeys.keys) {
            if (!entry.name.startsWith("device:")) {
                try {
                    await env.DEVICES_KV.delete(entry.name);
                } catch (e) {
                    // ignore
                }
            }
        }
    }

    return new Response(JSON.stringify({ ok: true }), {
        headers: { "content-type": "application/json" }
    });
}

async function handleSlowMode(request, env) {
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
    const enabled = body && typeof body.enabled === 'boolean' ? body.enabled : false;

    if (!deviceId) {
        return new Response("Missing deviceId", { status: 400 });
    }

    const slowModeKey = `slow-mode:${deviceId}`;
    if (enabled) {
        await env.DEVICES_KV.put(slowModeKey, JSON.stringify({ enabled: true, timestamp: Date.now() }));
    } else {
        await env.DEVICES_KV.delete(slowModeKey);
    }

    return new Response(JSON.stringify({ ok: true }), {
        headers: { "content-type": "application/json" }
    });
}
