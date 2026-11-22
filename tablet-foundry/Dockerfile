# TOLKIEN — the Fellowship's own image, split out of the orchestrator image.
#
# TWO consumers build from this context: `tablet-foundry` (the head) and `tablet-watchdog`
# (its outside supervisor). A tablet commit cannot rebuild the image HAI's brain runs
# from, and a chat deploy cannot disturb the Fellowship mid-run.
#
# THE ONE DELIBERATE COUPLING, narrow and named: the head imports the fleet's placement brain
# (`scheduler.py` + its only top-level dep `config.py`) rather than reimplementing it — that is
# the Fellowship order-taker's documented design ("the SCHEDULER half already exists and is
# imported, never reimplemented"). Both files are COPY'd AT BUILD TIME from the orchestrator's
# own build context via an additional build context, so there is exactly ONE canonical copy on
# disk and nothing to hand-sync. THE RULE THAT FOLLOWS: a change to scheduler.py or config.py
# rebuilds BOTH images (orchestrator AND tolkien); a tablet-only change rebuilds tolkien only;
# an orchestrator-app change rebuilds orchestrator only. The deploy runbook carries the consumer
# table.
#
# Deliberately ABSENT vs the orchestrator image: mediapipe/GL libraries, ffmpeg, the genetic-mesh
# subpackage, face_landmarker.task — none of it runs on the head, and its absence is what makes
# tablet builds fast and keeps this image from quietly becoming a second orchestrator.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    adb \
    libgomp1 libibverbs1 \
    && rm -rf /var/lib/apt/lists/*
# libgomp1 + libibverbs1: the only libs rigby's host-built llama-server (mounted read-only at /llama/bin for the
# voltron order kind) needs beyond this base image. llama.cpp itself is NOT built here.
# The GT 730 (Vulkan) server needs NOTHING from this image: it runs under the host's own loader and libraries
# (/hostlib, from rigby's compose file) - tolkien_llm.HOSTLIB says why.
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .
# the placement brain, from the orchestrator's OWN context (additional build context "orch",
# declared in docker-compose.override.yml) — one canonical source, baked at build time
COPY --from=orch scheduler.py config.py model_fit.py ./
# the ONE answer grader, from the GPU foundries' own build (additional build context "foundry" = a directory
# fleet_deploy_tolkien ships from mothership-build and sha-verifies) - rigby grades an answer as the menu does
COPY --from=foundry grade_jobs.py ./
# A job's page in a browser on rigby's GPU (tolkien_page): one render.py + one request guard with the coding runner and the
# GPU factories (test_fleet_deploy.TheRenderToolIsOneSource), one Playwright version with both, the browser where an unprivileged user can run it.
COPY --from=foundry render.py netguard.py ./
# rigby's chat window IS the GPU boxes' window program (tolkien_llm.run_window -> helper_window.serve): the
# program and the two modules it imports, stdlib only, the same bytes the GPU boxes run
COPY --from=foundry helper_window.py infer_ollama.py gpu_spill.py ./
# rigby's load report is the GPU boxes' own reporter (tolkien_telemetry.load_report_loop): the Lab\bin file
# the GPU boxes run from C:\ProgramData\HAI, shipped into the foundry context by fleet_deploy_tolkien
COPY --from=foundry gpu_load_reporter.py ./
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium && chmod -R a+rX /ms-playwright && rm -rf /var/lib/apt/lists/*
CMD ["python", "tablet_runner.py"]
