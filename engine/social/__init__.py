"""Logged-in social research subsystem (opt-in, read-only, encrypted vault, live human control)."""

PLATFORMS = ("instagram", "x", "linkedin")
RECIPES = ("profile_posts", "search", "post_replies", "trending")

REQUIRED_ACKNOWLEDGEMENT_SUMMARY = (
    "Automated access breaks the platform's terms, the account may be banned, "
    "and you should use a separate research account."
)


class SocialError(Exception):
    """Typed error raised by social research services and policy checks."""

    def __init__(self, message, status=400, code="social_error"):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status)
        self.code = str(code)


def normalize_platform(platform):
    """Validate and normalize a supported social platform identifier."""
    plat = str(platform or "").strip().lower()
    if plat not in PLATFORMS:
        raise SocialError(
            f"Unsupported platform '{platform}'. Expected one of: {', '.join(PLATFORMS)}",
            status=400,
            code="invalid_platform",
        )
    return plat


def normalize_recipe(recipe):
    """Validate and normalize a supported social research recipe name."""
    rec = str(recipe or "").strip().lower()
    if rec not in RECIPES:
        raise SocialError(
            f"Unsupported recipe '{recipe}'. Expected one of: {', '.join(RECIPES)}",
            status=400,
            code="invalid_recipe",
        )
    return rec
