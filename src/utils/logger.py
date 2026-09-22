"""
Industrial Production Logging Engine
Provides structured console and file logging with standardized formatting.
"""

import sys
import logging
from typing import Optional


def get_logger(name: str = "APMS", level: int = logging.INFO) -> logging.Logger:
    """Configures and returns a thread-safe structured logger."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger
