from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "source_url_template": "https://www.uscg.mil/Resources/Administrative-Law-Judges/Decisions/ALJ-Decisions-{year}/",
    "source_year_urls": {
        "2025": "https://www.uscg.mil/Resources/Administrative-Law-Judges/Decisions/ALJ-2025-Decision/",
    },
    "default_year": 2026,
    "output_root": "Downloads/USCG00 Collections",
    "sf_view_url": "file://legal.regn.net/FAB/PCLPROD/SF-View/PROD/team10/USCG00",
    "sf_view_required": True,
    "request_timeout_seconds": 60,
    "browser_fallback_enabled": True,
    "headless": True,
    "ocr_min_text_chars_per_page": 120,
    "ocr_last_pages": 3,
    "tesseract_path": "",
    "tesseract_lang": "eng",
    "file_prefix": "LDC_SMD",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36",
}


def load_config(project_root: Path) -> dict[str, Any]:
    path = project_root / "config.json"
    if not path.exists():
        example = project_root / "config.example.json"
        if example.exists():
            shutil.copyfile(example, path)
        else:
            path.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return {**DEFAULTS, **loaded}
