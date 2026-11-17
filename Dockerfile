# CPU-only image for the karate-coaching paper notebook.
# Code, data, secrets and outputs are NOT baked in; they are mounted at run time.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TF_CPP_MIN_LOG_LEVEL=2

# System libraries needed by OpenCV / MediaPipe
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Non-root user matching the host UID/GID so files written to mounted folders are yours, not root's
ARG HOST_UID=1000
ARG HOST_GID=1000
RUN groupadd -g ${HOST_GID} dev \
 && useradd -m -u ${HOST_UID} -g ${HOST_GID} -s /bin/bash dev \
 && mkdir -p /home/dev/.cache && chown -R dev:dev /home/dev

WORKDIR /workspace/karate-paper

# Dependencies in their own layer: only rebuilt when requirements.txt changes
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

USER dev
EXPOSE 8888
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser"]
