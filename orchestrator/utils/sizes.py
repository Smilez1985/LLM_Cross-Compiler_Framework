#!/usr/bin/env python3
"""
One size formatter for every window and the command line (wave 6, f-words): decimal units (kB, MB, GB, TB: 1 kB is
1000 bytes, as disks and downloads count), the language's decimal separator ("1.5 GB", "1,5 GB") and the word for
bytes from the language tables ("words.size.bytes"). The command line passes lang="en".

    format_size(1_500_000_000, "de")            -> "1,5 GB"
    format_size(532, "en")                      -> "532 bytes"
    format_size(2_048_000, "en", exact=True)    -> "2.0 MB (2,048,000 bytes)"
"""

from typing import Optional

from orchestrator.utils.localization import current_language, ui_text

UNITS = ((10 ** 12, "TB", 1), (10 ** 9, "GB", 1), (10 ** 6, "MB", 1), (10 ** 3, "kB", 0))


def _number(value: float, decimals: int, lang: str) -> str:
    text = f"{value:,.{decimals}f}"
    if lang == "de":   # 1,234.5 -> 1.234,5
        text = text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return text


def format_size(n: int, lang: Optional[str] = None, *, exact: bool = False) -> str:
    """`n` bytes for the person in `lang` (default: the UI language); with `exact`, the exact number of bytes follows
    in brackets. A negative or missing size counts as 0."""
    lang = lang or current_language()
    n = max(0, int(n or 0))
    shown = ""
    for i, (size, unit, decimals) in enumerate(UNITS):
        if n >= size:
            value = round(n / size, decimals)
            if i > 0 and value >= 1000:   # 999,999 bytes is "1.0 MB", not "1000 kB"
                size, unit, decimals = UNITS[i - 1]
                value = round(n / size, decimals)
            shown = f"{_number(value, decimals, lang)} {unit}"
            break
    if not shown:
        return ui_text("words.size.bytes", lang, n=_number(n, 0, lang))
    if exact:
        return ui_text("words.size.exact", lang, size=shown, n=_number(n, 0, lang))
    return shown


__all__ = ["UNITS", "format_size"]
