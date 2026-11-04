import os


def is_pull_request_preview() -> bool:
    """Render PR previews must never start workers against production services."""
    return os.getenv("IS_PULL_REQUEST", "false").strip().lower() == "true"
