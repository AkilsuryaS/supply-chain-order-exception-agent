# Vercel deployment

The repository includes a root FastAPI application in `app.py`, a thin Vercel Python Function entry point in `api/index.py`, and routing configuration in `vercel.json`. The browser UI, mock ERP endpoints, deterministic workflow, and Hugging Face agent are served from the same HTTPS origin.

## Deploy

Requirements:

- A Vercel account
- Node.js and npm, or an installed Vercel CLI
- A public GitHub repository if you want automatic deployments from Git

From the repository root:

```bash
npx vercel@latest
npx vercel@latest --prod
```

The first command signs in, creates or links the Vercel project, and produces a preview deployment. The second promotes the current revision to the production URL. Vercel can also import the GitHub repository through its dashboard to deploy every push to `main`.

Do not configure a shared `HF_TOKEN` when the application is intended as a bring-your-own-token demo. Each visitor enters a Hugging Face token in the password field; the browser sends it in the `X-HF-Token` request header over HTTPS, and the backend uses it only for that inference request. The application does not put it in browser storage, API responses, traces, metrics, or application logs.

## Validate

Replace the example host with the production URL:

```bash
curl -fsS 'https://your-project.vercel.app/health'
curl -fsS 'https://your-project.vercel.app/ready'
curl -fsS 'https://your-project.vercel.app/agent/exceptions'
```

Open `https://your-project.vercel.app/agent/llm-triage` to test the hosted agent with a personal Hugging Face token.

## Serverless boundaries

The FastAPI process is stateless between invocations. Synthetic order data ships with the deployment, while trace and audit buffers are process-local and best-effort. Different warm instances do not share telemetry, and cold starts clear it. A production implementation should export OpenTelemetry data to a durable backend and store audit decisions in a database or event stream.

Vercel request logs may contain paths, status codes, and platform metadata. Tokens are deliberately sent in a header rather than a URL or request body, but users should still create a narrowly scoped Hugging Face token and revoke it after a public demo if desired. Never paste tokens into source files, Git commits, screenshots, URLs, or support messages.

The configured function duration is 60 seconds, subject to the selected Vercel plan and runtime. Model availability, provider routing, Hugging Face credits, and Vercel limits remain external dependencies. The deterministic baseline continues to work if hosted LLM inference is unavailable.
