import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_config_dir = tempfile.TemporaryDirectory()
os.environ["ROBLOX_REJOINER_CONFIG"] = os.path.join(_config_dir.name, "config.json")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config_manager  # noqa: E402
import roblox_monitor  # noqa: E402


class RobloxLogMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_existing_disconnect_is_not_replayed(self):
        log_path = self.log_dir / "Player-old.log"
        log_path.write_text("Game disconnected\n", encoding="utf-8")
        monitor = roblox_monitor.RobloxLogMonitor(str(self.log_dir))

        self.assertIsNone(monitor.check_for_disconnect())
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write("ordinary log message\n")
        self.assertIsNone(monitor.check_for_disconnect())

        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write("Connection lost; error code: 277\n")
        self.assertEqual(monitor.check_for_disconnect(), "connection lost")

    def test_new_player_log_is_read_from_the_start(self):
        old_log = self.log_dir / "Player-old.log"
        old_log.write_text("running\n", encoding="utf-8")
        monitor = roblox_monitor.RobloxLogMonitor(str(self.log_dir))

        new_log = self.log_dir / "Player-new.log"
        new_log.write_text("Disconnected for being idle\n", encoding="utf-8")
        old_time = old_log.stat().st_mtime
        os.utime(new_log, (old_time + 2, old_time + 2))

        self.assertEqual(
            monitor.check_for_disconnect(),
            "disconnected for being idle",
        )

    def test_incomplete_line_is_completed_once(self):
        log_path = self.log_dir / "Player-current.log"
        log_path.write_text("running\n", encoding="utf-8")
        monitor = roblox_monitor.RobloxLogMonitor(str(self.log_dir))

        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write("error code")
        self.assertIsNone(monitor.check_for_disconnect())

        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(": 279\n")
        self.assertEqual(monitor.check_for_disconnect(), "error code: 279")
        self.assertIsNone(monitor.check_for_disconnect())


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.original_place_id = roblox_monitor.PLACE_ID
        self.original_active = roblox_monitor.MONITOR_ACTIVE
        roblox_monitor.PLACE_ID = "123456"
        roblox_monitor.MONITOR_ACTIVE = True
        with roblox_monitor.state_lock:
            roblox_monitor.state["monitoring"] = True

    def tearDown(self):
        roblox_monitor.PLACE_ID = self.original_place_id
        roblox_monitor.MONITOR_ACTIVE = self.original_active

    @mock.patch.object(roblox_monitor, "_wait_for_roblox", return_value=True)
    @mock.patch.object(roblox_monitor, "launch_roblox", side_effect=[False, True])
    @mock.patch.object(roblox_monitor, "wait_for_internet", return_value=True)
    @mock.patch.object(roblox_monitor, "_controlled_delay", return_value=True)
    def test_failed_launch_is_retried(
        self,
        _delay,
        _internet,
        launch,
        wait_for_process,
    ):
        self.assertTrue(roblox_monitor.recover_roblox("Disconnected"))
        self.assertEqual(launch.call_count, 2)
        wait_for_process.assert_called_once()

    def test_socket_command_requires_matching_auth(self):
        with mock.patch.object(roblox_monitor, "SOCKET_AUTH", "secret"):
            self.assertEqual(roblox_monitor._decode_command("secret:GET_STATE"), "GET_STATE")
            self.assertIsNone(roblox_monitor._decode_command("wrong:GET_STATE"))
            self.assertIsNone(roblox_monitor._decode_command("GET_STATE"))


class ConfigTests(unittest.TestCase):
    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with mock.patch.dict(os.environ, {"ROBLOX_REJOINER_CONFIG": path}):
                settings = config_manager.DEFAULT_CONFIG.copy()
                settings["PLACE_ID"] = "987654"
                settings["SOCKET_AUTH"] = "test-auth"
                self.assertTrue(config_manager.save_config(settings))
                self.assertEqual(config_manager.load_config()["PLACE_ID"], "987654")
                self.assertFalse(os.path.exists(f"{path}.tmp"))


if __name__ == "__main__":
    unittest.main()
