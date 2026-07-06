// Cloudflare Worker for "telelluc-log-auth".
//
// Backend registry for telelluc.py instances running on multiple machines.
//
//   POST /heartbeat   (Authorization: Bearer AGENT_TOKEN)   { hostname }
//     Called periodically by each telelluc.py. Upserts the device record
//     (auto-incrementing id on first sighting, refreshed IP + lastSeen on
//     every call). The IP is read from the cf-connecting-ip header, so the
//     agent never needs to detect its own public IP.
//
//   GET /devices      (Authorization: Bearer INTERNAL_TOKEN)
//     Called only by the "telelluc" site worker (never directly by a
//     browser) to list all known devices.
//
// Required bindings (Cloudflare dashboard -> Settings):
//   KV Namespace binding: DEVICES_KV
// Required secrets (Settings -> Variables and Secrets, Encrypted):
//   AGENT_TOKEN    - shared secret embedded in telelluc.py
//   INTERNAL_TOKEN - shared secret, must match the same value set on the
//                    "telelluc" site worker

const ONLINE_WINDOW_MS = 45 * 1000; // heartbeat interval is 20s, allow one miss

export default {
    async fetch(request, env) {
        const url = new URL(request.url);

        if (url.pathname === "/heartbeat" && request.method === "POST") {
            return handleHeartbeat(request, env);
        }

        if (url.pathname === "/devices" && request.method === "GET") {
            return handleDevices(request, env);
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
    return new Response(JSON.stringify({ ok: true }), {
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
