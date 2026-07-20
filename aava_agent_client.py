import uuid
from dataclasses import dataclass, field

import requests


def find_output_paths(value: object, prefix: str = "") -> list[str]:
    """
    Find response paths named output without returning their values.
    """
    paths: list[str] = []

    if isinstance(value, dict):
        for key, nested_value in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key == "output":
                paths.append(path)
            paths.extend(find_output_paths(nested_value, path))
    elif isinstance(value, list):
        for index, nested_value in enumerate(value):
            path = f"{prefix}[{index}]"
            paths.extend(find_output_paths(nested_value, path))

    return paths


def extract_agent_output(response_body: dict) -> str:
    """
    Extract the final Markdown output from an AAVA agent response.

    The documented response shape uses data.agentResponse.output, while
    some AAVA agent responses return the final Markdown at top-level output.
    """
    if not isinstance(response_body, dict):
        raise ValueError("AAVA response body must be a dictionary.")

    output = None
    output_path = ""

    data = response_body.get("data")
    if isinstance(data, dict):
        agent_response = data.get("agentResponse")
        if isinstance(agent_response, dict) and "output" in agent_response:
            output = agent_response["output"]
            output_path = "data.agentResponse.output"
        elif isinstance(agent_response, dict):
            agent = agent_response.get("agent")
            if isinstance(agent, dict) and "output" in agent:
                output = agent["output"]
                output_path = "data.agentResponse.agent.output"

        if output is None and "output" in data:
            output = data["output"]
            output_path = "data.output"

    if output is None and "output" in response_body:
        output = response_body["output"]
        output_path = "output"

    if output is None:
        output_paths = find_output_paths(response_body)
        path_hint = (
            f" Found output paths: {', '.join(output_paths)}."
            if output_paths
            else ""
        )
        raise ValueError(
            "AAVA response is missing output. Expected either "
            "data.agentResponse.output, data.agentResponse.agent.output, "
            "data.output, or top-level output."
            f"{path_hint}"
        )

    if not isinstance(output, str):
        raise ValueError(f"AAVA {output_path} must be a string.")

    if not output.strip():
        raise ValueError(f"AAVA {output_path} is empty.")

    return output


@dataclass(frozen=True)
class AgentExecutionRequest:
    """
    Stores all values needed to execute a single AAVA agent.
    """

    agent_id: str
    user: str
    user_inputs: dict[str, str]
    execution_id: str = field(
        default_factory=lambda: str(uuid.uuid4())
    )

    def to_payload(self) -> dict[str, object]:
        """
        Create the JSON payload expected by the AAVA agent endpoint.
        """
        return {
            "agentId": self.agent_id,
            "executionId": self.execution_id,
            "user": self.user,
            "userInputs": self.user_inputs,
        }

    def safe_preview(self) -> dict[str, object]:
        """
        Create a request preview without exposing authorization.
        """
        return {
            "method": "POST",
            "content_type": "application/json",
            "json": self.to_payload(),
            "authorization": "Bearer ********",
        }


class AavaAgentClient:
    """
    Sends synchronous single-agent execution requests to AAVA.
    """

    def __init__(
        self,
        agent_execution_url: str,
        bearer_token: str,
        timeout_seconds: int = 360,
    ) -> None:
        self.agent_execution_url = agent_execution_url
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds

    def execute(
        self,
        request: AgentExecutionRequest,
    ) -> requests.Response:
        """
        Submit an AAVA agent request as JSON and wait for completion.
        """
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {self.bearer_token}",
        }

        response = requests.post(
            self.agent_execution_url,
            headers=headers,
            json=request.to_payload(),
            timeout=self.timeout_seconds,
        )

        return response
