import difflib
import json
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
from execution_store import ExecutionRecord, ExecutionStore


DEFAULT_REPO_URL = "https://github.com/NeilChaudhari21/python_demo"
DEFAULT_DUCKDB_PATH = "data/aava_executions.duckdb"


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


def optional_secret(
    name: str,
    default_value: str,
) -> str:
    """
    Read an optional Streamlit secret with a default fallback.
    """
    try:
        value = str(st.secrets[name]).strip()
    except KeyError:
        return default_value

    return value or default_value


@st.cache_resource
def get_execution_store(database_path: str) -> ExecutionStore:
    """Create the local DuckDB execution store."""
    return ExecutionStore(database_path)


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


def persist_execution_safely(
    record: ExecutionRecord,
    secret_values: tuple[str, ...],
) -> str | None:
    """
    Save execution history without hiding completed AAVA results.
    """
    try:
        return execution_store.save_execution(
            record,
            secret_values=secret_values,
        )
    except Exception as error:
        st.warning(
            "The execution attempt could not be saved to "
            f"DuckDB history: {error}"
        )
        return None


def concise_text(value: str, max_length: int = 800) -> str:
    """Trim long server messages for display and storage."""
    text = value.strip()
    if len(text) > max_length:
        return text[:max_length] + "..."

    return text


def format_created_at(value: object) -> str:
    """Format stored timestamps for display."""
    if value is None:
        return "Not recorded"

    try:
        return value.strftime("%Y-%m-%d %H:%M UTC")
    except AttributeError:
        return str(value)


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
    *,
    execution_name: str,
    target_id: str,
    repo_url: str | None = None,
    branch: str | None = None,
    target_python_version: str | None = None,
    target_branch: str | None = None,
) -> tuple[str, float, dict]:
    """Execute an agent request and return its Markdown output."""
    started_at = time.perf_counter()
    request_json = request.to_payload()

    def save_agent_record(
        *,
        outcome: str,
        response_json: dict,
        elapsed_seconds: float | None,
        http_status: int | None,
        aava_status: str | None,
        output_markdown: str | None = None,
        error_message: str | None = None,
    ) -> None:
        persist_execution_safely(
            ExecutionRecord(
                execution_type="agent",
                execution_name=execution_name,
                target_id=target_id,
                repo_url=repo_url,
                branch=branch,
                target_python_version=target_python_version,
                target_branch=target_branch,
                outcome=outcome,
                aava_status=aava_status,
                http_status=http_status,
                elapsed_seconds=elapsed_seconds,
                request_json=request_json,
                response_json=response_json,
                aava_execution_id=request.execution_id,
                job_id=None,
                output_markdown=output_markdown,
                error_message=error_message,
            ),
            secret_values=(bearer_token,),
        )

    with st.spinner(spinner_message):
        try:
            response = agent_client.execute(request)
        except requests.Timeout:
            error_message = (
                "The AAVA agent request timed out before the completed "
                "response was returned."
            )
            save_agent_record(
                outcome="TIMEOUT",
                response_json={},
                elapsed_seconds=time.perf_counter() - started_at,
                http_status=None,
                aava_status=None,
                error_message=error_message,
            )
            st.error(
                error_message
            )
            st.stop()
        except requests.RequestException as error:
            error_message = (
                f"Could not connect to AAVA agent endpoint: {error}"
            )
            save_agent_record(
                outcome="REQUEST_ERROR",
                response_json={},
                elapsed_seconds=time.perf_counter() - started_at,
                http_status=None,
                aava_status=None,
                error_message=error_message,
            )
            st.error(error_message)
            st.stop()

    elapsed_seconds = time.perf_counter() - started_at

    try:
        response_body = response.json()
        if not isinstance(response_body, dict):
            response_body = {
                "raw_response": response.text,
            }
    except ValueError:
        response_body = {
            "raw_response": response.text,
        }

    aava_status = response_body.get("status")

    if not response.ok:
        server_message = concise_text(response.text)
        save_agent_record(
            outcome="HTTP_ERROR",
            response_json=response_body,
            elapsed_seconds=elapsed_seconds,
            http_status=response.status_code,
            aava_status=aava_status,
            error_message=server_message or (
                f"AAVA returned HTTP {response.status_code}."
            ),
        )

        st.error(f"AAVA returned HTTP {response.status_code}.")
        if server_message:
            st.code(server_message)
        st.stop()

    if "raw_response" in response_body:
        error_message = "AAVA returned a non-JSON response."
        save_agent_record(
            outcome="INVALID_RESPONSE",
            response_json=response_body,
            elapsed_seconds=elapsed_seconds,
            http_status=response.status_code,
            aava_status=None,
            error_message=error_message,
        )
        st.error(error_message)
        st.stop()

    if aava_status is not None and aava_status != "SUCCESS":
        error_message = (
            f"AAVA agent execution returned status `{aava_status}`."
        )
        save_agent_record(
            outcome="AAVA_ERROR",
            response_json=response_body,
            elapsed_seconds=elapsed_seconds,
            http_status=response.status_code,
            aava_status=aava_status,
            error_message=error_message,
        )
        st.error(error_message)
        st.stop()

    try:
        output = extract_agent_output(response_body)
    except ValueError as error:
        error_message = str(error)
        save_agent_record(
            outcome="INVALID_RESPONSE",
            response_json=response_body,
            elapsed_seconds=elapsed_seconds,
            http_status=response.status_code,
            aava_status=aava_status,
            error_message=error_message,
        )
        st.error(str(error))
        st.stop()

    save_agent_record(
        outcome="SUCCESS",
        response_json=response_body,
        elapsed_seconds=elapsed_seconds,
        http_status=response.status_code,
        aava_status=aava_status or "SUCCESS",
        output_markdown=output,
    )

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
    storage_request_json = workflow_request.safe_storage_payload()

    def save_workflow_record(
        *,
        outcome: str,
        response_json: dict,
        elapsed_seconds: float | None,
        http_status: int | None,
        aava_status: str | None,
        aava_execution_id: str | None = None,
        job_id: str | None = None,
        output_markdown: str | None = None,
        error_message: str | None = None,
    ) -> None:
        persist_execution_safely(
            ExecutionRecord(
                execution_type="workflow",
                execution_name="Python Migration Workflow",
                target_id=pipeline_id,
                repo_url=repo_url,
                branch=branch,
                target_python_version=target_python_version,
                target_branch=target_branch,
                outcome=outcome,
                aava_status=aava_status,
                http_status=http_status,
                elapsed_seconds=elapsed_seconds,
                request_json=storage_request_json,
                response_json=response_json,
                aava_execution_id=aava_execution_id,
                job_id=job_id,
                output_markdown=output_markdown,
                error_message=error_message,
            ),
            secret_values=(
                github_token,
                bearer_token,
            ),
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
    started_at = time.perf_counter()
    with st.spinner(
        "Submitting the workflow to AAVA..."
    ):
        try:
            response = workflow_client.execute(
                workflow_request
            )
        except requests.Timeout:
            elapsed_seconds = time.perf_counter() - started_at
            error_message = (
                "The AAVA workflow request timed out before a response "
                "was returned."
            )
            save_workflow_record(
                outcome="TIMEOUT",
                response_json={},
                elapsed_seconds=elapsed_seconds,
                http_status=None,
                aava_status=None,
                error_message=error_message,
            )
            st.error(error_message)
            st.stop()
        except requests.RequestException as error:
            elapsed_seconds = time.perf_counter() - started_at
            error_message = f"Could not submit the workflow: {error}"
            save_workflow_record(
                outcome="REQUEST_ERROR",
                response_json={},
                elapsed_seconds=elapsed_seconds,
                http_status=None,
                aava_status=None,
                error_message=error_message,
            )
            st.error(
                error_message
            )
            st.stop()

    elapsed_seconds = time.perf_counter() - started_at

    # Attempt to parse the AAVA response as JSON.
    try:
        response_body = response.json()
        response_is_json = isinstance(response_body, dict)
        if not response_is_json:
            response_body = {
                "raw_response": response.text
            }
    except ValueError:
        response_is_json = False
        response_body = {
            "raw_response": response.text
        }

    response_data = {}
    api_status = None
    workflow_execution_id = None
    job_id = None

    if isinstance(response_body, dict):
        data_value = response_body.get(
            "data",
            {},
        )
        if isinstance(data_value, dict):
            response_data = data_value

        api_status = response_body.get(
            "status",
            "SUCCESS" if response.ok else None,
        )
        workflow_execution_id = response_data.get(
            "workflowExecutionId"
        )
        job_id = response_data.get(
            "jobId"
        )

    if not response.ok:
        workflow_outcome = "HTTP_ERROR"
        workflow_error_message = concise_text(response.text) or (
            f"AAVA returned HTTP {response.status_code}."
        )
    elif not response_is_json:
        workflow_outcome = "INVALID_RESPONSE"
        workflow_error_message = "AAVA returned a non-JSON response."
    elif api_status is not None and api_status != "SUCCESS":
        workflow_outcome = "AAVA_ERROR"
        workflow_error_message = (
            f"AAVA workflow returned status `{api_status}`."
        )
    else:
        workflow_outcome = "SUCCESS"
        workflow_error_message = None

    save_workflow_record(
        outcome=workflow_outcome,
        response_json=response_body,
        elapsed_seconds=elapsed_seconds,
        http_status=response.status_code,
        aava_status=api_status,
        aava_execution_id=workflow_execution_id,
        job_id=str(job_id) if job_id is not None else None,
        error_message=workflow_error_message,
    )

    if response.ok:
        st.success(
            "Workflow submitted successfully."
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
            execution_name="Repository Analyzer Agent",
            target_id=repo_analyzer_agent_id,
            repo_url=repo_url,
            branch=branch,
            target_python_version=target_python_version,
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
            "Use Repository Analyzer result from history",
            "Paste analyzer output",
            "Upload .md or .txt file",
        ],
        key="python_migration_analyzer_source",
    )

    latest_output = st.session_state["repo_analyzer_output"]
    history_output = ""
    history_repo_url = None
    history_branch = None
    history_target_version = ""
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

    elif analyzer_source == "Use Repository Analyzer result from history":
        try:
            history_records = execution_store.list_executions(limit=500)
        except Exception as error:
            history_records = []
            st.error(f"Could not load Repository Analyzer history: {error}")

        analyzer_records = [
            record
            for record in history_records
            if (
                record.get("execution_type") == "agent"
                and record.get("execution_name")
                == "Repository Analyzer Agent"
                and record.get("outcome") == "SUCCESS"
            )
        ]

        if analyzer_records:
            analyzer_record_by_id = {
                str(record["record_id"]): record
                for record in analyzer_records
            }
            selected_history_id = st.selectbox(
                "Saved Repository Analyzer result",
                list(analyzer_record_by_id.keys()),
                format_func=lambda record_id: make_history_label(
                    analyzer_record_by_id[record_id]
                ),
                key="python_migration_history_analyzer_record",
            )

            try:
                selected_history_record = execution_store.get_execution(
                    selected_history_id
                )
            except Exception as error:
                selected_history_record = None
                st.error(
                    "Could not load the selected Repository Analyzer "
                    f"result: {error}"
                )

            if selected_history_record is not None:
                history_output = (
                    selected_history_record.get("output_markdown")
                    or ""
                )
                history_repo_url = selected_history_record.get("repo_url")
                history_branch = selected_history_record.get("branch")
                history_target_version = (
                    selected_history_record.get("target_python_version")
                    or ""
                )

                if history_output:
                    st.success(
                        "Saved Repository Analyzer result is available."
                    )
                    info_columns = st.columns(4)
                    info_columns[0].write("**Repository**")
                    info_columns[0].write(history_repo_url or "Not recorded")
                    info_columns[1].write("**Branch**")
                    info_columns[1].write(history_branch or "Not recorded")
                    info_columns[2].write("**Target**")
                    info_columns[2].write(
                        history_target_version or "Not recorded"
                    )
                    info_columns[3].metric(
                        "Characters",
                        len(history_output),
                    )

                    with st.expander(
                        "Saved analyzer output preview",
                        expanded=False,
                    ):
                        st.markdown(history_output)
                else:
                    st.warning(
                        "The selected Repository Analyzer history record "
                        "does not include Markdown output."
                    )
        else:
            st.warning(
                "No successful Repository Analyzer results are stored "
                "in execution history yet."
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
    use_latest_analyzer = (
        analyzer_source == "Use latest Repository Analyzer result"
    )
    use_history_analyzer = (
        analyzer_source == "Use Repository Analyzer result from history"
    )

    with st.form(
        "python_migration_form",
        clear_on_submit=False,
    ):
        if use_latest_analyzer or use_history_analyzer:
            target_python_version = (
                saved_target_version
                if use_latest_analyzer
                else history_target_version
            )
            st.write("**Target Python version**")
            st.code(target_python_version or "Not recorded")
        else:
            target_python_version = st.text_input(
                "Target Python version",
                value="3.14",
                help="Examples: 3.12, 3.13, or 3.14",
                key="python_migration_target_version_input",
            )

        submitted = st.form_submit_button(
            "Run Python Migration Agent",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if use_latest_analyzer:
            analyzer_output = latest_output
        elif use_history_analyzer:
            analyzer_output = history_output
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
            execution_name="Python Migration Agent",
            target_id=python_migration_agent_id,
            repo_url=(
                st.session_state["repo_analyzer_repo_url"]
                if use_latest_analyzer
                else history_repo_url
                if use_history_analyzer
                else None
            ),
            branch=(
                st.session_state["repo_analyzer_branch"]
                if use_latest_analyzer
                else history_branch
                if use_history_analyzer
                else None
            ),
            target_python_version=target_python_version,
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


def make_history_label(record: dict[str, object]) -> str:
    """Create a readable selectbox label for a history record."""
    created_at = format_created_at(record.get("created_at"))
    name = record.get("execution_name") or "Execution"
    outcome = record.get("outcome") or "UNKNOWN"
    repo_url = record.get("repo_url") or "No repository"

    return f"{created_at} | {name} | {outcome} | {repo_url}"


def workflow_request_from_history(
    record: dict[str, object],
    github_token: str,
) -> WorkflowRequest:
    """Rebuild a full workflow request from sanitized history data."""
    request_json = record.get("request_json") or {}
    user_inputs = request_json.get("userInputs") or {}
    github_config = {}
    github_config_string = user_inputs.get(
        "{{github_config_string_true}}",
        "{}",
    )

    if isinstance(github_config_string, str):
        try:
            parsed_config = json.loads(github_config_string)
        except ValueError:
            parsed_config = {}

        if isinstance(parsed_config, dict):
            github_config = parsed_config

    return WorkflowRequest(
        pipeline_id=str(request_json.get("pipelineId") or pipeline_id),
        priority=str(request_json.get("priority") or priority),
        repo_url=str(
            user_inputs.get("{{repo_url_string_true}}")
            or record.get("repo_url")
            or ""
        ),
        branch=str(
            user_inputs.get("{{branch_string_true}}")
            or record.get("branch")
            or ""
        ),
        target_python_version=str(
            user_inputs.get("{{target_python_version_string_true}}")
            or record.get("target_python_version")
            or ""
        ),
        github_token=github_token,
        target_branch=str(
            github_config.get("target_branch")
            or record.get("target_branch")
            or ""
        ),
        commit_message=str(
            github_config.get("commit_message")
            or "Apply Python migration updates from UI"
        ),
    )


def rerun_workflow_from_history(
    record: dict[str, object],
    github_token: str,
) -> None:
    """Run a stored full workflow request with a fresh GitHub token."""
    workflow_request = workflow_request_from_history(
        record,
        github_token,
    )
    validation_errors: list[str] = []

    if not is_valid_http_url(workflow_request.repo_url):
        validation_errors.append(
            "Stored workflow record does not contain a valid repository URL."
        )

    if not workflow_request.branch:
        validation_errors.append(
            "Stored workflow record does not contain a source branch."
        )

    if not is_valid_python_version(workflow_request.target_python_version):
        validation_errors.append(
            "Stored workflow record does not contain a valid target "
            "Python version."
        )

    if not workflow_request.target_branch:
        validation_errors.append(
            "Stored workflow record does not contain a target branch."
        )

    if not workflow_request.commit_message:
        validation_errors.append(
            "Stored workflow record does not contain a commit message."
        )

    if not github_token:
        validation_errors.append(
            "GitHub personal access token is required to rerun a full "
            "workflow."
        )

    if display_validation_errors(validation_errors):
        st.stop()

    storage_request_json = workflow_request.safe_storage_payload()
    started_at = time.perf_counter()

    with st.spinner("Rerunning the full workflow from history..."):
        try:
            response = workflow_client.execute(workflow_request)
        except requests.Timeout:
            elapsed_seconds = time.perf_counter() - started_at
            error_message = (
                "The AAVA workflow request timed out before a response "
                "was returned."
            )
            persist_execution_safely(
                ExecutionRecord(
                    execution_type="workflow",
                    execution_name="Python Migration Workflow",
                    target_id=workflow_request.pipeline_id,
                    repo_url=workflow_request.repo_url,
                    branch=workflow_request.branch,
                    target_python_version=(
                        workflow_request.target_python_version
                    ),
                    target_branch=workflow_request.target_branch,
                    outcome="TIMEOUT",
                    aava_status=None,
                    http_status=None,
                    elapsed_seconds=elapsed_seconds,
                    request_json=storage_request_json,
                    response_json={},
                    aava_execution_id=None,
                    job_id=None,
                    output_markdown=None,
                    error_message=error_message,
                ),
                secret_values=(
                    github_token,
                    bearer_token,
                ),
            )
            st.error(error_message)
            st.stop()
        except requests.RequestException as error:
            elapsed_seconds = time.perf_counter() - started_at
            error_message = f"Could not submit the workflow: {error}"
            persist_execution_safely(
                ExecutionRecord(
                    execution_type="workflow",
                    execution_name="Python Migration Workflow",
                    target_id=workflow_request.pipeline_id,
                    repo_url=workflow_request.repo_url,
                    branch=workflow_request.branch,
                    target_python_version=(
                        workflow_request.target_python_version
                    ),
                    target_branch=workflow_request.target_branch,
                    outcome="REQUEST_ERROR",
                    aava_status=None,
                    http_status=None,
                    elapsed_seconds=elapsed_seconds,
                    request_json=storage_request_json,
                    response_json={},
                    aava_execution_id=None,
                    job_id=None,
                    output_markdown=None,
                    error_message=error_message,
                ),
                secret_values=(
                    github_token,
                    bearer_token,
                ),
            )
            st.error(error_message)
            st.stop()

    elapsed_seconds = time.perf_counter() - started_at

    try:
        response_body = response.json()
        response_is_json = isinstance(response_body, dict)
        if not response_is_json:
            response_body = {
                "raw_response": response.text,
            }
    except ValueError:
        response_is_json = False
        response_body = {
            "raw_response": response.text,
        }

    response_data = response_body.get("data", {})
    if not isinstance(response_data, dict):
        response_data = {}

    api_status = response_body.get(
        "status",
        "SUCCESS" if response.ok else None,
    )
    workflow_execution_id = response_data.get("workflowExecutionId")
    job_id = response_data.get("jobId")

    if not response.ok:
        outcome = "HTTP_ERROR"
        error_message = concise_text(response.text) or (
            f"AAVA returned HTTP {response.status_code}."
        )
    elif not response_is_json:
        outcome = "INVALID_RESPONSE"
        error_message = "AAVA returned a non-JSON response."
    elif api_status is not None and api_status != "SUCCESS":
        outcome = "AAVA_ERROR"
        error_message = f"AAVA workflow returned status `{api_status}`."
    else:
        outcome = "SUCCESS"
        error_message = None

    persist_execution_safely(
        ExecutionRecord(
            execution_type="workflow",
            execution_name="Python Migration Workflow",
            target_id=workflow_request.pipeline_id,
            repo_url=workflow_request.repo_url,
            branch=workflow_request.branch,
            target_python_version=workflow_request.target_python_version,
            target_branch=workflow_request.target_branch,
            outcome=outcome,
            aava_status=api_status,
            http_status=response.status_code,
            elapsed_seconds=elapsed_seconds,
            request_json=storage_request_json,
            response_json=response_body,
            aava_execution_id=(
                str(workflow_execution_id)
                if workflow_execution_id is not None
                else None
            ),
            job_id=str(job_id) if job_id is not None else None,
            output_markdown=None,
            error_message=error_message,
        ),
        secret_values=(
            github_token,
            bearer_token,
        ),
    )

    if response.ok:
        st.success("Workflow rerun submitted successfully.")
    else:
        st.error(f"AAVA returned HTTP {response.status_code}.")

    status_column, api_column, job_column = st.columns(3)
    status_column.metric("HTTP", response.status_code)
    api_column.metric("API status", str(api_status or "Not returned"))
    job_column.metric("Job ID", str(job_id or "Not returned"))

    if workflow_execution_id:
        st.write("**Workflow execution ID**")
        st.code(str(workflow_execution_id))

    with st.expander(
        "Rerun AAVA response",
        expanded=response.ok,
    ):
        st.json(response_body)

    if error_message:
        st.error(error_message)


def rerun_agent_from_history(record: dict[str, object]) -> None:
    """Rerun a stored standalone agent request."""
    request_json = record.get("request_json") or {}
    user_inputs = request_json.get("userInputs") or {}
    target_id = str(record.get("target_id") or request_json.get("agentId"))
    execution_name = str(record.get("execution_name") or "Agent")

    if not isinstance(user_inputs, dict):
        st.error("Stored agent record does not contain valid user inputs.")
        st.stop()

    if not target_id:
        st.error("Stored agent record does not contain an agent ID.")
        st.stop()

    agent_request = AgentExecutionRequest(
        agent_id=target_id,
        user=str(request_json.get("user") or aava_user_email),
        user_inputs={
            str(key): str(value)
            for key, value in user_inputs.items()
        },
    )

    output, elapsed_seconds, _response_body = run_agent_request(
        agent_request,
        f"Rerunning {execution_name}. This may take a few minutes...",
        execution_name=execution_name,
        target_id=target_id,
        repo_url=(
            str(record.get("repo_url"))
            if record.get("repo_url") is not None
            else None
        ),
        branch=(
            str(record.get("branch"))
            if record.get("branch") is not None
            else None
        ),
        target_python_version=(
            str(record.get("target_python_version"))
            if record.get("target_python_version") is not None
            else None
        ),
        target_branch=(
            str(record.get("target_branch"))
            if record.get("target_branch") is not None
            else None
        ),
    )

    if execution_name == "Repository Analyzer Agent":
        st.session_state["repo_analyzer_output"] = output
        st.session_state["repo_analyzer_elapsed_seconds"] = elapsed_seconds
        st.session_state["repo_analyzer_execution_id"] = (
            agent_request.execution_id
        )
        st.session_state["repo_analyzer_repo_url"] = (
            record.get("repo_url") or ""
        )
        st.session_state["repo_analyzer_branch"] = record.get("branch") or ""
        st.session_state["repo_analyzer_target_version"] = (
            record.get("target_python_version") or ""
        )
    elif execution_name == "Python Migration Agent":
        st.session_state["python_migration_output"] = output
        st.session_state["python_migration_elapsed_seconds"] = elapsed_seconds
        st.session_state["python_migration_execution_id"] = (
            agent_request.execution_id
        )
        st.session_state["python_migration_target_version"] = (
            record.get("target_python_version") or ""
        )

    render_report_result(
        success_message=f"{execution_name} rerun completed.",
        elapsed_seconds=elapsed_seconds,
        execution_id=agent_request.execution_id,
        output=output,
        download_label=f"Download {execution_name} Rerun Output",
        download_file_name=(
            f"{execution_name.lower().replace(' ', '-')}-rerun.md"
        ),
    )


def render_rerun_controls(record: dict[str, object]) -> None:
    """Render rerun controls for a selected history record."""
    st.write("**Rerun**")

    if record["execution_type"] == "agent":
        if st.button(
            f"Rerun {record['execution_name']}",
            type="primary",
            use_container_width=True,
        ):
            rerun_agent_from_history(record)

        return

    if record["execution_type"] == "workflow":
        st.caption(
            "Full workflow history does not store the GitHub token, so "
            "enter a fresh token before rerunning."
        )
        github_token = st.text_input(
            "GitHub personal access token for rerun",
            type="password",
            key=f"rerun_github_token_{record['record_id']}",
        )

        if st.button(
            "Rerun Full Workflow",
            type="primary",
            use_container_width=True,
        ):
            rerun_workflow_from_history(
                record,
                github_token.strip(),
            )


def render_record_comparison(records: list[dict[str, object]]) -> None:
    """Render controls for comparing two stored execution records."""
    st.subheader("Compare Executions")

    if len(records) < 2:
        st.info("At least two stored executions are needed for comparison.")
        return

    record_by_id = {
        str(record["record_id"]): record
        for record in records
    }
    record_ids = list(record_by_id.keys())
    compare_columns = st.columns(2)
    first_id = compare_columns[0].selectbox(
        "First execution",
        record_ids,
        format_func=lambda record_id: make_history_label(
            record_by_id[record_id]
        ),
        key="compare_first_execution",
    )
    second_default_index = 1 if len(record_ids) > 1 else 0
    second_id = compare_columns[1].selectbox(
        "Second execution",
        record_ids,
        index=second_default_index,
        format_func=lambda record_id: make_history_label(
            record_by_id[record_id]
        ),
        key="compare_second_execution",
    )

    if first_id == second_id:
        st.warning("Choose two different executions to compare.")
        return

    try:
        first_record = execution_store.get_execution(first_id)
        second_record = execution_store.get_execution(second_id)
    except Exception as error:
        st.error(f"Could not load comparison records: {error}")
        return

    if first_record is None or second_record is None:
        st.warning("One of the selected executions could not be found.")
        return

    first_meta = {
        "Name": first_record["execution_name"],
        "Outcome": first_record["outcome"],
        "Repository": first_record["repo_url"],
        "Branch": first_record["branch"],
        "Target Python": first_record["target_python_version"],
        "HTTP": first_record["http_status"],
        "Elapsed Seconds": first_record["elapsed_seconds"],
    }
    second_meta = {
        "Name": second_record["execution_name"],
        "Outcome": second_record["outcome"],
        "Repository": second_record["repo_url"],
        "Branch": second_record["branch"],
        "Target Python": second_record["target_python_version"],
        "HTTP": second_record["http_status"],
        "Elapsed Seconds": second_record["elapsed_seconds"],
    }
    meta_columns = st.columns(2)
    meta_columns[0].json(first_meta)
    meta_columns[1].json(second_meta)

    first_output = first_record.get("output_markdown") or ""
    second_output = second_record.get("output_markdown") or ""

    if first_output and second_output:
        diff = "\n".join(
            difflib.unified_diff(
                first_output.splitlines(),
                second_output.splitlines(),
                fromfile=f"{first_record['execution_name']} {first_id[:8]}",
                tofile=f"{second_record['execution_name']} {second_id[:8]}",
                lineterm="",
            )
        )
        st.write("**Markdown Output Diff**")
        st.code(diff or "No Markdown differences.", language="diff")

        output_columns = st.columns(2)
        with output_columns[0].expander(
            "First Markdown Output",
            expanded=False,
        ):
            st.markdown(first_output)
        with output_columns[1].expander(
            "Second Markdown Output",
            expanded=False,
        ):
            st.markdown(second_output)
    else:
        st.info(
            "Markdown diff is available when both selected records have "
            "stored Markdown output. Full workflow records often only "
            "contain submission metadata."
        )

    with st.expander("First Request JSON", expanded=False):
        st.json(first_record["request_json"])
    with st.expander("Second Request JSON", expanded=False):
        st.json(second_record["request_json"])
    with st.expander("First Response JSON", expanded=False):
        st.json(first_record["response_json"])
    with st.expander("Second Response JSON", expanded=False):
        st.json(second_record["response_json"])


def run_execution_history_tab() -> None:
    """Render locally stored DuckDB execution history."""
    st.subheader("Execution History")

    st.caption(
        "Full workflow and standalone-agent requests are stored locally "
        "in DuckDB. Credentials are redacted before storage."
    )
    st.caption(
        "Local files on Streamlit Community Cloud are not guaranteed to "
        "persist across app restarts or redeployments."
    )

    try:
        records = execution_store.list_executions(limit=100)
    except Exception as error:
        st.error(f"Could not load execution history: {error}")
        return

    if not records:
        st.info("No stored executions yet.")
        return

    filter_columns = st.columns(2)
    execution_type_filter = filter_columns[0].selectbox(
        "Execution type",
        [
            "All",
            "workflow",
            "agent",
        ],
    )
    outcome_filter = filter_columns[1].selectbox(
        "Outcome",
        [
            "All",
            "SUCCESS",
            "HTTP_ERROR",
            "AAVA_ERROR",
            "INVALID_RESPONSE",
            "TIMEOUT",
            "REQUEST_ERROR",
        ],
    )

    filtered_records = records
    if execution_type_filter != "All":
        filtered_records = [
            record
            for record in filtered_records
            if record.get("execution_type") == execution_type_filter
        ]

    if outcome_filter != "All":
        filtered_records = [
            record
            for record in filtered_records
            if record.get("outcome") == outcome_filter
        ]

    if not filtered_records:
        st.info("No stored executions match the selected filters.")
        return

    table_rows = [
        {
            "Created At": format_created_at(record.get("created_at")),
            "Type": record.get("execution_type"),
            "Name": record.get("execution_name"),
            "Repository": record.get("repo_url"),
            "Branch": record.get("branch"),
            "Target Python": record.get("target_python_version"),
            "Target Branch": record.get("target_branch"),
            "Outcome": record.get("outcome"),
            "HTTP": record.get("http_status"),
            "Elapsed Seconds": record.get("elapsed_seconds"),
            "AAVA Execution ID": record.get("aava_execution_id"),
            "Job ID": record.get("job_id"),
        }
        for record in filtered_records
    ]

    st.dataframe(
        table_rows,
        use_container_width=True,
        hide_index=True,
    )

    record_by_id = {
        str(record["record_id"]): record
        for record in filtered_records
    }
    selected_record_id = st.selectbox(
        "Select an execution",
        list(record_by_id.keys()),
        format_func=lambda record_id: make_history_label(
            record_by_id[record_id]
        ),
    )

    try:
        selected_record = execution_store.get_execution(selected_record_id)
    except Exception as error:
        st.error(f"Could not load selected execution: {error}")
        return

    if selected_record is None:
        st.warning("The selected execution could not be found.")
        return

    st.write("**Execution metadata**")
    metadata = {
        "Record ID": selected_record["record_id"],
        "Created At": format_created_at(selected_record["created_at"]),
        "Type": selected_record["execution_type"],
        "Name": selected_record["execution_name"],
        "Target ID": selected_record["target_id"],
        "Repository": selected_record["repo_url"],
        "Branch": selected_record["branch"],
        "Target Python": selected_record["target_python_version"],
        "Target Branch": selected_record["target_branch"],
        "Outcome": selected_record["outcome"],
        "AAVA Status": selected_record["aava_status"],
        "HTTP": selected_record["http_status"],
        "Elapsed Seconds": selected_record["elapsed_seconds"],
        "AAVA Execution ID": selected_record["aava_execution_id"],
        "Job ID": selected_record["job_id"],
    }
    st.json(metadata)

    render_rerun_controls(selected_record)

    request_json = selected_record["request_json"]
    response_json = selected_record["response_json"]
    request_download = json.dumps(
        request_json,
        indent=2,
        ensure_ascii=False,
        default=str,
    )
    response_download = json.dumps(
        response_json,
        indent=2,
        ensure_ascii=False,
        default=str,
    )

    download_columns = st.columns(3)
    short_record_id = str(selected_record_id)[:8]
    download_columns[0].download_button(
        "Download Request JSON",
        data=request_download,
        file_name=f"execution-{short_record_id}-request.json",
        mime="application/json",
        use_container_width=True,
    )
    download_columns[1].download_button(
        "Download Response JSON",
        data=response_download,
        file_name=f"execution-{short_record_id}-response.json",
        mime="application/json",
        use_container_width=True,
    )

    output_markdown = selected_record["output_markdown"]
    if output_markdown:
        download_columns[2].download_button(
            "Download Markdown Output",
            data=output_markdown,
            file_name=f"execution-{short_record_id}-output.md",
            mime="text/markdown",
            use_container_width=True,
        )

    with st.expander(
        "Stored Request JSON",
        expanded=False,
    ):
        st.json(request_json)

    with st.expander(
        "Stored Response JSON",
        expanded=False,
    ):
        st.json(response_json)

    if output_markdown:
        st.write("**Stored Agent Output**")
        st.markdown(output_markdown)

    if selected_record["error_message"]:
        st.write("**Stored Error Message**")
        st.error(selected_record["error_message"])

    st.divider()
    render_record_comparison(records)


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
duckdb_path = optional_secret("DUCKDB_PATH", DEFAULT_DUCKDB_PATH)

try:
    execution_store = get_execution_store(duckdb_path)
except Exception as error:
    st.error(
        "Could not initialize local DuckDB execution history: "
        f"{error}"
    )
    st.stop()


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

    st.write("**Execution history DB**")
    st.code(duckdb_path)

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


(
    full_workflow_tab,
    repository_analyzer_tab,
    python_migration_tab,
    execution_history_tab,
) = st.tabs(
    [
        "Full Workflow",
        "Repository Analyzer",
        "Python Migration",
        "Execution History",
    ]
)

with full_workflow_tab:
    run_full_workflow_tab()

with repository_analyzer_tab:
    run_repository_analyzer_tab()

with python_migration_tab:
    run_python_migration_tab()

with execution_history_tab:
    run_execution_history_tab()
