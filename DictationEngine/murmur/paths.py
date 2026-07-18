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


def ensure_config_dir() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)


def bootstrap_config() -> bool:
    """Create ~/.murmur/config.yaml from the shipped template on first run.
    Returns True if this was a first run (the config was just created)."""
    ensure_config_dir()
    if not os.path.exists(CONFIG_PATH) and os.path.exists(DEFAULT_CONFIG_TEMPLATE):
        shutil.copyfile(DEFAULT_CONFIG_TEMPLATE, CONFIG_PATH)
        return True
    return False
