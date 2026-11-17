#!/usr/bin/env python3
"""
Build the local Markdown Sword of Coming Fandom wiki (`wik_fandom/`)
from the consolidated MediaWiki raw snapshot (`wik_fandom/_raw/fandom_dump.json`).

Outputs:
  - wik_fandom/pages/**/*.md         (all 183 ns=0 pages: 163 articles + 20 redirects)
  - wik_fandom/categories/*.md       (all category index pages)
  - wik_fandom/GALLERY.md            (visual catalog of all 245 Fandom media items)
  - wik_fandom/INDEX.md              (master A-Z & thematic index of the entire wiki)
  - wik_fandom/images_manifest.json  (245 media items with URLs, SHA-1, sizes, local paths, usage)

Also runs an automated link & completeness verifier at the end of the build.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shutil
from pathlib import Path
from urllib.parse import quote


INFOBOX_NAMES = {"character", "donghua", "novel", "author"}
SPOILER_TEMPLATES = {"spoilers", "contains spoilers"}
IMAGE_EXT_RE = re.compile(r"\.(?:png|jpg|jpeg|webp|gif)$", re.I)

MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/youtube": ".jpg",
}


def sanitize_segment(seg: str) -> str:
    seg = seg.strip().replace(" ", "_")
    seg = re.sub(r'[<>:"\\|?*]', "_", seg)
    return seg


def title_to_rel_md(title: str) -> str:
    """Return path relative to `wik_fandom/`, e.g. `pages/Chen_Ping_An/Cultivation.md`."""
    parts = [sanitize_segment(p) for p in title.split("/")]
    return "pages/" + "/".join(parts) + ".md"


def cat_to_rel_md(cat_name: str) -> str:
    if cat_name.lower().startswith("category:"):
        cat_name = cat_name.split(":", 1)[1]
    return f"categories/{sanitize_segment(cat_name)}.md"


def normalize_key(s: str) -> str:
    s = s.strip().replace("_", " ").replace("’", "'")
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def github_slug(heading: str) -> str:
    h = re.sub(r"[*_`~\[\]()]", "", heading.strip().lower())
    h = re.sub(r"[^\w\s-]", "", h)
    h = re.sub(r"\s+", "-", h).strip("-")
    return h


def local_media_filename(name: str, mime: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*]', "_", name.strip())
    ext = MIME_TO_EXT.get(mime, ".bin")
    if not safe.lower().endswith(ext) and not IMAGE_EXT_RE.search(safe):
        safe = safe + ext
    return safe


def rel_link(from_rel_doc: str, to_rel_doc: str, anchor: str = "") -> str:
    from_dir = posixpath.dirname(from_rel_doc)
    rel = posixpath.relpath(to_rel_doc, from_dir)
    parts = [quote(p, safe="_-.'()~") for p in rel.split("/")]
    url = "/".join(parts)
    if anchor:
        slug = github_slug(anchor)
        if slug:
            url += f"#{slug}"
    return url


def extract_balanced_templates(text: str) -> list[tuple[int, int, str]]:
    """Return top-level `{{ ... }}` spans as (start, end, inner_content)."""
    spans: list[tuple[int, int, str]] = []
    depth = 0
    start = -1
    i = 0
    n = len(text)
    while i < n - 1:
        two = text[i : i + 2]
        if two == "{{":
            if depth == 0:
                start = i
            depth += 1
            i += 2
            continue
        if two == "}}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append((start, i + 2, text[start + 2 : i]))
                start = -1
            i += 2
            continue
        i += 1
    return spans


def split_template_parts(inner: str) -> tuple[str, list[str], dict[str, str]]:
    """Split template inner string `Name|pos1|k=v|...` respecting nested `{{...}}` and `[[...]]`."""
    parts: list[str] = []
    buf: list[str] = []
    depth_t = 0
    depth_l = 0
    i = 0
    n = len(inner)
    while i < n:
        if i + 1 < n and inner[i : i + 2] == "{{":
            depth_t += 1
            buf.append("{{")
            i += 2
            continue
        if i + 1 < n and inner[i : i + 2] == "}}" and depth_t > 0:
            depth_t -= 1
            buf.append("}}")
            i += 2
            continue
        if i + 1 < n and inner[i : i + 2] == "[[":
            depth_l += 1
            buf.append("[[")
            i += 2
            continue
        if i + 1 < n and inner[i : i + 2] == "]]" and depth_l > 0:
            depth_l -= 1
            buf.append("]]")
            i += 2
            continue
        if i + 2 < n and inner[i : i + 3] == "|-|" and depth_t == 0 and depth_l == 0:
            buf.append("|-|")
            i += 3
            continue
        if inner[i] == "|" and depth_t == 0 and depth_l == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(inner[i])
        i += 1
    parts.append("".join(buf))

    name = parts[0].strip()
    positional: list[str] = []
    named: dict[str, str] = {}
    for raw_p in parts[1:]:
        eq_idx = -1
        dt = dl = 0
        j = 0
        while j < len(raw_p):
            if j + 1 < len(raw_p) and raw_p[j : j + 2] == "{{":
                dt += 1
                j += 2
                continue
            if j + 1 < len(raw_p) and raw_p[j : j + 2] == "}}" and dt > 0:
                dt -= 1
                j += 2
                continue
            if j + 1 < len(raw_p) and raw_p[j : j + 2] == "[[":
                dl += 1
                j += 2
                continue
            if j + 1 < len(raw_p) and raw_p[j : j + 2] == "]]" and dl > 0:
                dl -= 1
                j += 2
                continue
            if raw_p[j] == "=" and dt == 0 and dl == 0:
                eq_idx = j
                break
            j += 1
        if eq_idx != -1:
            k = raw_p[:eq_idx].strip()
            v = raw_p[eq_idx + 1 :].strip()
            named[k] = v
        else:
            positional.append(raw_p.strip())
    return name, positional, named


class WikiBuilder:
    def __init__(self, dump_path: Path, out_dir: Path, use_local_images: bool = False) -> None:
        self.dump_path = dump_path
        self.out_dir = out_dir
        self.use_local_images = use_local_images

        with open(dump_path, encoding="utf-8") as f:
            self.dump = json.load(f)

        self.pages: list[dict] = self.dump["pages"]
        self.categories_raw: list[dict] = self.dump["categories"]
        self.templates_raw: list[dict] = self.dump["templates"]
        self.media_raw: list[dict] = self.dump["media"]

        # Map normalized page title -> page dict
        self.page_by_norm: dict[str, dict] = {}
        self.redirect_map: dict[str, tuple[str, str]] = {}  # norm_title -> (target_title, anchor)
        for p in self.pages:
            nk = normalize_key(p["title"])
            self.page_by_norm[nk] = p

        for p in self.pages:
            c = p["content"].lstrip()
            if c.upper().startswith("#REDIRECT"):
                m = re.search(r"\[\[([^\]|]+)", c)
                if m:
                    raw_target = m.group(1).strip()
                    if "#" in raw_target:
                        t_part, a_part = raw_target.split("#", 1)
                    else:
                        t_part, a_part = raw_target, ""
                    tp = self.page_by_norm.get(normalize_key(t_part))
                    canon_t = tp["title"] if tp else t_part.strip()
                    self.redirect_map[normalize_key(p["title"])] = (canon_t, a_part.strip())

        # Map normalized template name -> content
        self.templates: dict[str, str] = {}
        for t in self.templates_raw:
            tname = t["title"]
            if tname.lower().startswith("template:"):
                tname = tname.split(":", 1)[1]
            self.templates[normalize_key(tname)] = t["content"]

        # Map media name -> media record + track usage
        self.media_by_exact: dict[str, dict] = {}
        self.media_by_norm: dict[str, dict] = {}
        self.media_usage: dict[str, set[str]] = {}
        for m in self.media_raw:
            rec = dict(m)
            local_fn = local_media_filename(m["name"], m["mime"])
            rec["local_path"] = f"images/{local_fn}"
            self.media_by_exact[m["name"]] = rec
            self.media_by_norm[normalize_key(m["name"])] = rec
            self.media_usage[m["name"]] = set()

        # Category memberships: category_name -> set of page titles
        self.category_members: dict[str, set[str]] = {}
        for c in self.categories_raw:
            cname = c["title"]
            if cname.lower().startswith("category:"):
                cname = cname.split(":", 1)[1]
            self.category_members.setdefault(cname, set())

    def lookup_media(self, raw_name: str, used_by_title: str | None = None) -> dict | None:
        s = raw_name.strip()
        if s.lower().startswith(("file:", "image:")):
            s = s.split(":", 1)[1].strip()
        s_under = s.replace(" ", "_")
        rec = self.media_by_exact.get(s_under) or self.media_by_norm.get(normalize_key(s))
        if rec and used_by_title:
            self.media_usage[rec["name"]].add(used_by_title)
        return rec

    def media_src(self, rec: dict, current_rel_doc: str) -> str:
        if self.use_local_images:
            return rel_link(current_rel_doc, rec["local_path"])
        return rec["url"]

    def resolve_page_link(
        self, raw_target: str, label: str | None, current_rel_doc: str, follow_redirects: bool = True
    ) -> str:
        target = raw_target.strip()
        if not target:
            return label or ""

        # Category links `[[:Category:Characters|Click here]]`
        if target.lstrip(":").lower().startswith("category:"):
            cat_name = target.lstrip(":").split(":", 1)[1].strip()
            if cat_name == "{{SITENAME}}":
                cat_name = "Sword of Coming Wiki"
            self.category_members.setdefault(cat_name, set())
            cat_rel = cat_to_rel_md(cat_name)
            disp = (label or cat_name).strip()
            return f"[{disp}]({rel_link(current_rel_doc, cat_rel)})"

        # Special / Fandom help links
        if target.lower().startswith("special:"):
            disp = (label or target).strip()
            url = "https://sword-of-coming.fandom.com/wiki/" + quote(target.replace(" ", "_"), safe=":/_-")
            return f"[{disp}]({url})"
        if target.lower().startswith("w:"):
            wtarget = target[2:].strip()
            disp = (label or wtarget).strip()
            url = "https://community.fandom.com/wiki/" + quote(wtarget.replace(" ", "_"), safe=":/_-")
            return f"[{disp}]({url})"

        if "#" in target:
            page_part, anchor_part = target.split("#", 1)
        else:
            page_part, anchor_part = target, ""

        page_part = page_part.strip()
        anchor_part = anchor_part.strip()
        disp = (label if label is not None and label.strip() else (page_part or anchor_part)).strip()

        if not page_part and anchor_part:
            return f"[{disp}](#{github_slug(anchor_part)})"

        nk = normalize_key(page_part)
        if follow_redirects and nk in self.redirect_map:
            redir_title, redir_anchor = self.redirect_map[nk]
            dest_page = self.page_by_norm.get(normalize_key(redir_title))
            if dest_page:
                to_rel = title_to_rel_md(dest_page["title"])
                eff_anchor = anchor_part or redir_anchor
                return f"[{disp}]({rel_link(current_rel_doc, to_rel, eff_anchor)})"

        dest_page = self.page_by_norm.get(nk)
        if dest_page:
            to_rel = title_to_rel_md(dest_page["title"])
            return f"[{disp}]({rel_link(current_rel_doc, to_rel, anchor_part)})"

        # Uncreated wiki redlink -> render clean readable text without broken href
        return disp

    def clean_infobox_value(self, val: str, current_rel_doc: str, page_title: str) -> str:
        val = val.strip()
        if not val:
            return ""
        # Separate adjacent wiki links that lost `<br>` (`[[A]][[B]]` -> `[[A]]<br>[[B]]`)
        val = re.sub(r"\]\]\s*\[\[", "]]<br>[[", val)
        val = re.sub(r"\)\s*\[\[", ")<br>[[", val)
        # Separate camelCase collisions from stripped `<br>` in plain text, e.g. `GatekeeperShopkeeper`
        # while avoiding `A'Liang`, `Mc...`, URLs, or `[http...]`
        def split_glued(match: re.Match) -> str:
            return f"{match.group(1)}<br>{match.group(2)}"

        val = re.sub(r"([a-z\)\]])([A-Z][a-z])", split_glued, val)
        lines = [ln.strip() for ln in val.splitlines() if ln.strip()]
        rendered = [self.convert_inline(ln, current_rel_doc, page_title) for ln in lines]
        rendered = [r for r in rendered if r]
        joined = "<br>".join(rendered)
        return joined.replace("|", "\\|")

    def render_infobox(
        self, tname: str, named: dict[str, str], current_rel_doc: str, page_title: str
    ) -> str:
        portraits: list[tuple[str, str]] = []  # (alt, src)
        rows: list[tuple[str, str]] = []

        skip_keys = {"header1", "imagecaption"}
        for k, v in named.items():
            kl = k.strip().lower()
            v_str = v.strip()
            if not v_str or kl in skip_keys:
                continue
            if kl == "image" or kl.startswith("image"):
                # Can be single filename or multi-line gallery `Name.png|Caption`
                for line in v_str.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("[["):
                        m = re.match(r"\[\[(?:File|Image):([^|\]]+)", line, re.I)
                        fname = m.group(1).strip() if m else line
                        cap = page_title
                    elif "|" in line:
                        fname, cap = line.split("|", 1)
                        fname, cap = fname.strip(), cap.strip()
                    else:
                        fname, cap = line, page_title
                    rec = self.lookup_media(fname, page_title)
                    if rec:
                        alt = f"{page_title} ({cap})" if cap and cap != page_title else page_title
                        portraits.append((alt, self.media_src(rec, current_rel_doc)))
                continue

            pretty_k = k.strip().replace("_", " ")
            pretty_k = pretty_k[0].upper() + pretty_k[1:] if pretty_k else k
            cleaned_v = self.clean_infobox_value(v_str, current_rel_doc, page_title)
            if cleaned_v:
                rows.append((pretty_k, cleaned_v))

        out_parts: list[str] = []
        if portraits:
            alt0, src0 = portraits[0]
            out_parts.append(f"![{alt0}]({src0})")
        if rows:
            table_lines = [
                "| Attribute | Details |",
                "| :--- | :--- |",
            ]
            for rk, rv in rows:
                table_lines.append(f"| **{rk}** | {rv} |")
            out_parts.append("\n".join(table_lines))
        if len(portraits) > 1:
            extra_imgs = " ".join(f"![{alt}]({src})" for alt, src in portraits[1:])
            out_parts.append(f"**Additional Portraits / Posters:**\n\n{extra_imgs}")
        return "\n\n".join(out_parts)

    def render_tabs(self, named: dict[str, str], current_rel_doc: str, page_title: str) -> str:
        root_title = page_title.split("/", 1)[0]
        tabs: list[str] = []
        ordered = sorted(
            ((k, v.strip()) for k, v in named.items() if k.lower().startswith("tab") and v.strip()),
            key=lambda kv: int(re.sub(r"\D", "", kv[0]) or "0"),
        )
        for idx, (_, label) in enumerate(ordered):
            cand_title = root_title if idx == 0 or normalize_key(label) == normalize_key(root_title) else f"{root_title}/{label}"
            dest = self.page_by_norm.get(normalize_key(cand_title))
            if normalize_key(cand_title) == normalize_key(page_title):
                tabs.append(f"**{label}**")
            elif dest:
                href = rel_link(current_rel_doc, title_to_rel_md(dest["title"]))
                tabs.append(f"[{label}]({href})")
            else:
                tabs.append(label)
        if not tabs:
            return ""
        return "> **Subpages:** " + " · ".join(tabs)

    def expand_templates(self, text: str, current_rel_doc: str, page_title: str, depth: int = 0) -> str:
        if depth > 6:
            return text

        spans = extract_balanced_templates(text)
        if not spans:
            return text

        out: list[str] = []
        last = 0
        for start, end, inner in spans:
            out.append(text[last:start])
            last = end
            tname, pos, named = split_template_parts(inner)
            tnorm = normalize_key(tname)

            if tnorm == "sitename":
                out.append("Sword of Coming Wiki")
            elif tnorm == "-":
                out.append("\n\n---\n\n")
            elif tnorm.startswith("raw:") or tnorm.startswith("navbox"):
                out.append("")
            elif tnorm in SPOILER_TEMPLATES:
                out.append(
                    "\n\n> **Spoiler Warning:** This article contains spoilers from the *Sword of Coming* (*Jian Lai* / *Unsheathed*) novel and donghua.\n\n"
                )
            elif tnorm == "main":
                links = [self.resolve_page_link(p, None, current_rel_doc) for p in pos if p.strip()]
                if links:
                    out.append("\n\n> *Main article: " + ", ".join(links) + "*\n\n")
            elif tnorm == "tabs":
                rendered_tabs = self.render_tabs(named, current_rel_doc, page_title)
                if rendered_tabs:
                    out.append("\n\n" + rendered_tabs + "\n\n")
            elif tnorm in INFOBOX_NAMES and (named or pos):
                # Distinguish `{{characters}}` main-page template (no params) from `{{Character|...}}` infobox
                ib = self.render_infobox(tnorm, named, current_rel_doc, page_title)
                if ib:
                    out.append("\n\n" + ib + "\n\n")
            elif tnorm == "header":
                hdr = named.get("header", "").strip()
                body = named.get("content", "").strip()
                expanded_body = self.expand_templates(body, current_rel_doc, page_title, depth + 1)
                hdr_md = f"\n\n## {hdr}\n\n" if hdr else "\n\n"
                out.append(hdr_md + expanded_body + "\n\n")
            elif tnorm == "charportal":
                img_name = named.get("Image") or named.get("image") or ""
                art = named.get("articlename") or named.get("Name") or ""
                disp = named.get("Name") or art
                rec = self.lookup_media(img_name, page_title) if img_name else None
                link_md = self.resolve_page_link(art, disp, current_rel_doc)
                if rec:
                    src = self.media_src(rec, current_rel_doc)
                    out.append(f"![{disp}]({src})<br>**{link_md}**")
                else:
                    out.append(f"**{link_md}**")
            elif tnorm == "ep-list":
                num = named.get("number", "").strip()
                ep = named.get("ep", num).strip()
                img_name = named.get("image", "").strip()
                novel = named.get("novel", "—").strip() or "—"
                link = named.get("link", "").strip()
                rec = self.lookup_media(img_name, page_title) if img_name else None
                img_md = f"![Episode {ep}]({self.media_src(rec, current_rel_doc)})" if rec else "—"
                link_md = f"[Episode {ep}]({link})" if link else f"Episode {ep}"
                out.append(f"| {num} | {img_md} | {novel} | {link_md} |")
            elif tnorm in self.templates:
                raw_t = self.templates[tnorm]
                # Strip template-only categories (`[[Category:Main Page Templates]]`)
                raw_t = re.sub(r"\[\[Category:[^\]]+\]\]", "", raw_t, flags=re.I)
                expanded = self.expand_templates(raw_t, current_rel_doc, page_title, depth + 1)
                out.append("\n\n" + expanded + "\n\n")
            else:
                out.append("")
        out.append(text[last:])
        return "".join(out)

    def render_file_embed(self, inner: str, current_rel_doc: str, page_title: str) -> str:
        """Convert `File:Foo.png|thumb|caption` inside `[[...]]`."""
        parts = [p.strip() for p in inner.split("|")]
        raw_file = parts[0]
        fname = raw_file.split(":", 1)[1].strip() if ":" in raw_file else raw_file
        opts_ignore = {"thumb", "thumbnail", "frame", "frameless", "border", "center", "left", "right", "none"}
        caption = ""
        link_target = ""
        for p in parts[1:]:
            pl = p.lower()
            if pl in opts_ignore or re.match(r"^\d*x?\d+px$", pl):
                continue
            if pl.startswith("link="):
                link_target = p[5:].strip()
                continue
            if pl.startswith("alt="):
                continue
            caption = p
        rec = self.lookup_media(fname, page_title)
        alt = caption or fname
        if rec:
            src = self.media_src(rec, current_rel_doc)
            if rec["mime"] == "video/youtube":
                img_md = f"![Video Preview: {alt}]({src})"
            else:
                img_md = f"![{alt}]({src})"
            if link_target:
                if link_target.startswith(("http://", "https://")):
                    return f"[{img_md}]({link_target})"
                dest = self.page_by_norm.get(normalize_key(link_target))
                if dest:
                    href = rel_link(current_rel_doc, title_to_rel_md(dest["title"]))
                    return f"[{img_md}]({href})"
            return img_md
        return f"*[Missing media: {fname}]*"

    def convert_wiki_tables(self, text: str, current_rel_doc: str, page_title: str) -> str:
        """Convert `{| ... |}` MediaWiki tables (including nested wrapper tables) to Markdown."""
        table_re = re.compile(r"\{\|([^{}]*?)\|\}", re.S)
        guard = 0
        while "{|" in text and guard < 15:
            guard += 1
            new_text = table_re.sub(
                lambda m: self._render_single_table(m.group(1), current_rel_doc, page_title),
                text,
            )
            if new_text == text:
                break
            text = new_text
        return text

    def _strip_cell_attrs(self, cell: str) -> tuple[str, int]:
        """Strip `style="..." colspan="4" |` prefix from a MediaWiki table cell."""
        colspan = 1
        m_cs = re.search(r'colspan\s*=\s*"?(\d+)"?', cell)
        if m_cs:
            colspan = int(m_cs.group(1))
        if "|" in cell:
            # Check if everything before the first `|` (outside `[[...]]` / `{{...}}`) looks like HTML attributes
            dt = dl = 0
            for idx, ch in enumerate(cell):
                if idx + 1 < len(cell) and cell[idx : idx + 2] in ("[[", "{{"):
                    if cell[idx : idx + 2] == "[[":
                        dl += 1
                    else:
                        dt += 1
                elif idx + 1 < len(cell) and cell[idx : idx + 2] in ("]]", "}}"):
                    if cell[idx : idx + 2] == "]]" and dl > 0:
                        dl -= 1
                    elif cell[idx : idx + 2] == "}}" and dt > 0:
                        dt -= 1
                elif ch == "|" and dt == 0 and dl == 0:
                    prefix = cell[:idx].strip()
                    if re.search(r'(?:style|class|width|align|colspan|rowspan|scope|border|valign)\s*=', prefix, re.I):
                        return cell[idx + 1 :].strip(), colspan
                    break
        return cell.strip(), colspan

    def _render_single_table(self, body: str, current_rel_doc: str, page_title: str) -> str:
        # Unwrap outer layout table wrapping an already-rendered Markdown table
        if "| --- |" in body and "! " not in body and "|-" not in body:
            inner_lines = [
                ln for ln in body.splitlines()
                if ln.strip() and not re.search(r'(?:width|border|align|style|cellspacing)\s*=', ln, re.I) and ln.strip() != "|"
            ]
            return "\n\n" + "\n".join(inner_lines) + "\n\n"

        lines = body.splitlines()
        rows: list[list[tuple[str, bool, int]]] = []  # [(content, is_header, colspan)]
        cur_row: list[tuple[str, bool, int]] = []
        cur_cell: list[str] | None = None
        cur_is_hdr = False

        def flush_cell() -> None:
            nonlocal cur_cell
            if cur_cell is not None:
                raw = "\n".join(cur_cell).strip()
                cleaned, cs = self._strip_cell_attrs(raw)
                cur_row.append((cleaned, cur_is_hdr, cs))
                cur_cell = None

        def flush_row() -> None:
            nonlocal cur_row
            flush_cell()
            if cur_row:
                rows.append(cur_row)
                cur_row = []

        for idx, line in enumerate(lines):
            s = line.strip()
            if idx == 0 and (not s or re.search(r'(?:class|style|width|border|cellspacing|align)\s*=', s, re.I)):
                continue
            if s.startswith("|+"):
                continue
            if s.startswith("|-"):
                flush_row()
                continue
            # Already-expanded `{{Ep-list}}` Markdown row `| 1 | ![...] | — | [...] |`
            if s.startswith("| ") and s.endswith(" |") and s.count("|") >= 4:
                flush_row()
                inner_cells = [c.strip() for c in s.strip("|").split("|")]
                rows.append([(c, False, 1) for c in inner_cells])
                continue
            if s.startswith("!") or s.startswith("|"):
                is_hdr = s.startswith("!")
                flush_cell()
                rest = s[1:]
                sep = "!!" if is_hdr else "||"
                parts = rest.split(sep)
                for p in parts[:-1]:
                    cleaned, cs = self._strip_cell_attrs(p)
                    cur_row.append((cleaned, is_hdr, cs))
                cur_is_hdr = is_hdr
                cur_cell = [parts[-1]]
            else:
                if cur_cell is not None:
                    cur_cell.append(line)

        flush_row()
        if not rows:
            return ""

        # If single-row wrapper table (like `Template:Characters`), join cells cleanly as a Markdown table
        max_cols = max(sum(cs for _, _, cs in r) for r in rows)
        if max_cols <= 0:
            return ""

        md_rows: list[str] = []
        for r_idx, r in enumerate(rows):
            rendered_cells: list[str] = []
            for cell_txt, is_hdr, cs in r:
                c_inline = " ".join(
                    ln.strip() for ln in self.convert_inline(cell_txt, current_rel_doc, page_title).splitlines() if ln.strip()
                )
                c_inline = c_inline.replace("|", "\\|")
                if is_hdr and r_idx > 0 and c_inline and not c_inline.startswith("**"):
                    c_inline = f"**{c_inline}**"
                rendered_cells.append(c_inline)
                for _ in range(cs - 1):
                    rendered_cells.append("")
            while len(rendered_cells) < max_cols:
                rendered_cells.append("")
            md_rows.append("| " + " | ".join(rendered_cells[:max_cols]) + " |")
            if r_idx == 0:
                md_rows.append("| " + " | ".join(["---"] * max_cols) + " |")

        return "\n\n" + "\n".join(md_rows) + "\n\n"

    def convert_inline(self, text: str, current_rel_doc: str, page_title: str) -> str:
        # Remove magic words
        text = text.replace("__TOC__", "").replace("__NOTOC__", "")

        # Extract inline Categories if any slipped through
        text = re.sub(r"\[\[Category:[^\]]+\]\]", "", text, flags=re.I)

        # Format glued `<ref>` Chapter citations: e.g. `sideburns.Chapter 61` or `Chapter 509Chapter 511`
        text = re.sub(
            r"(?<!\[)(?<!\()(?<=[a-zA-Z0-9.”\"'\)\]])Chapter\s+(\d+(?:\s*\(\d+\))?)",
            r" *(Chapter \1)*",
            text,
        )
        # Consecutive `*(Chapter 509)*Chapter 511` -> `*(Chapter 509, Chapter 511)*`
        text = re.sub(
            r"\*\(Chapter ([^)]+)\)\*\s*Chapter\s+(\d+(?:\s*\(\d+\))?)",
            r"*(Chapter \1, Chapter \2)*",
            text,
        )

        # Convert `[[File:...]]` / `[[Image:...]]` and `[[Page|Label]]`
        def repl_wikilink(m: re.Match) -> str:
            inner = m.group(1)
            if inner.strip().lower().startswith(("file:", "image:")):
                return self.render_file_embed(inner, current_rel_doc, page_title)
            if "|" in inner:
                t, lbl = inner.split("|", 1)
                return self.resolve_page_link(t, lbl, current_rel_doc)
            return self.resolve_page_link(inner, None, current_rel_doc)

        text = re.sub(r"\[\[([^\[\]]+)\]\]", repl_wikilink, text)

        # Convert external MediaWiki links `[https://example.com Label]` (avoiding Markdown `[Label](url)`)
        def repl_extlink(m: re.Match) -> str:
            url = m.group(1)
            lbl = (m.group(2) or "").strip()
            return f"[{lbl or url}]({url})"

        text = re.sub(r"(?<!!)\[(https?://[^\s\]]+)(?:\s+([^\]]+))?\](?!\()", repl_extlink, text)

        # Bold + Italic `'''''...'''''`, Bold `'''...'''`, Italic `''...''`
        text = re.sub(r"'''''(.+?)'''''", r"***\1***", text)
        text = re.sub(r"'''(.+?)'''", r"**\1**", text)
        text = re.sub(r"''(.+?)''", r"*\1*", text)
        return text

    def convert_block_lines(self, text: str, current_rel_doc: str, page_title: str) -> str:
        lines = text.splitlines()
        out: list[str] = []
        va_rows: list[tuple[str, str, str]] = []

        def flush_va() -> None:
            nonlocal va_rows
            if va_rows:
                out.append("| Portrait | Character | Voice Actor |")
                out.append("| :--- | :--- | :--- |")
                for img_md, char_md, actor_md in va_rows:
                    out.append(f"| {img_md} | {char_md} | {actor_md} |")
                out.append("")
                va_rows = []

        for raw_line in lines:
            s = raw_line.strip()

            # Fandom tabber labels `|-|Season 1=...`
            if s.startswith("|-|") and "=" in s:
                flush_va()
                tab_label, rest = s[3:].split("=", 1)
                out.append(f"### {tab_label.strip()}")
                if rest.strip():
                    out.append(self.convert_inline(rest.strip(), current_rel_doc, page_title))
                continue

            # Voice actor gallery rows like `ping.jpg|[[Chen Ping An]]|[https://... Name]`
            if s.count("|") == 2 and not s.startswith(("|", "!", "{", "[")):
                p0, p1, p2 = [x.strip() for x in s.split("|", 2)]
                if IMAGE_EXT_RE.search(p0):
                    rec = self.lookup_media(p0, page_title)
                    img_md = f"![{p0}]({self.media_src(rec, current_rel_doc)})" if rec else f"*{p0}*"
                    char_md = self.convert_inline(p1, current_rel_doc, page_title)
                    actor_md = self.convert_inline(p2, current_rel_doc, page_title)
                    va_rows.append((img_md, char_md, actor_md))
                    continue

            flush_va()

            # Bare gallery image filenames on their own line (from `<gallery>` tags)
            if s and not s.startswith(("[", "!", "#", "*", "|", "=", "{", ">")) and IMAGE_EXT_RE.search(s) and len(s) < 120:
                fname = s.split("|", 1)[0].strip()
                rec = self.lookup_media(fname, page_title)
                if rec:
                    out.append(f"![{fname}]({self.media_src(rec, current_rel_doc)})")
                    continue

            # MediaWiki headings `== Heading ==`
            m_hdr = re.match(r"^(=+)\s*(.*?)\s*\1\s*$", s)
            if m_hdr:
                level = min(6, len(m_hdr.group(1)))
                h_txt = self.convert_inline(m_hdr.group(2), current_rel_doc, page_title)
                # Strip redundant bold markers inside headings
                h_txt = re.sub(r"^\*\*(.*)\*\*$", r"\1", h_txt.strip())
                out.append(f"\n{'#' * level} {h_txt}\n")
                continue

            # Bullet / numbered lists (`*Item` -> `* Item`, `**Item` -> `  * Item`, `#Item` -> `1. Item`)
            m_bul = re.match(r"^(\*+)\s*(.+)$", s)
            if m_bul and not s.startswith("**"):
                indent = "  " * (len(m_bul.group(1)) - 1)
                out.append(f"{indent}- {self.convert_inline(m_bul.group(2), current_rel_doc, page_title)}")
                continue
            if s.startswith("**") and not s.endswith("**") and "'''" in raw_line:
                m_bul2 = re.match(r"^(\*+)\s*(.+)$", raw_line.strip())
                if m_bul2:
                    indent = "  " * (len(m_bul2.group(1)) - 1)
                    out.append(f"{indent}- {self.convert_inline(m_bul2.group(2), current_rel_doc, page_title)}")
                    continue

            out.append(self.convert_inline(raw_line, current_rel_doc, page_title))

        flush_va()
        rendered = "\n".join(out)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
        return rendered

    def render_page(self, page: dict) -> str:
        title = page["title"]
        pageid = page["pageid"]
        ts = page.get("timestamp", "")
        raw = page["content"]
        rel_doc = title_to_rel_md(title)
        fandom_url = "https://sword-of-coming.fandom.com/wiki/" + quote(title.replace(" ", "_"), safe="/_-'()")

        # Collect explicit inline `[[Category:...]]` + API `categories`
        cats = set(page.get("categories", []))
        for cm in re.finditer(r"\[\[Category:([^\]|]+)", raw, flags=re.I):
            cname = cm.group(1).strip()
            if cname == "{{SITENAME}}":
                cname = "Sword of Coming Wiki"
            cats.add(cname)
        sorted_cats = sorted(cats)
        for c in sorted_cats:
            self.category_members.setdefault(c, set()).add(title)

        # Redirect page handling
        if raw.lstrip().upper().startswith("#REDIRECT"):
            dest_title, dest_anchor = self.redirect_map.get(normalize_key(title), ("", ""))
            dest_link = self.resolve_page_link(
                f"{dest_title}#{dest_anchor}" if dest_anchor else dest_title,
                f"{dest_title} ({dest_anchor})" if dest_anchor else dest_title,
                rel_doc,
                follow_redirects=False,
            )
            body = f"> **Redirect page** → {dest_link}"
        else:
            expanded = self.expand_templates(raw, rel_doc, title)
            with_tables = self.convert_wiki_tables(expanded, rel_doc, title)
            body = self.convert_block_lines(with_tables, rel_doc, title)
            if not body:
                body = "*This article is currently a stub on the Sword of Coming Fandom Wiki.*"

        index_href = rel_link(rel_doc, "INDEX.md")
        header_lines = [
            f"# {title}",
            "",
            f"[← Wiki Index]({index_href}) · [Original Fandom Page]({fandom_url})",
            "",
            "---",
            "",
        ]

        footer_lines = ["", "---", ""]
        if sorted_cats:
            cat_links = [
                f"[{c}]({rel_link(rel_doc, cat_to_rel_md(c))})"
                for c in sorted_cats
            ]
            footer_lines.append("**Categories:** " + " · ".join(cat_links))
        footer_lines.append(
            f"*Source: `sword-of-coming.fandom.com` · Page ID: `{pageid}` · Last revision: `{ts}`*"
        )
        return "\n".join(header_lines) + body + "\n" + "\n".join(footer_lines) + "\n"

    def write_categories(self) -> int:
        cat_dir = self.out_dir / "categories"
        cat_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for cname in sorted(self.category_members.keys()):
            rel_doc = cat_to_rel_md(cname)
            members = sorted(self.category_members[cname])
            fandom_cat_url = "https://sword-of-coming.fandom.com/wiki/Category:" + quote(
                cname.replace(" ", "_"), safe="/_-'()"
            )
            lines = [
                f"# Category: {cname}",
                "",
                f"[← Wiki Index]({rel_link(rel_doc, 'INDEX.md')}) · [Fandom Category]({fandom_cat_url})",
                "",
                f"Total pages in category: **{len(members)}**",
                "",
            ]
            if members:
                for m_title in members:
                    href = rel_link(rel_doc, title_to_rel_md(m_title))
                    is_redir = normalize_key(m_title) in self.redirect_map
                    suffix = " *(redirect)*" if is_redir else ""
                    lines.append(f"- [{m_title}]({href}){suffix}")
            else:
                lines.append("*No main-namespace articles currently assigned to this category.*")
            lines.append("")
            out_path = self.out_dir / rel_doc
            out_path.write_text("\n".join(lines), encoding="utf-8")
            count += 1
        return count

    def write_gallery_and_manifest(self) -> tuple[Path, Path]:
        manifest_items: list[dict] = []
        gallery_rel = "GALLERY.md"
        gallery_lines = [
            "# Media Gallery (All 245 Fandom Files)",
            "",
            "[← Wiki Index](INDEX.md)",
            "",
            "Complete catalog of all **245** media items (`222` raster images + `23` YouTube video preview embeds) registered on `sword-of-coming.fandom.com` (`list=allimages`).",
            "",
            "To download all 245 files locally into `wik_fandom/images/` and optionally switch Markdown links to offline relative paths, run:",
            "",
            "```bash",
            "python3 scripts/download_fandom_images.py --rewrite-local",
            "```",
            "",
            "| # | File Name | Type | Dimensions | Size | Used On | Preview / Link |",
            "| ---: | :--- | :--- | :--- | ---: | :--- | :--- |",
        ]

        for idx, m in enumerate(self.media_raw, 1):
            rec = self.media_by_exact[m["name"]]
            used_on = sorted(self.media_usage.get(m["name"], set()))
            item = {
                "name": m["name"],
                "local_path": rec["local_path"],
                "mime": m["mime"],
                "size": m["size"],
                "width": m["width"],
                "height": m["height"],
                "sha1": m["sha1"],
                "url": m["url"],
                "used_on_pages": used_on,
            }
            manifest_items.append(item)

            src = self.media_src(rec, gallery_rel)
            dims = f"{m['width']}×{m['height']}"
            size_kb = f"{m['size'] / 1024:.1f} KB"
            used_links = ", ".join(
                f"[{t}]({rel_link(gallery_rel, title_to_rel_md(t))})" for t in used_on[:3]
            )
            if len(used_on) > 3:
                used_links += f" (+{len(used_on) - 3})"
            if not used_links:
                used_links = "*(unreferenced upload)*"
            safe_name = m["name"].replace("|", "\\|")
            preview_md = f"[Open File]({src})"
            gallery_lines.append(
                f"| {idx} | `{safe_name}` | `{m['mime']}` | {dims} | {size_kb} | {used_links} | {preview_md} |"
            )

        gallery_path = self.out_dir / gallery_rel
        gallery_path.write_text("\n".join(gallery_lines) + "\n", encoding="utf-8")

        manifest_obj = {
            "source_wiki": self.dump["source_wiki"],
            "snapshot_date": self.dump["snapshot_date"],
            "total_media": len(manifest_items),
            "raster_images": sum(1 for x in manifest_items if x["mime"].startswith("image/")),
            "youtube_embeds": sum(1 for x in manifest_items if x["mime"] == "video/youtube"),
            "referenced_in_articles": sum(1 for x in manifest_items if x["used_on_pages"]),
            "items": manifest_items,
        }
        manifest_path = self.out_dir / "images_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return gallery_path, manifest_path

    def write_index(self) -> Path:
        index_rel = "INDEX.md"
        articles = [p for p in self.pages if normalize_key(p["title"]) not in self.redirect_map]
        redirects = [p for p in self.pages if normalize_key(p["title"]) in self.redirect_map]

        characters = []
        world_locations = []
        sects_clans = []
        cultivation_lore = []
        media_portal = []
        subpages = []

        loc_cats = {"Locations", "Location", "Continents", "Continent", "Realms", "Worlds", "Small Worlds", "Nations"}
        sect_cats = {"Sects", "Clans"}
        cult_cats = {"Cultivation", "Items"}
        media_titles = {"Sword of Coming Wiki", "Donghua", "Light Novel"}

        for p in articles:
            t = p["title"]
            cset = set(p.get("categories", []))
            if "/" in t:
                subpages.append(p)
            elif t in media_titles or "Media" in cset or "Donghua" in cset:
                media_portal.append(p)
            elif "Characters" in cset or "{{Character" in p["content"]:
                characters.append(p)
            elif cset & sect_cats or t in ("Sects", "Clans"):
                sects_clans.append(p)
            elif cset & loc_cats:
                world_locations.append(p)
            elif cset & cult_cats:
                cultivation_lore.append(p)
            else:
                cultivation_lore.append(p)

        def fmt_list(plist: list[dict]) -> list[str]:
            return [
                f"- [{p['title']}]({rel_link(index_rel, title_to_rel_md(p['title']))})"
                for p in sorted(plist, key=lambda x: x["title"].lower())
            ]

        lines = [
            "# Sword of Coming Fandom Wiki — Master Index",
            "",
            "Complete offline/Markdown mirror of **`sword-of-coming.fandom.com`** (`Special:AllPages`).",
            "",
            "- **Total Main-Namespace Pages (`ns=0`):** `183` (`163` articles + `20` redirects)",
            f"- **Categories:** `{len(self.category_members)}`",
            f"- **Media & Images (`allimages`):** `{len(self.media_raw)}` ([Browse Full Media Gallery](GALLERY.md) · [`images_manifest.json`](images_manifest.json))",
            "",
            "## Portal, Novel & Donghua",
            "",
            *fmt_list(media_portal),
            "",
            f"## Characters ({len(characters)})",
            "",
            *fmt_list(characters),
            "",
            f"## Character & Media Subpages ({len(subpages)})",
            "",
            *fmt_list(subpages),
            "",
            f"## World, Continents, Nations & Locations ({len(world_locations)})",
            "",
            *fmt_list(world_locations),
            "",
            f"## Sects & Clans ({len(sects_clans)})",
            "",
            *fmt_list(sects_clans),
            "",
            f"## Cultivation, Systems & Lore ({len(cultivation_lore)})",
            "",
            *fmt_list(cultivation_lore),
            "",
            f"## Categories ({len(self.category_members)})",
            "",
        ]
        for cname in sorted(self.category_members.keys()):
            cnt = len(self.category_members[cname])
            lines.append(
                f"- [{cname}]({rel_link(index_rel, cat_to_rel_md(cname))}) ({cnt})"
            )

        lines.extend([
            "",
            f"## Redirect Pages ({len(redirects)})",
            "",
        ])
        for p in sorted(redirects, key=lambda x: x["title"].lower()):
            dest_t, dest_a = self.redirect_map[normalize_key(p["title"])]
            src_href = rel_link(index_rel, title_to_rel_md(p["title"]))
            dest_href = rel_link(index_rel, title_to_rel_md(dest_t), dest_a)
            anchor_note = f"#{dest_a}" if dest_a else ""
            lines.append(f"- [{p['title']}]({src_href}) → [{dest_t}{anchor_note}]({dest_href})")

        index_path = self.out_dir / index_rel
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return index_path

    def verify_links(self) -> list[tuple[str, str]]:
        """Verify every relative Markdown link across all generated `.md` files resolves on disk."""
        from urllib.parse import unquote

        link_re = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
        broken: list[tuple[str, str]] = []
        for md_file in sorted(self.out_dir.rglob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            for m in link_re.finditer(text):
                href = m.group(1).strip()
                if not href or href.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                path_part = unquote(href.split("#", 1)[0])
                if not path_part:
                    continue
                resolved = (md_file.parent / path_part).resolve()
                if not resolved.exists():
                    broken.append((str(md_file.relative_to(self.out_dir)), href))
        return broken

    def build(self) -> dict:
        pages_dir = self.out_dir / "pages"
        cats_dir = self.out_dir / "categories"
        if pages_dir.exists():
            shutil.rmtree(pages_dir)
        if cats_dir.exists():
            shutil.rmtree(cats_dir)
        pages_dir.mkdir(parents=True, exist_ok=True)

        for p in self.pages:
            rel_md = title_to_rel_md(p["title"])
            out_path = self.out_dir / rel_md
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(self.render_page(p), encoding="utf-8")

        cat_count = self.write_categories()
        self.write_gallery_and_manifest()
        self.write_index()
        broken = self.verify_links()

        return {
            "ok": len(broken) == 0,
            "pages_written": len(self.pages),
            "categories_written": cat_count,
            "media_manifest_count": len(self.media_raw),
            "broken_relative_links": len(broken),
            "broken_samples": broken[:10],
        }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build wik_fandom/ Markdown mirror from fandom_dump.json")
    ap.add_argument(
        "--dump",
        type=Path,
        default=Path("wik_fandom/_raw/fandom_dump.json"),
        help="Path to consolidated MediaWiki JSON dump",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("wik_fandom"),
        help="Target wiki directory (default: wik_fandom)",
    )
    ap.add_argument(
        "--local-images",
        action="store_true",
        help="Emit relative local `images/...` paths instead of `static.wikia.nocookie.net` CDN URLs",
    )
    args = ap.parse_args()

    builder = WikiBuilder(args.dump, args.out, use_local_images=args.local_images)
    summary = builder.build()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
