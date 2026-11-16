"""Posts without a '## Текст поста' heading must export as-is.

Regression: the README quickstart and content/examples use plain Markdown
posts. Threads rejected them and the other publishers exported empty text,
so a fresh clone published nothing (issue #4).
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path("content/examples/week-2025-01-06-to-2025-01-12")
FIRST_LINE = "Most startups don't fail because of bad code."


@pytest.mark.parametrize("platform", ["threads", "linkedin", "facebook", "telegram"])
def test_export_txt_keeps_plain_markdown_post(tmp_path, platform):
    root = tmp_path / "week"
    shutil.copytree(EXAMPLES, root)

    result = subprocess.run(
        [sys.executable, f"scripts/{platform}_publisher.py", "export-txt", "--content-root", str(root)],
        capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 0, result.stderr
    txt = (root / "2025-01-06" / "09-00.txt").read_text(encoding="utf-8")
    assert txt.startswith(FIRST_LINE)
