import '.ci/python.just'

# container image tasks
mod container '.ci/container.just'

toolchain := "python"

default: build
