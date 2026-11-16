#!/usr/bin/env python3
"""
Разбивает исходный файл «剑来_全集_1198章.txt» на отдельные файлы глав в папке chapters/.

Особенности обработки:
1. Сохраняет аннотацию книги в `chapters/0000_synopsis.txt`.
2. Предисловие автора (`新书感言`) сохраняет в `chapters/0000.txt`, чтобы `chapters/0001.txt`
   напрямую соответствовал первой главе (`第一章 惊蛰`), `0002.txt` — второй и т.д.
3. Очищает 21 встроенный водяной знак пиратских сайтов вида `[笔趣阁 www.xxx.yyy]`,
   разрывавший китайские слова прямо посреди предложения (например, `大[...]骊` -> `大骊`),
   не затрагивая оригинальный монолитный файл в корне репозитория.
4. Генерирует `chapters/index.json` и `chapters/README.md` с полной картой глав,
   отмечая дубликаты источника, авторские обращения и главы-заглушки (`正在手打中`).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SOURCE_FILE = Path("剑来_全集_1198章.txt")
OUTPUT_DIR = Path("chapters")

# В исходнике все 21 квадратные скобки [...] — водяные знаки сайтов-агрегаторов с доменами
WATERMARK_RE = re.compile(
    r"\[[^\]]*(?:www\.|http|\.com|\.net|\.org|\.info|\.vip|\.xyz|\.biz|\.cc|\.la|\.me)[^\]]*\]"
)

CHAPTER_HEADER_RE = re.compile(r"^第([零〇一二两三四五六七八九十百千0-9]+)[章张]\s*(.*)$")

CN_NUM = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def parse_chinese_number(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    # Обработка опечаток автора вроде «四五百五十二» (452): если два числа перед «百», берём первое
    if raw == "四五百五十二":
        return 452
    total = 0
    num = 0
    for idx, ch in enumerate(raw):
        if ch in CN_NUM:
            num = CN_NUM[ch]
        elif ch in CN_UNIT:
            unit = CN_UNIT[ch]
            if num == 0 and ch == "十" and idx == 0:
                num = 1
            total += num * unit
            num = 0
        else:
            return None
    return total + num


def classify_entry(seq_id: int, title: str, body: str) -> tuple[str, str]:
    """Возвращает (entry_type, status)."""
    if "正在手打中" in body:
        entry_type = "chapter" if CHAPTER_HEADER_RE.match(title) else "author_notice"
        return entry_type, "missing_stub"
    if seq_id == 0 or title == "新书感言":
        return "foreword", "ok"
    if not CHAPTER_HEADER_RE.match(title):
        return "author_note", "ok"
    return "chapter", "ok"


def main() -> None:
    if not SOURCE_FILE.exists():
        raise FileNotFoundError(f"Не найден исходный файл: {SOURCE_FILE}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    raw_text = SOURCE_FILE.read_text(encoding="utf-8")
    lines = raw_text.splitlines()

    # Первые 10 строк до разделителя '====' — метаданные и синопсис книги
    separator_idx = next(
        i for i, line in enumerate(lines) if line.startswith("====")
    )
    synopsis_text = "\n".join(lines[:separator_idx]).strip() + "\n"
    (OUTPUT_DIR / "0000_synopsis.txt").write_text(synopsis_text, encoding="utf-8")

    # Заголовки глав — все непустые строки после разделителя без отступа '　　'
    header_indices = [
        i
        for i in range(separator_idx + 1, len(lines))
        if lines[i].strip() and not lines[i].startswith("　　")
    ]

    manifest_entries: list[dict] = []
    seen_hashes: dict[str, str] = {}
    total_watermarks_removed = 0

    for seq_id, start_line in enumerate(header_indices):
        end_line = (
            header_indices[seq_id + 1]
            if seq_id + 1 < len(header_indices)
            else len(lines)
        )
        raw_title = lines[start_line].strip()
        raw_body = "\n".join(lines[start_line + 1 : end_line]).strip()

        watermarks = WATERMARK_RE.findall(raw_body)
        cleaned_body = WATERMARK_RE.sub("", raw_body)
        total_watermarks_removed += len(watermarks)

        chapter_match = CHAPTER_HEADER_RE.match(raw_title)
        parsed_chapter_num = (
            parse_chinese_number(chapter_match.group(1)) if chapter_match else None
        )
        chapter_subtitle = chapter_match.group(2).strip() if chapter_match else raw_title

        entry_type, status = classify_entry(seq_id, raw_title, cleaned_body)

        full_chapter_text = f"{raw_title}\n\n{cleaned_body}\n"
        content_hash = hashlib.sha256(full_chapter_text.encode("utf-8")).hexdigest()[:16]

        file_id = f"{seq_id:04d}"
        filename = f"{file_id}.txt"
        duplicate_of = seen_hashes.get(content_hash)
        if duplicate_of is not None:
            status = "duplicate"
        else:
            seen_hashes[content_hash] = file_id

        (OUTPUT_DIR / filename).write_text(full_chapter_text, encoding="utf-8")

        # Подсчёт китайских иероглифов и знаков без пробелов/переносов
        non_ws_chars = len(re.sub(r"\s+", "", cleaned_body))

        manifest_entries.append(
            {
                "id": file_id,
                "file": filename,
                "raw_title": raw_title,
                "chapter_number_in_title": parsed_chapter_num,
                "subtitle": chapter_subtitle,
                "entry_type": entry_type,
                "status": status,
                "duplicate_of": duplicate_of,
                "char_count": non_ws_chars,
                "watermarks_removed": len(watermarks),
                "source_line_start": start_line + 1,
                "sha256_16": content_hash,
            }
        )

    summary = {
        "book_title_zh": "剑来",
        "book_title_ru_working": "Грядущий меч (Да прибудет меч)",
        "author_zh": "烽火戏诸侯",
        "author_ru": "Фэнхо Сичжухоу",
        "source_file": str(SOURCE_FILE),
        "synopsis_file": "0000_synopsis.txt",
        "total_entries": len(manifest_entries),
        "counts_by_status": {
            "ok": sum(1 for e in manifest_entries if e["status"] == "ok"),
            "duplicate": sum(1 for e in manifest_entries if e["status"] == "duplicate"),
            "missing_stub": sum(
                1 for e in manifest_entries if e["status"] == "missing_stub"
            ),
        },
        "counts_by_type": {
            "foreword": sum(1 for e in manifest_entries if e["entry_type"] == "foreword"),
            "chapter": sum(1 for e in manifest_entries if e["entry_type"] == "chapter"),
            "author_note": sum(
                1 for e in manifest_entries if e["entry_type"] == "author_note"
            ),
            "author_notice": sum(
                1 for e in manifest_entries if e["entry_type"] == "author_notice"
            ),
        },
        "total_watermarks_removed": total_watermarks_removed,
        "entries": manifest_entries,
    }

    (OUTPUT_DIR / "index.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Генерируем краткий README для навигации по папке chapters/
    anomalies = [
        e for e in manifest_entries if e["status"] != "ok" or e["entry_type"] != "chapter"
    ]
    readme_lines = [
        "# Главы оригинала 《剑来》 (`chapters/`)",
        "",
        "Папка содержит автоматическую разбивку исходного файла `剑来_全集_1198章.txt` с помощью скрипта `scripts/split_chapters.py`.",
        "",
        "## Схема нумерации файлов",
        "",
        "- `0000_synopsis.txt` — метаданные книги и авторская аннотация.",
        "- `0000.txt` — `新书感言` (Предисловие автора к новой книге).",
        "- `0001.txt` … `1197.txt` — основные главы и авторские записи (всего **1198** записей, `0001.txt` = `第一章 惊蛰`).",
        "- `index.json` — полный машиночитаемый реестр всех 1198 файлов (названия, объём в знаках, статус, хеши, очищенные водяные знаки).",
        "",
        "Почему используется сквозной 4-значный индекс `NNNN.txt`, а не номер из заголовка главы:",
        "у Фэнхо Сичжухоу в оригинальной веб-публикации встречаются опечатки нумерации (например, две разные главы под номером `第七百章`, опечатка `第四百二十张` вместо `章`, скачок `第八百八十一章` между 810 и 812 главами). Сквозной индекс гарантирует строгий хронологический порядок чтения и отсутствие перезаписи файлов.",
        "",
        "## Сводка по корпусу",
        "",
        f"- Всего записей: **{summary['total_entries']}**",
        f"- Полноценных глав/записей (`status: ok`): **{summary['counts_by_status']['ok']}**",
        f"- Точных дубликатов источника (`status: duplicate`): **{summary['counts_by_status']['duplicate']}**",
        f"- Глав-заглушек пиратского парсера (`status: missing_stub`): **{summary['counts_by_status']['missing_stub']}**",
        f"- Очищено встроенных водяных знаков `[笔趣阁 www...]`: **{summary['total_watermarks_removed']}**",
        "",
        "## Особые записи и аномалии исходника",
        "",
        "| Файл | Заголовок в оригинале | Тип | Статус | Примечание |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    for e in anomalies:
        note = ""
        if e["status"] == "duplicate":
            note = f"Точный дубликат файла `{e['duplicate_of']}.txt` (пропускать при переводе)"
        elif e["status"] == "missing_stub":
            note = "В исходнике с сайта заглушка `正在手打中...` (текст отсутствует)"
        elif e["entry_type"] == "foreword":
            note = "Авторское предисловие о замысле романа"
        elif e["entry_type"] == "author_note":
            note = "Послесловие автора к тому 《小夫子》 и разбор персонажей"
        readme_lines.append(
            f"| `{e['file']}` | {e['raw_title']} | `{e['entry_type']}` | `{e['status']}` | {note} |"
        )

    (OUTPUT_DIR / "README.md").write_text(
        "\n".join(readme_lines) + "\n", encoding="utf-8"
    )
    print(
        f"Готово: создано {len(manifest_entries)} файлов глав + 0000_synopsis.txt + index.json + README.md"
    )


if __name__ == "__main__":
    main()
