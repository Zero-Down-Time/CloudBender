import '.ci/python.just'

# container image tasks
mod container '.ci/container.just'

toolchain := "python"
registry := "public.ecr.aws/zero-downtime/cloudbender"

default: build

# Convenience wrappers — pass the registry through to module recipes
push image="":
  just container::push {{ registry }} {{ image }}

ecr-login:
  just container::ecr-login {{ registry }}

create-repo image="":
  just container::create-repo {{ registry }} {{ image }}
