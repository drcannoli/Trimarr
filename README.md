# Trimmarr

Web UI for Sonarr cleanup by tag. Connects to Sonarr via API, lists monitored series with trimmarr tags, and deletes older episodes/seasons while unmonitoring them.

## Run with Docker

Copy `.env.example` to `.env` and set `SONARR_URL` and `SONARR_API_KEY`.

```bash
docker compose up -d
```

Open http://localhost:8080

### Pre-built images (amd64, arm64)

Images are built on each release:

```bash
docker pull ghcr.io/drcannoli/trimarr:latest
docker run -d -p 8080:8080 --env-file .env ghcr.io/drcannoli/trimarr:latest
```

Use a specific version tag (e.g. `1.0.0`) for reproducible deployments.

### Build from source

```bash
docker build -t trimmarr .
docker run -d -p 8080:8080 --env-file .env --name trimmarr trimmarr
```

Single-use mode (run cleanup once and exit, for cron):

```bash
docker run --rm --env-file .env -e TRIMMARR_RUN=true ghcr.io/drcannoli/trimarr:latest
```

## Environment

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| SONARR_URL | yes | http://localhost:8989 | Sonarr API base URL |
| SONARR_API_KEY | yes | | From Sonarr Settings > General > API Key |
| TRIMMARR_DRY_RUN | no | true | When true, Execute performs dry run only |
| TRIMMARR_RUN | no | false | When true, run cleanup once and exit (no web server) |
| TRIMMARR_INTERVAL | no | 0 | Hours between scheduled cleanups in server mode. Set 0 to disable. Ignored when TRIMMARR_RUN=true. |

## Modes

**Server mode** (default): Web UI and background scheduler. With `TRIMMARR_INTERVAL` > 0, cleanup runs every N hours.

**Single-use mode** (`TRIMMARR_RUN=true`): No web UI. Runs cleanup once and exits. Schedule via cron, k8s CronJob, etc.

## Tag format

Create tags in Sonarr (Settings > Tags) and assign to series:

- `trimmarr_retain_X_season` / `trimmarr_retain_X_seasons` keeps the X most recent seasons (with files)
- `trimmarr_retain_X_episode` / `trimmarr_retain_X_episodes` keeps the X most recent episodes by air date
- Both tags: keeps X full seasons plus Y most recent episodes from the next season back

Each series uses the rule from its first matching trimmarr tag. Deletion removes the episode file from disk and unmonitors the episode in Sonarr.

## Releases

Versions follow [semantic versioning](https://semver.org/). Use [conventional commits](https://www.conventionalcommits.org/) to trigger releases:

- `feat:` minor bump (1.0.0 → 1.1.0)
- `fix:` patch bump (1.0.0 → 1.0.1)
- `feat!:` or `BREAKING CHANGE:` major bump (1.0.0 → 2.0.0)
