"""Browser page-content extraction helpers for ambient context."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape as html_unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import config

_PAGE_BOILERPLATE_TOKENS = {
    "ad",
    "ads",
    "advertisement",
    "banner",
    "breadcrumbs",
    "cookie",
    "cookies",
    "consent",
    "footer",
    "header",
    "modal",
    "nav",
    "navigation",
    "newsletter",
    "popup",
    "sidebar",
    "subscribe",
}
_PAGE_BOILERPLATE_ROLES = {"banner", "navigation", "contentinfo", "complementary", "dialog"}
_MAX_PAGE_HEADINGS = 20
_MAX_PAGE_LINKS = 12


def _redact(text: str) -> str:
    """Remove secrets while deferring URLs to the session-aware cloud gate."""
    from core.privacy_redaction import redact_text

    return redact_text(text, exclude_categories={"url"})


@dataclass
class PageContext:
    """Structured page text prepared for model context."""

    title: str = ""
    description: str = ""
    headings: list[str] = field(default_factory=list)
    main_text: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)
    content_label: str = "Main content"

    def format(self, max_chars: int) -> str:
        """Render the page context in priority order without relevance labels."""
        parts: list[str] = []
        if self.title:
            parts.append(f"Title:\n{self.title}")
        if self.description:
            parts.append(f"Page description:\n{self.description}")
        if self.headings:
            headings = "\n".join(f"- {heading}" for heading in self.headings[:_MAX_PAGE_HEADINGS])
            parts.append(f"Headings:\n{headings}")
        if self.main_text:
            parts.append(f"{self.content_label}:\n{self.main_text}")
        if self.links:
            links = "\n".join(f"- {label}: {href}" for label, href in self.links[:_MAX_PAGE_LINKS])
            parts.append(f"Links:\n{links}")
        text = "\n\n".join(part for part in parts if part).strip()
        return _clip_page_context(_redact(text), max_chars)


def _clip_page_context(text: str, max_chars: int | None) -> str:
    """Clip page context without cutting through words when possible."""
    text = (text or "").strip()
    if not text or not max_chars or max_chars <= 0 or len(text) <= max_chars:
        return text
    suffix = "\n\n[truncated]"
    limit = max(0, max_chars - len(suffix))
    clipped = text[:limit].rsplit(" ", 1)[0].strip() or text[:limit].strip()
    return f"{clipped}{suffix}"


_MOJIBAKE_MARKER_RE = re.compile(
    r"(?:Ã.|Â.|â[€‚ƒ„…†‡ˆ‰Š‹ŒŽ™š›œžŸ]|ðŸ|ï¿½|�|è¨|äº|æœ|Å|Ëœ)"
)
_MOJIBAKE_REPAIR_CHUNK_RE = re.compile(
    r"[\u00a0-\u00ff\u0152\u0153\u0160\u0161\u0178\u017D\u017E"
    r"\u02c6\u02dc\u201a-\u201e\u2020-\u2022\u2030\u2039\u203a\u20ac\u2122]{2,}"
)


def _mojibake_score(text: str) -> int:
    """Return a rough score for text that looks like UTF-8 decoded as cp1252."""
    raw = str(text or "")
    return (
        len(_MOJIBAKE_MARKER_RE.findall(raw)) * 4
        + raw.count("�") * 8
        + raw.count("ï¿½") * 8
    )


def _repair_mojibake_text(text: str) -> str:
    """Repair common UTF-8-as-cp1252 mojibake in fetched context text."""
    raw = str(text or "")
    if not raw or _mojibake_score(raw) <= 0:
        return raw

    def repair_chunk(match: re.Match[str]) -> str:
        chunk = match.group(0)
        try:
            repaired = chunk.encode("cp1252").decode("utf-8")
        except UnicodeError:
            return chunk
        if _mojibake_score(repaired) < _mojibake_score(chunk):
            return repaired
        return chunk

    repaired = raw
    for _ in range(2):
        next_text = _MOJIBAKE_REPAIR_CHUNK_RE.sub(repair_chunk, repaired)
        if next_text == repaired:
            break
        repaired = next_text
    return repaired


def _normalize_page_text(text: str) -> str:
    """Normalize page text while preserving useful paragraph breaks."""
    text = _repair_mojibake_text(text).replace("\xa0", " ")
    text = re.sub(r"\r\n?", "\n", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    cleaned: list[str] = []
    seen_short: set[str] = set()
    blank_pending = False
    for line in lines:
        if not line:
            blank_pending = bool(cleaned)
            continue
        key = line.casefold()
        if len(line) <= 100 and key in seen_short:
            continue
        if len(line) <= 100:
            seen_short.add(key)
        if blank_pending and cleaned and cleaned[-1]:
            cleaned.append("")
        cleaned.append(line)
        blank_pending = False
    return "\n".join(cleaned).strip()


def _node_tokens(node) -> set[str]:
    """Return normalized id/class/role tokens for an HTML node."""
    raw: list[str] = []
    for attr in ("id", "class", "role", "aria-label"):
        value = node.get(attr)
        if isinstance(value, list):
            raw.extend(str(item) for item in value)
        elif value:
            raw.append(str(value))
    tokens: set[str] = set()
    for item in raw:
        tokens.update(part for part in re.split(r"[^a-z0-9]+", item.lower()) if part)
    return tokens


def _is_page_boilerplate_node(node) -> bool:
    """Return whether an HTML node is probably page chrome, not page content."""
    name = str(getattr(node, "name", "") or "").lower()
    if name in {"html", "body", "main", "article"}:
        return False
    role = str(node.get("role") or "").strip().lower()
    if role in _PAGE_BOILERPLATE_ROLES:
        return True
    tokens = _node_tokens(node)
    return bool(tokens & _PAGE_BOILERPLATE_TOKENS)


def _unique_texts(values: list[str], max_items: int) -> list[str]:
    """Keep non-empty strings once, preserving order."""
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_page_text(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
        if len(results) >= max_items:
            break
    return results


def _html_attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    """Convert HTMLParser attrs into a normalized dictionary."""
    return {str(name).lower(): str(value or "") for name, value in attrs}


def _html_tokens(attrs: dict[str, str]) -> set[str]:
    """Return normalized id/class/role tokens from parser attrs."""
    raw = " ".join(attrs.get(name, "") for name in ("id", "class", "role", "aria-label"))
    return {part for part in re.split(r"[^a-z0-9]+", raw.lower()) if part}


def _is_boilerplate_attrs(tag: str, attrs: dict[str, str]) -> bool:
    """Return whether parser attrs describe likely page chrome."""
    if tag in {"html", "body", "main", "article"}:
        return False
    if attrs.get("hidden") or attrs.get("aria-hidden", "").lower() == "true":
        return True
    if attrs.get("role", "").strip().lower() in _PAGE_BOILERPLATE_ROLES:
        return True
    return bool(_html_tokens(attrs) & _PAGE_BOILERPLATE_TOKENS)


class FallbackHTMLPageParser(HTMLParser):
    """Small stdlib page-context extractor used when BeautifulSoup is absent."""

    _SKIPPED_TAGS = {"script", "style", "template", "noscript", "svg", "iframe", "form"}
    _TEXT_BREAK_TAGS = {
        "article",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "li",
        "main",
        "p",
        "section",
    }

    def __init__(self, url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.url = url
        self.title_parts: list[str] = []
        self.description = ""
        self.headings: list[str] = []
        self._heading_parts: list[str] = []
        self._heading_tag = ""
        self._body_parts: list[str] = []
        self._main_parts: list[str] = []
        self._links: list[tuple[str, str]] = []
        self._active_links: list[list[Any]] = []
        self._skip_depth = 0
        self._body_depth = 0
        self._main_depth = 0
        self._in_title = False
        self._main_seen = False

    def handle_starttag(self, tag: str, attrs_raw: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs = _html_attrs(attrs_raw)
        if tag == "meta":
            key = (attrs.get("name") or attrs.get("property") or "").lower()
            if key in {"description", "og:description"} and attrs.get("content"):
                self.description = _normalize_page_text(html_unescape(attrs["content"]))
            return
        if tag == "body":
            self._body_depth += 1
        if tag in {"main", "article"} or attrs.get("role", "").strip().lower() == "main":
            self._main_depth += 1
            self._main_seen = True
        if tag in self._SKIPPED_TAGS or _is_boilerplate_attrs(tag, attrs):
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in {"h1", "h2", "h3"}:
            self._heading_tag = tag
            self._heading_parts = []
        if tag == "a" and attrs.get("href") and self._inside_content():
            self._active_links.append([urljoin(self.url, attrs["href"].strip()), []])
        if tag in self._TEXT_BREAK_TAGS:
            self._append_text("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag == self._heading_tag:
            heading = _normalize_page_text(" ".join(self._heading_parts))
            if heading:
                self.headings.append(heading)
            self._heading_tag = ""
            self._heading_parts = []
        if tag == "a" and self._active_links:
            href, parts = self._active_links.pop()
            label = _normalize_page_text(" ".join(str(part) for part in parts))
            if label and str(href).startswith(("http://", "https://")):
                self._links.append((label[:120], str(href)))
        if tag in self._TEXT_BREAK_TAGS:
            self._append_text("\n")
        if tag in {"main", "article"} and self._main_depth:
            self._main_depth -= 1
        if tag == "body" and self._body_depth:
            self._body_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = html_unescape(data or "")
        if self._in_title:
            self.title_parts.append(text)
        if self._heading_tag:
            self._heading_parts.append(text)
        for _href, parts in self._active_links:
            parts.append(text)
        if self._inside_content():
            self._append_text(text)

    def _inside_content(self) -> bool:
        return self._main_depth > 0 or (not self._main_seen and self._body_depth > 0)

    def _append_text(self, text: str) -> None:
        if self._main_depth > 0:
            self._main_parts.append(text)
        if self._body_depth > 0:
            self._body_parts.append(text)

    def page_context(self) -> PageContext:
        main_source = self._main_parts if self._main_seen else self._body_parts
        links = _unique_links(self._links, _MAX_PAGE_LINKS)
        return PageContext(
            title=_normalize_page_text(" ".join(self.title_parts)),
            description=self.description,
            headings=_unique_texts(self.headings, _MAX_PAGE_HEADINGS),
            main_text=_normalize_page_text("".join(main_source)),
            links=links,
        )


_FallbackHTMLPageParser = FallbackHTMLPageParser


def _unique_links(values: list[tuple[str, str]], max_items: int) -> list[tuple[str, str]]:
    """Keep non-empty links once, preserving order."""
    links: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, href in values:
        label = _normalize_page_text(label)
        href = str(href or "").strip()
        key = (label.casefold(), href)
        if not label or not href or key in seen:
            continue
        seen.add(key)
        links.append((label, href))
        if len(links) >= max_items:
            break
    return links


def _extract_html_page_context_fallback(url: str, html: str) -> PageContext:
    """Extract useful HTML context with the Python standard library."""
    parser = FallbackHTMLPageParser(url)
    parser.feed(html or "")
    parser.close()
    return parser.page_context()


def _extract_html_page_context(url: str, html: str) -> PageContext:
    """Extract title, headings, main content, and links from HTML."""
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return _extract_html_page_context_fallback(url, html)

    soup = BeautifulSoup(html or "", "html.parser")

    title = _normalize_page_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    description = ""
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta is None:
        meta = soup.find("meta", attrs={"property": re.compile(r"^og:description$", re.I)})
    if meta is not None:
        description = _normalize_page_text(str(meta.get("content") or ""))

    for tag in soup(["script", "style", "template", "noscript", "svg", "iframe", "form"]):
        tag.decompose()
    for tag in list(soup.find_all(True)):
        try:
            if tag.has_attr("hidden") or str(tag.get("aria-hidden") or "").lower() == "true":
                tag.decompose()
            elif _is_page_boilerplate_node(tag):
                tag.decompose()
        except Exception:
            continue

    headings = _unique_texts(
        [tag.get_text(" ", strip=True) for tag in soup.find_all(["h1", "h2", "h3"])],
        _MAX_PAGE_HEADINGS,
    )

    root = soup.select_one("main, article, [role='main']") or soup.body or soup
    main_text = _normalize_page_text(root.get_text("\n", strip=True))

    links: list[tuple[str, str]] = []
    seen_links: set[tuple[str, str]] = set()
    for tag in root.find_all("a", href=True):
        label = _normalize_page_text(tag.get_text(" ", strip=True))
        href = urljoin(url, str(tag.get("href") or "").strip())
        if not label or not href.startswith(("http://", "https://")):
            continue
        key = (label.casefold(), href)
        if key in seen_links:
            continue
        seen_links.add(key)
        links.append((label[:120], href))
        if len(links) >= _MAX_PAGE_LINKS:
            break

    return PageContext(
        title=title,
        description=description,
        headings=headings,
        main_text=main_text,
        links=links,
    )


def _looks_like_rendered_heading(line: str) -> bool:
    """Best-effort heading detector for rendered browser text."""
    text = line.strip()
    if not (3 <= len(text) <= 100):
        return False
    if text.endswith((".", ",", ";", ":")):
        return False
    words = text.split()
    if len(words) > 14:
        return False
    letters = sum(ch.isalpha() for ch in text)
    return letters >= 2


def _extract_rendered_page_context(rendered_text: str) -> PageContext:
    """Extract a stable structure from rendered text captured from a browser."""
    main_text = _normalize_page_text(rendered_text)
    lines = [line.strip() for line in main_text.splitlines() if line.strip()]
    title = lines[0] if lines and len(lines[0]) <= 140 else ""
    heading_candidates = [
        line for line in lines[1 if title else 0 :]
        if _looks_like_rendered_heading(line)
    ]
    headings = _unique_texts(heading_candidates, _MAX_PAGE_HEADINGS)
    return PageContext(
        title=title,
        headings=headings,
        main_text=main_text,
        content_label="Visible page content",
    )


def extract_useful_page_context(
    *,
    url: str = "",
    html: str = "",
    rendered_text: str = "",
    max_chars: int | None = None,
) -> str:
    """Turn fetched or rendered page content into ordered model context."""
    if max_chars is None:
        max_chars = config.CONTEXT_BROWSER_MAX_CHARS
    try:
        page = (
            _extract_html_page_context(url, html)
            if html
            else _extract_rendered_page_context(rendered_text)
        )
        formatted = page.format(max_chars)
        return _redact(formatted)
    except Exception:
        fallback = _normalize_page_text(rendered_text or html)
        return _clip_page_context(_redact(fallback), max_chars)
