from __future__ import annotations

import csv
from pathlib import Path

from core.models import CollectionResult

REPORT_HEADERS = {
    "docket": "Docket Number",
    "status": "Status",
    "message": "Result Details",
    "source_url": "Source URL",
    "decision_date": "Decision Date",
    "text_source": "Text Source",
    "final_filename": "Final Filename",
}


def write_reports(run_dir: Path, results: list[CollectionResult], title: str = "USCG00 collection") -> dict[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for result in results:
        values = result.as_dict()
        rows.append({header: values.get(key, "") for key, header in REPORT_HEADERS.items()})
    csv_path = run_dir / f"Report_{run_dir.name}.csv"
    fields = list(REPORT_HEADERS.values())
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return {"csv": csv_path}
