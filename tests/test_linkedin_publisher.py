import json
import subprocess
import sys
from pathlib import Path

SCRIPT = [sys.executable, "scripts/linkedin_publisher.py"]
FIXTURES = Path("tests/fixtures/linkedin")


def test_export_txt_creates_txt_from_md():
    """export-txt should create .txt file next to .md file."""
    txt_file = FIXTURES / "2026-01-01" / "09-00.txt"
    txt_file.unlink(missing_ok=True)

    result = subprocess.run(
        [*SCRIPT, "export-txt", "--content-root", str(FIXTURES)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert txt_file.exists(), "export-txt should create 09-00.txt"

    text = txt_file.read_text().strip()
    assert "This is a test LinkedIn post." in text
    assert "# 09:00" not in text  # headers should be stripped

    txt_file.unlink(missing_ok=True)


def test_export_txt_missing_content_root_fails():
    """export-txt with nonexistent path should fail."""
    result = subprocess.run(
        [*SCRIPT, "export-txt", "--content-root", "nonexistent/path"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0


def _make_txt_fixture(tmp_path):
    """Create a minimal content tree for build-queue tests."""
    day_dir = tmp_path / "2026-01-01"
    day_dir.mkdir(parents=True)
    (day_dir / "09-00.txt").write_text("Test post.\n")
    return tmp_path


def test_build_queue_creates_queue_file(tmp_path):
    """build-queue should create a JSON queue file from .txt files."""
    content_dir = _make_txt_fixture(tmp_path / "content")
    queue_file = tmp_path / "queue.json"

    result = subprocess.run(
        [*SCRIPT, "build-queue",
         "--content-root", str(content_dir),
         "--queue-file", str(queue_file),
         "--timezone", "Asia/Almaty"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert queue_file.exists()

    queue = json.loads(queue_file.read_text())
    assert "items" in queue
    assert len(queue["items"]) == 1

    item = queue["items"][0]
    assert item["id"] == "2026-01-01__09-00"
    assert item["status"] == "pending"
    assert "publish_at_utc" in item
    assert item["relative_path"] == "2026-01-01/09-00.txt"


def test_build_queue_preserves_posted_status(tmp_path):
    """build-queue should not overwrite posted items."""
    content_dir = _make_txt_fixture(tmp_path / "content")
    queue_file = tmp_path / "queue.json"

    existing = {
        "version": 1,
        "items": [{
            "id": "2026-01-01__09-00",
            "status": "posted",
            "relative_path": "2026-01-01/09-00.txt",
            "publish_at_utc": "2026-01-01T04:00:00Z",
        }]
    }
    queue_file.write_text(json.dumps(existing))

    result = subprocess.run(
        [*SCRIPT, "build-queue",
         "--content-root", str(content_dir),
         "--queue-file", str(queue_file),
         "--timezone", "Asia/Almaty"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0

    queue = json.loads(queue_file.read_text())
    item = next(i for i in queue["items"] if i["id"] == "2026-01-01__09-00")
    assert item["status"] == "posted"


def _load_module():
    """Import linkedin_publisher as a module for direct function testing."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("linkedin_publisher", "scripts/linkedin_publisher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_playwright_command_basic(tmp_path):
    """build_playwright_command should construct the correct node command."""
    mod = _load_module()
    txt_file = tmp_path / "post.txt"
    txt_file.write_text("Hello LinkedIn\n")
    storage_state = tmp_path / "auth.json"
    storage_state.write_text("{}")

    config = {
        "LINKEDIN_NODE_BIN": "node",
        "LINKEDIN_PLAYWRIGHT_STORAGE_STATE": str(storage_state),
        "LINKEDIN_PLAYWRIGHT_HEADLESS": "true",
    }

    cmd = mod.build_playwright_command(config, str(txt_file))
    assert cmd[0] == "node"
    assert "linkedin_publish_playwright.mjs" in cmd[1]
    assert "--storage-state" in cmd
    assert str(storage_state) in cmd
    assert "--headless" in cmd
    assert str(txt_file) in cmd


def test_build_playwright_command_headed(tmp_path):
    """build_playwright_command with headless=false should pass --headed."""
    mod = _load_module()
    txt_file = tmp_path / "post.txt"
    txt_file.write_text("Hello\n")
    storage_state = tmp_path / "auth.json"
    storage_state.write_text("{}")

    config = {
        "LINKEDIN_NODE_BIN": "node",
        "LINKEDIN_PLAYWRIGHT_STORAGE_STATE": str(storage_state),
        "LINKEDIN_PLAYWRIGHT_HEADLESS": "false",
    }

    cmd = mod.build_playwright_command(config, str(txt_file))
    assert "--headed" in cmd
    assert "--headless" not in cmd


def test_publish_text_file_dry_run(tmp_path):
    """publish_text_file in dry_run mode should not call subprocess."""
    mod = _load_module()
    txt_file = tmp_path / "post.txt"
    txt_file.write_text("Hello LinkedIn\n")

    config = {
        "LINKEDIN_NODE_BIN": "node",
        "LINKEDIN_PLAYWRIGHT_STORAGE_STATE": "state/linkedin-browser-auth.json",
        "LINKEDIN_PLAYWRIGHT_HEADLESS": "true",
    }

    result = mod.publish_text_file(config, str(txt_file), dry_run=True)
    assert result["status"] == "dry-run"
    assert result["file"] == str(txt_file)


def test_publish_text_file_missing_storage_state(tmp_path):
    """publish_text_file should raise if no storage state configured."""
    import pytest as _pytest
    mod = _load_module()
    txt_file = tmp_path / "post.txt"
    txt_file.write_text("Hello\n")

    config = {}

    with _pytest.raises(RuntimeError, match="LINKEDIN_PLAYWRIGHT_STORAGE_STATE"):
        mod.publish_text_file(config, str(txt_file))


def test_run_exits_cleanly_on_sigterm(tmp_path):
    """run with an empty queue should exit cleanly on SIGTERM."""
    import signal as sig
    import time

    queue_file = tmp_path / "queue.json"
    queue_file.write_text(json.dumps({"version": 1, "items": []}))

    proc = subprocess.Popen(
        [*SCRIPT, "run",
         "--queue-file", str(queue_file),
         "--poll-seconds", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(2)
    proc.send_signal(sig.SIGTERM)
    proc.wait(timeout=5)
    assert proc.returncode == 0, f"stderr: {proc.stderr.read()}"
