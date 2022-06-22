ARG RUNTIME_VERSION="3.8"
ARG DISTRO_VERSION="3.15"
ARG PULUMI_VERSION="3.34.0"

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
RUN cd /root/.pulumi/bin && rm -f *dotnet *nodejs *go *java && strip pulumi* || true


# Now build the final runtime
FROM python:${RUNTIME_VERSION}-alpine${DISTRO_VERSION}

    #cd /etc/apk/keys && \
    #echo "@testing http://dl-cdn.alpinelinux.org/alpine/edge/testing" >> /etc/apk/repositories && \
    #cfssl@testing \

RUN apk upgrade -U --available --no-cache && \
    apk add --no-cache \
    libstdc++ \
    libc6-compat \
    ca-certificates \
    aws-cli \
    podman

COPY --from=builder /venv /venv
COPY --from=builder /root/.pulumi/bin /usr/local/bin
RUN mkdir /workspace && \
    cd /usr/bin && ln -s podman docker

WORKDIR /workspace

ENV VIRTUAL_ENV=/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Dont run as root by default
RUN addgroup cloudbender && adduser cloudbender -G cloudbender -D
USER cloudbender

CMD ["cloudbender"]
