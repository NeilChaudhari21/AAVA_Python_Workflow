# AAVA Python Migration UI

A Streamlit interface for running the complete AAVA Python migration workflow,
running documentation-focused AAVA agents independently, and reviewing local
execution history.

The app has four top-level tabs:

- **Full Workflow** runs the existing end-to-end migration workflow.
- **Repository Analyzer** runs only the Repository Analyzer Agent.
- **Python Migration** runs only the Python Migration Agent.
- **Execution History** shows locally stored DuckDB execution records.

## Request formats

The **Full Workflow** tab preserves the original workflow request. It sends a
`multipart/form-data` POST with exactly these fields:

- `pipelineId`
- `userInputs`
- `priority`

It also sends the AAVA API token using the Authorization header:

```http
Authorization: Bearer <AAVA_TOKEN>
```

The full workflow asks the user to enter a GitHub personal access token in the
UI for each run. That token is inserted into the existing nested
`github_config` workflow input and is never displayed in request previews or
stored in execution history.

The **Repository Analyzer** and **Python Migration** tabs use the standalone
AAVA agent endpoint. These requests are JSON POST requests to
`AAVA_AGENT_EXECUTION_URL`. They are synchronous and can take one to several
minutes to return the completed response. The app waits for the HTTP response
and does not poll.

Standalone agent reports are extracted from the final Markdown `output` field.
The app supports these observed AAVA response shapes:

```text
data.agentResponse.output
data.agentResponse.agent.output
data.output
output
```

## Local execution history

DuckDB is embedded directly in the Python app. No DuckDB account, external
server, MotherDuck, PostgreSQL, Supabase, or other service is required.

By default, execution records are stored in:

```text
data/aava_executions.duckdb
```

`DUCKDB_PATH` is optional. If it is missing or blank, the default path above is
used and the parent directory is created automatically.

Every attempted AAVA API call is recorded after validation passes:

- Full workflow submissions
- Repository Analyzer agent executions
- Python Migration agent executions

Each record stores execution metadata, sanitized request JSON, sanitized
response JSON, and extracted agent Markdown output when available. Credentials
are redacted before storage, including Authorization values, AAVA bearer
tokens, GitHub tokens, access tokens, and nested workflow `github_config`
tokens.

The Execution History tab can inspect stored records, view sanitized request
and response JSON, render stored Markdown output, and download those stored
artifacts.

Local DuckDB files persist during normal local development. Streamlit Community
Cloud does not guarantee persistence of local files across app restarts or
redeployments. A persistent company deployment will eventually need an
approved persistent volume or external storage location.

## Project structure

```text
aava-workflow-ui/
|-- app.py
|-- aava_client.py
|-- aava_agent_client.py
|-- execution_store.py
|-- requirements.txt
|-- README.md
|-- .gitignore
|-- .streamlit/
|   |-- secrets.toml
|   `-- secrets.toml.example
`-- tests/
    |-- test_aava_agent_client.py
    |-- test_aava_client.py
    `-- test_execution_store.py
```

## Setup

### 1. Open the project folder

```powershell
cd aava-workflow-ui
```

### 2. Create a virtual environment

```powershell
py -m venv .venv
```

### 3. Activate the virtual environment

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

### 5. Configure secrets

Open:

```text
.streamlit/secrets.toml
```

Add placeholder-free values copied from your working AAVA/Postman requests:

```toml
AAVA_WORKFLOW_URL = "YOUR_WORKFLOW_EXECUTION_URL"
AAVA_AGENT_EXECUTION_URL = "PASTE_EXACT_AGENT_EXECUTION_URL_FROM_POSTMAN"
AAVA_PIPELINE_ID = "YOUR_PIPELINE_ID"
AAVA_PRIORITY = "1"
AAVA_BEARER_TOKEN = "YOUR_AAVA_TOKEN"
AAVA_USER_EMAIL = "your.name@ascendion.com"
AAVA_REPO_ANALYZER_AGENT_ID = "45881"
AAVA_PYTHON_MIGRATION_AGENT_ID = "45878"

# Optional
DUCKDB_PATH = "data/aava_executions.duckdb"
```

Do not put a GitHub token in `.streamlit/secrets.toml`. The Full Workflow tab
asks each user to enter their own GitHub token for each workflow run.

## Run the application

```powershell
python -m streamlit run app.py
```

Streamlit should open the application in a browser. The default local address
is usually:

```text
http://localhost:8501
```

## Full Workflow tab

The Full Workflow tab runs all four workflow agents through the existing AAVA
workflow endpoint.

The UI collects:

- GitHub repository URL
- Source branch
- Target Python version
- GitHub personal access token
- Target branch
- Commit message

Use a GitHub token that can read the source repository and create or update the
target branch. The app builds the AAVA `userInputs` object using the existing
workflow variable names:

```text
{{repo_url_string_true}}
{{branch_string_true}}
{{target_python_version_string_true}}
{{repo_analyzer_output_string_true}}
{{repository_string_true}}
{{migration_string_true}}
{{target_string_true}}
{{codebase_bundle_string_true}}
{{base_branch_string_true}}
{{github_config_string_true}}
{{migration_agent_output_string_true}}
```

## Repository Analyzer tab

The Repository Analyzer tab runs agent ID `45881` independently. It collects:

- GitHub repository URL
- Source branch
- Target Python version

The standalone agent does not require a GitHub token. The report is displayed
as rendered Markdown, saved in session state, stored in DuckDB history, and can
be downloaded as `repository-analysis.md`.

## Python Migration tab

The Python Migration tab runs agent ID `45878` independently. It requires the
complete Repository Analyzer Markdown report and a target Python version.

The analyzer report can come from:

- The latest Repository Analyzer run in the current Streamlit session
- Pasted Markdown
- An uploaded `.md` or `.txt` file

When using the latest Repository Analyzer result, the Python Migration tab
automatically uses the target Python version from that analyzer run. For pasted
or uploaded analyzer reports, the user enters the target version manually.

The standalone Python Migration Agent does not require a GitHub token,
repository URL, branch, target branch, or commit message. The report is
displayed as rendered Markdown, saved in session state, stored in DuckDB
history, and can be downloaded as `python-migration-report.md`.

## Security

Never commit:

```text
.streamlit/secrets.toml
data/*.duckdb
data/*.duckdb.wal
*.duckdb
*.duckdb.wal
```

The `.gitignore` file excludes these automatically.

The request previews hide authorization values. The app does not store the
AAVA bearer token or GitHub token in Streamlit session state, does not cache
agent requests, and redacts credentials before writing request or response JSON
to DuckDB.

## Verification

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run the unit tests:

```powershell
python -m unittest discover -s tests -v
```

Run the compile check:

```powershell
python -m compileall app.py aava_client.py aava_agent_client.py execution_store.py
```

Run the app locally:

```powershell
python -m streamlit run app.py
```
