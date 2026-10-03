"""Model revisions Murmur trusts, and where their files are on disk.

Loading a model by repo name ("mlx-community/...") asks Hugging Face for the
newest revision every time, so whatever the repo's owner pushes next would be
loaded with the user's microphone audio and text. The models the installer
sets up are pinned to the commits they were tested with instead.

To move a pin: check the new revision works, then update its hash here.
"""

import os

PINNED_REVISIONS = {
    "mlx-community/whisper-large-v3-turbo-q4": "660c343bbf4e52ac257f0b7d952e5388e6f93bef",
    "mlx-community/Qwen2.5-3B-Instruct-4bit": "4f83f8f146fdf28b512a06562b671d7af4fab457",
    "mlx-community/Qwen2.5-1.5B-Instruct-4bit": "8b403126fc14f14cfc99bb4cfa72ecbc129ea677",
}


def pinned_revision(repo: str):
    """The commit `repo` is pinned to, or None for a model the user picked."""
    return PINNED_REVISIONS.get(repo)


def local_model_path(repo: str) -> str:
    """A local folder holding `repo` at its pinned revision (newest for models
    without a pin). Uses the cache when it's there, so a warm start makes no
    network request; downloads otherwise."""
    if os.path.isdir(os.path.expanduser(repo)):
        return repo  # already a local folder
    from huggingface_hub import snapshot_download

    revision = pinned_revision(repo)
    try:
        return snapshot_download(repo, revision=revision, local_files_only=True)
    except Exception:
        return snapshot_download(repo, revision=revision)
