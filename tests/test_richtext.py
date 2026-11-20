"""Formatting in reports, business items and comments (richtext.py): only bold, italic, lists and links; typed HTML stays
text; links only to http(s) and mailto, always showing their address; a QR code only for an address marked [QR]; old
plain text reads the same."""
import datetime as dt

from club_minutes import richtext
from club_minutes.meeting import Item, Meeting


def test_formatting_and_nothing_else():
    assert str(richtext.render("**2 m** and *new*")) == "<strong>2 m</strong> and <em>new</em>"
    assert "<ul>" in richtext.render("- one\n- two") and "<ol>" in richtext.render("1. first\n2. second")
    for t in ("<script>alert(1)</script>", '<img src=x onerror="alert(1)">', "    indented old line", "# not a heading",
              "`code`", "![x](https://example.org/x.png)"):
        out = str(richtext.render(t))
        assert "<script" not in out and "<img" not in out and "<h1" not in out and "<code" not in out and "<pre" not in out, t
    assert str(richtext.render("<b>hi</b>")) == "&lt;b&gt;hi&lt;/b&gt;"  # HTML typed in shows as text
    assert str(richtext.render("Tom & Jerry 5 > 3")) == "Tom &amp; Jerry 5 &gt; 3"
    assert str(richtext.render("50/50 drawing - Rich $7.50")) == "50/50 drawing - Rich $7.50"  # old text unchanged


def test_only_an_address_marked_qr_gets_a_code_and_addresses_always_show():
    web = str(richtext.render("Plans: https://example.org/jpole [QR], [the form](https://example.org/form), see https://example.org/photos."))
    assert web.count("<svg") == 1 and 'class="qrcode" href="https://example.org/jpole"' in web and "[QR]" not in web
    assert web.count("https://example.org/jpole</a>") == 1  # the address shows once, in the text, not again by its code
    assert 'the form (<a href="https://example.org/form" rel="noopener nofollow" target="_blank">https://example.org/form</a>)' in web  # a word never hides an address
    assert "photos</a>." in web  # the full stop outside the link
    assert richtext.render("https://example.org/a [qr]").count("<svg") == 1  # any case
    assert richtext.render('[old](https://example.org/a "qr")').count("<svg") == 1  # the first version's mark still works


def test_the_pdf_shows_the_address_and_its_qr():
    pdf = str(richtext.render("Plans: https://example.org/jpole [QR], [the form](https://example.org/form)", pdf=True))
    assert "Plans: https://example.org/jpole" in pdf and "the form (https://example.org/form)" in pdf
    assert pdf.count("<svg") == 1 and "<a " not in pdf and pdf.count("https://example.org/jpole<") == 1


def test_unsafe_links_become_plain_words():
    bad = str(richtext.render('[click](javascript:alert(1) "qr") [mail](mailto:jane@example.org "qr")'))
    assert "javascript" not in bad and 'href="mailto:jane@example.org"' in bad and "<svg" not in bad  # no QR for mail


def test_plain_drops_the_marks_and_the_qr_mark():
    assert richtext.plain('**Bold** *it* https://example.org/f [QR] [x](https://example.org/x) snake_case') \
        == "Bold it https://example.org/f x (https://example.org/x) snake_case"
    assert richtext.plain('[https://example.org/a](https://example.org/a "qr")') == "https://example.org/a"


def test_an_item_with_a_list_in_it_stays_one_item():
    """An item's other lines are saved indented, so a report with a list reads back as one item; older files'
    unindented continuation lines still join with a space; typing can't forge a section, item or tag."""
    m = Meeting(date=dt.date(2026, 10, 15), sections={"New Business": [
        Item("Ideas:\n\n- a GOTA station\n- a food tent", "n0call"), Item("Next\n## Fake\n- forged <!-- member: x -->")]})
    back = Meeting.from_markdown(m.to_markdown())
    assert list(back.sections) == ["New Business"]
    assert [(i.text, i.member) for i in back.items("New Business")] == [
        ("Ideas:\n\n- a GOTA station\n- a food tent", "n0call"), ("Next\n## Fake\n- forged  member: x -->", None)]
    old = Meeting.from_markdown("---\ndate: 2026-10-15\n---\n## New Business\n- first line\nwrapped on\n- second\n")
    assert [i.text for i in old.items("New Business")] == ["first line wrapped on", "second"]


def test_an_items_first_line_is_its_title():
    from club_minutes.richtext import title
    assert title("Ask the **park**\n- pavilion\n- [form](https://example.org/f \"qr\")") == "Ask the park"
    assert title("") == ""



def test_a_too_long_qr_link_and_odd_text_dont_break_anything():
    from club_minutes.meeting import ITEM_MAX, item_text
    from club_minutes.richtext import render
    long = "https://example.org/" + "a" * 2900
    out = render(f'[x]({long} "qr")')
    assert "<svg" not in out and 'href="https://example.org/' in out  # a plain link, not a crash
    assert len(item_text("x" * 50000)) == ITEM_MAX and item_text("a\x01b\x02") == "ab"



def test_a_list_typed_right_under_a_line_is_a_list():
    from club_minutes.richtext import render
    out = render("Built a **new** antenna\n- mast up\n- feed line next\nPlans next week\nand after")
    assert "<p>Built a <strong>new</strong> antenna</p>" in out and out.count("<li>") == 2
    assert "Plans next week<br>" in out  # a single line break stays a break
    out = render("Fruit:\n1. Apple\n2. kiwi\n3.\n- \nDone")
    assert out.count("<li>") == 2 and "3." not in out and "<p>Done</p>" in out  # an empty item (Enter after the last) is dropped
