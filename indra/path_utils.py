from __future__ import annotations
import re


def compile_path_regex(path: str) -> str:
    """Convert a Spring path pattern to a regex string anchored with ^...$."""
    if not path:
        return ""
    # Replace {varName} with named capture groups
    regex = re.sub(r"\{([^}]+)\}", r"(?P<\1>[^/]+)", path)
    # Replace /** with wildcard
    regex = regex.replace("/**", "(?:/.*)?")
    return f"^{regex}$"


def match_url_pattern(url_pattern: str, path_regex: str) -> bool:
    """Return True if *url_pattern* matches the compiled *path_regex*.

    The url_pattern is the literal URL string extracted from a RestCall
    (e.g. ``/api/users/123``).  The path_regex is a regex string produced by
    :func:`compile_path_regex` (e.g. ``^/api/users/(?P<id>[^/]+)$``).

    Returns False when either argument is empty or the regex is invalid.
    """
    if not url_pattern or not path_regex:
        return False
    try:
        return bool(re.match(path_regex, url_pattern))
    except re.error:
        return False
