#!/usr/bin/env python3
# Copyright © 2025 Sierra Labs LLC
# SPDX-License-Identifier: AGPL-3.0-only
# License-Filename: LICENSE

"""
Regression test for iteration variable bug in Claude event consumer.

BUG DESCRIPTION:
The consumer loop referenced `iteration` variable without initializing it,
causing a NameError when pending_count >= 200 and the code path tried to
log every 10th iteration: `if iteration % 10 == 0`.

This test verifies the iteration variable is properly initialized and
incremented.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))


class TestIterationVariableFix:
    """Regression tests for iteration variable initialization."""

    def test_iteration_variable_initialized(self):
        """Verify iteration is initialized before use in run() method."""
        # Read the source file and verify iteration is initialized
        consumer_file = project_root / "src" / "processing" / "claude_code" / "event_consumer.py"

        with open(consumer_file, "r") as f:
            source_code = f.read()

        # Find the run method
        assert "def run(self)" in source_code, "run() method should exist"

        # Extract run method body
        run_start = source_code.find("def run(self)")
        # Find the while loop start
        while_start = source_code.find("while self.running:", run_start)
        assert while_start > run_start, "while loop should be in run()"

        # Check that iteration = 0 appears before while loop
        code_before_while = source_code[run_start:while_start]
        assert "iteration = 0" in code_before_while, \
            "iteration should be initialized to 0 before while loop"

    def test_iteration_incremented_in_loop(self):
        """Verify iteration is incremented at start of each loop."""
        consumer_file = project_root / "src" / "processing" / "claude_code" / "event_consumer.py"

        with open(consumer_file, "r") as f:
            source_code = f.read()

        # Find the while loop
        while_start = source_code.find("while self.running:")
        assert while_start > 0, "while loop should exist"

        # Get some code after the while loop start
        code_after_while = source_code[while_start:while_start + 500]

        # iteration += 1 should appear early in the loop
        assert "iteration += 1" in code_after_while, \
            "iteration should be incremented at start of loop"

    def test_iteration_used_safely(self):
        """Verify iteration % 10 is only used after initialization."""
        consumer_file = project_root / "src" / "processing" / "claude_code" / "event_consumer.py"

        with open(consumer_file, "r") as f:
            lines = f.readlines()

        init_line = None
        mod_line = None

        for i, line in enumerate(lines):
            if "iteration = 0" in line and init_line is None:
                init_line = i
            if "iteration % 10" in line and mod_line is None:
                mod_line = i

        assert init_line is not None, "iteration = 0 should exist"
        assert mod_line is not None, "iteration % 10 should exist"
        assert init_line < mod_line, \
            f"iteration must be initialized (line {init_line}) before use (line {mod_line})"

    @patch('src.processing.claude_code.event_consumer.logger')
    def test_consumer_loop_no_nameerror(self, mock_logger):
        """Integration test: consumer loop should not raise NameError."""
        from src.processing.claude_code.event_consumer import ClaudeEventConsumer

        # Create mock dependencies
        mock_redis = MagicMock()
        mock_redis.xinfo_groups.return_value = []
        mock_redis.xgroup_create = MagicMock()
        mock_redis.xpending.return_value = {'pending': 0}
        mock_redis.xreadgroup.return_value = []

        mock_writer = MagicMock()
        mock_cdc = MagicMock()

        # Create consumer
        consumer = ClaudeEventConsumer(
            redis_client=mock_redis,
            claude_writer=mock_writer,
            cdc_publisher=mock_cdc,
            stream_name="test:stream",
            consumer_group="test_group",
            consumer_name="test_consumer",
        )

        # Simulate high pending count to trigger the iteration % 10 path
        # This would have caused NameError before the fix
        consumer._get_pending_count = MagicMock(return_value=250)
        consumer._process_pending_messages = MagicMock()
        consumer._should_throttle_reads = MagicMock(return_value=False)
        consumer._read_messages = MagicMock(return_value=[])

        # Run a few iterations then stop
        iteration_count = [0]
        original_running = [True]

        def stop_after_iterations():
            iteration_count[0] += 1
            if iteration_count[0] >= 5:
                consumer.running = False
            return 250  # Keep returning high pending count

        consumer._get_pending_count = stop_after_iterations

        # This should NOT raise NameError
        try:
            consumer.run()
        except NameError as e:
            pytest.fail(f"NameError raised - iteration variable not initialized: {e}")
        except Exception as e:
            # Other exceptions are OK for this test - we just care about NameError
            if "iteration" in str(e).lower():
                pytest.fail(f"Iteration-related error: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
