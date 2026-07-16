"""Qt-free chat reply HTML rendering helpers."""

from __future__ import annotations

import html
import re

import config
from core.assistant_text import split_tagged_text
from ui.text_annotations import (
    TextAnnotation,
    annotation_tooltip_anchor,
    annotations_for_subrange,
    compose_annotated_slices,
    normalize_range_annotations,
)

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_TABLE_DIVIDER_CELL_RE = re.compile(r"^:?-{3,}:?$")

# Colours for code blocks and "thinking" text in rendered replies. Seeded with
# the original dark values and refreshed from the app theme by
# ui.chat_window._refresh_chat_palette() so light mode renders readable code.
_RENDER_PALETTE: dict[str, str] = {
    "code_bg": "#26263a",
    "code_inline_bg": "#303044",
    "thought": "#8f8f9e",
    "table_header_bg": "#34345a",
    "table_row_bg": "#26263a",
    "table_alt_bg": "#2b2b43",
    "table_border": "#494967",
}


def set_render_palette(
    *,
    code_bg: str = "",
    code_inline_bg: str = "",
    thought: str = "",
    table_header_bg: str = "",
    table_row_bg: str = "",
    table_alt_bg: str = "",
    table_border: str = "",
) -> None:
    """Update the themed colours used when rendering chat reply HTML."""
    if code_bg:
        _RENDER_PALETTE["code_bg"] = code_bg
    if code_inline_bg:
        _RENDER_PALETTE["code_inline_bg"] = code_inline_bg
    if thought:
        _RENDER_PALETTE["thought"] = thought
    if table_header_bg:
        _RENDER_PALETTE["table_header_bg"] = table_header_bg
    if table_row_bg:
        _RENDER_PALETTE["table_row_bg"] = table_row_bg
    if table_alt_bg:
        _RENDER_PALETTE["table_alt_bg"] = table_alt_bg
    if table_border:
        _RENDER_PALETTE["table_border"] = table_border
_NUMBER_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_WS_RE = re.compile(r"(\s+)")


def _split_table_row(line: str) -> list[tuple[str, int]]:
    """Return Markdown table cells and their source offsets.

    Pipes inside inline-code spans or escaped as ``\\|`` remain part of the
    cell. Offsets let addon annotations keep targeting the original text.
    """
    cells: list[tuple[str, int]] = []
    boundaries: list[int] = []
    in_code = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "`":
            in_code = not in_code
            continue
        if char == "|" and not in_code:
            boundaries.append(index)

    if not boundaries:
        return []
    starts = [0, *(position + 1 for position in boundaries)]
    ends = [*boundaries, len(line)]
    raw_cells = list(zip(starts, ends, strict=True))
    if raw_cells and not line[raw_cells[0][0]:raw_cells[0][1]].strip():
        raw_cells.pop(0)
    if raw_cells and not line[raw_cells[-1][0]:raw_cells[-1][1]].strip():
        raw_cells.pop()
    for start, end in raw_cells:
        raw = line[start:end]
        leading = len(raw) - len(raw.lstrip())
        value = raw.strip().replace(r"\|", "|")
        cells.append((value, start + leading))
    return cells


def _table_block_at(lines: list[str], start: int) -> tuple[list, list, list, int] | None:
    """Parse a GitHub-style pipe table beginning at ``start``."""
    if start + 1 >= len(lines):
        return None
    header = _split_table_row(lines[start])
    divider = _split_table_row(lines[start + 1])
    if not header or len(header) != len(divider):
        return None
    if not all(_TABLE_DIVIDER_CELL_RE.fullmatch(cell.strip()) for cell, _offset in divider):
        return None
    alignments: list[str] = []
    for cell, _offset in divider:
        if cell.startswith(":") and cell.endswith(":"):
            alignments.append("center")
        elif cell.endswith(":"):
            alignments.append("right")
        else:
            alignments.append("left")
    rows: list[list[tuple[str, int]]] = []
    end = start + 2
    while end < len(lines):
        row = _split_table_row(lines[end])
        if not row:
            break
        if len(row) < len(header):
            row.extend([("", len(lines[end]))] * (len(header) - len(row)))
        rows.append(row[:len(header)])
        end += 1
    return header, alignments, rows, end


def _compact_markdown_tables(text: str) -> str:
    """Turn pipe tables into a narrow, speech-bubble-friendly card list."""
    lines = str(text or "").splitlines()
    output: list[str] = []
    index = 0
    in_fence = False
    while index < len(lines):
        if _FENCE_RE.match(lines[index]):
            in_fence = not in_fence
            output.append(lines[index])
            index += 1
            continue
        if in_fence:
            output.append(lines[index])
            index += 1
            continue
        table = _table_block_at(lines, index)
        if table is None:
            output.append(lines[index])
            index += 1
            continue
        header, _alignments, rows, end = table
        header_values = [cell for cell, _offset in header]
        output.append("**" + "  ·  ".join(header_values) + "**")
        for row in rows:
            values = [cell for cell, _offset in row]
            if not values:
                continue
            lead = values[0]
            rest = "  ·  ".join(value for value in values[1:] if value)
            output.append(f"**{lead}** — {rest}" if rest else f"**{lead}**")
        index = end
    return "\n".join(output)


def _contains_markdown_table(text: str) -> bool:
    """Return whether text contains a table outside a fenced code block."""
    lines = str(text or "").splitlines()
    in_fence = False
    for index, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and _table_block_at(lines, index) is not None:
            return True
    return False


def _annotation_attrs(annotation: TextAnnotation, extra_style: str = "") -> str:
    """Return sanitized HTML attributes for one annotation span."""
    attrs: list[str] = []
    style = "; ".join(s for s in [annotation.style, extra_style] if s)
    if style:
        attrs.append(f'style="{html.escape(style, quote=True)}"')
    return " ".join(attrs)


def _annotated_text_html(text: str, annotations: list[TextAnnotation], extra_style: str = "") -> str:
    """Escape text and wrap annotated slices in safe spans."""
    if not annotations:
        escaped = html.escape(text)
        if extra_style:
            style = html.escape(extra_style, quote=True)
            return f'<span style="{style}">{escaped}</span>'
        return escaped
    parts: list[str] = []
    for item in compose_annotated_slices(text, annotations):
        escaped = html.escape(item.text)
        if item.annotation is None:
            if extra_style:
                style = html.escape(extra_style, quote=True)
                parts.append(f'<span style="{style}">{escaped}</span>')
            else:
                parts.append(escaped)
        else:
            attrs = _annotation_attrs(item.annotation, extra_style)
            attr_text = f" {attrs}" if attrs else ""
            rendered = f"<{item.annotation.tag}{attr_text}>{escaped}</{item.annotation.tag}>"
            anchor = annotation_tooltip_anchor(item.annotation)
            if anchor:
                rendered = (
                    f'<a href="{anchor}" style="color: inherit; text-decoration: none;">'
                    f"{rendered}</a>"
                )
            parts.append(rendered)
    return "".join(parts)


def _segment_text_to_html(text: str, annotations: list[TextAnnotation] | None = None, base_offset: int = 0) -> str:
    """Render plain text for QTextBrowser HTML."""
    if not annotations:
        return html.escape(text).replace("\n", "<br>")
    clipped = annotations_for_subrange(annotations, base_offset, base_offset + len(text))
    return _annotated_text_html(text, clipped).replace("\n", "<br>")


def _inline_markdown_html(
    text: str,
    annotations: list[TextAnnotation] | None = None,
    base_offset: int = 0,
) -> str:
    """Render a small, safe inline markdown subset for chat messages."""
    if annotations:
        return _inline_markdown_html_annotated(text, annotations, base_offset)

    placeholders: list[str] = []

    def stash_code(match: re.Match[str]) -> str:
        placeholders.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\x00{len(placeholders) - 1}\x00"

    escaped = re.sub(r"`([^`\n]+)`", stash_code, text)
    escaped = html.escape(escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(?!\s)(.+?)(?<!\s)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"_(?!\s)(.+?)(?<!\s)_", r"<em>\1</em>", escaped)
    for idx, rendered in enumerate(placeholders):
        escaped = escaped.replace(html.escape(f"\x00{idx}\x00"), rendered)
    return escaped


def _inline_markdown_html_annotated(
    text: str,
    annotations: list[TextAnnotation],
    base_offset: int,
) -> str:
    """Render inline markdown while decorating only escaped text nodes."""
    parts: list[str] = []
    code_pattern = re.compile(r"`([^`\n]+)`")
    cursor = 0
    for match in code_pattern.finditer(text):
        if match.start() > cursor:
            parts.append(_inline_plain_markdown_annotated(text[cursor:match.start()], annotations, base_offset + cursor))
        parts.append(f"<code>{html.escape(match.group(1))}</code>")
        cursor = match.end()
    if cursor < len(text):
        parts.append(_inline_plain_markdown_annotated(text[cursor:], annotations, base_offset + cursor))
    return "".join(parts)


def _inline_plain_markdown_annotated(
    text: str,
    annotations: list[TextAnnotation],
    base_offset: int,
) -> str:
    """Render bold/emphasis markers and annotation spans for non-code inline text."""
    parts: list[str] = []
    strong = False
    emphasis = False
    buf = ""
    buf_start = 0

    def flush(until: int) -> None:
        nonlocal buf, buf_start
        if not buf:
            buf_start = until
            return
        start = base_offset + buf_start
        clipped = annotations_for_subrange(annotations, start, start + len(buf))
        parts.append(_annotated_text_html(buf, clipped))
        buf = ""
        buf_start = until

    i = 0
    while i < len(text):
        if text.startswith("**", i) or text.startswith("__", i):
            flush(i)
            parts.append("</strong>" if strong else "<strong>")
            strong = not strong
            i += 2
            buf_start = i
            continue
        if text[i] in {"*", "_"}:
            flush(i)
            parts.append("</em>" if emphasis else "<em>")
            emphasis = not emphasis
            i += 1
            buf_start = i
            continue
        if not buf:
            buf_start = i
        buf += text[i]
        i += 1
    flush(len(text))
    if emphasis:
        parts.append("</em>")
    if strong:
        parts.append("</strong>")
    return "".join(parts)


def _chat_markdown_html(
    text: str,
    annotations: list[TextAnnotation] | None = None,
    base_offset: int = 0,
) -> str:
    """Render model replies with readable paragraphs, lists, and code blocks."""
    lines = str(text or "").splitlines()
    if not lines:
        return ""
    parts: list[str] = []
    paragraph: list[tuple[str, int]] = []
    list_kind: str | None = None
    code_lines: list[str] | None = None
    offset = base_offset

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            body = "<br>".join(_inline_markdown_html(line, annotations, line_offset) for line, line_offset in paragraph)
            parts.append(f"<p>{body}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            parts.append(f"</{list_kind}>")
            list_kind = None

    line_offsets: list[int] = []
    running_offset = base_offset
    for raw_line in lines:
        line_offsets.append(running_offset)
        running_offset += len(raw_line) + 1

    line_index = 0
    while line_index < len(lines):
        raw_line = lines[line_index]
        line = raw_line.rstrip()
        if code_lines is not None:
            if _FENCE_RE.match(line):
                code = html.escape("\n".join(code_lines))
                parts.append(f"<pre><code>{code}</code></pre>")
                code_lines = None
            else:
                code_lines.append(raw_line)
            offset += len(raw_line) + 1
            line_index += 1
            continue
        if _FENCE_RE.match(line):
            flush_paragraph()
            close_list()
            code_lines = []
            offset += len(raw_line) + 1
            line_index += 1
            continue
        if not line.strip():
            flush_paragraph()
            close_list()
            offset += len(raw_line) + 1
            line_index += 1
            continue
        table = _table_block_at(lines, line_index)
        if table is not None:
            flush_paragraph()
            close_list()
            header, alignments, rows, end = table
            table_border = _RENDER_PALETTE["table_border"]
            parts.append(
                '<table cellspacing="0" cellpadding="0" '
                'style="border-collapse:collapse; width:100%; margin:2px 0 10px 0;">'
            )
            parts.append("<thead><tr>")
            for column, ((cell, cell_offset), alignment) in enumerate(
                zip(header, alignments, strict=True)
            ):
                edge = "" if column else f"border-left:1px solid {table_border};"
                parts.append(
                    f'<th style="text-align:{alignment}; padding:7px 9px; font-weight:700; '
                    f'background:{_RENDER_PALETTE["table_header_bg"]}; '
                    f'border-top:1px solid {table_border}; border-right:1px solid {table_border}; '
                    f'border-bottom:1px solid {table_border}; {edge}">'
                    f'{_inline_markdown_html(cell, annotations, line_offsets[line_index] + cell_offset)}</th>'
                )
            parts.append("</tr></thead><tbody>")
            for row_index, row in enumerate(rows):
                row_bg = _RENDER_PALETTE["table_alt_bg" if row_index % 2 else "table_row_bg"]
                parts.append("<tr>")
                source_line = line_index + 2 + row_index
                for column, ((cell, cell_offset), alignment) in enumerate(
                    zip(row, alignments, strict=True)
                ):
                    edge = "" if column else f"border-left:1px solid {table_border};"
                    parts.append(
                        f'<td style="text-align:{alignment}; padding:7px 9px; background:{row_bg}; '
                        f'border-right:1px solid {table_border}; border-bottom:1px solid {table_border}; {edge}">'
                        f'{_inline_markdown_html(cell, annotations, line_offsets[source_line] + cell_offset)}</td>'
                    )
                parts.append("</tr>")
            parts.append("</tbody></table>")
            offset = line_offsets[end] if end < len(lines) else running_offset
            line_index = end
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            close_list()
            level = min(6, len(heading.group(1)))
            heading_text = heading.group(2)
            heading_offset = offset + heading.start(2)
            parts.append(f"<h{level}>{_inline_markdown_html(heading_text, annotations, heading_offset)}</h{level}>")
            offset += len(raw_line) + 1
            line_index += 1
            continue
        bullet = _BULLET_RE.match(line)
        number = _NUMBER_RE.match(line)
        if bullet or number:
            flush_paragraph()
            desired = "ul" if bullet else "ol"
            if list_kind != desired:
                close_list()
                parts.append(f"<{desired}>")
                list_kind = desired
            item = (bullet or number).group(1)
            item_offset = offset + (bullet or number).start(1)
            parts.append(f"<li>{_inline_markdown_html(item, annotations, item_offset)}</li>")
            offset += len(raw_line) + 1
            line_index += 1
            continue
        close_list()
        paragraph.append((line, offset))
        offset += len(raw_line) + 1
        line_index += 1

    if code_lines is not None:
        code = html.escape("\n".join(code_lines))
        parts.append(f"<pre><code>{code}</code></pre>")
    flush_paragraph()
    close_list()
    return "".join(parts)


def _accent_color() -> str:
    """The same colour the speech bubble uses to highlight TTS-read words."""
    return getattr(config, "BUBBLE_READ_WORD_COLOR", "#4da3ff") or "#4da3ff"


def _reply_html(
    text: str,
    start_idx: int,
    read_count: int | None,
    annotations: list[TextAnnotation] | None = None,
    base_offset: int = 0,
) -> tuple[int, str]:
    """Render a reply segment for live TTS read-position highlighting."""
    if annotations:
        return _reply_html_annotated(text, start_idx, read_count, annotations, base_offset)

    accent = _accent_color()
    parts: list[str] = []
    idx = start_idx

    def flush(segment: str, is_bold: bool) -> None:
        nonlocal idx
        for piece in _WS_RE.split(segment):
            if not piece:
                continue
            if piece.isspace():
                parts.append(piece.replace("\n", "<br>"))
                continue
            if is_bold:
                read = read_count is None or idx < read_count
                style = "font-weight:bold;"
                if read:
                    style += f"color:{accent};"
                parts.append(f'<span style="{style}">{html.escape(piece)}</span>')
            else:
                parts.append(html.escape(piece))
            idx += 1

    bold = False
    buf = ""
    i = 0
    while i < len(text):
        if text.startswith("**", i) or text.startswith("__", i):
            flush(buf, bold)
            buf = ""
            bold = not bold
            i += 2
            continue
        buf += text[i]
        i += 1
    flush(buf, bold)
    return idx, "".join(parts)


def _reply_html_annotated(
    text: str,
    start_idx: int,
    read_count: int | None,
    annotations: list[TextAnnotation],
    base_offset: int,
) -> tuple[int, str]:
    """Render TTS/read-position spans and annotation spans together."""
    accent = _accent_color()
    parts: list[str] = []
    idx = start_idx

    def flush(segment: str, is_bold: bool, segment_offset: int) -> None:
        nonlocal idx
        local = 0
        for piece in _WS_RE.split(segment):
            if not piece:
                continue
            piece_offset = segment_offset + local
            local += len(piece)
            if piece.isspace():
                parts.append(piece.replace("\n", "<br>"))
                continue
            extra_style = ""
            if is_bold:
                read = read_count is None or idx < read_count
                extra_style = "font-weight:bold;"
                if read:
                    extra_style += f"color:{accent};"
            clipped = annotations_for_subrange(
                annotations,
                base_offset + piece_offset,
                base_offset + piece_offset + len(piece),
            )
            parts.append(_annotated_text_html(piece, clipped, extra_style))
            idx += 1

    bold = False
    buf = ""
    buf_start = 0
    i = 0
    while i < len(text):
        if text.startswith("**", i) or text.startswith("__", i):
            flush(buf, bold, buf_start)
            buf = ""
            bold = not bold
            i += 2
            buf_start = i
            continue
        if not buf:
            buf_start = i
        buf += text[i]
        i += 1
    flush(buf, bold, buf_start)
    return idx, "".join(parts)


def _assistant_segments_to_html(
    segments: list[tuple[str, bool]],
    read_count: int | None = 0,
    annotations: object = None,
) -> str:
    """Render assistant thought/reply segments for the chat transcript."""
    full_text = "".join(text for text, _is_thought in segments)
    safe_annotations = normalize_range_annotations(annotations, full_text) if annotations else []
    # The legacy TTS renderer is a flat word stream and cannot retain block
    # structure. Prefer a stable table over momentarily exposing raw pipes while
    # the read position is mirrored into the full chat window.
    if read_count != 0 and any(
        not is_thought and _contains_markdown_table(text)
        for text, is_thought in segments
    ):
        read_count = 0
    if read_count == 0:
        code_bg = _RENDER_PALETTE["code_bg"]
        code_inline_bg = _RENDER_PALETTE["code_inline_bg"]
        thought_color = _RENDER_PALETTE["thought"]
        parts: list[str] = [
            "<style>"
            "p { margin: 0 0 8px 0; }"
            "p:last-child { margin-bottom: 0; }"
            "ul, ol { margin: 0 0 8px 22px; padding: 0; }"
            "li { margin: 2px 0; }"
            f"pre {{ margin: 0 0 8px 0; padding: 8px; border-radius: 6px;"
            f" background: {code_bg}; white-space: pre-wrap; }}"
            f"code {{ font-family: Consolas, 'Cascadia Mono', monospace;"
            f" background: {code_inline_bg}; padding: 1px 3px; border-radius: 3px; }}"
            "pre code { background: transparent; padding: 0; }"
            "h1, h2, h3, h4, h5, h6 { margin: 0 0 8px 0; font-weight: 700; }"
            "</style>"
        ]
        prev_is_thought: bool | None = None
        offset = 0
        for text, is_thought in segments:
            segment_annotations = annotations_for_subrange(safe_annotations, offset, offset + len(text))
            if is_thought:
                parts.append(
                    f'<div style="color: {thought_color};">'
                    f"{_chat_markdown_html(text, segment_annotations)}</div>"
                )
            else:
                if prev_is_thought:
                    parts.append("<div style='height: 6px;'></div>")
                parts.append(_chat_markdown_html(text, segment_annotations))
            prev_is_thought = is_thought
            offset += len(text)
        return "".join(parts)

    parts: list[str] = []
    idx = 0
    prev_is_thought: bool | None = None
    offset = 0
    for text, is_thought in segments:
        segment_annotations = annotations_for_subrange(safe_annotations, offset, offset + len(text))
        if is_thought:
            parts.append(
                f'<span style="color: {_RENDER_PALETTE["thought"]};">'
                f"{_segment_text_to_html(text, segment_annotations)}</span>"
            )
        else:
            if prev_is_thought:
                parts.append("<br>")
            idx, body = _reply_html(text, idx, read_count, segment_annotations)
            parts.append(body)
        prev_is_thought = is_thought
        offset += len(text)
    return "".join(parts)


def _assistant_text_to_html(text: str, read_count: int | None = 0, annotations: object = None) -> str:
    """Render tagged assistant text for the chat transcript."""
    return _assistant_segments_to_html(split_tagged_text(text), read_count, annotations)


def _user_text_to_html(text: str, annotations: object = None) -> str:
    """Render user text with optional safe annotations."""
    safe_annotations = normalize_range_annotations(annotations, text) if annotations else []
    return _segment_text_to_html(text, safe_annotations)
