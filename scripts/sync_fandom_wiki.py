#!/usr/bin/env python3
"""
Standalone MediaWiki API crawler for `https://sword-of-coming.fandom.com`.
Fetches all main-namespace pages (`ns=0`), categories (`ns=14`), category memberships,
transcluded templates (`ns=10`), and media metadata (`allimages`), updates
`wik_fandom/_raw/fandom_dump.json`, rebuilds `wik_fandom/`, and optionally downloads all images.

Usage (requires outbound HTTPS access to `sword-of-coming.fandom.com`):
  python3 scripts/sync_fandom_wiki.py --build
  python3 scripts/sync_fandom_wiki.py --build --download-images --rewrite-local
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path


API_URL = "https://sword-of-coming.fandom.com/api.php"
USER_AGENT = "SwordOfComingWikiMirror/1.0 (https://github.com/Blezz-tech/testz)"


def api_get(params: dict) -> dict:
    q = {**params, "format": "json", "utf8": "1", "formatversion": "2"}
    url = API_URL + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all_ns0_pages() -> list[dict]:
    pages_by_id: dict[int, dict] = {}
    cont: dict = {}
    while True:
        data = api_get({
            "action": "query",
            "generator": "allpages",
            "gapnamespace": "0",
            "gaplimit": "50",
            "prop": "revisions",
            "rvprop": "content|timestamp",
            "rvslots": "main",
            **cont,
        })
        for p in data.get("query", {}).get("pages", []):
            rev = (p.get("revisions") or [{}])[0]
            content = rev.get("slots", {}).get("main", {}).get("content", "")
            pages_by_id[p["pageid"]] = {
                "pageid": p["pageid"],
                "ns": p.get("ns", 0),
                "title": p["title"],
                "timestamp": rev.get("timestamp", ""),
                "content": content,
                "categories": [],
            }
        if "continue" not in data:
            break
        cont = data["continue"]

    # Fetch full category memberships (`prop=categories`) including template-assigned categories
    cont = {}
    while True:
        data = api_get({
            "action": "query",
            "generator": "allpages",
            "gapnamespace": "0",
            "gaplimit": "500",
            "prop": "categories",
            "cllimit": "500",
            **cont,
        })
        for p in data.get("query", {}).get("pages", []):
            pid = p["pageid"]
            if pid in pages_by_id:
                existing = set(pages_by_id[pid]["categories"])
                for c in p.get("categories", []):
                    ct = c["title"]
                    if ct.startswith("Category:"):
                        ct = ct[len("Category:") :]
                    existing.add(ct)
                pages_by_id[pid]["categories"] = sorted(existing)
        if "continue" not in data:
            break
        cont = data["continue"]

    return sorted(pages_by_id.values(), key=lambda x: (x["title"], x["pageid"]))


def fetch_categories() -> list[dict]:
    cats: list[dict] = []
    cont: dict = {}
    while True:
        data = api_get({
            "action": "query",
            "list": "allpages",
            "apnamespace": "14",
            "aplimit": "500",
            **cont,
        })
        cats.extend(data.get("query", {}).get("allpages", []))
        if "continue" not in data:
            break
        cont = data["continue"]
    return sorted(cats, key=lambda c: c["title"])


def fetch_extra_pages() -> list[dict]:
    """Fetch non-main content pages: User (2), Project (4), User blog (500), and curated MediaWiki (8)."""
    extra: list[dict] = []
    for ns_id in ("2", "4", "500"):
        cont: dict = {}
        while True:
            data = api_get({
                "action": "query",
                "generator": "allpages",
                "gapnamespace": ns_id,
                "gaplimit": "50",
                "prop": "revisions|categories",
                "rvprop": "ids|timestamp|content",
                "rvslots": "main",
                "cllimit": "500",
                **cont,
            })
            for p in data.get("query", {}).get("pages", []):
                rev = (p.get("revisions") or [{}])[0]
                cats = [
                    c["title"].split(":", 1)[1] if ":" in c["title"] else c["title"]
                    for c in p.get("categories", [])
                ]
                extra.append({
                    "pageid": p["pageid"],
                    "ns": p.get("ns", int(ns_id)),
                    "title": p["title"],
                    "revid": rev.get("revid"),
                    "timestamp": rev.get("timestamp", ""),
                    "categories": sorted(cats),
                    "content": rev.get("slots", {}).get("main", {}).get("content", ""),
                })
            if "continue" not in data:
                break
            cont = data["continue"]

    mw_data = api_get({
        "action": "query",
        "titles": "MediaWiki:Wiki-navigation|MediaWiki:Wiki-description-site-meta",
        "prop": "revisions",
        "rvprop": "ids|timestamp|content",
        "rvslots": "main",
    })
    for p in mw_data.get("query", {}).get("pages", []):
        if p.get("missing"):
            continue
        rev = (p.get("revisions") or [{}])[0]
        extra.append({
            "pageid": p["pageid"],
            "ns": p.get("ns", 8),
            "title": p["title"],
            "revid": rev.get("revid"),
            "timestamp": rev.get("timestamp", ""),
            "categories": [],
            "content": rev.get("slots", {}).get("main", {}).get("content", ""),
        })

    return sorted(extra, key=lambda x: (x["ns"], x["title"]))


def fetch_transcluded_templates(pages: list[dict]) -> list[dict]:
    skip = {"character", "location", "tabs", "spoilers", "contains spoilers", "main", "-", "align", "sitename"}
    tmpl_re = re.compile(r"\{\{\s*([^{}|#\n]+)")
    wanted: set[str] = {"Template:Navbox/Donghua", "Template:Music"}
    for p in pages:
        for m in tmpl_re.finditer(p["content"]):
            name = m.group(1).strip()
            if name and name.lower() not in skip and not name.lower().startswith("raw:"):
                wanted.add(f"Template:{name[0].upper() + name[1:]}")

    fetched: dict[str, dict] = {}
    queue = sorted(wanted)
    while queue:
        batch = queue[:40]
        queue = queue[40:]
        data = api_get({
            "action": "query",
            "titles": "|".join(batch),
            "prop": "revisions",
            "rvprop": "content|timestamp",
            "rvslots": "main",
        })
        for t in data.get("query", {}).get("pages", []):
            if t.get("missing"):
                continue
            rev = (t.get("revisions") or [{}])[0]
            content = rev.get("slots", {}).get("main", {}).get("content", "")
            fetched[t["title"]] = {
                "pageid": t["pageid"],
                "ns": t.get("ns", 10),
                "title": t["title"],
                "timestamp": rev.get("timestamp", ""),
                "content": content,
            }
            # Discover nested templates
            for m in tmpl_re.finditer(content):
                sub = m.group(1).strip()
                cand = f"Template:{sub[0].upper() + sub[1:]}" if sub else ""
                if (
                    cand
                    and sub.lower() not in skip
                    and not sub.lower().startswith(("raw:", "navbox", "header", "charportal", "ep-list"))
                    and cand not in fetched
                    and cand not in queue
                ):
                    queue.append(cand)
    return sorted(fetched.values(), key=lambda x: x["title"])


def fetch_all_media() -> list[dict]:
    media: list[dict] = []
    cont: dict = {}
    while True:
        data = api_get({
            "action": "query",
            "list": "allimages",
            "ailimit": "500",
            "aiprop": "url|size|dimensions|mime|sha1",
            **cont,
        })
        for img in data.get("query", {}).get("allimages", []):
            media.append({
                "name": img["name"],
                "mime": img.get("mime", "application/octet-stream"),
                "size": int(img.get("size", 0)),
                "width": int(img.get("width", 0)),
                "height": int(img.get("height", 0)),
                "sha1": img.get("sha1", ""),
                "url": img["url"],
            })
        if "continue" not in data:
            break
        cont = data["continue"]
    return sorted(media, key=lambda m: m["name"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync Sword of Coming Fandom Wiki via MediaWiki API")
    ap.add_argument(
        "--dump",
        type=Path,
        default=Path("wik_fandom/_raw/fandom_dump.json"),
        help="Output path for consolidated raw JSON dump",
    )
    ap.add_argument("--build", action="store_true", help="Rebuild Markdown files after syncing")
    ap.add_argument("--download-images", action="store_true", help="Also download all media files locally")
    ap.add_argument("--rewrite-local", action="store_true", help="Rewrite Markdown image links to local paths")
    args = ap.parse_args()

    pages = fetch_all_ns0_pages()
    extra_pages = fetch_extra_pages()
    categories = fetch_categories()
    templates = fetch_transcluded_templates(pages + extra_pages)
    media = fetch_all_media()

    dump = {
        "source_wiki": "https://sword-of-coming.fandom.com",
        "api_endpoint": API_URL,
        "snapshot_date": dt.date.today().isoformat(),
        "counts": {
            "pages_ns0": len(pages),
            "articles_non_redirect": sum(
                1 for p in pages if not p["content"].lstrip().upper().startswith("#REDIRECT")
            ),
            "redirects": sum(1 for p in pages if p["content"].lstrip().upper().startswith("#REDIRECT")),
            "extra_pages_non_ns0": len(extra_pages),
            "categories_ns14": len(categories),
            "templates_ns10_content": len(templates),
            "media_allimages": len(media),
            "media_raster_images": sum(1 for m in media if m["mime"].startswith("image/")),
            "media_youtube_embeds": sum(1 for m in media if m["mime"] == "video/youtube"),
        },
        "pages": pages,
        "extra_pages": extra_pages,
        "categories": categories,
        "templates": templates,
        "media": media,
    }

    args.dump.parent.mkdir(parents=True, exist_ok=True)
    args.dump.write_text(json.dumps(dump, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"synced_to": str(args.dump), "counts": dump["counts"]}, ensure_ascii=False, indent=2))

    scripts_dir = Path(__file__).resolve().parent
    if args.build:
        rc = subprocess.call([sys.executable, str(scripts_dir / "build_fandom_wiki.py"), "--dump", str(args.dump)])
        if rc != 0:
            return rc

    if args.download_images:
        dl_cmd = [sys.executable, str(scripts_dir / "download_fandom_images.py")]
        if args.rewrite_local:
            dl_cmd.append("--rewrite-local")
        return subprocess.call(dl_cmd)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
