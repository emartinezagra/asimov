import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "asimov.log")


def setup_logging(level=None):
    root = logging.getLogger()

    if level is None:
        level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    if not root.handlers:
        os.makedirs(LOG_DIR, exist_ok=True)

        formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        root.setLevel(level)
        root.addHandler(file_handler)
        root.addHandler(console_handler)

    return logging.getLogger("asimov")
