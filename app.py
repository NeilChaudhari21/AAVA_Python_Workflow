import re
from urllib.parse import urlparse

import streamlit as st

from aava_client import AavaWorkflowClient, WorkflowRequest


st.set_page_config(
    page_title="AAVA Python Migration",
    page_icon="🐍",
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


# Load secure configuration values.
workflow_url = required_secret("AAVA_WORKFLOW_URL")
pipeline_id = required_secret("AAVA_PIPELINE_ID")
priority = required_secret("AAVA_PRIORITY")
bearer_token = required_secret("AAVA_BEARER_TOKEN")


# Create the client responsible for calling AAVA.
client = AavaWorkflowClient(
    workflow_url=workflow_url,
    bearer_token=bearer_token,
)


st.title("AAVA Python Migration")

st.caption(
    "Enter the migration settings and submit the AAVA workflow "
    "through the same multipart/form-data request used by Postman."
)


# Sidebar information
with st.sidebar:
    st.subheader("Connection")

    st.success("Secrets loaded")

    st.write("**Pipeline ID**")
    st.code(pipeline_id)

    st.write("**Priority**")
    st.code(priority)

    st.caption(
        "AAVA connection secrets are loaded securely. Each workflow "
        "run uses the GitHub token entered in the form."
    )


# Main workflow form
with st.form(
    "workflow_form",
    clear_on_submit=False,
):
    st.subheader("Repository")

    repo_url = st.text_input(
        "GitHub repository URL",
        value="https://github.com/NeilChaudhari21/python_demo",
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


if submitted:
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

    if validation_errors:
        for error in validation_errors:
            st.error(error)

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
            response = client.execute(
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
