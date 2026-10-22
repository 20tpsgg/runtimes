# syntax=docker/dockerfile:1.7

# CI resolves this tag to a digest once, then tests and publishes that exact base.
ARG BASE_IMAGE=eclipse-temurin:25-jdk-noble
FROM ${BASE_IMAGE}

ARG BASE_IMAGE
ARG JAVA_MAJOR=25
ARG REVISION=local
ARG JAVA_SUPPORT=lts
ARG JDK_VENDOR=Temurin
ARG MINIMUM_JAVA_VERSION=25.0.5

LABEL org.opencontainers.image.title="20tps Java runtime" \
      org.opencontainers.image.description="Java runtime for Minecraft servers managed by the 20tps daemon" \
      org.opencontainers.image.source="https://github.com/20tpsgg/runtimes" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.base.name="${BASE_IMAGE}" \
      gg.20tps.java.major="${JAVA_MAJOR}" \
      gg.20tps.java.support="${JAVA_SUPPORT}" \
      gg.20tps.java.vendor="${JDK_VENDOR}"

RUN if [ -n "$MINIMUM_JAVA_VERSION" ]; then \
        installed=$(java -XshowSettings:properties -version 2>&1 \
            | awk '$1 == "java.version" {print $3}' | tr '_' '.'); \
        dpkg --compare-versions "$installed" ge "$MINIMUM_JAVA_VERSION" \
            || { echo "Java $installed is older than required $MINIMUM_JAVA_VERSION; waiting for an updated Temurin base image." >&2; exit 1; }; \
    fi

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        bash ca-certificates curl fontconfig git iproute2 jq \
        libfreetype6 libstdc++6 lsof openssl sqlite3 tar tzdata unzip \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 988 container \
    && useradd --uid 988 --gid 988 --home-dir /home/container \
        --create-home --shell /bin/bash container

COPY --chmod=755 java/entrypoint.sh /entrypoint.sh

ENV USER=container \
    HOME=/home/container \
    TZ=UTC

USER 988:988
WORKDIR /home/container

# Retain Temurin's CA entrypoint. It ultimately execs this command and leaves
# JAVA_TOOL_OPTIONS intact, including the daemon's injected Minecraft agent.
CMD ["/bin/bash", "/entrypoint.sh"]
