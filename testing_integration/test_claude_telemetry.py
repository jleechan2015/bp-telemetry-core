#!/usr/bin/env python3
# Copyright © 2025 Sierra Labs LLC
# SPDX-License-Identifier: AGPL-3.0-only
# License-Filename: LICENSE

"""
Real Integration Tests for Claude Code Telemetry

Invokes Claude Code with --dangerously-skip-permissions and verifies
telemetry events are captured in the database.

Usage:
    python testing_integration/test_claude_telemetry.py
"""

import os
import sqlite3
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


class ClaudeTelemetryTest(BaseTelemetryTest):
    """Test harness for Claude Code telemetry integration tests."""

    TABLE = "claude_raw_traces"

    def __init__(self):
        super().__init__()
        self.test_marker = f"TEST_{uuid.uuid4().hex[:8]}"
        self.server_manager = TelemetryServerManager()

    def check_cli(self) -> bool:
        """Check if Claude CLI is available."""
        try:
            result = subprocess.run(
                ["claude", "--version"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                print(f"  Claude version: {result.stdout.strip()}")
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return False

    def get_redis_count(self) -> int:
        """Count events in Redis since test started."""
        try:
            import redis
            r = redis.Redis(host='localhost', port=6379, decode_responses=True)
            events = r.xrange('telemetry:message_queue', '-', '+')
            return sum(1 for _, d in events if d.get('timestamp', '') >= self.start_time.isoformat())
        except Exception:
            return 0

    def get_sqlite_count(self) -> int:
        """Count events in SQLite since test started."""
        return self.get_event_count(self.TABLE)

    def get_recent(self, limit: int = 5) -> list:
        """Get recent events filtered by test start time."""
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

    def run_claude(self, prompt: str, timeout: int = 30) -> tuple[bool, str]:
        """Run Claude command and return (success, output)."""
        try:
            result = subprocess.run(
                ["claude", "-p", prompt, "--dangerously-skip-permissions"],
                capture_output=True, text=True, timeout=timeout,
                env={**os.environ, "NO_COLOR": "1"}
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, "Timeout"
        except Exception as e:
            return False, str(e)


def run_all_tests():
    """Run all Claude integration tests."""
    print("=" * 70)
    print("Claude Code Telemetry - Integration Tests")
    print("=" * 70)
    print(f"Started: {datetime.now(timezone.utc).isoformat()}\n")

    h = ClaudeTelemetryTest()
    we_started_server = False

    try:
        # Prerequisites
        print("[TEST] Redis...")
        if h.check_redis():
            h.record("redis", True, "Running")
        else:
            h.record("redis", False, "Not running - start with: redis-server")
            return _finish(h)

        print("\n[TEST] Claude CLI...")
        if h.check_cli():
            h.record("cli", True, "Installed")
        else:
            h.record("cli", False, "Not found - npm install -g @anthropic/claude-code")
            return _finish(h)

        # Server
        print("\n[TEST] Server...")
        if h.server_manager.is_running():
            h.record("server", True, "Already running")
        else:
            we_started_server = h.server_manager.start(timeout=30)
            h.record("server", we_started_server, "Started" if we_started_server else "Failed to start")
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

        # Event generation
        print("\n[TEST] Event generation...")
        initial = h.get_sqlite_count()
        success, output = h.run_claude(f"echo 'marker: {h.test_marker}'")
        if not success:
            h.record("events", False, f"Claude failed: {output[:100]}")
        else:
            time.sleep(5)
            new_count = h.get_sqlite_count() - initial
            if new_count > 0:
                h.record("events", True, f"Generated {new_count} events")
            else:
                redis_count = h.get_redis_count()
                if redis_count > 0:
                    h.record("events", False, f"In Redis ({redis_count}) but not SQLite")
                else:
                    h.record("events", False, "No events - check hooks installed")

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


def _finish(harness: ClaudeTelemetryTest) -> int:
    """Print summary, save results, return exit code."""
    harness.print_summary()
    save_test_results(harness.results, "claude_telemetry_integration", "claude_integration")
    return 1 if harness.results['failed'] else 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
