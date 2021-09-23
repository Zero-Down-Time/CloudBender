ARG RUNTIME_VERSION="3.9"
ARG DISTRO_VERSION="3.14"
ARG PULUMI_VERSION=latest

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
    openssl-dev

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



# Now build the final runtime
FROM python:${RUNTIME_VERSION}-alpine${DISTRO_VERSION}

# Install GCC (Alpine uses musl but we compile and link dependencies with GCC)
RUN apk add --no-cache \
    libstdc++ \
    libc6-compat \
    ca-certificates \
    podman

COPY --from=builder /venv /venv
COPY --from=builder /root/.pulumi/bin /usr/local/bin
RUN mkdir /workspace && \
    cd /usr/bin && ln -s podman docker && \
    cd /usr/local/bin && \
    rm -f *dotnet *nodejs *go
WORKDIR /workspace

ENV VIRTUAL_ENV=/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Dont run as root by default
RUN addgroup cloudbender && adduser cloudbender -G cloudbender -D
USER cloudbender

CMD ["cloudbender"]
