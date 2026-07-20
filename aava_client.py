import json
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class WorkflowRequest:
    """
    Stores all values needed to execute the AAVA workflow.
    """

    pipeline_id: str
    priority: str

    repo_url: str
    branch: str
    target_python_version: str

    github_token: str
    target_branch: str
    commit_message: str

    def build_user_inputs(self) -> dict[str, str]:
        """
        Build the exact userInputs structure used by the working
        Postman request.

        The GitHub configuration must be converted into a JSON string
        because AAVA expects the github_config workflow input to be text.
        """
        github_config = {
            "github_token": self.github_token,
            "target_branch": self.target_branch,
            "commit_message": self.commit_message,
        }

        user_inputs = {
            "{{repo_url_string_true}}": self.repo_url,
            "{{branch_string_true}}": self.branch,
            "{{target_python_version_string_true}}": (
                self.target_python_version
            ),

            "{{repo_analyzer_output_string_true}}": "",
            "{{repository_string_true}}": "",
            "{{migration_string_true}}": "",
            "{{target_string_true}}": "",
            "{{codebase_bundle_string_true}}": "",
            "{{base_branch_string_true}}": "",

            "{{github_config_string_true}}": json.dumps(
                github_config
            ),

            "{{migration_agent_output_string_true}}": "",
        }

        return user_inputs

    def multipart_fields(
        self,
    ) -> list[tuple[str, tuple[None, str]]]:
        """
        Create the exact three multipart/form-data fields required
        by the AAVA workflow endpoint:

        - pipelineId
        - userInputs
        - priority

        Using `(None, value)` makes Requests treat each multipart
        part as a normal text field rather than a file upload.
        """
        user_inputs_json = json.dumps(
            self.build_user_inputs()
        )

        return [
            (
                "pipelineId",
                (
                    None,
                    str(self.pipeline_id),
                ),
            ),
            (
                "userInputs",
                (
                    None,
                    user_inputs_json,
                ),
            ),
            (
                "priority",
                (
                    None,
                    str(self.priority),
                ),
            ),
        ]

    def safe_preview(self) -> dict[str, object]:
        """
        Create a request preview that is safe to display in the UI.

        Both the AAVA bearer token and GitHub personal access token
        are hidden.
        """
        storage_payload = self.safe_storage_payload()
        user_inputs = dict(storage_payload["userInputs"])
        github_config_string = user_inputs[
            "{{github_config_string_true}}"
        ]
        user_inputs[
            "{{github_config_string_true}}"
        ] = json.dumps(
            json.loads(github_config_string),
            indent=2,
        )
        preview_payload = {
            **storage_payload,
            "userInputs": user_inputs,
        }

        return {
            "method": "POST",
            "content_type": "multipart/form-data",
            "form-data": preview_payload,
            "authorization": "Bearer ********",
        }

    def safe_storage_payload(self) -> dict[str, object]:
        """
        Create the logical workflow request with secrets redacted.
        """
        user_inputs = self.build_user_inputs()

        github_config_string = user_inputs[
            "{{github_config_string_true}}"
        ]

        github_config = json.loads(
            github_config_string
        )

        github_config["github_token"] = "********"

        user_inputs[
            "{{github_config_string_true}}"
        ] = json.dumps(
            github_config
        )

        return {
            "pipelineId": str(
                self.pipeline_id
            ),
            "userInputs": user_inputs,
            "priority": str(
                self.priority
            ),
        }


class AavaWorkflowClient:
    """
    Sends workflow execution requests to AAVA.
    """

    def __init__(
        self,
        workflow_url: str,
        bearer_token: str,
        timeout_seconds: int = 180,
    ) -> None:
        self.workflow_url = workflow_url
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds

    def execute(
        self,
        workflow_request: WorkflowRequest,
    ) -> requests.Response:
        """
        Submit the workflow to AAVA using the same multipart/form-data
        structure as the working Postman request.
        """
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Authorization": (
                f"Bearer {self.bearer_token}"
            ),
        }

        response = requests.post(
            self.workflow_url,
            headers=headers,
            files=workflow_request.multipart_fields(),
            timeout=self.timeout_seconds,
        )

        return response
