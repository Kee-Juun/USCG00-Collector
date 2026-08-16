# USCG00 Collector

Standalone Python desktop collector for USCG Administrative Law Judge decisions.
It reads a year-specific decisions table, checks each docket against SF-View before
download, extracts the authoritative decision date from the PDF (OCR fallback),
performs content/date duplicate validation, and saves accepted files as:

`LDC_SMD_2026-0002_03032026.pdf`

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config.example.json config.json
python main.py
```

The compact 500x246 PyQt6 desktop UI uses a dark rounded frameless shell with a
custom title bar, year selector, live progress, and centered milestone status.
Output routing and SF-View duplicate checking remain locked to their configured
background locations. The progress bar uses an activity sheen while running, and
the result dialog opens the completed run folder only when the user clicks OK.
Detailed activity is written to the run's text log. For automation or testing:

```powershell
python main.py --year 2026 --no-ui
python main.py --verify-samples
```

`sf_view_required` defaults to `true`: collection stops before downloading when
SF-View cannot be indexed. Set the real USCG00 SF-View location in `config.json`.
The verifier downloads only the five known samples to a temporary directory and
does not add them to the collected-file output. Run folders are created
automatically under `Downloads/USCG00 Collections/`.

USCG page slugs are not consistent across all years. The `source_year_urls`
setting maps exceptions explicitly (including the official 2025 page), while
other years continue to use `source_url_template`.

## Include/exclude rule

The PDF content gate follows the FDUSCG00 instruction while applying the current
operational rule: collect every genuine decision or order (including consent,
admission, default, withdrawal, dismissal, interim, supplemental, and final
orders). Exclude annexes, manual amendments/corrections, and non-order filings
such as complaints, answers, motions, notices, briefs, exhibits, transcripts,
certificates of service, and standalone settlement agreements.

The CSV report includes a `Final Filename` column containing only the final saved
PDF name for each included document, ready to copy into a feedback form. Exported
headers are human-readable phrases such as `Docket Number`, `Decision Date`, and
`Final Filename`. Its name matches the run folder with a `Report_` prefix, such as
`Report_USCG00_2025_20260811_195500.csv`. Each run folder also contains a matching
`Log_<run-folder-name>.txt` activity log. JSON and text reports are intentionally
not generated.

## Architecture

The codebase follows the same functional-package convention used by the WVPUC0,
ALLCUS, and FCC collectors:

```text
app/          CLI orchestration and unattended entry point
config/       Configuration defaults and loading
core/         Collector workflow, source scraper, and domain models
download/     PDF extraction, OCR, validation, and fingerprints
reporting/    Human-readable CSV report generation
ui/           PyQt6 desktop window
utils/        Logging and SF-View duplicate checking
tests/        Regression tests
assets/       Fonts and SVG interface assets
main.py       Application entry point
```

Each folder is an importable Python package with a focused responsibility. This
keeps UI, collection, document-processing, duplicate-checking, and reporting code
independently discoverable without changing the runtime workflow.
