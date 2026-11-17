#!/usr/bin/env python3
"""
Утилита управления единой Лор-Вики (wiki/) для перевода новеллы 《剑来》.
Работает на стандартной библиотеке Python 3 — сторонние пакеты не требуются.

Команды:
  python3 scripts/wiki_tool.py build
      Собирает поисковый индекс `wiki/_index.json`: все карточки, граф прямых
      и обратных ссылок, таблицу китайских ключей (от длинных к коротким).

  python3 scripts/wiki_tool.py check
      Проверяет YAML-заголовки, обязательные поля, дубли id и китайских ключей,
      все относительные Markdown-ссылки в `wiki/` и `docs/`, а также полноту
      автогенерируемого `wiki/INDEX.md`.

  python3 scripts/wiki_tool.py index
      Перегенерирует `wiki/INDEX.md` — полный алфавитный указатель всех статей
      по категориям (его не нужно править вручную).

  python3 scripts/wiki_tool.py scan chapters/0001.txt [--max-chapter N]
      Сканирует китайский текст главы и выводит досье перевода: найденные
      персонажи, топонимы, термины, ранги и идиомы с защитой от спойлеров.
      Если `--max-chapter` не указан, номер главы берётся из имени файла.

  python3 scripts/wiki_tool.py lookup 陈平安
  python3 scripts/wiki_tool.py lookup Пинъань
      Быстрый поиск карточки по китайскому, пиньиньскому или русскому имени
      (в том числе по неточному совпадению).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

WIKI_DIR = Path("wiki")
DOCS_DIR = Path("docs")
INDEX_FILE = WIKI_DIR / "_index.json"
HUMAN_INDEX = WIKI_DIR / "INDEX.md"

MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
REQUIRED_FIELDS = {"id", "type", "zh", "ru", "first_chapter"}
SKIP_DIRS = {"_templates"}
SKIP_FILES = {"README.MD", "INDEX.MD"}

TYPE_ORDER = [
    "character",
    "cultivation",
    "faction",
    "location",
    "concept",
    "artifact",
    "idiom",
    "media",
]
TYPE_LABELS = {
    "character": "Персонажи",
    "cultivation": "Культивация и учения",
    "faction": "Фракции, империи и секты",
    "location": "Миры, континенты и локации",
    "concept": "Концепты и устройство мира",
    "artifact": "Артефакты и снаряжение",
    "idiom": "Идиомы, обычаи и аллюзии",
    "media": "Медиа и метаданные",
}
TYPE_TITLES_SCAN = {
    "character": "Персонажи (имена собственные)",
    "location": "Локации (топонимы)",
    "cultivation": "Культивация, ранги и учения",
    "concept": "Концепты и устройство мира",
    "faction": "Фракции, державы и секты",
    "artifact": "Артефакты и снаряжение",
    "idiom": "Идиомы, обычаи и аллюзии",
    "media": "Медиа и метаданные",
}


# --------------------------------------------------------------------------
# Разбор фронтматтера
# --------------------------------------------------------------------------
def parse_scalar(val: str) -> Any:
    val = val.strip()
    if val == "":
        return ""
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    if val.lower() in {"null", "none", "~"}:
        return None
    if re.fullmatch(r"-?\d+", val):
        return int(val)
    if (val.startswith("[") and val.endswith("]")) or (
        val.startswith('"') and val.endswith('"')
    ):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
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
        if rel_parts[0] in SKIP_DIRS:
            continue
        if md_path.name.upper() in SKIP_FILES:
            continue
        files.append(md_path)
    return files


FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
HTML_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")


def extract_md_links(md_text: str) -> list[tuple[str, str]]:
    """Извлекает пары (текст_ссылки, целевой_путь), игнорируя код, URL и якоря."""
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


def read_meta(md_path: Path) -> tuple[dict[str, Any], str]:
    text = md_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text, md_path)
    missing = REQUIRED_FIELDS - set(meta.keys())
    if missing:
        raise ValueError(f"{md_path}: не хватает обязательных полей {sorted(missing)}")
    if meta.get("type") not in TYPE_LABELS:
        raise ValueError(
            f"{md_path}: неизвестный type={meta.get('type')!r}. "
            f"Допустимо: {', '.join(sorted(TYPE_LABELS))}"
        )
    return meta, body


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------
def build_index(verbose: bool = True) -> dict[str, Any]:
    entity_files = iter_entity_files()
    entities_by_id: dict[str, dict[str, Any]] = {}
    path_to_id: dict[Path, str] = {}

    for md_path in entity_files:
        meta, body = read_meta(md_path)
        entity_id = str(meta["id"])
        if entity_id in entities_by_id:
            raise ValueError(
                f"Дублирующийся id={entity_id!r} в {md_path} "
                f"и {entities_by_id[entity_id]['path']}"
            )

        resolved = md_path.resolve()
        path_to_id[resolved] = entity_id

        raw_links = extract_md_links(body)
        entities_by_id[entity_id] = {
            "id": entity_id,
            "type": meta["type"],
            "zh": meta["zh"],
            "zh_aliases": list(meta.get("zh_aliases") or []),
            "pinyin": meta.get("pinyin", ""),
            "palladius": meta.get("palladius", ""),
            "ru": meta["ru"],
            "ru_aliases": list(meta.get("ru_aliases") or []),
            "first_chapter": int(meta["first_chapter"]),
            "gender": meta.get("gender"),
            "subtype": meta.get("subtype"),
            "parent": meta.get("parent"),
            "needs_endnote": meta.get("needs_endnote"),
            "scan_skip": bool(meta.get("scan_skip") or False),
            "scan_hints": list(meta.get("scan_hints") or []),
            "tags": list(meta.get("tags") or []),
            "path": str(md_path.as_posix()),
            "links_to": [],
            "referenced_by": [],
            "_raw_links": [(md_path, target) for _, target in raw_links],
        }

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

    zh_lookup: dict[str, list[str]] = {}
    for entity_id, item in entities_by_id.items():
        terms = [item["zh"]] + item["zh_aliases"]
        for term in terms:
            term = str(term).strip()
            if not term:
                continue
            zh_lookup.setdefault(term, [])
            if entity_id not in zh_lookup[term]:
                zh_lookup[term].append(entity_id)

    sorted_zh_lookup = dict(sorted(zh_lookup.items(), key=lambda kv: (-len(kv[0]), kv[0])))

    index_data = {
        "schema_version": "2.0",
        "total_entities": len(entities_by_id),
        "counts_by_type": {
            t: sum(1 for e in entities_by_id.values() if e["type"] == t)
            for t in sorted({e["type"] for e in entities_by_id.values()})
        },
        "entities": dict(sorted(entities_by_id.items())),
        "zh_lookup": sorted_zh_lookup,
    }

    INDEX_FILE.write_text(
        json.dumps(index_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if verbose:
        counts = ", ".join(f"{k}={v}" for k, v in index_data["counts_by_type"].items())
        print(
            f"Индекс собран: {INDEX_FILE} "
            f"(карточек: {index_data['total_entities']}, ключей: {len(sorted_zh_lookup)}; {counts})"
        )
    return index_data


# --------------------------------------------------------------------------
# index (человекочитаемый INDEX.md)
# --------------------------------------------------------------------------
def render_human_index(index_data: dict[str, Any]) -> str:
    entities = index_data["entities"]
    lines = [
        "# Полный указатель Лор-Вики 《剑来》",
        "",
        "[← Портал Вики](README.md) · [Документация перевода](../docs/TRANSLATION_WORKFLOW.md)",
        "",
        "> Файл генерируется автоматически командой `python3 scripts/wiki_tool.py index`.",
        "> Не редактируйте его вручную: после правки карточек просто перегенерируйте указатель.",
        "",
        f"**Всего статей: {index_data['total_entities']}.**",
        "",
    ]
    for type_key in TYPE_ORDER:
        group = [e for e in entities.values() if e["type"] == type_key]
        if not group:
            continue
        group.sort(key=lambda e: e["ru"].lower().replace("ё", "е"))
        lines.append(f"## {TYPE_LABELS[type_key]} ({len(group)})")
        lines.append("")
        lines.append("| Статья | Китайский | Глава |")
        lines.append("| :--- | :--- | :---: |")
        for ent in group:
            rel = Path(ent["path"]).relative_to(WIKI_DIR).as_posix()
            zh = ent["zh"]
            if ent["zh_aliases"]:
                zh += " / " + " / ".join(ent["zh_aliases"][:3])
            lines.append(f"| [{ent['ru']}]({rel}) | `{zh}` | {ent['first_chapter']} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_human_index(index_data: dict[str, Any]) -> None:
    HUMAN_INDEX.write_text(render_human_index(index_data), encoding="utf-8")
    print(f"Указатель обновлён: {HUMAN_INDEX} (статей: {index_data['total_entities']})")


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------
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
                errors.append(f"{md_path}: битая ссылка [{label}]({rel_target})")
    return errors


def check_duplicate_zh(index_data: dict[str, Any]) -> list[str]:
    """Один китайский ключ не должен вести на две разные сущности.

    Подстраницы персонажей (`parent`) делят имя с родительской карточкой —
    это нормально, поэтому такие совпадения игнорируются.
    """
    warnings: list[str] = []
    entities = index_data["entities"]
    for term, ids in index_data["zh_lookup"].items():
        roots = [i for i in ids if not entities.get(i, {}).get("parent")]
        if len(roots) > 1:
            warnings.append(
                f"Ключ `{term}` принадлежит нескольким карточкам: {', '.join(roots)}"
            )
    return warnings


def check_stubs(index_data: dict[str, Any]) -> list[str]:
    """Карточки, в которых после заголовка почти нет содержательного текста."""
    warnings: list[str] = []
    for ent in index_data["entities"].values():
        text = Path(ent["path"]).read_text(encoding="utf-8")
        body = text.split("---", 2)[-1]
        body = HTML_IMG_RE.sub("", body)
        body = re.sub(r"^#.*$", "", body, flags=re.M)
        body = re.sub(r"^\[.*?\]\(.*?\).*$", "", body, flags=re.M)
        body = re.sub(r"^\|.*$", "", body, flags=re.M)
        if len(body.strip()) < 400:
            warnings.append(f"Короткая карточка ({len(body.strip())} симв.): {ent['path']}")
    return warnings


def check_index_freshness() -> list[str]:
    warnings: list[str] = []
    if not HUMAN_INDEX.exists():
        return ["wiki/INDEX.md отсутствует — выполните `python3 scripts/wiki_tool.py index`"]
    current = HUMAN_INDEX.read_text(encoding="utf-8")
    data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    expected = render_human_index(data)
    if current != expected:
        warnings.append("wiki/INDEX.md устарел — выполните `python3 scripts/wiki_tool.py index`")
    return warnings


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------
def infer_chapter_number(chapter_path: Path) -> int | None:
    stem = chapter_path.stem
    m = re.fullmatch(r"(\d{1,5})", stem)
    if m:
        return int(m.group(1))
    m = re.match(r"(\d{1,5})", stem)
    if m:
        return int(m.group(1))
    return None


def scan_chapter(chapter_path: Path, max_chapter: int | None) -> None:
    if not INDEX_FILE.exists():
        build_index(verbose=False)

    index_data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    chapter_text = chapter_path.read_text(encoding="utf-8")
    entities = index_data["entities"]
    zh_lookup = index_data["zh_lookup"]

    matched: dict[str, dict[str, Any]] = {}
    for zh_term, entity_ids in zh_lookup.items():
        count = chapter_text.count(zh_term)
        if count == 0:
            continue
        for eid in entity_ids:
            ent = entities[eid]
            if ent.get("scan_skip"):
                continue
            if max_chapter is not None and ent["first_chapter"] > max_chapter:
                continue
            slot = matched.setdefault(
                eid, {"entity": ent, "matched_terms": {}, "total_hits": 0}
            )
            slot["matched_terms"][zh_term] = count
            slot["total_hits"] += count

    # Схлопываем подстраницы: если найдена и родительская карточка, и её подстраницы,
    # оставляем только родительскую — иначе досье пухнет от дублей (см. chen-pingan).
    root_ids = {
        eid for eid, slot in matched.items() if not slot["entity"].get("parent")
    }
    matched = {
        eid: slot
        for eid, slot in matched.items()
        if not (
            slot["entity"].get("parent")
            and any(
                other in root_ids
                for other in matched
                if other != eid
                and entities[other]["zh"] == slot["entity"]["zh"]
            )
        )
    }

    title_line = chapter_text.splitlines()[0].strip() if chapter_text else chapter_path.name
    print(f"=== Досье перевода: {chapter_path} («{title_line}») ===")
    if max_chapter is not None:
        print(f"Анти-спойлерный фильтр: карточки с first_chapter <= {max_chapter}")
    print(f"Найдено карточек в Лор-Вики: {len(matched)}\n")

    for t_key in TYPE_ORDER:
        group = [v for v in matched.values() if v["entity"]["type"] == t_key]
        if not group:
            continue
        group.sort(key=lambda item: -item["total_hits"])
        print(f"## {TYPE_TITLES_SCAN.get(t_key, t_key)}")
        for item in group:
            ent = item["entity"]
            hits = ", ".join(
                f"{k}×{v}" for k, v in sorted(
                    item["matched_terms"].items(), key=lambda kv: -len(kv[0])
                )
            )
            note = ""
            if ent.get("needs_endnote") is True:
                note = " [НУЖНА СНОСКА]"
            elif ent.get("needs_endnote") is False:
                note = " [сноска не нужна, если сохранены обе части образа]"
            print(f"  - {ent['zh']} → {ent['ru']}{note}")
            print(
                f"    Вхождения: {hits} | Карточка: {ent['path']} "
                f"(исходящих ссылок: {len(ent['links_to'])}, "
                f"обратных: {len(ent['referenced_by'])})"
            )
            for hint in ent.get("scan_hints") or []:
                print(f"    ⚠ {hint}")
        print()


# --------------------------------------------------------------------------
# lookup
# --------------------------------------------------------------------------
def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def cmd_lookup(query: str) -> None:
    if not INDEX_FILE.exists():
        build_index(verbose=False)
    index_data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    q = normalize(query)
    exact: list[dict[str, Any]] = []
    fuzzy: list[dict[str, Any]] = []
    for ent in index_data["entities"].values():
        names = [ent["ru"], ent["zh"], ent.get("palladius") or "", ent.get("pinyin") or ""]
        names += ent["ru_aliases"] + ent["zh_aliases"]
        norm_names = [normalize(n) for n in names if n]
        if q in norm_names:
            exact.append(ent)
        elif any(q and (q in n or n in q) for n in norm_names):
            fuzzy.append(ent)

    def print_entity(ent: dict[str, Any]) -> None:
        print(f"\n### {ent['ru']} ({ent['zh']})")
        print(f"Тип: {ent['type']} | Первое появление: глава {ent['first_chapter']}")
        if ent.get("palladius"):
            print(f"Палладий: {ent['palladius']} | Пиньинь: {ent.get('pinyin', '')}")
        if ent["ru_aliases"]:
            print(f"Русские варианты: {', '.join(ent['ru_aliases'])}")
        if ent["zh_aliases"]:
            print(f"Китайские варианты: {', '.join(ent['zh_aliases'])}")
        print(f"Карточка: {ent['path']}")
        print(f"Ссылок исходящих: {len(ent['links_to'])}, обратных: {len(ent['referenced_by'])}")

    if not exact and not fuzzy:
        print(f"Ничего не найдено по запросу {query!r}.")
        sys.exit(1)
    for ent in exact:
        print_entity(ent)
    if fuzzy:
        print("\nПохожие карточки:")
        for ent in fuzzy[:10]:
            print(f"  - {ent['ru']} ({ent['zh']}) — {ent['path']}")


# --------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Инструмент управления Лор-Вики 《剑来》")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build", help="Собрать wiki/_index.json и граф обратных ссылок")
    sub.add_parser("index", help="Перегенерировать человекочитаемый wiki/INDEX.md")
    sub.add_parser("check", help="Проверить карточки, ссылки и свежесть INDEX.md")

    scan_p = sub.add_parser("scan", help="Просканировать главу по базе Лор-Вики")
    scan_p.add_argument("chapter_file", type=Path, help="Путь к файлу главы (chapters/NNNN.txt)")
    scan_p.add_argument(
        "--max-chapter",
        type=int,
        default=None,
        help="Максимальный номер главы для отсечения спойлеров (по умолчанию — из имени файла)",
    )
    scan_p.add_argument(
        "--no-auto-chapter",
        action="store_true",
        help="Не выводить номер главы из имени файла (отключить анти-спойлерный фильтр)",
    )

    look_p = sub.add_parser("lookup", help="Найти карточку по имени")
    look_p.add_argument("query", help="Китайское, пиньиньское или русское имя")

    args = parser.parse_args()

    if args.cmd == "build":
        build_index()
    elif args.cmd == "index":
        write_human_index(build_index(verbose=False))
    elif args.cmd == "check":
        idx = build_index(verbose=False)
        write_human_index(idx)
        errors = check_all_links()
        warnings = check_duplicate_zh(idx) + check_stubs(idx) + check_index_freshness()
        for warn in warnings:
            print(f"ПРЕДУПРЕЖДЕНИЕ: {warn}", file=sys.stderr)
        if errors:
            for err in errors:
                print(f"ОШИБКА: {err}", file=sys.stderr)
            sys.exit(1)
        print(
            f"Проверка пройдена: {idx['total_entities']} карточек валидны, "
            f"битых относительных ссылок: 0, предупреждений: {len(warnings)}."
        )
    elif args.cmd == "scan":
        max_ch = args.max_chapter
        if max_ch is None and not args.no_auto_chapter:
            max_ch = infer_chapter_number(args.chapter_file)
        scan_chapter(args.chapter_file, max_ch)
    elif args.cmd == "lookup":
        cmd_lookup(args.query)


if __name__ == "__main__":
    main()
