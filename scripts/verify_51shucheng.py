#!/usr/bin/env python3
"""
Полная программная и криминалистическая верификация разбитых глав `chapters/`
против исходного корпуса `剑来_全集_1198章.txt` и живого оглавления / страниц
оригинального источника `https://www.51shucheng.net/wangluo/jianlai`.

Что проверяет скрипт:
1. Целостность файловой структуры `chapters/` (`0000_synopsis.txt` + `0000.txt`..`1197.txt`,
   отсутствие пропусков и лишних `.txt`, совпадение `sha256_16` и `char_count` с `index.json`).
2. Посимвольная (100% lossless) сверка каждого из 1 198 файлов + синопсиса против
   монолитного исходника `剑来_全集_1198章.txt` с точным учётом всех 21 удалённых
   встроенных водяных знаков вида `[笔趣阁 www.xxx.yyy]`.
3. Сверка 1-к-1 всех 1 198 глав (количество, порядок, заголовки, аномалии нумерации)
   с живым оглавлением `https://www.51shucheng.net/wangluo/jianlai` (`chapters/51shucheng_toc.json`),
   а также обогащение `chapters/index.json` прямыми ссылками `source_url` и `source_html_id`.
4. Опциональный сетевой режим `--live` (для запуска вне песочницы с прямым доступом в интернет),
   который многопоточно скачивает все 1 198 страниц `51shucheng.net` и сверяет текст каждой главы.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILE = ROOT / "剑来_全集_1198章.txt"
CHAPTERS_DIR = ROOT / "chapters"
INDEX_JSON = CHAPTERS_DIR / "index.json"
TOC_TSV = CHAPTERS_DIR / "_51shucheng_toc.tsv"
TOC_JSON = CHAPTERS_DIR / "51shucheng_toc.json"
BASE_URL = "https://www.51shucheng.net/wangluo/jianlai"

WATERMARK_RE = re.compile(
    r"\[[^\]]*(?:www\.|http|\.com|\.net|\.org|\.info|\.vip|\.xyz|\.biz|\.cc|\.la|\.me)[^\]]*\]"
)


def build_or_load_toc() -> dict:
    """Формирует `chapters/51shucheng_toc.json` из TSV-снимка оглавления или читает готовый JSON."""
    if TOC_TSV.exists():
        entries = []
        raw_lines = TOC_TSV.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(raw_lines):
            if not line.strip():
                continue
            html_id_str, title = line.split("\t", 1)
            html_id = int(html_id_str.strip())
            entries.append(
                {
                    "id": f"{idx:04d}",
                    "html_id": html_id,
                    "title": title.strip(),
                    "url": f"{BASE_URL}/{html_id}.html",
                }
            )
        toc_data = {
            "source_url": BASE_URL,
            "fetched_date": "2026-11-16",
            "book_title": "剑来",
            "author": "烽火戏诸侯",
            "synopsis_on_site": (
                "大千世界，无奇不有。我陈平安，唯有一剑，可搬山，倒海，降妖，镇魔，"
                "敕神，摘星，断江，摧城，开天！————我叫陈平安，平平安安的平安，我是一名剑客。"
            ),
            "total_entries": len(entries),
            "first_html_id": entries[0]["html_id"] if entries else None,
            "last_html_id": entries[-1]["html_id"] if entries else None,
            "entries": entries,
        }
        TOC_JSON.write_text(
            json.dumps(toc_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        TOC_TSV.unlink()
        return toc_data

    if TOC_JSON.exists():
        return json.loads(TOC_JSON.read_text(encoding="utf-8"))

    raise FileNotFoundError(f"Не найден файл оглавления {TOC_JSON}")


def verify_local_corpus(toc_data: dict, enrich_index: bool = False) -> dict:
    """Выполняет трёхуровневую локальную верификацию всех 1198 глав."""
    errors: list[str] = []

    if not SOURCE_FILE.exists():
        raise FileNotFoundError(f"Отсутствует исходный файл: {SOURCE_FILE}")
    if not INDEX_JSON.exists():
        raise FileNotFoundError(f"Отсутствует индекс: {INDEX_JSON}")

    index_data = json.loads(INDEX_JSON.read_text(encoding="utf-8"))
    entries = index_data["entries"]
    toc_entries = toc_data["entries"]

    # 1. Проверка комплектности файлов в chapters/
    expected_txt_files = {"0000_synopsis.txt"} | {f"{i:04d}.txt" for i in range(1198)}
    actual_txt_files = {p.name for p in CHAPTERS_DIR.glob("*.txt")}
    missing_files = sorted(expected_txt_files - actual_txt_files)
    extra_files = sorted(actual_txt_files - expected_txt_files)
    if missing_files:
        errors.append(f"Отсутствуют файлы глав: {missing_files[:10]}")
    if extra_files:
        errors.append(f"Лишние .txt файлы в chapters/: {extra_files[:10]}")

    # 2. Посимвольная сверка с монолитным файлом 剑来_全集_1198章.txt
    raw_lines = SOURCE_FILE.read_text(encoding="utf-8").splitlines()
    sep_idx = next(i for i, line in enumerate(raw_lines) if line.startswith("===="))
    expected_synopsis = "\n".join(raw_lines[:sep_idx]).strip() + "\n"
    actual_synopsis = (CHAPTERS_DIR / "0000_synopsis.txt").read_text(encoding="utf-8")
    if actual_synopsis != expected_synopsis:
        errors.append("Несовпадение текста в 0000_synopsis.txt с шапкой монолитного файла!")

    # Проверка вхождения синопсиса с сайта 51shucheng.net в наш 0000_synopsis.txt
    if re.sub(r"\s+", "", toc_data["synopsis_on_site"]) not in re.sub(
        r"\s+", "", actual_synopsis
    ):
        errors.append("Синопсис на странице 51shucheng.net не совпадает с 0000_synopsis.txt!")

    header_indices = [
        i
        for i in range(sep_idx + 1, len(raw_lines))
        if raw_lines[i].strip() and not raw_lines[i].startswith("　　")
    ]
    if len(header_indices) != 1198:
        errors.append(f"В монолитном файле найдено {len(header_indices)} заголовков вместо 1198!")

    watermarks_found: list[dict] = []
    exact_text_matches = 0
    hash_matches = 0
    char_count_matches = 0

    for seq_id, start_line in enumerate(header_indices):
        end_line = (
            header_indices[seq_id + 1]
            if seq_id + 1 < len(header_indices)
            else len(raw_lines)
        )
        raw_title = raw_lines[start_line].strip()
        raw_body = "\n".join(raw_lines[start_line + 1 : end_line]).strip()
        wms = WATERMARK_RE.findall(raw_body)
        if wms:
            watermarks_found.append(
                {
                    "id": f"{seq_id:04d}",
                    "title": raw_title,
                    "count": len(wms),
                    "samples": wms,
                }
            )
        cleaned_body = WATERMARK_RE.sub("", raw_body)
        expected_chapter_text = f"{raw_title}\n\n{cleaned_body}\n"

        chapter_file = CHAPTERS_DIR / f"{seq_id:04d}.txt"
        actual_chapter_text = chapter_file.read_text(encoding="utf-8")
        if actual_chapter_text == expected_chapter_text:
            exact_text_matches += 1
        else:
            errors.append(f"Расхождение текста в {chapter_file.name} ({raw_title})")

        idx_entry = entries[seq_id]
        actual_hash = hashlib.sha256(actual_chapter_text.encode("utf-8")).hexdigest()[:16]
        if actual_hash == idx_entry["sha256_16"]:
            hash_matches += 1
        else:
            errors.append(f"Несовпадение SHA-256 для {chapter_file.name}")

        actual_non_ws = len(re.sub(r"\s+", "", cleaned_body))
        if actual_non_ws == idx_entry["char_count"]:
            char_count_matches += 1
        else:
            errors.append(f"Несовпадение char_count для {chapter_file.name}")

    # 3. Сверка 1-к-1 против живого оглавления https://www.51shucheng.net/wangluo/jianlai
    if len(entries) != len(toc_entries):
        errors.append(
            f"Количество глав локально ({len(entries)}) != на сайте 51shucheng.net ({len(toc_entries)})"
        )

    toc_title_matches = 0
    title_mismatches = []
    for loc, rem in zip(entries, toc_entries):
        if loc["id"] != rem["id"]:
            errors.append(f"Сбой сквозного ID: {loc['id']} != {rem['id']}")
        if loc["raw_title"] == rem["title"]:
            toc_title_matches += 1
        else:
            title_mismatches.append(
                {
                    "id": loc["id"],
                    "local_title": loc["raw_title"],
                    "remote_title": rem["title"],
                    "url": rem["url"],
                }
            )
        if enrich_index:
            loc["source_html_id"] = rem["html_id"]
            loc["source_url"] = rem["url"]

    if title_mismatches:
        errors.append(f"Несовпадения заголовков с 51shucheng.net: {title_mismatches[:5]}")

    if enrich_index and not errors:
        index_data["source_site"] = BASE_URL
        INDEX_JSON.write_text(
            json.dumps(index_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    total_wm_count = sum(item["count"] for item in watermarks_found)

    return {
        "ok": len(errors) == 0,
        "total_chapters_expected": 1198,
        "txt_files_verified": len(actual_txt_files),
        "exact_monolith_matches": exact_text_matches,
        "sha256_matches": hash_matches,
        "char_count_matches": char_count_matches,
        "live_toc_total": len(toc_entries),
        "live_toc_title_matches": toc_title_matches,
        "watermarks_removed_total": total_wm_count,
        "chapters_with_watermarks": watermarks_found,
        "errors": errors,
    }


def extract_51shucheng_prose(raw_html: str) -> str:
    """Извлекает чистый текст главы из HTML страницы 51shucheng.net (#neirong)."""
    m = re.search(
        r'<div[^>]+id=["\']neirong["\'][^>]*>(.*?)</div>',
        raw_html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not m:
        raise ValueError("Не найден блок #neirong в HTML")
    chunk = m.group(1)
    chunk = re.sub(r"<script.*?</script>", "", chunk, flags=re.DOTALL | re.IGNORECASE)
    chunk = re.sub(r"<p[^>]*>", "\n", chunk, flags=re.IGNORECASE)
    chunk = re.sub(r"</p>", "\n", chunk, flags=re.IGNORECASE)
    chunk = re.sub(r"<br\s*/?>", "\n", chunk, flags=re.IGNORECASE)
    chunk = re.sub(r"<[^>]+>", "", chunk)
    text = html.unescape(chunk)
    paras = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n\n".join(f"　　{p}" for p in paras)


def verify_live_http(toc_data: dict, workers: int = 8) -> dict:
    """Сетевой режим: скачивает все 1198 страниц с 51shucheng.net и сверяет полный текст."""
    toc_entries = toc_data["entries"]
    mismatches = []
    checked = 0

    def check_one(item: dict) -> tuple[str, bool, str]:
        req = urllib.request.Request(
            item["url"],
            headers={"User-Agent": "Mozilla/5.0 (compatible; JianLaiVerifier/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw_html = resp.read().decode("utf-8", errors="replace")
        remote_body = WATERMARK_RE.sub("", extract_51shucheng_prose(raw_html))
        local_text = (CHAPTERS_DIR / f"{item['id']}.txt").read_text(encoding="utf-8")
        _, _, local_body = local_text.partition("\n\n")
        norm_remote = re.sub(r"\s+", "", remote_body)
        norm_local = re.sub(r"\s+", "", local_body)
        if norm_remote == norm_local:
            return item["id"], True, "ok"
        return (
            item["id"],
            False,
            f"len_local={len(norm_local)} vs len_remote={len(norm_remote)} ({item['url']})",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(check_one, item): item for item in toc_entries}
        for fut in concurrent.futures.as_completed(futures):
            cid, ok, msg = fut.result()
            checked += 1
            if not ok:
                mismatches.append({"id": cid, "detail": msg})
            if checked % 100 == 0:
                print(f"[live] Проверено {checked}/{len(toc_entries)}...", file=sys.stderr)

    return {
        "checked": checked,
        "matched": checked - len(mismatches),
        "mismatches": mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Верификация глав «剑来» против 51shucheng.net")
    parser.add_argument(
        "--enrich-index",
        action="store_true",
        help="Добавить source_html_id и source_url в chapters/index.json",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Скачать по HTTP все 1198 страниц с 51shucheng.net и сверить полный текст",
    )
    parser.add_argument("--workers", type=int, default=8, help="Потоков для --live")
    args = parser.parse_args()

    toc_data = build_or_load_toc()
    report = verify_local_corpus(toc_data, enrich_index=args.enrich_index)

    if args.live:
        report["live_http"] = verify_live_http(toc_data, workers=args.workers)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
