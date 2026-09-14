"""Parser for the CMS HCPCS Level II quarterly alpha-numeric file.

The real file is a fixed-width CMS RIF text file (320-byte records, though
trailing filler is trimmed on disk). Byte offsets used here (1-indexed, per
CMS's own ``HCPC<year>_recordlayout.txt``):

======================================  =====  ===  ===
field                                   len    beg  end
======================================  =====  ===  ===
HCPCS code                              5      1    5
HCPCS Record Identification Code (RIC)  1      11   11
HCPCS Short Description                 28     92   119
HCPCS Termination Date (YYYYMMDD)       8      285  292
======================================  =====  ===  ===

RIC 3 = first line of a procedure record with detail fields populated
(2, 4, 7, 8 are continuation/modifier records with no useful data here).
Only HCPCS **Level II** codes (a letter followed by four digits) are
kept: CPT Level I codes and their descriptors are AMA-copyrighted and
must never be stored, per engine policy.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

_LEVEL2_CODE = re.compile(r"^[A-Z]\d{4}$")

_CODE = slice(0, 5)
_RIC = slice(10, 11)
_SHORT_DESC = slice(91, 119)
_TERMINATION = slice(284, 292)


def parse_hcpcs2_file(path: Path) -> Iterator[tuple[str, str]]:
    """Yield (code, short_desc) for active, un-terminated HCPCS Level II codes."""
    with path.open(encoding="latin-1") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if len(line) < 11 or line[_RIC] != "3":
                continue
            code = line[_CODE].strip()
            if not _LEVEL2_CODE.match(code):
                continue
            if len(line) < 292:
                continue
            if line[_TERMINATION].strip():
                continue
            short_desc = line[_SHORT_DESC].strip()
            if not short_desc:
                continue
            yield code, short_desc
