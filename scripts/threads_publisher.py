#!/usr/bin/env python3

from __future__ import annotations

import argparse
import atexit
import fcntl
import hashlib
import http.server
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "Asia/Almaty"
DEFAULT_API_BASE = "https://graph.threads.net/v1.0"
DEFAULT_SCOPES = "threads_basic,threads_content_publish,threads_manage_insights"
DEFAULT_MAX_TEXT_CHARS = 500
DEFAULT_POLL_SECONDS = 30
DEFAULT_PUBLISH_BACKEND = "official"
DEFAULT_DEBUG_LOG_PATH = "state/threads-publisher-debug.jsonl"
DEFAULT_DEBUG_ARTIFACTS_DIR = "state/publish-debug"
DEFAULT_MAX_PUBLISH_LAG_SECONDS = 900
DEFAULT_MEDIA_LOOKUP_FIELDS = (
    "id,media_product_type,media_type,media_url,permalink,owner,username,"
    "text,topic_tag,timestamp,shortcode,thumbnail_url,children,is_quote_post"
)
DEFAULT_POST_INSIGHT_METRICS = "views,likes,replies,reposts,quotes,shares"
DEFAULT_USER_INSIGHT_METRICS = "views,likes,replies,reposts,quotes,clicks,followers_count"


def utc_timestamp() -> str:
    return now_utc().isoformat().replace("+00:00", "Z")


def sanitize_fragment(value: str) -> str:
    allowed = {"-", "_", "."}
    return "".join(char if char.isalnum() or char in allowed else "_" for char in value)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def count_queue_statuses(payload: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in payload.get("items", []):
        status = item.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def item_lag_seconds(item: dict[str, Any], current: datetime) -> float:
    publish_at = datetime.fromisoformat(item["publish_at_utc"].replace("Z", "+00:00"))
    return round((current - publish_at).total_seconds(), 3)


def format_interrupted_reason(item: dict[str, Any]) -> str:
    started_at = item.get("publish_started_at")
    session_id = item.get("publishing_session_id")
    hostname = item.get("publishing_hostname")
    pid = item.get("publishing_pid")
    parts = ["Previous publish attempt was interrupted"]
    details: list[str] = []
    if started_at:
        details.append(f"started_at={started_at}")
    if session_id:
        details.append(f"session_id={session_id}")
    if hostname:
        details.append(f"hostname={hostname}")
    if pid:
        details.append(f"pid={pid}")
    if details:
        parts.append(f"({', '.join(details)})")
    return " ".join(parts)


def snapshot_item(item: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(item, ensure_ascii=False, default=str))


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass
class PublishAttemptContext:
    attempt_id: str
    item_id: str
    attempt_number: int
    backend: str
    text_file: Path
    artifact_dir: Path
    scheduled_at_utc: str
    started_at_utc: str
    lag_seconds: float
    text_chars: int
    text_sha256: str
    relative_path: str

    def to_debug_payload(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "item_id": self.item_id,
            "attempt_number": self.attempt_number,
            "backend": self.backend,
            "text_file": str(self.text_file),
            "artifact_dir": str(self.artifact_dir),
            "scheduled_at_utc": self.scheduled_at_utc,
            "started_at_utc": self.started_at_utc,
            "lag_seconds": self.lag_seconds,
            "text_chars": self.text_chars,
            "text_sha256": self.text_sha256,
            "relative_path": self.relative_path,
        }


@dataclass
class DebugState:
    session_id: str
    log_path: Path
    artifacts_dir: Path
    capture_success_artifacts: bool
    hostname: str = field(default_factory=socket.gethostname)
    pid: int = field(default_factory=os.getpid)
    shutdown_requested: bool = False
    current_attempt_id: str | None = None
    shutdown_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def log(self, event: str, **data: Any) -> None:
        record = {
            "ts": utc_timestamp(),
            "event": event,
            "session_id": self.session_id,
            "pid": self.pid,
            "hostname": self.hostname,
            "current_attempt_id": self.current_attempt_id,
            **data,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def create_attempt_context(
        self,
        item: dict[str, Any],
        backend: str,
        current: datetime,
    ) -> PublishAttemptContext:
        text_file = Path(item["text_file"])
        text_content = text_file.read_text(encoding="utf-8")
        started_at_utc = current.isoformat().replace("+00:00", "Z")
        timestamp = current.strftime("%Y%m%dT%H%M%SZ")
        attempt_number = int(item.get("attempts", 0))
        attempt_id = (
            f"{timestamp}_{sanitize_fragment(item['id'])}_"
            f"try{attempt_number:02d}_{uuid.uuid4().hex[:8]}"
        )
        artifact_dir = self.artifacts_dir / current.strftime("%Y-%m-%d") / attempt_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        context = PublishAttemptContext(
            attempt_id=attempt_id,
            item_id=item["id"],
            attempt_number=attempt_number,
            backend=backend,
            text_file=text_file,
            artifact_dir=artifact_dir,
            scheduled_at_utc=item["publish_at_utc"],
            started_at_utc=started_at_utc,
            lag_seconds=item_lag_seconds(item, current),
            text_chars=len(text_content.strip()),
            text_sha256=sha256_text(text_content),
            relative_path=item.get("relative_path", ""),
        )
        write_json_file(artifact_dir / "attempt-context.json", context.to_debug_payload())
        (artifact_dir / "source.txt").write_text(text_content, encoding="utf-8")
        return context


def env_int(config: dict[str, str], key: str, default: int) -> int:
    value = env_get(config, key)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer value for {key}: {value}") from exc


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


def write_dotenv(path: Path, updates: dict[str, str]) -> None:
    existing = load_dotenv(path) if path.exists() else {}
    existing.update(updates)
    lines = [f"{key}={value}" for key, value in sorted(existing.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_config(root: Path) -> dict[str, str]:
    config = load_dotenv(root / ".env")
    for key, value in os.environ.items():
        config[key] = value
    return config


def env_get(config: dict[str, str], key: str, default: str | None = None) -> str | None:
    return config.get(key, default)


def env_bool(config: dict[str, str], key: str, default: bool) -> bool:
    value = env_get(config, key)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Invalid boolean value for {key}: {value}")


def resolve_path(root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def create_debug_state(config: dict[str, str], root: Path) -> DebugState:
    log_path = resolve_path(root, env_get(config, "THREADS_DEBUG_LOG_PATH", DEFAULT_DEBUG_LOG_PATH))
    artifacts_dir = resolve_path(root, env_get(config, "THREADS_DEBUG_ARTIFACTS_DIR", DEFAULT_DEBUG_ARTIFACTS_DIR))
    if log_path is None or artifacts_dir is None:
        raise RuntimeError("Failed to resolve debug log paths")

    debug_state = DebugState(
        session_id=f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
        log_path=log_path,
        artifacts_dir=artifacts_dir,
        capture_success_artifacts=env_bool(config, "THREADS_DEBUG_CAPTURE_SUCCESS_ARTIFACTS", default=False),
    )

    def log_exit() -> None:
        debug_state.log(
            "publisher_process_exit",
            shutdown_requested=debug_state.shutdown_requested,
        )

    atexit.register(log_exit)
    return debug_state


def install_signal_handlers(debug_state: DebugState) -> None:
    def handle_signal(signum: int, _frame: Any) -> None:
        signal_name = signal.Signals(signum).name
        debug_state.shutdown_requested = True
        debug_state.shutdown_event.set()
        debug_state.log(
            "signal_received",
            signal=signal_name,
        )

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


def resolve_publish_backend(config: dict[str, str], override: str | None = None) -> str:
    backend = override or env_get(config, "THREADS_PUBLISH_BACKEND", DEFAULT_PUBLISH_BACKEND) or DEFAULT_PUBLISH_BACKEND
    if backend not in {"official", "playwright"}:
        raise RuntimeError(f"Unsupported publish backend: {backend}")
    return backend


def now_utc() -> datetime:
    return datetime.now(UTC)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def slug_for_task(relative_path: Path) -> str:
    pieces = list(relative_path.with_suffix("").parts)
    return "__".join(pieces)


def clean_text_block(value: str) -> str:
    lines = [line.rstrip() for line in value.strip().splitlines()]
    cleaned: list[str] = []
    blank = False
    for line in lines:
        if line.strip():
            cleaned.append(line)
            blank = False
        elif not blank:
            cleaned.append("")
            blank = True
    return "\n".join(cleaned).strip()


def parse_post_markdown(markdown_text: str) -> tuple[str, str]:
    text_marker = "## Текст поста"
    cta_marker = "## CTA"

    if text_marker not in markdown_text:
        raise ValueError("Markdown post does not contain '## Текст поста'")

    _, after_text = markdown_text.split(text_marker, 1)
    if cta_marker in after_text:
        body_part, cta_part = after_text.split(cta_marker, 1)
    else:
        body_part, cta_part = after_text, ""

    body = clean_text_block(body_part)
    cta = clean_text_block(cta_part)
    if cta.startswith("CTA:"):
        cta = clean_text_block(cta.removeprefix("CTA:"))
    return body, cta


def render_plain_text(body: str, cta: str) -> str:
    if cta:
        return f"{body}\n\n{cta}\n"
    return f"{body}\n"


def export_txt(content_root: Path) -> list[Path]:
    created: list[Path] = []
    for md_path in sorted(content_root.rglob("*.md")):
        if md_path.name == "README.md":
            continue
        body, cta = parse_post_markdown(md_path.read_text(encoding="utf-8"))
        txt_path = md_path.with_suffix(".txt")
        txt_path.write_text(render_plain_text(body, cta), encoding="utf-8")
        created.append(txt_path)
    return created


def parse_publish_time(date_dir_name: str, time_file_name: str, timezone_name: str) -> tuple[str, str]:
    local_dt = datetime.strptime(
        f"{date_dir_name} {time_file_name}",
        "%Y-%m-%d %H-%M",
    ).replace(tzinfo=ZoneInfo(timezone_name))
    return local_dt.isoformat(), local_dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_queue(content_root: Path, queue_path: Path, timezone_name: str) -> dict[str, Any]:
    existing: dict[str, dict[str, Any]] = {}
    if queue_path.exists():
        try:
            old_payload = load_json(queue_path)
            for item in old_payload.get("items", []):
                if item.get("status") in {"posted", "publishing", "interrupted", "failed", "dry-run", "missed"}:
                    existing[item["id"]] = item
        except Exception:  # noqa: BLE001
            pass

    preserved = 0
    items: list[dict[str, Any]] = []
    for txt_path in sorted(content_root.rglob("*.txt")):
        if txt_path.name == "README.txt":
            continue
        if len(txt_path.parts) < 3:
            continue
        date_dir_name = txt_path.parent.name
        time_name = txt_path.stem
        try:
            publish_local, publish_utc = parse_publish_time(date_dir_name, time_name, timezone_name)
        except ValueError:
            continue
        relative_path = txt_path.relative_to(content_root)
        item_id = slug_for_task(relative_path)

        old = existing.get(item_id)
        if old and old.get("status") in {"posted", "interrupted", "failed", "dry-run", "missed"}:
            old["text_file"] = str(txt_path.resolve())
            items.append(old)
            preserved += 1
        elif old and old.get("status") == "publishing":
            old["text_file"] = str(txt_path.resolve())
            old["status"] = "interrupted"
            old["last_error"] = format_interrupted_reason(old)
            items.append(old)
            preserved += 1
        else:
            items.append(
                {
                    "id": item_id,
                    "relative_path": relative_path.as_posix(),
                    "text_file": str(txt_path.resolve()),
                    "publish_at_local": publish_local,
                    "publish_at_utc": publish_utc,
                    "timezone": timezone_name,
                    "status": "pending",
                    "attempts": 0,
                    "thread_ids": [],
                    "publish_backend": None,
                    "posted_at": None,
                    "last_error": None,
                }
            )

    payload = {
        "version": 1,
        "generated_at": now_utc().isoformat().replace("+00:00", "Z"),
        "content_root": str(content_root.resolve()),
        "timezone": timezone_name,
        "items": items,
    }
    atomic_write_json(queue_path, payload)
    if preserved:
        print(f"Preserved {preserved} already-published item(s).")
    return payload


def reconcile_interrupted_items(
    payload: dict[str, Any],
    debug_state: DebugState | None = None,
) -> int:
    updated = 0
    for item in payload.get("items", []):
        if item.get("status") != "publishing":
            continue
        item["status"] = "interrupted"
        item["last_error"] = format_interrupted_reason(item)
        updated += 1
        if debug_state:
            debug_state.log(
                "reconcile_interrupted_item",
                item_id=item.get("id"),
                publish_started_at=item.get("publish_started_at"),
                publishing_session_id=item.get("publishing_session_id"),
                publishing_hostname=item.get("publishing_hostname"),
                publishing_pid=item.get("publishing_pid"),
                reason=item.get("last_error"),
            )
    return updated


def mark_missed_items(
    payload: dict[str, Any],
    current: datetime,
    max_publish_lag_seconds: int,
    debug_state: DebugState | None = None,
) -> int:
    updated = 0
    for item in payload.get("items", []):
        if item.get("status") != "pending":
            continue
        lag_seconds = item_lag_seconds(item, current)
        if lag_seconds <= max_publish_lag_seconds:
            continue
        item["status"] = "missed"
        item["last_error"] = (
            f"Missed scheduled slot by {lag_seconds} second(s), "
            f"above max lag {max_publish_lag_seconds}"
        )
        updated += 1
        if debug_state:
            debug_state.log(
                "mark_item_missed",
                item_id=item.get("id"),
                publish_at_utc=item.get("publish_at_utc"),
                lag_seconds=lag_seconds,
                max_publish_lag_seconds=max_publish_lag_seconds,
            )
    return updated


def find_due_items(payload: dict[str, Any], current: datetime) -> list[dict[str, Any]]:
    due: list[dict[str, Any]] = []
    for item in payload.get("items", []):
        status = item.get("status")
        if status not in {"pending"}:
            continue
        publish_at = datetime.fromisoformat(item["publish_at_utc"].replace("Z", "+00:00"))
        if publish_at <= current:
            due.append(item)
    due.sort(key=lambda item: item["publish_at_utc"])
    return due


def split_long_post(text: str, max_chars: int) -> list[str]:
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        raise ValueError("Text file is empty")

    paragraphs = normalized.split("\n\n")
    segments: list[str] = []
    current = ""

    def push(chunk: str) -> None:
        nonlocal current
        if not chunk.strip():
            return
        candidate = chunk if not current else f"{current}\n\n{chunk}"
        if len(candidate) <= max_chars:
            current = candidate
            return

        if current:
            segments.append(current)
            current = ""

        if len(chunk) <= max_chars:
            current = chunk
            return

        for piece in split_chunk(chunk, max_chars):
            if len(piece) > max_chars:
                raise ValueError("Split algorithm produced a chunk above limit")
            if current:
                segments.append(current)
            current = piece

    for paragraph in paragraphs:
        push(paragraph.strip())

    if current:
        segments.append(current)

    return segments


def split_chunk(chunk: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    remaining = chunk.strip()
    while remaining:
        if len(remaining) <= max_chars:
            pieces.append(remaining)
            break

        break_at = find_breakpoint(remaining, max_chars)
        pieces.append(remaining[:break_at].rstrip())
        remaining = remaining[break_at:].lstrip()
    return pieces


def find_breakpoint(text: str, max_chars: int) -> int:
    window = text[: max_chars + 1]
    preferred = ["\n- ", "\n", ". ", "! ", "? ", "; ", ": ", ", ", " "]
    for token in preferred:
        index = window.rfind(token)
        if index > 60:
            return index + (1 if token.startswith("\n") else len(token))
    return max_chars


def parse_csv_values(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def normalize_threads_post_path(value: str) -> str | None:
    parsed = urllib.parse.urlparse(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    if "/post/" not in path:
        return None
    return path


def extract_shortcode(value: str) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None

    path = normalize_threads_post_path(stripped)
    if path:
        parts = [part for part in path.split("/") if part]
        try:
            post_index = parts.index("post")
        except ValueError:
            return None
        if post_index + 1 < len(parts):
            return parts[post_index + 1]
        return None

    if "/" in stripped or "@" in stripped or " " in stripped:
        return None
    return stripped


def parse_unix_timestamp_arg(
    value: str | None,
    timezone_name: str,
    *,
    end_of_day: bool = False,
) -> str | None:
    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return stripped

    date_only = False
    try:
        if len(stripped) == 10:
            dt = datetime.strptime(stripped, "%Y-%m-%d")
            date_only = True
        else:
            dt = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(
            f"Unsupported timestamp value {value!r}. Use unix seconds or ISO date/datetime."
        ) from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
    if date_only and end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return str(int(dt.timestamp()))


@dataclass
class ThreadsApi:
    access_token: str
    api_base: str

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        url = f"{self.api_base.rstrip('/')}/{path.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode(params, doseq=True)
            url = f"{url}?{query}"

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        request = urllib.request.Request(url, method=method.upper(), headers=headers)

        for attempt in range(1, retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = response.read().decode("utf-8")
                    return json.loads(data) if data else {}
            except urllib.error.HTTPError as exc:
                payload_text = exc.read().decode("utf-8", errors="replace")
                if attempt < retries and exc.code in {429, 500, 502, 503, 504}:
                    time.sleep(attempt * 2)
                    continue
                raise RuntimeError(f"HTTP {exc.code}: {payload_text}") from exc
            except urllib.error.URLError as exc:
                if attempt < retries:
                    time.sleep(attempt * 2)
                    continue
                raise RuntimeError(f"Network error: {exc}") from exc

        raise RuntimeError("Request failed after retries")

    def create_text_container(
        self,
        text: str,
        reply_to_id: str | None = None,
        reply_control: str = "everyone",
    ) -> str:
        params: dict[str, Any] = {
            "media_type": "TEXT",
            "text": text,
            "reply_control": reply_control,
        }
        if reply_to_id:
            params["reply_to_id"] = reply_to_id
        payload = self._request("POST", "me/threads", params=params)
        container_id = payload.get("id")
        if not container_id:
            raise RuntimeError(f"Threads API did not return a container id: {payload}")
        return container_id

    def publish_container(self, container_id: str) -> str:
        payload = self._request("POST", "me/threads_publish", params={"creation_id": container_id})
        thread_id = payload.get("id")
        if not thread_id:
            raise RuntimeError(f"Threads API did not return a post id: {payload}")
        return thread_id

    def whoami(self) -> dict[str, Any]:
        return self._request("GET", "me", params={"fields": "id,username,name,threads_profile_picture_url"})

    def get_media(self, media_id: str, fields: str = DEFAULT_MEDIA_LOOKUP_FIELDS) -> dict[str, Any]:
        return self._request("GET", media_id, params={"fields": fields})

    def list_threads(
        self,
        user_id: str = "me",
        *,
        fields: str = DEFAULT_MEDIA_LOOKUP_FIELDS,
        since: str | None = None,
        until: str | None = None,
        limit: int = 25,
        after: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "fields": fields,
            "limit": limit,
        }
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        if after:
            params["after"] = after
        return self._request("GET", f"{user_id}/threads", params=params)

    def get_media_insights(self, media_id: str, metrics: list[str]) -> dict[str, Any]:
        return self._request("GET", f"{media_id}/insights", params={"metric": ",".join(metrics)})

    def get_user_insights(
        self,
        user_id: str,
        metrics: list[str],
        *,
        since: str | None = None,
        until: str | None = None,
        breakdown: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"metric": ",".join(metrics)}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        if breakdown:
            params["breakdown"] = breakdown
        return self._request("GET", f"{user_id}/threads_insights", params=params)


def create_threads_api(config: dict[str, str]) -> ThreadsApi:
    require_config(config, "THREADS_ACCESS_TOKEN")
    return ThreadsApi(
        access_token=env_get(config, "THREADS_ACCESS_TOKEN") or "",
        api_base=env_get(config, "THREADS_API_BASE", DEFAULT_API_BASE) or DEFAULT_API_BASE,
    )


def resolve_post_reference(
    api: ThreadsApi,
    reference: str,
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 25,
    max_pages: int = 10,
) -> dict[str, Any]:
    ref = reference.strip()
    if not ref:
        raise RuntimeError("Post reference must not be empty")

    if ref.isdigit():
        media = api.get_media(ref)
        return {
            "resolution": "media-id",
            "input": reference,
            "lookup": {"pages_scanned": 0, "per_page_limit": limit, "max_pages": max_pages},
            "media": media,
        }

    target_path = normalize_threads_post_path(ref)
    target_shortcode = extract_shortcode(ref)
    if target_path is None and target_shortcode is None:
        raise RuntimeError(
            "Unsupported post reference. Use a numeric media id, post URL, or shortcode."
        )

    after: str | None = None
    pages_scanned = 0
    while pages_scanned < max_pages:
        pages_scanned += 1
        payload = api.list_threads(
            fields=DEFAULT_MEDIA_LOOKUP_FIELDS,
            since=since,
            until=until,
            limit=limit,
            after=after,
        )
        for media in payload.get("data", []):
            media_path = normalize_threads_post_path(str(media.get("permalink", "")))
            media_shortcode = str(media.get("shortcode") or "").strip() or extract_shortcode(
                str(media.get("permalink", ""))
            )
            if target_path and media_path == target_path:
                return {
                    "resolution": "permalink",
                    "input": reference,
                    "lookup": {"pages_scanned": pages_scanned, "per_page_limit": limit, "max_pages": max_pages},
                    "media": media,
                }
            if target_shortcode and media_shortcode and media_shortcode == target_shortcode:
                return {
                    "resolution": "shortcode",
                    "input": reference,
                    "lookup": {"pages_scanned": pages_scanned, "per_page_limit": limit, "max_pages": max_pages},
                    "media": media,
                }

        after = payload.get("paging", {}).get("cursors", {}).get("after")
        if not after:
            break

    raise RuntimeError(
        "Could not resolve post reference via Threads API. "
        "Try a direct media id or increase --lookup-pages/--lookup-limit."
    )


def list_recent_threads(
    api: ThreadsApi,
    *,
    since: str | None,
    until: str | None,
    limit: int,
    max_pages: int,
    fields: str,
) -> dict[str, Any]:
    after: str | None = None
    pages_scanned = 0
    collected: list[dict[str, Any]] = []

    while pages_scanned < max_pages:
        pages_scanned += 1
        payload = api.list_threads(
            fields=fields,
            since=since,
            until=until,
            limit=limit,
            after=after,
        )
        collected.extend(payload.get("data", []))
        after = payload.get("paging", {}).get("cursors", {}).get("after")
        if not after:
            break

    return {
        "data": collected,
        "paging": {
            "pages_scanned": pages_scanned,
            "has_next_page": bool(after),
            "next_after": after,
        },
    }


def publish_text_file_official(
    api: ThreadsApi,
    text_file: Path,
    max_chars: int,
    dry_run: bool = False,
    debug_state: DebugState | None = None,
    attempt_context: PublishAttemptContext | None = None,
) -> list[str]:
    text = text_file.read_text(encoding="utf-8").strip()
    segments = split_long_post(text, max_chars=max_chars)
    if debug_state and attempt_context:
        debug_state.log(
            "official_publish_prepare",
            **attempt_context.to_debug_payload(),
            segment_count=len(segments),
            segment_lengths=[len(segment) for segment in segments],
        )
    if dry_run:
        print(f"[dry-run] {text_file} -> {len(segments)} segment(s)")
        return []

    published_ids: list[str] = []
    reply_to_id: str | None = None
    for index, segment in enumerate(segments, start=1):
        container_id = api.create_text_container(segment, reply_to_id=reply_to_id)
        thread_id = api.publish_container(container_id)
        published_ids.append(thread_id)
        if debug_state and attempt_context:
            debug_state.log(
                "official_publish_segment_done",
                **attempt_context.to_debug_payload(),
                segment_index=index,
                segment_chars=len(segment),
                container_id=container_id,
                thread_id=thread_id,
                reply_to_id=reply_to_id,
            )
        reply_to_id = thread_id
        time.sleep(1)
    return published_ids


def build_playwright_publish_command(
    config: dict[str, str],
    root: Path,
    text_file: Path,
    max_chars: int,
) -> list[str]:
    node_bin = env_get(config, "THREADS_NODE_BIN", "node") or "node"
    script_path = (root / "scripts" / "threads_publish_playwright.mjs").resolve()
    command = [node_bin, str(script_path), "--max-chars", str(max_chars)]

    storage_state = resolve_path(root, env_get(config, "THREADS_PLAYWRIGHT_STORAGE_STATE"))
    profile_dir = resolve_path(root, env_get(config, "THREADS_PLAYWRIGHT_PROFILE_DIR"))
    if storage_state:
        command += ["--storage-state", str(storage_state)]
    elif profile_dir:
        command += ["--profile-dir", str(profile_dir)]

    browser_executable = env_get(config, "THREADS_PLAYWRIGHT_BROWSER_EXECUTABLE")
    if browser_executable:
        command += ["--browser-executable", browser_executable]

    browser_channel = env_get(config, "THREADS_PLAYWRIGHT_BROWSER_CHANNEL")
    if browser_channel:
        command += ["--browser-channel", browser_channel]

    profile_url = env_get(config, "THREADS_PLAYWRIGHT_PROFILE_URL")
    if profile_url:
        command += ["--profile-url", profile_url]

    command.append("--headless" if env_bool(config, "THREADS_PLAYWRIGHT_HEADLESS", default=False) else "--headed")
    command.append(str(text_file))
    return command


def publish_text_file_playwright(
    config: dict[str, str],
    root: Path,
    text_file: Path,
    max_chars: int,
    dry_run: bool = False,
    debug_state: DebugState | None = None,
    attempt_context: PublishAttemptContext | None = None,
) -> list[str]:
    text = text_file.read_text(encoding="utf-8").strip()
    segments = split_long_post(text, max_chars=max_chars)
    if dry_run:
        print(f"[dry-run] {text_file} -> {len(segments)} segment(s)")
        return []

    storage_state = resolve_path(root, env_get(config, "THREADS_PLAYWRIGHT_STORAGE_STATE"))
    profile_dir = resolve_path(root, env_get(config, "THREADS_PLAYWRIGHT_PROFILE_DIR"))
    if not storage_state and not profile_dir:
        raise RuntimeError(
            "Playwright backend requires THREADS_PLAYWRIGHT_STORAGE_STATE or THREADS_PLAYWRIGHT_PROFILE_DIR"
        )

    env = os.environ.copy()
    env.update(config)
    if debug_state and attempt_context:
        env["THREADS_DEBUG_SESSION_ID"] = debug_state.session_id
        env["THREADS_DEBUG_LOG_PATH"] = str(debug_state.log_path)
        env["THREADS_DEBUG_ATTEMPT_ID"] = attempt_context.attempt_id
        env["THREADS_DEBUG_ATTEMPT_DIR"] = str(attempt_context.artifact_dir)
        env["THREADS_DEBUG_CAPTURE_SUCCESS_ARTIFACTS"] = "true" if debug_state.capture_success_artifacts else "false"
    command = build_playwright_publish_command(config, root, text_file, max_chars)
    started_monotonic = time.monotonic()
    if debug_state and attempt_context:
        debug_state.log(
            "playwright_publish_subprocess_start",
            **attempt_context.to_debug_payload(),
            command=command,
            segment_count=len(segments),
            segment_lengths=[len(segment) for segment in segments],
        )
    completed = subprocess.run(
        command,
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    duration_seconds = round(time.monotonic() - started_monotonic, 3)
    if attempt_context:
        (attempt_context.artifact_dir / "playwright-stdout.log").write_text(completed.stdout, encoding="utf-8")
        (attempt_context.artifact_dir / "playwright-stderr.log").write_text(completed.stderr, encoding="utf-8")
    if debug_state and attempt_context:
        debug_state.log(
            "playwright_publish_subprocess_exit",
            **attempt_context.to_debug_payload(),
            returncode=completed.returncode,
            duration_seconds=duration_seconds,
            stdout_log=str(attempt_context.artifact_dir / "playwright-stdout.log"),
            stderr_log=str(attempt_context.artifact_dir / "playwright-stderr.log"),
        )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise RuntimeError(f"Playwright publish failed: {details}")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Playwright publisher returned non-JSON output: {completed.stdout.strip()}") from exc
    if attempt_context:
        write_json_file(attempt_context.artifact_dir / "playwright-result.json", payload)

    results = payload.get("results", [])
    if not results:
        raise RuntimeError(f"Playwright publisher returned no results: {payload}")

    first_result = results[0]
    post_url = first_result.get("postUrl")
    if debug_state and attempt_context:
        debug_state.log(
            "playwright_publish_result_parsed",
            **attempt_context.to_debug_payload(),
            post_url=post_url,
            result_count=len(results),
            resolution=first_result.get("resolution"),
            candidate_post_urls=first_result.get("candidatePostUrls"),
            baseline_post_urls=first_result.get("baselinePostUrls"),
            poll_attempts=first_result.get("pollAttempts"),
        )
    return [post_url] if post_url else []


def publish_text_file(
    config: dict[str, str],
    root: Path,
    backend: str,
    text_file: Path,
    max_chars: int,
    dry_run: bool = False,
    debug_state: DebugState | None = None,
    attempt_context: PublishAttemptContext | None = None,
) -> list[str]:
    if backend == "official":
        api = ThreadsApi(
            access_token=env_get(config, "THREADS_ACCESS_TOKEN", "") or "",
            api_base=env_get(config, "THREADS_API_BASE", DEFAULT_API_BASE) or DEFAULT_API_BASE,
        )
        return publish_text_file_official(
            api,
            text_file,
            max_chars=max_chars,
            dry_run=dry_run,
            debug_state=debug_state,
            attempt_context=attempt_context,
        )
    if backend == "playwright":
        return publish_text_file_playwright(
            config,
            root,
            text_file,
            max_chars=max_chars,
            dry_run=dry_run,
            debug_state=debug_state,
            attempt_context=attempt_context,
        )
    raise RuntimeError(f"Unsupported publish backend: {backend}")


@contextmanager
def lock_file(path: Path, debug_state: DebugState | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        handle.seek(0)
        existing_metadata = handle.read().strip()
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            if debug_state:
                debug_state.log(
                "lock_busy",
                lock_path=str(path),
                existing_metadata=existing_metadata or None,
            )
            raise RuntimeError(f"Another publisher process already holds lock {path}") from exc
        metadata = {
            "session_id": debug_state.session_id if debug_state else None,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "acquired_at": utc_timestamp(),
            "lock_path": str(path),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        yield


def require_config(config: dict[str, str], *keys: str) -> None:
    missing = [key for key in keys if not env_get(config, key)]
    if missing:
        raise RuntimeError(f"Missing required config: {', '.join(missing)}")


def queue_run(
    config: dict[str, str],
    root: Path,
    queue_path: Path,
    backend: str,
    interval_seconds: int,
    dry_run: bool,
    once: bool,
) -> None:
    if backend == "official" and not dry_run:
        require_config(config, "THREADS_ACCESS_TOKEN")
    max_chars = int(env_get(config, "THREADS_MAX_TEXT_CHARS", str(DEFAULT_MAX_TEXT_CHARS)) or DEFAULT_MAX_TEXT_CHARS)
    max_publish_lag_seconds = env_int(
        config,
        "THREADS_MAX_PUBLISH_LAG_SECONDS",
        DEFAULT_MAX_PUBLISH_LAG_SECONDS,
    )
    debug_state = create_debug_state(config, root)
    install_signal_handlers(debug_state)
    debug_state.log(
        "publisher_run_start",
        queue_path=str(queue_path),
        backend=backend,
        dry_run=dry_run,
        once=once,
        interval_seconds=interval_seconds,
        max_chars=max_chars,
        max_publish_lag_seconds=max_publish_lag_seconds,
        argv=sys.argv,
    )

    lock_path = queue_path.parent / "threads-publisher.lock"
    with lock_file(lock_path, debug_state=debug_state):
        debug_state.log(
            "lock_acquired",
            queue_path=str(queue_path),
            lock_path=str(lock_path),
        )
        payload = load_json(queue_path)
        reconciled = reconcile_interrupted_items(payload, debug_state=debug_state)
        if reconciled:
            atomic_write_json(queue_path, payload)
            debug_state.log(
                "reconcile_interrupted_items_done",
                queue_path=str(queue_path),
                updated_count=reconciled,
            )
        while True:
            if debug_state.shutdown_requested:
                debug_state.log("shutdown_before_queue_scan")
                return

            payload = load_json(queue_path)
            current = now_utc()
            missed_count = mark_missed_items(
                payload,
                current,
                max_publish_lag_seconds=max_publish_lag_seconds,
                debug_state=debug_state,
            )
            if missed_count:
                atomic_write_json(queue_path, payload)
                debug_state.log(
                    "missed_items_persisted",
                    queue_path=str(queue_path),
                    updated_count=missed_count,
                )
            due_items = find_due_items(payload, current)
            debug_state.log(
                "queue_scan",
                queue_path=str(queue_path),
                current_utc=current.isoformat().replace("+00:00", "Z"),
                status_counts=count_queue_statuses(payload),
                due_count=len(due_items),
                due_items=[
                    {
                        "id": item["id"],
                        "publish_at_utc": item["publish_at_utc"],
                        "lag_seconds": item_lag_seconds(item, current),
                        "attempts": item.get("attempts", 0),
                    }
                    for item in due_items
                ],
            )

            if not due_items and once:
                debug_state.log("queue_empty_once_exit", queue_path=str(queue_path))
                print("No due tasks.")
                return

            for item in due_items:
                if debug_state.shutdown_requested:
                    debug_state.log(
                        "shutdown_before_publish_attempt",
                        queue_path=str(queue_path),
                        next_item_id=item["id"],
                    )
                    return

                started_at = now_utc()
                item["attempts"] = int(item.get("attempts", 0)) + 1
                attempt_context = debug_state.create_attempt_context(item, backend, started_at)
                debug_state.current_attempt_id = attempt_context.attempt_id
                write_json_file(attempt_context.artifact_dir / "queue-item-before.json", snapshot_item(item))
                debug_state.log(
                    "publish_attempt_mark_publishing",
                    **attempt_context.to_debug_payload(),
                    queue_path=str(queue_path),
                )
                item["status"] = "publishing"
                item["last_error"] = None
                item["publish_backend"] = backend
                item["publish_started_at"] = attempt_context.started_at_utc
                item["publishing_session_id"] = debug_state.session_id
                item["publishing_pid"] = debug_state.pid
                item["publishing_hostname"] = debug_state.hostname
                atomic_write_json(queue_path, payload)
                debug_state.log(
                    "queue_item_status_written",
                    **attempt_context.to_debug_payload(),
                    queue_path=str(queue_path),
                    status=item["status"],
                )

                text_file = Path(item["text_file"])
                print(f"Publishing {item['id']} from {text_file} via {backend}")
                try:
                    try:
                        thread_ids = publish_text_file(
                            config=config,
                            root=root,
                            backend=backend,
                            text_file=text_file,
                            max_chars=max_chars,
                            dry_run=dry_run,
                            debug_state=debug_state,
                            attempt_context=attempt_context,
                        )
                        item["status"] = "posted" if not dry_run else "dry-run"
                        item["thread_ids"] = thread_ids
                        item["publish_backend"] = backend
                        item["posted_at"] = now_utc().isoformat().replace("+00:00", "Z")
                        write_json_file(attempt_context.artifact_dir / "queue-item-after.json", snapshot_item(item))
                        debug_state.log(
                            "publish_attempt_succeeded",
                            **attempt_context.to_debug_payload(),
                            status=item["status"],
                            thread_ids=thread_ids,
                            posted_at=item["posted_at"],
                        )
                    except Exception as exc:  # noqa: BLE001
                        item["status"] = "failed"
                        item["last_error"] = str(exc)
                        item["publish_backend"] = backend
                        write_json_file(attempt_context.artifact_dir / "queue-item-after.json", snapshot_item(item))
                        atomic_write_json(queue_path, payload)
                        debug_state.log(
                            "publish_attempt_failed",
                            **attempt_context.to_debug_payload(),
                            status=item["status"],
                            error=str(exc),
                        )
                        print(f"Failed: {item['id']} -> {exc}", file=sys.stderr)
                        continue

                    atomic_write_json(queue_path, payload)
                    debug_state.log(
                        "queue_item_status_persisted",
                        **attempt_context.to_debug_payload(),
                        status=item["status"],
                        queue_path=str(queue_path),
                    )
                    print(f"Done: {item['id']}")
                except Exception as exc:  # noqa: BLE001
                    debug_state.log(
                        "publish_attempt_internal_error",
                        **attempt_context.to_debug_payload(),
                        error=str(exc),
                    )
                    raise
                finally:
                    debug_state.current_attempt_id = None

            if debug_state.shutdown_requested:
                debug_state.log("shutdown_after_publish_cycle")
                return

            debug_state.log(
                "sleep_before_next_scan",
                interval_seconds=interval_seconds,
            )
            debug_state.shutdown_event.wait(interval_seconds)


def build_authorize_url(config: dict[str, str], state: str | None = None) -> str:
    require_config(config, "THREADS_APP_ID", "THREADS_REDIRECT_URI")
    state_value = state or str(uuid.uuid4())
    params = {
        "client_id": env_get(config, "THREADS_APP_ID"),
        "redirect_uri": env_get(config, "THREADS_REDIRECT_URI"),
        "scope": env_get(config, "THREADS_SCOPES", DEFAULT_SCOPES),
        "response_type": "code",
        "state": state_value,
    }
    return f"https://threads.net/oauth/authorize?{urllib.parse.urlencode(params)}"


def exchange_code(config: dict[str, str], code: str) -> dict[str, Any]:
    require_config(config, "THREADS_APP_ID", "THREADS_APP_SECRET", "THREADS_REDIRECT_URI")
    form = urllib.parse.urlencode(
        {
            "client_id": env_get(config, "THREADS_APP_ID"),
            "client_secret": env_get(config, "THREADS_APP_SECRET"),
            "grant_type": "authorization_code",
            "redirect_uri": env_get(config, "THREADS_REDIRECT_URI"),
            "code": code,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://graph.threads.net/oauth/access_token",
        data=form,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def exchange_long_lived_token(config: dict[str, str], short_lived_token: str) -> dict[str, Any]:
    require_config(config, "THREADS_APP_SECRET")
    params = urllib.parse.urlencode(
        {
            "grant_type": "th_exchange_token",
            "client_secret": env_get(config, "THREADS_APP_SECRET"),
            "access_token": short_lived_token,
        }
    )
    url = f"https://graph.threads.net/access_token?{params}"
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def refresh_long_lived_token(long_lived_token: str) -> dict[str, Any]:
    params = urllib.parse.urlencode(
        {
            "grant_type": "th_refresh_token",
            "access_token": long_lived_token,
        }
    )
    url = f"https://graph.threads.net/refresh_access_token?{params}"
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def exchange_full_token_flow(config: dict[str, str], code: str) -> dict[str, Any]:
    short_payload = exchange_code(config, code)
    short_token = short_payload.get("access_token")
    if not short_token:
        raise RuntimeError(f"Short-lived token was not returned: {short_payload}")

    long_payload = exchange_long_lived_token(config, short_token)
    long_token = long_payload.get("access_token")
    if not long_token:
        raise RuntimeError(f"Long-lived token was not returned: {long_payload}")

    api = ThreadsApi(
        access_token=long_token,
        api_base=env_get(config, "THREADS_API_BASE", DEFAULT_API_BASE) or DEFAULT_API_BASE,
    )
    profile = api.whoami()
    return {
        "short_lived": short_payload,
        "long_lived": long_payload,
        "profile": profile,
    }


def save_long_lived_token_to_env(
    root: Path,
    config: dict[str, str],
    access_token: str,
    expires_in: str | int | None,
) -> None:
    if not access_token:
        raise RuntimeError("Long-lived token payload does not contain access_token")

    write_dotenv(
        root / ".env",
        {
            "THREADS_APP_ID": env_get(config, "THREADS_APP_ID", "") or "",
            "THREADS_APP_SECRET": env_get(config, "THREADS_APP_SECRET", "") or "",
            "THREADS_REDIRECT_URI": env_get(config, "THREADS_REDIRECT_URI", "") or "",
            "THREADS_CALLBACK_BIND_URI": env_get(config, "THREADS_CALLBACK_BIND_URI", "") or "",
            "THREADS_API_BASE": env_get(config, "THREADS_API_BASE", DEFAULT_API_BASE) or DEFAULT_API_BASE,
            "THREADS_SCOPES": env_get(config, "THREADS_SCOPES", DEFAULT_SCOPES) or DEFAULT_SCOPES,
            "THREADS_TIMEZONE": env_get(config, "THREADS_TIMEZONE", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE,
            "THREADS_MAX_TEXT_CHARS": env_get(config, "THREADS_MAX_TEXT_CHARS", str(DEFAULT_MAX_TEXT_CHARS))
            or str(DEFAULT_MAX_TEXT_CHARS),
            "THREADS_ACCESS_TOKEN": access_token,
            "THREADS_ACCESS_TOKEN_EXPIRES_IN": str(expires_in or ""),
            "THREADS_ACCESS_TOKEN_REFRESHED_AT": now_utc().isoformat().replace("+00:00", "Z"),
        },
    )


def save_auth_payload_to_env(root: Path, config: dict[str, str], payload: dict[str, Any]) -> None:
    save_long_lived_token_to_env(
        root,
        config,
        payload["long_lived"]["access_token"],
        payload["long_lived"].get("expires_in", ""),
    )


def extract_code_from_redirect_value(value: str) -> str:
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme and parsed.netloc:
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        if code:
            return code
        error_value = params.get("error", [None])[0]
        if error_value:
            raise RuntimeError(f"OAuth redirect returned error: {error_value}")
        raise RuntimeError("Redirect URL does not contain ?code=")
    return value.strip()


def wait_for_oauth_callback(bind_uri: str, timeout_seconds: int) -> str:
    parsed = urllib.parse.urlparse(bind_uri)
    if parsed.scheme != "http":
        raise RuntimeError("Callback listener currently supports only http:// bind URIs")
    if not parsed.hostname or parsed.port is None:
        raise RuntimeError("Callback bind URI must include hostname and port")

    callback_path = parsed.path or "/"
    result: dict[str, str] = {}
    finished = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            request_url = urllib.parse.urlparse(self.path)
            if request_url.path != callback_path:
                self.send_response(404)
                self.end_headers()
                return

            params = urllib.parse.parse_qs(request_url.query)
            if "error" in params:
                result["error"] = params["error"][0]
                body = "OAuth returned an error. You can close this tab."
            elif "code" in params:
                result["code"] = params["code"][0]
                body = "Threads OAuth completed. You can close this tab."
            else:
                body = "No code was found in callback."

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            finished.set()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    server = http.server.ThreadingHTTPServer((parsed.hostname, parsed.port), CallbackHandler)
    server.timeout = 1

    try:
        start = time.time()
        while time.time() - start < timeout_seconds and not finished.is_set():
            server.handle_request()
    finally:
        server.server_close()

    if "error" in result:
        raise RuntimeError(f"OAuth callback returned error: {result['error']}")
    if "code" not in result:
        raise RuntimeError("Timed out waiting for OAuth callback")
    return result["code"]


def command_export_txt(args: argparse.Namespace, root: Path) -> None:
    created = export_txt((root / args.content_root).resolve())
    print(f"Exported {len(created)} txt file(s).")


def command_build_queue(args: argparse.Namespace, root: Path) -> None:
    content_root = (root / args.content_root).resolve()
    queue_path = (root / args.queue_file).resolve()
    payload = build_queue(content_root, queue_path, args.timezone)
    print(f"Queued {len(payload['items'])} task(s) into {queue_path}")


def command_run(args: argparse.Namespace, root: Path) -> None:
    config = get_config(root)
    backend = resolve_publish_backend(config, args.backend)
    queue_run(
        config=config,
        root=root,
        queue_path=(root / args.queue_file).resolve(),
        backend=backend,
        interval_seconds=args.interval_seconds,
        dry_run=args.dry_run,
        once=args.once,
    )


def command_oauth_url(args: argparse.Namespace, root: Path) -> None:
    config = get_config(root)
    print(build_authorize_url(config, state=args.state))


def command_exchange_code(args: argparse.Namespace, root: Path) -> None:
    config = get_config(root)
    payload = exchange_code(config, args.code)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_exchange_long_token(args: argparse.Namespace, root: Path) -> None:
    config = get_config(root)
    payload = exchange_long_lived_token(config, args.short_lived_token)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_exchange_full_code(args: argparse.Namespace, root: Path) -> None:
    config = get_config(root)
    payload = exchange_full_token_flow(config, args.code)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.write_env:
        save_auth_payload_to_env(root, config, payload)
        print("\nSaved Threads credentials and long-lived token into .env")


def command_refresh_token(args: argparse.Namespace, root: Path) -> None:
    config = get_config(root)
    long_lived_token = args.long_lived_token or env_get(config, "THREADS_ACCESS_TOKEN")
    if not long_lived_token:
        raise RuntimeError("Missing long-lived token. Pass it explicitly or set THREADS_ACCESS_TOKEN.")
    payload = refresh_long_lived_token(long_lived_token)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.write_env:
        save_long_lived_token_to_env(root, config, payload.get("access_token", ""), payload.get("expires_in", ""))
        print("Saved refreshed Threads access token into .env", file=sys.stderr)


def command_auth_start(args: argparse.Namespace, root: Path) -> None:
    config = get_config(root)
    require_config(config, "THREADS_APP_ID", "THREADS_APP_SECRET", "THREADS_REDIRECT_URI")

    authorize_url = build_authorize_url(config, state=args.state)
    print("Open this URL and complete Threads authorization:\n")
    print(authorize_url)
    print("")

    if args.listen:
        callback_bind_uri = (
            env_get(config, "THREADS_CALLBACK_BIND_URI")
            or env_get(config, "THREADS_REDIRECT_URI")
            or ""
        )
        print(f"Waiting for callback on {callback_bind_uri} ...")
        if callback_bind_uri != (env_get(config, "THREADS_REDIRECT_URI") or ""):
            print(f"Public redirect URI: {env_get(config, 'THREADS_REDIRECT_URI')}")
        code = wait_for_oauth_callback(callback_bind_uri, args.timeout_seconds)
    else:
        print("Paste the full redirected URL or just the `code` value, then press Enter:")
        pasted = input().strip()
        code = extract_code_from_redirect_value(pasted)

    payload = exchange_full_token_flow(config, code)
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.write_env:
        save_auth_payload_to_env(root, config, payload)
        print("\nSaved Threads credentials and long-lived token into .env")


def command_whoami(_args: argparse.Namespace, root: Path) -> None:
    config = get_config(root)
    api = create_threads_api(config)
    print(json.dumps(api.whoami(), ensure_ascii=False, indent=2))


def command_list_posts(args: argparse.Namespace, root: Path) -> None:
    config = get_config(root)
    api = create_threads_api(config)
    payload = list_recent_threads(
        api,
        since=args.since,
        until=args.until,
        limit=args.limit,
        max_pages=args.max_pages,
        fields=args.fields,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_post_insights(args: argparse.Namespace, root: Path) -> None:
    config = get_config(root)
    api = create_threads_api(config)
    resolved = resolve_post_reference(
        api,
        args.post_ref,
        since=args.lookup_since,
        until=args.lookup_until,
        limit=args.lookup_limit,
        max_pages=args.lookup_pages,
    )
    media_id = str(resolved["media"]["id"])
    payload = {
        "resolved": resolved,
        "insights": api.get_media_insights(media_id, parse_csv_values(args.metrics)),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_user_insights(args: argparse.Namespace, root: Path) -> None:
    config = get_config(root)
    api = create_threads_api(config)
    profile = api.whoami()
    user_id = args.user_id if args.user_id and args.user_id != "me" else str(profile["id"])
    timezone_name = env_get(config, "THREADS_TIMEZONE", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
    payload = {
        "profile": profile,
        "insights": api.get_user_insights(
            user_id,
            parse_csv_values(args.metrics),
            since=parse_unix_timestamp_arg(args.since, timezone_name, end_of_day=False),
            until=parse_unix_timestamp_arg(args.until, timezone_name, end_of_day=True),
            breakdown=args.breakdown,
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_publish_file(args: argparse.Namespace, root: Path) -> None:
    config = get_config(root)
    backend = resolve_publish_backend(config, args.backend)
    if backend == "official" and not args.dry_run:
        require_config(config, "THREADS_ACCESS_TOKEN")
    max_chars = int(env_get(config, "THREADS_MAX_TEXT_CHARS", str(DEFAULT_MAX_TEXT_CHARS)) or DEFAULT_MAX_TEXT_CHARS)
    debug_state = create_debug_state(config, root)
    debug_state.log(
        "manual_publish_start",
        backend=backend,
        text_file=str((root / args.text_file).resolve()),
        dry_run=args.dry_run,
        max_chars=max_chars,
    )
    text_file = (root / args.text_file).resolve()
    current = now_utc()
    manual_item = {
        "id": f"manual__{text_file.stem}",
        "text_file": str(text_file),
        "relative_path": str(args.text_file),
        "publish_at_utc": current.isoformat().replace("+00:00", "Z"),
        "attempts": 1,
    }
    attempt_context = debug_state.create_attempt_context(manual_item, backend, current)
    debug_state.current_attempt_id = attempt_context.attempt_id
    try:
        thread_ids = publish_text_file(
            config=config,
            root=root,
            backend=backend,
            text_file=text_file,
            max_chars=max_chars,
            dry_run=args.dry_run,
            debug_state=debug_state,
            attempt_context=attempt_context,
        )
        debug_state.log(
            "manual_publish_finished",
            **attempt_context.to_debug_payload(),
            thread_ids=thread_ids,
            dry_run=args.dry_run,
        )
    finally:
        debug_state.current_attempt_id = None
    if not args.dry_run:
        print(json.dumps({"thread_ids": thread_ids, "backend": backend}, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Threads publisher: export txt files, build queue, run official API publisher."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export-txt", help="Create .txt siblings next to markdown posts.")
    export_parser.add_argument(
        "--content-root",
        default="content/threads-week-2026-03-10-to-2026-03-16",
        help="Directory containing dated post folders.",
    )
    export_parser.set_defaults(func=command_export_txt)

    queue_parser = subparsers.add_parser("build-queue", help="Build a scheduled publish queue from .txt files.")
    queue_parser.add_argument(
        "--content-root",
        default="content/threads-week-2026-03-10-to-2026-03-16",
        help="Directory containing dated post folders.",
    )
    queue_parser.add_argument(
        "--queue-file",
        default="state/threads-publish-queue.json",
        help="Where to store the queue JSON.",
    )
    queue_parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help="IANA timezone used to interpret date folder + time file names.",
    )
    queue_parser.set_defaults(func=command_build_queue)

    run_parser = subparsers.add_parser("run", help="Run the forever publisher daemon.")
    run_parser.add_argument("--queue-file", default="state/threads-publish-queue.json")
    run_parser.add_argument("--backend", choices=["official", "playwright"], default=None)
    run_parser.add_argument("--interval-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--once", action="store_true", help="Process due items once and exit.")
    run_parser.set_defaults(func=command_run)

    oauth_parser = subparsers.add_parser("oauth-url", help="Print the OAuth authorization URL.")
    oauth_parser.add_argument("--state", default=None)
    oauth_parser.set_defaults(func=command_oauth_url)

    auth_parser = subparsers.add_parser(
        "auth-start",
        help="Run the full OAuth flow, exchange tokens and optionally save .env.",
    )
    auth_parser.add_argument("--state", default=None)
    auth_parser.add_argument(
        "--listen",
        action="store_true",
        help="Listen on THREADS_REDIRECT_URI and capture the OAuth callback automatically.",
    )
    auth_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="How long to wait for the OAuth callback when --listen is enabled.",
    )
    auth_parser.add_argument(
        "--write-env",
        action="store_true",
        help="Write the resulting long-lived token and config into .env",
    )
    auth_parser.set_defaults(func=command_auth_start)

    exchange_parser = subparsers.add_parser("exchange-code", help="Exchange OAuth code for short-lived token.")
    exchange_parser.add_argument("code")
    exchange_parser.set_defaults(func=command_exchange_code)

    full_exchange_parser = subparsers.add_parser(
        "exchange-full-code",
        help="Exchange OAuth code, mint a long-lived token, and optionally save it into .env.",
    )
    full_exchange_parser.add_argument("code")
    full_exchange_parser.add_argument("--write-env", action="store_true")
    full_exchange_parser.set_defaults(func=command_exchange_full_code)

    long_token_parser = subparsers.add_parser(
        "exchange-long-token",
        help="Exchange short-lived token for long-lived Threads token.",
    )
    long_token_parser.add_argument("short_lived_token")
    long_token_parser.set_defaults(func=command_exchange_long_token)

    refresh_parser = subparsers.add_parser("refresh-token", help="Refresh a long-lived Threads token.")
    refresh_parser.add_argument("long_lived_token", nargs="?", default=None)
    refresh_parser.add_argument("--write-env", action="store_true")
    refresh_parser.set_defaults(func=command_refresh_token)

    whoami_parser = subparsers.add_parser("whoami", help="Inspect the authenticated Threads account.")
    whoami_parser.set_defaults(func=command_whoami)

    list_posts_parser = subparsers.add_parser(
        "list-posts",
        help="List own Threads posts via official API.",
    )
    list_posts_parser.add_argument("--since", default=None, help="Optional pass-through since filter for /me/threads.")
    list_posts_parser.add_argument("--until", default=None, help="Optional pass-through until filter for /me/threads.")
    list_posts_parser.add_argument("--limit", type=int, default=25, help="Items per API page.")
    list_posts_parser.add_argument("--max-pages", type=int, default=4, help="How many pages to scan.")
    list_posts_parser.add_argument("--fields", default=DEFAULT_MEDIA_LOOKUP_FIELDS)
    list_posts_parser.set_defaults(func=command_list_posts)

    post_insights_parser = subparsers.add_parser(
        "post-insights",
        help="Fetch insights for a Threads post by media id, URL, or shortcode.",
    )
    post_insights_parser.add_argument("post_ref", help="Threads media id, post URL, or shortcode.")
    post_insights_parser.add_argument(
        "--metrics",
        default=DEFAULT_POST_INSIGHT_METRICS,
        help="Comma-separated media insight metrics.",
    )
    post_insights_parser.add_argument(
        "--lookup-since",
        default=None,
        help="Optional since filter when resolving a URL/shortcode via /me/threads.",
    )
    post_insights_parser.add_argument(
        "--lookup-until",
        default=None,
        help="Optional until filter when resolving a URL/shortcode via /me/threads.",
    )
    post_insights_parser.add_argument("--lookup-limit", type=int, default=25, help="Items per lookup page.")
    post_insights_parser.add_argument("--lookup-pages", type=int, default=4, help="How many lookup pages to scan.")
    post_insights_parser.set_defaults(func=command_post_insights)

    user_insights_parser = subparsers.add_parser(
        "user-insights",
        help="Fetch Threads profile insights for the authenticated user.",
    )
    user_insights_parser.add_argument(
        "--metrics",
        default=DEFAULT_USER_INSIGHT_METRICS,
        help="Comma-separated profile insight metrics.",
    )
    user_insights_parser.add_argument(
        "--user-id",
        default="me",
        help="Threads user id. Defaults to the authenticated user.",
    )
    user_insights_parser.add_argument(
        "--since",
        default=None,
        help="Unix seconds or ISO date/datetime. Defaults to API behaviour if omitted.",
    )
    user_insights_parser.add_argument(
        "--until",
        default=None,
        help="Unix seconds or ISO date/datetime. Defaults to API behaviour if omitted.",
    )
    user_insights_parser.add_argument(
        "--breakdown",
        default=None,
        help="Required for follower_demographics: country, city, age, or gender.",
    )
    user_insights_parser.set_defaults(func=command_user_insights)

    publish_parser = subparsers.add_parser("publish-file", help="Publish one txt file immediately.")
    publish_parser.add_argument("text_file")
    publish_parser.add_argument("--backend", choices=["official", "playwright"], default=None)
    publish_parser.add_argument("--dry-run", action="store_true")
    publish_parser.set_defaults(func=command_publish_file)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        args.func(args, root)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
