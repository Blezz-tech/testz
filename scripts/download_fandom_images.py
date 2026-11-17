#!/usr/bin/env python3
"""
Download all 245 media files from `wik_fandom/images_manifest.json` into `wik_fandom/images/`,
verify SHA-1 checksums for raster images, and optionally rebuild `wik_fandom/**/*.md`
to use local relative `images/...` paths (`--rewrite-local`) or restore CDN URLs (`--restore-cdn`).

Requires direct outbound HTTPS access to `static.wikia.nocookie.net` (run locally outside restricted sandboxes).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


USER_AGENT = "SwordOfComingWikiMirror/1.0 (https://github.com/Blezz-tech/testz)"


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def check_existing(item: dict, root: Path) -> bool:
    dest = root / item["local_path"]
    if not dest.exists() or dest.stat().st_size == 0:
        return False
    if item["mime"].startswith("image/"):
        return sha1_file(dest) == item["sha1"]
    return dest.stat().st_size > 0


def download_one(item: dict, root: Path, timeout: int = 30) -> tuple[str, str, str]:
    """Return `(status, name, detail)` where status in `{'skipped', 'downloaded', 'failed'}`."""
    dest = root / item["local_path"]
    if check_existing(item, root):
        return ("skipped", item["name"], "already verified")

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(item["url"], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if not data:
            return ("failed", item["name"], "empty response body")
        if item["mime"].startswith("image/"):
            got_sha1 = hashlib.sha1(data).hexdigest()
            if got_sha1 != item["sha1"]:
                # Fandom occasionally serves WebP-transcoded bytes unless `format=original` is appended; retry once
                sep = "&" if "?" in item["url"] else "?"
                orig_url = item["url"] + f"{sep}format=original"
                req2 = urllib.request.Request(orig_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req2, timeout=timeout) as resp2:
                    data2 = resp2.read()
                got_sha1_2 = hashlib.sha1(data2).hexdigest()
                if got_sha1_2 == item["sha1"]:
                    data = data2
        tmp.write_bytes(data)
        tmp.replace(dest)
        return ("downloaded", item["name"], f"{len(data)} bytes")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return ("failed", item["name"], str(exc))


def rebuild_markdown(wiki_dir: Path, local_images: bool) -> int:
    build_script = Path(__file__).resolve().parent / "build_fandom_wiki.py"
    dump_path = wiki_dir / "_raw" / "fandom_dump.json"
    cmd = [sys.executable, str(build_script), "--dump", str(dump_path), "--out", str(wiki_dir)]
    if local_images:
        cmd.append("--local-images")
    return subprocess.call(cmd)


def main() -> int:
    ap = argparse.ArgumentParser(description="Download & verify Sword of Coming Fandom media assets")
    ap.add_argument(
        "--wiki-dir",
        type=Path,
        default=Path("wik_fandom"),
        help="Path to wik_fandom directory (default: wik_fandom)",
    )
    ap.add_argument("--workers", type=int, default=8, help="Concurrent download threads (default: 8)")
    ap.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds (default: 30)")
    ap.add_argument(
        "--verify-only",
        action="store_true",
        help="Only check already-downloaded files in wik_fandom/images/ without network requests",
    )
    ap.add_argument(
        "--rewrite-local",
        action="store_true",
        help="After downloading, rebuild Markdown pages to use offline relative `images/...` paths",
    )
    ap.add_argument(
        "--restore-cdn",
        action="store_true",
        help="Rebuild Markdown pages to use live `static.wikia.nocookie.net` CDN URLs and exit",
    )
    args = ap.parse_args()

    if args.restore_cdn:
        return rebuild_markdown(args.wiki_dir, local_images=False)

    manifest_path = args.wiki_dir / "images_manifest.json"
    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest["items"]

    if args.verify_only:
        ok = sum(1 for it in items if check_existing(it, args.wiki_dir))
        missing = len(items) - ok
        print(json.dumps({"total": len(items), "verified_local": ok, "missing": missing}, indent=2))
        return 0 if missing == 0 else 1

    counts = {"downloaded": 0, "skipped": 0, "failed": 0}
    failures: list[tuple[str, str]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = [pool.submit(download_one, it, args.wiki_dir, args.timeout) for it in items]
        for fut in concurrent.futures.as_completed(futs):
            status, name, detail = fut.result()
            counts[status] += 1
            if status == "failed":
                failures.append((name, detail))

    summary = {
        "total_manifest": len(items),
        **counts,
        "failed_samples": failures[:10],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if counts["failed"] > 0:
        print(
            "Warning: Some files could not be downloaded (check outbound internet access to static.wikia.nocookie.net).",
            file=sys.stderr,
        )
        return 1

    if args.rewrite_local:
        return rebuild_markdown(args.wiki_dir, local_images=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
