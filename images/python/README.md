# Python base images (apko + Wolfi)

Two image definitions for the backend, built with
[apko](https://github.com/chainguard-dev/apko) from public
[Wolfi](https://github.com/wolfi-dev) packages:

| File | Role | Contents |
|---|---|---|
| `runtime.apko.yaml` | final stage of `backend/Dockerfile.production` | CPython, its shared libraries, CA certificates; no shell, no package manager; runs as `nonroot` (65532) |
| `builder.apko.yaml` | build stage | the same CPython plus busybox, bash, pip, setuptools and a C toolchain |

## Why

The interpreter is pinned to one CPython minor version through the Wolfi
package stream (`python-3.14`), while every other package floats to its
newest build each time the images are rebuilt. A rebuild therefore picks
up security fixes without changing the Python version underneath the
application. Moving to a new minor version is a deliberate edit: the
stream name in both files.

Both files must name the same stream. The build stage installs the
application's dependencies into its site-packages and the runtime stage
receives a copy of them, so the two interpreters have to agree on the
version.

## Building locally

apko needs no Docker daemon:

```sh
apko build images/python/runtime.apko.yaml pablo-python:runtime runtime.tar
apko build images/python/builder.apko.yaml pablo-python:builder builder.tar
docker load < runtime.tar
docker load < builder.tar
```

`docker load` prints the loaded tags (apko appends the architecture,
e.g. `pablo-python:runtime-amd64`). Then build the backend on them:

```sh
docker build -f backend/Dockerfile.production \
  --build-arg BUILDER_IMAGE=pablo-python:builder-amd64 \
  --build-arg RUNTIME_IMAGE=pablo-python:runtime-amd64 \
  -t pablo-backend:apko .
```

Without the two `--build-arg`s, `Dockerfile.production` builds on its
pinned default bases, as before.

## Checking package names

Package names are Wolfi's, not a distribution's. To confirm that a name
exists before adding it:

```sh
curl -sSL https://packages.wolfi.dev/os/x86_64/APKINDEX.tar.gz | tar -xzO APKINDEX \
  | grep -x 'P:python-3.14'
```

`apko lock <file>` resolves the full package set (with versions) into a
`.lock.json` next to the config. That records exactly what a given build
contained.
