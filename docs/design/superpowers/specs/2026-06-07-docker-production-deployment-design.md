# Docker Production Deployment Improvements Design

## Goal

Improve DeerFlow production Docker deployment so an operator can install, configure, start, inspect, update, and troubleshoot the production stack from documented commands.

This work focuses on production deployment only. Docker development commands such as `make docker-start` and `scripts/docker.sh` remain out of scope unless a shared helper is needed.

## Current State

- `scripts/deploy.sh` supports `build`, `start`, default build-and-start, and `down`.
- `docker/docker-compose.yaml` defines the production topology: `nginx`, `frontend`, `gateway`, and optional `provisioner`.
- `Makefile` exposes only `make up` and `make down` for production Docker.
- `README.md` and `README_zh.md` mention production deployment, but the operational guidance is brief.
- Runtime config and data paths are controlled by `DEER_FLOW_CONFIG_PATH`, `DEER_FLOW_EXTENSIONS_CONFIG_PATH`, `DEER_FLOW_HOME`, `DEER_FLOW_DOCKER_SOCKET`, and `PORT`.

## Proposed Approach

Use the existing production script as the single production deployment entrypoint and make it more operator-friendly.

### Script Interface

Keep existing commands compatible:

- `scripts/deploy.sh` builds and starts production services.
- `scripts/deploy.sh build` builds images.
- `scripts/deploy.sh start` starts pre-built images.
- `scripts/deploy.sh down` stops and removes production containers.

Add production operations:

- `scripts/deploy.sh help` prints command and environment usage.
- `scripts/deploy.sh config` prints the resolved deployment configuration without starting services.
- `scripts/deploy.sh status` shows production container status.
- `scripts/deploy.sh logs [service]` tails logs for all services or one service.
- `scripts/deploy.sh restart` restarts production services without rebuilding.

### Preflight Checks

Before commands that require Docker, the script should check:

- Docker CLI exists.
- Docker daemon is reachable.
- Docker Compose plugin is usable.
- `config.yaml` exists or can be seeded from `config.example.yaml`.
- `extensions_config.json` exists or can be created safely.
- `DEER_FLOW_HOME` exists or can be created.
- `BETTER_AUTH_SECRET` exists in the environment or can be generated and persisted.
- Docker socket exists when sandbox mode requires AioSandboxProvider without provisioner.
- Provisioner mode starts the `provisioner` service only when `config.yaml` has `sandbox.use: deerflow.community.aio_sandbox:AioSandboxProvider` and a non-empty `provisioner_url`.

Failures should be actionable: each error should include the missing item and the smallest next command or configuration change.

### Makefile Commands

Keep current commands:

- `make up`
- `make down`

Add production aliases:

- `make prod-config`
- `make prod-status`
- `make prod-logs`
- `make prod-restart`

These targets should call `scripts/deploy.sh` instead of duplicating deployment logic.

### Documentation

Add a Chinese production Docker deployment manual at:

- `docs/DOCKER_PRODUCTION_DEPLOYMENT_zh.md`

The manual should cover:

- Recommended server sizing.
- Prerequisites.
- First deployment.
- Required files and environment variables.
- Runtime data persistence.
- Start, stop, restart, status, and logs.
- Updating images after code changes.
- Optional mirrors for restricted networks.
- Sandbox mode differences: local, AIO Docker, provisioner.
- Security notes for non-local deployments.
- Common troubleshooting steps.

Update `README_zh.md` to link to the manual from the Docker production section.

## Error Handling

- Do not overwrite existing user config values.
- Do not print secret values.
- Do not delete runtime data during `down`.
- If a generated secret file cannot be created, stop with instructions to set `BETTER_AUTH_SECRET` manually.
- If Docker is unavailable, stop before any compose command.
- If compose configuration cannot be rendered, print the failing command context and stop.

## Testing And Verification

Use static and light runtime checks:

- `bash -n scripts/deploy.sh`
- `scripts/deploy.sh help`
- `scripts/deploy.sh config`
- `docker compose -p deer-flow -f docker/docker-compose.yaml config`

If Docker is not reachable in the current environment, report that runtime compose verification was not run.

## Out Of Scope

- Changing the production container topology.
- Adding remote SSH deployment.
- Adding CI/CD pipeline support.
- Changing local development commands.
- Migrating runtime data.
