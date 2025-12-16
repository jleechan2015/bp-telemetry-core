#!/usr/bin/env python3
"""Simulate the NameError bug to capture the actual error."""

# Simulate the buggy code path
def buggy_consumer_loop():
    """This is what the code looked like BEFORE the fix."""
    pending_count = 250  # High backlog triggers the bug
    
    # NO iteration = 0 initialization!
    
    while True:
        try:
            if pending_count >= 200:
                # This line references 'iteration' which was never defined
                if iteration % 10 == 0:  # NameError!
                    print("Prioritizing pending messages")
            break
        except NameError as e:
            return str(e)
    return None

def fixed_consumer_loop():
    """This is what the code looks like AFTER the fix."""
    pending_count = 250
    
    iteration = 0  # FIXED: Initialize iteration
    
    while True:
        iteration += 1  # FIXED: Increment iteration
        try:
            if pending_count >= 200:
                if iteration % 10 == 0:
                    print("Prioritizing pending messages")
            break
        except NameError as e:
            return str(e)
    return None

if __name__ == "__main__":
    print("=" * 60)
    print("BEFORE FIX (buggy code):")
    print("=" * 60)
    error = buggy_consumer_loop()
    if error:
        print(f"NameError raised: {error}")
    else:
        print("No error")
    
    print()
    print("=" * 60)
    print("AFTER FIX (corrected code):")
    print("=" * 60)
    error = fixed_consumer_loop()
    if error:
        print(f"NameError raised: {error}")
    else:
        print("No error - code runs correctly")
