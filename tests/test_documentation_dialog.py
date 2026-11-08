"""The Docs viewer: contents list, search, and wrapped code blocks."""
from app.documentation_dialog import DocumentationDialog, find_all, headings

MD = """# Title

Intro with `inline` code.

## Alpha

Some text about gitleaks and more gitleaks.

### Beta

```bash
python packaging/make_icon.py && cd packaging && iconutil -c icns icon.iconset -o icon.icns && rm -rf x
```

| a | b |
|---|---|
| 1 | 2 |
"""


def test_contents_lists_headings_in_order(qapp):
    d = DocumentationDialog(None, MD, "now")
    assert [h[1] for h in headings(d.browser.document())] == ["Title", "Alpha", "Beta"]
    assert d.toc.count() == 3


def test_search_is_case_insensitive_and_steps_with_wraparound(qapp):
    d = DocumentationDialog(None, MD)
    d.search.setText("GITLEAKS")
    assert len(find_all(d.browser.document(), "gitleaks")) == 2
    assert d.count_label.text() == "1 of 2"
    d.next_match()
    assert d.count_label.text() == "2 of 2"
    d.next_match()
    assert d.count_label.text() == "1 of 2"
    d.prev_match()
    assert d.count_label.text() == "2 of 2"


def test_no_match_and_cleared_search(qapp):
    d = DocumentationDialog(None, MD)
    d.search.setText("zzzz")
    assert d.count_label.text() == "No matches"
    d.search.clear()
    assert d.count_label.text() == ""
    assert d.browser.extraSelections() == []


def test_code_blocks_wrap(qapp):
    d = DocumentationDialog(None, MD)
    b = d.browser.document().begin()
    while b.isValid():
        if "iconutil" in b.text():
            assert not b.blockFormat().nonBreakableLines()
            return
        b = b.next()
    raise AssertionError("code block not found")
