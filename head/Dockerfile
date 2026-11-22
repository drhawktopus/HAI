FROM python:3.12-slim
# openssh-client: on-demand SSH probe of a GPU box's free VRAM (gaming-busy check) via gpu-free.ps1
# ffmpeg: provides ffprobe for the media_probe tool (audio/subtitle track / dual-audio detection)
# libgles2/libgl1/libglib2.0-0: mediapipe's C bindings (identity pipeline) dlopen GLES even for
# CPU inference — slim lacks them (OSError libGLESv2.so.2 otherwise)
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client ffmpeg \
    libgles2 libegl1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .
# genetic-mesh subsystem (#19 Stages 1-4) — its own in-tree subpackage; COPY *.py misses subdirs
COPY genetic_mesh/ ./genetic_mesh/
# MediaPipe FaceLandmarker model — the identity pipeline's landmark extractor (#19 P3)
ADD --checksum=sha256:64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task ./face_landmarker.task
# McCoy's question set (#34 Phase 8): mccoy.py loads it at import, and its exact wording is pinned to the 0.90
# threshold. COPY *.py misses it, and the import error shows only in the gate's collection count.
COPY mccoy_questions.json .
# Tolkien's questions for McCoy (shadow until measured): mccoy.py loads this at import too.
COPY tolkien_questions.json .
# The release welcome + spotlight tour, served at /ui/<name> beside the chat page. COPY *.py misses them.
COPY ui/ ./ui/
EXPOSE 8099
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8099"]
