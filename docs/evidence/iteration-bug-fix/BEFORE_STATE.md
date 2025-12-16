# BEFORE STATE - Bug Evidence

## Date: 2025-12-16
## Issue: Claude Event Consumer Not Processing Messages

### 1. Redis Queue Backlog
```
Consumer group: claude_processors
  Last delivered ID: 1764758965978-0
  Pending: 5
  Lag: 9695  <-- 9,695 unprocessed messages!
```

### 2. Consumer Idle Despite Work
```
=== Claude Consumer Info ===
  Consumer: claude-consumer-76724  Pending: 0  Idle: 69ms
```
Consumer appears active but has 0 pending - not claiming messages.

### 3. Bug Location
File: `src/processing/claude_code/event_consumer.py`
Line: ~630 (in `run()` method)

**Buggy Code:**
```python
while self.running:
    # ... later in loop references 'iteration' without defining it
    if iteration % 10 == 0:  # NameError: name 'iteration' is not defined
```

### 4. Root Cause Analysis
From `/tmp/bp-telemetry-debug/final_analysis.txt`:
```
SECONDARY ISSUE:
Even when hooks DO fire and write to Redis (10,000+ events in queue):
- The server's Claude event consumer is NOT processing them
- Events sit in Redis with lag=9842 messages
- Server has a bug: undefined `iteration` variable causes silent failure
```

### 5. Server Log Evidence
```
2025-12-16 10:07:25 - src.processing.claude_code.event_consumer - INFO - Claude Code event consumer started: claude-consumer-76724
```
No processing logs after startup - consumer was silently failing.

### 6. SQLite Status Before Fix
No recent Claude events being written despite 9,695+ in queue.
