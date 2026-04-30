#!/usr/bin/env python3
"""LinkedIn publisher daemon — queue, scheduling, publishing."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}")


def build_playwright_command(config: dict, txt_file: str) -> list[str]:
    """Build the node command to run linkedin_publish_playwright.mjs."""
    node_bin = config.get("LINKEDIN_NODE_BIN", "node") or "node"
    script_path = str(Path(__file__).resolve().parent / "linkedin_publish_playwright.mjs")

    command = [node_bin, script_path]

    storage_state = config.get("LINKEDIN_PLAYWRIGHT_STORAGE_STATE")
    if storage_state:
        command += ["--storage-state", storage_state]

    headless_str = config.get("LINKEDIN_PLAYWRIGHT_HEADLESS", "false").lower()
    if headless_str in ("1", "true", "yes", "on"):
        command.append("--headless")
    else:
        command.append("--headed")

    command.append(txt_file)
    return command


def publish_text_file(config: dict, txt_file: str, dry_run: bool = False) -> dict:
    """Publish a single text file via Playwright subprocess."""
    text = Path(txt_file).read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Text file is empty: {txt_file}")

    if dry_run:
        log(f"[dry-run] {txt_file}")
        return {"file": txt_file, "status": "dry-run"}

    storage_state = config.get("LINKEDIN_PLAYWRIGHT_STORAGE_STATE")
    if not storage_state:
        raise RuntimeError(
            "LINKEDIN_PLAYWRIGHT_STORAGE_STATE is required for Playwright publishing"
        )

    command = build_playwright_command(config, txt_file)
    log(f"PLAYWRIGHT subprocess: {' '.join(command)}")

    env = os.environ.copy()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=120,
    )

    if completed.returncode != 0:
        error_msg = completed.stderr.strip() or f"exit code {completed.returncode}"
        log(f"PLAYWRIGHT FAILED: {error_msg}")
        return {"file": txt_file, "status": "failed", "error": error_msg}

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = {}

    log(f"PLAYWRIGHT OK: {txt_file}")
    return {"file": txt_file, "status": result.get("status", "posted")}


def extract_post_text(md_content: str) -> str:
    """Extract text between '## Post text' / '## Текст поста' and the next '##' heading."""
    lines = md_content.split("\n")
    capturing = False
    result: list[str] = []
    for line in lines:
        lower = line.strip().lower()
        if lower.startswith("## post text") or lower.startswith("## текст поста"):
            capturing = True
            continue
        if capturing and line.strip().startswith("## "):
            break
        if capturing:
            result.append(line)
    return "\n".join(result).strip()


def command_export_txt(args: argparse.Namespace) -> None:
    content_root = Path(args.content_root)
    if not content_root.is_dir():
        print(f"Content root not found: {content_root}", file=sys.stderr)
        raise SystemExit(1)

    count = 0
    for md_file in sorted(content_root.rglob("*.md")):
        if md_file.name == "README.md":
            continue
        txt_file = md_file.with_suffix(".txt")
        text = extract_post_text(md_file.read_text(encoding="utf-8"))
        if not text:
            log(f"SKIP {md_file} — no post text found")
            continue
        txt_file.write_text(text + "\n", encoding="utf-8")
        log(f"WROTE {txt_file}")
        count += 1

    log(f"export-txt done: {count} files written")


def command_build_queue(args: argparse.Namespace) -> None:
    content_root = Path(args.content_root)
    queue_file = Path(args.queue_file)
    tz = ZoneInfo(args.timezone)

    if not content_root.is_dir():
        print(f"Content root not found: {content_root}", file=sys.stderr)
        raise SystemExit(1)

    # Load existing queue to preserve statuses
    existing_statuses: dict[str, str] = {}
    existing_items: dict[str, dict] = {}
    preserve = {"posted", "dry-run", "failed", "interrupted", "missed"}
    if queue_file.exists():
        old = json.loads(queue_file.read_text(encoding="utf-8"))
        for item in old.get("items", []):
            existing_statuses[item["id"]] = item.get("status", "pending")
            existing_items[item["id"]] = item

    items = []
    for txt_file in sorted(content_root.rglob("*.txt")):
        day_dir = txt_file.parent.name
        time_part = txt_file.stem

        try:
            local_dt = datetime.strptime(
                f"{day_dir}T{time_part.replace('-', ':')}",
                "%Y-%m-%dT%H:%M",
            ).replace(tzinfo=tz)
        except ValueError:
            log(f"SKIP {txt_file} — cannot parse date/time")
            continue

        utc_dt = local_dt.astimezone(timezone.utc)
        item_id = f"{day_dir}__{time_part}"

        old_status = existing_statuses.get(item_id)
        if old_status in preserve:
            items.append(existing_items[item_id])
            log(f"PRESERVE {item_id} status={old_status}")
            continue

        status = "pending"
        if old_status == "publishing":
            status = "interrupted"
            log(f"INTERRUPTED {item_id} (was publishing)")

        items.append({
            "id": item_id,
            "relative_path": f"{day_dir}/{txt_file.name}",
            "text_file": str(txt_file.resolve()),
            "publish_at_local": local_dt.isoformat(),
            "publish_at_utc": utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timezone": args.timezone,
            "status": status,
            "attempts": 0,
            "post_ids": [],
            "publish_backend": None,
            "posted_at": None,
            "last_error": None,
        })
        log(f"QUEUE {item_id} at {local_dt.isoformat()}")

    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "content_root": str(content_root.resolve()),
        "timezone": args.timezone,
        "items": items,
    }
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"build-queue done: {len(items)} items → {queue_file}")


def command_run(args: argparse.Namespace) -> None:
    queue_file = Path(args.queue_file)
    poll_seconds = int(args.poll_seconds)

    running = True

    def handle_signal(signum, _frame):
        nonlocal running
        log(f"Received signal {signum}, shutting down")
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log(f"LinkedIn publisher daemon started, polling every {poll_seconds}s")
    log(f"Queue: {queue_file}")

    while running:
        if not queue_file.exists():
            log("Queue file not found, waiting...")
            time.sleep(poll_seconds)
            continue

        queue = json.loads(queue_file.read_text(encoding="utf-8"))
        pending = [i for i in queue.get("items", []) if i["status"] == "pending"]

        if not pending:
            time.sleep(poll_seconds)
            continue

        now_utc = datetime.now(timezone.utc)
        config = {
            "LINKEDIN_NODE_BIN": os.environ.get("LINKEDIN_NODE_BIN", "node"),
            "LINKEDIN_PLAYWRIGHT_STORAGE_STATE": os.environ.get("LINKEDIN_PLAYWRIGHT_STORAGE_STATE", ""),
            "LINKEDIN_PLAYWRIGHT_HEADLESS": os.environ.get("LINKEDIN_PLAYWRIGHT_HEADLESS", "true"),
        }
        for item in pending:
            if not running:
                break
            publish_at = datetime.fromisoformat(item["publish_at_utc"].replace("Z", "+00:00"))
            if now_utc < publish_at:
                continue

            log(f"PUBLISH {item['id']}")
            item["status"] = "publishing"
            item["attempts"] = item.get("attempts", 0) + 1
            queue_file.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

            try:
                result = publish_text_file(config, item["text_file"])
                item["status"] = result["status"]
                if result.get("error"):
                    item["last_error"] = result["error"]
                if result["status"] == "posted":
                    item["posted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception as exc:
                item["status"] = "failed"
                item["last_error"] = str(exc)
                log(f"PUBLISH ERROR {item['id']}: {exc}")

            queue_file.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            log(f"STATUS {item['id']} -> {item['status']}")
            break  # one post per loop iteration

        time.sleep(poll_seconds)

    log("LinkedIn publisher daemon stopped")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LinkedIn publisher")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export-txt", help="Create .txt from .md posts")
    export_parser.add_argument("--content-root", required=True)
    export_parser.set_defaults(func=command_export_txt)

    queue_parser = subparsers.add_parser("build-queue", help="Build publish queue from .txt files")
    queue_parser.add_argument("--content-root", required=True)
    queue_parser.add_argument("--queue-file", required=True)
    queue_parser.add_argument("--timezone", default="Asia/Almaty")
    queue_parser.set_defaults(func=command_build_queue)

    run_parser = subparsers.add_parser("run", help="Run the publisher daemon")
    run_parser.add_argument("--queue-file", default="state/linkedin-publish-queue.json")
    run_parser.add_argument("--poll-seconds", default="30")
    run_parser.set_defaults(func=command_run)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
