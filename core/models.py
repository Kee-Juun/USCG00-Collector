from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class DecisionRow:
    docket: str
    pdf_url: str
    visible_date: Optional[date] = None
    allegations: str = ""
    findings: str = ""
    order_type: str = ""


@dataclass(frozen=True)
class PdfAnalysis:
    docket: str
    decision_date: date
    text: str
    text_source: str
    page_count: int
    content_hash: str
    byte_hash: str


@dataclass
class CollectionResult:
    docket: str
    status: str
    message: str
    source_url: str = ""
    output_file: str = ""
    decision_date: str = ""
    text_source: str = ""
    final_filename: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SFViewEntry:
    label: str
    docket: str
    decision_date: Optional[date]
    path: Optional[Path] = None
