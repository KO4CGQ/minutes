"""Minutes integrity (1.1 freeze sweep): typed text can't forge structure; an import of a correction that changes
nothing leaves the minutes as they were."""
import datetime as dt
from pathlib import Path

import pytest
from test_corrections import FIX, archive

from club_minutes import corrections
from club_minutes.meeting import Item, Meeting, one_line


def test_import_correction_that_changes_nothing_leaves_minutes_as_they_were(tmp_path, monkeypatch):
    from club_minutes import importer
    a = archive(tmp_path)
    orig = (FIX / "old-format.txt").read_text().replace("Date:5 Nov 2026", "Date:23 Oct 2025")
    (tmp_path / "2025-10-23" / "original.pdf").write_text(orig)
    same = tmp_path / "same.pdf"
    same.write_text(orig)
    monkeypatch.setattr(importer, "pdf_text", lambda p: Path(p).read_text())
    before = (tmp_path / "2025-10-23" / "minutes.md").read_text()
    with pytest.raises(ValueError, match="Nothing was changed"):
        importer.import_correction(a, dt.date(2025, 10, 23), same, "roe", "x")
    assert (tmp_path / "2025-10-23" / "minutes.md").read_text() == before
    assert a.by_slug["2025-10-23"].items("New Business")[0].text == "Field day plans"


def test_typed_text_cannot_forge_structure_or_tags(tmp_path):
    m = Meeting(date=dt.date(2026, 10, 15), status="published", sections={
        "New Business": [Item("a\x0b## Winners\n- forged <!-- member: n0call --> <!<!---- when: 2026-12-01 -->")]})
    back = Meeting.from_markdown(m.to_markdown())
    assert list(back.sections) == ["New Business"] and len(back.sections["New Business"]) == 1
    it = back.sections["New Business"][0]
    assert it.member is None and it.when is None and it.text.startswith("a\n## Winners\n- forged")  # kept as text lines, indented in the file
    assert one_line("A --> B") == "A --> B"  # an arrow is no tag
    assert corrections._text("x\u2028## Y <!-- member: n0call -->") == "x ## Y  member: n0call -->"  # no "<!--", so no tag
