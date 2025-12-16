#!/usr/bin/env python3
# Copyright © 2025 Sierra Labs LLC
# SPDX-License-Identifier: AGPL-3.0-only
# License-Filename: LICENSE

"""
Real Integration Tests for Cursor Telemetry

These tests invoke the Cursor Agent CLI with --force flag and verify
telemetry events are captured in the database.

Usage:
    python testing_integration/test_cursor_telemetry.py
"""

import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from testing_integration.test_harness_utils import (
    BaseTelemetryTest, TelemetryServerManager, save_test_results
)


class CursorTelemetryTest(BaseTelemetryTest):
    """Test harness for Cursor telemetry integration tests."""

    TABLE = "cursor_raw_traces"

    def __init__(self):
        super().__init__()
        self.test_marker = f"TEST_{uuid.uuid4().hex[:8]}"
        self.server_manager = TelemetryServerManager()

    def check_cli(self) -> bool:
        """Check if Cursor Agent CLI is available."""
        try:
            result = subprocess.run(
                ["cursor-agent", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                print(f"  Cursor Agent version: {result.stdout.strip()}")
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return False

    def get_sqlite_count(self) -> int:
        """Count events in SQLite since test started."""
        return self.get_event_count(self.TABLE)

    def get_recent(self, limit: int = 5) -> list:
        """Get recent events filtered by test start time."""
        import sqlite3
        if not self.telemetry_db.exists():
            return []
        try:
            with sqlite3.connect(str(self.telemetry_db)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(f"""
                    SELECT * FROM {self.TABLE}
                    WHERE timestamp >= ?
                    ORDER BY timestamp DESC LIMIT ?
                """, (self.start_time.isoformat(), limit))
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    def run_cursor(self, prompt: str, timeout: int = 60) -> tuple[bool, str]:
        """Run Cursor CLI with a prompt and return (success, output).

        Uses: cursor-agent -p -f "prompt"
        - -p/--print: non-interactive mode for scripts
        - -f/--force: force allow commands unless explicitly denied
        """
        try:
            full_cmd = ["cursor-agent", "-p", "-f", prompt]
            print(f"  Command: {' '.join(full_cmd)}")

            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "NO_COLOR": "1"}
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, "Timeout"
        except FileNotFoundError:
            return False, "cursor-agent not found"
        except Exception as e:
            return False, str(e)


def run_all_tests():
    """Run all Cursor integration tests."""
    print("=" * 70)
    print("Cursor Telemetry - Integration Tests (REAL CLI)")
    print("=" * 70)
    print(f"Started: {datetime.now(timezone.utc).isoformat()}\n")

    h = CursorTelemetryTest()
    we_started_server = False

    try:
        # Prerequisites
        print("[TEST] Redis...")
        if h.check_redis():
            h.record("redis", True, "Running")
        else:
            h.record("redis", False, "Not running - start with: redis-server")
            return _finish(h)

        print("\n[TEST] Cursor CLI...")
        if h.check_cli():
            h.record("cli", True, "Installed")
        else:
            h.record("cli", False, "Not found - install cursor-agent CLI", skip=True)
            return _finish(h)

        # Server
        print("\n[TEST] Server...")
        if h.server_manager.is_running():
            h.record("server", True, "Already running")
        else:
            we_started_server = h.server_manager.start(timeout=30)
            h.record("server", we_started_server, "Started" if we_started_server else "Failed")
            if not we_started_server:
                return _finish(h)

        # Database
        print("\n[TEST] Database...")
        time.sleep(2)
        if h.telemetry_db.exists():
            h.record("database", True, f"Exists at {h.telemetry_db}")
        else:
            h.record("database", False, "Not found", skip=True)
            return _finish(h)

        # Event generation - REAL CLI INVOCATION
        print("\n[TEST] Event generation (invoking real Cursor CLI)...")
        initial = h.get_sqlite_count()
        print(f"  Running: cursor-agent -p --force 'echo test marker: {h.test_marker}'")

        success, output = h.run_cursor(f"echo 'test marker: {h.test_marker}'")
        if not success:
            h.record("events", False, f"Cursor CLI failed: {output[:100]}", skip=True)
        else:
            time.sleep(5)
            new_count = h.get_sqlite_count() - initial
            if new_count > 0:
                h.record("events", True, f"Generated {new_count} events")
            else:
                h.record("events", False, "No events captured - check telemetry hooks")

        # Event structure
        print("\n[TEST] Event structure...")
        events = h.get_recent(limit=3)
        if events:
            required = ["event_id", "event_type", "timestamp"]
            missing = [f for f in required if f not in events[0] or events[0][f] is None]
            if missing:
                h.record("structure", False, f"Missing: {missing}")
            else:
                h.record("structure", True, f"Fields: {len(events[0])} columns")
        else:
            h.record("structure", False, "No events to validate", skip=True)

    finally:
        if we_started_server:
            print("\n[CLEANUP] Stopping server...")
            h.server_manager.stop()

    return _finish(h)


def _finish(harness: CursorTelemetryTest) -> int:
    """Print summary, save results, return exit code."""
    harness.print_summary()
    save_test_results(harness.results, "cursor_telemetry_integration", "cursor_integration")
    return 1 if harness.results['failed'] else 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
