import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "gateway_install",
    Path(__file__).resolve().parents[1] / "codex-backend/gateway-patches/install.py",
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def log(request, message):
    return f"[2026-10-31 08:00:00] [{request}] [info ] {message}"


class GatewayDrain(unittest.TestCase):
    def test_unfinished_request_from_exited_process_does_not_block_install(self):
        lines = [
            log("1234abcd", "session-affinity: cache hit"),
            "CLIProxyAPI Version: test, Commit: test, BuiltAt: unknown",
        ]
        self.assertEqual(installer.pending_requests(lines), 0)

    def test_current_process_requests_still_block_install(self):
        for event in ("cache hit", "cache miss, new binding", "LCP cache miss, new binding"):
            with self.subTest(event=event):
                lines = [
                    "CLIProxyAPI Version: test, Commit: test, BuiltAt: unknown",
                    log("1234abcd", f"session-affinity: {event}"),
                ]
                self.assertEqual(installer.pending_requests(lines), 1)
                lines.append(log("1234abcd", '[gin_logger.go:113] 200 | POST "/v1/messages"'))
                self.assertEqual(installer.pending_requests(lines), 0)

    def test_completed_other_request_does_not_release_active_request(self):
        lines = [
            log("1234abcd", "session-affinity: cache hit"),
            log("deadbeef", '[gin_logger.go:113] 200 | GET "/v1/models"'),
        ]
        self.assertEqual(installer.pending_requests(lines), 1)


if __name__ == "__main__":
    unittest.main()
