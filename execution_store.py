import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


REDACTION_VALUE = "********"
SENSITIVE_KEY_NAMES = {
    "authorization",
    "githubtoken",
    "githubpersonalaccesstoken",
    "personalaccesstoken",
    "accesstoken",
    "bearertoken",
    "apitoken",
    "token",
}


@dataclass(frozen=True)
class ExecutionRecord:
    """
    Stores one attempted AAVA workflow or agent execution.
    """

    execution_type: str
    execution_name: str
    target_id: str | None
    repo_url: str | None
    branch: str | None
    target_python_version: str | None
    target_branch: str | None
    outcome: str
    aava_status: str | None
    http_status: int | None
    elapsed_seconds: float | None
    request_json: dict[str, Any]
    response_json: dict[str, Any]
    aava_execution_id: str | None
    job_id: str | None
    output_markdown: str | None
    error_message: str | None


def normalize_key(key: object) -> str:
    """Normalize a key for sensitive-name matching."""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: object) -> bool:
    """Return whether a key name should have its value redacted."""
    normalized = normalize_key(key)
    return any(name in normalized for name in SENSITIVE_KEY_NAMES)


def replace_known_secrets(value: str, secret_values: tuple[str, ...]) -> str:
    """Replace exact known secret occurrences inside a string."""
    redacted = value

    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, REDACTION_VALUE)

    return redacted


def redact_sensitive_data(
    value: Any,
    secret_values: tuple[str, ...] = (),
) -> Any:
    """
    Return a redacted copy of nested data without mutating the original.

    JSON-formatted strings are parsed, recursively redacted, and serialized
    back to JSON so nested workflow configuration strings are sanitized too.
    """
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}

        for key, nested_value in value.items():
            if is_sensitive_key(key):
                sanitized[key] = REDACTION_VALUE
            else:
                sanitized[key] = redact_sensitive_data(
                    nested_value,
                    secret_values,
                )

        return sanitized

    if isinstance(value, list):
        return [
            redact_sensitive_data(
                item,
                secret_values,
            )
            for item in value
        ]

    if isinstance(value, str):
        stripped = value.strip()

        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return replace_known_secrets(value, secret_values)

            redacted = redact_sensitive_data(parsed, secret_values)
            return json.dumps(redacted, ensure_ascii=False, default=str)

        return replace_known_secrets(value, secret_values)

    return value


def json_dumps(value: Any) -> str:
    """Serialize data for DuckDB JSON columns."""
    return json.dumps(value, ensure_ascii=False, default=str)


def json_loads(value: Any) -> dict[str, Any]:
    """Load JSON data returned by DuckDB back into a dictionary."""
    if isinstance(value, dict):
        return value

    if value is None:
        return {}

    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, dict) else {}


class ExecutionStore:
    """
    Local DuckDB-backed storage for AAVA execution history.
    """

    def __init__(
        self,
        database_path: str,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._lock = threading.Lock()
        self._create_table()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        """Open a short-lived DuckDB connection."""
        return duckdb.connect(str(self.database_path))

    def _create_table(self) -> None:
        """Create the execution_runs table if it does not exist."""
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS execution_runs (
                        record_id VARCHAR PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL,

                        execution_type VARCHAR NOT NULL,
                        execution_name VARCHAR NOT NULL,
                        target_id VARCHAR,

                        aava_execution_id VARCHAR,
                        job_id VARCHAR,

                        repo_url VARCHAR,
                        branch VARCHAR,
                        target_python_version VARCHAR,
                        target_branch VARCHAR,

                        outcome VARCHAR NOT NULL,
                        aava_status VARCHAR,
                        http_status INTEGER,
                        elapsed_seconds DOUBLE,

                        request_json JSON NOT NULL,
                        response_json JSON NOT NULL,

                        output_markdown VARCHAR,
                        error_message VARCHAR
                    );
                    """
                )

    def save_execution(
        self,
        record: ExecutionRecord,
        secret_values: tuple[str, ...] = (),
    ) -> str:
        """
        Sanitize and persist an execution record.
        """
        record_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc)
        request_json = redact_sensitive_data(
            record.request_json,
            secret_values,
        )
        response_json = redact_sensitive_data(
            record.response_json,
            secret_values,
        )
        output_markdown = (
            replace_known_secrets(record.output_markdown, secret_values)
            if record.output_markdown is not None
            else None
        )
        error_message = (
            replace_known_secrets(record.error_message, secret_values)
            if record.error_message is not None
            else None
        )

        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO execution_runs (
                        record_id,
                        created_at,
                        execution_type,
                        execution_name,
                        target_id,
                        aava_execution_id,
                        job_id,
                        repo_url,
                        branch,
                        target_python_version,
                        target_branch,
                        outcome,
                        aava_status,
                        http_status,
                        elapsed_seconds,
                        request_json,
                        response_json,
                        output_markdown,
                        error_message
                    )
                    VALUES (
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        CAST(? AS JSON),
                        CAST(? AS JSON),
                        ?,
                        ?
                    );
                    """,
                    [
                        record_id,
                        created_at,
                        record.execution_type,
                        record.execution_name,
                        record.target_id,
                        record.aava_execution_id,
                        record.job_id,
                        record.repo_url,
                        record.branch,
                        record.target_python_version,
                        record.target_branch,
                        record.outcome,
                        record.aava_status,
                        record.http_status,
                        record.elapsed_seconds,
                        json_dumps(request_json),
                        json_dumps(response_json),
                        output_markdown,
                        error_message,
                    ],
                )

        return record_id

    def list_executions(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Return newest execution summaries without large JSON fields.
        """
        safe_limit = max(
            1,
            min(
                int(limit),
                1000,
            ),
        )

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    record_id,
                    CAST(created_at AS VARCHAR) AS created_at,
                    execution_type,
                    execution_name,
                    repo_url,
                    branch,
                    target_python_version,
                    target_branch,
                    outcome,
                    aava_status,
                    http_status,
                    elapsed_seconds,
                    aava_execution_id,
                    job_id
                FROM execution_runs
                ORDER BY created_at DESC
                LIMIT ?;
                """,
                [safe_limit],
            ).fetchall()

        columns = [
            "record_id",
            "created_at",
            "execution_type",
            "execution_name",
            "repo_url",
            "branch",
            "target_python_version",
            "target_branch",
            "outcome",
            "aava_status",
            "http_status",
            "elapsed_seconds",
            "aava_execution_id",
            "job_id",
        ]

        return [
            dict(zip(columns, row, strict=True))
            for row in rows
        ]

    def get_execution(
        self,
        record_id: str,
    ) -> dict[str, Any] | None:
        """
        Return one complete execution record by ID.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    record_id,
                    CAST(created_at AS VARCHAR) AS created_at,
                    execution_type,
                    execution_name,
                    target_id,
                    aava_execution_id,
                    job_id,
                    repo_url,
                    branch,
                    target_python_version,
                    target_branch,
                    outcome,
                    aava_status,
                    http_status,
                    elapsed_seconds,
                    request_json,
                    response_json,
                    output_markdown,
                    error_message
                FROM execution_runs
                WHERE record_id = ?;
                """,
                [record_id],
            ).fetchone()

        if row is None:
            return None

        columns = [
            "record_id",
            "created_at",
            "execution_type",
            "execution_name",
            "target_id",
            "aava_execution_id",
            "job_id",
            "repo_url",
            "branch",
            "target_python_version",
            "target_branch",
            "outcome",
            "aava_status",
            "http_status",
            "elapsed_seconds",
            "request_json",
            "response_json",
            "output_markdown",
            "error_message",
        ]
        record = dict(zip(columns, row, strict=True))
        record["request_json"] = json_loads(record["request_json"])
        record["response_json"] = json_loads(record["response_json"])

        return record
