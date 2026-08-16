from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

import fitz
from pypdf import PdfReader

from core.models import PdfAnalysis
from utils.sfview_checker import DOCKET_RE, normalize_docket


MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    ) if name
}
MONTH_PATTERN = "|".join(MONTHS)
DATE_PATTERNS = [
    re.compile(rf"\b({MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s+(20\d{{2}})\b", re.I),
    re.compile(rf"\b(\d{{1,2}})\s*(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?({MONTH_PATTERN})\s*,?\s+(20\d{{2}})\b", re.I),
    re.compile(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](20\d{2})(?!\d)"),
]
DATE_CONTEXT_RE = re.compile(r"\b(?:dated|date|done|ordered|issued|entered|signed|day of)\b", re.I)
ORDER_HEADING_RE = re.compile(
    r"^\s*(?:(?:amended|corrected|final|initial|interim|supplemental)\s+)?"
    r"(?:decision(?:\s+and\s+(?:final\s+)?order)?|decision\s+of\s+the\s+commandant|"
    r"(?:consent|admission|default|withdrawal|dismissal|final|initial|interim|supplemental)?\s*order)\b",
    re.I | re.M,
)
OPERATIVE_ORDER_RE = re.compile(
    r"\b(?:it\s+is\s+(?:hereby\s+)?ordered|it\s+is\s+further\s+ordered|"
    r"upon\s+consideration.+?ordered|wherefore\s*,?\s*order|decision\s+of\s+the\s+commandant)\b",
    re.I | re.S,
)
ANNEX_MANUAL_RE = re.compile(
    r"\bannex\b.*\bmanual\s+for\s+courts[- ]martial\b|"
    r"\bmanual\s+for\s+courts[- ]martial\b.*\b(?:annex|amendment|correction)\b|"
    r"\bcorrection\s+documents?\s+for\s+manuals?\b",
    re.I | re.S,
)
NON_ORDER_HEADING_RE = re.compile(
    r"^\s*(?:complaint|answer|motion|notice|brief|exhibit|annex|settlement\s+agreement|"
    r"certificate\s+of\s+service|transcript|memorandum)\b",
    re.I | re.M,
)


class PdfAnalysisError(ValueError):
    pass


class NonOrderError(PdfAnalysisError):
    pass


class PdfAnalyzer:
    def __init__(self, config: dict):
        self.config = config

    def analyze(self, data: bytes, expected_docket: str) -> PdfAnalysis:
        if not data.startswith(b"%PDF"):
            raise PdfAnalysisError("Downloaded content is not a PDF")
        try:
            reader = PdfReader(io.BytesIO(data))
            page_texts = [(page.extract_text() or "").strip() for page in reader.pages]
        except Exception as exc:
            raise PdfAnalysisError(f"Unreadable PDF: {exc}") from exc
        if not page_texts:
            raise PdfAnalysisError("PDF contains no pages")

        source = "pdf"
        text = "\n\f\n".join(page_texts).strip()
        decision_date = extract_decision_date(page_texts, expected_docket)
        threshold = int(self.config["ocr_min_text_chars_per_page"]) * len(page_texts)
        if len(text) < threshold or decision_date is None or normalize_docket(expected_docket) not in text:
            ocr_pages = self._ocr_relevant_pages(data, len(page_texts))
            if ocr_pages:
                source = "pdf+tesseract" if text else "tesseract"
                for index, ocr_text in ocr_pages.items():
                    if len(ocr_text) > len(page_texts[index]):
                        page_texts[index] = ocr_text
                text = "\n\f\n".join(page_texts).strip()
                decision_date = extract_decision_date(page_texts, expected_docket)

        if not text:
            raise PdfAnalysisError("No extractable text; install/configure Tesseract OCR")
        expected = normalize_docket(expected_docket)
        found = {normalize_docket(value) for value in DOCKET_RE.findall(text)}
        if expected not in found and expected not in text:
            raise PdfAnalysisError(f"Expected docket {expected} was not found in PDF text")
        is_order, order_reason = classify_order_document(text)
        if not is_order:
            raise NonOrderError(order_reason)
        if decision_date is None:
            raise PdfAnalysisError("Could not determine a reliable decision date from the PDF")

        normalized = normalize_content(text)
        return PdfAnalysis(
            docket=expected,
            decision_date=decision_date,
            text=text,
            text_source=source,
            page_count=len(page_texts),
            content_hash=hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest(),
            byte_hash=hashlib.sha256(data).hexdigest(),
        )

    def _ocr_relevant_pages(self, data: bytes, page_count: int) -> dict[int, str]:
        executable = resolve_tesseract(str(self.config.get("tesseract_path") or ""))
        if not executable:
            return {}
        count = max(1, int(self.config.get("ocr_last_pages", 3)))
        indexes = {0, *range(max(0, page_count - count), page_count)}
        output: dict[int, str] = {}
        document = fitz.open(stream=data, filetype="pdf")
        try:
            with tempfile.TemporaryDirectory(prefix="uscg00_ocr_") as temp_dir:
                for index in sorted(indexes):
                    page = document.load_page(index)
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
                    image = Path(temp_dir) / f"page_{index + 1}.png"
                    pixmap.save(str(image))
                    completed = subprocess.run(
                        [executable, str(image), "stdout", "--psm", "6", "-l", self.config["tesseract_lang"]],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="ignore",
                        timeout=60,
                        check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    if completed.returncode == 0 and completed.stdout.strip():
                        output[index] = completed.stdout.strip()
        finally:
            document.close()
        return output


def resolve_tesseract(configured: str = "") -> str:
    candidates = [configured, shutil.which("tesseract") or "", r"C:\Program Files\Tesseract-OCR\tesseract.exe"]
    return next((value for value in candidates if value and Path(value).exists()), "")


def extract_decision_date(page_texts: list[str], docket: str = "") -> date | None:
    """Score dates by last-page position and decision/signature context."""
    docket_year = int(docket[:4]) if re.match(r"^20\d{2}-", docket or "") else None
    candidates: list[tuple[int, int, date]] = []
    page_count = len(page_texts)
    for page_index, text in enumerate(page_texts):
        for pattern_index, pattern in enumerate(DATE_PATTERNS):
            for match in pattern.finditer(text):
                try:
                    if pattern_index == 0:
                        value = date(int(match.group(3)), MONTHS[match.group(1).lower()], int(match.group(2)))
                    elif pattern_index == 1:
                        value = date(int(match.group(3)), MONTHS[match.group(2).lower()], int(match.group(1)))
                    else:
                        value = date(int(match.group(3)), int(match.group(1)), int(match.group(2)))
                except (ValueError, KeyError):
                    continue
                if docket_year and abs(value.year - docket_year) > 1:
                    continue
                context = text[max(0, match.start() - 100):match.end() + 50]
                score = page_index * 1000 + match.start()
                if page_index == page_count - 1:
                    score += 5000
                if DATE_CONTEXT_RE.search(context):
                    score += 3000
                candidates.append((score, match.start(), value))
    return max(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None


def normalize_content(text: str) -> str:
    value = text.replace("\x00", " ").lower()
    value = re.sub(r"\bpage\s+\d+\s+of\s+\d+\b", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def classify_order_document(text: str) -> tuple[bool, str]:
    """Accept genuine decisions/orders and reject related non-order material."""
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return False, "No extractable content"
    if ANNEX_MANUAL_RE.search(normalized):
        return False, "Excluded annex or manual-amendment/correction document"

    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    head = "\n".join(lines[:100])
    if ORDER_HEADING_RE.search(head):
        return True, "Decision/order heading found"
    if OPERATIVE_ORDER_RE.search(normalized):
        return True, "Operative order language found"
    if NON_ORDER_HEADING_RE.search(head):
        heading = NON_ORDER_HEADING_RE.search(head).group(0).strip()
        return False, f"Excluded non-order document ({heading})"
    return False, "PDF has no decision/order heading or operative order language"
