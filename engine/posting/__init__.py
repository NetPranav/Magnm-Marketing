"""Official-API social posting package (LinkedIn, Instagram, X) with human confirm gate."""

PLATFORMS = ("linkedin", "instagram", "x")


class PostingError(Exception):
    """Typed error raised by posting services, vaults, and providers."""

    def __init__(self, message, status=400, code="posting_error", *, uncertain=False):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)
        self.code = str(code)
        self.uncertain = bool(uncertain)


def normalize_platform(platform):
    """Validate and normalize a supported official-API posting platform identifier."""
    plat = str(platform or "").strip().lower()
    if plat not in PLATFORMS:
        raise PostingError(
            f"Unsupported posting platform '{platform}'. Expected one of: {', '.join(PLATFORMS)}",
            status=400,
            code="invalid_platform",
        )
    return plat
