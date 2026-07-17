import unittest
import uuid
from unittest.mock import patch

from aava_agent_client import (
    AavaAgentClient,
    AgentExecutionRequest,
    extract_agent_output,
)


class AgentExecutionRequestTests(unittest.TestCase):
    def test_to_payload_contains_required_fields(self) -> None:
        request = AgentExecutionRequest(
            agent_id="45881",
            user="user@example.com",
            user_inputs={
                "repo_url": "https://github.com/example/repo",
            },
        )

        payload = request.to_payload()

        self.assertEqual(payload["agentId"], "45881")
        self.assertEqual(payload["executionId"], request.execution_id)
        self.assertEqual(payload["user"], "user@example.com")
        self.assertEqual(
            payload["userInputs"],
            {
                "repo_url": "https://github.com/example/repo",
            },
        )

    def test_execution_ids_are_unique_valid_uuids(self) -> None:
        first = AgentExecutionRequest(
            agent_id="45881",
            user="user@example.com",
            user_inputs={},
        )
        second = AgentExecutionRequest(
            agent_id="45881",
            user="user@example.com",
            user_inputs={},
        )

        self.assertNotEqual(first.execution_id, second.execution_id)
        self.assertEqual(str(uuid.UUID(first.execution_id)), first.execution_id)
        self.assertEqual(
            str(uuid.UUID(second.execution_id)),
            second.execution_id,
        )

    def test_repository_analyzer_payload_keys_are_exact(self) -> None:
        request = AgentExecutionRequest(
            agent_id="45881",
            user="user@example.com",
            user_inputs={
                "repo_url": "https://github.com/example/repo",
                "branch": "main",
                "target_python_version": "3.14",
            },
        )

        user_inputs = request.to_payload()["userInputs"]

        self.assertEqual(
            set(user_inputs.keys()),
            {
                "repo_url",
                "branch",
                "target_python_version",
            },
        )
        self.assertNotIn("{{repo_url_string_true}}", user_inputs)

    def test_python_migration_payload_keys_are_exact(self) -> None:
        markdown = "# Analysis\n\n- Keep this exactly."
        request = AgentExecutionRequest(
            agent_id="45878",
            user="user@example.com",
            user_inputs={
                "repo_analyzer_output": markdown,
                "target_python_version": "3.14",
            },
        )

        user_inputs = request.to_payload()["userInputs"]

        self.assertEqual(
            set(user_inputs.keys()),
            {
                "repo_analyzer_output",
                "target_python_version",
            },
        )
        self.assertEqual(user_inputs["repo_analyzer_output"], markdown)

    def test_safe_preview_contains_no_real_bearer_token(self) -> None:
        bearer_value = "unit-test-bearer-value"
        request = AgentExecutionRequest(
            agent_id="45881",
            user="user@example.com",
            user_inputs={},
        )

        preview = request.safe_preview()

        self.assertEqual(preview["authorization"], "Bearer ********")
        self.assertNotIn(bearer_value, str(preview))


class ExtractAgentOutputTests(unittest.TestCase):
    def test_extract_agent_output_success(self) -> None:
        response_body = {
            "data": {
                "agentResponse": {
                    "output": "# Final Markdown",
                    "ignored": "not returned",
                }
            }
        }

        self.assertEqual(
            extract_agent_output(response_body),
            "# Final Markdown",
        )

    def test_extract_agent_output_top_level_success(self) -> None:
        response_body = {
            "status": "SUCCESS",
            "output": "# Top-level Markdown",
        }

        self.assertEqual(
            extract_agent_output(response_body),
            "# Top-level Markdown",
        )

    def test_extract_agent_output_data_output_success(self) -> None:
        response_body = {
            "status": "SUCCESS",
            "data": {
                "output": "# Data Markdown",
            },
        }

        self.assertEqual(
            extract_agent_output(response_body),
            "# Data Markdown",
        )

    def test_extract_agent_output_nested_agent_output_success(self) -> None:
        response_body = {
            "status": "SUCCESS",
            "data": {
                "agentResponse": {
                    "agent": {
                        "output": "# Nested Agent Markdown",
                    },
                },
            },
        }

        self.assertEqual(
            extract_agent_output(response_body),
            "# Nested Agent Markdown",
        )

    def test_extract_agent_output_missing_output_raises(self) -> None:
        response_body = {
            "data": {
                "agentResponse": {},
            }
        }

        with self.assertRaises(ValueError):
            extract_agent_output(response_body)

    def test_extract_agent_output_empty_output_raises(self) -> None:
        response_body = {
            "data": {
                "agentResponse": {
                    "output": "   ",
                }
            }
        }

        with self.assertRaises(ValueError):
            extract_agent_output(response_body)

    def test_extract_agent_output_wrong_response_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            extract_agent_output(["not", "a", "dict"])


class AavaAgentClientTests(unittest.TestCase):
    @patch("aava_agent_client.requests.post")
    def test_execute_posts_json_payload(self, mock_post) -> None:
        request = AgentExecutionRequest(
            agent_id="45881",
            user="user@example.com",
            user_inputs={
                "repo_url": "https://github.com/example/repo",
            },
        )
        client = AavaAgentClient(
            agent_execution_url="https://aava.example/agents/execute",
            bearer_token="unit-test-bearer-value",
            timeout_seconds=123,
        )

        response = client.execute(request)

        self.assertEqual(response, mock_post.return_value)
        mock_post.assert_called_once_with(
            "https://aava.example/agents/execute",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Authorization": "Bearer unit-test-bearer-value",
            },
            json=request.to_payload(),
            timeout=123,
        )


if __name__ == "__main__":
    unittest.main()
