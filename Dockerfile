ARG RUNTIME_VERSION="3.10"
ARG DISTRO_VERSION="3.16"
ARG PULUMI_VERSION="3.40.1"

FROM python:${RUNTIME_VERSION}-alpine${DISTRO_VERSION} AS builder
ARG PULUMI_VERSION

RUN apk add --no-cache \
    autoconf \
    automake \
    build-base \
    cmake \
    curl \
    make \
    libc6-compat \
    gcc \
    linux-headers \
    libffi-dev \
    openssl-dev \
    git

RUN if [ "$PULUMI_VERSION" = "latest" ]; then \
    curl -fsSL https://get.pulumi.com/ | sh; \
  else \
    curl -fsSL https://get.pulumi.com/ | sh -s -- --version $PULUMI_VERSION ; \
  fi

ENV VIRTUAL_ENV=/venv
RUN python -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install CloudBender
WORKDIR /app
COPY . /app
RUN pip install -r requirements.txt
RUN pip install . --no-deps

# minimal pulumi
RUN cd /root/.pulumi/bin && rm -f *dotnet *yaml *go *java && strip pulumi* || true


# Now build the final runtime, incl. running rootless containers
FROM python:${RUNTIME_VERSION}-alpine${DISTRO_VERSION}

ARG USER=cloudbender

    #cd /etc/apk/keys && \
    #echo "@testing http://dl-cdn.alpinelinux.org/alpine/edge/testing" >> /etc/apk/repositories && \
    #cfssl@testing \

RUN apk upgrade -U --available --no-cache && \
    apk add --no-cache \
    libstdc++ \
    libc6-compat \
    ca-certificates \
    aws-cli \
    fuse-overlayfs \
    podman \
    buildah \
    strace

COPY --from=builder /venv /venv
COPY --from=builder /root/.pulumi/bin /usr/local/bin

# Dont run as root by default
RUN addgroup $USER && adduser $USER -G $USER -D && \
    mkdir -p /home/$USER/.local/share/containers && \
    chown $USER:$USER -R /home/$USER

# Rootless podman
RUN mkdir -p /home/$USER/.config/containers

ADD --chown=$USER:$USER conf/containers.conf conf/registries.conf conf/storage.conf /home/$USER/.config/containers

RUN echo -e "$USER:1:999\n$USER:1001:64535" > /etc/subuid && \
    echo -e "$USER:1:999\n$USER:1001:64535" > /etc/subgid && \
    cd /usr/bin && ln -s podman docker && \
    chown $USER:$USER -R /home/$USER

WORKDIR /workspace

ENV XDG_RUNTIME_DIR=/tmp/podman-run-1000
ENV _CONTAINERS_USERNS_CONFIGURED=""
ENV BUILDAH_ISOLATION=chroot

ENV VIRTUAL_ENV=/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PULUMI_SKIP_UPDATE_CHECK=true

USER $USER

# Allow container layers to be stored in PVCs
VOLUME /home/$USER/.local/share/containers

CMD ["cloudbender"]
