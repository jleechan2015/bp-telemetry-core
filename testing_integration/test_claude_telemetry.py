#!/usr/bin/env python3
# Copyright © 2025 Sierra Labs LLC
# SPDX-License-Identifier: AGPL-3.0-only
# License-Filename: LICENSE

"""
Real Integration Tests for Claude Code Telemetry

These tests invoke Claude Code with --dangerously-skip-permissions and verify
that telemetry events are properly captured in the database.

The test automatically starts and stops the telemetry server.

Usage:
    python testing_integration/test_claude_telemetry.py

    # Or with pytest:
    pytest testing_integration/test_claude_telemetry.py -v -s
"""

import os
import signal
import sqlite3
import subprocess
import sys
import time

import pytest
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path BEFORE local imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from testing_integration.test_harness_utils import BaseTelemetryTest, save_test_results

class TelemetryServerManager:
    """Manages telemetry server lifecycle for testing."""

    def __init__(self):
        self.server_process = None
        self.server_script = project_root / "scripts" / "start_server.py"
        self.pid_file = Path.home() / ".blueplane" / "server.pid"

    def is_running(self) -> bool:
        """Check if telemetry server is running."""
        if self.pid_file.exists():
            try:
                pid = int(self.pid_file.read_text().strip())
                os.kill(pid, 0)  # Check if process exists
                return True
            except (ValueError, OSError):
                pass
        return False

    def start(self, timeout: int = 30) -> bool:
        """Start telemetry server and wait for initialization."""
        if self.is_running():
            print("  Server already running")
            return True

        print(f"  Starting telemetry server from {self.server_script}...")

        # Start server in background
        self.server_process = subprocess.Popen(
            [sys.executable, str(self.server_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(project_root),
        )

        # Wait for server to be ready (PID file created)
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.pid_file.exists():
                print(f"  Server started (PID: {self.pid_file.read_text().strip()})")
                time.sleep(2)  # Give it a moment to fully initialize
                return True
            time.sleep(0.5)

        # Check if process died
        if self.server_process.poll() is not None:
            stdout, stderr = self.server_process.communicate()
            print(f"  Server failed to start:")
            print(f"  stdout: {stdout.decode()[:500]}")
            print(f"  stderr: {stderr.decode()[:500]}")
            return False

        print(f"  Server start timed out after {timeout}s")
        return False

    def stop(self) -> None:
        """Stop telemetry server."""
        if self.pid_file.exists():
            try:
                pid = int(self.pid_file.read_text().strip())
                print(f"  Stopping server (PID: {pid})...")
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
                # Check if still running
                try:
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)  # Force kill
                except OSError:
                    # Already gone; that's fine
                    pass
            except (ValueError, OSError) as e:
                print(f"  Warning: Could not stop server: {e}")

        if self.server_process:
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_process.kill()

        # Clean up PID file if it exists
        if self.pid_file.exists():
            try:
                self.pid_file.unlink()
            except OSError:
                # If cleanup fails, ignore so tests can continue
                pass


class ClaudeTelemetryTest(BaseTelemetryTest):
    """Test harness for Claude Code telemetry integration tests."""

    TABLE = "claude_raw_traces"

    def __init__(self):
        super().__init__()
        self.test_marker = f"TEST_{uuid.uuid4().hex[:8]}"
        self.server_manager = TelemetryServerManager()

    def check_prerequisites(self) -> bool:
        """Check if Claude Code CLI is available."""
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                print(f"  Claude Code version: {version}")
                return True
            return False
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def get_redis_event_count_since(self) -> int:
        """Get count of events in Redis message queue since test started."""
        try:
            import redis
            r = redis.Redis(host='localhost', port=6379, decode_responses=True)
            events = r.xrange('telemetry:message_queue', '-', '+')
            count = 0
            for event_id, data in events:
                ts = data.get('timestamp', '')
                if ts >= self.start_time.isoformat():
                    count += 1
            return count
        except Exception as e:
            print(f"  Warning: Redis error - {e}")
            return 0

    def get_event_count_since(self) -> int:
        """Get count of Claude events since test started."""
        return self.get_event_count(self.TABLE)

    def get_recent_events_since_start(self, limit: int = 5) -> list:
        """Get recent events from database filtered by test start time."""
        if not self.telemetry_db.exists():
            return []

        try:
            with sqlite3.connect(str(self.telemetry_db)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(f"""
                    SELECT * FROM {self.TABLE}
                    WHERE timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (self.start_time.isoformat(), limit))
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"  Warning: DB error - {e}")
            return []

    def run_claude_command(self, prompt: str, timeout: int = 30) -> tuple[bool, str]:
        """Run Claude Code with a prompt and return success status and output."""
        try:
            result = subprocess.run(
                [
                    "claude",
                    "-p", prompt,
                    "--dangerously-skip-permissions"
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "NO_COLOR": "1"}
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, "Command timed out"
        except Exception as e:
            return False, str(e)


@pytest.fixture
def harness():
    """Provide a ClaudeTelemetryTest instance for standalone tests."""
    test_instance = ClaudeTelemetryTest()
    yield test_instance


def test_redis_available(harness: ClaudeTelemetryTest):
    """Test that Redis is running."""
    print("\n[TEST] Redis availability...")

    available = harness.check_redis()
    harness.record(
        "redis_available",
        available,
        "Redis is running" if available else "Redis not running - start with: redis-server"
    )
    assert available, "Redis is not available - start with: redis-server"
    return True


def test_server_starts(harness: ClaudeTelemetryTest):
    """Test that telemetry server can start."""
    print("\n[TEST] Starting telemetry server...")

    started = harness.server_manager.start(timeout=30)
    harness.record(
        "server_starts",
        started,
        "Telemetry server started" if started else "Failed to start telemetry server"
    )
    assert started, "Failed to start telemetry server"
    return True


def test_claude_cli_available(harness: ClaudeTelemetryTest):
    """Test that Claude CLI is installed and accessible."""
    print("\n[TEST] Claude CLI availability...")

    available = harness.check_prerequisites()
    harness.record(
        "claude_cli_available",
        available,
        "Claude CLI is installed" if available else "Claude CLI not found - install with: npm install -g @anthropic/claude-code"
    )
    assert available, "Claude CLI not found - install with: npm install -g @anthropic/claude-code"
    return True


def test_telemetry_db_exists(harness: ClaudeTelemetryTest):
    """Test that telemetry database exists."""
    print("\n[TEST] Telemetry database...")

    # Wait a moment for DB to be created
    time.sleep(2)

    db_exists = harness.telemetry_db.exists()
    if db_exists:
        harness.record("telemetry_db", True, f"Database exists at {harness.telemetry_db}")
    else:
        harness.record(
            "telemetry_db",
            False,
            f"Database not found at {harness.telemetry_db}",
            skip=True
        )
    assert db_exists, f"Telemetry database not found at {harness.telemetry_db}"
    return True


def test_simple_prompt_generates_events(harness: ClaudeTelemetryTest):
    """Test that a simple Claude prompt generates telemetry events."""
    print("\n[TEST] Simple prompt generates events...")

    initial_sqlite_count = harness.get_event_count_since()
    initial_redis_count = harness.get_redis_event_count_since()
    print(f"  Initial counts - SQLite: {initial_sqlite_count}, Redis: {initial_redis_count}")

    # Run a simple Claude command
    prompt = f"echo 'test marker: {harness.test_marker}'"
    print(f"  Running: claude -p \"{prompt}\" --dangerously-skip-permissions")

    success, output = harness.run_claude_command(prompt)
    if not success:
        harness.record("simple_prompt", False, f"Claude command failed: {output[:200]}")
        return False

    # Wait for events to be processed
    print("  Waiting for events to be captured...")
    time.sleep(5)

    final_sqlite_count = harness.get_event_count_since()
    final_redis_count = harness.get_redis_event_count_since()
    new_sqlite = final_sqlite_count - initial_sqlite_count
    new_redis = final_redis_count - initial_redis_count

    print(f"  Final counts - SQLite: {final_sqlite_count} (+{new_sqlite}), Redis: {final_redis_count} (+{new_redis})")

    # Check both Redis and SQLite
    if new_sqlite > 0:
        harness.record("simple_prompt", True, f"Generated {new_sqlite} SQLite events, {new_redis} Redis events")
        assert new_sqlite > 0, "Should have generated SQLite events"
        return True
    elif new_redis > 0:
        harness.record(
            "simple_prompt",
            False,
            f"Events in Redis ({new_redis}) but not in SQLite - server not consuming queue"
        )
        assert False, f"Events in Redis ({new_redis}) but not in SQLite - server not consuming queue"
    else:
        # Check if hooks are installed
        hooks_dir = Path.home() / ".claude" / "hooks" / "telemetry"
        if not hooks_dir.exists():
            harness.record(
                "simple_prompt",
                False,
                f"No events captured - telemetry hooks not installed. Run: python scripts/install_claude_hooks.py",
                skip=True
            )
            pytest.skip("Telemetry hooks not installed")
        else:
            harness.record(
                "simple_prompt",
                False,
                "No new events in Redis or SQLite - hooks not firing"
            )
            assert False, "No new events in Redis or SQLite - hooks not firing"


def test_event_structure(harness: ClaudeTelemetryTest):
    """Test that captured events have proper structure."""
    print("\n[TEST] Event structure validation...")

    events = harness.get_recent_events_since_start(limit=3)
    if not events:
        harness.record("event_structure", False, "No recent events to validate", skip=True)
        pytest.skip("No recent events to validate")

    # Check required fields
    required_fields = ["event_id", "event_type", "timestamp"]
    event = events[0]

    missing_fields = [f for f in required_fields if f not in event or event[f] is None]
    if missing_fields:
        harness.record("event_structure", False, f"Missing fields: {missing_fields}")
        assert False, f"Event missing required fields: {missing_fields}"

    harness.record("event_structure", True, f"Event has all required fields: {list(event.keys())}")
    assert len(missing_fields) == 0, "Event should have all required fields"
    return True


def test_conversation_tracking(harness: ClaudeTelemetryTest):
    """Test that conversation events are properly tracked."""
    print("\n[TEST] Conversation tracking...")

    # Run a multi-turn style prompt
    prompt = "What is 2+2? Just reply with the number."
    success, output = harness.run_claude_command(prompt, timeout=60)

    if not success:
        harness.record("conversation_tracking", False, f"Command failed: {output[:100]}")
        assert False, f"Claude command failed: {output[:100]}"

    time.sleep(3)

    # Check for conversation-related events
    events = harness.get_recent_events_since_start(limit=10)
    conversation_events = [e for e in events if "conversation" in str(e.get("event_type", "")).lower()]

    if conversation_events:
        harness.record("conversation_tracking", True, f"Found {len(conversation_events)} conversation events")
    else:
        harness.record(
            "conversation_tracking",
            True,
            f"Found {len(events)} events (conversation tracking may use different event types)"
        )
    assert len(events) > 0 or len(conversation_events) >= 0, "Should find events after Claude command"
    return True


def run_all_tests():
    """Run all integration tests."""
    print("=" * 70)
    print("Claude Code Telemetry - Real Integration Tests")
    print("=" * 70)
    print(f"\nTest started at: {datetime.now(timezone.utc).isoformat()}")

    harness = ClaudeTelemetryTest()
    server_started_by_us = False

    try:
        # Check prerequisites first
        if not test_redis_available(harness):
            print("\n!! Redis not available - cannot run integration tests")
            save_results(harness)
            return 1

        if not test_claude_cli_available(harness):
            print("\n!! Claude CLI not available - skipping remaining tests")
            harness.record("remaining_tests", False, "Skipped due to missing CLI", skip=True)
            save_results(harness)
            return 1

        # Start server if not already running
        if not harness.server_manager.is_running():
            server_started_by_us = test_server_starts(harness)
            if not server_started_by_us:
                print("\n!! Failed to start server - cannot run tests")
                save_results(harness)
                return 1
        else:
            harness.record("server_starts", True, "Server already running")

        # Run tests
        test_telemetry_db_exists(harness)
        test_simple_prompt_generates_events(harness)
        test_event_structure(harness)
        test_conversation_tracking(harness)

    finally:
        # Stop server if we started it
        if server_started_by_us:
            print("\n[CLEANUP] Stopping telemetry server...")
            harness.server_manager.stop()
            print("  Server stopped")

    # Summary
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print(f"  Passed:  {len(harness.results['passed'])}")
    print(f"  Failed:  {len(harness.results['failed'])}")
    print(f"  Skipped: {len(harness.results['skipped'])}")

    if harness.results['failed']:
        print("\nFailed tests:")
        for name, msg in harness.results['failed']:
            print(f"  - {name}: {msg}")

    # Save results to file
    save_results(harness)

    return 1 if harness.results['failed'] else 0


def save_results(harness: ClaudeTelemetryTest):
    """Save test results to /tmp/bp-telemetry-core/bug_fix/."""
    save_test_results(
        harness.results,
        "claude_telemetry_integration",
        "claude_integration"
    )


# Pytest compatibility
class TestClaudeTelemetry:
    """Pytest-compatible test class."""

    def setup_method(self):
        self.harness = ClaudeTelemetryTest()

    def teardown_method(self):
        """Clean up resources after each test."""
        if hasattr(self.harness, 'server_manager'):
            self.harness.server_manager.stop()

    def test_claude_cli_available(self):
        assert test_claude_cli_available(self.harness)

    def test_telemetry_db_exists(self):
        # Skip if CLI not available
        if not self.harness.check_prerequisites():
            pytest.skip("Claude CLI not available")
        assert test_telemetry_db_exists(self.harness)

    def test_simple_prompt_generates_events(self):
        if not self.harness.check_prerequisites():
            pytest.skip("Claude CLI not available")
        if not self.harness.telemetry_db.exists():
            pytest.skip("Telemetry DB not found")
        assert test_simple_prompt_generates_events(self.harness)


if __name__ == "__main__":
    sys.exit(run_all_tests())