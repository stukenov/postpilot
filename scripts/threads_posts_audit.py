#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import threads_publisher as tp


DEFAULT_POST_METRICS = tp.parse_csv_values(tp.DEFAULT_POST_INSIGHT_METRICS)
DEFAULT_USER_METRICS = tp.parse_csv_values(tp.DEFAULT_USER_INSIGHT_METRICS)


def to_iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_input_datetime(value: str | None, timezone_name: str, *, end_of_day: bool = False) -> datetime | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return datetime.fromtimestamp(int(stripped), tz=UTC)

    date_only = len(stripped) == 10
    if date_only:
        dt = datetime.strptime(stripped, "%Y-%m-%d").replace(tzinfo=ZoneInfo(timezone_name))
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt.astimezone(UTC)

    parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.astimezone(UTC)


def insight_value(item: dict[str, Any]) -> Any:
    if "values" in item and isinstance(item["values"], list) and item["values"]:
        first = item["values"][0]
        if isinstance(first, dict) and "value" in first:
            return first["value"]
        return first
    if "total_value" in item:
        total_value = item["total_value"]
        if isinstance(total_value, dict) and "value" in total_value:
            return total_value["value"]
        return total_value
    if "value" in item:
        return item["value"]
    return None


def flatten_insights(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("data", [])
    flattened: dict[str, Any] = {}
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        flattened[name] = insight_value(item)
    return flattened


def summarize_user_insights(payload: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    series: dict[str, Any] = {}
    links: dict[str, Any] = {}

    for item in payload.get("data", []):
        name = str(item.get("name") or "").strip()
        if not name:
            continue

        if "values" in item and isinstance(item["values"], list):
            points = []
            numeric_values: list[int | float] = []
            for value_item in item["values"]:
                if not isinstance(value_item, dict):
                    continue
                point_value = value_item.get("value")
                point = {
                    "value": point_value,
                    "end_time": value_item.get("end_time"),
                }
                points.append(point)
                if isinstance(point_value, (int, float)) and not isinstance(point_value, bool):
                    numeric_values.append(point_value)
            series[name] = points
            metrics[name] = {
                "period": item.get("period"),
                "series_total": sum(numeric_values) if numeric_values else None,
                "latest_value": numeric_values[-1] if numeric_values else None,
                "points": len(points),
            }
            continue

        if "total_value" in item:
            total_value = item["total_value"]
            if isinstance(total_value, dict) and "value" in total_value:
                total_value = total_value["value"]
            metrics[name] = {
                "period": item.get("period"),
                "value": total_value,
            }
            continue

        if "link_total_values" in item and isinstance(item["link_total_values"], list):
            link_values = []
            total = 0
            for link_item in item["link_total_values"]:
                if not isinstance(link_item, dict):
                    continue
                value = numeric_or_zero(link_item.get("value"))
                total += value
                link_values.append(
                    {
                        "link_url": link_item.get("link_url"),
                        "value": value,
                    }
                )
            metrics[name] = {
                "period": item.get("period"),
                "value": total,
            }
            links[name] = link_values
            continue

        metrics[name] = {
            "period": item.get("period"),
            "value": insight_value(item),
        }

    return {
        "metrics": metrics,
        "series": series,
        "links": links,
    }


def post_text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def preview_text(text: str, limit: int = 140) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def numeric_or_zero(value: Any) -> int | float:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return 0


def top_posts(posts: list[dict[str, Any]], metric: str, limit: int = 5) -> list[dict[str, Any]]:
    ordered = sorted(posts, key=lambda post: numeric_or_zero(post["insights"].get(metric)), reverse=True)
    result: list[dict[str, Any]] = []
    for post in ordered[:limit]:
        result.append(
            {
                "id": post["id"],
                "permalink": post["permalink"],
                "published_at_utc": post["timestamp_utc"],
                "published_at_local": post["timestamp_local"],
                "metric": metric,
                "value": post["insights"].get(metric),
                "engagement_total": post["engagement_total"],
                "text_preview": post["text_preview"],
            }
        )
    return result


def build_duplicates(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for post in posts:
        grouped[post["text_sha256"]].append(post)

    duplicates: list[dict[str, Any]] = []
    for sha_value, items in grouped.items():
        if len(items) < 2:
            continue
        duplicates.append(
            {
                "text_sha256": sha_value,
                "post_count": len(items),
                "text_preview": items[0]["text_preview"],
                "posts": [
                    {
                        "id": item["id"],
                        "permalink": item["permalink"],
                        "published_at_utc": item["timestamp_utc"],
                        "published_at_local": item["timestamp_local"],
                        "views": item["insights"].get("views"),
                        "likes": item["insights"].get("likes"),
                    }
                    for item in items
                ],
            }
        )
    duplicates.sort(key=lambda item: item["post_count"], reverse=True)
    return duplicates


def posts_by_local_date(posts: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for post in posts:
        local_date = str(post["timestamp_local"]).split("T", 1)[0]
        counts[local_date] += 1
    return dict(sorted(counts.items()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit Threads posts and insights for a given date range.")
    parser.add_argument("--since", default=None, help="Unix seconds or ISO date/datetime. Default: now - 7 days.")
    parser.add_argument("--until", default=None, help="Unix seconds or ISO date/datetime. Default: now.")
    parser.add_argument("--timezone", default=None, help="IANA timezone. Default: THREADS_TIMEZONE or Asia/Almaty.")
    parser.add_argument("--limit", type=int, default=100, help="Items per /me/threads page.")
    parser.add_argument("--max-pages", type=int, default=10, help="How many /me/threads pages to scan.")
    parser.add_argument(
        "--post-metrics",
        default=",".join(DEFAULT_POST_METRICS),
        help="Comma-separated post insight metrics.",
    )
    parser.add_argument(
        "--user-metrics",
        default=",".join(DEFAULT_USER_METRICS),
        help="Comma-separated user insight metrics.",
    )
    parser.add_argument("--skip-user-insights", action="store_true")
    parser.add_argument("--output", default=None, help="Optional path to write the JSON report.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    config = tp.get_config(root)
    timezone_name = args.timezone or tp.env_get(config, "THREADS_TIMEZONE", tp.DEFAULT_TIMEZONE) or tp.DEFAULT_TIMEZONE
    api = tp.create_threads_api(config)
    profile = api.whoami()

    now_utc = datetime.now(tz=UTC)
    since_dt = parse_input_datetime(args.since, timezone_name) or (now_utc - timedelta(days=7))
    requested_until_dt = parse_input_datetime(args.until, timezone_name, end_of_day=True)
    until_dt = requested_until_dt or now_utc
    if until_dt > now_utc:
        until_dt = now_utc
    if since_dt > until_dt:
        raise RuntimeError("--since must be earlier than or equal to --until")

    since_unix = str(int(since_dt.timestamp()))
    until_unix = str(int(until_dt.timestamp()))
    post_metrics = tp.parse_csv_values(args.post_metrics)
    user_metrics = tp.parse_csv_values(args.user_metrics)

    listing = tp.list_recent_threads(
        api,
        since=since_unix,
        until=until_unix,
        limit=args.limit,
        max_pages=args.max_pages,
        fields=tp.DEFAULT_MEDIA_LOOKUP_FIELDS,
    )

    posts: list[dict[str, Any]] = []
    totals = {metric: 0 for metric in post_metrics}
    local_tz = ZoneInfo(timezone_name)

    for media in listing.get("data", []):
        raw_text = str(media.get("text") or "")
        timestamp_raw = str(media.get("timestamp") or "")
        timestamp_dt = datetime.strptime(timestamp_raw, "%Y-%m-%dT%H:%M:%S%z").astimezone(UTC)
        insights_raw = api.get_media_insights(str(media["id"]), post_metrics)
        insights = flatten_insights(insights_raw)
        for metric in post_metrics:
            totals[metric] += numeric_or_zero(insights.get(metric))

        engagement_total = sum(
            numeric_or_zero(insights.get(metric))
            for metric in ["likes", "replies", "reposts", "quotes", "shares"]
        )

        posts.append(
            {
                "id": str(media["id"]),
                "permalink": media.get("permalink"),
                "shortcode": media.get("shortcode"),
                "username": media.get("username"),
                "media_type": media.get("media_type"),
                "media_product_type": media.get("media_product_type"),
                "timestamp_utc": to_iso_z(timestamp_dt),
                "timestamp_local": timestamp_dt.astimezone(local_tz).isoformat(),
                "text": raw_text,
                "text_chars": len(raw_text),
                "text_preview": preview_text(raw_text),
                "text_sha256": post_text_sha(raw_text),
                "is_quote_post": bool(media.get("is_quote_post")),
                "raw_media": media,
                "insights": insights,
                "raw_insights": insights_raw,
                "engagement_total": engagement_total,
            }
        )

    duplicates = build_duplicates(posts)
    averages = {
        metric: round(totals[metric] / len(posts), 3) if posts else 0
        for metric in post_metrics
    }

    user_insights_payload: dict[str, Any] | None = None
    if not args.skip_user_insights:
        try:
            user_insights_raw = api.get_user_insights(
                str(profile["id"]),
                user_metrics,
                since=since_unix,
                until=until_unix,
            )
            user_insights_payload = {
                **summarize_user_insights(user_insights_raw),
                "raw": user_insights_raw,
            }
        except Exception as exc:  # noqa: BLE001
            user_insights_payload = {
                "error": str(exc),
                "metrics": None,
                "series": None,
                "links": None,
            }

    report = {
        "generated_at_utc": to_iso_z(now_utc),
        "profile": profile,
        "range": {
            "since_utc": to_iso_z(since_dt),
            "until_utc": to_iso_z(until_dt),
            "since_local": since_dt.astimezone(local_tz).isoformat(),
            "until_local": until_dt.astimezone(local_tz).isoformat(),
            "timezone": timezone_name,
            "since_unix": since_unix,
            "until_unix": until_unix,
        },
        "api": {
            "media_fields": tp.DEFAULT_MEDIA_LOOKUP_FIELDS,
            "post_metrics": post_metrics,
            "user_metrics": user_metrics,
            "pages_scanned": listing.get("paging", {}).get("pages_scanned"),
            "has_next_page": listing.get("paging", {}).get("has_next_page"),
        },
        "summary": {
            "post_count": len(posts),
            "exact_duplicate_groups": len(duplicates),
            "exact_duplicate_posts": sum(item["post_count"] for item in duplicates),
            "extra_duplicate_posts": sum(item["post_count"] - 1 for item in duplicates),
            "posts_by_local_date": posts_by_local_date(posts),
            "totals": totals,
            "averages": averages,
        },
        "top_posts": {
            "by_views": top_posts(posts, "views"),
            "by_likes": top_posts(posts, "likes"),
            "by_engagement": sorted(
                [
                    {
                        "id": post["id"],
                        "permalink": post["permalink"],
                        "published_at_utc": post["timestamp_utc"],
                        "published_at_local": post["timestamp_local"],
                        "engagement_total": post["engagement_total"],
                        "views": post["insights"].get("views"),
                        "likes": post["insights"].get("likes"),
                        "text_preview": post["text_preview"],
                    }
                    for post in posts
                ],
                key=lambda post: numeric_or_zero(post["engagement_total"]),
                reverse=True,
            )[:5],
        },
        "duplicates": duplicates,
        "user_insights": user_insights_payload,
        "posts": sorted(posts, key=lambda post: post["timestamp_utc"], reverse=True),
    }

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = root / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
