#!/usr/bin/env python3
# Copyright © 2025 Sierra Labs LLC
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared helpers and base classes for integration test harnesses."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

RESULTS_DIR = Path("/tmp/bp-telemetry-core/bug_fix")


class BaseTelemetryTest:
    """Base test harness with common functionality for telemetry tests."""

    def __init__(self):
        self.telemetry_db = Path.home() / ".blueplane" / "telemetry.db"
        self.start_time = datetime.now(timezone.utc)
        self.results = {"passed": [], "failed": [], "skipped": []}

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
