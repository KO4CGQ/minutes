"""Ohio: regular election days (Ohio Revised Code 3501.01; there's no official feed). The general election every
November, the first Tuesday after the first Monday; the primary on that Tuesday in May, or the 3rd Tuesday of March
in a presidential year. August special elections are left out: held only when something is put on that ballot.
shortcut: rules as of 2026; if the law changes, change them here."""
import datetime as dt

from . import nth_weekday
from .us import HOLIDAYS  # noqa: F401  (a state has its country's holidays too, if a club picks it for those)

NOTES = "Polls are open 6:30 AM to 7:30 PM. Registration closes 30 days before. Your ballot and polling place: voteohio.gov"


def elections(lo: dt.date, hi: dt.date) -> list[tuple[dt.date, str, str]]:
    def after_first_monday(year, month):
        return nth_weekday(year, month, 0, 1) + dt.timedelta(days=1)
    out = []
    for year in range(lo.year, hi.year + 1):
        primary = nth_weekday(year, 3, 1, 3) if year % 4 == 0 else after_first_monday(year, 5)
        for d, name in ((primary, "Ohio primary election"), (after_first_monday(year, 11), "Election Day (Ohio general election)")):
            if lo <= d <= hi:
                out.append((d, name, NOTES))
    return out
