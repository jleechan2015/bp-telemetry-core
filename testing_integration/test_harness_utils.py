#!/usr/bin/env python3
# Copyright © 2025 Sierra Labs LLC
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared helpers and base classes for integration test harnesses."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

RESULTS_DIR = Path("/tmp/bp-telemetry-core/bug_fix")
PROJECT_ROOT = Path(__file__).parent.parent


class BaseTelemetryTest:
    """Base test harness with common functionality for telemetry tests.

    Subclasses only need to define class attributes:
        CLI_COMMAND: list[str] - Command to run (e.g., ["claude"] or ["cursor-agent"])
        CLI_ARGS: list[str] - Arguments for the CLI (e.g., ["-p", "--dangerously-skip-permissions"])
        TABLE: str - SQLite table name (e.g., "claude_raw_traces")
        SUITE_NAME: str - Test suite name for reporting
        FILE_PREFIX: str - Prefix for result files
    """

    # Subclasses must override these
    CLI_COMMAND: list[str] = []
    CLI_ARGS: list[str] = []
    TABLE: str = ""
    SUITE_NAME: str = ""
    FILE_PREFIX: str = ""

    def __init__(self):
        self.telemetry_db = Path.home() / ".blueplane" / "telemetry.db"
        self.start_time = datetime.now(timezone.utc)
        self.results = {"passed": [], "failed": [], "skipped": []}
        self.test_marker = f"TEST_{uuid.uuid4().hex[:8]}"
        self.server_manager = TelemetryServerManager()

    def record(self, name: str, passed: bool, message: str = "", skip: bool = False):
        """Record test result with consistent formatting."""
        if skip:
            self.results["skipped"].append((name, message))
            print(f"  ⏭️  {name}: SKIPPED - {message}")
        elif passed:
            self.results["passed"].append((name, message))
            print(f"  ✅ {name}: {message}")
        else:
            self.results["failed"].append((name, message))
            print(f"  ❌ {name}: {message}")

    def check_redis(self) -> bool:
        """Check if Redis is running."""
        try:
            import redis
            r = redis.Redis(host='localhost', port=6379)
            r.ping()
            return True
        except Exception:
            return False

    def get_event_count(self, table: str, hours: Optional[int] = None) -> int:
        """
        Get count of events from a table.

        Args:
            table: Table name (must be in allowed list)
            hours: If provided, only count events from last N hours.
                   If None, counts events since test started.
        """
        allowed_tables = {"claude_raw_traces", "cursor_raw_traces"}
        if table not in allowed_tables:
            raise ValueError(f"Invalid table name: {table}")

        if not self.telemetry_db.exists():
            return 0

        if hours is not None:
            from datetime import timedelta
            since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        else:
            since = self.start_time.isoformat()

        try:
            with sqlite3.connect(str(self.telemetry_db)) as conn:
                cursor = conn.execute(f"""
                    SELECT COUNT(*) FROM {table}
                    WHERE timestamp >= ?
                """, (since,))
                return cursor.fetchone()[0]
        except sqlite3.Error as e:
            print(f"  Warning: DB error - {e}")
            return 0

    def get_recent_events(self, table: str, limit: int = 5) -> list:
        """
        Get recent events from a table.

        Args:
            table: Table name (must be in allowed list)
            limit: Maximum number of events to return
        """
        allowed_tables = {"claude_raw_traces", "cursor_raw_traces"}
        if table not in allowed_tables:
            raise ValueError(f"Invalid table name: {table}")

        if not self.telemetry_db.exists():
            return []

        try:
            with sqlite3.connect(str(self.telemetry_db)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(f"""
                    SELECT * FROM {table}
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"  Warning: DB error - {e}")
            return []

    def check_table_exists(self, table: str) -> bool:
        """Check if a table exists in the telemetry database."""
        if not self.telemetry_db.exists():
            return False

        try:
            with sqlite3.connect(str(self.telemetry_db)) as conn:
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name=?
                """, (table,))
                return cursor.fetchone() is not None
        except sqlite3.Error:
            return False

    def print_summary(self) -> None:
        """Print test results summary."""
        print("\n" + "=" * 70)
        print("RESULTS SUMMARY")
        print("=" * 70)
        print(f"  Passed:  {len(self.results['passed'])}")
        print(f"  Failed:  {len(self.results['failed'])}")
        print(f"  Skipped: {len(self.results['skipped'])}")

        if self.results['failed']:
            print("\nFailed tests:")
            for name, msg in self.results['failed']:
                print(f"  - {name}: {msg}")

    def check_cli(self) -> bool:
        """Check if CLI is available using CLI_COMMAND."""
        if not self.CLI_COMMAND:
            raise ValueError("CLI_COMMAND must be defined in subclass")
        try:
            result = subprocess.run(
                self.CLI_COMMAND + ["--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                cli_name = self.CLI_COMMAND[0]
                print(f"  {cli_name} version: {result.stdout.strip()}")
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return False

    def run_cli(self, prompt: str, timeout: int = 60) -> tuple[bool, str]:
        """Run CLI with a prompt and return (success, output).

        Uses CLI_COMMAND + CLI_ARGS + prompt
        """
        if not self.CLI_COMMAND:
            raise ValueError("CLI_COMMAND must be defined in subclass")
        try:
            full_cmd = self.CLI_COMMAND + self.CLI_ARGS + [prompt]
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
            return False, f"{self.CLI_COMMAND[0]} not found"
        except Exception as e:
            return False, str(e)

    def get_sqlite_count(self) -> int:
        """Count events in SQLite since test started."""
        return self.get_event_count(self.TABLE)

    def get_recent(self, limit: int = 5) -> list:
        """Get recent events filtered by test start time."""
        if not self.TABLE:
            raise ValueError("TABLE must be defined in subclass")
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

    def run_all_tests(self) -> int:
        """Run all integration tests for this CLI.

        Returns exit code (0 = success, 1 = failure).
        """
        cli_name = self.CLI_COMMAND[0] if self.CLI_COMMAND else "Unknown"
        print("=" * 70)
        print(f"{cli_name.title()} Telemetry - Integration Tests (REAL CLI)")
        print("=" * 70)
        print(f"Started: {datetime.now(timezone.utc).isoformat()}\n")

        we_started_server = False

        try:
            # Prerequisites
            print("[TEST] Redis...")
            if self.check_redis():
                self.record("redis", True, "Running")
            else:
                self.record("redis", False, "Not running - start with: redis-server")
                return self._finish()

            print(f"\n[TEST] {cli_name.title()} CLI...")
            if self.check_cli():
                self.record("cli", True, "Installed")
            else:
                self.record("cli", False, f"Not found - install {cli_name}", skip=True)
                return self._finish()

            # Server
            print("\n[TEST] Server...")
            if self.server_manager.is_running():
                self.record("server", True, "Already running")
            else:
                we_started_server = self.server_manager.start(timeout=30)
                self.record("server", we_started_server,
                           "Started" if we_started_server else "Failed")
                if not we_started_server:
                    return self._finish()

            # Database
            print("\n[TEST] Database...")
            time.sleep(2)
            if self.telemetry_db.exists():
                self.record("database", True, f"Exists at {self.telemetry_db}")
            else:
                self.record("database", False, "Not found", skip=True)
                return self._finish()

            # Event generation - REAL CLI INVOCATION
            print(f"\n[TEST] Event generation (invoking real {cli_name.title()} CLI)...")
            initial = self.get_sqlite_count()
            print(f"  Running: {' '.join(self.CLI_COMMAND + self.CLI_ARGS)} 'echo test marker: {self.test_marker}'")

            success, output = self.run_cli(f"echo 'test marker: {self.test_marker}'")
            if not success:
                self.record("events", False, f"{cli_name.title()} CLI failed: {output[:100]}", skip=True)
            else:
                time.sleep(5)
                new_count = self.get_sqlite_count() - initial
                if new_count > 0:
                    self.record("events", True, f"Generated {new_count} events")
                else:
                    self.record("events", False, "No events captured - check telemetry hooks")

            # Event structure
            print("\n[TEST] Event structure...")
            events = self.get_recent(limit=3)
            if events:
                required = ["event_id", "event_type", "timestamp"]
                missing = [f for f in required if f not in events[0] or events[0][f] is None]
                if missing:
                    self.record("structure", False, f"Missing: {missing}")
                else:
                    self.record("structure", True, f"Fields: {len(events[0])} columns")
            else:
                self.record("structure", False, "No events to validate", skip=True)

        finally:
            if we_started_server:
                print("\n[CLEANUP] Stopping server...")
                self.server_manager.stop()

        return self._finish()

    def _finish(self) -> int:
        """Print summary, save results, return exit code."""
        self.print_summary()
        save_test_results(self.results, self.SUITE_NAME, self.FILE_PREFIX)
        return 1 if self.results['failed'] else 0


class TelemetryServerManager:
    """Manages telemetry server lifecycle for testing."""

    def __init__(self):
        self.server_process = None
        self.server_script = PROJECT_ROOT / "scripts" / "start_server.py"
        self.pid_file = Path.home() / ".blueplane" / "server.pid"

    def is_running(self) -> bool:
        """Check if telemetry server is running."""
        if self.pid_file.exists():
            try:
                pid = int(self.pid_file.read_text().strip())
                os.kill(pid, 0)
                return True
            except (ValueError, OSError):
                pass
        return False

    def start(self, timeout: int = 30) -> bool:
        """Start telemetry server and wait for initialization."""
        if self.is_running():
            print("  Server already running")
            return True

        print(f"  Starting telemetry server...")
        self.server_process = subprocess.Popen(
            [sys.executable, str(self.server_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )

        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.pid_file.exists():
                print(f"  Server started (PID: {self.pid_file.read_text().strip()})")
                time.sleep(2)
                return True
            time.sleep(0.5)

        if self.server_process.poll() is not None:
            stdout, stderr = self.server_process.communicate()
            print(f"  Server failed: {stderr.decode()[:200]}")
            return False

        print(f"  Server start timed out")
        return False

    def stop(self) -> None:
        """Stop telemetry server."""
        if self.pid_file.exists():
            try:
                pid = int(self.pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
                try:
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            except (ValueError, OSError):
                pass

        if self.server_process:
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_process.kill()

        if self.pid_file.exists():
            try:
                self.pid_file.unlink()
            except OSError:
                pass


def save_test_results(results_dict: dict, test_suite_name: str, file_prefix: str) -> None:
    """Persist integration test results to JSON and text summary files."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    results = {
        "test_suite": test_suite_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "passed": len(results_dict.get("passed", [])),
            "failed": len(results_dict.get("failed", [])),
            "skipped": len(results_dict.get("skipped", [])),
        },
        "passed": [{"name": n, "message": m} for n, m in results_dict.get("passed", [])],
        "failed": [{"name": n, "message": m} for n, m in results_dict.get("failed", [])],
        "skipped": [{"name": n, "message": m} for n, m in results_dict.get("skipped", [])],
    }

    # Save JSON
    result_file = RESULTS_DIR / f"{file_prefix}_results.json"
    with open(result_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n📄 Results saved to: {result_file}")

    # Save text summary
    summary_file = RESULTS_DIR / f"{file_prefix}_summary.txt"
    with open(summary_file, "w") as f:
        f.write(f"{test_suite_name.replace('_', ' ').title()} - Integration Test Results\n")
        f.write("=" * 50 + "\n")
        f.write(f"Timestamp: {results['timestamp']}\n\n")
        f.write(f"Passed:  {results['summary']['passed']}\n")
        f.write(f"Failed:  {results['summary']['failed']}\n")
        f.write(f"Skipped: {results['summary']['skipped']}\n\n")

        sections = [
            ("passed", "✅", "PASSED"),
            ("failed", "❌", "FAILED"),
            ("skipped", "⏭️", "SKIPPED"),
        ]
        for key, emoji, title in sections:
            if results[key]:
                f.write(f"{title}:\n")
                for t in results[key]:
                    f.write(f"  {emoji} {t['name']}: {t['message']}\n")
                f.write("\n")

    print(f"📄 Summary saved to: {summary_file}")
