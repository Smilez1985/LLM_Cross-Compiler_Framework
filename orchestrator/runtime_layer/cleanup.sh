# cleanup.sh - the short clean-up container of an interrupted build (part of the runtime layer; tools.sh says what
# that is)
#
# A build stopped inside a stage module (a Cancel, a time limit, a crash) can leave files the stage account wrote and
# never gave to the shared group: a module killed in the middle runs no stage_share. Before the framework deletes such
# a build's folders (orchestrator/Core/interrupted_build.py, through Core/deletion.py), the host starts this script in
# a container as the stage account itself (it owns those files, so no capability is needed by anyone; no network,
# no capabilities, no-new-privileges, the layer read-only): it makes everything the stage account owns in the named
# folders removable for the person (owner and group may read, write and walk; nothing sticky). Nothing is deleted
# here and nothing is chowned.
#
# Usage: cleanup.sh SHARED_GID FOLDER...

unset BASH_ENV ENV CDPATH
case "$0" in
    */*) LLMCF_LAYER="${0%/*}" ;;
    *) LLMCF_LAYER="." ;;
esac
LLMCF_LAYER="$(cd "$LLMCF_LAYER" && pwd)"
# shellcheck source=tools.sh
. "$LLMCF_LAYER/tools.sh"

gid="${1:-}"
case "$gid" in ''|*[!0-9]*) echo "ERROR: cleanup.sh needs the shared group id." >&2; exit 2 ;; esac
shift
me="$(id -u)"
for folder in "$@"; do
    [ -d "$folder" ] && [ ! -L "$folder" ] || continue
    chmod -R u+rwX,g+rwX,-t "$folder" 2>/dev/null || true
    if layer_has_tools; then
        find "$folder" -user "$me" ! -type l ! -group "$gid" -exec "$BB" chgrp -h "$gid" {} + 2>/dev/null || true
    else
        find "$folder" -user "$me" ! -type l ! -group "$gid" -exec chgrp -h "$gid" {} + 2>/dev/null || true
    fi
done
echo "=== Clean-up: the stage account's files are removable ==="
exit 0
