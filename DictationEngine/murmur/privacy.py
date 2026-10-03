"""Keeps dictated words out of the log and the files Murmur leaves behind."""

import logging
import os


def said(logger: logging.Logger, text: str) -> str:
    """What a log line may show of dictated (or selected) text: the words
    themselves only when DEBUG logging was switched on on purpose, otherwise
    just how long it was. murmur.log is kept on disk and rotated, not wiped."""
    text = text or ""
    if logger.isEnabledFor(logging.DEBUG):
        return repr(text)
    return f"<{len(text)} chars>"


def make_private(path: str, mode: int) -> None:
    """chmod that never raises: a file that's missing or not ours stays as is."""
    try:
        if os.path.exists(path) and (os.stat(path).st_mode & 0o777) != mode:
            os.chmod(path, mode)
    except OSError:
        pass
