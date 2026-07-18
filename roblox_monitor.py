import json
import os
import secrets
import socket
import subprocess
import threading
import time
from datetime import datetime

import psutil
import requests

from config_manager import get_config_path, load_config

try:
    import win32gui
    from PIL import ImageGrab

    HAS_SCREENSHOT_LIBS = True
except ImportError:
    HAS_SCREENSHOT_LIBS = False


DEFAULT_PLACE_ID = "PUT_PLACE_ID_HERE"
INTERNET_ENDPOINTS = (
    ("www.roblox.com", 443),
    ("apis.roblox.com", 443),
)
DISCONNECT_MARKERS = (
    "connection lost",
    "lost connection",
    "error code 277",
    "error code: 277",
    "error code 279",
    "error code: 279",
    "error code 268",
    "error code: 268",
    "error code 273",
    "error code: 273",
    "disconnected for being idle",
    "idled for 20 minutes",
    "you were kicked from this experience",
    "disconnect reason received",
    "game disconnected",
)

PLACE_ID = DEFAULT_PLACE_ID
REJOIN_DELAY = 5.0
CHECK_INTERVAL = 3.0
SOCKET_HOST = "127.0.0.1"
SOCKET_PORT = 45678
SOCKET_AUTH = ""
GAME_NAME = "Not Set"
GAME_URL = "https://www.roblox.com/games/"

MONITOR_ACTIVE = True
SOCKET_ACTIVE = True


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")


def _positive_number(value, fallback: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(value))
    except (TypeError, ValueError):
        return fallback


def _valid_place_id(place_id: str) -> bool:
    return bool(place_id and place_id != DEFAULT_PLACE_ID and place_id.isdigit())


def get_game_name(place_id: str) -> str:
    """Return the Roblox experience name, with the place ID as a safe fallback."""
    if not _valid_place_id(place_id):
        return "Not Set"

    headers = {"User-Agent": "Roblox-Auto-Rejoiner/1.0"}
    try:
        response = requests.get(
            f"https://apis.roblox.com/universes/v1/places/{place_id}/universe",
            headers=headers,
            timeout=5,
        )
        response.raise_for_status()
        universe_id = response.json().get("universeId")
        if universe_id:
            response = requests.get(
                f"https://games.roblox.com/v1/games?universeIds={universe_id}",
                headers=headers,
                timeout=5,
            )
            response.raise_for_status()
            games = response.json().get("data", [])
            if games and games[0].get("name"):
                return games[0]["name"]
    except (requests.RequestException, ValueError, TypeError) as exc:
        log(f"Could not fetch the game name: {exc}")

    return f"Place {place_id}"


state = {
    "monitoring": True,
    "roblox_running": False,
    "crash_count": 0,
    "freeze_count": 0,
    "disconnect_count": 0,
    "last_crash": None,
    "last_freeze": None,
    "last_disconnect": None,
    "last_disconnect_reason": None,
    "last_rejoin": None,
    "monitor_start": datetime.now().isoformat(),
    "place_id": PLACE_ID,
    "game_name": GAME_NAME,
    "game_url": GAME_URL,
    "status_message": "Starting up...",
}
state_lock = threading.Lock()
_recovery_lock = threading.Lock()
_manual_rejoin = threading.Event()


def reload_monitor_config(fetch_game_name: bool = False) -> None:
    """Reload settings without requiring either service to be re-imported."""
    global PLACE_ID, REJOIN_DELAY, CHECK_INTERVAL
    global SOCKET_HOST, SOCKET_PORT, SOCKET_AUTH, GAME_NAME, GAME_URL

    config = load_config()
    PLACE_ID = str(config.get("PLACE_ID", DEFAULT_PLACE_ID)).strip()
    REJOIN_DELAY = _positive_number(config.get("REJOIN_DELAY"), 5.0)
    CHECK_INTERVAL = _positive_number(config.get("CHECK_INTERVAL"), 3.0, 0.5)
    SOCKET_HOST = str(config.get("SOCKET_HOST", "127.0.0.1"))
    try:
        SOCKET_PORT = int(config.get("SOCKET_PORT", 45678))
    except (TypeError, ValueError):
        SOCKET_PORT = 45678
    SOCKET_AUTH = str(config.get("SOCKET_AUTH", ""))
    GAME_NAME = get_game_name(PLACE_ID) if fetch_game_name else (
        "Not Set" if not _valid_place_id(PLACE_ID) else f"Place {PLACE_ID}"
    )
    GAME_URL = f"https://www.roblox.com/games/{PLACE_ID}"

    with state_lock:
        state.update(
            place_id=PLACE_ID,
            game_name=GAME_NAME,
            game_url=GAME_URL,
        )


reload_monitor_config()


class RobloxLogMonitor:
    """Tails only new Roblox Player log data and reports disconnect markers."""

    def __init__(self, log_dir: str | None = None):
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        self.log_dir = log_dir or os.path.join(local_app_data, "Roblox", "logs")
        self.current_log: str | None = None
        self.position = 0
        self.pending_line = ""
        self._select_latest(start_at_end=True)

    def get_latest_log(self) -> str | None:
        try:
            entries = [
                os.path.join(self.log_dir, name)
                for name in os.listdir(self.log_dir)
                if name.lower().endswith(".log")
            ]
            player_logs = [path for path in entries if "player" in os.path.basename(path).lower()]
            candidates = player_logs or entries
            return max(candidates, key=os.path.getmtime) if candidates else None
        except (FileNotFoundError, OSError):
            return None

    def _select_latest(self, start_at_end: bool) -> bool:
        latest = self.get_latest_log()
        if not latest:
            return False
        self.current_log = latest
        self.pending_line = ""
        try:
            self.position = os.path.getsize(latest) if start_at_end else 0
        except OSError:
            self.position = 0
        return True

    def check_for_disconnect(self) -> str | None:
        latest = self.get_latest_log()
        if not latest:
            return None
        if latest != self.current_log:
            self.current_log = latest
            self.position = 0
            self.pending_line = ""

        try:
            size = os.path.getsize(latest)
            if size < self.position:
                self.position = 0
                self.pending_line = ""

            with open(latest, "rb") as log_file:
                log_file.seek(self.position)
                new_data = log_file.read(512 * 1024)
                self.position = log_file.tell()
        except OSError:
            return None

        if not new_data:
            return None

        text = self.pending_line + new_data.decode("utf-8", errors="ignore")
        lines = text.splitlines(keepends=True)
        if lines and not lines[-1].endswith(("\n", "\r")):
            self.pending_line = lines.pop()
        else:
            self.pending_line = ""

        for line in lines:
            lowered = line.lower()
            for marker in DISCONNECT_MARKERS:
                if marker in lowered:
                    return marker
        return None


def is_roblox_running() -> bool:
    for process in psutil.process_iter(["name"]):
        try:
            name = process.info.get("name") or ""
            if name.lower() == "robloxplayerbeta.exe":
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def is_roblox_frozen() -> bool:
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            [
                "tasklist",
                "/FI",
                "IMAGENAME eq RobloxPlayerBeta.exe",
                "/FI",
                "STATUS eq NOT RESPONDING",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return "RobloxPlayerBeta.exe" in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def kill_roblox() -> None:
    log("Force-closing Roblox processes...")
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "RobloxPlayerBeta.exe", "/T"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"Could not close Roblox: {exc}")


def take_screenshot() -> str:
    if not HAS_SCREENSHOT_LIBS:
        return "ERR: Pillow or pywin32 is not installed"

    window = win32gui.FindWindow(None, "Roblox") or win32gui.FindWindow("Win32Window0", "Roblox")
    if not window:
        return "ERR: Roblox window not found"

    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
        rect = win32gui.GetWindowRect(window)
        if rect[0] <= -32000:
            return "ERR: Roblox window is minimized"
        screenshot = ImageGrab.grab(bbox=rect, all_screens=True)
        path = os.path.join(os.path.dirname(get_config_path()), "roblox_current.png")
        screenshot.save(path)
        return path
    except Exception as exc:
        return f"ERR: {exc}"


def launch_roblox() -> bool:
    if not _valid_place_id(PLACE_ID):
        log("Place ID is not configured; Roblox was not launched.")
        return False

    uri = f"roblox://placeId={PLACE_ID}"
    log(f"Launching Roblox -> Place ID {PLACE_ID}")
    try:
        os.startfile(uri)
        return True
    except (AttributeError, OSError) as exc:
        log(f"Roblox URI launch failed: {exc}")

    versions_dir = os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions")
    try:
        executables = []
        for version in os.scandir(versions_dir):
            executable = os.path.join(version.path, "RobloxPlayerBeta.exe")
            if version.is_dir() and os.path.isfile(executable):
                executables.append(executable)
        executable = max(executables, key=os.path.getmtime)
        subprocess.Popen([executable, "--app", "--launchtime=0", uri])
        return True
    except (OSError, ValueError) as exc:
        log(f"Direct Roblox launch failed: {exc}")
        return False


def internet_available(timeout: float = 3.0) -> bool:
    """Check that a Roblox endpoint is reachable, including working DNS."""
    for endpoint in INTERNET_ENDPOINTS:
        try:
            with socket.create_connection(endpoint, timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _monitoring_enabled() -> bool:
    with state_lock:
        return bool(state["monitoring"])


def _controlled_delay(seconds: float, pause_aware: bool = True) -> bool:
    deadline = time.monotonic() + seconds
    while MONITOR_ACTIVE and time.monotonic() < deadline:
        if pause_aware and not _monitoring_enabled():
            time.sleep(0.25)
            deadline += 0.25
            continue
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    return MONITOR_ACTIVE


def wait_for_internet(pause_aware: bool = True) -> bool:
    announced = False
    while MONITOR_ACTIVE:
        if pause_aware and not _monitoring_enabled():
            time.sleep(0.25)
            continue
        if internet_available():
            if announced:
                log("Internet connection restored.")
            return True
        if not announced:
            log("No Roblox network connection. Waiting before relaunching...")
            announced = True
        with state_lock:
            state["status_message"] = "Waiting for internet..."
        if not _controlled_delay(5, pause_aware=pause_aware):
            break
    return False


def _wait_for_roblox(timeout: float = 30.0, pause_aware: bool = True) -> bool:
    deadline = time.monotonic() + timeout
    while MONITOR_ACTIVE and time.monotonic() < deadline:
        if is_roblox_running():
            return True
        if not _controlled_delay(1, pause_aware=pause_aware):
            return False
    return is_roblox_running()


def recover_roblox(reason: str, pause_aware: bool = True) -> bool:
    """Serialize recovery and keep retrying until Roblox starts or monitoring stops."""
    if not _recovery_lock.acquire(blocking=False):
        return is_roblox_running()
    try:
        with state_lock:
            state["status_message"] = f"{reason}; rejoining in {REJOIN_DELAY:g}s..."
        log(f"Waiting {REJOIN_DELAY:g} seconds before rejoining...")
        if not _controlled_delay(REJOIN_DELAY, pause_aware=pause_aware):
            return False

        attempt = 0
        while MONITOR_ACTIVE:
            if pause_aware and not _monitoring_enabled():
                time.sleep(0.25)
                continue
            if not _valid_place_id(PLACE_ID):
                with state_lock:
                    state["status_message"] = "Invalid or missing Place ID"
                return False
            if not wait_for_internet(pause_aware=pause_aware):
                return False

            attempt += 1
            with state_lock:
                state["status_message"] = f"Launching Roblox (attempt {attempt})..."
            if launch_roblox():
                with state_lock:
                    state["last_rejoin"] = datetime.now().isoformat()
                if _wait_for_roblox(pause_aware=pause_aware):
                    with state_lock:
                        state["roblox_running"] = True
                        state["status_message"] = (
                            "Rejoined - Monitoring"
                            if state["monitoring"]
                            else "Rejoined - Paused"
                        )
                    log("Roblox started. Resuming monitoring.")
                    return True

            with state_lock:
                state["status_message"] = "Roblox did not start; retrying..."
            log("Roblox did not start. Retrying in 10 seconds...")
            if not _controlled_delay(10, pause_aware=pause_aware):
                return False
        return False
    finally:
        _recovery_lock.release()


def _manual_rejoin_worker() -> None:
    try:
        kill_roblox()
        _controlled_delay(2, pause_aware=False)
        recover_roblox("Manual rejoin", pause_aware=False)
    finally:
        _manual_rejoin.clear()


def request_manual_rejoin() -> bool:
    if _manual_rejoin.is_set() or _recovery_lock.locked():
        return False
    _manual_rejoin.set()
    threading.Thread(target=_manual_rejoin_worker, daemon=True).start()
    return True


def monitor_loop() -> None:
    global MONITOR_ACTIVE
    MONITOR_ACTIVE = True
    reload_monitor_config(fetch_game_name=True)
    log_monitor = RobloxLogMonitor()
    log("Monitor started. Watching for Roblox events...")

    with state_lock:
        state["monitor_start"] = datetime.now().isoformat()
        state["status_message"] = "Monitoring"

    was_running = is_roblox_running()
    with state_lock:
        state["roblox_running"] = was_running
    if was_running:
        log("Roblox is already open.")

    while MONITOR_ACTIVE:
        if not _monitoring_enabled():
            with state_lock:
                state["status_message"] = "Paused"
            time.sleep(0.25)
            continue

        now_running = is_roblox_running()
        with state_lock:
            state["roblox_running"] = now_running
            if state["status_message"] == "Paused":
                state["status_message"] = "Monitoring"

        if now_running and is_roblox_frozen():
            event_time = datetime.now().isoformat()
            log(f"Roblox is not responding (detected {event_time}).")
            with state_lock:
                state["freeze_count"] += 1
                state["last_freeze"] = event_time
                state["status_message"] = "Frozen; force-closing..."
            kill_roblox()
            _controlled_delay(2)
            was_running = recover_roblox("Frozen")
            continue

        disconnect_reason = log_monitor.check_for_disconnect() if now_running else None
        if now_running and disconnect_reason:
            event_time = datetime.now().isoformat()
            log(f"Roblox disconnect detected ({disconnect_reason}) at {event_time}.")
            with state_lock:
                state["disconnect_count"] += 1
                state["last_disconnect"] = event_time
                state["last_disconnect_reason"] = disconnect_reason
                state["status_message"] = "Disconnected; force-closing..."
            kill_roblox()
            _controlled_delay(2)
            was_running = recover_roblox("Disconnected")
            continue

        if was_running and not now_running and not _manual_rejoin.is_set():
            event_time = datetime.now().isoformat()
            log(f"Roblox closed unexpectedly at {event_time}.")
            with state_lock:
                state["crash_count"] += 1
                state["last_crash"] = event_time
            was_running = recover_roblox("Crashed")
            continue

        was_running = now_running
        _controlled_delay(CHECK_INTERVAL)

    with state_lock:
        state["roblox_running"] = is_roblox_running()
        state["status_message"] = "Stopped"
    log("Monitor stopped.")


def _decode_command(raw_data: str) -> str | None:
    if not SOCKET_AUTH:
        return raw_data
    token, separator, command = raw_data.partition(":")
    if separator and secrets.compare_digest(token, SOCKET_AUTH):
        return command
    return None


def handle_client(connection: socket.socket, _address) -> None:
    try:
        connection.settimeout(5)
        raw_data = connection.recv(2048).decode("utf-8", errors="replace").strip()
        command = _decode_command(raw_data)
        if command is None:
            connection.sendall(b"ERR: Unauthorized")
        elif command == "GET_STATE":
            with state_lock:
                payload = json.dumps(state)
            connection.sendall(payload.encode("utf-8"))
        elif command == "PAUSE":
            with state_lock:
                state["monitoring"] = False
                state["status_message"] = "Paused"
            connection.sendall(b"OK: Monitoring paused")
        elif command == "RESUME":
            with state_lock:
                state["monitoring"] = True
                state["status_message"] = "Monitoring"
            connection.sendall(b"OK: Monitoring resumed")
        elif command == "REJOIN_NOW":
            if request_manual_rejoin():
                connection.sendall(b"OK: Rejoin started")
            else:
                connection.sendall(b"ERR: A rejoin is already in progress")
        elif command == "GET_SCREENSHOT":
            connection.sendall(take_screenshot().encode("utf-8"))
        else:
            connection.sendall(b"ERR: Unknown command")
    except (OSError, ValueError) as exc:
        log(f"Socket client error: {exc}")
    finally:
        connection.close()


def socket_server() -> None:
    global SOCKET_ACTIVE
    SOCKET_ACTIVE = True
    log(f"Socket server listening on {SOCKET_HOST}:{SOCKET_PORT}")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((SOCKET_HOST, SOCKET_PORT))
            server.listen()
            server.settimeout(1)
            while SOCKET_ACTIVE:
                try:
                    connection, address = server.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=handle_client,
                    args=(connection, address),
                    daemon=True,
                ).start()
    except OSError as exc:
        log(f"Socket server error: {exc}")
    finally:
        SOCKET_ACTIVE = False


def main() -> None:
    print("=" * 50)
    print("  Roblox Auto-Rejoin Monitor")
    print(f"  Place: {PLACE_ID}")
    print("=" * 50)
    threading.Thread(target=socket_server, daemon=True).start()
    try:
        monitor_loop()
    except KeyboardInterrupt:
        globals()["MONITOR_ACTIVE"] = False
        globals()["SOCKET_ACTIVE"] = False
        log("Stopped by user.")


if __name__ == "__main__":
    main()
