# Setup Readiness Checker

One number for “can I run this project on **my** laptop?” — not a generic CI box, and not the AWS host.

Amazon Bedrock agents read the GitHub or local source, then **ScoreAgent** compares that to **this PC’s** fingerprint and writes the % ready number plus the missing/mismatch list.

## Amazon Bedrock setup (required for LLM scoring)

The laptop agent only measures your PC. Bedrock runs in the API (`dev_server.py` or Lambda) and produces the score + blockers.

### 1. AWS account and CLI

1. Create/sign in at https://aws.amazon.com/
2. Install AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
3. Create an IAM user (or use the hackathon account) with `AmazonBedrockFullAccess` (or at least `bedrock:InvokeModel` on `*`).
4. Create an access key, then in PowerShell:

```powershell
aws configure
```

Use region **`us-east-1`** (Nova Lite is available there). Paste Access Key ID and Secret.

### 2. Turn on the model in Bedrock

1. Open https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/modelaccess
2. Click **Modify model access**
3. Enable **Amazon Nova Lite** (`amazon.nova-lite-v1:0`) — or Claude if you prefer
4. Wait until status is **Access granted**

### 3. Prove it from this repo

```powershell
cd C:\Users\Yash\Desktop\repo-checker
$env:AWS_REGION="us-east-1"
$env:BEDROCK_MODEL_ID="amazon.nova-lite-v1:0"
Remove-Item Env:BEDROCK_DISABLED -ErrorAction SilentlyContinue
python backend/check_bedrock.py
```

You should see `ok Bedrock responded`. If it fails, `/health` and the script print the AWS error (AccessDenied, model not enabled, wrong region).

### 4. Restart the local API **with** Bedrock (do not set BEDROCK_DISABLED)

Stop the old `python backend/dev_server.py` window, then:

```powershell
cd C:\Users\Yash\Desktop\repo-checker
$env:AWS_REGION="us-east-1"
$env:BEDROCK_MODEL_ID="amazon.nova-lite-v1:0"
$env:AGENT_PORT="9877"
$env:AUTO_START_AGENT="0"
python backend/dev_server.py
```

The log must say `bedrock=on`. UI header should switch from **heuristic scoring** to **Bedrock scoring on**.

Then check a GitHub URL or local folder as usual. ScoreAgent uses your fingerprint vs the repo and lists what this PC is missing.

## Two ways to check

### 1. GitHub URL (dashboard)

Paste a public repo URL. The UI analyzes it, then **automatically** asks the local agent on your laptop to fingerprint, install (sandbox), and boot. You do **not** paste `irm ... | python ...`.

### 2. Local folder (dashboard or CLI)

Check the folder you are already working in:

- **UI:** switch to **Local folder**, paste an absolute path (e.g. `C:\Users\you\Desktop\my-app`)
- **CLI:** `python agent/setup_check.py --path "C:\Users\you\Desktop\my-app" --api http://127.0.0.1:8787`

## Installs require your OK

The first pass only **fingerprints** your machine (OS, Python, Node, RAM, etc.). It does **not** install missing tools or project dependencies.

Each blocker in the UI has **Install** / **Skip**. Nothing runs until you click **Yes, install** for that item (sandboxed pip/npm, winget/brew for git/node/python, docker run for Redis/Postgres, etc.).

## Local demo

From the repo root:

```powershell
python -m pip install -r backend/requirements.txt
python backend/dev_server.py
```

That starts the API on `:8787` **and** auto-starts the local agent sidecar (default `:9876`). With Bedrock env vars set (section above), scoring uses the LLM.

Second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL (usually [http://127.0.0.1:5173](http://127.0.0.1:5173)). When the header says **agent online**, click **Check setup** — the score fills in by itself.

If the agent is offline (e.g. Amplify frontend talking to AWS API), start it once on your laptop:

```powershell
python agent/setup_check.py serve --api https://YOUR_API_URL
```

## What each folder does

| Path | Role |
| --- | --- |
| `frontend/` | React + Tailwind dashboard (Amplify Hosting) |
| `backend/dev_server.py` | Local FastAPI + auto-starts agent sidecar |
| `backend/lambdas/` | AWS Lambda entrypoints |
| `backend/shared/agents.py` | Bedrock RepoAnalyst + **ScoreAgent** (PC gaps) + auditor |
| `backend/shared/diagnose.py` | Bedrock DiagnoseAgent on install logs |
| `backend/template.yaml` | SAM: HTTP API + DynamoDB + Lambdas + Bedrock IAM |
| `agent/setup_check.py` | Local agent: `serve`, session check, `--path` |

## Hackathon AWS map (Ship It)

| Track | What this project uses |
| --- | --- |
| Agents and AI | **Groq** LLM for RepoAnalyst + ScoreAgent + crash preview (Bedrock stays wired; this account uses `BEDROCK_DISABLED=1`) |
| Serverless | Lambda + API Gateway HTTP API (SAM `backend/template.yaml`) |
| Data | DynamoDB session store |
| Hosting | Amplify Hosting (`amplify.yml`) |
| Observability | CloudWatch logs from each Lambda (SAM default) |

The Lambda **never** fingerprints itself. Only the local agent reports OS / runtimes / install / boot.

## AWS deploy

### 0. IAM (one-time)

Your IAM user needs CloudFormation + Lambda + API Gateway + DynamoDB + S3 + IAM role creation.

In AWS Console → IAM → Users → `yashpatil` → Add permissions → attach **AdministratorAccess** (hackathon)  
or create a policy from `backend/iam-sam-deploy-policy.json` and attach it.

Also install: AWS CLI (done), SAM CLI (`winget install Amazon.SAM-CLI`), Python 3.12 (`py install 3.12`).

### 1. Backend (Lambda + API Gateway + DynamoDB)

```powershell
# Put GROQ_API_KEY in repo-root .env first
cd backend
.\deploy.ps1
```

Or manually:

```powershell
cd backend
sam build
sam deploy --guided
# Parameters: BedrockDisabled=1, paste GroqApiKey, GroqModel=openai/gpt-oss-20b
```

Note the stack output **ApiBaseUrl**.

### 2. Frontend (Amplify)

1. AWS Console → Amplify → Create new app → Host web app → connect this GitHub repo (or drag-drop `frontend/dist`).
2. Build settings use root `amplify.yml`.
3. Environment variables:
   - `VITE_API_URL` = ApiBaseUrl from SAM
   - `VITE_AGENT_URL` = `http://127.0.0.1:9877`
4. Save and redeploy (Vite bakes these in at build time).

Manual preview without Amplify:

```powershell
cd frontend
$env:VITE_API_URL="https://YOUR_API.execute-api.us-east-1.amazonaws.com"
$env:VITE_AGENT_URL="http://127.0.0.1:9877"
npm.cmd run build
npm.cmd run preview
```

### 3. Laptop agent (required for fingerprinting)

```powershell
python agent/setup_check.py serve --api https://YOUR_API.execute-api.us-east-1.amazonaws.com
# default agent port should be 9877 to match the UI
```

### 4. Smoke test

```powershell
curl https://YOUR_API.execute-api.us-east-1.amazonaws.com/health
# expect llm.enabled true, provider groq
```

## Scoring

| Signal | Weight | Notes |
| --- | --- | --- |
| Runtime match | 50 | Python/Node version vs this laptop (Node major is a minimum) |
| Tooling | 25 | git, pip/npm — Node present counts as npm on Windows |
| Env / services | 10 | collapsed into one config card when possible |
| Sandbox install | 8 | optional until you approve |
| Boot check | 7 | only when the repo has a real start command |

Fingerprint-only never claims fully ready. Use **Future-crash preview** for the ordered failure chain on this PC.
