"""Filesystem locations for Murmur's local state. Everything lives in ~/.murmur."""

import os
import shutil

CONFIG_DIR = os.path.expanduser("~/.murmur")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")
DB_PATH = os.path.join(CONFIG_DIR, "murmur.db")
LOG_PATH = os.path.join(CONFIG_DIR, "murmur.log")

# The template shipped alongside the source tree.
DEFAULT_CONFIG_TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.default.yaml"
)


# Files in CONFIG_DIR that can hold dictated text or personal vocabulary.
PRIVATE_FILES = ("config.yaml", "murmur.db", "notch.json", "notch.cmd", "axdump.json") + tuple(
    "murmur.log" + suffix for suffix in ("", ".1", ".2", ".3")
)


def ensure_config_dir() -> None:
    """Creates ~/.murmur readable by this user only, and tightens what an older
    version left world-readable (the log used to be 0644 with transcripts in it)."""
    from .privacy import make_private

    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    make_private(CONFIG_DIR, 0o700)
    for name in PRIVATE_FILES:
        make_private(os.path.join(CONFIG_DIR, name), 0o600)


def bootstrap_config() -> bool:
    """Create ~/.murmur/config.yaml from the shipped template on first run.
    Returns True if this was a first run (the config was just created)."""
    ensure_config_dir()
    if not os.path.exists(CONFIG_PATH) and os.path.exists(DEFAULT_CONFIG_TEMPLATE):
        shutil.copyfile(DEFAULT_CONFIG_TEMPLATE, CONFIG_PATH)
        return True
    return False
