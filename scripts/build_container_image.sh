#!/bin/bash
# Builds the LLM Cross-Compiler Framework as a container image on this computer.
#
# No framework image is published or pulled: the image is built here, from exactly the files the repository ships
# (git ls-files, as install.sh copies them), by running the same installation inside the container as on a Debian
# computer (install.sh). It gets a local tag, and nothing is pushed anywhere. The only image Docker downloads is the
# Debian base image, pinned by digest in "Docker Setup/Dockerfile".
#
#   scripts/build_container_image.sh                 headless image (CLI and MCP server), tag lcf-framework:local
#   scripts/build_container_image.sh --gui           also the desktop libraries for the GUI, tag lcf-framework:local-gui
#   scripts/build_container_image.sh --tag NAME      another local tag (no registry part such as ghcr.io/ in it)
#   scripts/build_container_image.sh --uid N --gid N the container user's ids (default: yours; never 0)
#
# Exit codes: 0 built, 1 the build failed, 2 a wrong option or a missing prerequisite (Docker, BuildKit, git).
# Guide: docs/Container_Setup.md (German: docs/Container_Setup_DE.md).

set -euo pipefail

here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo=$(CDPATH='' cd -- "$here/.." && pwd)
dockerfile_rel="Docker Setup/Dockerfile"
marker=".lcf-container-context"

gui=0
tag=""
uid=$(id -u)
gid=$(id -g)

usage() {
    sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

fail() {   # a wrong option or a missing prerequisite
    echo "build_container_image.sh: $*" >&2
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --gui) gui=1 ;;
        --tag) [ $# -ge 2 ] || fail "--tag needs a name"; tag=$2; shift ;;
        --tag=*) tag=${1#--tag=} ;;
        --uid) [ $# -ge 2 ] || fail "--uid needs a number"; uid=$2; shift ;;
        --gid) [ $# -ge 2 ] || fail "--gid needs a number"; gid=$2; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown option '$1' (see --help)" ;;
    esac
    shift
done

if [ -z "$tag" ]; then
    if [ "$gui" = 1 ]; then tag="lcf-framework:local-gui"; else tag="lcf-framework:local"; fi
fi

# A local name only: [namespace/]name[:tag], lowercase. A first part with a dot or a colon (ghcr.io/..., host:5000/...)
# or "localhost" would name a registry, which suggests publishing; the framework never pushes an image.
local_tag_ok() {
    local name=$1 first
    [[ $name =~ ^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)?(:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?$ ]] ||
        return 1
    first=${name%%/*}
    if [ "$first" != "$name" ] && { [[ $first == *.* ]] || [ "$first" = localhost ]; }; then
        return 1
    fi
    return 0
}
local_tag_ok "$tag" ||
    fail "'$tag' is not a local image name. Use a name such as lcf-framework:local, without a registry (no ghcr.io/, docker.io/, host:port/): the image stays on this computer."

[[ $uid =~ ^[0-9]+$ && $gid =~ ^[0-9]+$ ]] || fail "--uid and --gid take numbers"
# Built by root (sudo), the container user would be root too: use the usual first user's ids instead.
if [ "$uid" = 0 ]; then uid=1000; fi
if [ "$gid" = 0 ]; then gid=1000; fi

# --- prerequisites -----------------------------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 ||
    fail "Docker is not installed. Install Docker Engine (Linux) or Docker Desktop, then run this again."
# No Docker daemon, but the person's Podman socket (install.sh's fallback on Rocky Linux, AlmaLinux and Oracle Linux):
# the framework's own choice (orchestrator/utils/docker_engine.py, standard library only), asked with the system's
# python3 when there is one. A DOCKER_HOST you set always wins.
engine_helper="$repo/orchestrator/utils/docker_engine.py"
if [ -z "${DOCKER_HOST:-}" ] && command -v python3 >/dev/null 2>&1; then
    podman_host=$(python3 -I "$engine_helper" --docker-host 2>/dev/null || true)
    if [ -n "$podman_host" ]; then
        export DOCKER_HOST="$podman_host"
        echo "No Docker daemon here: building through Podman's Docker-compatible socket (DOCKER_HOST=$podman_host)."
    fi
fi
docker info >/dev/null 2>&1 ||
    fail "the Docker daemon is not reachable (is it running, and may your user use it? 'docker info' shows why)."
engine=""
if command -v python3 >/dev/null 2>&1; then
    engine=$(python3 -I "$engine_helper" --engine 2>/dev/null || true)
fi
if docker --version 2>/dev/null | grep -qi '^podman'; then engine=podman; fi
buildkit=1
if [ "$engine" = podman ]; then
    # Podman builds with its own builder (buildah), which knows the one BuildKit feature the Dockerfile uses: the
    # read-only bind mount of the staged context (RUN --mount=type=bind). Docker's CLI must not ask Podman for BuildKit.
    buildkit=0
    echo "The container engine is Podman: it builds the image with its own builder (buildah), not BuildKit."
else
    docker buildx version >/dev/null 2>&1 ||
        fail "BuildKit (docker buildx) is missing; the Dockerfile needs it. Install the docker-buildx plugin."
fi
if ! command -v git >/dev/null 2>&1 ||
        ! git -C "$repo" -c safe.directory="$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    fail "$repo is not a git checkout: the image is built from the files the repository ships (git ls-files). Clone the repository and run this there."
fi
[ -f "$repo/$dockerfile_rel" ] || fail "$dockerfile_rel is missing in $repo."

# --- the build context: only the files the repository ships --------------------------------------------------------
stage=$(mktemp -d "${TMPDIR:-/tmp}/lcf-container-context.XXXXXX")
list=$(mktemp "${TMPDIR:-/tmp}/lcf-container-files.XXXXXX")
cleanup() { rm -rf "$stage" "$list"; }
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Tracked files that exist in the work tree (a file deleted but not yet committed is skipped), like install.sh. A link
# is copied as the link, never as the file it points to.
git -C "$repo" -c safe.directory="$repo" ls-files -z | while IFS= read -r -d '' f; do
    if [ -f "$repo/$f" ] || [ -L "$repo/$f" ]; then printf '%s\0' "$f"; fi
done > "$list"
[ -s "$list" ] || fail "git ls-files listed no files in $repo."
tar -C "$repo" --null --files-from="$list" -cf - | tar -C "$stage" -xf -
printf 'Build context staged by scripts/build_container_image.sh: only the files the repository ships.\n' \
    > "$stage/$marker"

echo "Building $tag on this computer from $(tr -cd '\0' < "$list" | wc -c) shipped files (GUI libraries: $([ "$gui" = 1 ] && echo yes || echo no))..."
if ! DOCKER_BUILDKIT=$buildkit docker build \
        --file "$stage/$dockerfile_rel" \
        --build-arg "WITH_GUI=$gui" \
        --build-arg "LCF_UID=$uid" \
        --build-arg "LCF_GID=$gid" \
        --tag "$tag" \
        "$stage"; then
    echo "The image was not built (see the output above)." >&2
    exit 1
fi

name="lcf-framework"
[ "$gui" = 1 ] && name="lcf-framework-gui"
cat <<EOF

Built $tag on this computer. Nothing was pushed; no framework image was pulled.

Try it (your data lives in the volume lcf-data, mounted at /data):
  docker run --rm --pull never -v lcf-data:/data $tag llm-cli --version
  docker run --rm --pull never -v lcf-data:/data $tag llm-cli mojo

Keep it running for AI clients and for your own commands:
  docker run -d --pull never --name $name --restart unless-stopped --cap-drop ALL \
      --security-opt no-new-privileges:true -v lcf-data:/data $tag sleep infinity
  (or: make up, which uses "Docker Setup/docker-compose.yml")
  docker exec -it $name llm-cli ditto setup

MCP server for an AI client (stdio; the HTTP transport listens on the container's loopback only):
  on this computer:     docker exec -i $name llm-cli mcp serve
  from another one:     ssh user@this-computer docker exec -i $name llm-cli mcp serve
EOF
if [ "$gui" = 1 ]; then
    cat <<EOF

GUI (explicit option; the container gets access to your X11 display):
  make gui   (see docs/Container_Setup.md, "The GUI")
EOF
fi
echo
echo "Guide: docs/Container_Setup.md"
