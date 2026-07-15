# AAVA Python Migration UI

A Streamlit interface for running the AAVA Python migration workflow.

The application reproduces the working request imported into Postman from the
AAVA browser Inspect tool.

## Request format

The application sends a POST request using `multipart/form-data`.

The request contains exactly three form fields:

- `pipelineId`
- `userInputs`
- `priority`

It also sends the AAVA API token using the Authorization header:

```http
Authorization: Bearer <AAVA_TOKEN>
```

The GitHub personal access token is entered in the UI for each workflow run
and securely inserted into the `github_config` workflow input.

## Project structure

```text
aava-workflow-ui/
├── app.py
├── aava_client.py
├── requirements.txt
├── README.md
├── .gitignore
└── .streamlit/
    ├── secrets.toml
    └── secrets.toml.example
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
pip install -r requirements.txt
```

### 5. Configure secrets

Open:

```text
.streamlit/secrets.toml
```

Add the exact values from the working Postman request:

```toml
AAVA_WORKFLOW_URL = "YOUR_WORKFLOW_EXECUTION_URL"
AAVA_PIPELINE_ID = "YOUR_PIPELINE_ID"
AAVA_PRIORITY = "1"
AAVA_BEARER_TOKEN = "YOUR_AAVA_TOKEN"
```

## Run the application

```powershell
streamlit run app.py
```

Streamlit should open the application in a browser.

The default local address is usually:

```text
http://localhost:8501
```

## Workflow inputs

The UI collects:

- GitHub repository URL
- GitHub personal access token
- Source branch
- Target Python version
- Target branch
- Commit message

Use a GitHub token that can read the source repository and create or update
the target branch.

The application builds the AAVA `userInputs` object using the exact workflow
variable names:

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

The intermediate workflow values are submitted as empty strings and populated
during workflow execution.

## Security

Never commit:

```text
.streamlit/secrets.toml
```

The `.gitignore` file excludes it automatically.

The GitHub personal access token is entered in the app form for each run and
is not stored in `.streamlit/secrets.toml`.

The request preview in the UI hides:

- The AAVA bearer token
- The GitHub personal access token

## Verifying execution

After submitting the workflow:

1. Confirm the UI returns a successful HTTP response.
2. Record the workflow execution ID or job ID.
3. Open AAVA execution history.
4. Confirm all workflow agents and tools completed.
5. Check the target GitHub branch for the generated commit.
