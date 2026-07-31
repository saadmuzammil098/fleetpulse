# syntax=docker/dockerfile:1.7
#
# Multi-stage build for the Task 4 Fleet Health Risk API.
#
# Build context is the repo root (fleetpulse/), not task-5/ — the app code
# being containerized lives in task-4/src, and this Dockerfile intentionally
# does not duplicate it. Build from the repo root:
#   docker build -f task-5/Dockerfile -t fleetpulse-api:latest .
# (docker-compose.yml sets this context/dockerfile pairing for you.)
#
# Stage 1 installs dependencies into a user site-packages dir with a
# BuildKit pip cache mount, so repeat builds don't re-download the same
# wheels. Stage 2 is the actual runtime image: no compiler, no pip cache,
# no build tooling — just Python, the installed packages, and the app.

FROM python:3.12-slim AS builder

WORKDIR /build
COPY task-5/requirements.txt .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --user --no-warn-script-location -r requirements.txt

FROM python:3.12-slim AS runtime

# Trim the base Debian image down to what a headless Python API process
# actually uses at runtime. perl-base and util-linux/login's CLI tools
# (mount, su, login itself) are pulled in by the base image for
# interactive/administrative use we never do in a running container —
# and, as of this build, perl-base alone accounted for a CRITICAL and
# several HIGH CVEs (see task-5/reports/trivy_report.md: purging these
# took the scan from 23 HIGH/CRITICAL findings to 0). USER switches to
# non-root below, so no admin tooling is needed afterward either.
RUN apt-get purge -y --allow-remove-essential perl-base login util-linux ncurses-bin && \
    rm -rf /var/lib/apt/lists/*

# Non-root service user — the API never needs root inside the container.
RUN groupadd --system fleetpulse && \
    useradd --system --gid fleetpulse --home-dir /home/fleetpulse --create-home fleetpulse

# --chown up front, not a separate `chown -R` RUN afterward — chowning a
# large tree in its own layer forces a full copy-up of every file under
# overlayfs, nearly doubling this layer's size for no benefit.
COPY --chown=fleetpulse:fleetpulse --from=builder /root/.local /home/fleetpulse/.local
COPY --chown=fleetpulse:fleetpulse task-4/src /app/task-4/src

# Fallback default for task-4/src/config.py's TASK3_ROOT when this image
# is run directly (`docker run`, no FLEETPULSE_TASK3_ROOT set) — resolves
# to /app/task-3, an empty placeholder. In real use (docker-compose, or
# any FLEETPULSE_TASK3_ROOT-set `docker run`) task-3 is bind-mounted at
# its real host path instead — see README "why the model isn't baked in"
# for why a fixed relative mount point can't work in general.
RUN mkdir -p /app/task-3 && chown fleetpulse:fleetpulse /app/task-3

ENV PATH="/home/fleetpulse/.local/bin:${PATH}" \
    PYTHONPATH="/home/fleetpulse/.local/lib/python3.12/site-packages" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/task-4
USER fleetpulse

EXPOSE 8811

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8811/health', timeout=2).read()" || exit 1

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8811"]
