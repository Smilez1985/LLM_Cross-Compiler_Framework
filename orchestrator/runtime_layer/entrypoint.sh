# entrypoint.sh - the entrypoint of every build container (part of the runtime layer; tools.sh says what that is)
#
# The host sets it as the container's entrypoint itself (/llmcf/bin/busybox sh /llmcf/entrypoint.sh) and starts the
# container as root with exactly CAP_SETUID and CAP_SETGID, no-new-privileges, a read-only image and no network: the
# image's own ENTRYPOINT, CMD and USER are never used for a build. It forgets what an image could have put into the
# environment to steer a shell (BASH_ENV, ENV, IFS, CDPATH), keeps the image's PATH for the stage modules only, makes
# the stage runner the user STAGE_RUNNER_IDS names (the person's uid:gid; 0:0 under a rootless engine, where root in
# the container is the person) with those two rights as ambient capabilities, through the layer's own setpriv, and
# starts runner.sh on the layer's own busybox sh. Its supplementary groups are the container's own (a GPU's render
# group), without root's group.

unset BASH_ENV ENV CDPATH
unset IFS   # the default field splitting (space, tab, newline), whatever the environment said
case "$0" in
    */*) LLMCF_LAYER="${0%/*}" ;;
    *) LLMCF_LAYER="." ;;
esac
LLMCF_LAYER="$(cd "$LLMCF_LAYER" && pwd)"
LLMCF_IMAGE_PATH="$PATH"
export LLMCF_LAYER LLMCF_IMAGE_PATH
# shellcheck source=tools.sh
. "$LLMCF_LAYER/tools.sh"

refuse() {  # CODE TEXT
    echo "ERROR: $2; nothing was run." >&2
    exit "$1"
}

layer_has_tools || refuse 90 "The framework's runtime layer has no tools (bin/busybox)"
[ -f "$SETPRIV" ] && [ -x "$SETPRIV" ] || refuse 84 "The framework's runtime layer has no setpriv"

ids="${STAGE_RUNNER_IDS:-}"
case "$ids" in
    *[!0-9:]*|:*|*:|*:*:*|'') refuse 85 "The build sandbox gave no valid runner ids (STAGE_RUNNER_IDS uid:gid)" ;;
esac
case "$ids" in *:*) ;; *) refuse 85 "The build sandbox gave no valid runner ids (STAGE_RUNNER_IDS uid:gid)" ;; esac
uid="${ids%%:*}"
gid="${ids##*:}"

if [ "$uid" = "0" ]; then
    exec "$BB" sh "$LLMCF_LAYER/runner.sh" "$@"
fi
groups="$(id -G | tr ' ' '\n' | sed '/^0$/d' | paste -sd, -)"
if [ -n "$groups" ]; then
    exec "$SETPRIV" --reuid="$uid" --regid="$gid" --groups="$groups" \
        --inh-caps=-all,+setuid,+setgid --ambient-caps=-all,+setuid,+setgid -- "$BB" sh "$LLMCF_LAYER/runner.sh" "$@"
fi
exec "$SETPRIV" --reuid="$uid" --regid="$gid" --clear-groups \
    --inh-caps=-all,+setuid,+setgid --ambient-caps=-all,+setuid,+setgid -- "$BB" sh "$LLMCF_LAYER/runner.sh" "$@"
