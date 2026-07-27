# Mimetic Command Testing Guide

## System Flow & Timeouts

### Timeline for Mimetic Command Execution

```
[Total Maximum Time: ~7 seconds]

1. User executes "mimetic" in terminal web (0s)
   ├─ Frontend sends command to Cloudflare Worker (immediate)
   └─ Worker stores in KV (immediate)

2. Python Agent waits for next polling cycle (0-5s)
   ├─ COMMAND_CHECK_INTERVAL_SECONDS = 5 seconds (normal mode)
   ├─ Worst case: just polled, must wait full 5 seconds
   └─ Best case: already waiting, executes immediately

3. Python Agent receives command and executes __mimetic__ (5s)
   ├─ mimetic_keylogger() starts
   ├─ keyboard.on_press() listener installed
   └─ Waits for ESC key or 2-second timeout

4. Mimetic captures keyboard input (5-7s)
   ├─ User types or presses ESC
   ├─ Returns immediately if ESC pressed
   └─ Auto-returns after 2 seconds if no ESC

5. Python Agent sends result back to Worker (7s)
   └─ Worker stores in KV

6. Frontend polls for result (7-13s)
   ├─ Polls every 150ms
   ├─ Maximum 40 attempts = 6 seconds total
   ├─ Retrieves result and displays
   └─ Total timeout = 6 seconds from step 1

CRITICAL: Total time can be 5 (poll wait) + 2 (mimetic) = 7 seconds
Frontend only waits 6 seconds → Can timeout in worst case
```

## Testing Procedures

### Test 1: Quick Response (Should Always Work)
1. Execute: `mimetic`
2. Immediately press: `hello` then `ESC`
3. Expected: See "hello" in output within 2 seconds
4. Status: ✓ PASS if output appears, ✗ FAIL if timeout

### Test 2: Timeout Handling (2-Second Limit)
1. Execute: `mimetic`
2. Wait 2 seconds without pressing anything
3. Expected: Auto-returns "No keys recorded" after 2 seconds
4. Status: ✓ PASS if completes within 4 seconds total, ✗ FAIL if timeout

### Test 3: Multiple Characters
1. Execute: `mimetic`
2. Type slowly: `a b c [ENTER] x y z [SPACE] test`
3. Press: `ESC`
4. Expected: See captured sequence with special keys marked
5. Status: ✓ PASS if all keys captured correctly

### Test 4: Special Keys Detection
1. Execute: `mimetic`
2. Press: `[SHIFT] [CTRL] [ALT] [DELETE] [BACKSPACE] [TAB]`
3. Press: `ESC`
4. Expected: See `[SHIFT] [CTRL] [ALT] [DELETE] [BACKSPACE] [TAB]`
5. Status: ✓ PASS if all special keys recognized

## Code Architecture for Future AI Review

### Key Components:
- **Frontend Timeout**: `waitForShellResult()` = 40 attempts × 150ms = 6 seconds
- **Agent Polling**: `command_check_loop()` = 5 seconds default (COMMAND_CHECK_INTERVAL_SECONDS)
- **Mimetic Timeout**: `stop_event.wait(timeout=2)` = 2 seconds maximum
- **Worker Response**: log-auth-worker.js stores results with 600s TTL

### Potential Issues & Mitigation:

1. **Keyboard Module Permissions**
   - Issue: May fail if not running as administrator
   - Check: Python output should show "[mimetic] Installing keyboard module..." if missing
   - Solution: EXE may need administrator privileges in Windows

2. **Timeout Margin**: Only 1 second buffer (7s max vs 6s frontend wait)
   - If polling cycle is delayed by network: could timeout
   - Solution: Could reduce COMMAND_CHECK_INTERVAL_SECONDS to 2s (requires worker update)

3. **ESC Key Detection**
   - Issue: keyboard.on_press() must intercept ESC before OS
   - May fail if another app has keyboard focus
   - Solution: User should ensure terminal web has focus

### How to Debug:

1. Check Python logs (look for "[mimetic]" prefix):
   - "[mimetic] Installing keyboard module..." = first run setup
   - "[mimetic] Keyboard module installed" = ready to capture

2. Check Cloudflare Worker logs:
   - Monitor command queue and result storage
   - Verify requestId is properly passed through

3. Check Frontend Console:
   - Open browser DevTools (F12)
   - Watch Network tab for API calls
   - Check Console for JavaScript errors

## If Tests Fail:

### If ALL tests timeout:
- Check Python is running (look for "[command] Checkeando comandos..." logs)
- Check internet connection to Cloudflare Worker
- Verify keyboard module installed: run `python -m pip show keyboard`

### If Tests 3-4 fail but Test 1 passes:
- keyboard module may have permission issues
- Try running EXE as Administrator
- Check Windows Defender isn't blocking keyboard module

### If only Test 2 fails:
- 2-second timeout may be too short for your system
- Monitor time delta between command sent and result returned
- May need to increase timeout if network is slow

## Code Quality Notes for Future Implementation:

✓ Proper error handling with try/except
✓ Keyboard unhook on all exit paths (normal, exception, timeout)
✓ RequestId passed through entire stack for command isolation
✓ Console output with flush=True for real-time debugging

⚠ Single-threaded keyboard capture (blocks for 2 seconds)
⚠ No progress indicator while waiting for ESC
⚠ Limited to 2 seconds hard cap (not user-configurable)
