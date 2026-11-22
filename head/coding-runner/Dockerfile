FROM python:3.12-slim
# ── ENVIRONMENT PARITY WITH THE ORCHESTRATOR (a structural rule) ──────────────────────────────────
# This container runs the ORCHESTRATOR'S OWN TEST SUITE on behalf of HAI. An image that carried only
# the runner's lean deps could not even IMPORT the test modules that need the image pipeline
# (`No module named 'PIL'`), so the workspace suite collected fewer tests than the gate: HAI-green
# and gate-green were two derivations of one idea, and two derivations drift. The build context is
# therefore ./orchestrator (see docker-compose.override.yml) so BOTH requirement sets are visible
# from one source of truth — the orchestrator's requirements.txt is never copied or duplicated here,
# it is installed directly.
#
# apt parity matters as much as pip parity: mediapipe's C bindings dlopen GLES even for CPU-only
# inference (OSError libGLESv2.so.2 otherwise), and ffprobe backs the media_probe tool. identity.py
# imports mediapipe/insightface/scipy LAZILY, so without these a test would pass on import and fail
# the moment it exercised the real path — installed-but-not-working, which reads exactly like
# working. git + ripgrep are the runner's own tools.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git ripgrep openssh-client ffmpeg \
       libgles2 libegl1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./orchestrator-requirements.txt
COPY coding-runner/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r orchestrator-requirements.txt -r requirements.txt
# stream_clip.py is an ORCHESTRATOR-level module, copied in deliberately: the runner's output
# cap and the orchestrator's tool-reply cap are two layers of one idea; cut independently from the
# head, between them they destroy every test verdict HAI receives. One shared module, one
# derivation, tested once in the gate suite.
COPY stream_clip.py ./
# ── A REAL BROWSER, PUT HERE ON PURPOSE (the "right tools" set) ────────────────────────────────────
# A job may change its workspace freely; changing the RUNNER is the operator's call, and this is
# that call made: a job that needs to SEE its page gets the browser as part of the image, used only
# through runner.render (render.py), whose every request is checked by netguard.py - the
# orchestrator's own "public internet only" rule, copied in like stream_clip.py so the two sides
# cannot drift. Browsers live in a fixed path so any user in the container finds them.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir playwright==1.64.1 \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY netguard.py ./
COPY coding-runner/render.py ./
COPY coding-runner/app.py coding-runner/runner.py coding-runner/test_worker.py coding-runner/registry.json ./
COPY coding-runner/workspaces ./workspaces
RUN mkdir -p /state
ENV CODING_RUNNER_STATE=/state
EXPOSE 8100
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
