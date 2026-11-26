"""PDFs number their pages ("Page X of Y") only when there's more than one."""
import io

import pypdf
import pytest

from club_minutes import pdf


@pytest.fixture(autouse=True)
def _keep_pillow_strict(monkeypatch):
    """Making a PDF imports weasyprint, which turns on Pillow's reading of cut-off pictures for the rest of the run."""
    from PIL import ImageFile
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", ImageFile.LOAD_TRUNCATED_IMAGES)


def pages(html):
    return [p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf._write(html))).pages]


def test_page_numbers_only_past_one_page():
    assert "Page 1 of" not in pages("<p>One short page.</p>")[0]
    long = pages("".join(f"<p>Line {i}</p>" for i in range(200)))
    n = len(long)
    assert n > 1 and f"Page 1 of {n}" in long[0] and f"Page {n} of {n}" in long[-1]


def test_minutes_from_before_the_update_are_never_numbered(tmp_path, monkeypatch):
    import datetime as dt

    from test_corrections import archive
    monkeypatch.setattr(pdf, "DATA", tmp_path)
    got = []
    monkeypatch.setattr(pdf, "_write", lambda html, numbers=True: got.append(numbers) or b"%PDF")
    a = archive(tmp_path / "m")
    (tmp_path / "pdf-pages-from").write_text("2026-10-16")  # the update came the day after these minutes
    pdf.render_pdf(a, a.by_slug["2026-10-15"], None)
    (tmp_path / "pdf-pages-from").write_text("2026-10-15")  # minutes of that day or later: numbered
    pdf.render_pdf(a, a.by_slug["2026-10-15"], None)
    (tmp_path / "pdf-pages-from").unlink()
    pdf.render_pdf(a, a.by_slug["2026-10-15"], None)
    assert got == [False, True, False] and pdf.numbers_from() == dt.date.today().isoformat()  # written on first use


def test_an_unnumbered_pdf_stays_unnumbered_past_one_page():
    long = pdf._write("".join(f"<p>Line {i}</p>" for i in range(200)), numbers=False)
    assert "Page 1 of" not in pypdf.PdfReader(io.BytesIO(long)).pages[0].extract_text()
