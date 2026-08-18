from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .models import CollectionResult, DecisionRow
from .source import USCGSource
from download.pdf_processor import NonOrderError, PdfAnalysisError, PdfAnalyzer
from reporting.csv_reporter import write_reports
from utils.logger import attach_run_log
from utils.sfview_checker import SFViewChecker


class CollectionBlocked(RuntimeError):
    pass


class Collector:
    def __init__(self, config: dict, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.source = USCGSource(config)
        self.sfview = SFViewChecker(config)
        self.analyzer = PdfAnalyzer(config)
        self._content_hashes: dict[str, str] = {}
        self.run_dir: Path | None = None
        self.log_path: Path | None = None

    def collect(self, year: int, progress: Callable[[int, int], None] | None = None) -> tuple[Path, list[CollectionResult]]:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name = f"USCG00_{year}_{stamp}"
        run_dir = resolve_output_root(self.config) / run_name
        collected_dir = run_dir / "Collected"
        self.run_dir = run_dir
        self.log_path = attach_run_log(self.logger, run_dir, run_name)
        self.logger.info("Run folder ready: %s", run_dir)
        if not self.sfview.prepare():
            message = f"SF-View could not be indexed: {self.sfview.error}"
            if self.config.get("sf_view_required", True):
                self.logger.error(message)
                raise CollectionBlocked(message)
            self.logger.warning(message + "; continuing because sf_view_required=false")
        else:
            self.logger.info("SF-View index ready: %d files", len(self.sfview.entries))

        rows = self.source.list_decisions(year)
        self.logger.info("USCG %s page contains %d downloadable decisions", year, len(rows))
        results: list[CollectionResult] = []
        try:
            for index, row in enumerate(rows, 1):
                result = self._process(row, collected_dir)
                results.append(result)
                self.logger.info("[%s] %s - %s", result.status, row.docket, result.message)
                if progress:
                    progress(index, len(rows))
            write_reports(run_dir, results, f"USCG00 Collector - {year}")
        finally:
            self.source.close()
        return run_dir, results

    def _process(self, row: DecisionRow, collected_dir: Path) -> CollectionResult:
        matches = self.sfview.matches(row.docket)
        if matches:
            labels = "; ".join(item.label for item in matches[:3])
            return CollectionResult(row.docket, "SKIPPED_SFVIEW", f"Docket already exists in SF-View: {labels}", row.pdf_url)
        try:
            data = self.source.download(row.pdf_url)
            analysis = self.analyzer.analyze(data, row.docket)
        except NonOrderError as exc:
            return CollectionResult(row.docket, "EXCLUDED_NON_ORDER", str(exc), row.pdf_url)
        except Exception as exc:
            return CollectionResult(row.docket, "ERROR", str(exc), row.pdf_url)

        prior = self._content_hashes.get(analysis.content_hash)
        if prior:
            return CollectionResult(row.docket, "SKIPPED_CONTENT", f"Same normalized content as {prior}", row.pdf_url, decision_date=analysis.decision_date.isoformat(), text_source=analysis.text_source)

        base = f"{self.config['file_prefix']}_{analysis.docket}_{analysis.decision_date:%m%d%Y}"
        target = collected_dir / f"{base}.pdf"
        variant = 1
        while target.exists():
            try:
                existing = self.analyzer.analyze(target.read_bytes(), analysis.docket)
                if existing.content_hash == analysis.content_hash:
                    return CollectionResult(row.docket, "SKIPPED_CONTENT", f"Identical content already saved as {target.name}", row.pdf_url, str(target), analysis.decision_date.isoformat(), analysis.text_source)
            except PdfAnalysisError:
                pass
            variant += 1
            target = collected_dir / f"{base}_v{variant}.pdf"

        collected_dir.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".pdf.part")
        temporary.write_bytes(data)
        temporary.replace(target)
        self._content_hashes[analysis.content_hash] = target.name
        message = f"Saved {target.name}"
        if variant > 1:
            message += " (same docket/date, different verified content)"
        return CollectionResult(
            docket=row.docket,
            status="SAVED",
            message=message,
            source_url=row.pdf_url,
            output_file=str(target),
            decision_date=analysis.decision_date.isoformat(),
            text_source=analysis.text_source,
            final_filename=target.name,
        )


def verify_samples(config: dict, logger: logging.Logger, dockets: Iterable[str]) -> tuple[Path, list[CollectionResult]]:
    source = USCGSource(config)
    analyzer = PdfAnalyzer(config)
    run_name = f"Source_Verification_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir = resolve_output_root(config) / run_name
    attach_run_log(logger, run_dir, run_name)
    requested = set(dockets)
    rows = {row.docket: row for row in source.list_decisions(2026) if row.docket in requested}
    results: list[CollectionResult] = []
    try:
        for docket in requested:
            row = rows.get(docket)
            if row is None:
                results.append(CollectionResult(docket, "ERROR", "Downloadable row not found on 2026 page"))
                continue
            try:
                analysis = analyzer.analyze(source.download(row.pdf_url), docket)
                filename = f"{config['file_prefix']}_{docket}_{analysis.decision_date:%m%d%Y}.pdf"
                results.append(CollectionResult(
                    docket=docket,
                    status="VERIFIED",
                    message=f"Downloadable; target filename {filename}",
                    source_url=row.pdf_url,
                    output_file=filename,
                    decision_date=analysis.decision_date.isoformat(),
                    text_source=analysis.text_source,
                    final_filename=filename,
                ))
            except Exception as exc:
                results.append(CollectionResult(docket, "ERROR", str(exc), row.pdf_url))
            logger.info("[%s] %s - %s", results[-1].status, docket, results[-1].message)
    finally:
        source.close()
    write_reports(run_dir, results, "USCG00 source verification (no collected PDFs saved)")
    return run_dir, results


def resolve_output_root(config: dict) -> Path:
    configured = Path(str(config.get("output_root") or "Downloads/USCG00 Collections")).expanduser()
    return configured if configured.is_absolute() else Path.home() / configured
