#!/usr/bin/env python3
"""Generate Threads posts for the next day using OpenRouter tool_use."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time as time_module
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ALMATY = ZoneInfo("Asia/Almaty")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SLOTS = "09-00,10-30,12-00,13-30,15-00,16-30,18-00,19-30,21-00,22-00"
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_MODEL = "openai/gpt-4o"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRY_DELAYS = [30, 60, 120]

WEEKDAYS_RU = {
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье",
}

CONTENT_GUARDRAILS = """
## Стиль и качество постов

Ты пишешь от лица основателя AI-компании в Казахстане. Пиши как будто рассказываешь коллеге за кофе. Не как копирайтер, не как ChatGPT, не как маркетолог.

### Тон
- Спокойный, наблюдательный, с лёгкой иронией.
- Много первого лица: "я видел", "мы проверили", "один клиент рассказал".
- Конкретные цифры, имена ниш, названия систем (1С, Bitrix24, Kommo, WhatsApp).
- Короткие предложения. Абзацы по 1-3 предложения.
- Заканчивай сильной мыслью, а не призывом к действию.

### Формат
- 350-550 символов.
- Первая строка = хук. Цитата, парадокс, наблюдение, конкретная ситуация.
- НЕ начинай с вопроса. НЕ начинай с "Многие думают...", "В современном мире...", "AI помогает...".
- Одна история или наблюдение → одно последствие → одна мысль.
- Никаких списков, пунктов, нумерации.
- В поле cta всегда пиши просто "CTA:" — мы не используем призывы.

### 3 лейна (чередуй в течение дня)
1. Что стало возможно / конкретная capability / новизна
2. Где теряются деньги / диагностика / управленческая слепота
3. Полевая история / наблюдение / личная позиция

### ОБЯЗАТЕЛЬНЫЕ ПРИЁМЫ (используй в каждом посте минимум один)
- Конкретная цифра: "17 заявок", "28 часов тишины", "4% конверсия"
- Конкретная ниша: клиника, учебный центр, стройка, поставщик, торговля
- Конкретная ситуация: что случилось, с кем, что нашли

### Примеры ХОРОШИХ постов (копируй стиль, не содержание):

Вчера общался с владельцем сервисной компании. 40 сотрудников, нормальный оборот. Говорит: "У нас с продажами все ок, просто рынок сжался."

Залезли в WhatsApp его менеджеров. За неделю — 17 заявок без ответа. Семнадцать. Просто потерялись между чатами.

Он не знал. Менеджеры не специально. Никто не виноват. Просто нет системы, которая скажет: "Эй, тут человек ждет ответ вторые сутки."

Бизнес теряет деньги тихо. Без скандалов, без ошибок в отчетах. Заявка просто растворяется — и все.

---

"Давайте заведём общий чат для заявок — все будут видеть!"

Знакомо? Звучит логично. На практике — ловушка.

Когда видят все, отвечает никто. Каждый думает, что другой уже взял.

Заявка висит в чате. Все видели. Никто не взял. Клиент ушёл.

Один владелец на каждую заявку. Это скучно, но это работает.

---

Любимая ошибка собственника: "Надо просто нанять сильнее".

Потом приходят сильные менеджеры и попадают в ту же яму: заявки теряются, follow-up на памяти, руководитель видит картину задним числом.

Через пару месяцев даже хороший менеджер начинает выглядеть "слабым". Не потому что разучился продавать, а потому что система съедает дисциплину быстрее, чем человек успевает её поддерживать.

### Примеры ПЛОХИХ постов (так НЕ писать):
- "AI-надстройки помогают бизнесу быть эффективнее" — абстрактно, пусто
- "В современном мире скорость ответа имеет решающее значение" — канцелярит
- "Это не революция, это эволюция обычного рабочего процесса" — клише
- "Искусственный интеллект здесь становится инструментом" — объяснялка
- "Контроль нужен не только на больших объемах" — банальность
- Любой текст, который звучит как статья на VC.ru или пресс-релиз
"""


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}")


# --- Helpers ---


def get_week_folder(d: date) -> str:
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return f"week-{monday.isoformat()}-to-{sunday.isoformat()}"


def resolve_target_date(value: str) -> date:
    if value == "tomorrow":
        return (datetime.now(ALMATY) + timedelta(days=1)).date()
    if value == "auto":
        raise ValueError("Use find_next_empty_dates() for auto mode")
    return date.fromisoformat(value)


def find_day_dir(content_root: Path, d: date) -> Path | None:
    """Find existing day directory across any week folder."""
    for day_dir in content_root.glob(f"week-*/{d.isoformat()}"):
        if day_dir.is_dir():
            return day_dir
    return None


def count_existing_posts(content_root: Path, d: date, configured_slots: list[str]) -> int:
    """Count how many configured slots already have .md files for a date."""
    day_dir = find_day_dir(content_root, d)
    if not day_dir:
        return 0
    return sum(1 for s in configured_slots if (day_dir / f"{s}.md").exists())


def find_next_empty_dates(content_root: Path, configured_slots: list[str], days_ahead: int) -> list[date]:
    """Find dates from tomorrow to tomorrow+days_ahead that don't have all posts yet."""
    today = datetime.now(ALMATY).date()
    empty_dates = []
    for offset in range(1, days_ahead + 1):
        d = today + timedelta(days=offset)
        existing = count_existing_posts(content_root, d, configured_slots)
        if existing < len(configured_slots):
            empty_dates.append(d)
            log(f"AUTO: {d.isoformat()} has {existing}/{len(configured_slots)} posts — needs generation")
        else:
            log(f"AUTO: {d.isoformat()} has {existing}/{len(configured_slots)} posts — full")
    return empty_dates


# --- Tool functions ---


def list_recent_posts(content_root: Path, ref_date: date, days: int) -> list[dict]:
    """Return titles of posts from the last N days."""
    cutoff = ref_date - timedelta(days=days)
    results = []
    for md_file in sorted(content_root.glob("week-*/????-??-??/*.md")):
        if md_file.name == "README.md":
            continue
        day_dir = md_file.parent.name
        try:
            post_date = date.fromisoformat(day_dir)
        except ValueError:
            continue
        if not (cutoff <= post_date <= ref_date):
            continue
        time_slot = md_file.stem
        title = ""
        first_line = md_file.read_text(encoding="utf-8").split("\n", 1)[0]
        if "—" in first_line:
            title = first_line.split("—", 1)[1].strip()
        results.append({
            "date": day_dir,
            "time_slot": time_slot,
            "title": title,
        })
    return results


def get_slots(content_root: Path, target_date: date, configured_slots: list[str]) -> dict:
    """Check which slots are free/occupied for the target date."""
    day_dir = find_day_dir(content_root, target_date)
    free = []
    occupied = []
    for slot in configured_slots:
        if day_dir and (day_dir / f"{slot}.md").exists():
            occupied.append(slot)
        else:
            free.append(slot)
    return {
        "date": target_date.isoformat(),
        "free_slots": free,
        "occupied_slots": occupied,
    }


def create_post(
    content_root: Path,
    target_date: date,
    configured_slots: list[str],
    time_slot: str,
    title: str,
    step: str,
    fmt: str,
    goal: str,
    body: str,
    cta: str,
) -> dict:
    """Write a .md post file. Returns status dict."""
    if time_slot not in configured_slots:
        return {"status": "error", "message": f"Invalid time_slot: {time_slot}"}
    if not body or not body.strip():
        return {"status": "error", "message": "Body is empty"}
    if len(body) < 50:
        return {"status": "error", "message": f"Body too short: {len(body)} chars (min 50). Write 300-600 chars."}
    if len(body) > 600:
        return {"status": "error", "message": f"Body too long: {len(body)} chars (max 600)"}

    week_folder = get_week_folder(target_date)
    day_dir = content_root / week_folder / target_date.isoformat()
    md_path = day_dir / f"{time_slot}.md"

    if md_path.exists():
        return {"status": "already_exists", "path": str(md_path)}

    day_dir.mkdir(parents=True, exist_ok=True)
    time_formatted = time_slot.replace("-", ":")
    weekday_ru = WEEKDAYS_RU[target_date.weekday()]

    content = (
        f"# {time_formatted} — {title}\n\n"
        f"- Дата: {target_date.isoformat()}\n"
        f"- День: {weekday_ru}\n"
        f"- Время публикации: {time_formatted}\n"
        f"- Шаг стратегии: {step}\n"
        f"- Формат: {fmt}\n"
        f"- Цель: {goal}\n\n"
        f"## Текст поста\n\n"
        f"{body}\n\n"
        f"## CTA\n\n"
        f"{cta}\n"
    )
    md_path.write_text(content, encoding="utf-8")

    char_count = len(body)
    if char_count < 300 or char_count > 600:
        log(f"WARNING: post {time_slot} body is {char_count} chars (target 300-600)")

    return {"status": "created", "path": str(md_path)}


# --- Tool dispatch ---


def execute_tool(
    name: str,
    arguments: dict,
    target_date: date,
    content_root: Path,
    configured_slots: list[str],
    lookback_days: int,
) -> dict | list:
    """Dispatch a tool call to the right function."""
    log(f"TOOL {name}({json.dumps(arguments, ensure_ascii=False)[:200]})")

    if name == "list_recent_posts":
        days = arguments.get("days", lookback_days)
        return list_recent_posts(content_root, target_date, days)

    if name == "get_slots":
        d = date.fromisoformat(arguments["date"]) if "date" in arguments else target_date
        return get_slots(content_root, d, configured_slots)

    if name == "create_post":
        return create_post(
            content_root=content_root,
            target_date=target_date,
            configured_slots=configured_slots,
            time_slot=arguments["time_slot"],
            title=arguments["title"],
            step=arguments["step"],
            fmt=arguments.get("format", ""),
            goal=arguments["goal"],
            body=arguments["body"],
            cta=arguments.get("cta", "CTA:"),
        )

    return {"status": "error", "message": f"Unknown tool: {name}"}


# --- System prompt ---


def build_system_prompt(strategy_root: Path) -> str:
    """Build system prompt from strategy docs. Only reads README.md files to keep size manageable."""
    parts = [
        "Ты контент-менеджер Threads-аккаунта про AI-надстройки для бизнеса в Казахстане.",
        "",
        "Тематика: AI-надстройки поверх существующих систем (1С, CRM, Bitrix24, Kommo) для малого и среднего бизнеса в Казахстане.",
        "Ниши: клиники, учебные центры, торговля, стройка, поставщики.",
        "Продукт: спасение заявок, автоматизация follow-up, прозрачность для руководителя.",
        "",
    ]

    # Read only README.md files from strategy (summaries, not full details)
    strategy_text = []
    for md_file in sorted(strategy_root.rglob("README.md")):
        strategy_text.append(md_file.read_text(encoding="utf-8"))
    if strategy_text:
        parts.append("## Стратегия\n")
        parts.append("\n\n---\n\n".join(strategy_text))

    parts.append(CONTENT_GUARDRAILS)

    parts.append(
        "## ПОРЯДОК ДЕЙСТВИЙ (строго по шагам)\n\n"
        "Шаг 1. Вызови list_recent_posts() — посмотри что уже было.\n"
        "Шаг 2. Вызови get_slots() — посмотри свободные слоты.\n"
        "Шаг 3. Для КАЖДОГО свободного слота вызови create_post(). "
        "Ты ДОЛЖЕН создать пост для КАЖДОГО свободного слота. Не останавливайся пока все слоты не заполнены. "
        "Если свободных слотов 10, вызови create_post ровно 10 раз.\n\n"
        "ВАЖНО:\n"
        "- Отвечай ТОЛЬКО вызовами функций. Никакого текста.\n"
        "- Каждый пост: 300-600 символов.\n"
        "- Пиши на русском языке.\n"
        "- Не повторяй темы из недавних постов.\n"
        "- Ротируй лейны: новизна/capability, потери/диагностика, наблюдение/история.\n"
    )

    prompt = "\n".join(parts)

    if len(prompt) > 50000:
        log(f"WARNING: system prompt is {len(prompt)} chars (>50k)")
    else:
        log(f"System prompt: {len(prompt)} chars")

    return prompt


# --- OpenRouter API ---


def build_tool_definitions() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "list_recent_posts",
                "description": "List titles of recent Threads posts from the last N days. Use this to see what topics have already been covered.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "Number of days to look back (default 14)",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_slots",
                "description": "Check which time slots are free or occupied for the target date.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "Target date in YYYY-MM-DD format",
                        },
                    },
                    "required": ["date"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_post",
                "description": "Create a new Threads post for a specific time slot. Writes the .md file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "time_slot": {"type": "string", "description": "Time slot, e.g. '09-00'"},
                        "title": {"type": "string", "description": "Post title (without time prefix)"},
                        "step": {"type": "string", "description": "Strategy step: 'Шаг 1', 'Шаг 2', or 'Шаг 3'"},
                        "format": {"type": "string", "description": "Post format, e.g. 'Короткий список', 'Вертикальное наблюдение'"},
                        "goal": {"type": "string", "description": "One-line goal of the post"},
                        "body": {"type": "string", "description": "The post text (target 300-600 chars). Must be substantial, with a story or observation."},
                        "cta": {"type": "string", "description": "Call-to-action text"},
                    },
                    "required": ["time_slot", "title", "step", "format", "goal", "body", "cta"],
                },
            },
        },
    ]


def call_openrouter(messages: list[dict], tools: list[dict], config: dict) -> dict:
    """Call OpenRouter API with retry on transient failures."""
    payload = {
        "model": config["OPENROUTER_MODEL"],
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {config['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json",
    }

    last_error = None
    for attempt, delay in enumerate([0] + RETRY_DELAYS):
        if delay:
            log(f"Retrying in {delay}s (attempt {attempt + 1})...")
            time_module.sleep(delay)
        try:
            req = urllib.request.Request(OPENROUTER_URL, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            log(f"OpenRouter request failed: {exc}")

    raise RuntimeError(f"OpenRouter API failed after {len(RETRY_DELAYS) + 1} attempts: {last_error}")


# --- Agentic loop ---


def run_generation(target_date: date, content_root: Path, config: dict) -> tuple[list[dict], int]:
    """Run the agentic loop: call OpenRouter, dispatch tools, repeat."""
    configured_slots = config.get("THREADS_GENERATE_SLOTS", DEFAULT_SLOTS).split(",")
    lookback_days = int(config.get("THREADS_GENERATE_LOOKBACK_DAYS", str(DEFAULT_LOOKBACK_DAYS)))

    system_prompt = build_system_prompt(Path(config.get("STRATEGY_ROOT", str(PROJECT_ROOT / "threads-strategy"))))
    tools = build_tool_definitions()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Сгенерируй 10 постов на {target_date.isoformat()}"},
    ]

    max_iterations = 30
    total_tokens = 0
    created_posts = []

    for iteration in range(max_iterations):
        log(f"Loop iteration {iteration + 1}/{max_iterations}")
        response = call_openrouter(messages, tools, config)
        usage = response.get("usage", {})
        total_tokens += usage.get("total_tokens", 0)

        choice = response["choices"][0]["message"]

        tool_calls = choice.get("tool_calls")
        if not tool_calls:
            # Check how many slots are still free
            slots_result = get_slots(content_root, target_date, configured_slots)
            remaining = len(slots_result["free_slots"])

            if remaining > 0 and iteration < max_iterations - 1:
                log(f"Model stopped but {remaining} slots still free, nudging")
                messages.append(choice)
                messages.append({
                    "role": "user",
                    "content": f"Ещё {remaining} слотов не заполнены: {', '.join(slots_result['free_slots'])}. "
                    f"Вызови create_post() для каждого из них. Не останавливайся.",
                })
                continue
            log("Model finished (no more tool calls)")
            break

        # Append full assistant message once
        messages.append(choice)

        # Execute each tool and append results
        for tc in tool_calls:
            fn = tc["function"]
            try:
                arguments = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
            except json.JSONDecodeError:
                arguments = {}

            result = execute_tool(
                name=fn["name"],
                arguments=arguments,
                target_date=target_date,
                content_root=content_root,
                configured_slots=configured_slots,
                lookback_days=lookback_days,
            )

            if isinstance(result, dict) and result.get("status") == "created":
                created_posts.append(result)

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })
    else:
        log(f"WARNING: max iterations ({max_iterations}) reached")

    return created_posts, total_tokens


# --- Review & Rewrite pipeline ---

DEFAULT_REVIEW_MODEL = "google/gemini-2.5-pro"
DEFAULT_REWRITE_MODEL = "google/gemini-2.5-flash"

REVIEW_PROMPT = """Ты редактор Threads-аккаунта. Проверь каждый пост по критериям:

1. Первая строка — хук? (цитата, парадокс, конкретная ситуация — НЕ вопрос, НЕ "Многие думают...")
2. Есть конкретная цифра, ниша или ситуация?
3. Тон — живой, от первого лица, как рассказ коллеге? (НЕ статья, НЕ пресс-релиз)
4. 350-550 символов?
5. Нет клише: "инновационный", "эффективный", "в современном мире", "это не революция, это эволюция"
6. Нет CTA типа "узнайте", "свяжитесь", "подпишитесь"
7. Заканчивается сильной мыслью, а не банальностью

Для каждого поста ответь СТРОГО в формате:
СЛОТ: HH-MM
ВЕРДИКТ: OK или ПЕРЕПИСАТЬ
ПРОБЛЕМЫ: (только если ПЕРЕПИСАТЬ) конкретно что не так
НАПРАВЛЕНИЕ: (только если ПЕРЕПИСАТЬ) как исправить, какой угол взять

Не переписывай посты сам. Только дай feedback.
"""

REWRITE_PROMPT = """Ты автор Threads-аккаунта про AI-надстройки для бизнеса в Казахстане.
Тон: спокойный, от первого лица, как рассказ коллеге. Конкретные цифры, ниши, ситуации.
350-550 символов. Без списков. Без CTA.

Ниже посты и замечания редактора. Перепиши ТОЛЬКО те посты, где вердикт ПЕРЕПИСАТЬ.
Для каждого переписанного поста выведи СТРОГО в формате:
СЛОТ: HH-MM
ТЕКСТ:
(новый текст поста)
---

Посты с вердиктом OK не трогай, не выводи.
"""


def read_generated_posts(content_root: Path, target_date: date) -> list[dict]:
    """Read all generated .md posts for a date."""
    week_folder = get_week_folder(target_date)
    day_dir = content_root / week_folder / target_date.isoformat()
    posts = []
    if not day_dir.exists():
        return posts
    for md_file in sorted(day_dir.glob("*.md")):
        if md_file.name == "README.md":
            continue
        text = md_file.read_text(encoding="utf-8")
        # Extract body from ## Текст поста
        body = ""
        lines = text.split("\n")
        capturing = False
        body_lines = []
        for line in lines:
            if line.strip().lower().startswith("## текст поста"):
                capturing = True
                continue
            if capturing and line.strip().startswith("## "):
                break
            if capturing:
                body_lines.append(line)
        body = "\n".join(body_lines).strip()
        posts.append({
            "slot": md_file.stem,
            "body": body,
            "path": str(md_file),
        })
    return posts


def run_review(posts: list[dict], config: dict) -> str:
    """Call review model to get feedback on posts."""
    review_model = config.get("OPENROUTER_REVIEW_MODEL", DEFAULT_REVIEW_MODEL)
    log(f"REVIEW: sending {len(posts)} posts to {review_model}")

    posts_text = "\n\n".join(
        f"СЛОТ: {p['slot']}\nТЕКСТ ({len(p['body'])} символов):\n{p['body']}"
        for p in posts
    )

    messages = [
        {"role": "system", "content": REVIEW_PROMPT},
        {"role": "user", "content": posts_text},
    ]

    review_config = {**config, "OPENROUTER_MODEL": review_model}
    response = call_openrouter(messages, [], review_config)
    feedback = response["choices"][0]["message"].get("content", "")
    tokens = response.get("usage", {}).get("total_tokens", 0)
    log(f"REVIEW: got feedback, {tokens} tokens")
    return feedback, tokens


def run_rewrite(posts: list[dict], feedback: str, config: dict) -> dict[str, str]:
    """Call rewrite model to fix posts based on feedback. Returns {slot: new_body}."""
    # Check if any posts need rewriting
    if "ПЕРЕПИСАТЬ" not in feedback:
        log("REWRITE: all posts OK, skipping")
        return {}, 0

    rewrite_model = config.get("OPENROUTER_REWRITE_MODEL", DEFAULT_REWRITE_MODEL)
    log(f"REWRITE: sending to {rewrite_model}")

    posts_text = "\n\n".join(
        f"СЛОТ: {p['slot']}\nТЕКСТ:\n{p['body']}"
        for p in posts
    )

    messages = [
        {"role": "system", "content": REWRITE_PROMPT},
        {"role": "user", "content": f"ПОСТЫ:\n\n{posts_text}\n\nОТЗЫВ РЕДАКТОРА:\n\n{feedback}"},
    ]

    rewrite_config = {**config, "OPENROUTER_MODEL": rewrite_model}
    response = call_openrouter(messages, [], rewrite_config)
    result_text = response["choices"][0]["message"].get("content", "")
    tokens = response.get("usage", {}).get("total_tokens", 0)

    # Parse rewrites
    rewrites = {}
    current_slot = None
    current_lines = []
    for line in result_text.split("\n"):
        if line.startswith("СЛОТ:"):
            if current_slot and current_lines:
                rewrites[current_slot] = "\n".join(current_lines).strip()
            current_slot = line.split(":", 1)[1].strip()
            current_lines = []
        elif line.startswith("ТЕКСТ:"):
            current_lines = []
        elif line.strip() == "---":
            if current_slot and current_lines:
                rewrites[current_slot] = "\n".join(current_lines).strip()
            current_slot = None
            current_lines = []
        elif current_slot is not None:
            current_lines.append(line)
    if current_slot and current_lines:
        rewrites[current_slot] = "\n".join(current_lines).strip()

    log(f"REWRITE: {len(rewrites)} posts rewritten, {tokens} tokens")
    return rewrites, tokens


def apply_rewrites(content_root: Path, target_date: date, rewrites: dict[str, str]) -> int:
    """Overwrite .md post bodies with rewritten text."""
    week_folder = get_week_folder(target_date)
    day_dir = content_root / week_folder / target_date.isoformat()
    applied = 0
    for slot, new_body in rewrites.items():
        if not new_body or len(new_body) < 50:
            log(f"REWRITE: skipping {slot}, body too short ({len(new_body)} chars)")
            continue
        md_path = day_dir / f"{slot}.md"
        if not md_path.exists():
            continue
        old_content = md_path.read_text(encoding="utf-8")
        # Replace body between ## Текст поста and ## CTA
        lines = old_content.split("\n")
        new_lines = []
        skip = False
        inserted = False
        for line in lines:
            if line.strip().lower().startswith("## текст поста"):
                new_lines.append(line)
                new_lines.append("")
                new_lines.append(new_body)
                new_lines.append("")
                skip = True
                inserted = True
                continue
            if skip and line.strip().startswith("## "):
                skip = False
            if not skip:
                new_lines.append(line)
        if inserted:
            md_path.write_text("\n".join(new_lines), encoding="utf-8")
            log(f"REWRITE: applied to {slot} ({len(new_body)} chars)")
            applied += 1
    return applied


# --- Logging ---


def write_log_entry(log_path: Path, entry: dict) -> None:
    entry["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --- Post-generation steps ---


def run_post_generation_steps(content_root: Path, week_folder: str) -> None:
    """Run export-txt and build-queue after generation."""
    week_path = content_root / week_folder
    cli = str(PROJECT_ROOT / "cli.py")

    log(f"Running export-txt for {week_path}")
    subprocess.run(
        [sys.executable, cli, "export-txt", "threads", "--content-root", str(week_path)],
        timeout=30,
    )

    queue_file = str(PROJECT_ROOT / "state" / "threads-publish-queue.json")
    log(f"Running build-queue for {week_path}")
    subprocess.run(
        [sys.executable, cli, "build-queue", "threads",
         "--content-root", str(week_path),
         "--queue-file", queue_file],
        timeout=30,
    )


# --- Main ---


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Threads posts for a target date")
    parser.add_argument("--date", required=True, help="Target date: 'auto', 'tomorrow', or YYYY-MM-DD")
    parser.add_argument("--days-ahead", type=int, default=4, help="For auto mode: how many days ahead to check (default 4)")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("OPENROUTER_API_KEY is required", file=sys.stderr)
        return 1

    config = {
        "OPENROUTER_API_KEY": api_key,
        "OPENROUTER_MODEL": os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL),
        "OPENROUTER_REVIEW_MODEL": os.environ.get("OPENROUTER_REVIEW_MODEL", DEFAULT_REVIEW_MODEL),
        "OPENROUTER_REWRITE_MODEL": os.environ.get("OPENROUTER_REWRITE_MODEL", DEFAULT_REWRITE_MODEL),
        "THREADS_GENERATE_SLOTS": os.environ.get("THREADS_GENERATE_SLOTS", DEFAULT_SLOTS),
        "THREADS_GENERATE_LOOKBACK_DAYS": os.environ.get("THREADS_GENERATE_LOOKBACK_DAYS", str(DEFAULT_LOOKBACK_DAYS)),
        "STRATEGY_ROOT": str(PROJECT_ROOT / "threads-strategy"),
    }

    content_root = PROJECT_ROOT / "content" / "threads"
    log_path = PROJECT_ROOT / "state" / "threads-generate.jsonl"
    configured_slots = config["THREADS_GENERATE_SLOTS"].split(",")

    # Resolve target dates
    if args.date == "auto":
        empty = find_next_empty_dates(content_root, configured_slots, args.days_ahead)
        if not empty:
            log(f"AUTO: all days covered for next {args.days_ahead} days, nothing to do")
            return 0
        # Generate for the first empty day only
        targets = [empty[0]]
        log(f"AUTO: first empty day is {targets[0].isoformat()}")
    else:
        targets = [resolve_target_date(args.date)]

    total_failures = 0
    for target in targets:
        log(f"=== Generating for {target.isoformat()} ===")
        start_time = time_module.time()

        try:
            # Step 1: Flash generates drafts
            created_posts, total_tokens = run_generation(target, content_root, config)
            log(f"Step 1 (generate): {len(created_posts)} posts, {total_tokens} tokens")

            # Step 2: Pro reviews
            if created_posts:
                posts = read_generated_posts(content_root, target)
                feedback, review_tokens = run_review(posts, config)
                total_tokens += review_tokens
                log(f"Step 2 (review): {review_tokens} tokens")

                # Step 3: Flash rewrites based on feedback
                rewrites, rewrite_tokens = run_rewrite(posts, feedback, config)
                total_tokens += rewrite_tokens
                if rewrites:
                    applied = apply_rewrites(content_root, target, rewrites)
                    log(f"Step 3 (rewrite): {applied} posts rewritten, {rewrite_tokens} tokens")
                else:
                    log(f"Step 3 (rewrite): nothing to rewrite")

            duration = round(time_module.time() - start_time, 1)
            log(f"Pipeline complete for {target}: {len(created_posts)} posts, {total_tokens} tokens, {duration}s")

            week_folder = get_week_folder(target)
            if created_posts:
                run_post_generation_steps(content_root, week_folder)

            write_log_entry(log_path, {
                "event": "generation_complete",
                "target_date": target.isoformat(),
                "model": config["OPENROUTER_MODEL"],
                "review_model": config["OPENROUTER_REVIEW_MODEL"],
                "posts_created": len(created_posts),
                "slots": [p.get("path", "").split("/")[-1].replace(".md", "") for p in created_posts],
                "total_tokens": total_tokens,
                "duration_seconds": duration,
            })

        except Exception as exc:
            duration = round(time_module.time() - start_time, 1)
            log(f"Generation failed for {target}: {exc}")
            write_log_entry(log_path, {
                "event": "generation_failed",
                "target_date": target.isoformat(),
                "error": str(exc),
                "duration_seconds": duration,
            })
            total_failures += 1

    return 1 if total_failures == len(targets) else 0


if __name__ == "__main__":
    raise SystemExit(main())
