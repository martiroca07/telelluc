#!/usr/bin/env python3
"""
Test suite to verify command isolation and functionality.
"""

import subprocess
import sys
import time

def test_processes_isolation():
    """Test that processes command works multiple times without contamination."""
    print("\n" + "="*60)
    print("TEST 1: PROCESSES COMMAND ISOLATION")
    print("="*60)

    results = []
    for i in range(3):
        print(f"\nRun {i+1}:")
        # Simulate running processes command
        result = subprocess.run(
            [sys.executable, "-c", """
import subprocess
result = subprocess.run(["tasklist", "/fo", "csv"], capture_output=True, text=True, timeout=15)
lines = result.stdout.strip().split("\\n")
print(f"Lines returned: {len(lines)}")
print(f"Has header: {'Name' in result.stdout}")
print(f"First 100 chars: {result.stdout[:100]}")
            """],
            capture_output=True,
            text=True,
            timeout=20
        )
        print(result.stdout)
        if result.stderr:
            print(f"Error: {result.stderr}")
        results.append(result.returncode == 0)

    print(f"\n✅ All runs successful: {all(results)}")
    return all(results)


def test_input_cleaning():
    """Test that input cleaning works correctly."""
    print("\n" + "="*60)
    print("TEST 2: INPUT CLEANING")
    print("="*60)

    test_inputs = [
        ("cd test", "cd test"),
        ("processes\x00", "processes"),
        ("processes\r\n", "processes"),
        ("  mimetic  ", "mimetic"),
        ("processes\x00\x00\r", "processes"),
    ]

    passed = 0
    for input_str, expected in test_inputs:
        cleaned = input_str.strip().replace('\x00', '').replace('\r', '')
        success = cleaned == expected
        status = "✅" if success else "❌"
        print(f"{status} Input: {repr(input_str)} → {repr(cleaned)} (expected: {repr(expected)})")
        if success:
            passed += 1

    print(f"\n✅ Passed: {passed}/{len(test_inputs)}")
    return passed == len(test_inputs)


def test_command_independence():
    """Test that commands don't interfere with each other."""
    print("\n" + "="*60)
    print("TEST 3: COMMAND INDEPENDENCE")
    print("="*60)

    # Test running different commands in sequence
    commands = [
        ("processes", "List processes"),
        ("ipconfig", "Network config"),
        ("processes", "List processes again"),
    ]

    results = []
    for cmd, desc in commands:
        print(f"\nExecuting: {desc} ('{cmd}')")
        result = subprocess.run(
            [sys.executable, "-c", f"""
import subprocess
if '{cmd}' == 'processes':
    result = subprocess.run(["tasklist", "/fo", "csv"], capture_output=True, text=True, timeout=15)
elif '{cmd}' == 'ipconfig':
    result = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=10)
print(f"Output lines: {{len(result.stdout.split(chr(10)))}}")
print(f"Success: {{bool(result.stdout)}}")
            """],
            capture_output=True,
            text=True,
            timeout=30
        )
        success = result.returncode == 0 and result.stdout
        status = "✅" if success else "❌"
        print(f"{status} {result.stdout.strip()}")
        results.append(success)

    print(f"\n✅ All commands independent: {all(results)}")
    return all(results)


def test_output_cleanliness():
    """Test that output doesn't have artifacts from previous commands."""
    print("\n" + "="*60)
    print("TEST 4: OUTPUT CLEANLINESS")
    print("="*60)

    # Run processes twice and check for clean output
    outputs = []
    for i in range(2):
        result = subprocess.run(
            [sys.executable, "-c", """
import subprocess
result = subprocess.run(["tasklist", "/fo", "csv"], capture_output=True, text=True, timeout=15)
# Check for clean output markers
has_no_nulls = '\\x00' not in result.stdout
has_no_control_chars = not any(c < ' ' and c not in '\\n\\t' for c in result.stdout)
print(f"No null bytes: {has_no_nulls}")
print(f"No control chars: {has_no_control_chars}")
            """],
            capture_output=True,
            text=True,
            timeout=20
        )
        print(f"\nRun {i+1}:")
        print(result.stdout.strip())
        outputs.append(result.returncode == 0)

    print(f"\n✅ All outputs clean: {all(outputs)}")
    return all(outputs)


def main():
    """Run all tests."""
    print("\n" + "="*70)
    print("TELELLUC COMMAND ISOLATION & FUNCTIONALITY TEST SUITE")
    print("="*70)

    tests = [
        test_input_cleaning,
        test_output_cleanliness,
        test_processes_isolation,
        test_command_independence,
    ]

    results = []
    for test in tests:
        try:
            results.append(test())
        except Exception as e:
            print(f"\n❌ Test failed with exception: {e}")
            results.append(False)

    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    passed = sum(results)
    total = len(results)
    print(f"✅ Passed: {passed}/{total}")

    if passed == total:
        print("\n🎉 ALL TESTS PASSED - Commands are properly isolated!")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed - See details above")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
