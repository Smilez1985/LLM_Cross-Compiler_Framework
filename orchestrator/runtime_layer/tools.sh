# shellcheck shell=sh
# tools.sh - the runtime layer's own tools, sourced by entrypoint.sh, runner.sh and cleanup.sh (never run on its own)
#
# The framework's runtime layer is one folder the host bind-mounts read-only into every build container at /llmcf
# (orchestrator/Core/runtime_layer.py): this file, entrypoint.sh, runner.sh (the stage runner), stage_user.sh (the
# stage account), stage_env.sh (what every stage module finds), cleanup.sh, and bin/ with a static busybox (sh and
# the small tools below) and a static util-linux setpriv. The host checks every file by sha256 against the shipped
# copy before every build. The target image and its Dockerfile cannot change these files, and while the runner holds
# its two rights (CAP_SETUID, CAP_SETGID) it runs nothing of the image: every tool below is the layer's busybox and
# the runner's PATH leads nowhere. Only the stage modules run the image's programs, as the stage account without any
# capability.
#
# LLMCF_LAYER is the folder of the script that sources this file (its own path, never the environment: an image could
# set any variable). Started by hand without bin/busybox (a test, a check of the repository), the tools are the
# system's; the build sandbox refuses that (entrypoint.sh, stage_preflight).

BB="$LLMCF_LAYER/bin/busybox"
# shellcheck disable=SC2034 # read by the scripts that source this file
SETPRIV="$LLMCF_LAYER/bin/setpriv"
LAYER_APPLETS="basename cat chgrp chmod cut date dirname env find head id mkdir mktemp mv paste rm sed sha256sum sleep
sort stat tr wc"

# layer_tools: every applet as a function over the layer's busybox, and a PATH into nothing. The PATH names a folder
# inside the read-only layer that does not exist: a folder elsewhere (/llmcf-runner-has-no-path) is one any image can
# create and fill; nothing can appear inside the layer (layer_check refuses any extra file, the host's inspect check any
# mount inside it).
layer_tools() {
    for _applet in $LAYER_APPLETS; do
        eval "$_applet() { \"\$BB\" $_applet \"\$@\"; }"
    done
    unset _applet
    # shellcheck disable=SC2123 # on purpose: the runner's PATH leads nowhere
    PATH="$LLMCF_LAYER/no-path"
    export PATH
}

layer_has_tools() { [ -f "$BB" ] && [ -x "$BB" ]; }

if layer_has_tools; then
    layer_tools
fi
