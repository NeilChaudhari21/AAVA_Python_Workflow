import re
import time
from urllib.parse import urlparse

import requests
import streamlit as st

from aava_agent_client import (
    AavaAgentClient,
    AgentExecutionRequest,
    extract_agent_output,
)
from aava_client import AavaWorkflowClient, WorkflowRequest


DEFAULT_REPO_URL = "https://github.com/NeilChaudhari21/python_demo"


st.set_page_config(
    page_title="AAVA Python Migration",
    page_icon=":snake:",
    layout="centered",
)

st.markdown(
    """
    <style>
        .block-container {
            max-width: 900px;
            padding-top: 2rem;
            padding-bottom: 3rem;
        }

        [data-testid="stForm"] {
            border: 1px solid rgba(128, 128, 128, 0.25);
            border-radius: 14px;
            padding: 1.25rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def required_secret(name: str) -> str:
    """
    Read a required value from Streamlit secrets.

    The application stops with a useful message when the value
    is missing or empty.
    """
    try:
        value = str(st.secrets[name]).strip()
    except KeyError:
        st.error(
            f"Missing `{name}` in `.streamlit/secrets.toml`. "
            "Copy `.streamlit/secrets.toml.example` to "
            "`.streamlit/secrets.toml` and fill in the values."
        )
        st.stop()

    if not value:
        st.error(
            f"`{name}` cannot be empty in `.streamlit/secrets.toml`."
        )
        st.stop()

    return value


def is_valid_http_url(value: str) -> bool:
    """Check that the repository URL uses HTTP or HTTPS."""
    parsed = urlparse(value)

    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
    )


def is_valid_python_version(value: str) -> bool:
    """
    Accept versions such as 3.12, 3.14, or 3.14.1.
    """
    return bool(
        re.fullmatch(
            r"\d+\.\d+(?:\.\d+)?",
            value,
        )
    )


def initialize_session_state() -> None:
    """Initialize report state without erasing successful runs."""
    defaults = {
        "repo_analyzer_output": "",
        "repo_analyzer_elapsed_seconds": None,
        "repo_analyzer_execution_id": "",
        "repo_analyzer_repo_url": "",
        "repo_analyzer_branch": "",
        "repo_analyzer_target_version": "",
        "python_migration_output": "",
        "python_migration_elapsed_seconds": None,
        "python_migration_execution_id": "",
        "python_migration_target_version": "",
    }

    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def clear_documentation_results() -> None:
    """Clear only standalone-agent report outputs and metadata."""
    st.session_state["repo_analyzer_output"] = ""
    st.session_state["repo_analyzer_elapsed_seconds"] = None
    st.session_state["repo_analyzer_execution_id"] = ""
    st.session_state["repo_analyzer_repo_url"] = ""
    st.session_state["repo_analyzer_branch"] = ""
    st.session_state["repo_analyzer_target_version"] = ""
    st.session_state["python_migration_output"] = ""
    st.session_state["python_migration_elapsed_seconds"] = None
    st.session_state["python_migration_execution_id"] = ""
    st.session_state["python_migration_target_version"] = ""


def display_validation_errors(validation_errors: list[str]) -> bool:
    """Render validation errors and report whether any were found."""
    if not validation_errors:
        return False

    for error in validation_errors:
        st.error(error)

    return True


def parse_json_response(response: requests.Response) -> dict:
    """Parse an AAVA response as JSON with a UI-safe error message."""
    try:
        response_body = response.json()
    except ValueError:
        st.error("AAVA returned a non-JSON response.")
        st.stop()

    if not isinstance(response_body, dict):
        st.error("AAVA returned JSON, but the response body is not an object.")
        st.stop()

    return response_body


def require_successful_agent_response(
    response: requests.Response,
    response_body: dict,
) -> None:
    """Validate HTTP status and top-level AAVA status."""
    if not response.ok:
        server_message = response.text.strip()
        if len(server_message) > 800:
            server_message = server_message[:800] + "..."

        st.error(f"AAVA returned HTTP {response.status_code}.")
        if server_message:
            st.code(server_message)
        st.stop()

    api_status = response_body.get("status")
    if api_status is not None and api_status != "SUCCESS":
        st.error(f"AAVA agent execution returned status `{api_status}`.")
        st.stop()


def run_agent_request(
    request: AgentExecutionRequest,
    spinner_message: str,
) -> tuple[str, float, dict]:
    """Execute an agent request and return its Markdown output."""
    started_at = time.perf_counter()

    with st.spinner(spinner_message):
        try:
            response = agent_client.execute(request)
        except requests.Timeout:
            st.error(
                "The AAVA agent request timed out before the completed "
                "response was returned."
            )
            st.stop()
        except requests.RequestException as error:
            st.error(f"Could not connect to AAVA agent endpoint: {error}")
            st.stop()

    elapsed_seconds = time.perf_counter() - started_at

    if not response.ok:
        server_message = response.text.strip()
        if len(server_message) > 800:
            server_message = server_message[:800] + "..."

        st.error(f"AAVA returned HTTP {response.status_code}.")
        if server_message:
            st.code(server_message)
        st.stop()

    response_body = parse_json_response(response)
    require_successful_agent_response(response, response_body)

    try:
        output = extract_agent_output(response_body)
    except ValueError as error:
        st.error(str(error))
        st.stop()

    return output, elapsed_seconds, response_body


def render_report_result(
    *,
    success_message: str,
    elapsed_seconds: float | None,
    execution_id: str,
    output: str,
    download_label: str,
    download_file_name: str,
) -> None:
    """Render standalone-agent metadata, Markdown, and download action."""
    st.success(success_message)

    meta_columns = st.columns(2)
    meta_columns[0].metric(
        "Elapsed",
        (
            f"{elapsed_seconds:.1f}s"
            if elapsed_seconds is not None
            else "Not recorded"
        ),
    )
    meta_columns[1].write("**Execution ID**")
    meta_columns[1].code(execution_id or "Not recorded")

    st.download_button(
        download_label,
        data=output,
        file_name=download_file_name,
        mime="text/markdown",
        use_container_width=True,
    )

    st.markdown(output)


def run_full_workflow_tab() -> None:
    """Render and submit the existing full-workflow form."""
    with st.form(
        "workflow_form",
        clear_on_submit=False,
    ):
        st.subheader("Repository")

        repo_url = st.text_input(
            "GitHub repository URL",
            value=DEFAULT_REPO_URL,
            placeholder="https://github.com/owner/repository",
        )

        left_column, right_column = st.columns(2)

        with left_column:
            branch = st.text_input(
                "Source branch",
                value="main",
            )

        with right_column:
            target_python_version = st.text_input(
                "Target Python version",
                value="3.14",
                help="Examples: 3.12, 3.13, or 3.14",
            )

        st.subheader("GitHub output")

        github_token = st.text_input(
            "GitHub personal access token",
            type="password",
            help=(
                "Use a token that can read the source repository and "
                "create or update the target branch."
            ),
        )

        target_branch = st.text_input(
            "Target branch",
            value="aava",
            help=(
                "The branch where the workflow writes "
                "the migrated files."
            ),
        )

        commit_message = st.text_input(
            "Commit message",
            value="Apply Python migration updates from UI",
        )

        show_preview = st.checkbox(
            "Show request preview after submitting",
            value=True,
        )

        submitted = st.form_submit_button(
            "Run workflow",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    # Remove accidental leading and trailing spaces.
    repo_url = repo_url.strip()
    branch = branch.strip()
    target_python_version = target_python_version.strip()
    github_token = github_token.strip()
    target_branch = target_branch.strip()
    commit_message = commit_message.strip()

    validation_errors: list[str] = []

    if not is_valid_http_url(repo_url):
        validation_errors.append(
            "Enter a valid HTTP or HTTPS repository URL."
        )

    if not branch:
        validation_errors.append(
            "Source branch is required."
        )

    if not is_valid_python_version(target_python_version):
        validation_errors.append(
            "Target Python version must look like `3.14` "
            "or `3.14.1`."
        )

    if not github_token:
        validation_errors.append(
            "GitHub personal access token is required."
        )

    if not target_branch:
        validation_errors.append(
            "Target branch is required."
        )

    if not commit_message:
        validation_errors.append(
            "Commit message is required."
        )

    if display_validation_errors(validation_errors):
        st.stop()

    # Build the workflow request using the values entered in the UI.
    workflow_request = WorkflowRequest(
        pipeline_id=pipeline_id,
        priority=priority,
        repo_url=repo_url,
        branch=branch,
        target_python_version=target_python_version,
        github_token=github_token,
        target_branch=target_branch,
        commit_message=commit_message,
    )

    # Display the outgoing request without exposing either token.
    if show_preview:
        with st.expander(
            "Request preview",
            expanded=False,
        ):
            st.json(
                workflow_request.safe_preview()
            )

    # Send the workflow request to AAVA.
    with st.spinner(
        "Submitting the workflow to AAVA..."
    ):
        try:
            response = workflow_client.execute(
                workflow_request
            )
        except Exception as error:
            st.error(
                f"Could not submit the workflow: {error}"
            )
            st.stop()

    # Attempt to parse the AAVA response as JSON.
    try:
        response_body = response.json()
    except ValueError:
        response_body = {
            "raw_response": response.text
        }

    if response.ok:
        st.success(
            "Workflow submitted successfully."
        )

        if isinstance(response_body, dict):
            response_data = response_body.get(
                "data",
                {},
            )

            api_status = response_body.get(
                "status",
                "SUCCESS",
            )
        else:
            response_data = {}
            api_status = "SUCCESS"

        workflow_execution_id = response_data.get(
            "workflowExecutionId"
        )

        job_id = response_data.get(
            "jobId"
        )

        status_column, api_column, job_column = st.columns(3)

        status_column.metric(
            "HTTP",
            response.status_code,
        )

        api_column.metric(
            "API status",
            str(api_status),
        )

        job_column.metric(
            "Job ID",
            str(job_id or "Not returned"),
        )

        if workflow_execution_id:
            st.write(
                "**Workflow execution ID**"
            )

            st.code(
                str(workflow_execution_id)
            )

        with st.expander(
            "Complete AAVA response",
            expanded=True,
        ):
            st.json(response_body)

        st.info(
            "The API accepted the workflow. Confirm final completion "
            "in the AAVA execution history and check the target "
            "GitHub branch."
        )

    else:
        st.error(
            f"AAVA returned HTTP {response.status_code}."
        )

        with st.expander(
            "Error response",
            expanded=True,
        ):
            st.json(response_body)


def run_repository_analyzer_tab() -> None:
    """Render and submit the standalone Repository Analyzer form."""
    with st.form(
        "repository_analyzer_form",
        clear_on_submit=False,
    ):
        st.subheader("Repository Analyzer")

        repo_url = st.text_input(
            "GitHub repository URL",
            value=DEFAULT_REPO_URL,
            placeholder="https://github.com/owner/repository",
            key="repo_analyzer_repo_url_input",
        )

        left_column, right_column = st.columns(2)

        with left_column:
            branch = st.text_input(
                "Source branch",
                value="main",
                key="repo_analyzer_branch_input",
            )

        with right_column:
            target_python_version = st.text_input(
                "Target Python version",
                value="3.14",
                help="Examples: 3.12, 3.13, or 3.14",
                key="repo_analyzer_target_version_input",
            )

        show_preview = st.checkbox(
            "Show request preview",
            value=False,
            key="repo_analyzer_show_preview",
        )

        submitted = st.form_submit_button(
            "Run Repository Analyzer",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        repo_url = repo_url.strip()
        branch = branch.strip()
        target_python_version = target_python_version.strip()

        validation_errors: list[str] = []

        if not is_valid_http_url(repo_url):
            validation_errors.append(
                "Enter a valid HTTP or HTTPS repository URL."
            )

        if not branch:
            validation_errors.append(
                "Source branch is required."
            )

        if not is_valid_python_version(target_python_version):
            validation_errors.append(
                "Target Python version must look like `3.14` "
                "or `3.14.1`."
            )

        if display_validation_errors(validation_errors):
            st.stop()

        agent_request = AgentExecutionRequest(
            agent_id=repo_analyzer_agent_id,
            user=aava_user_email,
            user_inputs={
                "repo_url": repo_url,
                "branch": branch,
                "target_python_version": target_python_version,
            },
        )

        if show_preview:
            with st.expander(
                "Request preview",
                expanded=False,
            ):
                st.json(agent_request.safe_preview())

        output, elapsed_seconds, response_body = run_agent_request(
            agent_request,
            "Running Repository Analyzer Agent. "
            "This may take a few minutes...",
        )

        st.session_state["repo_analyzer_output"] = output
        st.session_state["repo_analyzer_elapsed_seconds"] = elapsed_seconds
        st.session_state["repo_analyzer_execution_id"] = (
            agent_request.execution_id
        )
        st.session_state["repo_analyzer_repo_url"] = repo_url
        st.session_state["repo_analyzer_branch"] = branch
        st.session_state["repo_analyzer_target_version"] = (
            target_python_version
        )
        st.session_state["repo_analyzer_http_status"] = response_body.get(
            "status",
            "SUCCESS",
        )

    if st.session_state["repo_analyzer_output"]:
        render_report_result(
            success_message="Repository Analyzer Agent completed.",
            elapsed_seconds=st.session_state[
                "repo_analyzer_elapsed_seconds"
            ],
            execution_id=st.session_state["repo_analyzer_execution_id"],
            output=st.session_state["repo_analyzer_output"],
            download_label="Download Repository Analysis",
            download_file_name="repository-analysis.md",
        )


def get_uploaded_markdown(uploaded_file) -> str:
    """Decode an uploaded Markdown or text file as UTF-8."""
    if uploaded_file is None:
        return ""

    try:
        return uploaded_file.getvalue().decode("utf-8")
    except UnicodeDecodeError:
        st.error("Uploaded analyzer report must be valid UTF-8 text.")
        st.stop()


def run_python_migration_tab() -> None:
    """Render and submit the standalone Python Migration form."""
    st.subheader("Python Migration")

    analyzer_source = st.radio(
        "Repository Analyzer report source",
        [
            "Use latest Repository Analyzer result",
            "Paste analyzer output",
            "Upload .md or .txt file",
        ],
        key="python_migration_analyzer_source",
    )

    latest_output = st.session_state["repo_analyzer_output"]
    pasted_output = ""
    uploaded_output = ""

    if analyzer_source == "Use latest Repository Analyzer result":
        if latest_output:
            st.success("Latest Repository Analyzer result is available.")
            info_columns = st.columns(4)
            info_columns[0].write("**Repository**")
            info_columns[0].write(
                st.session_state["repo_analyzer_repo_url"] or "Not recorded"
            )
            info_columns[1].write("**Branch**")
            info_columns[1].write(
                st.session_state["repo_analyzer_branch"] or "Not recorded"
            )
            info_columns[2].write("**Target**")
            info_columns[2].write(
                st.session_state["repo_analyzer_target_version"]
                or "Not recorded"
            )
            info_columns[3].metric(
                "Characters",
                len(latest_output),
            )

            with st.expander(
                "Latest analyzer output preview",
                expanded=False,
            ):
                st.markdown(latest_output)
        else:
            st.warning(
                "No Repository Analyzer result is saved in this session."
            )

    elif analyzer_source == "Paste analyzer output":
        pasted_output = st.text_area(
            "Repository Analyzer Markdown",
            height=350,
            key="python_migration_pasted_analyzer_output",
        )

    else:
        uploaded_file = st.file_uploader(
            "Upload Repository Analyzer Markdown",
            type=["md", "txt"],
            key="python_migration_uploaded_analyzer_output",
        )
        uploaded_output = get_uploaded_markdown(uploaded_file)
        if uploaded_output:
            st.caption(f"Loaded {len(uploaded_output)} characters.")

    saved_target_version = st.session_state[
        "repo_analyzer_target_version"
    ]
    default_target_version = (
        saved_target_version
        if (
            analyzer_source == "Use latest Repository Analyzer result"
            and saved_target_version
        )
        else "3.14"
    )

    with st.form(
        "python_migration_form",
        clear_on_submit=False,
    ):
        target_python_version = st.text_input(
            "Target Python version",
            value=default_target_version,
            help="Examples: 3.12, 3.13, or 3.14",
            key="python_migration_target_version_input",
        )

        submitted = st.form_submit_button(
            "Run Python Migration Agent",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if analyzer_source == "Use latest Repository Analyzer result":
            analyzer_output = latest_output
        elif analyzer_source == "Paste analyzer output":
            analyzer_output = pasted_output.strip()
        else:
            analyzer_output = uploaded_output

        target_python_version = target_python_version.strip()

        validation_errors: list[str] = []

        if not analyzer_output.strip():
            validation_errors.append(
                "Repository Analyzer output is required."
            )

        if not is_valid_python_version(target_python_version):
            validation_errors.append(
                "Target Python version must look like `3.14` "
                "or `3.14.1`."
            )

        if display_validation_errors(validation_errors):
            st.stop()

        agent_request = AgentExecutionRequest(
            agent_id=python_migration_agent_id,
            user=aava_user_email,
            user_inputs={
                "repo_analyzer_output": analyzer_output,
                "target_python_version": target_python_version,
            },
        )

        output, elapsed_seconds, response_body = run_agent_request(
            agent_request,
            "Running Python Migration Agent. "
            "This may take a few minutes...",
        )

        st.session_state["python_migration_output"] = output
        st.session_state["python_migration_elapsed_seconds"] = (
            elapsed_seconds
        )
        st.session_state["python_migration_execution_id"] = (
            agent_request.execution_id
        )
        st.session_state["python_migration_target_version"] = (
            target_python_version
        )
        st.session_state["python_migration_http_status"] = (
            response_body.get("status", "SUCCESS")
        )

    if st.session_state["python_migration_output"]:
        render_report_result(
            success_message="Python Migration Agent completed.",
            elapsed_seconds=st.session_state[
                "python_migration_elapsed_seconds"
            ],
            execution_id=st.session_state["python_migration_execution_id"],
            output=st.session_state["python_migration_output"],
            download_label="Download Python Migration Report",
            download_file_name="python-migration-report.md",
        )


initialize_session_state()

# Load secure configuration values.
workflow_url = required_secret("AAVA_WORKFLOW_URL")
agent_execution_url = required_secret("AAVA_AGENT_EXECUTION_URL")
pipeline_id = required_secret("AAVA_PIPELINE_ID")
priority = required_secret("AAVA_PRIORITY")
bearer_token = required_secret("AAVA_BEARER_TOKEN")
aava_user_email = required_secret("AAVA_USER_EMAIL")
repo_analyzer_agent_id = required_secret("AAVA_REPO_ANALYZER_AGENT_ID")
python_migration_agent_id = required_secret("AAVA_PYTHON_MIGRATION_AGENT_ID")


# Create clients responsible for calling AAVA.
workflow_client = AavaWorkflowClient(
    workflow_url=workflow_url,
    bearer_token=bearer_token,
)

agent_client = AavaAgentClient(
    agent_execution_url=agent_execution_url,
    bearer_token=bearer_token,
)


st.title("AAVA Python Migration")

st.caption(
    "Run the full migration workflow or execute documentation agents "
    "independently."
)


# Sidebar information
with st.sidebar:
    st.subheader("Connection")

    st.success("Secrets loaded")

    st.write("**Pipeline ID**")
    st.code(pipeline_id)

    st.write("**Priority**")
    st.code(priority)

    st.write("**Repository Analyzer Agent**")
    st.code(repo_analyzer_agent_id)

    st.write("**Python Migration Agent**")
    st.code(python_migration_agent_id)

    st.caption(
        "AAVA connection secrets are loaded securely. The full workflow "
        "uses the GitHub token entered in the form; standalone agents do "
        "not require a GitHub token."
    )

    if st.button(
        "Clear documentation results",
        use_container_width=True,
    ):
        clear_documentation_results()
        st.success("Documentation results cleared.")


full_workflow_tab, repository_analyzer_tab, python_migration_tab = st.tabs(
    [
        "Full Workflow",
        "Repository Analyzer",
        "Python Migration",
    ]
)

with full_workflow_tab:
    run_full_workflow_tab()

with repository_analyzer_tab:
    run_repository_analyzer_tab()

with python_migration_tab:
    run_python_migration_tab()
