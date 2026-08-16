from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from core.models import SFViewEntry


DOCKET_RE = re.compile(r"(?<!\d)(\d{4}-\d{4})(?!\d)")
FILENAME_RE = re.compile(r"LDC_SMD_[^\s\"'<>]*?\.pdf", re.IGNORECASE)
DATE_TOKEN_RE = re.compile(r"(?:^|_)(\d{8})(?:_|\.|$)")


class SFViewChecker:
    """Indexes SF-View once, then provides fast docket lookups."""

    def __init__(self, config: dict):
        self.config = config
        self.entries: list[SFViewEntry] = []
        self.by_docket: dict[str, list[SFViewEntry]] = {}
        self.error = ""

    def prepare(self) -> bool:
        self.entries.clear()
        self.by_docket.clear()
        self.error = ""
        raw = str(self.config.get("sf_view_url") or "").strip()
        if not raw:
            self.error = "SF-View URL/path is blank"
            return False
        try:
            parsed = urlparse(raw)
            if parsed.scheme in {"http", "https"}:
                response = requests.get(
                    raw,
                    timeout=self.config["request_timeout_seconds"],
                    headers={"User-Agent": self.config["user_agent"]},
                )
                response.raise_for_status()
                self._index_html(response.text)
            else:
                path = self._local_path(raw)
                if path.is_dir():
                    self._index_directory(path)
                elif path.is_file():
                    self._index_html(path.read_text(encoding="utf-8", errors="ignore"))
                else:
                    raise FileNotFoundError(path)
            return True
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return False

    def matches(self, docket: str) -> list[SFViewEntry]:
        return list(self.by_docket.get(normalize_docket(docket), []))

    def _index_directory(self, root: Path) -> None:
        for path in root.rglob("*.pdf"):
            self._add(path.name, path)

    def _index_html(self, html: str) -> None:
        seen: set[str] = set()
        for match in FILENAME_RE.finditer(unquote(html)):
            label = match.group(0).strip()
            if label.lower() not in seen:
                seen.add(label.lower())
                self._add(label)

    def _add(self, label: str, path: Path | None = None) -> None:
        docket_match = DOCKET_RE.search(label)
        if not docket_match:
            return
        entry = SFViewEntry(label, docket_match.group(1), extract_filename_date(label), path)
        self.entries.append(entry)
        self.by_docket.setdefault(entry.docket, []).append(entry)

    @staticmethod
    def _local_path(raw: str) -> Path:
        parsed = urlparse(raw)
        if parsed.scheme != "file":
            return Path(raw)
        if parsed.netloc:
            part = unquote(parsed.path).lstrip("/").replace("/", "\\")
            return Path(f"\\\\{parsed.netloc}\\{part}")
        return Path(unquote(parsed.path).replace("/", "\\"))


def normalize_docket(value: str) -> str:
    match = DOCKET_RE.search(value or "")
    return match.group(1) if match else value.strip().upper()


def extract_filename_date(value: str):
    match = DATE_TOKEN_RE.search(value)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%m%d%Y").date()
    except ValueError:
        return None
