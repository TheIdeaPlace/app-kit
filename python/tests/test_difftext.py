"""difftext: a diff as text to read by line, in each marking style."""
import difflib

import pytest

from theideaplace_appkit import __version__, difftext
from theideaplace_appkit.difftext import (
    CHANGED, DEFAULT, LABELS, SHORT, STYLES, SYMBOLS, WORDS, Hunk, parse_unified, render,
    render_lines, where)

LINES = [" keep", "-old line", "+new line", "+another", " ", " end"]


def test_words_is_the_default_and_reads_as_before():
    assert DEFAULT == WORDS
    assert render_lines(LINES) == [
        "Unchanged: keep", "Removed: old line", "Added: new line", "Added: another",
        "Unchanged: blank line", "Unchanged: end"]


def test_short_words():
    assert render_lines(LINES, SHORT) == [
        "Same keep", "Del old line", "Add new line", "Add another", "Same blank line",
        "Same end"]


def test_symbols_give_unchanged_lines_an_audible_mark():
    assert render_lines(LINES, SYMBOLS) == [
        "= keep", "- old line", "+ new line", "+ another", "= blank line", "= end"]


def test_changed_pairs_a_removed_line_with_its_replacement():
    # One removed, two added: the first added line replaces it, the second is new.
    assert render_lines(LINES, CHANGED) == [
        "= keep", "!- old line", "!+ new line", "+ another", "= blank line", "= end"]


def test_changed_interleaves_pairs_old_then_new():
    lines = ["-a1", "-a2", "-a3", "+b1", "+b2"]
    assert render_lines(lines, CHANGED) == ["!- a1", "!+ b1", "!- a2", "!+ b2", "- a3"]


def test_changed_keeps_plus_and_minus_without_a_partner():
    assert render_lines(["+x", " y", "-z"], CHANGED) == ["+ x", "= y", "- z"]
    # Added then removed isn't a replacement in diff order.
    assert render_lines(["+x", "-z"], CHANGED) == ["+ x", "- z"]


def test_every_style_gives_one_line_out_per_line_in():
    lines = ["-a", "-b", "\\ No newline at end of file", "+c", " d", "-e", "+f", "+g"]
    for style in STYLES:
        assert len(render_lines(lines, style)) == len(lines)


def test_a_no_newline_note_stays_after_its_line():
    lines = [" x", "-last", "\\ No newline at end of file", "+last!",
             "\\ No newline at end of file"]
    assert render_lines(lines) == [
        "Unchanged: x", "Removed: last", "Note: No newline at end of file", "Added: last!",
        "Note: No newline at end of file"]
    assert render_lines(lines, CHANGED) == [
        "= x", "!- last", "Note: No newline at end of file", "!+ last!",
        "Note: No newline at end of file"]


def test_blank_and_whitespace_only_lines_say_so():
    assert render_lines(["+", "-   ", "+\t"], SYMBOLS) == [
        "+ blank line", "- blank line", "+ blank line"]


def test_indentation_is_kept_after_the_mark():
    assert render_lines(["+    return x"]) == ["Added:     return x"]


def test_an_unknown_style_falls_back_to_words():
    assert render_lines(["+x"], "nonsense") == ["Added: x"]
    assert render_lines(["+x"], "") == ["Added: x"]


def test_every_style_has_a_label():
    assert set(LABELS) == set(STYLES)


def test_where():
    assert where(Hunk(0, 0, ["+x"])) == "A change:"
    assert where(Hunk(5, 5, ["-gone"])) == "At line 5:"
    assert where(Hunk(10, 10, LINES)) == "Lines 10 to 14:"


def test_render_gives_each_hunk_a_header():
    hunks = [Hunk(1, 1, ["+a"]), Hunk(9, 10, [" b", "-c"])]
    assert render(hunks, SYMBOLS) == ["", "At line 1:", "+ a", "", "At line 10:", "= b", "- c"]


def test_a_new_file_is_all_added():
    hunks = parse_unified("@@ -0,0 +1,3 @@\n+one\n+two\n+three")
    assert render(hunks, SHORT) == ["", "Lines 1 to 3:", "Add one", "Add two", "Add three"]


# -- parse_unified ---------------------------------------------------------------

GIT_DIFF = """diff --git a/x.py b/x.py
index 1111111..2222222 100644
--- a/x.py
+++ b/x.py
@@ -1,3 +1,3 @@ def top():
 a
-b
+B
 c
@@ -10 +10,2 @@
--- a SQL comment, removed
+-- its replacement
+one more
diff --git a/y.txt b/y.txt
--- a/y.txt
+++ b/y.txt
@@ -1 +1 @@
-old
\\ No newline at end of file
+new
\\ No newline at end of file
"""


def test_parses_git_diff_output_with_several_files_and_hunks():
    hunks = parse_unified(GIT_DIFF)
    assert [(h.old_start, h.new_start) for h in hunks] == [(1, 1), (10, 10), (1, 1)]
    assert hunks[0].lines == [" a", "-b", "+B", " c"]
    # "--- a SQL comment" is a removed line, not a file header: the counts say so.
    assert hunks[1].lines == ["--- a SQL comment, removed", "+-- its replacement", "+one more"]
    assert hunks[2].lines == ["-old", "\\ No newline at end of file", "+new",
                              "\\ No newline at end of file"]
    assert (hunks[1].added, hunks[1].removed) == (2, 1)


def test_parses_a_github_patch_with_crlf_and_a_blank_unchanged_line():
    patch = "@@ -3,4 +3,4 @@\r\n x\r\n\r\n-y\r\n+z\r\n w"
    hunks = parse_unified(patch)
    assert hunks[0].lines == [" x", " ", "-y", "+z", " w"]


def test_parses_difflib_output():
    text = "\n".join(difflib.unified_diff(["a", "b", "c"], ["a", "B", "c"], lineterm=""))
    hunks = parse_unified(text)
    assert [h.lines for h in hunks] == [[" a", "-b", "+B", " c"]]


def test_nothing_to_parse():
    assert parse_unified("") == []
    assert parse_unified("Binary files a/x.png and b/x.png differ") == []


def test_a_hunk_cut_short_stops_at_text_that_isnt_diff():
    hunks = parse_unified("@@ -1,5 +1,5 @@\n a\n-b\nnot a diff line\n+c")
    assert hunks[0].lines == [" a", "-b"]


def test_a_hunk_cut_short_gains_no_blank_line_from_the_last_newline():
    assert parse_unified("@@ -1,5 +1,5 @@\n a\n-b\n")[0].lines == [" a", "-b"]
    # A trailing blank unchanged line that the counts call for is still kept.
    assert parse_unified("@@ -1,2 +1,2 @@\n a\n\n")[0].lines == [" a", " "]


def test_a_hunk_with_no_lines_is_dropped_and_a_stray_note_ignored():
    assert parse_unified("@@ -1,2 +1,2 @@\nxyz") == []
    hunks = parse_unified("@@ -1,3 +1,3 @@\n a\nnot diff\n\\ No newline at end of file")
    assert hunks[0].lines == [" a"]


def test_a_deleted_file_says_where_its_lines_were():
    hunks = parse_unified("@@ -1,3 +0,0 @@\n-a\n-b\n-c")
    assert (hunks[0].old_start, hunks[0].new_start) == (1, 0)
    assert where(hunks[0]) == "Lines 1 to 3:"
    assert where(parse_unified("@@ -4 +0,0 @@\n-x")[0]) == "At line 4:"
    # Lines put in after line 7 of the old file, which was empty there.
    assert where(parse_unified("@@ -7,0 +8,2 @@\n+p\n+q")[0]) == "Lines 8 to 9:"


def test_changed_old_and_new_read_apart():
    # Two one-line replacements, or one of two lines: either way a became b
    # and c became d, and each line says which side it's on.
    assert render_lines(["-a", "+b", "-c", "+d"], CHANGED) == ["!- a", "!+ b", "!- c", "!+ d"]
    assert render_lines(["-a", "-c", "+b", "+d"], CHANGED) == ["!- a", "!+ b", "!- c", "!+ d"]
    for line in render_lines(["-a", "+b"], CHANGED):
        assert line.startswith(("!- ", "!+ "))


def test_version_is_set():
    assert __version__.count(".") == 2


@pytest.mark.parametrize("style", STYLES)
def test_marks_come_first(style):
    for line in render_lines(["+x", "-y", " z"], style):
        mark, _, text = line.partition(" ")
        assert mark and text in ("x", "y", "z")
