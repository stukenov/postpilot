#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "Asia/Almaty"
DEFAULT_MIN_AGE_SECONDS = 86400


def load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        values[key] = value
    return values


def parse_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_error": "invalid_json", "path": str(path)}


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def format_dt(value: datetime | None, tz_name: str | None = None) -> str | None:
    if value is None:
        return None
    if tz_name:
        try:
            value = value.astimezone(ZoneInfo(tz_name))
        except Exception:
            pass
    return value.isoformat().replace("+00:00", "Z")


def env_int(config: dict[str, str], key: str, default: int) -> int:
    value = config.get(key)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def run_check_output(command: list[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return completed.returncode == 0, (completed.stdout or completed.stderr).strip()


@dataclass
class CronEntry:
    schedule: str
    user: str
    command: str


def parse_cron_entry(path: Path) -> CronEntry | None:
    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" in line.split(maxsplit=1)[0]:
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        schedule = " ".join(parts[:5])
        user = parts[5]
        command = " ".join(parts[6:])
        return CronEntry(schedule=schedule, user=user, command=command)
    return None


def parse_cron_field(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            base, step_str = part.split("/", 1)
            step = int(step_str)
        else:
            base = part
        if base == "*":
            start = minimum
            end = maximum
        elif "-" in base:
            start_str, end_str = base.split("-", 1)
            start = int(start_str)
            end = int(end_str)
        else:
            start = int(base)
            end = int(base)
        for value in range(start, end + 1, step):
            if minimum <= value <= maximum:
                values.add(value)
    return values


def next_cron_run(schedule: str, start: datetime) -> datetime | None:
    parts = schedule.split()
    if len(parts) != 5:
        return None
    minute_values = parse_cron_field(parts[0], 0, 59)
    hour_values = parse_cron_field(parts[1], 0, 23)
    day_values = parse_cron_field(parts[2], 1, 31)
    month_values = parse_cron_field(parts[3], 1, 12)
    weekday_values = parse_cron_field(parts[4], 0, 7)

    current = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = current + timedelta(days=366)
    while current <= limit:
        cron_weekday = (current.weekday() + 1) % 7
        cron_weekday_alt = 7 if cron_weekday == 0 else cron_weekday
        if (
            current.minute in minute_values
            and current.hour in hour_values
            and current.day in day_values
            and current.month in month_values
            and (cron_weekday in weekday_values or cron_weekday_alt in weekday_values)
        ):
            return current
        current += timedelta(minutes=1)
    return None


def stat_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "modified_at_utc": format_dt(modified_at),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Threads token refresh setup.")
    parser.add_argument("--project-dir", default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--cron-file", default="/etc/cron.d/threads-token-refresh")
    parser.add_argument("--summary-file", default=None)
    parser.add_argument("--whoami-file", default=None)
    parser.add_argument("--log-file", default=None)
    args = parser.parse_args()

    project_dir = Path(args.project_dir).resolve() if args.project_dir else Path(__file__).resolve().parents[1]
    env_file = Path(args.env_file).resolve() if args.env_file else project_dir / ".env"
    summary_file = Path(args.summary_file).resolve() if args.summary_file else project_dir / "state" / "threads-token-refresh-last.json"
    whoami_file = Path(args.whoami_file).resolve() if args.whoami_file else project_dir / "state" / "threads-token-refresh-whoami.json"
    log_file = Path(args.log_file).resolve() if args.log_file else project_dir / "state" / "threads-token-refresh.log"
    cron_file = Path(args.cron_file).resolve()

    config = load_dotenv(env_file)
    now_utc = datetime.now(tz=UTC)
    timezone_name = config.get("THREADS_TIMEZONE", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
    min_age_seconds = env_int(config, "THREADS_TOKEN_REFRESH_MIN_AGE_SECONDS", DEFAULT_MIN_AGE_SECONDS)
    refreshed_at = parse_iso_datetime(config.get("THREADS_ACCESS_TOKEN_REFRESHED_AT"))
    expires_in = env_int(config, "THREADS_ACCESS_TOKEN_EXPIRES_IN", 0)
    next_eligible = refreshed_at + timedelta(seconds=min_age_seconds) if refreshed_at else None
    expires_at = refreshed_at + timedelta(seconds=expires_in) if refreshed_at and expires_in else None
    age_seconds = int((now_utc - refreshed_at).total_seconds()) if refreshed_at else None

    cron_entry = parse_cron_entry(cron_file)
    system_timezone = datetime.now().astimezone().tzname() or "unknown"
    next_cron = next_cron_run(cron_entry.schedule, datetime.now().astimezone()) if cron_entry else None
    cron_active_ok, cron_active_text = run_check_output(["systemctl", "is-active", "cron"])
    publisher_active_ok, publisher_active_text = run_check_output(["systemctl", "is-active", "threads-publisher"])

    whoami_payload = parse_json_file(whoami_file)
    summary_payload = parse_json_file(summary_file)

    report = {
        "checked_at_utc": format_dt(now_utc),
        "project_dir": str(project_dir),
        "env": {
            "env_file": str(env_file),
            "threads_scopes": config.get("THREADS_SCOPES"),
            "threads_redirect_uri": config.get("THREADS_REDIRECT_URI"),
            "threads_callback_bind_uri": config.get("THREADS_CALLBACK_BIND_URI"),
            "threads_timezone": timezone_name,
            "token_present": bool(config.get("THREADS_ACCESS_TOKEN")),
            "token_refreshed_at_utc": format_dt(refreshed_at),
            "token_refreshed_at_local": format_dt(refreshed_at, timezone_name),
            "token_age_seconds": age_seconds,
            "token_expires_in_seconds": expires_in or None,
            "token_expires_at_utc": format_dt(expires_at),
            "token_expires_at_local": format_dt(expires_at, timezone_name),
            "min_age_seconds": min_age_seconds,
            "next_eligible_refresh_at_utc": format_dt(next_eligible),
            "next_eligible_refresh_at_local": format_dt(next_eligible, timezone_name),
        },
        "cron": {
            "cron_file": str(cron_file),
            "installed": cron_entry is not None,
            "service_active": cron_active_ok and cron_active_text == "active",
            "service_status": cron_active_text,
            "system_timezone": system_timezone,
            "schedule": cron_entry.schedule if cron_entry else None,
            "user": cron_entry.user if cron_entry else None,
            "command": cron_entry.command if cron_entry else None,
            "next_run_system_tz": format_dt(next_cron.astimezone(), None) if next_cron else None,
            "next_run_local": format_dt(next_cron.astimezone(ZoneInfo(timezone_name)), timezone_name) if next_cron else None,
        },
        "publisher_service": {
            "active": publisher_active_ok and publisher_active_text == "active",
            "status": publisher_active_text,
        },
        "state_files": {
            "summary_file": {
                "path": str(summary_file),
                **stat_file(summary_file),
                "payload": summary_payload,
            },
            "whoami_file": {
                "path": str(whoami_file),
                **stat_file(whoami_file),
                "profile": {
                    "id": whoami_payload.get("id"),
                    "username": whoami_payload.get("username"),
                    "name": whoami_payload.get("name"),
                }
                if isinstance(whoami_payload, dict)
                else None,
            },
            "log_file": {
                "path": str(log_file),
                **stat_file(log_file),
            },
        },
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
