from __future__ import annotations

import kuzu

SCHEMA_VERSION: int = 2

_NODE_TABLES = [
    """CREATE NODE TABLE Repo(
        id STRING,
        name STRING,
        root_path STRING,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE File(
        id STRING,
        path STRING,
        language STRING,
        repo_id STRING,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE Class(
        id STRING,
        name STRING,
        fqn STRING,
        file_id STRING,
        repo_id STRING,
        is_interface BOOLEAN,
        annotations STRING[],
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE Method(
        id STRING,
        name STRING,
        fqn STRING,
        class_id STRING,
        file_id STRING,
        repo_id STRING,
        line_start INT64,
        is_suspend BOOLEAN,
        annotations STRING[],
        generated BOOLEAN,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE Endpoint(
        id STRING,
        http_method STRING,
        path STRING,
        path_regex STRING,
        handler_method_id STRING,
        repo_id STRING,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE RestCall(
        id STRING,
        http_method STRING,
        url_pattern STRING,
        callee_name STRING,
        caller_method_id STRING,
        repo_id STRING,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE EntityRelation(
        id STRING,
        source_class_id STRING,
        target_class_fqn STRING,
        field_name STRING,
        relation_type STRING,
        fetch_type STRING,
        repo_id STRING,
        PRIMARY KEY(id)
    )""",
]

_REL_TABLES = [
    "CREATE REL TABLE CALLS(FROM Method TO Method)",
    "CREATE REL TABLE CALLS_REST(FROM Method TO Endpoint)",
    "CREATE REL TABLE UNRESOLVED_CALL(FROM Method TO RestCall)",
    "CREATE REL TABLE CONTAINS_CLASS(FROM File TO Class)",
    "CREATE REL TABLE CONTAINS_METHOD(FROM Class TO Method)",
    "CREATE REL TABLE EXPOSES(FROM Repo TO Endpoint)",
    "CREATE REL TABLE DEPENDS_ON(FROM Repo TO Repo)",
    "CREATE REL TABLE EXTENDS(FROM Class TO Class)",
    "CREATE REL TABLE IMPLEMENTS(FROM Class TO Class)",
    "CREATE REL TABLE HAS_RELATION(FROM Class TO EntityRelation)",
]

_DROP_REL_TABLES = [
    "CALLS",
    "CALLS_REST",
    "UNRESOLVED_CALL",
    "CONTAINS_CLASS",
    "CONTAINS_METHOD",
    "EXPOSES",
    "DEPENDS_ON",
    "EXTENDS",
    "IMPLEMENTS",
    "HAS_RELATION",
]

_DROP_NODE_TABLES = [
    "EntityRelation",
    "RestCall",
    "Endpoint",
    "Method",
    "Class",
    "File",
    "Repo",
]


def create_schema(conn: kuzu.Connection) -> None:
    for ddl in _NODE_TABLES:
        conn.execute(ddl)
    for ddl in _REL_TABLES:
        conn.execute(ddl)


def drop_schema(conn: kuzu.Connection) -> None:
    for table in _DROP_REL_TABLES:
        try:
            conn.execute(f"DROP TABLE {table}")
        except Exception:
            pass
    for table in _DROP_NODE_TABLES:
        try:
            conn.execute(f"DROP TABLE {table}")
        except Exception:
            pass


def init_schema(conn: kuzu.Connection) -> None:
    drop_schema(conn)
    create_schema(conn)
