"""License compliance checker for Maven/Gradle dependencies."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

# Default allowlist — MIT-compatible licenses
DEFAULT_ALLOWED = frozenset({
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause",
    "ISC", "0BSD", "Unlicense", "CC0-1.0",
})

# Licenses that require source disclosure or are incompatible with commercial use
COPYLEFT_LICENSES = frozenset({
    "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later",
    "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
    "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
    "LGPL-2.0", "LGPL-2.1", "LGPL-3.0",
    "LGPL-2.0-only", "LGPL-2.1-only", "LGPL-3.0-only",
    "EUPL-1.1", "EUPL-1.2",
    "CDDL-1.0", "EPL-1.0", "EPL-2.0",
    "MPL-2.0",  # weak copyleft — flag as warning
})

# Licenses treated as WARNING rather than VIOLATION (weak/file copyleft)
_WARNING_LICENSES = frozenset({
    "MPL-2.0", "LGPL-2.0", "LGPL-2.1", "LGPL-3.0",
    "LGPL-2.0-only", "LGPL-2.1-only", "LGPL-3.0-only",
    "EPL-1.0", "EPL-2.0", "CDDL-1.0",
    "EUPL-1.1", "EUPL-1.2",
})

# Licenses treated as VIOLATION (strong copyleft)
_VIOLATION_LICENSES = frozenset({
    "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later",
    "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
    "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
})

_MAVEN_NS = "http://maven.apache.org/POM/4.0.0"
_SKIP_SCOPES = {"test", "provided"}


def parse_pom_xml(pom_path: str) -> list[dict]:
    """Parse pom.xml and return list of {group, artifact, version} dicts.

    Uses xml.etree.ElementTree (stdlib). Handles the Maven namespace:
    {http://maven.apache.org/POM/4.0.0}
    Extracts <dependency> elements from <dependencies> sections.
    Skips <scope>test</scope> and <scope>provided</scope> dependencies.
    Handles ${property} version references — returns version as-is (don't resolve).
    """
    tree = ET.parse(pom_path)
    root = tree.getroot()

    # Detect namespace prefix
    ns = _MAVEN_NS if root.tag.startswith("{") else ""
    tag = (lambda name: f"{{{_MAVEN_NS}}}{name}") if ns else (lambda name: name)

    deps: list[dict] = []
    # Search all <dependencies> elements (may be in <dependencyManagement> too — skip those)
    for deps_elem in root.iter(tag("dependencies")):
        # Skip if parent is <dependencyManagement>
        parent_tag = deps_elem.tag  # noqa: F841 — we iterate parent differently below
        for dep in deps_elem.findall(tag("dependency")):
            scope_elem = dep.find(tag("scope"))
            scope = scope_elem.text.strip().lower() if scope_elem is not None and scope_elem.text else ""
            if scope in _SKIP_SCOPES:
                continue
            group_elem = dep.find(tag("groupId"))
            artifact_elem = dep.find(tag("artifactId"))
            version_elem = dep.find(tag("version"))
            if group_elem is None or artifact_elem is None:
                continue
            group = (group_elem.text or "").strip()
            artifact = (artifact_elem.text or "").strip()
            version = (version_elem.text or "").strip() if version_elem is not None else ""
            if group and artifact:
                deps.append({"group": group, "artifact": artifact, "version": version})

    return deps


def parse_gradle(gradle_path: str) -> list[dict]:
    """Parse build.gradle or build.gradle.kts using regex.

    Matches patterns like:
      - implementation 'group:artifact:version'
      - implementation("group:artifact:version")
      - api 'group:artifact:version'
      - compileOnly 'group:artifact:version'

    Skips testImplementation, testCompileOnly, testRuntime.
    Returns list of {group, artifact, version} dicts.
    """
    with open(gradle_path, encoding="utf-8") as f:
        content = f.read()

    # Configurations to include
    include_configs = {
        "implementation", "api", "compileOnly", "runtimeOnly",
        "annotationProcessor", "kapt", "compile",
    }
    # Configurations to skip (test-related)
    skip_prefixes = ("test", "androidTest", "debugImplementation", "releaseImplementation")

    # Match the following patterns:
    #   config 'group:artifact:version'
    #   config "group:artifact:version"
    #   config("group:artifact:version")
    #   config('group:artifact:version')
    # Opening: optional ( then quote; closing: quote then optional )
    pattern = re.compile(
        r"""^[ \t]*(\w+)\s*\(?[\"']([A-Za-z0-9._\-]+):([A-Za-z0-9._\-]+):([A-Za-z0-9._\-]+)[\"']\)?""",
        re.MULTILINE,
    )

    deps: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for match in pattern.finditer(content):
        config, group, artifact, version = match.groups()
        # Skip test configurations
        if any(config.startswith(prefix) for prefix in skip_prefixes):
            continue
        if config not in include_configs:
            continue
        key = (group, artifact)
        if key not in seen:
            seen.add(key)
            deps.append({"group": group, "artifact": artifact, "version": version})

    return deps


def lookup_license_maven_central(group: str, artifact: str) -> str:
    """Query Maven Central search API for the license of a dependency.

    URL: https://search.maven.org/solrsearch/select?q=g:{group}+AND+a:{artifact}&rows=1&wt=json

    Parses the JSON response. The license is in response.docs[0].licenses[] if present.
    Returns the first license identifier string, or "UNKNOWN" if not found.

    Uses urllib.request (stdlib) with a 5-second timeout.
    Handles network errors gracefully — returns "UNKNOWN" on any exception.
    """
    url = (
        f"https://search.maven.org/solrsearch/select"
        f"?q=g:{group}+AND+a:{artifact}&rows=1&wt=json"
    )
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "dedalus-license-checker/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        docs = data.get("response", {}).get("docs", [])
        if not docs:
            return "UNKNOWN"
        licenses = docs[0].get("licenses", [])
        if not licenses:
            return "UNKNOWN"
        return licenses[0]
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def check_licenses(
    deps: list[dict],
    allowed: frozenset[str] = DEFAULT_ALLOWED,
    skip_lookup: bool = False,
    license_overrides: dict[str, str] | None = None,
) -> list[dict]:
    """For each dependency, look up its license and classify.

    Returns list of {
        group, artifact, version,
        license: str,          # SPDX identifier or "UNKNOWN"
        status: "OK" | "VIOLATION" | "WARNING" | "UNKNOWN",
        reason: str,           # human-readable explanation
    }

    Status logic:
      - "OK": license is in allowed set
      - "VIOLATION": license is in COPYLEFT_LICENSES and is a strong copyleft (GPL, AGPL)
      - "WARNING": license is MPL-2.0, LGPL, EPL, CDDL (weak/file copyleft — review needed)
      - "UNKNOWN": license could not be determined
    """
    overrides = license_overrides or {}
    results: list[dict] = []

    for dep in deps:
        group = dep["group"]
        artifact = dep["artifact"]
        version = dep.get("version", "")
        key = f"{group}:{artifact}"

        if key in overrides:
            spdx = overrides[key]
        elif skip_lookup:
            spdx = "UNKNOWN"
        else:
            spdx = lookup_license_maven_central(group, artifact)

        if spdx in allowed:
            status = "OK"
            reason = f"License {spdx!r} is in the allowed list."
        elif spdx in _VIOLATION_LICENSES:
            status = "VIOLATION"
            reason = (
                f"License {spdx!r} is a strong copyleft license incompatible with "
                "commercial use. Remove or replace this dependency."
            )
        elif spdx in _WARNING_LICENSES:
            status = "WARNING"
            reason = (
                f"License {spdx!r} is a weak/file-level copyleft license. "
                "Review whether your usage complies with its terms."
            )
        elif spdx == "UNKNOWN":
            status = "UNKNOWN"
            reason = "License could not be determined from Maven Central."
        else:
            # Any other license not in the allowlist
            status = "WARNING"
            reason = (
                f"License {spdx!r} is not in the allowed list. Review before use."
            )

        results.append({
            "group": group,
            "artifact": artifact,
            "version": version,
            "license": spdx,
            "status": status,
            "reason": reason,
        })

    return results
