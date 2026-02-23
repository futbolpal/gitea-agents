#!/usr/bin/env python3
"""Test runner for kilo-agent."""

import subprocess
import sys

def main():
    """Run the test suite."""
    print("Running kilo-agent tests...")

    result = subprocess.run(
        [sys.executable, '-m', 'unittest', 'discover', '-v'],
        capture_output=True,
        text=True
    )

    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)

    return result.returncode

if __name__ == '__main__':
    sys.exit(main())
