"""Yield candidate prose lines while preserving physical manuscript line numbers."""

from pathlib import Path

from check_deterministic import FENCE


def screenable_lines(path, content):
    suffix = Path(path).suffix.lower()
    if suffix == ".bib":
        return
    lines = content.splitlines()
    if suffix != ".md":
        for index, line in enumerate(lines, start=1):
            yield index, line, "prose"
        return

    has_opening = bool(lines and lines[0].strip() == "---")
    has_closing = any(line.strip() in ("---", "...") for line in lines[1:]) if has_opening else False
    metadata = has_opening and has_closing
    ambiguous_metadata = has_opening and not has_closing
    fence_char = None
    fence_size = 0
    comment = False
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if ambiguous_metadata and index == 1:
            continue
        if metadata:
            if index > 1 and stripped in ("---", "..."):
                metadata = False
            continue
        if fence_char:
            if stripped and set(stripped) == {fence_char} and len(stripped) >= fence_size:
                fence_char = None
            continue
        fence = FENCE.match(line)
        if fence:
            fence_char = fence.group(1)[0]
            fence_size = len(fence.group(1))
            continue
        if comment:
            if "-->" in line:
                comment = False
            continue
        if stripped.startswith("<!--"):
            comment = "-->" not in line
            continue
        context = ("ambiguous_frontmatter" if ambiguous_metadata else
                   "ambiguous_indentation" if line.startswith(("    ", "\t")) else "prose")
        yield index, line, context
