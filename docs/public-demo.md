# Tezcat public demo deployment

The Tezcat public demo keeps the existing React/Vite dashboard and FastAPI API as separate public services. The dashboard is published from GitHub Pages, while the API runs as a free Render web service. The research engine, experiment schema, batch semantics, artifact formats, hash checks, and F0–F11 behavior remain unchanged.

## Public URLs

| Surface | URL | Purpose |
|---|---|---|
| Dashboard | [https://satvikndxd.github.io/Tezcat/](https://satvikndxd.github.io/Tezcat/) | Public React/Vite dashboard |
| Research route | [https://satvikndxd.github.io/Tezcat/#/research](https://satvikndxd.github.io/Tezcat/#/research) | Existing list/run/analyze/report/reproduce workflow |
| API | [https://tezcat-public-api.onrender.com](https://tezcat-public-api.onrender.com) | Public FastAPI service |
| API health | [https://tezcat-public-api.onrender.com/api/health](https://tezcat-public-api.onrender.com/api/health) | Service health and version |
| API research registry | [https://tezcat-public-api.onrender.com/api/research/experiments](https://tezcat-public-api.onrender.com/api/research/experiments) | Existing persisted research versions |

GitHub Pages publishes the built `frontend/dist` artifact through `.github/workflows/deploy-pages.yml`. This follows GitHub's documented Actions flow of building the site, uploading a Pages artifact, and deploying that artifact with the Pages deployment action.[^2] The Vite build uses `VITE_BASE_PATH=/Tezcat/`, which is required because this is a project site rather than a user site. The dashboard uses its existing hash router, so the `/research` route remains valid without server-side rewrite rules.

The frontend API client reads `VITE_API_BASE_URL`. When that value is set to the public API origin, the client requests `${VITE_API_BASE_URL}/api/...`. When it is unset, the client continues using `/api`, and Vite's existing local proxy sends those requests to `http://localhost:8000`. The Pages workflow has a non-secret fallback to `https://tezcat-public-api.onrender.com`, because the current repository token cannot create GitHub Actions repository variables. The origin is public configuration, not a credential; no API keys or secrets are included in the frontend bundle.

## Deployment architecture

| Layer | Implementation | Configuration |
|---|---|---|
| Public frontend | GitHub Pages | `.github/workflows/deploy-pages.yml`, `VITE_BASE_PATH=/Tezcat/` |
| Public backend | Render Free web service | `render.yaml`, `Dockerfile`, `uvicorn tezcat.api.app:app` |
| API contract | Existing FastAPI routes under `/api` | No route or response-contract migration |
| Research registry | Existing file-backed `Registry` | `TEZCAT_DATA_DIR=/data` |
| Demo bootstrap | Existing `ExperimentVersion`, `BatchRunner`, and `analyze` APIs composed by `scripts/seed_public_demo.py` | Runs on container startup and resumes idempotently |
| Browser-to-API access | CORS middleware | `TEZCAT_CORS_ORIGINS` includes the GitHub Pages origin and local Vite origins |

The backend container builds the existing frontend as part of the image because the FastAPI application already supports serving `frontend/dist`; the public dashboard nevertheless uses GitHub Pages as the canonical frontend URL. The backend's separate API URL is what the Pages bundle calls in production.

## Local development

The existing local workflow remains available. From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cd frontend && npm install && npm run build && cd ..
.venv/bin/uvicorn tezcat.api.app:app --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000](http://localhost:8000). For a separate Vite development server, run `npm run dev` inside `frontend`; the existing proxy maps `/api` to `http://localhost:8000`. Leave `VITE_API_BASE_URL` unset for local development. Public-demo limits are disabled unless `TEZCAT_PUBLIC_DEMO` is explicitly enabled.

The repository launcher remains available as `scripts/run_local.sh`. It still builds the dashboard when needed, creates the local virtual environment when needed, and starts FastAPI on `${PORT:-8000}`.

## Environment variables

| Variable | Public deployment value | Local value or meaning |
|---|---|---|
| `VITE_BASE_PATH` | `/Tezcat/` | Usually unset, which means `/` |
| `VITE_API_BASE_URL` | `https://tezcat-public-api.onrender.com` | Unset; Vite proxy keeps `/api` local |
| `TEZCAT_STORE` | `local` | `local`; `aws` remains an optional existing adapter |
| `TEZCAT_DATA_DIR` | `/data` | `data` |
| `TEZCAT_CORS_ORIGINS` | `https://satvikndxd.github.io,http://localhost:5173,http://localhost:4173` | Usually unset, preserving the existing permissive default |
| `TEZCAT_PUBLIC_DEMO` | `true` | Unset or `false` |
| `TEZCAT_PUBLIC_MAX_RUNS` | `60` | Not applied locally |
| `TEZCAT_PUBLIC_MAX_STEPS` | `5000` | Not applied locally |
| `TEZCAT_PUBLIC_MAX_ACTIVE_BATCHES` | `1` | Not applied locally |
| `TEZCAT_PUBLIC_MAX_ACTIVE_RUNS` | `1` | Not applied locally |
| `TEZCAT_PUBLIC_MAX_SERIES` | `2000` | Not applied locally |
| `TEZCAT_PUBLIC_MAX_EXTERNAL_ITEMS` | `50` | Not applied locally |
| `TEZCAT_PUBLIC_MAX_EXTERNAL_DATASETS` | `25` | Not applied locally |
| `TEZCAT_PUBLIC_EXTERNAL_MIN_INTERVAL` | `10` seconds | Not applied locally |

The public guardrails are enforced only at the API boundary. They reject oversized public requests or return a temporary capacity response; they do not modify engine execution, configuration hashing, seed allocation, batch ordering, artifact contents, or reproducibility semantics. S3-A adds the same boundary for external observations: discovery is item-capped, snapshot registration is dataset-count and refresh-interval capped, and no scheduled provider scraper is configured.

## External Event-Market Intelligence demo rule

The MARKETS page is read-only. The public service should expose previously registered, terms-reviewed external datasets by default; live provider reads are manual, server-side, rate-aware, and bounded. Each dataset retains provider terms and permitted-use text. No provider credentials are sent to the frontend, and the S3-A layer does not place orders, access accounts or wallets, run arbitrage, or provide financial advice. Render’s ephemeral filesystem means user-created external snapshots should be treated as temporary demo state unless the existing optional AWS artifact store is deliberately configured.

## Research demo and persistence

The public service seeds the existing `examples/margin_spiral_ab.json` specification during container startup. The seeder registers the content-addressed version `expv_417305c181af`, runs its existing 40-row batch, and writes the existing analysis artifact. The resulting public workflow is the unchanged sequence: list the registry, view the experiment, run or resume a batch, analyze, render the report, and reproduce sampled stored rows.

The registry is file-backed under `TEZCAT_DATA_DIR/registry`. The repository contains the executable margin-spiral specification but does not track generated `data/` artifacts. Render Free services have an ephemeral filesystem: local files are lost when a service redeploys, restarts, or spins down, and a Free service cannot attach a persistent disk.[^1] Therefore, this deployment intentionally reseeds the demo on startup instead of claiming durable public persistence. User-created experiments and generated artifacts should be treated as temporary demo state. The seed operation is idempotent and took approximately 22 seconds in local verification for the 40-row demo batch; a cold public startup can take longer because the free service may sleep.

Render Free web services spin down after 15 minutes without inbound traffic and can take about one minute to start again on the next request.[^1] The first request after idle may therefore experience a cold-start delay plus demo seeding. Render documents the Free instance as a testing, hobby, and preview tier rather than a production tier.[^1]

## CI/CD and redeployment

A push to `main` triggers the Pages workflow, which installs the pinned frontend lockfile dependencies, builds the dashboard with the repository base path, uploads the static artifact, and deploys it to GitHub Pages. GitHub documents that an Actions-published Pages artifact must contain the entry `index.html` at the top level of the artifact; the workflow uploads `frontend/dist`, where Vite places that entry file.[^3]

Render is configured by the root `render.yaml` Blueprint. The Blueprint is linked to `satvikndxd/Tezcat` on `main` and is set to auto-deploy. A normal redeploy is therefore:

```bash
git add <changed-files>
git commit -m "Describe the deployment change"
git push origin main
```

The Render service rebuilds from the new commit, reseeds the demo if its filesystem is empty, and exposes the same API URL. The Pages workflow runs independently and publishes the new frontend bundle. If the backend hostname ever changes, update the fallback in `.github/workflows/deploy-pages.yml` and, if available, set the repository variable `VITE_API_BASE_URL` to the new public API origin before rerunning the workflow.

## Shut down or remove the deployment

To remove the backend, open the `tezcat-public-demo` Blueprint in the Render dashboard, open its settings, and delete the Blueprint and its `tezcat-public-api` service. To remove the frontend, open the repository's **Settings → Pages** page and disable the Pages site or change its publishing source, then disable or remove `.github/workflows/deploy-pages.yml` if future pushes should not deploy. The GitHub repository and its source code remain separate from both hosted services.

## Validation record

| Check | Result |
|---|---|
| GitHub Pages HTML loads | Passed; HTTP 200 |
| Vite assets load under `/Tezcat/` | Passed; JavaScript and CSS returned HTTP 200 |
| Existing `#/research` route opens | Passed in the public dashboard navigation |
| Render API health | Passed; `/api/health` returned HTTP 200 and `status: ok` |
| Research registry loads | Passed; `expv_417305c181af` is present |
| Existing experiment can be viewed | Passed; version and design returned HTTP 200 |
| Analyze endpoint | Passed; persisted analysis returned HTTP 200 |
| Report endpoint | Passed; rendered report returned HTTP 200 |
| Reproduce endpoint | Passed; 3 sampled rows verified and `success: true` |
| CORS | Passed; the GitHub Pages origin was returned in `Access-Control-Allow-Origin` |
| Public guardrails | Configured in `render.yaml` and FastAPI boundary helpers |
| No secrets committed | Passed; deployment configuration contains only public URLs and non-secret limits |
| Focused existing research tests | Passed; 11 tests passed |
| Full existing test suite | Verified locally after restoring the documented offline `tezcat.data` file provider: 380 passed, 7 skipped. |

[^1]: [Render, “Deploy for Free”](https://render.com/docs/free)
[^2]: [GitHub Docs, “Configuring a publishing source for your GitHub Pages site”](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
[^3]: [GitHub Docs, “Troubleshooting 404 errors for GitHub Pages sites”](https://docs.github.com/en/pages/getting-started-with-github-pages/troubleshooting-404-errors-for-your-github-pages-sites)
