import json
import unittest

from aava_client import WorkflowRequest


class WorkflowRequestTests(unittest.TestCase):
    def test_safe_storage_payload_redacts_github_token(self) -> None:
        request = WorkflowRequest(
            pipeline_id="pipeline-1",
            priority="1",
            repo_url="https://github.com/example/repo",
            branch="main",
            target_python_version="3.14",
            github_token="REAL_GITHUB_TOKEN",
            target_branch="aava",
            commit_message="Apply migration",
        )

        payload = request.safe_storage_payload()
        github_config = json.loads(
            payload["userInputs"]["{{github_config_string_true}}"]
        )
        serialized = json.dumps(payload)

        self.assertEqual(payload["pipelineId"], "pipeline-1")
        self.assertEqual(payload["priority"], "1")
        self.assertEqual(github_config["github_token"], "********")
        self.assertEqual(github_config["target_branch"], "aava")
        self.assertNotIn("REAL_GITHUB_TOKEN", serialized)


if __name__ == "__main__":
    unittest.main()
