from __future__ import annotations

import argparse
from pathlib import Path

from config.settings import load_config
from core.collector import CollectionBlocked, Collector, verify_samples
from utils.logger import build_logger


SAMPLES = ["2026-0002", "2026-0003", "2026-0007", "2026-0010", "2026-0024"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect USCG ALJ decision PDFs")
    parser.add_argument("--year", type=int, help="Decisions-page year")
    parser.add_argument("--no-ui", action="store_true", help="Run collection without desktop UI")
    parser.add_argument("--verify-samples", action="store_true", help="Verify the five known 2026 samples without collecting")
    parser.add_argument("--sf-view", help="Override the SF-View URL or local folder")
    parser.add_argument("--output", help="Override the output root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path(__file__).resolve().parent.parent
    config = load_config(project_root)
    if args.sf_view:
        config["sf_view_url"] = args.sf_view
    if args.output:
        config["output_root"] = args.output
    logger, _ = build_logger()

    if args.verify_samples:
        run_dir, results = verify_samples(config, logger, SAMPLES)
        print(f"Verification report: {run_dir / f'Report_{run_dir.name}.csv'}")
        return 0 if all(item.status == "VERIFIED" for item in results) else 1
    if not args.no_ui:
        from ui.main_window import run_app
        return run_app(config, project_root)
    year = args.year or int(config["default_year"])
    collector = Collector(config, logger)
    try:
        run_dir, _ = collector.collect(year)
    except CollectionBlocked as exc:
        print(f"Collection blocked: {exc}")
        if collector.log_path:
            print(f"Log: {collector.log_path}")
        return 2
    print(f"Run report: {run_dir / f'Report_{run_dir.name}.csv'}")
    return 0
