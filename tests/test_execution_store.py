import json
import tempfile
import unittest
import uuid
from pathlib import Path

from execution_store import (
    REDACTION_VALUE,
    ExecutionRecord,
    ExecutionStore,
)


def make_record(
    *,
    execution_name: str = "Repository Analyzer Agent",
    repo_url: str = "https://github.com/example/repo",
    request_json: dict | None = None,
    response_json: dict | None = None,
    output_markdown: str | None = "# Report\n",
) -> ExecutionRecord:
    return ExecutionRecord(
        execution_type="agent",
        execution_name=execution_name,
        target_id="45881",
        repo_url=repo_url,
        branch="main",
        target_python_version="3.14",
        target_branch=None,
        outcome="SUCCESS",
        aava_status="SUCCESS",
        http_status=200,
        elapsed_seconds=1.23,
        request_json=request_json or {
            "agentId": "45881",
            "userInputs": {
                "repo_url": repo_url,
            },
        },
        response_json=response_json or {
            "status": "SUCCESS",
            "data": {
                "agentResponse": {
                    "agent": {
                        "output": output_markdown,
                    },
                },
            },
        },
        aava_execution_id="execution-123",
        job_id=None,
        output_markdown=output_markdown,
        error_message=None,
    )


class ExecutionStoreTests(unittest.TestCase):
    def make_store(self, temp_dir: str) -> ExecutionStore:
        return ExecutionStore(
            str(Path(temp_dir) / "history" / "executions.duckdb")
        )

    def test_database_creation_creates_file_and_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "history" / "executions.duckdb"
            store = ExecutionStore(str(database_path))

            self.assertTrue(database_path.exists())
            self.assertEqual(store.list_executions(), [])

    def test_save_successful_record_and_retrieve(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)

            record_id = store.save_execution(make_record())
            loaded = store.get_execution(record_id)

            self.assertEqual(str(uuid.UUID(record_id)), record_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["execution_name"], "Repository Analyzer Agent")
            self.assertEqual(loaded["outcome"], "SUCCESS")

    def test_list_executions_returns_newest_first_without_large_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)

            first_id = store.save_execution(
                make_record(execution_name="First")
            )
            second_id = store.save_execution(
                make_record(execution_name="Second")
            )

            rows = store.list_executions()

            self.assertEqual(rows[0]["record_id"], second_id)
            self.assertEqual(rows[1]["record_id"], first_id)
            self.assertNotIn("request_json", rows[0])
            self.assertNotIn("response_json", rows[0])
            self.assertNotIn("output_markdown", rows[0])

    def test_json_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            request_json = {
                "outer": {
                    "inner": [
                        {
                            "value": "kept",
                        }
                    ],
                },
            }
            response_json = {
                "data": {
                    "items": [
                        1,
                        2,
                        3,
                    ],
                },
            }

            record_id = store.save_execution(
                make_record(
                    request_json=request_json,
                    response_json=response_json,
                )
            )
            loaded = store.get_execution(record_id)

            self.assertEqual(loaded["request_json"], request_json)
            self.assertEqual(loaded["response_json"], response_json)

    def test_sensitive_key_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)

            record_id = store.save_execution(
                make_record(
                    request_json={
                        "github_token": "REAL_GITHUB",
                        "authorization": "Bearer REAL_AAVA",
                        "nested": {
                            "access_token": "REAL_ACCESS",
                        },
                    },
                )
            )
            loaded = store.get_execution(record_id)

            self.assertEqual(
                loaded["request_json"]["github_token"],
                REDACTION_VALUE,
            )
            self.assertEqual(
                loaded["request_json"]["authorization"],
                REDACTION_VALUE,
            )
            self.assertEqual(
                loaded["request_json"]["nested"]["access_token"],
                REDACTION_VALUE,
            )

    def test_nested_github_config_string_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            request_json = {
                "{{github_config_string_true}}": (
                    "{\"github_token\":\"REAL_SECRET\","
                    "\"target_branch\":\"aava\"}"
                )
            }

            record_id = store.save_execution(
                make_record(request_json=request_json)
            )
            loaded = store.get_execution(record_id)
            serialized = json.dumps(loaded["request_json"])
            redacted_config = json.loads(
                loaded["request_json"]["{{github_config_string_true}}"]
            )

            self.assertNotIn("REAL_SECRET", serialized)
            self.assertEqual(
                redacted_config["github_token"],
                REDACTION_VALUE,
            )
            self.assertEqual(redacted_config["target_branch"], "aava")

    def test_known_secret_value_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)

            record_id = store.save_execution(
                make_record(
                    request_json={
                        "message": "prefix REAL_SECRET suffix",
                    },
                    response_json={
                        "message": "REAL_SECRET",
                    },
                ),
                secret_values=("REAL_SECRET",),
            )
            loaded = store.get_execution(record_id)
            serialized = json.dumps(
                {
                    "request": loaded["request_json"],
                    "response": loaded["response_json"],
                }
            )

            self.assertNotIn("REAL_SECRET", serialized)
            self.assertIn(REDACTION_VALUE, serialized)

    def test_agent_markdown_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            markdown = "# Report\n\n- Preserve **exactly**.\n"

            record_id = store.save_execution(
                make_record(output_markdown=markdown)
            )
            loaded = store.get_execution(record_id)

            self.assertEqual(loaded["output_markdown"], markdown)

    def test_missing_record_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)

            self.assertIsNone(store.get_execution("missing-record"))

    def test_parameter_safety_for_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            record_id = store.save_execution(
                make_record(
                    repo_url="https://github.com/example/repo'o",
                    request_json={
                        "quoted": "value with ' single quote",
                    },
                )
            )

            loaded = store.get_execution(record_id)
            missing = store.get_execution("not-real' OR '1'='1")

            self.assertEqual(
                loaded["request_json"]["quoted"],
                "value with ' single quote",
            )
            self.assertIsNone(missing)


if __name__ == "__main__":
    unittest.main()
