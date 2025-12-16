#!/usr/bin/env python3
# Copyright © 2025 Sierra Labs LLC
# SPDX-License-Identifier: AGPL-3.0-only
# License-Filename: LICENSE

"""
Real Integration Tests for Cursor Telemetry

Invokes the Cursor Agent CLI with -p (print mode) and -f (force) flags
and verifies telemetry events are captured in the database.

Usage:
    python testing_integration/test_cursor_telemetry.py
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from testing_integration.test_harness_utils import BaseTelemetryTest


class CursorTelemetryTest(BaseTelemetryTest):
    """Test harness for Cursor telemetry integration tests."""

    CLI_COMMAND = ["cursor-agent"]
    CLI_ARGS = ["-p", "-f"]  # -p: print/non-interactive, -f: force allow commands
    TABLE = "cursor_raw_traces"
    SUITE_NAME = "cursor_telemetry_integration"
    FILE_PREFIX = "cursor_integration"


if __name__ == "__main__":
    sys.exit(CursorTelemetryTest().run_all_tests())
