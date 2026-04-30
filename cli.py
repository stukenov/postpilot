#!/usr/bin/env python3
"""Unified CLI entry-point for multi-platform publishing.

Usage: python3 cli.py <command> <platform> [args...]

Examples:
    python3 cli.py run threads
    python3 cli.py build-queue linkedin --content-root content/linkedin/week-...
    python3 cli.py whoami threads
"""

import subprocess
import sys
from datetime import datetime, timezone

PLATFORMS = {"threads", "linkedin", "facebook", "telegram"}


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "Usage: python3 cli.py <command> <platform> [args...]\n"
            f"Platforms: {', '.join(sorted(PLATFORMS))}",
            file=sys.stderr,
        )
        return 1

    command = sys.argv[1]
    platform = sys.argv[2]
    rest_args = sys.argv[3:]

    if platform not in PLATFORMS:
        print(f"Unknown platform: {platform}. Must be one of: {', '.join(sorted(PLATFORMS))}", file=sys.stderr)
        return 1

    script = f"scripts/{platform}_publisher.py"
    full_cmd = [sys.executable, script, command, *rest_args]

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] cli: {platform} {command} {' '.join(rest_args)}".rstrip())

    result = subprocess.run(full_cmd)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
