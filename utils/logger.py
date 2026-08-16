from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%I:%M:%S %p"


def build_logger(
    log_dir: Path | None = None,
    sink: Optional[Callable[[str], None]] = None,
) -> tuple[logging.Logger, Path | None]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    logger = logging.getLogger(f"USCG00.{stamp}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT)
    path: Path | None = None
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"Log_USCG00_{stamp}.txt"
        handlers.insert(0, logging.FileHandler(path, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    if sink:
        class SinkHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                sink(self.format(record))
        handler = SinkHandler()
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger, path


def attach_run_log(logger: logging.Logger, run_dir: Path, run_name: str) -> Path:
    """Attach the one canonical text log whose name mirrors the run folder."""
    run_dir.mkdir(parents=True, exist_ok=True)
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler):
            logger.removeHandler(handler)
            handler.close()
    path = run_dir / f"Log_{run_name}.txt"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    logger.addHandler(handler)
    return path
