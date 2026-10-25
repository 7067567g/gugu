"""Repo/README/outline -> Mermaid (canonical) -> deterministic SVG.

D-P0-01 picks Mermaid as the canonical source and SVG as the canvas artifact.
The box has no ``mmdc``/``cairosvg``/``rsvg-convert`` and adding one would be a
new dependency, so this module renders the small flowchart subset we emit
ourselves. The Mermaid text stays the source of truth in asset metadata, and the
fallback ladder is explicit: Mermaid->SVG, then structured-outline->SVG, then a
text outline artifact (never a hard failure).
"""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

MERMAID_LIMIT = 120
_REPO_IGNORE = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    "coverage",
}
_NODE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[(?P<square>.*)\]|\((?P<round>.*)\)|\{(?P<diamond>.*)\})?$")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_ARROWS = ("-->", "---", "==>", "-.->", "..>")
_MAX_LABEL = 34


class DiagramError(Exception):
    """Raised when a source cannot be turned into a usable diagram."""


# ---------------------------------------------------------------------------
# Source -> Mermaid
# ---------------------------------------------------------------------------


def _clean_label(value: str, limit: int = _MAX_LABEL) -> str:
    text = str(value or "").replace("<br/>", " ").replace("<br>", " ")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = text.replace('"', "").replace("[", "").replace("]", "")
    text = text.replace("{", "").replace("}", "").replace("|", "").replace(";", "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _read_text(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _repo_items(root: Path) -> List[str]:
    names: List[str] = []
    try:
        entries = sorted(root.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except OSError as exc:
        raise DiagramError(f"repo 不可读: {exc}") from exc
    for entry in entries:
        if entry.name.startswith(".") or entry.name in _REPO_IGNORE:
            continue
        if entry.is_dir():
            names.append(entry.name + "/")
        elif entry.suffix.lower() in {".md", ".py", ".ts", ".tsx", ".toml", ".json", ".yaml", ".yml"}:
            names.append(entry.name)
        if len(names) >= 7:
            break
    return names


def _readme_headings(text: str) -> List[str]:
    headings: List[str] = []
    for line in text.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match:
            headings.append(_clean_label(match.group(2)))
        if len(headings) >= 7:
            break
    return [item for item in headings if item]


def _readme_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match and len(match.group(1)) == 1:
            return _clean_label(match.group(2), 48) or fallback
    return fallback


def _outline_lines(source: str) -> List[str]:
    lines: List[str] = []
    for raw in source.splitlines():
        line = raw.strip().lstrip("-*+").strip()
        line = re.sub(r"^\d+[.)]\s*", "", line)
        if not line or line.startswith("```"):
            continue
        lines.append(_clean_label(line, 60))
        if len(lines) >= 10:
            break
    return [item for item in lines if item]


def _edge_statements(lines: Iterable[str]) -> Tuple[List[Tuple[str, str]], List[str]]:
    edges: List[Tuple[str, str]] = []
    nodes: List[str] = []
    for line in lines:
        if "->" not in line:
            continue
        parts = re.split(r"\s*(-->|---|==>|-\.->|\.\.>)\s*", line)
        current: Optional[str] = None
        for index, part in enumerate(parts):
            if index % 2 == 1:
                continue
            label = _clean_label(part.strip().strip("|"), 24)
            if label:
                if current is None:
                    current = label
                    nodes.append(label)
                else:
                    edges.append((current, label))
                    nodes.append(label)
                    current = label
    return edges, nodes


def source_to_mermaid(source: str, source_kind: str, style: str = "") -> Dict[str, Any]:
    """Build canonical Mermaid text plus a structured outline for fallback."""
    kind = (source_kind or "repo").strip().lower()
    if kind not in {"repo", "readme", "outline"}:
        raise DiagramError(f"未知 source_kind: {source_kind!r}")

    title = ""
    outline: List[str] = []
    if kind == "repo":
        path = Path(source).expanduser()
        if path.is_dir():
            title = _clean_label(path.name or "repo", 48)
            items = _repo_items(path)
            outline = [title] + items
            if not items:
                raise DiagramError("repo 没有可画的顶层结构")
            lines = ["flowchart LR", f'  repo["{title}"]']
            for index, item in enumerate(items):
                lines.append(f'  repo --> n{index}["{item}"]')
            return {"title": title, "mermaid": "\n".join(lines), "outline": outline, "source_kind": kind}
        if path.is_file():
            text = _read_text(path)
            title = _readme_title(text, path.stem)
            items = _readme_headings(text)
            outline = [title] + items if items else [title] + _outline_lines(text)
            if not items:
                items = _outline_lines(text)
            if not items:
                raise DiagramError("README 没有可画的标题或段落")
            lines = ["flowchart TD", f'  root["{title}"]']
            for index, item in enumerate(items):
                lines.append(f'  root --> h{index}["{item}"]')
            return {"title": title, "mermaid": "\n".join(lines), "outline": outline, "source_kind": "readme"}
        # Not a path: treat the string itself as README text when it looks like markdown.
        text = source
        title = _readme_title(text, "README") if ("#" in text or "\n" in text) else _clean_label(source, 48)
        items = _readme_headings(text) if "#" in text else []
        if not items:
            items = _outline_lines(text)
        if not items:
            raise DiagramError("repo/readme source 为空或不可读")
        outline = [title] + items
        lines = ["flowchart TD", f'  root["{title}"]']
        for index, item in enumerate(items):
            lines.append(f'  root --> h{index}["{item}"]')
        return {"title": title, "mermaid": "\n".join(lines), "outline": outline, "source_kind": "readme"}

    # outline: may contain explicit "A -> B" edges or a bullet list.
    text = source
    title = "Outline"
    items = _outline_lines(text)
    edges, edge_nodes = _edge_statements(items)
    if edges:
        lines = ["flowchart LR"]
        seen: List[str] = []
        for src, dst in edges:
            for node in (src, dst):
                if node not in seen:
                    seen.append(node)
        for index, node in enumerate(seen):
            lines.append(f'  n{index}["{node}"]')
        index_of = {node: index for index, node in enumerate(seen)}
        for src, dst in edges:
            lines.append(f"  n{index_of[src]} --> n{index_of[dst]}")
        outline = [title] + items
        return {"title": title, "mermaid": "\n".join(lines), "outline": outline, "source_kind": kind}
    if not items:
        raise DiagramError("outline 为空")
    lines = ["flowchart TD", f'  root["{title}"]']
    for index, item in enumerate(items):
        lines.append(f'  root --> o{index}["{item}"]')
    return {"title": title, "mermaid": "\n".join(lines), "outline": [title] + items, "source_kind": kind}


def source_to_outline(source: str, source_kind: str) -> Dict[str, Any]:
    """Structured outline used when Mermaid rendering fails."""
    try:
        built = source_to_mermaid(source, source_kind)
        return {"title": built["title"], "outline": built["outline"]}
    except DiagramError:
        pass
    kind = (source_kind or "repo").strip().lower()
    path = Path(source).expanduser()
    if kind == "repo" and path.is_dir():
        title = _clean_label(path.name or "repo", 48)
        return {"title": title, "outline": [title] + _repo_items(path)}
    lines = _outline_lines(source)
    if not lines:
        raise DiagramError("无法生成结构化大纲")
    return {"title": "Outline", "outline": lines}


# ---------------------------------------------------------------------------
# Mermaid (subset) -> SVG
# ---------------------------------------------------------------------------


def _parse_mermaid(mermaid: str) -> Dict[str, Any]:
    lines = [line.strip() for line in mermaid.splitlines() if line.strip() and not line.strip().startswith("%%")]
    if not lines:
        raise DiagramError("Mermaid 为空")
    header = lines[0]
    if not re.match(r"^(flowchart|graph)\s+(TD|TB|LR|RL|BT)$", header, re.IGNORECASE):
        raise DiagramError(f"不支持的 Mermaid 头: {header!r}")
    direction = header.split()[-1].upper()

    nodes: Dict[str, str] = {}
    edges: List[Dict[str, Any]] = []
    statements: List[str] = []
    for line in lines[1:]:
        statements.extend(part.strip() for part in line.split(";") if part.strip())

    for statement in statements:
        if "->" not in statement and "---" not in statement:
            match = _NODE_RE.match(statement)
            if match:
                nodes[match.group(1)] = _node_label(match)
            continue

        parts = re.split(r"\s*(-->|---|==>|-\.->|\.\.>)\s*", statement)
        previous: Optional[str] = None
        pending_label = ""
        for index, part in enumerate(parts):
            if index % 2 == 1:
                continue
            token = part.strip()
            if not token:
                continue
            edge_label = ""
            label_match = re.match(r"^\|([^|]*)\|\s*(.*)$", token)
            if label_match:
                edge_label = _clean_label(label_match.group(1), 20)
                token = label_match.group(2).strip()
            match = _NODE_RE.match(token)
            if not match:
                previous = None
                continue
            node_id = match.group(1)
            nodes[node_id] = _node_label(match) or nodes.get(node_id, node_id)
            if previous is not None:
                edges.append({"from": previous, "to": node_id, "label": edge_label or pending_label})
            previous = node_id
            pending_label = ""

    if not nodes:
        raise DiagramError("Mermaid 没有解析出节点")
    if len(nodes) < 2:
        raise DiagramError("Mermaid 只有单个节点，降级为结构化大纲")
    for edge in edges:
        if edge["from"] not in nodes or edge["to"] not in nodes:
            raise DiagramError("Mermaid 边引用了未声明节点")
    return {"direction": direction, "nodes": nodes, "edges": edges}


def _node_label(match: re.Match[str]) -> str:
    raw = match.group("square") or match.group("round") or match.group("diamond") or ""
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        raw = raw[1:-1]
    raw = raw.replace("#quot;", '"').replace("#amp;", "&")
    return _clean_label(raw)


def _wrap(text: str, width: int = 18, max_lines: int = 2) -> List[str]:
    parts: List[str] = []
    current = ""
    for char in text:
        current += char
        if len(current) >= width:
            parts.append(current)
            current = ""
    if current:
        parts.append(current)
    if len(parts) > max_lines:
        parts = parts[:max_lines]
        parts[-1] = parts[-1][: max(1, width - 1)] + "…"
    return parts or [text]


def _layers(nodes: Mapping[str, str], edges: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    names = list(nodes)
    layer = {name: 0 for name in names}
    for _ in range(len(names) + 1):
        changed = False
        for edge in edges:
            src, dst = str(edge["from"]), str(edge["to"])
            if layer.get(dst, 0) < layer.get(src, 0) + 1 and layer.get(src, 0) + 1 < len(names):
                layer[dst] = layer[src] + 1
                changed = True
        if not changed:
            break
    # Cycles can leave everything at 0; spread them instead of collapsing.
    if len(set(layer.values())) == 1 and len(names) > 1:
        for index, name in enumerate(names):
            layer[name] = index
    return layer


def render_mermaid_svg(mermaid: str, title: str = "") -> str:
    graph = _parse_mermaid(mermaid)
    nodes: Dict[str, str] = graph["nodes"]
    edges: List[Dict[str, Any]] = graph["edges"]
    direction = graph["direction"]
    horizontal = direction in {"LR", "RL"}
    reverse = direction in {"RL", "BT"}

    layer = _layers(nodes, edges)
    buckets: Dict[int, List[str]] = {}
    for name in nodes:
        buckets.setdefault(layer[name], []).append(name)
    ordered_layers = sorted(buckets)
    if reverse:
        ordered_layers = list(reversed(ordered_layers))
    for bucket in buckets.values():
        bucket.sort()

    node_w, node_h = 196, 62
    gap_main, gap_cross = 86, 42
    margin, title_h = 40, 64
    max_cross = max(len(bucket) for bucket in buckets.values())

    positions: Dict[str, Tuple[float, float]] = {}
    for main_index, layer_index in enumerate(ordered_layers):
        bucket = buckets[layer_index]
        span = len(bucket) * node_h + (len(bucket) - 1) * gap_cross
        cross_max = max_cross * node_h + (max_cross - 1) * gap_cross
        start = margin + title_h + (cross_max - span) / 2
        for cross_index, name in enumerate(bucket):
            if horizontal:
                x = margin + main_index * (node_w + gap_main)
                y = start + cross_index * (node_h + gap_cross)
            else:
                x = start + cross_index * (node_w + gap_cross)
                y = margin + title_h + main_index * (node_h + gap_main)
            positions[name] = (x, y)

    if horizontal:
        width = margin * 2 + len(ordered_layers) * node_w + max(0, len(ordered_layers) - 1) * gap_main
        height = margin * 2 + title_h + max_cross * node_h + max(0, max_cross - 1) * gap_cross
    else:
        width = margin * 2 + max_cross * node_w + max(0, max_cross - 1) * gap_cross
        height = margin * 2 + title_h + len(ordered_layers) * node_h + max(0, len(ordered_layers) - 1) * gap_main
    width = max(int(width), 720)
    height = max(int(height), 420)

    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title or "architecture")}">',
        "<defs>",
        '<marker id="gugu-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">',
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#5b6472"/>',
        "</marker>",
        "</defs>",
        f'<rect width="{width}" height="{height}" fill="#f7f4ee"/>',
    ]
    if title:
        parts.append(
            f'<text x="{margin}" y="{margin + 24}" font-family="system-ui, sans-serif" font-size="24" '
            f'font-weight="600" fill="#23262b">{html.escape(title)}</text>'
        )
        parts.append(
            f'<text x="{margin}" y="{margin + 46}" font-family="system-ui, sans-serif" font-size="12" '
            f'fill="#7a828e">Mermaid source · deterministic SVG</text>'
        )
    parts.append(f'<desc>{html.escape(mermaid[:2000])}</desc>')

    for edge in edges:
        src, dst = str(edge["from"]), str(edge["to"])
        if src not in positions or dst not in positions:
            continue
        sx, sy = positions[src]
        dx, dy = positions[dst]
        if horizontal:
            x1, y1 = sx + node_w, sy + node_h / 2
            x2, y2 = dx, dy + node_h / 2
            if x2 < x1:
                x1, y1 = sx, sy + node_h / 2
                x2, y2 = dx + node_w, dy + node_h / 2
        else:
            x1, y1 = sx + node_w / 2, sy + node_h
            x2, y2 = dx + node_w / 2, dy
            if y2 < y1:
                x1, y1 = sx + node_w / 2, sy
                x2, y2 = dx + node_w / 2, dy + node_h
        if horizontal:
            control = (x1 + x2) / 2
            path = f"M {x1:.1f} {y1:.1f} C {control:.1f} {y1:.1f}, {control:.1f} {y2:.1f}, {x2:.1f} {y2:.1f}"
        else:
            control = (y1 + y2) / 2
            path = f"M {x1:.1f} {y1:.1f} C {x1:.1f} {control:.1f}, {x2:.1f} {control:.1f}, {x2:.1f} {y2:.1f}"
        parts.append(f'<path d="{path}" fill="none" stroke="#5b6472" stroke-width="1.6" marker-end="url(#gugu-arrow)"/>')
        if edge.get("label"):
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            parts.append(
                f'<text x="{mx:.1f}" y="{my - 6:.1f}" text-anchor="middle" font-family="system-ui, sans-serif" '
                f'font-size="11" fill="#7a828e">{html.escape(str(edge["label"]))}</text>'
            )

    for name, label in nodes.items():
        x, y = positions[name]
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{node_w}" height="{node_h}" rx="12" '
            f'fill="#ffffff" stroke="#c9c2b6" stroke-width="1.4"/>'
        )
        lines = _wrap(label)
        line_h = 17
        start_y = y + node_h / 2 - (len(lines) - 1) * line_h / 2 + 5
        for index, line in enumerate(lines):
            parts.append(
                f'<text x="{x + node_w / 2:.1f}" y="{start_y + index * line_h:.1f}" text-anchor="middle" '
                f'font-family="system-ui, sans-serif" font-size="14" fill="#23262b">{html.escape(line)}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def render_outline_svg(title: str, outline: Sequence[str]) -> str:
    """Simple, dependency-free SVG used when the Mermaid renderer fails."""
    items = [ _clean_label(item, 64) for item in outline if str(item).strip()][:12]
    if not items:
        raise DiagramError("大纲为空")
    margin, row_h, gap = 40, 46, 14
    width = 900
    height = margin * 2 + 64 + len(items) * row_h + max(0, len(items) - 1) * gap
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title or "outline")}">',
        f'<rect width="{width}" height="{height}" fill="#f7f4ee"/>',
        f'<text x="{margin}" y="{margin + 26}" font-family="system-ui, sans-serif" font-size="24" '
        f'font-weight="600" fill="#23262b">{html.escape(title or "Outline")}</text>',
        f'<text x="{margin}" y="{margin + 48}" font-family="system-ui, sans-serif" font-size="12" '
        f'fill="#7a828e">Structured outline fallback (Mermaid renderer unavailable)</text>',
    ]
    for index, item in enumerate(items):
        y = margin + 64 + index * (row_h + gap)
        parts.append(f'<rect x="{margin}" y="{y}" width="{width - margin * 2}" height="{row_h}" rx="10" fill="#ffffff" stroke="#c9c2b6"/>')
        parts.append(
            f'<text x="{margin + 18}" y="{y + row_h / 2 + 5}" font-family="system-ui, sans-serif" '
            f'font-size="15" fill="#23262b">{html.escape(item)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def render_text_outline(title: str, outline: Sequence[str]) -> str:
    lines = [f"# {title or 'Outline'}", "", "Mermaid 渲染失败，以下为结构化文本大纲：", ""]
    lines.extend(f"- {item}" for item in outline if str(item).strip())
    return "\n".join(lines).strip() + "\n"


def build_diagram(source: str, source_kind: str = "repo", style: str = "") -> Dict[str, Any]:
    """Return ``{title,mermaid,svg,outline,fallback}`` or raise ``DiagramError``."""
    built = source_to_mermaid(source, source_kind, style)
    mermaid = built["mermaid"]
    try:
        svg = render_mermaid_svg(mermaid, built["title"])
        fallback = None
    except DiagramError:
        svg = render_outline_svg(built["title"], built["outline"])
        fallback = "outline-svg"
    return {
        "title": built["title"],
        "mermaid": mermaid if fallback is None else "",
        "mermaid_attempt": mermaid,
        "svg": svg,
        "outline": built["outline"],
        "fallback": fallback,
        "source_kind": built["source_kind"],
        "style": style or "",
        "digest": hashlib.sha256(svg.encode("utf-8")).hexdigest()[:16],
    }
