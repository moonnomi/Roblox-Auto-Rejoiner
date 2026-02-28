import psutil
import subprocess
import time
import json
import socket
import threading
import os
import sys
import requests
import re
from datetime import datetime, timedelta

# Requirements for screenshotting (will be added to README)
try:
    import win32gui
    import win32ui
    import win32con
    from PIL import ImageGrab
    HAS_SCREENSHOT_LIBS = True
except ImportError:
    HAS_SCREENSHOT_LIBS = False

# ─── CONFIG ───────────────────────────────────────────────────────────────────
PLACE_ID = "PUT_PLACE_ID_HERE"
REJOIN_DELAY = 5          # seconds to wait before rejoining after crash
CHECK_INTERVAL = 3        # seconds between process checks
SOCKET_HOST = "127.0.0.1"
SOCKET_PORT = 45678
# ──────────────────────────────────────────────────────────────────────────────
def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

def get_game_name(place_id):
    """Fetches the game name using a multi-step fallback process."""
    if place_id == "PUT_PLACE_ID_HERE":
        return "Not Set"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        # Step 1: Get the Universe ID from the Place ID (usually public)
        universe_url = f"https://apis.roblox.com/universes/v1/places/{place_id}/universe"
        u_resp = requests.get(universe_url, headers=headers, timeout=5)
        
        if u_resp.status_code == 200:
            universe_id = u_resp.json().get("universeId")
            
            # Step 2: Get Game Details using the Universe ID
            details_url = f"https://games.roblox.com/v1/games?universeIds={universe_id}"
            d_resp = requests.get(details_url, headers=headers, timeout=5)
            
            if d_resp.status_code == 200:
                data = d_resp.json().get("data", [])
                if data:
                    return data[0].get("name", "Unknown Game")

        # Step 3: Final fallback - Scrape the title from the Roblox webpage
        log("API restricted. Attempting web scraping fallback...")
        web_url = f"https://www.roblox.com/games/{place_id}/"
        w_resp = requests.get(web_url, headers=headers, timeout=5)
        if w_resp.status_code == 200:
            import re
            # Roblox titles are formatted as "Name - Roblox"
            title_match = re.search(r"<title>(.*?) - Roblox</title>", w_resp.text)
            if title_match:
                return title_match.group(1).strip()

    except Exception as e:
        log(f"Error during game name detection: {e}")
    
    return "Unknown Game"

# Auto-fetch game info
GAME_NAME = get_game_name(PLACE_ID)
GAME_URL = f"https://www.roblox.com/games/{PLACE_ID}"

state = {
    "monitoring": True,
    "roblox_running": False,
    "crash_count": 0,
    "freeze_count": 0,
    "last_crash": None,
    "last_freeze": None,
    "last_rejoin": None,
    "monitor_start": datetime.now().isoformat(),
    "place_id": PLACE_ID,
    "game_name": GAME_NAME,
    "game_url": GAME_URL,
    "status_message": "Starting up...",
}
state_lock = threading.Lock()




def is_roblox_running() -> bool:
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] and "RobloxPlayerBeta" in proc.info["name"]:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def is_roblox_frozen() -> bool:
    """Checks if any Roblox process is 'Not Responding' using Windows tasklist."""
    try:
        # Use tasklist to find processes with 'Not Responding' status
        output = subprocess.check_output(
            'tasklist /FI "IMAGENAME eq RobloxPlayerBeta.exe" /FI "STATUS eq NOT RESPONDING"',
            shell=True, stderr=subprocess.STDOUT
        ).decode("utf-8", errors="ignore")
        
        return "RobloxPlayerBeta.exe" in output
    except Exception:
        return False


def kill_roblox():
    """Forcefully terminates all Roblox player processes."""
    log("Force-closing Roblox processes...")
    try:
        subprocess.run('taskkill /F /IM RobloxPlayerBeta.exe /T', shell=True, capture_output=True)
    except Exception as e:
        log(f"Error while killing Roblox: {e}")


def take_screenshot():
    """Captures a screenshot of the Roblox window specifically."""
    if not HAS_SCREENSHOT_LIBS:
        return "ERR: PIL or pywin32 not installed"

    hwnd = win32gui.FindWindow(None, "Roblox")
    if not hwnd:
        # Try finding by class name if window title is different
        hwnd = win32gui.FindWindow("Win32Window0", "Roblox")
    
    if not hwnd:
        return "ERR: Roblox window not found"

    try:
        # IMPORTANT: This tells Windows that our script is "DPI Aware".
        # Without this, GetWindowRect returns logical coordinates that 
        # don't match the physical pixels ImageGrab expects on high-res screens.
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
        
        # Get the window coordinates
        rect = win32gui.GetWindowRect(hwnd)
        
        # Check if the window is minimized (logical coordinates go to -32000)
        if rect[0] <= -32000:
            return "ERR: Roblox window is minimized (cannot capture)"
            
        # Capture only the specific bounding box of the Roblox window
        screenshot = ImageGrab.grab(bbox=rect, all_screens=True)
        
        filepath = os.path.join(os.getcwd(), "roblox_current.png")
        screenshot.save(filepath)
        return filepath
    except Exception as e:
        return f"ERR: {str(e)}"


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
        is_frozen = is_roblox_frozen() if now_running else False

        with state_lock:
            state["roblox_running"] = now_running

        if now_running and is_frozen:
            freeze_time = datetime.now().isoformat()
            log(f"❄️ Roblox is Not Responding! Detected freeze at {freeze_time}")
            with state_lock:
                state["freeze_count"] += 1
                state["last_freeze"] = freeze_time
                state["status_message"] = "Frozen! Force-closing..."
            
            kill_roblox()
            time.sleep(2)
            now_running = False # Trigger the rejoin logic below

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

        elif data == "GET_SCREENSHOT":
            res = take_screenshot()
            conn.sendall(res.encode("utf-8"))

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
