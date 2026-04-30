import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

SCRIPT = [sys.executable, "scripts/telegram_publisher.py"]
FIXTURES = Path("tests/fixtures/telegram")


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
    assert "This is a test Telegram post." in text
    assert "# 09:00" not in text

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
    """Import telegram_publisher as a module for direct function testing."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("telegram_publisher", "scripts/telegram_publisher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_publish_text_file_dry_run(tmp_path):
    """publish_text_file in dry_run mode should not call the API."""
    mod = _load_module()
    txt_file = tmp_path / "post.txt"
    txt_file.write_text("Hello Telegram\n")

    config = {
        "TELEGRAM_BOT_TOKEN": "fake-token",
        "TELEGRAM_CHAT_ID": "@test",
    }

    result = mod.publish_text_file(config, str(txt_file), dry_run=True)
    assert result["status"] == "dry-run"
    assert result["file"] == str(txt_file)


def test_publish_text_file_missing_token(tmp_path):
    """publish_text_file should raise if no bot token configured."""
    import pytest as _pytest
    mod = _load_module()
    txt_file = tmp_path / "post.txt"
    txt_file.write_text("Hello\n")

    config = {"TELEGRAM_CHAT_ID": "@test"}

    with _pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        mod.publish_text_file(config, str(txt_file))


def test_publish_text_file_missing_chat_id(tmp_path):
    """publish_text_file should raise if no chat_id configured."""
    import pytest as _pytest
    mod = _load_module()
    txt_file = tmp_path / "post.txt"
    txt_file.write_text("Hello\n")

    config = {"TELEGRAM_BOT_TOKEN": "fake-token"}

    with _pytest.raises(RuntimeError, match="TELEGRAM_CHAT_ID"):
        mod.publish_text_file(config, str(txt_file))


def test_publish_text_file_success(tmp_path):
    """publish_text_file should POST to Telegram Bot API and return posted."""
    mod = _load_module()
    txt_file = tmp_path / "post.txt"
    txt_file.write_text("Hello Telegram\n")

    config = {
        "TELEGRAM_BOT_TOKEN": "fake-token",
        "TELEGRAM_CHAT_ID": "@test",
    }

    fake_response = json.dumps({
        "ok": True,
        "result": {"message_id": 42},
    }).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_response
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = mod.publish_text_file(config, str(txt_file))

    assert result["status"] == "posted"
    assert result["message_id"] == 42

    call_args = mock_urlopen.call_args
    req = call_args[0][0]
    assert "api.telegram.org/botfake-token/sendMessage" in req.full_url
    body = json.loads(req.data)
    assert body["chat_id"] == "@test"
    assert body["text"] == "Hello Telegram"


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
