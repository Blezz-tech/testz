#!/usr/bin/env python3
"""
Утилита управления Лор-Вики (wiki/) для перевода новеллы 《剑来》.
Не требует сторонних библиотек (работает на стандартной библиотеке Python 3).

Команды:
  python3 scripts/wiki_tool.py build
      Собирает поисковый индекс `wiki/_index.json` и вычисляет обратные ссылки (backlinks).

  python3 scripts/wiki_tool.py check
      Проверяет целостность YAML-заголовков и всех перекрёстных Markdown-ссылок в `wiki/` и `docs/`.

  python3 scripts/wiki_tool.py scan chapters/0001.txt [--max-chapter 1]
      Сканирует китайский текст главы и выводит готовое досье найденных сущностей,
      топонимов, рангов и идиом (с защитой от спойлеров будущих глав).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

WIKI_DIR = Path("wiki")
DOCS_DIR = Path("docs")
INDEX_FILE = WIKI_DIR / "_index.json"

MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
REQUIRED_FIELDS = {"id", "type", "zh", "ru", "first_chapter"}


def parse_scalar(val: str) -> Any:
    val = val.strip()
    if val == "":
        return ""
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    if val.lower() in {"null", "none"}:
        return None
    if re.fullmatch(r"-?\d+", val):
        return int(val)
    if (val.startswith("[") and val.endswith("]")) or (
        val.startswith('"') and val.endswith('"')
    ):
        return json.loads(val)
    if val.startswith("'") and val.endswith("'"):
        return val[1:-1]
    return val


def parse_frontmatter(md_text: str, path: Path) -> tuple[dict[str, Any], str]:
    """Разбирает лаконичный YAML-заголовок между маркерами `---`."""
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: отсутствует открывающий блок YAML `---`")
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        raise ValueError(f"{path}: отсутствует закрывающий блок YAML `---`")

    meta: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in lines[1:end_idx]:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list_key is not None:
            meta[current_list_key].append(parse_scalar(stripped[2:]))
            continue
        current_list_key = None
        if ":" not in raw_line:
            raise ValueError(f"{path}: некорректная строка frontmatter: {raw_line!r}")
        key, raw_val = raw_line.split(":", 1)
        key = key.strip()
        raw_val = raw_val.strip()
        if raw_val == "":
            meta[key] = []
            current_list_key = key
        else:
            meta[key] = parse_scalar(raw_val)

    body = "\n".join(lines[end_idx + 1 :]).strip()
    return meta, body


def iter_entity_files() -> list[Path]:
    files: list[Path] = []
    for md_path in sorted(WIKI_DIR.rglob("*.md")):
        rel_parts = md_path.relative_to(WIKI_DIR).parts
        if rel_parts[0] == "_templates":
            continue
        if md_path.name.upper() == "README.MD":
            continue
        files.append(md_path)
    return files


FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def extract_md_links(md_text: str) -> list[tuple[str, str]]:
    """Извлекает пары (текст_ссылки, целевой_путь), игнорируя блоки кода, внешние URL и якоря."""
    cleaned = FENCED_CODE_RE.sub("", md_text)
    cleaned = INLINE_CODE_RE.sub("", cleaned)
    results: list[tuple[str, str]] = []
    for label, target in MD_LINK_RE.findall(cleaned):
        target = target.strip()
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        clean_target = target.split("#", 1)[0]
        if clean_target:
            results.append((label, clean_target))
    return results


def build_index() -> dict[str, Any]:
    entity_files = iter_entity_files()
    entities_by_id: dict[str, dict[str, Any]] = {}
    path_to_id: dict[Path, str] = {}

    # 1. Чтение всех карточек
    for md_path in entity_files:
        text = md_path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text, md_path)
        missing = REQUIRED_FIELDS - set(meta.keys())
        if missing:
            raise ValueError(f"{md_path}: не хватает обязательных полей {sorted(missing)}")

        entity_id = str(meta["id"])
        if entity_id in entities_by_id:
            raise ValueError(
                f"Дублирующийся id={entity_id!r} в {md_path} и {entities_by_id[entity_id]['path']}"
            )

        resolved = md_path.resolve()
        path_to_id[resolved] = entity_id

        # Первый абзац или краткая сводка для быстрого просмотра
        raw_links = extract_md_links(body)
        entities_by_id[entity_id] = {
            "id": entity_id,
            "type": meta["type"],
            "zh": meta["zh"],
            "zh_aliases": list(meta.get("zh_aliases", [])),
            "pinyin": meta.get("pinyin", ""),
            "palladius": meta.get("palladius", ""),
            "ru": meta["ru"],
            "ru_aliases": list(meta.get("ru_aliases", [])),
            "first_chapter": int(meta["first_chapter"]),
            "needs_endnote": meta.get("needs_endnote"),
            "tags": list(meta.get("tags", [])),
            "path": str(md_path.as_posix()),
            "links_to": [],
            "referenced_by": [],
            "_raw_links": [(md_path, target) for _, target in raw_links],
        }

    # 2. Разрешение прямых и обратных перекрёстных ссылок (граф вики)
    for entity_id, item in entities_by_id.items():
        outgoing_ids: list[str] = []
        for src_path, rel_target in item.pop("_raw_links"):
            target_resolved = (src_path.parent / rel_target).resolve()
            target_id = path_to_id.get(target_resolved)
            if target_id and target_id != entity_id and target_id not in outgoing_ids:
                outgoing_ids.append(target_id)
        item["links_to"] = sorted(outgoing_ids)

    for entity_id, item in entities_by_id.items():
        for target_id in item["links_to"]:
            entities_by_id[target_id]["referenced_by"].append(entity_id)

    for item in entities_by_id.values():
        item["referenced_by"].sort()

    # 3. Построение таблицы китайских поисковых ключей (от длинных к коротким)
    zh_lookup: dict[str, list[str]] = {}
    for entity_id, item in entities_by_id.items():
        terms = [item["zh"]] + item["zh_aliases"]
        for term in terms:
            term = term.strip()
            if not term:
                continue
            zh_lookup.setdefault(term, [])
            if entity_id not in zh_lookup[term]:
                zh_lookup[term].append(entity_id)

    sorted_zh_lookup = dict(
        sorted(zh_lookup.items(), key=lambda kv: (-len(kv[0]), kv[0]))
    )

    index_data = {
        "schema_version": "1.0",
        "total_entities": len(entities_by_id),
        "counts_by_type": {
            t: sum(1 for e in entities_by_id.values() if e["type"] == t)
            for t in sorted({e["type"] for e in entities_by_id.values()})
        },
        "entities": dict(sorted(entities_by_id.items())),
        "zh_lookup": sorted_zh_lookup,
    }

    INDEX_FILE.write_text(
        json.dumps(index_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return index_data


def check_all_links() -> list[str]:
    errors: list[str] = []
    md_files: list[Path] = []
    for folder in [WIKI_DIR, DOCS_DIR]:
        if folder.exists():
            md_files.extend(sorted(folder.rglob("*.md")))

    for md_path in md_files:
        if "_templates" in md_path.parts:
            continue
        text = md_path.read_text(encoding="utf-8")
        for label, rel_target in extract_md_links(text):
            target_path = (md_path.parent / rel_target).resolve()
            if not target_path.exists():
                errors.append(
                    f"{md_path}: битая ссылка [{label}]({rel_target}) -> файл не найден"
                )
    return errors


def scan_chapter(chapter_path: Path, max_chapter: int | None) -> None:
    if not INDEX_FILE.exists():
        build_index()

    index_data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    chapter_text = chapter_path.read_text(encoding="utf-8")

    entities = index_data["entities"]
    zh_lookup: dict[str, list[str]] = index_data["zh_lookup"]

    matched_entities: dict[str, dict[str, Any]] = {}
    for zh_term, entity_ids in zh_lookup.items():
        count = chapter_text.count(zh_term)
        if count == 0:
            continue
        for eid in entity_ids:
            ent = entities[eid]
            if max_chapter is not None and ent["first_chapter"] > max_chapter:
                continue
            slot = matched_entities.setdefault(
                eid,
                {
                    "entity": ent,
                    "matched_terms": {},
                    "total_hits": 0,
                },
            )
            slot["matched_terms"][zh_term] = count
            slot["total_hits"] += count

    type_titles = {
        "character": "Персонажи (Имена собственные)",
        "location": "Локации (Топонимы)",
        "cultivation": "Культивация и пути развития",
        "concept": "Концепты и устройство мира",
        "faction": "Фракции и государства",
        "artifact": "Артефакты и предметы",
        "idiom": "Идиомы, обычаи и литературные аллюзии",
    }

    title_line = chapter_text.splitlines()[0] if chapter_text else chapter_path.name
    print(f"=== Досье перевода для {chapter_path} («{title_line}») ===")
    if max_chapter is not None:
        print(f"Анти-спойлерный фильтр: first_chapter <= {max_chapter}")
    print(f"Найдено карточек в Лор-Вики: {len(matched_entities)}\n")

    for t_key, t_label in type_titles.items():
        group = [
            v for v in matched_entities.values() if v["entity"]["type"] == t_key
        ]
        if not group:
            continue
        group.sort(key=lambda item: -item["total_hits"])
        print(f"## {t_label}")
        for item in group:
            ent = item["entity"]
            hits_str = ", ".join(
                f"{k}×{v}" for k, v in item["matched_terms"].items()
            )
            extra = ""
            if ent.get("needs_endnote") is True:
                extra = " [РЕКОМЕНДУЕТСЯ СНОСКА В КОНЦЕ ГЛАВЫ]"
            elif ent.get("needs_endnote") is False:
                extra = " [сноска не требуется, если сохранены обе части образа]"
            links_count = len(ent.get("links_to", []))
            backlinks_count = len(ent.get("referenced_by", []))
            print(
                f"  - {ent['zh']} → {ent['ru']}{extra}\n"
                f"    Вхождения: {hits_str} | Карточка: {ent['path']} "
                f"(исходящих ссылок: {links_count}, обратных ссылок: {backlinks_count})"
            )
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Инструмент управления Лор-Вики 《剑来》")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build", help="Собрать wiki/_index.json и граф обратных ссылок")
    sub.add_parser("check", help="Проверить карточки и перекрёстные ссылки")

    scan_p = sub.add_parser("scan", help="Просканировать главу по базе Лор-Вики")
    scan_p.add_argument("chapter_file", type=Path, help="Путь к файлу главы (например, chapters/0001.txt)")
    scan_p.add_argument(
        "--max-chapter",
        type=int,
        default=None,
        help="Максимальный номер главы для отсечения спойлеров",
    )

    args = parser.parse_args()

    if args.cmd == "build":
        idx = build_index()
        print(
            f"Индекс обновлён: {INDEX_FILE} "
            f"(сущностей: {idx['total_entities']}, поисковых ключей: {len(idx['zh_lookup'])})"
        )
    elif args.cmd == "check":
        idx = build_index()
        errors = check_all_links()
        if errors:
            for err in errors:
                print(f"ОШИБКА: {err}", file=sys.stderr)
            sys.exit(1)
        print(
            f"Проверка пройдена успешно: {idx['total_entities']} карточек валидны, битых перекрёстных ссылок: 0."
        )
    elif args.cmd == "scan":
        scan_chapter(args.chapter_file, args.max_chapter)


if __name__ == "__main__":
    main()
