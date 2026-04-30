"""Unit tests for S11 — License Compliance (dedalus.license_checker)."""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import kuzu
import pytest

from dedalus.license_checker import (
    DEFAULT_ALLOWED,
    check_licenses,
    parse_gradle,
    parse_pom_xml,
)
from dedalus.schema import init_schema
import dedalus.mcp_server as mcp_mod
from dedalus.mcp_server import find_license_violations


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SAMPLE_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>my-app</artifactId>
    <version>1.0.0</version>

    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
            <version>3.2.0</version>
        </dependency>
        <dependency>
            <groupId>com.google.guava</groupId>
            <artifactId>guava</artifactId>
            <version>32.0.1-jre</version>
        </dependency>
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13.2</version>
            <scope>test</scope>
        </dependency>
        <dependency>
            <groupId>org.mockito</groupId>
            <artifactId>mockito-core</artifactId>
            <version>5.0.0</version>
            <scope>provided</scope>
        </dependency>
    </dependencies>
</project>
"""

_SAMPLE_GRADLE = """\
plugins {
    id 'java'
}

dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-web:3.2.0'
    implementation("com.google.guava:guava:32.0.1-jre")
    api 'com.fasterxml.jackson.core:jackson-databind:2.15.0'
    compileOnly 'org.projectlombok:lombok:1.18.28'
    testImplementation 'junit:junit:4.13.2'
    testCompileOnly 'org.mockito:mockito-core:5.0.0'
    testRuntimeOnly 'org.junit.platform:junit-platform-launcher:1.10.0'
}
"""

_SAMPLE_GRADLE_KTS = """\
dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web:3.2.0")
    api("com.google.guava:guava:32.0.1-jre")
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.0")
}
"""


def _write_tmp_file(content: str, filename: str) -> str:
    """Write content to a temp dir and return the file path."""
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _make_conn_with_repo(root_path: str) -> kuzu.Connection:
    """Create an in-memory KuzuDB with a single Repo node pointing to root_path."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    db = kuzu.Database(db_path)
    conn = kuzu.Connection(db)
    init_schema(conn)
    repo_id = str(uuid.uuid4())
    conn.execute(
        "CREATE (:Repo {id: $id, name: $name, root_path: $rp})",
        {"id": repo_id, "name": "test-license-repo", "rp": root_path},
    )
    return conn


# ---------------------------------------------------------------------------
# Test 1: parse_pom_xml extracts compile-scoped dependencies correctly
# ---------------------------------------------------------------------------

def test_parse_pom_xml_extracts_deps():
    pom_path = _write_tmp_file(_SAMPLE_POM, "pom.xml")
    deps = parse_pom_xml(pom_path)
    groups = {(d["group"], d["artifact"]) for d in deps}

    assert ("org.springframework.boot", "spring-boot-starter-web") in groups
    assert ("com.google.guava", "guava") in groups
    # Verify version is captured
    sb_dep = next(d for d in deps if d["artifact"] == "spring-boot-starter-web")
    assert sb_dep["version"] == "3.2.0"


# ---------------------------------------------------------------------------
# Test 2: parse_pom_xml skips test-scoped and provided-scoped dependencies
# ---------------------------------------------------------------------------

def test_parse_pom_xml_skips_test_and_provided():
    pom_path = _write_tmp_file(_SAMPLE_POM, "pom.xml")
    deps = parse_pom_xml(pom_path)
    artifacts = {d["artifact"] for d in deps}

    assert "junit" not in artifacts, "test-scoped dep should be excluded"
    assert "mockito-core" not in artifacts, "provided-scoped dep should be excluded"


# ---------------------------------------------------------------------------
# Test 3: parse_gradle extracts compile dependencies from a Gradle file
# ---------------------------------------------------------------------------

def test_parse_gradle_extracts_deps():
    gradle_path = _write_tmp_file(_SAMPLE_GRADLE, "build.gradle")
    deps = parse_gradle(gradle_path)
    artifacts = {d["artifact"] for d in deps}

    assert "spring-boot-starter-web" in artifacts
    assert "guava" in artifacts
    assert "jackson-databind" in artifacts
    assert "lombok" in artifacts


# ---------------------------------------------------------------------------
# Test 4: parse_gradle skips testImplementation and testCompileOnly
# ---------------------------------------------------------------------------

def test_parse_gradle_skips_test_deps():
    gradle_path = _write_tmp_file(_SAMPLE_GRADLE, "build.gradle")
    deps = parse_gradle(gradle_path)
    artifacts = {d["artifact"] for d in deps}

    assert "junit" not in artifacts, "testImplementation should be excluded"
    assert "mockito-core" not in artifacts, "testCompileOnly should be excluded"
    assert "junit-platform-launcher" not in artifacts, "testRuntimeOnly should be excluded"


# ---------------------------------------------------------------------------
# Test 5: check_licenses marks GPL-3.0 as VIOLATION
# ---------------------------------------------------------------------------

def test_check_licenses_gpl_is_violation():
    deps = [{"group": "bad.lib", "artifact": "gpl-thing", "version": "1.0"}]
    results = check_licenses(
        deps,
        license_overrides={"bad.lib:gpl-thing": "GPL-3.0"},
    )
    assert len(results) == 1
    assert results[0]["status"] == "VIOLATION"
    assert results[0]["license"] == "GPL-3.0"


# ---------------------------------------------------------------------------
# Test 6: check_licenses marks Apache-2.0 as OK
# ---------------------------------------------------------------------------

def test_check_licenses_apache_is_ok():
    deps = [{"group": "org.springframework", "artifact": "spring-core", "version": "6.0"}]
    results = check_licenses(
        deps,
        license_overrides={"org.springframework:spring-core": "Apache-2.0"},
    )
    assert len(results) == 1
    assert results[0]["status"] == "OK"
    assert results[0]["license"] == "Apache-2.0"


# ---------------------------------------------------------------------------
# Test 7: check_licenses marks MPL-2.0 as WARNING
# ---------------------------------------------------------------------------

def test_check_licenses_mpl_is_warning():
    deps = [{"group": "mozilla", "artifact": "some-lib", "version": "2.0"}]
    results = check_licenses(
        deps,
        license_overrides={"mozilla:some-lib": "MPL-2.0"},
    )
    assert len(results) == 1
    assert results[0]["status"] == "WARNING"
    assert results[0]["license"] == "MPL-2.0"


# ---------------------------------------------------------------------------
# Test 8: check_licenses marks UNKNOWN license as UNKNOWN
# ---------------------------------------------------------------------------

def test_check_licenses_unknown():
    deps = [{"group": "mystery", "artifact": "black-box", "version": "0.1"}]
    results = check_licenses(
        deps,
        skip_lookup=True,
    )
    assert len(results) == 1
    assert results[0]["status"] == "UNKNOWN"
    assert results[0]["license"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# Test 9: custom allowed list overrides the default
# ---------------------------------------------------------------------------

def test_check_licenses_custom_allowed():
    deps = [{"group": "some", "artifact": "lib", "version": "1.0"}]
    # LGPL-2.1 is normally a WARNING, but if we explicitly allow it → OK
    custom_allowed = frozenset({"LGPL-2.1"})
    results = check_licenses(
        deps,
        allowed=custom_allowed,
        license_overrides={"some:lib": "LGPL-2.1"},
    )
    assert results[0]["status"] == "OK"


# ---------------------------------------------------------------------------
# Test 10: find_license_violations integration — uses a tmp pom.xml + injected conn
# ---------------------------------------------------------------------------

def test_find_license_violations_integration():
    """Write a small pom.xml to a temp dir, inject a KuzuDB conn pointing at it,
    then call find_license_violations with license_overrides so no network call is made.
    Verifies that VIOLATION and WARNING items are returned; OK items are filtered out.
    """
    tmpdir = tempfile.mkdtemp()
    pom_content = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.test</groupId>
    <artifactId>test-app</artifactId>
    <version>1.0</version>
    <dependencies>
        <dependency>
            <groupId>org.springframework</groupId>
            <artifactId>spring-core</artifactId>
            <version>6.0</version>
        </dependency>
        <dependency>
            <groupId>bad.gpl</groupId>
            <artifactId>gpl-lib</artifactId>
            <version>1.0</version>
        </dependency>
        <dependency>
            <groupId>mozilla</groupId>
            <artifactId>mpl-lib</artifactId>
            <version>2.0</version>
        </dependency>
    </dependencies>
</project>
"""
    pom_path = os.path.join(tmpdir, "pom.xml")
    with open(pom_path, "w", encoding="utf-8") as f:
        f.write(pom_content)

    conn = _make_conn_with_repo(tmpdir)

    overrides = {
        "org.springframework:spring-core": "Apache-2.0",
        "bad.gpl:gpl-lib": "GPL-3.0",
        "mozilla:mpl-lib": "MPL-2.0",
    }

    with patch.object(mcp_mod, "_conn", conn), patch.object(mcp_mod, "_db", conn):
        results = find_license_violations(
            "test-license-repo",
            license_overrides=overrides,
        )

    # Only VIOLATION and WARNING should be returned; Apache-2.0 (OK) filtered out
    statuses = {r["status"] for r in results}
    artifacts = {r["artifact"] for r in results}

    assert "spring-core" not in artifacts, "OK (Apache-2.0) item should be filtered out"
    assert "gpl-lib" in artifacts, "GPL-3.0 VIOLATION should be present"
    assert "mpl-lib" in artifacts, "MPL-2.0 WARNING should be present"
    assert "VIOLATION" in statuses
    assert "WARNING" in statuses
    assert "OK" not in statuses
