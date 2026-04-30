#!/usr/bin/env python3

from __future__ import annotations

import argparse
import http.server
import json
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import threads_publisher as tp


HTML_PAGE = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Threads Posts</title>
  <style>
    :root {
      --bg: #f6f2e9;
      --panel: rgba(255, 252, 245, 0.9);
      --ink: #171512;
      --muted: #6f685f;
      --line: rgba(23, 21, 18, 0.14);
      --accent: #1f6a52;
      --soft: #ece4d6;
      --danger: #8a4338;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(31, 106, 82, 0.08), transparent 28rem),
        linear-gradient(180deg, #f8f5ee 0%, var(--bg) 100%);
    }
    .shell {
      max-width: 1320px;
      margin: 0 auto;
      padding: 24px 18px 40px;
    }
    .topbar {
      display: flex;
      gap: 16px;
      justify-content: space-between;
      align-items: end;
      margin-bottom: 22px;
      flex-wrap: wrap;
    }
    .title {
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      font-size: clamp(2rem, 4vw, 3.2rem);
      font-weight: 600;
      letter-spacing: -0.04em;
      line-height: 0.95;
    }
    .subtitle {
      margin: 8px 0 0;
      color: var(--muted);
      max-width: 58rem;
      font-size: 0.98rem;
    }
    .toolbar {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    .button {
      border: 1px solid var(--line);
      background: #fffdf8;
      color: var(--ink);
      border-radius: 999px;
      padding: 10px 14px;
      font: inherit;
      cursor: pointer;
      transition: background 120ms ease, border-color 120ms ease, transform 120ms ease;
    }
    .button:hover { background: #fff; border-color: rgba(23, 21, 18, 0.28); }
    .button:active { transform: translateY(1px); }
    .button[disabled] { cursor: default; opacity: 0.55; }
    .button.primary {
      background: var(--ink);
      border-color: var(--ink);
      color: #f8f5ee;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
      gap: 10px;
      margin: 18px 0 24px;
    }
    .stat {
      padding: 14px 15px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--panel);
      backdrop-filter: blur(10px);
    }
    .stat-label {
      display: block;
      color: var(--muted);
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }
    .stat-value {
      font-size: 1.35rem;
      line-height: 1;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 18px;
    }
    .panel {
      border: 1px solid var(--line);
      border-radius: 24px;
      background: var(--panel);
      backdrop-filter: blur(14px);
      min-height: 380px;
      overflow: hidden;
    }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 18px 18px 14px;
      border-bottom: 1px solid var(--line);
    }
    .panel-title {
      margin: 0;
      font-size: 1rem;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }
    .panel-subtitle {
      color: var(--muted);
      font-size: 0.85rem;
    }
    .list {
      display: grid;
      gap: 0;
    }
    .item {
      padding: 16px 18px 18px;
      border-bottom: 1px solid rgba(23, 21, 18, 0.08);
    }
    .item:last-child { border-bottom: 0; }
    .item-top {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
      margin-bottom: 10px;
    }
    .item-id {
      font-size: 0.78rem;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .item-time {
      font-size: 0.85rem;
      color: var(--muted);
      white-space: nowrap;
      text-align: right;
    }
    .preview {
      margin: 0 0 12px;
      font-size: 0.98rem;
      line-height: 1.45;
      max-width: 62ch;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }
    .tag {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 0.78rem;
      border: 1px solid var(--line);
      color: var(--muted);
      background: rgba(255, 255, 255, 0.6);
    }
    .tag.ok { color: var(--accent); border-color: rgba(31, 106, 82, 0.2); }
    .tag.warn { color: var(--danger); border-color: rgba(138, 67, 56, 0.18); }
    .item-actions {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .link {
      color: var(--ink);
      text-decoration: none;
      border-bottom: 1px solid rgba(23, 21, 18, 0.24);
    }
    .link:hover { border-color: rgba(23, 21, 18, 0.55); }
    .insights {
      margin-top: 14px;
      padding: 14px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: var(--soft);
      display: none;
    }
    .insights.visible { display: block; }
    .insights-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .insight {
      background: rgba(255, 255, 255, 0.55);
      border-radius: 12px;
      padding: 10px 11px;
      border: 1px solid rgba(23, 21, 18, 0.08);
    }
    .insight-label {
      display: block;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .insight-value {
      font-size: 1.15rem;
      line-height: 1;
    }
    .insight-meta {
      color: var(--muted);
      font-size: 0.85rem;
      line-height: 1.4;
    }
    .empty {
      padding: 18px;
      color: var(--muted);
    }
    .footnote {
      margin-top: 18px;
      color: var(--muted);
      font-size: 0.84rem;
    }
    @media (max-width: 980px) {
      .layout { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div>
        <h1 class="title">Threads Queue</h1>
        <p class="subtitle">План, факт и живая статистика по опубликованным постам в одном чистом представлении.</p>
      </div>
      <div class="toolbar">
        <button id="reloadButton" class="button primary" type="button">Обновить</button>
      </div>
    </div>

    <section id="stats" class="stats"></section>

    <div class="layout">
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2 class="panel-title">Запланированные</h2>
            <div class="panel-subtitle" id="scheduledSubtitle"></div>
          </div>
        </div>
        <div id="scheduledList" class="list"></div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <h2 class="panel-title">Опубликованные</h2>
            <div class="panel-subtitle" id="postedSubtitle"></div>
          </div>
          <div class="toolbar">
            <button id="bulkStatsButton" class="button" type="button">Запросить всю статистику</button>
          </div>
        </div>
        <div id="postedList" class="list"></div>
      </section>
    </div>

    <div id="footnote" class="footnote"></div>
  </div>

  <script>
    const state = {
      overview: null,
      batchLoading: false,
    };

    const metricsOrder = ["views", "likes", "replies", "reposts", "quotes", "shares"];

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function formatDateTime(value) {
      if (!value) return "—";
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return value;
      return parsed.toLocaleString("ru-RU", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    }

    function formatNumber(value) {
      if (value === null || value === undefined) return "—";
      return new Intl.NumberFormat("ru-RU").format(value);
    }

    function buildStat(label, value) {
      return `
        <div class="stat">
          <span class="stat-label">${escapeHtml(label)}</span>
          <div class="stat-value">${escapeHtml(formatNumber(value))}</div>
        </div>
      `;
    }

    function renderHeader(overview) {
      const counts = overview.counts || {};
      document.getElementById("stats").innerHTML = [
        buildStat("Запланировано", counts.pending || 0),
        buildStat("Опубликовано", counts.posted || 0),
        buildStat("Прервано", counts.interrupted || 0),
        buildStat("Очередь всего", counts.total || 0),
      ].join("");

      document.getElementById("scheduledSubtitle").textContent = `${counts.pending || 0} постов впереди`;
      document.getElementById("postedSubtitle").textContent = `${counts.posted || 0} уже в эфире`;

      const timezone = overview.timezone || "Asia/Almaty";
      const generatedAt = formatDateTime(overview.generated_at);
      document.getElementById("footnote").textContent = `Очередь: ${generatedAt}. Таймзона: ${timezone}. Статистика тянется живьем по кнопке у поста или пакетно по всему опубликованному списку.`;
    }

    function renderScheduled(items) {
      const root = document.getElementById("scheduledList");
      if (!items.length) {
        root.innerHTML = `<div class="empty">Запланированных постов нет.</div>`;
        return;
      }
      root.innerHTML = items.map((item) => `
        <article class="item">
          <div class="item-top">
            <div>
              <div class="item-id">${escapeHtml(item.id)}</div>
            </div>
            <div class="item-time">${escapeHtml(formatDateTime(item.publish_at_local || item.publish_at_utc))}</div>
          </div>
          <p class="preview">${escapeHtml(item.preview)}</p>
          <div class="meta">
            <span class="tag">${escapeHtml(item.relative_path)}</span>
            <span class="tag">${escapeHtml(item.text_chars)} симв.</span>
            <span class="tag">${escapeHtml(item.publish_backend || "pending")}</span>
          </div>
        </article>
      `).join("");
    }

    function renderPosted(items) {
      const root = document.getElementById("postedList");
      if (!items.length) {
        root.innerHTML = `<div class="empty">Опубликованных постов пока нет.</div>`;
        syncBulkStatsButton();
        return;
      }
      root.innerHTML = items.map((item) => `
        <article class="item" data-item-id="${escapeHtml(item.id)}">
          <div class="item-top">
            <div>
              <div class="item-id">${escapeHtml(item.id)}</div>
            </div>
            <div class="item-time">${escapeHtml(formatDateTime(item.posted_at || item.publish_at_local || item.publish_at_utc))}</div>
          </div>
          <p class="preview">${escapeHtml(item.preview)}</p>
          <div class="meta">
            <span class="tag ok">posted</span>
            <span class="tag">${escapeHtml(item.relative_path)}</span>
            <span class="tag">${escapeHtml(item.text_chars)} симв.</span>
            ${item.permalink ? `<a class="tag link" href="${escapeHtml(item.permalink)}" target="_blank" rel="noreferrer">открыть пост</a>` : ""}
          </div>
          <div class="item-actions">
            <button class="button" type="button" data-action="stats">Запросить статистику</button>
          </div>
          <div class="insights" id="stats-${escapeHtml(item.id)}"></div>
        </article>
      `).join("");
      syncBulkStatsButton();
    }

    function syncBulkStatsButton() {
      const button = document.getElementById("bulkStatsButton");
      const posted = state.overview?.posted || [];
      button.disabled = state.batchLoading || posted.length === 0;
      button.textContent = state.batchLoading ? button.textContent : "Запросить всю статистику";
    }

    function itemStatsButton(itemId) {
      return document.querySelector(`[data-item-id="${itemId}"] [data-action="stats"]`);
    }

    function renderInsights(itemId, payload, isError) {
      const box = document.getElementById(`stats-${itemId}`);
      if (!box) return;

      if (isError) {
        box.classList.add("visible");
        box.innerHTML = `<div class="insight-meta">${escapeHtml(payload.error || "Не удалось получить статистику.")}</div>`;
        return;
      }

      const metrics = payload.metrics || {};
      const cards = metricsOrder.map((metric) => `
        <div class="insight">
          <span class="insight-label">${escapeHtml(metric)}</span>
          <div class="insight-value">${escapeHtml(formatNumber(metrics[metric]))}</div>
        </div>
      `).join("");

      const post = payload.post || {};
      const permalink = post.permalink
        ? `<a class="link" href="${escapeHtml(post.permalink)}" target="_blank" rel="noreferrer">открыть пост</a>`
        : "";

      box.classList.add("visible");
      box.innerHTML = `
        <div class="insights-grid">${cards}</div>
        <div class="insight-meta">
          Media ID: ${escapeHtml(post.id || "—")}<br>
          Опубликован: ${escapeHtml(formatDateTime(post.timestamp || payload.item?.posted_at || null))}<br>
          ${permalink}
        </div>
      `;
    }

    async function fetchOverview() {
      const response = await fetch("./api/overview", { headers: { "Accept": "application/json" } });
      if (!response.ok) throw new Error(`overview ${response.status}`);
      return response.json();
    }

    async function fetchStats(itemId) {
      const response = await fetch(`./api/post-stats?item_id=${encodeURIComponent(itemId)}`, {
        headers: { "Accept": "application/json" },
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `stats ${response.status}`);
      return payload;
    }

    async function loadItemStats(itemId, options = {}) {
      const button = options.button || itemStatsButton(itemId);
      const loadingLabel = options.loadingLabel || "Запрашиваю…";
      const successLabel = options.successLabel || "Обновить статистику";
      const errorLabel = options.errorLabel || "Повторить";

      if (button) {
        button.disabled = true;
        button.textContent = loadingLabel;
      }
      try {
        const payload = await fetchStats(itemId);
        renderInsights(itemId, payload, false);
        if (button) {
          button.textContent = successLabel;
        }
        return { ok: true };
      } catch (error) {
        renderInsights(itemId, { error: error.message }, true);
        if (button) {
          button.textContent = errorLabel;
        }
        return { ok: false, error };
      } finally {
        if (button) {
          button.disabled = false;
        }
      }
    }

    async function loadAllPostedStats() {
      if (state.batchLoading) return;
      const items = state.overview?.posted || [];
      if (!items.length) return;

      const bulkButton = document.getElementById("bulkStatsButton");
      const reloadButton = document.getElementById("reloadButton");
      state.batchLoading = true;
      bulkButton.disabled = true;
      reloadButton.disabled = true;

      let successCount = 0;
      try {
        for (let index = 0; index < items.length; index += 1) {
          const item = items[index];
          bulkButton.textContent = `Статистика ${index + 1} / ${items.length}`;
          const result = await loadItemStats(item.id, {
            loadingLabel: "Идёт пакетный запрос…",
          });
          if (result.ok) {
            successCount += 1;
          }
        }
        bulkButton.textContent = `Готово ${successCount} / ${items.length}`;
      } finally {
        state.batchLoading = false;
        reloadButton.disabled = false;
        window.setTimeout(() => {
          syncBulkStatsButton();
        }, 900);
      }
    }

    async function loadOverview() {
      const button = document.getElementById("reloadButton");
      button.disabled = true;
      button.textContent = "Обновляю…";
      try {
        const overview = await fetchOverview();
        state.overview = overview;
        renderHeader(overview);
        renderScheduled(overview.scheduled || []);
        renderPosted(overview.posted || []);
      } catch (error) {
        document.getElementById("scheduledList").innerHTML = `<div class="empty">Не удалось загрузить очередь.</div>`;
        document.getElementById("postedList").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
      } finally {
        button.disabled = false;
        button.textContent = "Обновить";
      }
    }

    document.addEventListener("click", async (event) => {
      const button = event.target.closest('[data-action="stats"]');
      if (!button) return;
      const item = button.closest("[data-item-id]");
      if (!item) return;
      const itemId = item.getAttribute("data-item-id");
      if (!itemId) return;
      await loadItemStats(itemId, { button });
    });

    document.getElementById("reloadButton").addEventListener("click", loadOverview);
    document.getElementById("bulkStatsButton").addEventListener("click", loadAllPostedStats);
    loadOverview();
  </script>
</body>
</html>
"""


def now_utc_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def text_preview(text_file: Path, limit: int = 220) -> tuple[str, int]:
    if not text_file.exists():
        return "Файл поста не найден.", 0
    content = text_file.read_text(encoding="utf-8").strip()
    compact = " ".join(content.split())
    if len(compact) > limit:
        compact = compact[: limit - 1].rstrip() + "…"
    return compact, len(content)


def flatten_insights(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in payload.get("data", []):
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        value = None
        if isinstance(item.get("values"), list) and item["values"]:
            first = item["values"][0]
            if isinstance(first, dict):
                value = first.get("value")
            else:
                value = first
        elif isinstance(item.get("total_value"), dict):
            value = item["total_value"].get("value")
        elif "value" in item:
            value = item["value"]
        result[name] = value
    return result


@dataclass
class DashboardContext:
    root: Path
    queue_file: Path

    def config(self) -> dict[str, str]:
        return tp.get_config(self.root)

    def queue_payload(self) -> dict[str, Any]:
        return tp.load_json(self.queue_file)

    def item_card(self, item: dict[str, Any]) -> dict[str, Any]:
        preview, chars = text_preview(Path(item["text_file"]))
        permalink = item.get("thread_ids", [None])[0] if item.get("thread_ids") else None
        return {
            "id": item.get("id"),
            "status": item.get("status"),
            "relative_path": item.get("relative_path"),
            "publish_at_local": item.get("publish_at_local"),
            "publish_at_utc": item.get("publish_at_utc"),
            "posted_at": item.get("posted_at"),
            "attempts": item.get("attempts", 0),
            "publish_backend": item.get("publish_backend"),
            "preview": preview,
            "text_chars": chars,
            "permalink": permalink,
            "last_error": item.get("last_error"),
        }

    def overview(self) -> dict[str, Any]:
        payload = self.queue_payload()
        items = payload.get("items", [])
        scheduled = sorted(
            [self.item_card(item) for item in items if item.get("status") == "pending"],
            key=lambda item: item.get("publish_at_utc") or "",
        )
        posted = sorted(
            [self.item_card(item) for item in items if item.get("status") == "posted"],
            key=lambda item: item.get("posted_at") or item.get("publish_at_utc") or "",
            reverse=True,
        )
        interrupted = [self.item_card(item) for item in items if item.get("status") == "interrupted"]
        counts = {
            "total": len(items),
            "pending": len(scheduled),
            "posted": len(posted),
            "interrupted": len(interrupted),
        }
        return {
            "generated_at": payload.get("generated_at") or now_utc_iso(),
            "timezone": payload.get("timezone"),
            "counts": counts,
            "scheduled": scheduled,
            "posted": posted,
        }

    def post_stats(self, item_id: str) -> dict[str, Any]:
        payload = self.queue_payload()
        item = next((entry for entry in payload.get("items", []) if entry.get("id") == item_id), None)
        if not item:
            raise RuntimeError(f"Queue item not found: {item_id}")
        if item.get("status") != "posted":
            raise RuntimeError("Statistics are available only for posted items.")
        refs = item.get("thread_ids") or []
        if not refs:
            raise RuntimeError("This queue item does not have a published Threads URL.")

        api = tp.create_threads_api(self.config())
        resolved = tp.resolve_post_reference(api, str(refs[0]), limit=100, max_pages=10)
        media = resolved.get("media", {})
        insights_raw = api.get_media_insights(str(media["id"]), tp.parse_csv_values(tp.DEFAULT_POST_INSIGHT_METRICS))
        return {
            "fetched_at": now_utc_iso(),
            "item": {
                "id": item.get("id"),
                "posted_at": item.get("posted_at"),
                "publish_at_local": item.get("publish_at_local"),
                "publish_at_utc": item.get("publish_at_utc"),
            },
            "post": {
                "id": media.get("id"),
                "permalink": media.get("permalink"),
                "timestamp": media.get("timestamp"),
                "shortcode": media.get("shortcode"),
                "text": media.get("text"),
            },
            "metrics": flatten_insights(insights_raw),
        }


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    context: DashboardContext

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path or "/"
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path in {"/", "/index.html"}:
                self.respond_html(HTML_PAGE)
                return
            if path == "/api/overview":
                self.respond_json(self.context.overview())
                return
            if path == "/api/post-stats":
                item_id = query.get("item_id", [""])[0].strip()
                if not item_id:
                    self.respond_json({"error": "Missing ?item_id="}, status=400)
                    return
                self.respond_json(self.context.post_stats(item_id))
                return
            self.respond_json({"error": "Not found"}, status=404)
        except Exception as exc:  # noqa: BLE001
            self.respond_json({"error": str(exc)}, status=500)

    def respond_html(self, content: str, status: int = 200) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve a tiny Threads dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--queue-file", default="state/threads-publish-queue.json")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    context = DashboardContext(
        root=root,
        queue_file=(root / args.queue_file).resolve(),
    )

    class BoundHandler(DashboardHandler):
        pass

    BoundHandler.context = context
    server = http.server.ThreadingHTTPServer((args.host, args.port), BoundHandler)
    print(f"Threads dashboard listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
