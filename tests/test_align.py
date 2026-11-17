"""The aligner on its own: what counts as a markdown word, and how words find their source."""

from markdownify.align import (align_word_spans, align_words, markdown_words, normalize, word_ranges_of)


def texts(markdown):
    return [word.text for word in markdown_words(markdown)]


def test_heading_bullets_and_emphasis_are_not_words():
    markdown = "# Rated torque\n\n- **20** Nm\n1. peak _60_ Nm\n> quoted line\n"
    assert texts(markdown) == ["Rated", "torque", "20", "Nm", "peak", "60", "Nm", "quoted", "line"]


def test_word_ranges_cover_only_content():
    markdown = "## Title here"
    assert [markdown[start:end] for start, end in word_ranges_of(markdown)] == ["Title", "here"]


def test_gfm_table_pipes_and_separator_row_are_syntax():
    markdown = "| Model | Torque |\n|---|:---:|\n| AK80-9 | 9 Nm |\n"
    assert texts(markdown) == ["Model", "Torque", "AK80-9", "9", "Nm"]


def test_pipes_without_spaces_still_split_cells():
    assert texts("|a|b|\n|-|-|\n|1|2|") == ["a", "b", "1", "2"]


def test_escaped_characters_are_content_and_the_backslash_is_not():
    markdown = r"| a\|b | 5\*3 | snake_case |"
    words = markdown_words(markdown)
    assert [word.text for word in words] == ["a|b", "5*3", "snake_case"]
    # The range spans the escape, so the backslash is inside a word but not in its text.
    assert markdown[words[0].start:words[0].end] == r"a\|b"


def test_link_urls_and_images_are_not_words():
    markdown = "See [the datasheet](https://example.com/a.pdf) and ![plot](p.png) now"
    assert texts(markdown) == ["See", "the", "datasheet", "and", "now"]


def test_raw_html_table_tags_split_words_and_entities_decode():
    markdown = "<table><tr><th colspan=\"2\">Rated&amp;Peak</th></tr><tr><td>20</td><td>60</td></tr></table>"
    assert texts(markdown) == ["Rated&Peak", "20", "60"]


def test_code_fence_lines_are_syntax_and_code_is_content():
    assert texts("```python\nx = a*b\n```\n") == ["x", "=", "a*b"]


def test_normalize_expands_ligatures_and_drops_soft_hyphens():
    assert normalize("eﬃcient") == "efficient"
    assert normalize("tor­que") == "torque"


def test_exact_words_align_with_score_one():
    markdown = "Rated torque 20 Nm"
    result = align_words(markdown, word_ranges_of(markdown), ["Rated", "torque", "20", "Nm"])
    assert result == [(0, 1.0), (1, 1.0), (2, 1.0), (3, 1.0)]


def test_inserted_word_is_unlinked_and_missing_word_skipped():
    markdown = "Rated invented torque Nm"
    result = align_words(markdown, word_ranges_of(markdown), ["Rated", "torque", "20", "Nm"])
    assert result[0] == (0, 1.0)
    assert result[1][0] is None
    assert result[2] == (1, 1.0)
    assert result[3] == (3, 1.0)


def test_split_word_finds_both_halves_of_its_source():
    alignments = align_word_spans(["10", "A", "max"], ["10A", "max"])
    assert alignments[0].sources == [(0, 0, 2)]
    assert alignments[1].sources == [(0, 2, 3)]
    assert alignments[2].sources == [(1, 0, 3)] and alignments[2].score == 1.0


def test_joined_words_point_at_every_source_word():
    alignment = align_word_spans(["N·m"], ["N", "·", "m"])[0]
    assert [source for source, _start, _end in alignment.sources] == [0, 1, 2]
    assert alignment.score == 1.0


def test_changed_word_is_linked_below_one():
    alignment = align_word_spans(["torqve"], ["torque"])[0]
    assert alignment.index == 0 and 0.5 <= alignment.score < 1.0


def test_unrelated_word_is_left_unlinked():
    assert align_word_spans(["xyz"], ["torque"])[0].index is None


def test_ligature_in_source_aligns_exactly_and_maps_back_to_the_original_characters():
    alignment = align_word_spans(["efficient"], ["eﬃcient"])[0]
    assert alignment.score == 1.0 and alignment.sources == [(0, 0, 7)]


def test_blocks_keep_reordered_columns_apart():
    # Two columns: the markdown reads left then right; the source text layer interleaves them line by line.
    markdown = "alpha beta gamma\n\none two three"
    source = ["alpha", "one", "beta", "two", "gamma", "three"]
    ranges = word_ranges_of(markdown)
    blocks = [(0, 16, [0, 2, 4]), (18, len(markdown), [1, 3, 5])]
    result = align_words(markdown, ranges, source, block_spans=blocks)
    assert [index for index, _score in result] == [0, 2, 4, 1, 3, 5]
    assert all(score == 1.0 for _index, score in result)


def test_word_missed_by_its_block_is_found_in_the_second_pass():
    markdown = "alpha beta"
    result = align_words(markdown, word_ranges_of(markdown), ["alpha", "beta"], block_spans=[(0, 10, [0])])
    assert result == [(0, 1.0), (1, 1.0)]


def test_a_source_word_is_claimed_once():
    alignments = align_word_spans(["Nm", "Nm"], ["Nm"], groups=[([0], [0]), ([1], [0])])
    assert alignments[0].index == 0 and alignments[1].index is None


def test_escaped_ampersand_keeps_entity_text_literal():
    assert texts(r"Use \&amp; here") == ["Use", "&amp;", "here"]
    assert texts("Use &amp; here") == ["Use", "&", "here"]


def test_glued_source_word_is_shared_between_groups():
    # pdfplumber glued two table cells into one word; each cell's group takes its own half.
    alignments = align_word_spans(["LRS-350-4.2", "LRS-350-5"], ["LRS-350-4.2LRS-350-5"],
                                  groups=[([0], [0]), ([1], [0])])
    assert alignments[0].sources == [(0, 0, 11)] and alignments[0].score == 1.0
    assert alignments[1].sources == [(0, 11, 20)] and alignments[1].score == 1.0


# --- the HTML lane's findings (2026-10-29) ----------------------------------------------------------------------
def test_letters_stitched_from_unrelated_source_words_score_below_the_floor():
    # "M" from "Maximum" and "ode" from "Model" must not make an exact "Mode".
    alignment = align_word_spans(["Mode"], ["Maximum", "Model"])[0]
    assert alignment.score < 0.9
    assert len({source for source, _start, _end in alignment.sources}) <= 1


def test_adjacent_source_words_joined_at_their_boundary_still_score_one():
    alignment = align_word_spans(["torque2"], ["torque", "2"])[0]
    assert alignment.score == 1.0 and [source for source, _s, _e in alignment.sources] == [0, 1]


def test_heading_after_a_list_bullet_is_syntax():
    assert texts("- ## Specs\n") == ["Specs"]


def test_callout_marker_is_syntax():
    assert texts("> [!success] Rated torque\n") == ["Rated", "torque"]
    assert texts("> [!NOTE]-\n> body\n") == ["body"]


def test_brackets_of_a_multi_line_link_are_syntax():
    assert texts("See [the full\ndatasheet](https://example.com/a.pdf) now") == ["See", "the", "full", "datasheet", "now"]


def test_footnote_markers_are_syntax():
    assert texts("Peak torque[^1] is 60 Nm.\n\n[^1]: Measured at 25 °C.\n") == \
        ["Peak", "torque", "is", "60", "Nm.", "Measured", "at", "25", "°C."]


def test_a_word_split_over_two_baselines_joins_whole_source_words_out_of_order():
    # A subscript: the text layer reads "V", "rises", "IN" (the lowered "IN" sorts after the line); the markdown
    # reads "VIN". Whole words joined at their boundaries are the source's text, wherever they sort.
    alignment = align_word_spans(["VIN", "rises"], ["V", "rises", "IN"])[0]
    assert alignment.score == 1.0 and sorted(source for source, _s, _e in alignment.sources) == [0, 2]
