"""Parser for NCCI Procedure-to-Procedure (PTP) edit files.

The real practitioner and hospital/OPPS PTP releases are both tab-delimited,
CRLF-terminated text files with a 6-line preamble before data rows begin::

    CPT only copyright 2025 American Medical Association.  All rights reserved.
    Column1/Column2 Edits
    Column 1<TAB>Column 2<TAB>*=in existence<TAB>Effective<TAB>Deletion<TAB>Modifier<TAB>PTP Edit Rationale
    <TAB><TAB>prior to 1996<TAB>Date<TAB>Date<TAB>0=not allowed<TAB>
    <TAB><TAB><TAB><TAB>*=no data<TAB>1=allowed<TAB>
    <TAB><TAB><TAB><TAB><TAB>9=not applicable<TAB>

A deletion date of ``*`` means "no data" -- the edit is still active.
Each quarterly release is split into several same-format file parts
(f1, f2, ...); this module parses one file at a time and callers chain
the parts together.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path

from countercharge_engine.refdata.base import PtpEdit

_HEADER_LINES = 6


def _parse_yyyymmdd(raw: str) -> date:
    return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))


def parse_ptp_file(path: Path) -> Iterator[PtpEdit]:
    """Yield every edit row in one PTP practitioner or hospital text file.

    The returned :class:`PtpEdit` carries no notion of which CMS table
    (practitioner vs. OPPS) it came from -- callers know that from which
    file/:class:`~countercharge_engine.refdata.build.sources.Source` they
    parsed and are responsible for tagging rows with the right ``tbl``
    when inserting into sqlite.
    """
    with path.open(encoding="utf-8") as f:
        for _ in range(_HEADER_LINES):
            if f.readline() == "":
                return
        for line in f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            fields = line.split("\t")
            if len(fields) < 7:
                continue
            col1, col2, _in_existence, effective, deleted, modifier_ind, rationale = fields[:7]
            col1 = col1.strip()
            col2 = col2.strip()
            if not col1 or not col2:
                continue
            deleted = deleted.strip()
            yield PtpEdit(
                col1=col1,
                col2=col2,
                modifier_ind=int(modifier_ind.strip()),
                effective=_parse_yyyymmdd(effective.strip()),
                deleted=None if deleted == "*" else _parse_yyyymmdd(deleted),
                rationale=rationale.strip(),
            )


def parse_ptp_files(paths: Iterable[Path]) -> Iterator[PtpEdit]:
    """Yield PTP edits across every part of a (possibly multi-file) release."""
    for path in paths:
        yield from parse_ptp_file(path)


def keep_ptp_edit(edit: PtpEdit, cutoff: date) -> bool:
    """Whether ``edit`` is worth keeping under the builder's deletion cutoff.

    An edit still active (``deleted is None``) is always kept. A deleted
    edit is kept only if it was deleted on or after ``cutoff`` -- old enough
    that it could still govern a date of service after the cutoff. See
    ``engine/data/REFDATA.md`` for why this cutoff exists and what it costs.
    """
    return edit.deleted is None or edit.deleted >= cutoff
