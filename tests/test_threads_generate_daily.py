import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

FIXTURES = Path("tests/fixtures/threads-generate")
SCRIPT = [sys.executable, "scripts/threads_generate_daily.py"]


def _load_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "threads_generate_daily", "scripts/threads_generate_daily.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- get_week_folder ---


def test_get_week_folder_monday():
    mod = _load_module()
    assert mod.get_week_folder(date(2026, 1, 5)) == "week-2026-01-05-to-2026-01-11"


def test_get_week_folder_wednesday():
    mod = _load_module()
    assert mod.get_week_folder(date(2026, 1, 7)) == "week-2026-01-05-to-2026-01-11"


def test_get_week_folder_sunday():
    mod = _load_module()
    assert mod.get_week_folder(date(2026, 1, 11)) == "week-2026-01-05-to-2026-01-11"


# --- resolve_target_date ---


def test_resolve_target_date_iso():
    mod = _load_module()
    assert mod.resolve_target_date("2026-03-15") == date(2026, 3, 15)


def test_resolve_target_date_tomorrow():
    mod = _load_module()
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    expected = (datetime.now(ZoneInfo("Asia/Almaty")) + timedelta(days=1)).date()
    assert mod.resolve_target_date("tomorrow") == expected


# --- list_recent_posts ---


def test_list_recent_posts_finds_fixtures():
    mod = _load_module()
    posts = mod.list_recent_posts(
        content_root=FIXTURES,
        ref_date=date(2026, 1, 6),
        days=7,
    )
    assert len(posts) == 2
    titles = [p["title"] for p in posts]
    assert "Тестовый пост про потерю заявок" in titles
    assert "Второй тестовый пост про CRM" in titles


def test_list_recent_posts_respects_date_range():
    mod = _load_module()
    posts = mod.list_recent_posts(
        content_root=FIXTURES,
        ref_date=date(2026, 6, 1),
        days=7,
    )
    assert len(posts) == 0


def test_list_recent_posts_parses_title():
    mod = _load_module()
    posts = mod.list_recent_posts(
        content_root=FIXTURES,
        ref_date=date(2026, 1, 6),
        days=7,
    )
    post_09 = next(p for p in posts if p["time_slot"] == "09-00")
    assert post_09["date"] == "2026-01-06"
    assert post_09["title"] == "Тестовый пост про потерю заявок"


# --- get_slots ---


def test_get_slots_all_free(tmp_path):
    mod = _load_module()
    result = mod.get_slots(
        content_root=tmp_path,
        target_date=date(2026, 1, 6),
        configured_slots=["09-00", "10-30"],
    )
    assert result["date"] == "2026-01-06"
    assert result["free_slots"] == ["09-00", "10-30"]
    assert result["occupied_slots"] == []


def test_get_slots_some_occupied(tmp_path):
    mod = _load_module()
    week = tmp_path / "week-2026-01-05-to-2026-01-11" / "2026-01-06"
    week.mkdir(parents=True)
    (week / "09-00.md").write_text("# 09:00 — Existing\n")
    result = mod.get_slots(
        content_root=tmp_path,
        target_date=date(2026, 1, 6),
        configured_slots=["09-00", "10-30"],
    )
    assert result["free_slots"] == ["10-30"]
    assert result["occupied_slots"] == ["09-00"]


# --- create_post ---


def test_create_post_writes_md(tmp_path):
    mod = _load_module()
    result = mod.create_post(
        content_root=tmp_path,
        target_date=date(2026, 1, 6),
        configured_slots=["09-00", "10-30"],
        time_slot="09-00",
        title="Тестовый заголовок",
        step="Шаг 1",
        fmt="Короткий список",
        goal="Тестовая цель",
        body="Тестовый текст поста длиной больше пятидесяти символов для прохождения валидации.",
        cta="CTA:",
    )
    assert result["status"] == "created"
    md_path = tmp_path / "week-2026-01-05-to-2026-01-11" / "2026-01-06" / "09-00.md"
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "# 09:00 — Тестовый заголовок" in content
    assert "Шаг 1" in content
    assert "Тестовый текст поста" in content
    assert "- День: Вторник" in content


def test_create_post_rejects_invalid_slot(tmp_path):
    mod = _load_module()
    result = mod.create_post(
        content_root=tmp_path,
        target_date=date(2026, 1, 6),
        configured_slots=["09-00", "10-30"],
        time_slot="11-00",
        title="T", step="Шаг 1", fmt="F", goal="G",
        body="x" * 100,
        cta="CTA:",
    )
    assert result["status"] == "error"


def test_create_post_rejects_empty_body(tmp_path):
    mod = _load_module()
    result = mod.create_post(
        content_root=tmp_path,
        target_date=date(2026, 1, 6),
        configured_slots=["09-00"],
        time_slot="09-00",
        title="T", step="Шаг 1", fmt="F", goal="G",
        body="",
        cta="CTA:",
    )
    assert result["status"] == "error"


def test_create_post_rejects_too_short_body(tmp_path):
    mod = _load_module()
    result = mod.create_post(
        content_root=tmp_path,
        target_date=date(2026, 1, 6),
        configured_slots=["09-00"],
        time_slot="09-00",
        title="T", step="Шаг 1", fmt="F", goal="G",
        body="short",
        cta="CTA:",
    )
    assert result["status"] == "error"


def test_create_post_already_exists(tmp_path):
    mod = _load_module()
    kwargs = dict(
        content_root=tmp_path,
        target_date=date(2026, 1, 6),
        configured_slots=["09-00"],
        time_slot="09-00",
        title="T", step="Шаг 1", fmt="F", goal="G",
        body="x" * 100,
        cta="CTA:",
    )
    mod.create_post(**kwargs)
    result = mod.create_post(**kwargs)
    assert result["status"] == "already_exists"


# --- build_system_prompt ---


def test_build_system_prompt_includes_strategy():
    mod = _load_module()
    prompt = mod.build_system_prompt(strategy_root=Path("threads-strategy"))
    assert "контент-менеджер" in prompt
    assert "220-380" in prompt
    assert "list_recent_posts" in prompt


def test_build_system_prompt_logs_size_warning(tmp_path, capsys):
    mod = _load_module()
    strat = tmp_path / "strategy"
    strat.mkdir()
    (strat / "README.md").write_text("x" * 60000, encoding="utf-8")
    mod.build_system_prompt(strategy_root=strat)
    captured = capsys.readouterr()
    assert "WARNING" in captured.out


# --- build_tool_definitions ---


def test_build_tool_definitions():
    mod = _load_module()
    tools = mod.build_tool_definitions()
    assert len(tools) == 3
    names = {t["function"]["name"] for t in tools}
    assert names == {"list_recent_posts", "get_slots", "create_post"}
    for t in tools:
        assert "parameters" in t["function"]


# --- execute_tool ---


def test_execute_tool_list_recent_posts():
    mod = _load_module()
    result = mod.execute_tool(
        name="list_recent_posts",
        arguments={"days": 7},
        target_date=date(2026, 1, 6),
        content_root=FIXTURES,
        configured_slots=["09-00"],
        lookback_days=14,
    )
    assert isinstance(result, list)
    assert len(result) == 2


def test_execute_tool_get_slots(tmp_path):
    mod = _load_module()
    result = mod.execute_tool(
        name="get_slots",
        arguments={"date": "2026-01-06"},
        target_date=date(2026, 1, 6),
        content_root=tmp_path,
        configured_slots=["09-00", "10-30"],
        lookback_days=14,
    )
    assert "free_slots" in result


def test_execute_tool_create_post(tmp_path):
    mod = _load_module()
    result = mod.execute_tool(
        name="create_post",
        arguments={
            "time_slot": "09-00",
            "title": "Test",
            "step": "Шаг 1",
            "format": "Заметка",
            "goal": "Test",
            "body": "x" * 100,
            "cta": "CTA:",
        },
        target_date=date(2026, 1, 6),
        content_root=tmp_path,
        configured_slots=["09-00"],
        lookback_days=14,
    )
    assert result["status"] == "created"


def test_execute_tool_unknown():
    mod = _load_module()
    result = mod.execute_tool(
        name="unknown_tool",
        arguments={},
        target_date=date(2026, 1, 6),
        content_root=Path("."),
        configured_slots=[],
        lookback_days=14,
    )
    assert result["status"] == "error"


# --- write_log_entry ---


def test_log_jsonl_written(tmp_path):
    mod = _load_module()
    log_file = tmp_path / "generate.jsonl"
    mod.write_log_entry(log_file, {
        "event": "test",
        "target_date": "2026-01-06",
    })
    assert log_file.exists()
    entry = json.loads(log_file.read_text().strip())
    assert entry["event"] == "test"
    assert "ts" in entry


# --- CLI ---


def test_cli_missing_date_fails():
    result = subprocess.run(SCRIPT, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0


def test_cli_missing_api_key_fails():
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    result = subprocess.run(
        [*SCRIPT, "--date", "2026-01-06"],
        capture_output=True, text=True, timeout=10,
        env=env,
    )
    assert result.returncode != 0
    assert "OPENROUTER_API_KEY" in result.stderr
