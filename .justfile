import '.ci/python.just'

# container image tasks
mod container '.ci/container.just'

toolchain := "python"
registry := "public.ecr.aws/zero-downtime/cloudbender"

default: build
