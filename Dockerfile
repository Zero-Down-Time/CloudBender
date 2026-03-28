FROM alpine:3.23.3

# renovate: datasource=alpine-overlay depName=pulumi
ARG PULUMI=3.228.0
ARG USER=cloudbender

# trades about 300MB container size for 5s more startup latency
# ENV PYTHONDONTWRITEBYTECODE=1

RUN ALPINE_VERSION=$(. /etc/os-release && echo "$VERSION_ID" | cut -d. -f1,2) && \
    cd /etc/apk/keys && \
    wget "https://cdn.zero-downtime.net/alpine/stefan@zero-downtime.net-61bb6bfb.rsa.pub" && \
    echo "@kubezero https://cdn.zero-downtime.net/alpine/v${ALPINE_VERSION}/kubezero" >> /etc/apk/repositories && \
    apk upgrade -U -a --no-cache && \
    apk add --no-cache \
    ca-certificates \
    podman \
    passt \
    py3-boto3 \
    aws-cli \
    pulumi@kubezero~${PULUMI} \
    pulumi-language-python@kubezero~${PULUMI}

ADD dist /dist

RUN python3 -m venv venv && \
    . /venv/bin/activate && \
    pip install --no-cache-dir dist/cloudbender-*.whl

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

ENV _CONTAINERS_USERNS_CONFIGURED=""

ENV VIRTUAL_ENV=/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PULUMI_SKIP_UPDATE_CHECK=true

USER $USER

# Allow container layers to be stored in PVCs
VOLUME /home/$USER/.local/share/containers

CMD ["cloudbender"]
