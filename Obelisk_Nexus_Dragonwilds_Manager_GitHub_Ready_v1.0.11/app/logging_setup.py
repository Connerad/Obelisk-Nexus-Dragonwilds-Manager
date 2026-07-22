from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .paths import ensure_dirs


def configure_logging() -> logging.Logger:
    paths = ensure_dirs()
    logger = logging.getLogger("dwsm")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(paths["logs"] / "manager.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
        logger.addHandler(handler)
    return logger
