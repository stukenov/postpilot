import subprocess
import sys

CLI = [sys.executable, "cli.py"]


def test_routes_to_threads_publisher():
    """cli.py should call scripts/threads_publisher.py with the right args."""
    result = subprocess.run(
        [*CLI, "whoami", "threads"],
        capture_output=True, text=True, timeout=10,
    )
    # threads_publisher.py whoami needs THREADS_ACCESS_TOKEN which is not set,
    # so it will fail — but the point is it gets routed there, not "unknown platform"
    assert "unknown platform" not in result.stderr.lower()


def test_unknown_platform_fails():
    """cli.py should exit non-zero for unknown platforms."""
    result = subprocess.run(
        [*CLI, "run", "tiktok"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "tiktok" in result.stderr.lower()


def test_missing_args_shows_usage():
    """cli.py with no args should show usage and exit non-zero."""
    result = subprocess.run(
        CLI,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "usage" in result.stderr.lower()


def test_missing_platform_shows_error():
    """cli.py with command but no platform should error."""
    result = subprocess.run(
        [*CLI, "run"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0


def test_extra_args_forwarded():
    """Extra args after platform should be forwarded to the script."""
    result = subprocess.run(
        [*CLI, "build-queue", "threads", "--content-root", "content/threads/week-test"],
        capture_output=True, text=True, timeout=10,
    )
    # Will fail because path doesn't exist, but should NOT fail with "unknown platform"
    assert "unknown platform" not in result.stderr.lower()


def test_logs_to_stdout():
    """cli.py should log the invocation to stdout."""
    result = subprocess.run(
        [*CLI, "whoami", "threads"],
        capture_output=True, text=True, timeout=10,
    )
    assert "threads" in result.stdout.lower()
