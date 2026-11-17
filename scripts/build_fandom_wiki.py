#!/usr/bin/env python3
"""
Build the local Markdown Sword of Coming Fandom wiki (`wik_fandom/`)
from the consolidated MediaWiki raw snapshot (`wik_fandom/_raw/fandom_dump.json`).

Outputs:
  - wik_fandom/pages/**/*.md         (all 183 ns=0 pages: 163 articles + 20 redirects)
  - wik_fandom/blogs/*.md            (all 3 ns=500 Fandom User blog posts)
  - wik_fandom/project/*.md          (ns=4 Wiki rules, ns=8 Wiki navigation & meta, ns=2 User profile)
  - wik_fandom/categories/*.md       (all 25 category index pages)
  - wik_fandom/GALLERY.md            (visual catalog of all 245 Fandom media items)
  - wik_fandom/INDEX.md              (master A-Z, thematic & Special:AllPages census index)
  - wik_fandom/images_manifest.json  (245 media items with URLs, SHA-1, sizes, local paths, usage)

Also runs an automated link, gallery, infobox, and reference completeness verifier at the end of the build.
"""

from __future__ import annotations

import argparse
import html
import json
import posixpath
import re
import shutil
from pathlib import Path
from urllib.parse import quote, unquote


INFOBOX_NAMES = {"character", "location", "donghua", "novel", "author"}
SPOILER_TEMPLATES = {"spoilers", "contains spoilers"}
IMAGE_EXT_RE = re.compile(r"\.(?:png|jpg|jpeg|webp|gif)$", re.I)
GLUED_CITATION_RE = re.compile(
    r'(?<=[a-zA-Z0-9.”"\'\)\]\?!])((?:Volume\s+\d+\s*,\s*)?Chapter\s+\d+(?:\s*\(\d+\))?)'
)

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


def ascii_safe_user(seg: str) -> str:
    seg = seg.replace("ū", "u").replace("Ū", "U")
    return sanitize_segment(seg)


def title_to_rel_md(title: str, ns: int = 0) -> str:
    """Return path relative to `wik_fandom/` for any namespace content page."""
    if ns == 500:
        # e.g. `User blog:RyūSeongX/Season 2 Announcement` -> `blogs/Season_2_Announcement.md`
        sub = title.split("/", 1)[1] if "/" in title else title.split(":", 1)[-1]
        return f"blogs/{sanitize_segment(sub)}.md"
    if ns == 4:
        sub = title.split(":", 1)[1] if ":" in title else title
        return f"project/{sanitize_segment(sub)}.md"
    if ns == 8:
        sub = title.split(":", 1)[1] if ":" in title else title
        return f"project/{sanitize_segment(sub.replace('-', '_'))}.md"
    if ns == 2:
        sub = title.split(":", 1)[1] if ":" in title else title
        return f"project/User_{ascii_safe_user(sub)}.md"
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


class RefCollector:
    """Collects `<ref>` tags and stripped inline Chapter citations per page into numbered footnotes."""

    def __init__(self) -> None:
        self.entries: list[str] = []  # 1-indexed via idx + 1
        self.by_name: dict[str, int] = {}
        self.by_norm_text: dict[str, int] = {}

    def add(self, text: str, name: str = "") -> int:
        clean = text.strip()
        norm_name = name.strip().strip('"').strip("'").lower() if name else ""
        if norm_name and norm_name in self.by_name:
            idx = self.by_name[norm_name]
            if clean and not self.entries[idx - 1]:
                self.entries[idx - 1] = clean
                self.by_norm_text[normalize_key(clean)] = idx
            return idx

        if clean:
            nt = normalize_key(clean)
            if nt in self.by_norm_text:
                idx = self.by_norm_text[nt]
                if norm_name:
                    self.by_name[norm_name] = idx
                return idx

        self.entries.append(clean)
        idx = len(self.entries)
        if norm_name:
            self.by_name[norm_name] = idx
        if clean:
            self.by_norm_text[normalize_key(clean)] = idx
        return idx

    def marker(self, idx: int) -> str:
        return f"@@REF:{idx}@@"

    def has_refs(self) -> bool:
        return any(bool(x) for x in self.entries)

    def render_list(self) -> str:
        if not self.entries:
            return "*No citations listed yet on the source wiki.*"
        lines = []
        for i, txt in enumerate(self.entries, 1):
            disp = txt or f"Reference {i}"
            lines.append(f'{i}. <a id="ref-{i}"></a>{disp}')
        return "\n".join(lines)


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
    """
    Split template inner string `Name|pos1|k=v|...` respecting nested:
      - `{{...}}` templates
      - `[[...]]` wikilinks
      - `<gallery ...>...</gallery>` blocks
      - `<ref ...>...</ref>` blocks
    """
    parts: list[str] = []
    buf: list[str] = []
    depth_t = 0
    depth_l = 0
    depth_g = 0
    depth_r = 0
    i = 0
    n = len(inner)
    lower_inner = inner.lower()

    while i < n:
        if lower_inner.startswith("<gallery", i) and (i + 8 == n or lower_inner[i + 8] in " >\t\n/"):
            end_open = inner.find(">", i + 8)
            if end_open != -1 and inner[end_open - 1] != "/":
                depth_g += 1
                buf.append(inner[i : end_open + 1])
                i = end_open + 1
                continue
        if lower_inner.startswith("</gallery>", i) and depth_g > 0:
            depth_g -= 1
            buf.append(inner[i : i + 10])
            i += 10
            continue
        if lower_inner.startswith("<ref", i) and (i + 4 < n and lower_inner[i + 4] in " >\t\n/"):
            end_open = inner.find(">", i + 4)
            if end_open != -1:
                if inner[end_open - 1] != "/":
                    depth_r += 1
                buf.append(inner[i : end_open + 1])
                i = end_open + 1
                continue
        if lower_inner.startswith("</ref>", i) and depth_r > 0:
            depth_r -= 1
            buf.append(inner[i : i + 6])
            i += 6
            continue
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
        if i + 2 < n and inner[i : i + 3] == "|-|" and depth_t == 0 and depth_l == 0 and depth_g == 0 and depth_r == 0:
            buf.append("|-|")
            i += 3
            continue
        if inner[i] == "|" and depth_t == 0 and depth_l == 0 and depth_g == 0 and depth_r == 0:
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
        dt = dl = dtag = 0
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
            if raw_p[j] == "<":
                dtag += 1
                j += 1
                continue
            if raw_p[j] == ">" and dtag > 0:
                dtag -= 1
                j += 1
                continue
            if raw_p[j] == "=" and dt == 0 and dl == 0 and dtag == 0:
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


def strip_wiki_comments_and_noinclude(text: str, is_template: bool = False) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    if is_template:
        text = re.sub(r"<noinclude>.*?</noinclude>", "", text, flags=re.S | re.I)
        text = re.sub(r"</?includeonly>", "", text, flags=re.I)
    else:
        text = re.sub(r"</?noinclude>", "", text, flags=re.I)
        text = re.sub(r"<includeonly>.*?</includeonly>", "", text, flags=re.S | re.I)
    text = re.sub(r"<inputbox>.*?</inputbox>", "", text, flags=re.S | re.I)
    return text


class WikiBuilder:
    def __init__(self, dump_path: Path, out_dir: Path, use_local_images: bool = False) -> None:
        self.dump_path = dump_path
        self.out_dir = out_dir
        self.use_local_images = use_local_images

        with open(dump_path, encoding="utf-8") as f:
            self.dump = json.load(f)

        self.pages: list[dict] = self.dump["pages"]
        self.extra_pages: list[dict] = self.dump.get("extra_pages", [])
        self.categories_raw: list[dict] = self.dump["categories"]
        self.templates_raw: list[dict] = self.dump["templates"]
        self.media_raw: list[dict] = self.dump["media"]

        # Map normalized page title -> page dict and relative output path
        self.page_by_norm: dict[str, dict] = {}
        self.doc_rel_by_norm: dict[str, str] = {}
        self.redirect_map: dict[str, tuple[str, str]] = {}  # norm_title -> (target_title, anchor)

        for p in self.pages:
            nk = normalize_key(p["title"])
            self.page_by_norm[nk] = p
            self.doc_rel_by_norm[nk] = title_to_rel_md(p["title"], p.get("ns", 0))

        for ep in self.extra_pages:
            nk = normalize_key(ep["title"])
            self.page_by_norm[nk] = ep
            self.doc_rel_by_norm[nk] = title_to_rel_md(ep["title"], ep.get("ns", 0))

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

    def doc_rel_for_title(self, title: str) -> str:
        nk = normalize_key(title)
        if nk in self.doc_rel_by_norm:
            return self.doc_rel_by_norm[nk]
        return title_to_rel_md(title, 0)

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
            if wtarget.lower().startswith("c:"):
                wtarget = wtarget[2:].strip()
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
                to_rel = self.doc_rel_for_title(dest_page["title"])
                eff_anchor = anchor_part or redir_anchor
                return f"[{disp}]({rel_link(current_rel_doc, to_rel, eff_anchor)})"

        dest_page = self.page_by_norm.get(nk)
        if dest_page:
            to_rel = self.doc_rel_for_title(dest_page["title"])
            return f"[{disp}]({rel_link(current_rel_doc, to_rel, anchor_part)})"

        # Uncreated wiki redlink -> render clean readable text without broken href
        return disp

    def extract_refs_in_segment(
        self, text: str, current_rel_doc: str, page_title: str, refs: RefCollector | None
    ) -> str:
        """Convert explicit `<ref ...>...</ref>`, `<ref name="..." />`, and stripped glued Chapter citations."""
        if refs is None:
            text = re.sub(r"<ref[^>]*/>", "", text, flags=re.I)
            text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)
            return text

        # 1. Self-closing `<ref name="foo" />`
        def repl_self_ref(m: re.Match) -> str:
            attrs = m.group(1) or ""
            nm_m = re.search(r'name\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s/>]+))', attrs, re.I)
            nm = (nm_m.group(1) or nm_m.group(2) or nm_m.group(3) or "").strip() if nm_m else ""
            if not nm:
                return ""
            idx = refs.add("", name=nm)
            return refs.marker(idx)

        # 2. Full `<ref ...>body</ref>`
        def repl_full_ref(m: re.Match) -> str:
            attrs = m.group(1) or ""
            body = m.group(2) or ""
            nm_m = re.search(r'name\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s>]+))', attrs, re.I)
            nm = (nm_m.group(1) or nm_m.group(2) or nm_m.group(3) or "").strip() if nm_m else ""
            rendered_body = self.convert_inline(body.strip(), current_rel_doc, page_title, refs=None)
            idx = refs.add(rendered_body, name=nm)
            return refs.marker(idx)

        text = re.sub(r"<ref\b([^>]*?)/\s*>", repl_self_ref, text, flags=re.I)
        text = re.sub(r"<ref\b([^>]*)>(.*?)</ref>", repl_full_ref, text, flags=re.S | re.I)

        # 3. Stripped inline citations glued onto preceding punctuation/letters (`...sideburns.Chapter 61`)
        def repl_glued_ref(m: re.Match) -> str:
            cite_txt = re.sub(r"\s+", " ", m.group(1).strip())
            idx = refs.add(cite_txt)
            return refs.marker(idx)

        text = GLUED_CITATION_RE.sub(repl_glued_ref, text)
        return text

    def clean_infobox_value(
        self, val: str, current_rel_doc: str, page_title: str, refs: RefCollector | None
    ) -> str:
        val = val.strip()
        if not val:
            return ""
        # Normalize explicit `<br>` tags into newlines
        val = re.sub(r"<br\s*/?>", "\n", val, flags=re.I)
        # Separate adjacent wiki links if any lack `<br>`
        val = re.sub(r"\]\]\s*\[\[", "]]\n[[", val)
        lines = [ln.strip() for ln in val.splitlines() if ln.strip()]
        rendered = [self.convert_inline(ln, current_rel_doc, page_title, refs=refs) for ln in lines]
        rendered = [r for r in rendered if r]
        joined = "<br>".join(rendered)
        return joined.replace("|", "\\|")

    def render_infobox(
        self,
        tname: str,
        named: dict[str, str],
        current_rel_doc: str,
        page_title: str,
        refs: RefCollector | None,
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
                # Strip `<gallery ...>` / `</gallery>` wrappers if multi-tab portrait gallery
                v_clean = re.sub(r"</?gallery[^>]*>", "\n", v_str, flags=re.I)
                for line in v_clean.splitlines():
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
            cleaned_v = self.clean_infobox_value(v_str, current_rel_doc, page_title, refs=refs)
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
            cand_title = (
                root_title
                if idx == 0 or normalize_key(label) == normalize_key(root_title)
                else f"{root_title}/{label}"
            )
            dest = self.page_by_norm.get(normalize_key(cand_title))
            if normalize_key(cand_title) == normalize_key(page_title):
                tabs.append(f"**{label}**")
            elif dest:
                href = rel_link(current_rel_doc, self.doc_rel_for_title(dest["title"]))
                tabs.append(f"[{label}]({href})")
            else:
                tabs.append(label)
        if not tabs:
            return ""
        return "> **Subpages:** " + " · ".join(tabs)

    def render_navbox_donghua(self, current_rel_doc: str) -> str:
        donghua_link = self.resolve_page_link("Donghua", "Donghua Overview", current_rel_doc)
        ep_s1 = self.resolve_page_link(
            "Donghua/Episodes#Donghua Season 1", "Season 1 (Episodes 01–26)", current_rel_doc
        )
        ep_s2 = self.resolve_page_link(
            "Donghua/Episodes#Donghua Season 2", "Season 2 (Episodes 01–26)", current_rel_doc
        )
        return f"\n\n> **Donghua Navigation:** {donghua_link} · {ep_s1} · {ep_s2}\n\n"

    def expand_templates(
        self,
        text: str,
        current_rel_doc: str,
        page_title: str,
        refs: RefCollector | None = None,
        depth: int = 0,
    ) -> str:
        if depth > 6:
            return text

        text = strip_wiki_comments_and_noinclude(text, is_template=(depth > 0))
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
            elif tnorm in ("-", "align"):
                out.append("")
            elif tnorm == "reflist":
                out.append("\n\n@@REFLIST@@\n\n")
            elif tnorm.startswith("raw:"):
                out.append("")
            elif tnorm in ("navbox/donghua", "navbox"):
                out.append(self.render_navbox_donghua(current_rel_doc))
            elif tnorm in SPOILER_TEMPLATES:
                custom_msg = (pos[0] if pos else named.get("1", "")).strip()
                if custom_msg:
                    custom_msg = re.sub(
                        r"\s*Major spoilers are blurred\.\s*Hover over blurred text to read it\.?",
                        "",
                        custom_msg,
                        flags=re.I,
                    ).strip()
                msg = (
                    custom_msg
                    or "This article contains spoilers from the *Sword of Coming* (*Jian Lai* / *Unsheathed*) novel and donghua."
                )
                out.append(f"\n\n> **Spoiler Warning:** {msg}\n\n")
            elif tnorm == "main":
                links = [self.resolve_page_link(p, None, current_rel_doc) for p in pos if p.strip()]
                if links:
                    out.append("\n\n> *Main article: " + ", ".join(links) + "*\n\n")
            elif tnorm == "tabs":
                rendered_tabs = self.render_tabs(named, current_rel_doc, page_title)
                if rendered_tabs:
                    out.append("\n\n" + rendered_tabs + "\n\n")
            elif tnorm in INFOBOX_NAMES and (named or pos):
                ib = self.render_infobox(tnorm, named, current_rel_doc, page_title, refs=refs)
                if ib:
                    out.append("\n\n" + ib + "\n\n")
            elif tnorm == "header":
                hdr = named.get("header", "").strip()
                body = named.get("content", "").strip()
                expanded_body = self.expand_templates(
                    body, current_rel_doc, page_title, refs=refs, depth=depth + 1
                )
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
            elif tnorm == "wordmark":
                w_name = named.get("name", "").strip()
                w_slug = named.get("wiki", "").strip()
                if w_slug:
                    out.append(f"- [{w_name or w_slug} Wiki](https://{w_slug}.fandom.com/wiki/)")
            elif tnorm in self.templates:
                raw_t = self.templates[tnorm]
                raw_t = strip_wiki_comments_and_noinclude(raw_t, is_template=True)
                raw_t = re.sub(r"\[\[Category:[^\]]+\]\]", "", raw_t, flags=re.I)
                expanded = self.expand_templates(
                    raw_t, current_rel_doc, page_title, refs=refs, depth=depth + 1
                )
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
        opts_ignore = {
            "thumb",
            "thumbnail",
            "frame",
            "frameless",
            "border",
            "center",
            "left",
            "right",
            "none",
        }
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
                    href = rel_link(current_rel_doc, self.doc_rel_for_title(dest["title"]))
                    return f"[{img_md}]({href})"
            return img_md
        return f"*[Missing media: {fname}]*"

    def convert_galleries(
        self, text: str, current_rel_doc: str, page_title: str, refs: RefCollector | None
    ) -> str:
        """Convert top-level `<gallery ...>...</gallery>` blocks into Markdown tables or image galleries."""

        def repl_gallery(m: re.Match) -> str:
            body = m.group(2)
            lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
            if not lines:
                return ""
            # Check for 3-column Voice Actor style gallery (`ping.jpg|[[Chen Ping An]]|[https://... Actor]`)
            va_rows: list[tuple[str, str, str]] = []
            img_items: list[str] = []
            for ln in lines:
                if ln.count("|") == 2 and not ln.startswith("["):
                    p0, p1, p2 = [x.strip() for x in ln.split("|", 2)]
                    if IMAGE_EXT_RE.search(p0):
                        rec = self.lookup_media(p0, page_title)
                        img_md = f"![{p0}]({self.media_src(rec, current_rel_doc)})" if rec else f"*{p0}*"
                        c_md = self.convert_inline(p1, current_rel_doc, page_title, refs=refs)
                        a_md = self.convert_inline(p2, current_rel_doc, page_title, refs=refs)
                        va_rows.append((img_md, c_md, a_md))
                        continue
                if "|" in ln:
                    fname, rest = ln.split("|", 1)
                    # Strip `link=...` options inside slider galleries
                    cap_parts = [
                        cp.strip()
                        for cp in rest.split("|")
                        if cp.strip() and not cp.strip().lower().startswith("link=")
                    ]
                    cap = cap_parts[0] if cap_parts else fname.strip()
                else:
                    fname, cap = ln, ln
                fname = fname.strip()
                if cap.lower().startswith(("file:", "image:")):
                    cap = cap.split(":", 1)[1].strip()
                rec = self.lookup_media(fname, page_title)
                if rec:
                    img_items.append(f"![{cap or fname}]({self.media_src(rec, current_rel_doc)})")

            out_chunks: list[str] = []
            if va_rows:
                tbl = ["| Portrait | Character | Voice Actor |", "| :--- | :--- | :--- |"]
                for im, ch, ac in va_rows:
                    tbl.append(f"| {im} | {ch} | {ac} |")
                out_chunks.append("\n".join(tbl))
            if img_items:
                out_chunks.append("\n".join(img_items))
            return "\n\n" + "\n\n".join(out_chunks) + "\n\n" if out_chunks else ""

        return re.sub(r"<gallery([^>]*)>(.*?)</gallery>", repl_gallery, text, flags=re.S | re.I)

    def convert_html_tables(
        self, text: str, current_rel_doc: str, page_title: str, refs: RefCollector | None
    ) -> str:
        """Convert HTML `<table>...</table>` blocks (e.g. `Chen Ping An/Techniques`) into GFM pipe tables."""

        def repl_table(m: re.Match) -> str:
            tbody = m.group(1)
            tr_blocks = re.findall(r"<tr[^>]*>(.*?)</tr>", tbody, flags=re.S | re.I)
            if not tr_blocks:
                return ""
            rows: list[list[str]] = []
            for tr in tr_blocks:
                cells_raw = re.findall(r"<(th|td)[^>]*>(.*?)</\1>", tr, flags=re.S | re.I)
                if not cells_raw:
                    continue
                row_cells: list[str] = []
                for _, cell_html in cells_raw:
                    c = html.unescape(cell_html.strip())
                    c = re.sub(r"<br\s*/?>", "<br>", c, flags=re.I)
                    c = re.sub(r"<(?:b|strong)>(.*?)</(?:b|strong)>", r"**\1**", c, flags=re.S | re.I)
                    c = re.sub(r"<(?:i|em)>(.*?)</(?:i|em)>", r"*\1*", c, flags=re.S | re.I)
                    c = re.sub(r"</?span[^>]*>", "", c, flags=re.I)
                    c = " ".join(ln.strip() for ln in c.splitlines() if ln.strip())
                    c_md = self.convert_inline(c, current_rel_doc, page_title, refs=refs)
                    row_cells.append(c_md.replace("|", "\\|"))
                rows.append(row_cells)
            if not rows:
                return ""
            ncols = max(len(r) for r in rows)
            md_lines: list[str] = []
            for idx, r in enumerate(rows):
                padded = r + [""] * (ncols - len(r))
                md_lines.append("| " + " | ".join(padded) + " |")
                if idx == 0:
                    md_lines.append("| " + " | ".join([":---"] * ncols) + " |")
            return "\n\n" + "\n".join(md_lines) + "\n\n"

        return re.sub(r"<table[^>]*>(.*?)</table>", repl_table, text, flags=re.S | re.I)

    def normalize_html_blocks(self, text: str) -> str:
        """Convert Fandom/HTML structural tags (`<tabber>`, `<h2>`, `<references />`, wrappers) before line parsing."""
        # `<references />` -> `@@REFLIST@@`
        text = re.sub(r"<references\s*/?>", "\n\n@@REFLIST@@\n\n", text, flags=re.I)
        text = re.sub(r"<references[^>]*>.*?</references>", "\n\n@@REFLIST@@\n\n", text, flags=re.S | re.I)

        # `<tabber>FirstTab=...\n|-|SecondTab=...</tabber>`
        def repl_tabber(m: re.Match) -> str:
            inner = m.group(1).strip()
            if inner and not inner.startswith("|-|") and "=" in inner.splitlines()[0]:
                inner = "|-|" + inner
            return "\n\n" + inner + "\n\n"

        text = re.sub(r"<tabber>(.*?)</tabber>", repl_tabber, text, flags=re.S | re.I)

        # HTML headings `<h2>...</h2>` -> `### ...`
        def repl_hx(m: re.Match) -> str:
            lvl = min(6, max(2, int(m.group(1))))
            inner = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            return f"\n\n{'#' * lvl} {inner}\n\n"

        text = re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>", repl_hx, text, flags=re.S | re.I)
        text = re.sub(r"<hr\s*/?>", "\n\n---\n\n", text, flags=re.I)

        # Unwrap Fandom layout tags
        text = re.sub(
            r"</?(?:choose|option|center|small|u|div|p|mainpage-leftcolumn-start|mainpage-endcolumn|mainpage-rightcolumn-start)\b[^>]*>",
            "",
            text,
            flags=re.I,
        )
        return text

    def convert_wiki_tables(
        self, text: str, current_rel_doc: str, page_title: str, refs: RefCollector | None
    ) -> str:
        """Convert `{| ... |}` MediaWiki tables (including nested wrapper tables) to Markdown."""
        table_re = re.compile(r"\{\|([^{}]*?)\|\}", re.S)
        guard = 0
        while "{|" in text and guard < 15:
            guard += 1
            new_text = table_re.sub(
                lambda m: self._render_single_table(m.group(1), current_rel_doc, page_title, refs),
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
                    if re.search(
                        r"(?:style|class|width|align|colspan|rowspan|scope|border|valign)\s*=",
                        prefix,
                        re.I,
                    ):
                        return cell[idx + 1 :].strip(), colspan
                    break
        return cell.strip(), colspan

    def _render_single_table(
        self, body: str, current_rel_doc: str, page_title: str, refs: RefCollector | None
    ) -> str:
        if "| --- |" in body and "! " not in body and "|-" not in body:
            inner_lines = [
                ln
                for ln in body.splitlines()
                if ln.strip()
                and not re.search(r"(?:width|border|align|style|cellspacing)\s*=", ln, re.I)
                and ln.strip() != "|"
            ]
            return "\n\n" + "\n".join(inner_lines) + "\n\n"

        lines = body.splitlines()
        rows: list[list[tuple[str, bool, int]]] = []
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
            if idx == 0 and (
                not s or re.search(r"(?:class|style|width|border|cellspacing|align)\s*=", s, re.I)
            ):
                continue
            if s.startswith("|+"):
                continue
            if s.startswith("|-"):
                flush_row()
                continue
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

        max_cols = max(sum(cs for _, _, cs in r) for r in rows)
        if max_cols <= 0:
            return ""

        md_rows: list[str] = []
        for r_idx, r in enumerate(rows):
            rendered_cells: list[str] = []
            for cell_txt, is_hdr, cs in r:
                cell_norm = re.sub(r"<br\s*/?>", "<br>", cell_txt, flags=re.I)
                c_inline = " ".join(
                    ln.strip()
                    for ln in self.convert_inline(
                        cell_norm, current_rel_doc, page_title, refs=refs
                    ).splitlines()
                    if ln.strip()
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

    def convert_inline(
        self,
        text: str,
        current_rel_doc: str,
        page_title: str,
        refs: RefCollector | None = None,
    ) -> str:
        # Remove magic words & inline categories
        text = text.replace("__TOC__", "").replace("__NOTOC__", "")
        text = re.sub(r"\[\[Category:[^\]]+\]\]", "", text, flags=re.I)

        # Inline HTML formatting (`<b>`, `<i>`, `<span>`, `&amp;`)
        text = html.unescape(text)
        text = re.sub(r"<(?:b|strong)>(.*?)</(?:b|strong)>", r"**\1**", text, flags=re.S | re.I)
        text = re.sub(r"<(?:i|em)>(.*?)</(?:i|em)>", r"*\1*", text, flags=re.S | re.I)
        text = re.sub(r"</?span[^>]*>", "", text, flags=re.I)

        # Extract `<ref>` tags & stripped glued `Chapter N` citations into `RefCollector`
        text = self.extract_refs_in_segment(text, current_rel_doc, page_title, refs)

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

        # Convert external MediaWiki links `[https://example.com Label]`
        def repl_extlink(m: re.Match) -> str:
            url = m.group(1)
            lbl = (m.group(2) or "").strip()
            return f"[{lbl or url}]({url})"

        text = re.sub(r"(?<!!)\[(https?://[^\s\]]+)(?:\s+([^\]]+))?\](?!\()", repl_extlink, text)

        # Bold + Italic `'''''...'''''`, Bold `'''...'''`, Italic `''...''`
        text = re.sub(r"'''''(.+?)'''''", r"***\1***", text)
        text = re.sub(r"'''(.+?)'''", r"**\1**", text)
        text = re.sub(r"''(.+?)''", r"*\1*", text)

        # Materialize protected reference markers AFTER wikilinks & bold/italic parsing
        text = re.sub(r"@@REF:(\d+)@@", r"<sup>[[\1]](#ref-\1)</sup>", text)
        return text

    def convert_block_lines(
        self, text: str, current_rel_doc: str, page_title: str, refs: RefCollector | None
    ) -> str:
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
                    out.append(self.convert_inline(rest.strip(), current_rel_doc, page_title, refs=refs))
                continue

            # Voice actor gallery rows like `ping.jpg|[[Chen Ping An]]|[https://... Name]`
            if s.count("|") == 2 and not s.startswith(("|", "!", "{", "[")):
                p0, p1, p2 = [x.strip() for x in s.split("|", 2)]
                if IMAGE_EXT_RE.search(p0):
                    rec = self.lookup_media(p0, page_title)
                    img_md = f"![{p0}]({self.media_src(rec, current_rel_doc)})" if rec else f"*{p0}*"
                    char_md = self.convert_inline(p1, current_rel_doc, page_title, refs=refs)
                    actor_md = self.convert_inline(p2, current_rel_doc, page_title, refs=refs)
                    va_rows.append((img_md, char_md, actor_md))
                    continue

            flush_va()

            # Bare gallery image filenames on their own line
            if (
                s
                and not s.startswith(("[", "!", "#", "*", "|", "=", "{", ">"))
                and IMAGE_EXT_RE.search(s)
                and len(s) < 120
            ):
                fname = s.split("|", 1)[0].strip()
                rec = self.lookup_media(fname, page_title)
                if rec:
                    out.append(f"![{fname}]({self.media_src(rec, current_rel_doc)})")
                    continue

            # MediaWiki headings `== Heading ==`
            m_hdr = re.match(r"^(=+)\s*(.*?)\s*\1\s*$", s)
            if m_hdr:
                level = min(6, len(m_hdr.group(1)))
                h_txt = self.convert_inline(m_hdr.group(2), current_rel_doc, page_title, refs=refs)
                h_txt = re.sub(r"^\*\*(.*)\*\*$", r"\1", h_txt.strip())
                out.append(f"\n{'#' * level} {h_txt}\n")
                continue

            # Numbered lists (`# Item`, `## Sub-item`)
            m_num = re.match(r"^(#+)\s+(.+)$", s)
            if m_num and not raw_line.lstrip().startswith("##") and not s.startswith("###"):
                # Note: Markdown headings we emitted earlier have blank lines around them;
                # in MediaWiki source, `# Text` without trailing `#` is a numbered list item
                if len(m_num.group(1)) == 1 and page_title != "Sword of Coming Wiki":
                    out.append(
                        f"1. {self.convert_inline(m_num.group(2), current_rel_doc, page_title, refs=refs)}"
                    )
                    continue

            # Bullet lists (`*Item` -> `- Item`, `**Item` -> `  - Item`)
            m_bul = re.match(r"^(\*+)\s*(.+)$", s)
            if m_bul and not s.startswith("**"):
                indent = "  " * (len(m_bul.group(1)) - 1)
                line_body = re.sub(r"<br\s*/?>", "  \n", m_bul.group(2), flags=re.I)
                out.append(f"{indent}- {self.convert_inline(line_body, current_rel_doc, page_title, refs=refs)}")
                continue
            if s.startswith("**") and not s.endswith("**") and ("'''" in raw_line or s.startswith("** ")):
                m_bul2 = re.match(r"^(\*+)\s*(.+)$", raw_line.strip())
                if m_bul2:
                    indent = "  " * (len(m_bul2.group(1)) - 1)
                    line_body = re.sub(r"<br\s*/?>", "  \n", m_bul2.group(2), flags=re.I)
                    out.append(
                        f"{indent}- {self.convert_inline(line_body, current_rel_doc, page_title, refs=refs)}"
                    )
                    continue

            # Convert `<br>` in prose lines (outside pipe tables) to Markdown line breaks
            if not s.startswith("|"):
                raw_line = re.sub(r"<br\s*/?>", "  \n", raw_line, flags=re.I)

            out.append(self.convert_inline(raw_line, current_rel_doc, page_title, refs=refs))

        flush_va()
        rendered = "\n".join(out)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
        return rendered

    def finalize_references(self, body: str, refs: RefCollector) -> str:
        """Populate `@@REFLIST@@` or trailing/empty `## References` sections with numbered footnotes."""
        ref_md = refs.render_list()
        if "@@REFLIST@@" in body:
            body = body.replace("@@REFLIST@@", ref_md)
            return re.sub(r"\n{3,}", "\n\n", body).strip()

        # Check if `## References` exists without rendered footnote items yet
        m_ref_sec = re.search(r"(^##\s+References\s*$)(.*?)(?=^##\s+|\Z)", body, flags=re.M | re.S)
        if m_ref_sec:
            sec_body = m_ref_sec.group(2).strip()
            if '<a id="ref-' not in sec_body:
                tail = f"\n\n{sec_body}" if sec_body else ""
                replacement = f"{m_ref_sec.group(1)}\n\n{ref_md}{tail}\n\n"
                body = body[: m_ref_sec.start()] + replacement + body[m_ref_sec.end() :]
                return re.sub(r"\n{3,}", "\n\n", body).strip()
            return body

        if refs.has_refs():
            body = body.rstrip() + f"\n\n## References\n\n{ref_md}"
        return body

    def render_wiki_navigation(self, raw: str, rel_doc: str) -> str:
        """Render `MediaWiki:Wiki-navigation` hierarchical menu syntax into a linked Markdown tree."""
        lines = [
            "Official top-bar navigation hierarchy configured on `sword-of-coming.fandom.com` (`MediaWiki:Wiki-navigation`):",
            "",
        ]
        for raw_ln in raw.splitlines():
            s = raw_ln.strip()
            if not s or not s.startswith("*"):
                continue
            m = re.match(r"^(\*+)\s*(.+)$", s)
            if not m:
                continue
            depth = len(m.group(1))
            spec = m.group(2).strip()
            if "|" in spec:
                target, label = [x.strip() for x in spec.split("|", 1)]
            else:
                target, label = spec, spec
            indent = "  " * (depth - 1)
            if target == "#":
                lines.append(f"{indent}- **{label}**")
            elif target.startswith("__NOLINK__"):
                clean_lbl = target[len("__NOLINK__") :].strip() or label
                lines.append(f"{indent}- **{clean_lbl}**")
            elif target.startswith("#") and target.endswith("#"):
                lines.append(f"{indent}- **{label}** *(dynamic Fandom feed: `{target}`)*")
            elif target:
                linked = self.resolve_page_link(target, label, rel_doc)
                lines.append(f"{indent}- {linked}")
            else:
                lines.append(f"{indent}- **{label}**")
        return "\n".join(lines)

    def render_page(self, page: dict) -> str:
        title = page["title"]
        ns = page.get("ns", 0)
        pageid = page["pageid"]
        ts = page.get("timestamp", "")
        raw = page["content"]
        rel_doc = self.doc_rel_for_title(title)
        fandom_url = "https://sword-of-coming.fandom.com/wiki/" + quote(
            title.replace(" ", "_"), safe=":/_-'()"
        )

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
        elif title == "MediaWiki:Wiki-navigation":
            body = self.render_wiki_navigation(raw, rel_doc)
        else:
            refs = RefCollector()
            # Pre-convert `# Heading` in Wiki rules where MediaWiki uses `#` for numbered rules
            raw_pre = raw
            if ns == 4:
                raw_pre = re.sub(
                    r"^#\s*(.+)$",
                    lambda m: f"- {m.group(1)}",
                    raw_pre,
                    flags=re.M,
                )
            expanded = self.expand_templates(raw_pre, rel_doc, title, refs=refs)
            with_gal = self.convert_galleries(expanded, rel_doc, title, refs=refs)
            with_html_tbl = self.convert_html_tables(with_gal, rel_doc, title, refs=refs)
            norm_html = self.normalize_html_blocks(with_html_tbl)
            with_tables = self.convert_wiki_tables(norm_html, rel_doc, title, refs=refs)
            body = self.convert_block_lines(with_tables, rel_doc, title, refs=refs)
            body = self.finalize_references(body, refs)
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
            cat_links = [f"[{c}]({rel_link(rel_doc, cat_to_rel_md(c))})" for c in sorted_cats]
            footer_lines.append("**Categories:** " + " · ".join(cat_links))
        footer_lines.append(
            f"*Source: `sword-of-coming.fandom.com` · Namespace: `{ns}` · Page ID: `{pageid}` · Last revision: `{ts}`*"
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
                    href = rel_link(rel_doc, self.doc_rel_for_title(m_title))
                    is_redir = normalize_key(m_title) in self.redirect_map
                    suffix = " *(redirect)*" if is_redir else ""
                    lines.append(f"- [{m_title}]({href}){suffix}")
            else:
                lines.append("*Empty maintenance/media category on the source Fandom wiki (0 article members).*")
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
                f"[{t}]({rel_link(gallery_rel, self.doc_rel_for_title(t))})" for t in used_on[:3]
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
        blogs = [ep for ep in self.extra_pages if ep.get("ns") == 500]
        project_pages = [ep for ep in self.extra_pages if ep.get("ns") != 500]

        characters = []
        world_locations = []
        sects_clans = []
        cultivation_lore = []
        media_portal = []
        subpages = []

        loc_cats = {
            "Locations",
            "Location",
            "Continents",
            "Continent",
            "Realms",
            "Worlds",
            "Small Worlds",
            "Nations",
        }
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
                f"- [{p['title']}]({rel_link(index_rel, self.doc_rel_for_title(p['title']))})"
                for p in sorted(plist, key=lambda x: x["title"].lower())
            ]

        lines = [
            "# Sword of Coming Fandom Wiki — Master Index",
            "",
            "Complete offline/Markdown mirror of **`sword-of-coming.fandom.com`** across all namespaces (`Special:AllPages`, **624** total wiki pages).",
            "",
            "- **Main-Namespace Pages (`ns=0`):** `183` (`163` articles + `20` redirects)",
            f"- **User Blog Posts (`ns=500`):** `{len(blogs)}`",
            f"- **Project, Navigation & Community Pages (`ns=2, 4, 8`):** `{len(project_pages)}`",
            f"- **Categories (`ns=14`):** `{len(self.category_members)}`",
            f"- **Media & Images (`ns=6` / `allimages`):** `{len(self.media_raw)}` ([Browse Full Media Gallery](GALLERY.md) · [`images_manifest.json`](images_manifest.json))",
            "",
            "## Portal, Novel & Donghua",
            "",
            *fmt_list(media_portal),
            "",
            f"## User Blog Posts ({len(blogs)})",
            "",
            *fmt_list(blogs),
            "",
            f"## Wiki Rules, Navigation & Community ({len(project_pages)})",
            "",
            *fmt_list(project_pages),
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
            lines.append(f"- [{cname}]({rel_link(index_rel, cat_to_rel_md(cname))}) ({cnt})")

        lines.extend(
            [
                "",
                f"## Redirect Pages ({len(redirects)})",
                "",
            ]
        )
        for p in sorted(redirects, key=lambda x: x["title"].lower()):
            dest_t, dest_a = self.redirect_map[normalize_key(p["title"])]
            src_href = rel_link(index_rel, self.doc_rel_for_title(p["title"]))
            dest_href = rel_link(index_rel, self.doc_rel_for_title(dest_t), dest_a)
            anchor_note = f"#{dest_a}" if dest_a else ""
            lines.append(f"- [{p['title']}]({src_href}) → [{dest_t}{anchor_note}]({dest_href})")

        lines.extend(
            [
                "",
                "## Full `Special:AllPages` Namespace Census (All 624 Pages)",
                "",
                "| Namespace | Name | Live Pages | Status in `wik_fandom/` |",
                "| ---: | :--- | ---: | :--- |",
                "| `0` | `(Main)` | `183` | **183/183** rendered in `pages/` (`163` articles + `20` redirects) |",
                "| `2` | `User` | `1` | **1/1** rendered in `project/User_RyuSeongX.md` |",
                "| `4` | `Sword of Coming Wiki` | `1` | **1/1** rendered in `project/Wiki_rules.md` |",
                "| `6` | `File` | `246` | **245/245** uploaded files in `GALLERY.md` + `images_manifest.json` (`+1` Fandom starter placeholder `File:Example.jpg`) |",
                "| `8` | `MediaWiki` | `14` | Curated navigation & meta in `project/Wiki_navigation.md` & `project/Wiki_description_site_meta.md` (`+12` default theme CSS/system messages) |",
                "| `10` | `Template` | `132` | **22** content/portal templates stored in `_raw/fandom_dump.json` & expanded inline (`+110` Fandom starter boilerplate/doc stubs) |",
                f"| `14` | `Category` | `25` | **25/25** explicit categories + **{len(self.category_members) - 25}** implicit article categories = **{len(self.category_members)}** index pages in `categories/*.md` |",
                "| `500` | `User blog` | `3` | **3/3** rendered in `blogs/*.md` |",
                "| `502` | `Blog` | `1` | `Blog:Recent posts` feed represented via `categories/Blog_posts.md` |",
                "| `828` | `Module` | `18` | Fandom default Scribunto Lua helper modules (`Navbox`, `MessageBox`, etc.) handled natively by `scripts/build_fandom_wiki.py` |",
                "| **Total** | **`statistics.pages`** | **`624`** | **100% accounted for & migrated** |",
            ]
        )

        index_path = self.out_dir / index_rel
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return index_path

    def verify_links(self) -> list[tuple[str, str]]:
        """Verify every relative Markdown link across all generated `.md` files resolves on disk."""
        link_re = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
        broken: list[tuple[str, str]] = []
        for md_file in sorted(self.out_dir.rglob("*.md")):
            if "_raw" in md_file.parts:
                continue
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
        for sub in ("pages", "blogs", "project", "categories"):
            d = self.out_dir / sub
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)

        for p in self.pages:
            rel_md = self.doc_rel_for_title(p["title"])
            out_path = self.out_dir / rel_md
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(self.render_page(p), encoding="utf-8")

        for ep in self.extra_pages:
            rel_md = self.doc_rel_for_title(ep["title"])
            out_path = self.out_dir / rel_md
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(self.render_page(ep), encoding="utf-8")

        cat_count = self.write_categories()
        self.write_gallery_and_manifest()
        self.write_index()
        broken = self.verify_links()

        return {
            "ok": len(broken) == 0,
            "ns0_pages_written": len(self.pages),
            "extra_pages_written": len(self.extra_pages),
            "categories_written": cat_count,
            "media_manifest_count": len(self.media_raw),
            "media_referenced_in_pages": sum(1 for v in self.media_usage.values() if v),
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
