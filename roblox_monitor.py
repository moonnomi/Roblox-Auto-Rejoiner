
import psutil
import subprocess
import time
import json
import socket
import threading
import os
import sys
from datetime import datetime, timedelta

# ─── CONFIG ───────────────────────────────────────────────────────────────────
PLACE_ID = "PUT_PLACE_ID_HERE"
GAME_NAME = "GAME_NAME_HERE" # Optional, just for display
GAME_URL = f"https://www.roblox.com/games/{PLACE_ID}"
REJOIN_DELAY = 5          # seconds to wait before rejoining after crash
CHECK_INTERVAL = 3        # seconds between process checks
SOCKET_HOST = "127.0.0.1"
SOCKET_PORT = 45678
# ──────────────────────────────────────────────────────────────────────────────

state = {
    "monitoring": True,
    "roblox_running": False,
    "crash_count": 0,
    "last_crash": None,
    "last_rejoin": None,
    "monitor_start": datetime.now().isoformat(),
    "place_id": PLACE_ID,
    "game_name": GAME_NAME,
    "game_url": GAME_URL,
    "status_message": "Starting up...",
}
state_lock = threading.Lock()


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def is_roblox_running() -> bool:
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] and "RobloxPlayerBeta" in proc.info["name"]:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def launch_roblox():
    log(f"Launching Roblox → Place ID {PLACE_ID}")
    uri = f"roblox://placeId={PLACE_ID}"
    try:
        os.startfile(uri)          # Uses Windows shell to open the Roblox URI
    except Exception as e:
        log(f"Failed to launch via URI: {e}")
        # Fallback: try the Roblox player directly
        try:
            roblox_path = os.path.expandvars(
                r"%LOCALAPPDATA%\Roblox\Versions"
            )
            for version in sorted(os.listdir(roblox_path), reverse=True):
                exe = os.path.join(roblox_path, version, "RobloxPlayerBeta.exe")
                if os.path.exists(exe):
                    subprocess.Popen([exe, f"--app", "--launchtime=0",
                                      f"roblox://placeId={PLACE_ID}"])
                    break
        except Exception as e2:
            log(f"Fallback launch also failed: {e2}")


def monitor_loop():
    global state
    log("Monitor started. Watching for Roblox process...")


    time.sleep(2)

    with state_lock:
        state["status_message"] = "Monitoring"

    was_running = is_roblox_running()
    if was_running:
        log("Roblox is already open.")
        with state_lock:
            state["roblox_running"] = True

    while True:
        with state_lock:
            if not state["monitoring"]:
                time.sleep(1)
                continue

        now_running = is_roblox_running()

        with state_lock:
            state["roblox_running"] = now_running

        if was_running and not now_running:
            # Roblox just closed / crashed
            crash_time = datetime.now().isoformat()
            log(f"⚠  Roblox closed! Detected crash at {crash_time}")
            with state_lock:
                state["crash_count"] += 1
                state["last_crash"] = crash_time
                state["status_message"] = f"Crashed! Rejoining in {REJOIN_DELAY}s..."

            log(f"Waiting {REJOIN_DELAY} seconds before rejoining...")
            time.sleep(REJOIN_DELAY)

            launch_roblox()
            rejoin_time = datetime.now().isoformat()
            with state_lock:
                state["last_rejoin"] = rejoin_time
                state["status_message"] = "Rejoined - Monitoring"

            log("Rejoin command sent. Resuming monitoring...")
            # Wait a bit for Roblox to actually start before next check
            time.sleep(8)
            now_running = is_roblox_running()
            with state_lock:
                state["roblox_running"] = now_running

        was_running = now_running
        time.sleep(CHECK_INTERVAL)


# ─── SOCKET SERVER (for Discord bot to query) ─────────────────────────────────

def handle_client(conn, addr):
    try:
        data = conn.recv(1024).decode("utf-8").strip()
        if data == "GET_STATE":
            with state_lock:
                payload = json.dumps(state)
            conn.sendall(payload.encode("utf-8"))

        elif data == "PAUSE":
            with state_lock:
                state["monitoring"] = False
                state["status_message"] = "Paused"
            conn.sendall(b"OK: Monitoring paused")

        elif data == "RESUME":
            with state_lock:
                state["monitoring"] = True
                state["status_message"] = "Monitoring"
            conn.sendall(b"OK: Monitoring resumed")

        elif data == "REJOIN_NOW":
            launch_roblox()
            with state_lock:
                state["last_rejoin"] = datetime.now().isoformat()
            conn.sendall(b"OK: Rejoin command sent")

        else:
            conn.sendall(b"ERR: Unknown command")
    except Exception as e:
        log(f"Socket error: {e}")
    finally:
        conn.close()


def socket_server():
    log(f"Socket server listening on {SOCKET_HOST}:{SOCKET_PORT}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((SOCKET_HOST, SOCKET_PORT))
        s.listen()
        while True:
            try:
                conn, addr = s.accept()
                threading.Thread(target=handle_client, args=(conn, addr),
                                 daemon=True).start()
            except Exception as e:
                log(f"Server error: {e}")


if __name__ == "__main__":
    print("=" * 50)
    print("  Roblox Auto-Rejoin Monitor")
    print(f"  Game : {GAME_NAME}")
    print(f"  Place: {PLACE_ID}")
    print("=" * 50)

    # Start socket server in background
    threading.Thread(target=socket_server, daemon=True).start()

    # Run monitor (blocking)
    try:
        monitor_loop()
    except KeyboardInterrupt:
        log("Stopped by user.")
