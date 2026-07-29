# TELELLUC - Windows SSH Simulator

Remote command execution and file management simulator for Windows systems over Cloudflare Workers.

## Quick Start

1. **Compile the agent**
   ```bash
   cd C:\Users\User\Desktop\telelluc
   python -m PyInstaller --onefile --noconsole --icon telelluc.ico --name "tl-service" telelluc.py
   ```

2. **Deploy the backend**
   ```bash
   npx wrangler deploy --config wrangler-auth.toml
   ```

3. **Deploy the frontend**
   ```bash
   npx wrangler deploy
   ```

## Configuration

### Changing Login Credentials

To change the username and password for the web interface:

#### **From Cloudflare Dashboard (Recommended)**

1. Go to https://dash.cloudflare.com/
2. Select **Workers** → **telelluc-site**
3. Click **Settings** → **Environment Variables**
4. Find `AUTH_USER` and `AUTH_PASS`
5. Click **Edit** and change the values:
   - `AUTH_USER`: your new username
   - `AUTH_PASS`: your new password
6. Click **Save and deploy**

#### **From Local Code (via wrangler)**

1. Open `wrangler.toml`
2. Find the `[env.production]` section
3. Update the variables:
   ```toml
   [env.production]
   vars = { 
       AUTH_USER = "your_new_username",
       AUTH_PASS = "your_new_password"
   }
   ```
4. Deploy:
   ```bash
   npx wrangler deploy
   ```

## Architecture

### Command Execution Flow

**Query Commands** (ipconfig, disk, processes, sysinfo, taskkill):
- Simple last-result pattern
- Each execution gets unique requestId
- KV key: `command-result:${deviceId}:${requestId}`
- Frontend retries 80× over 12 seconds
- Result deleted immediately after retrieval (prevents contamination)

**Control Mode** (cd, ls, pwd, cat, nano, etc):
- Persistent session with requestId isolation
- KV key: `command-result:${deviceId}:${requestId}`
- 60-second inactivity timeout (auto-disconnect)
- Auto-cleanup on page refresh
- Working directory synchronized with prompt

### Critical Files

| File | Purpose |
|------|---------|
| `telelluc.py` | Windows agent (command execution) |
| `worker/log-auth-worker.js` | Backend (Cloudflare Workers) - KV storage, device management |
| `worker/site-worker.js` | Frontend proxy (Cloudflare Workers) - authentication, routing |
| `public/index.html` | Web terminal UI |

### Key Timeouts

| Setting | Value | Purpose |
|---------|-------|---------|
| Heartbeat (normal) | 60 seconds | Device check-in frequency |
| Heartbeat (slow mode) | 300 seconds | Reduced polling for inactive devices |
| Inactivity threshold | 130 seconds | Mark offline (normal mode) |
| Inactivity threshold (slow) | 360 seconds | Mark offline (slow mode) |
| Control mode timeout | 60 seconds | Auto-disconnect on inactivity |
| Query timeout | 12 seconds | Max wait for command response |

## DO NOT TOUCH - Critical Code Sections

⚠️ These sections have documented incidents. Do NOT modify without understanding the consequences.

### Backend (`log-auth-worker.js`)

**handleCommandDequeue - requestId conditional** (line ~732)
- Only include requestId if it has value (not empty string)
- Python's `if requestId:` fails on `""`
- INCIDENT: v0.1.56 broke isolation by returning empty string
- CONSEQUENCE: All commands used same KV key → complete output contamination

**handleCommandResult - KV key generation** (line ~327)
- Both patterns MUST coexist: `command-result:${deviceId}:${requestId}` AND `command-result:${deviceId}`
- INCIDENT: v0.1.50 removed requestId support → control mode broke
- CONSEQUENCE: ls, cd, pwd showed timeout despite executing

**handleGetCommandResult - immediate deletion** (line ~375)
- MUST delete KV entry immediately after returning
- INCIDENT: v0.1.50 changed storage pattern, output contamination occurred
- CONSEQUENCE: Running "disk 1" followed by "ipconfig 1" mixed their outputs

### Frontend (`public/index.html`)

**queryWithRetry function** (line ~767)
- 80 attempts × 150ms = 12 second timeout
- DO NOT REDUCE: agent heartbeat is 60s, needs buffer for slow devices
- DO NOT INCREASE DELAY: 150ms is calibrated for agent polling

**sendRemoteCommand function** (line ~1596)
- Control mode uses requestId isolation (DIFFERENT from queryWithRetry)
- INCIDENT: v0.1.49 tried to merge both patterns → broke both
- Control mode REQUIRES requestId for multi-step command isolation

**INACTIVITY_TIMEOUT** (line ~608)
- 60 seconds (1 minute)
- Prevents orphaned sessions when browser refreshes
- INCIDENT: v0.1.59 was 5 minutes, sessions stayed alive after refresh

### Agent (`telelluc.py`)

**__reset_dir__ handler** (line ~729)
- MUST use global current_working_dir (declared at function start)
- INCIDENT: v0.1.59 wasn't resetting directory on control mode entry
- CONSEQUENCE: Prompt showed C:\Users but was actually in old location

## Troubleshooting

**"No response from device"**
- Device is offline or unresponsive
- Check: agent running, heartbeat not missed 2+ times

**Output shows wrong command's result**
- Query command contamination (rare with v0.1.56+)
- Workaround: run command again with different requestId
- Root cause: check KV result deletion in backend

**Control mode stuck in old directory**
- Page refresh while in control mode didn't exit cleanly
- Solution: v0.1.59 auto-exits on refresh (close and reopen control mode)

**Device marked offline but agent is running**
- Slow mode activated - inactivity threshold increased to 360s
- Device is actually fine, just slow polling
- Run `fast 1` to restore normal polling

## Version History

Latest stable: **v0.1.61**

Key versions:
- v0.1.60: Fixed control mode directory reset bug
- v0.1.59: Auto-cleanup orphaned sessions on page refresh
- v0.1.58: 60-second inactivity timeout
- v0.1.57: Fixed requestId passthrough in agent dequeue
- v0.1.56: Query command isolation (separate requestId per execution)

See git log for full history.
