"""A diff as text to read a line at a time, for a screen reader.

A unified diff marks each line with "+", "-" or a space. At a screen reader's
lower punctuation levels those marks aren't spoken, and a space never is, so
this module writes the mark as a word or an audible symbol, always first on
the line, one line out for each line in. The person chooses the style
(TheIdeaPlace/AIChat#174): full words, short words, or diff symbols.

Nothing here imports wx or reads files, so any app can show the result in a
read-only text box. The Chat Place uses it for Changed Files; GHManage for a
commit's or pull request's changes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

#: "Added: x", "Removed: x", "Unchanged: x". The default.
WORDS = "words"
#: "Add x", "Del x", "Same x": shorter, and still spoken at every punctuation level.
SHORT = "short"
#: "+ x", "- x", "= x", like ``git diff``. "=" stands for git's space, which
#: is never spoken.
SYMBOLS = "symbols"
#: As SYMBOLS, but a removed line followed by its replacement reads "!- old",
#: then "!+ new": "!" as ``diff -c`` marks a changed line, then which side,
#: so either line can be told apart when arrowed to on its own.
CHANGED = "changed"

STYLES: Tuple[str, ...] = (WORDS, SHORT, SYMBOLS, CHANGED)
DEFAULT = WORDS

#: What a settings choice shows for each style.
LABELS: Dict[str, str] = {
    WORDS: "Words (Added, Removed, Unchanged)",
    SHORT: "Short words (Add, Del, Same)",
    SYMBOLS: "Diff symbols (+, -, =)",
    CHANGED: "Diff symbols with changed lines (!-, !+, +, -, =)",
}

#: To show beside the choice: symbols can be silent.
SYMBOLS_NOTE = ("Symbols are spoken only if your screen reader's punctuation level "
                "reads them.")

_MARKS: Dict[str, Dict[str, str]] = {
    WORDS: {"+": "Added:", "-": "Removed:", " ": "Unchanged:"},
    SHORT: {"+": "Add", "-": "Del", " ": "Same"},
    SYMBOLS: {"+": "+", "-": "-", " ": "="},
    CHANGED: {"+": "+", "-": "-", " ": "=", "!-": "!-", "!+": "!+"},
}

_HEADER = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    """One run of a diff's lines, each starting " ", "-", "+" or "\\"."""
    old_start: int  # 1-based; 0 when not known
    new_start: int
    lines: List[str]

    @property
    def added(self) -> int:
        return sum(1 for line in self.lines if line.startswith("+"))

    @property
    def removed(self) -> int:
        return sum(1 for line in self.lines if line.startswith("-"))


def parse_unified(text: str) -> List[Hunk]:
    """The hunks in unified-diff text: ``git diff`` output, a GitHub
    ``files[].patch``, or ``difflib.unified_diff`` lines joined by newlines.

    Anything outside a hunk (``diff --git``, ``index``, the ``---``/``+++``
    file names) is skipped. Each ``@@`` header's line counts say where a hunk
    ends, so a removed line that itself starts "--" is still a removed line.
    A hunk with no lines is dropped.

    Not handled: combined diffs ("@@@", from merge commits) and coloured
    output, so run git with ``--no-color``.
    """
    hunks: List[Hunk] = []
    old_left = new_left = 0
    after_line = False  # the last line read was one of a hunk's
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the newline ending the text doesn't start another line
    for line in lines:
        match = _HEADER.match(line)
        if match:
            old_left = int(match.group(2) or 1)
            new_left = int(match.group(4) or 1)
            hunks.append(Hunk(int(match.group(1)), int(match.group(3)), []))
            after_line = False
            continue
        if not hunks:
            continue
        if line.startswith("\\"):
            if after_line:  # "\ No newline at end of file", about that line
                hunks[-1].lines.append(line)
            continue
        after_line = False
        if old_left <= 0 and new_left <= 0:
            continue  # between hunks or files
        if line == "":
            line = " "  # some tools strip the space from a blank unchanged line
        mark = line[:1]
        if mark == "+":
            new_left -= 1
        elif mark == "-":
            old_left -= 1
        elif mark == " ":
            old_left -= 1
            new_left -= 1
        else:
            old_left = new_left = 0  # not diff text: the hunk was cut short
            continue
        hunks[-1].lines.append(line)
        after_line = True
    return [hunk for hunk in hunks if hunk.lines]


def where(hunk: Hunk) -> str:
    """"At line 12:", "Lines 12 to 20:" (in the new file), or "A change:"
    when where it is isn't known. A hunk that leaves nothing in the new file
    (git's "+0,0": a file deleted) says where its lines were in the old one."""
    kept = sum(1 for line in hunk.lines if line.startswith(" "))
    if hunk.new_start:
        start, count = hunk.new_start, hunk.added + kept
    elif hunk.old_start:
        start, count = hunk.old_start, hunk.removed + kept
    else:
        return "A change:"
    end = start + max(count - 1, 0)
    if end <= start:
        return f"At line {start}:"
    return f"Lines {start} to {end}:"


def render_lines(lines: Sequence[str], style: str = DEFAULT) -> List[str]:
    """One line of text for each diff line, its mark first. A blank line
    reads "blank line"; "\\ No newline at end of file" reads "Note: …"."""
    style = style if style in STYLES else DEFAULT
    marks = _MARKS[style]
    out: List[str] = []
    for mark, line in _marked(lines, style):
        text = line[1:]
        if mark == "\\":
            out.append(f"Note: {text.strip()}")
        else:
            out.append(f"{marks[mark]} {text if text.strip() else 'blank line'}")
    return out


def render(hunks: Sequence[Hunk], style: str = DEFAULT) -> List[str]:
    """Each hunk as a blank line, where it is, then its lines. The first
    hunk has the blank line too, to follow a heading line (as The Chat
    Place's path and counts); take ``[1:]`` when nothing comes before."""
    out: List[str] = []
    for hunk in hunks:
        out.append("")
        out.append(where(hunk))
        out.extend(render_lines(hunk.lines, style))
    return out


def _marked(lines: Sequence[str], style: str) -> List[Tuple[str, str]]:
    """Each line with its mark ("+", "-", " " or "\\"), in the order to read
    them.

    In the CHANGED style, a run of removed lines followed straight away by a
    run of added lines is paired in order, and each pair reads old then new,
    so the pairs are interleaved: "!- old 1", "!+ new 1", "!- old 2",
    "!+ new 2". That is out of the diff's order on purpose, so each change is
    heard whole. Lines left over when the runs differ in length keep "-" or
    "+". A "\\" note stays after its line.
    """
    marked = [(_mark(line), line) for line in lines]
    if style != CHANGED:
        return marked
    out: List[Tuple[str, str]] = []
    i = 0
    while i < len(marked):
        if marked[i][0] != "-":
            out.append(marked[i])
            i += 1
            continue
        removed, i = _run(marked, i, "-")
        added, i = _run(marked, i, "+")
        pairs = min(len(removed), len(added))
        for old, new in zip(removed[:pairs], added[:pairs]):
            out.extend(_remark(old, "!-"))
            out.extend(_remark(new, "!+"))
        for group in removed[pairs:] + added[pairs:]:
            out.extend(group)
    return out


def _run(marked: List[Tuple[str, str]], i: int,
         mark: str) -> Tuple[List[List[Tuple[str, str]]], int]:
    """The lines from ``i`` marked ``mark``, each with any notes after it."""
    groups: List[List[Tuple[str, str]]] = []
    while i < len(marked) and marked[i][0] == mark:
        group = [marked[i]]
        i += 1
        while i < len(marked) and marked[i][0] == "\\":
            group.append(marked[i])
            i += 1
        groups.append(group)
    return groups, i


def _remark(group: List[Tuple[str, str]], mark: str) -> List[Tuple[str, str]]:
    """The group with its first line (not its notes) marked ``mark``."""
    return [(mark, group[0][1])] + group[1:]


def _mark(line: str) -> str:
    mark = line[:1]
    return mark if mark in ("+", "-", "\\") else " "
