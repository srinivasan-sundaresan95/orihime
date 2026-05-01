"""Security configuration: custom taint sources, sinks, and sanitizers.

Orihime loads security rules from ``~/.orihime/security.yml`` (or the path in
``ORIHIME_SECURITY_CONFIG``).  The file is optional — if absent or empty, only
the built-in annotation-based rules apply.

YAML schema::

    version: 1
    sources:
      # Annotation-based: any method parameter annotated with one of these
      # is treated as user-controlled taint.
      annotations:
        - "org.springframework.web.bind.annotation.RequestParam"
        - "org.springframework.web.bind.annotation.PathVariable"
        - "org.springframework.web.bind.annotation.RequestBody"
      # Method-based: return value of these methods is tainted.
      methods:
        - "javax.servlet.http.HttpServletRequest.getParameter"
        - "javax.servlet.http.HttpServletRequest.getHeader"

    sinks:
      # Any call to these methods propagates taint into a dangerous operation.
      methods:
        - "java.sql.Statement.execute"
        - "java.sql.Statement.executeQuery"
        - "org.springframework.web.client.RestTemplate.getForEntity"

    sanitizers:
      # Calls to these methods are treated as sanitizers — taint stops here.
      methods:
        - "org.springframework.web.util.HtmlUtils.htmlEscape"
        - "org.owasp.esapi.ESAPI.encoder"

The built-in defaults (Spring MVC annotations, RestTemplate, WebClient) are
always active and are merged with any user-defined rules at load time.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    import yaml  # type: ignore[import-untyped]
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# ---------------------------------------------------------------------------
# Built-in defaults
# ---------------------------------------------------------------------------

_BUILTIN_SOURCE_ANNOTATIONS: list[str] = [
    # Spring MVC — user input parameters
    "RequestParam",
    "PathVariable",
    "RequestBody",
    "RequestHeader",
    "MatrixVariable",
    "ModelAttribute",
    # JAX-RS
    "QueryParam",
    "PathParam",
    "FormParam",
    "HeaderParam",
    "CookieParam",
]

_BUILTIN_SOURCE_METHODS: list[str] = [
    # Raw servlet access
    "HttpServletRequest.getParameter",
    "HttpServletRequest.getHeader",
    "HttpServletRequest.getCookies",
    "HttpServletRequest.getInputStream",
    "HttpServletRequest.getReader",
    # Spring convenience
    "ServerHttpRequest.getBody",
]

_BUILTIN_SINK_METHODS: list[str] = [
    # SQL
    "Statement.execute",
    "Statement.executeQuery",
    "Statement.executeUpdate",
    "PreparedStatement.execute",
    "PreparedStatement.executeQuery",
    # Spring HTTP clients — cross-service sinks
    "RestTemplate.getForEntity",
    "RestTemplate.postForEntity",
    "RestTemplate.exchange",
    "RestTemplate.getForObject",
    "RestTemplate.postForObject",
    "WebClient.get",
    "WebClient.post",
    "WebClient.put",
    "WebClient.delete",
    "WebClient.patch",
    # Command execution
    "Runtime.exec",
    "ProcessBuilder.start",
    # Path traversal
    "File.<init>",
    "Files.readAllBytes",
    "Files.newBufferedReader",
]

_BUILTIN_SANITIZER_METHODS: list[str] = [
    "HtmlUtils.htmlEscape",
    "StringEscapeUtils.escapeHtml4",
    "ESAPI.encoder",
    "Encode.forHtml",
]


# ---------------------------------------------------------------------------
# Loaded config
# ---------------------------------------------------------------------------

class SecurityConfig:
    """Merged security configuration (built-ins + user YAML overrides)."""

    def __init__(
        self,
        source_annotations: list[str],
        source_methods: list[str],
        sink_methods: list[str],
        sanitizer_methods: list[str],
    ) -> None:
        self.source_annotations = source_annotations
        self.source_methods = source_methods
        self.sink_methods = sink_methods
        self.sanitizer_methods = sanitizer_methods

    def is_source_annotation(self, annotation: str) -> bool:
        short = annotation.split(".")[-1]
        return any(
            short == s.split(".")[-1] or annotation.endswith(s)
            for s in self.source_annotations
        )

    def is_sink_method(self, method_name: str) -> bool:
        short = method_name.split(".")[-1]
        return any(
            short == s.split(".")[-1] or method_name.endswith(s)
            for s in self.sink_methods
        )

    def is_sanitizer_method(self, method_name: str) -> bool:
        short = method_name.split(".")[-1]
        return any(
            short == s.split(".")[-1] or method_name.endswith(s)
            for s in self.sanitizer_methods
        )


def _load_yaml_config(path: Path) -> dict:
    if not _HAS_YAML:
        return {}
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_security_config(config_path: str | Path | None = None) -> SecurityConfig:
    """Load and merge built-in + user security config.

    Parameters
    ----------
    config_path:
        Path to the YAML config file.  Defaults to
        ``$ORIHIME_SECURITY_CONFIG`` or ``~/.orihime/security.yml``.
    """
    if config_path is None:
        config_path = Path(
            os.environ.get("ORIHIME_SECURITY_CONFIG", str(Path.home() / ".orihime" / "security.yml"))
        )
    user = _load_yaml_config(Path(config_path))

    sources = user.get("sources", {})
    sinks = user.get("sinks", {})
    sanitizers = user.get("sanitizers", {})

    # Merge: built-ins first, user rules appended (deduped)
    def _merge(builtin: list[str], user_list: list) -> list[str]:
        combined = list(builtin)
        for item in (user_list or []):
            if isinstance(item, str) and item not in combined:
                combined.append(item)
        return combined

    return SecurityConfig(
        source_annotations=_merge(_BUILTIN_SOURCE_ANNOTATIONS, sources.get("annotations", [])),
        source_methods=_merge(_BUILTIN_SOURCE_METHODS, sources.get("methods", [])),
        sink_methods=_merge(_BUILTIN_SINK_METHODS, sinks.get("methods", [])),
        sanitizer_methods=_merge(_BUILTIN_SANITIZER_METHODS, sanitizers.get("methods", [])),
    )


# Module-level singleton — lazy-loaded on first use, cached thereafter.
_config: SecurityConfig | None = None


def get_security_config() -> SecurityConfig:
    global _config
    if _config is None:
        _config = load_security_config()
    return _config


def reload_security_config(path: str | Path | None = None) -> SecurityConfig:
    """Force reload from disk — useful after user edits the YAML file."""
    global _config
    _config = load_security_config(path)
    return _config
