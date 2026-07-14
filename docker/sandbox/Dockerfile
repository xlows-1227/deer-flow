# Custom DeerFlow sandbox image with pre-installed Python packages.
# Extends the official AIO sandbox so Browser/VNC/VSCode/MCP remain intact.
#
# Build:
#   docker build -t deer-flow-sandbox:enhanced docker/sandbox
#
# Pin a release tag for reproducibility (see agent-infra/sandbox releases).
ARG BASE_IMAGE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.11.0
FROM ${BASE_IMAGE}

USER root

# System deps: Graphviz rendering, CJK/emoji fonts for PPT and charts
RUN apt-get update && apt-get install -y --no-install-recommends \
    graphviz \
    fontconfig \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Python packages — split layers so enhancing deps can rebuild without reinstalling base
COPY requirements-base.txt /tmp/requirements-base.txt
COPY requirements-enhanced.txt /tmp/requirements-enhanced.txt
RUN pip install --no-cache-dir -r /tmp/requirements-base.txt \
    && pip install --no-cache-dir -r /tmp/requirements-enhanced.txt \
    && rm /tmp/requirements-*.txt

# Do not set USER gem here: upstream entrypoint creates the gem user at runtime.
# Leaving the image default user lets /opt/gem/run.sh bootstrap correctly.
