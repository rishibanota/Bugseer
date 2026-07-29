"""Duplicate-code detection via rolling hashes of normalized line windows.

Fast enough to run on every file in a large repository: each file is reduced to
a set of window fingerprints, and cross-file matches are resolved with a single
dictionary pass over the whole scan.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Iterable

_WS = re.compile(r"\s+")
_STRING = re.compile(r"""(['"])(?:\\.|(?!\1).)*\1""")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_COMMENT_PREFIXES = ("#", "//", "*", "/*", "--", "%")


def normalize_line(line: str) -> str:
    """Collapse literals and whitespace so cosmetic differences don't hide clones."""
    s = line.strip()
    if not s or s.startswith(_COMMENT_PREFIXES):
        return ""
    s = _STRING.sub("'S'", s)
    s = _NUMBER.sub("N", s)
    s = _WS.sub(" ", s)
    return s


def fingerprints(lines: Iterable[str], window: int = 6) -> dict[str, list[int]]:
    """Map each window hash to the 1-based line numbers where it starts."""
    normalized: list[tuple[int, str]] = []
    for idx, raw in enumerate(lines, start=1):
        norm = normalize_line(raw)
        if norm and len(norm) > 3:
            normalized.append((idx, norm))

    out: dict[str, list[int]] = defaultdict(list)
    if len(normalized) < window:
        return out
    for i in range(len(normalized) - window + 1):
        chunk = normalized[i : i + window]
        # Skip windows that are mostly boilerplate (imports, closing braces).
        joined = "\n".join(c[1] for c in chunk)
        if len(set(c[1] for c in chunk)) < max(2, window // 2):
            continue
        digest = hashlib.blake2b(joined.encode("utf-8"), digest_size=12).hexdigest()
        out[digest].append(chunk[0][0])
    return out


def internal_duplication(lines: list[str], window: int = 6) -> tuple[int, float]:
    """Return (duplicate block count, duplicated line ratio) inside one file."""
    fps = fingerprints(lines, window)
    dup_blocks = 0
    dup_lines: set[int] = set()
    for _digest, positions in fps.items():
        if len(positions) > 1:
            dup_blocks += len(positions) - 1
            for start in positions:
                dup_lines.update(range(start, start + window))
    total = max(1, len([line for line in lines if normalize_line(line)]))
    return dup_blocks, min(1.0, len(dup_lines) / total)


class CrossFileDuplication:
    """Accumulates fingerprints across files, then reports shared clones."""

    def __init__(self, window: int = 6) -> None:
        self.window = window
        self._index: dict[str, list[tuple[str, int]]] = defaultdict(list)

    def add(self, path: str, fps: dict[str, list[int]]) -> None:
        for digest, positions in fps.items():
            for pos in positions:
                self._index[digest].append((path, pos))

    def clones(self, min_files: int = 2) -> dict[str, list[dict[str, object]]]:
        """path -> list of {partner, lines, partner_lines} clone records."""
        result: dict[str, list[dict[str, object]]] = defaultdict(list)
        pair_hits: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)

        for _digest, occurrences in self._index.items():
            files = {p for p, _ in occurrences}
            if len(files) < min_files:
                continue
            # Cap the fan-out of extremely common boilerplate windows.
            if len(occurrences) > 40:
                continue
            for i, (path_a, line_a) in enumerate(occurrences):
                for path_b, line_b in occurrences[i + 1 :]:
                    if path_a == path_b:
                        continue
                    key = (path_a, path_b) if path_a < path_b else (path_b, path_a)
                    val = (line_a, line_b) if path_a < path_b else (line_b, line_a)
                    pair_hits[key].append(val)

        for (path_a, path_b), positions in pair_hits.items():
            if len(positions) < 1:
                continue
            lines_a = sorted({p[0] for p in positions})
            lines_b = sorted({p[1] for p in positions})
            record_a = {
                "partner": path_b,
                "shared_windows": len(positions),
                "lines": lines_a[:10],
                "partner_lines": lines_b[:10],
            }
            record_b = {
                "partner": path_a,
                "shared_windows": len(positions),
                "lines": lines_b[:10],
                "partner_lines": lines_a[:10],
            }
            result[path_a].append(record_a)
            result[path_b].append(record_b)

        for path in result:
            result[path].sort(key=lambda r: r["shared_windows"], reverse=True)  # type: ignore[arg-type,return-value]
            result[path] = result[path][:5]
        return dict(result)
