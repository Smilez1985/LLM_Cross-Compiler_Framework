#!/bin/bash
# Lists the files of yours that a framework container holds outside its data volume (/data).
#
# Targets you create with the module wizard or `llm-cli module create` and community packs you install go to the
# program folder in the container, as on bare metal, and files you make in a shell there go to its home folder. They
# are lost when the container is removed or replaced, so the make targets never remove or replace a container that
# holds such files (make up, make gui, make clean); this script tells them, and shows you every file.
#
#   scripts/container_kept_files.sh CONTAINER
#
# One line per file: its full path in the container, the file name, the size and what it is.
# Exit codes: 0 nothing of yours (or no such container), 3 the container holds files of yours (listed),
# 2 Docker is not reachable or the container cannot be read.
# Guide: docs/Container_Setup.md (German: docs/Container_Setup_DE.md).

set -uo pipefail

INSTALL_DIR=/opt/llm-cross-compiler-framework
# The program folder of an image built before the repository was renamed (LLM Conversion Framework). A container made
# from such an image keeps your targets and packs there, so they count as yours as well: make must keep it too.
OLD_INSTALL_DIR=/opt/llm-conversion-framework

# A check that cannot run must not look like "nothing of yours": every failure exits 2, and the make targets then
# remove and replace nothing.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    echo "container_kept_files.sh: needs bash 4 or newer." >&2
    exit 2
fi

if [ $# -ne 1 ] || [ -z "$1" ]; then
    echo "usage: scripts/container_kept_files.sh CONTAINER" >&2
    exit 2
fi
container=$1

if ! docker container inspect "$container" >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        exit 0   # no such container: nothing it could hold
    fi
    echo "container_kept_files.sh: the Docker daemon is not reachable ('docker info' shows why)." >&2
    exit 2
fi
if ! changes=$(docker diff "$container"); then
    echo "container_kept_files.sh: the changes in $container could not be read ('docker diff $container')." >&2
    exit 2
fi

# Where the framework or you keep things: the program folder and the home folders. The rest of the container's own
# layer (/tmp, /run, ...) belongs to the system, and Python's byte code and shell histories are not yours to keep.
yours() {
    case "$1" in
        */__pycache__ | */__pycache__/* | *.pyc) return 1 ;;
        /home/*/.bash_history | /home/*/.python_history | /home/*/.lesshst) return 1 ;;
        "$INSTALL_DIR"/* | "$OLD_INSTALL_DIR"/* | /home/*/*) return 0 ;;
    esac
    return 1
}

what_it_is() {   # $1 the path, $2 A (added) or C (a file that was there before and was changed)
    if [ "$2" = C ]; then
        case "$1" in
            "$INSTALL_DIR"/* | "$OLD_INSTALL_DIR"/*) echo "a file the framework ships, changed in this container" ;;
            *) echo "a file in the container user's home folder, changed in this container" ;;
        esac
        return
    fi
    case "$1" in
        "$INSTALL_DIR"/targets/* | "$OLD_INSTALL_DIR"/targets/*)
            echo "a target in the program folder (one you created, or a target pack you installed)" ;;
        "$INSTALL_DIR"/community/* | "$OLD_INSTALL_DIR"/community/*) echo "a community pack in the program folder" ;;
        "$INSTALL_DIR"/* | "$OLD_INSTALL_DIR"/*) echo "a file in the program folder" ;;
        *) echo "a file in the container user's home folder" ;;
    esac
}

# docker diff: "A path" added, "C path" changed (a folder is "C" when something inside it changed), "D path" deleted.
declare -A seen=() is_added=()
added=() changed=()
while IFS= read -r line; do
    [ -n "$line" ] || continue
    kind=${line%% *}
    path=${line#* }
    seen[$path]=1
    yours "$path" || continue
    case "$kind" in
        A) added+=("$path"); is_added[$path]=1 ;;
        C) changed+=("$path") ;;
    esac
done <<< "$changes"

# A changed entry with something below it in the list is a folder: its files are listed by their own entries, and the
# folder itself is never copied (the program folder holds the whole installation).
is_parent() {
    local candidate
    for candidate in "${!seen[@]}"; do
        case "$candidate" in "$1"/*) return 0 ;; esac
    done
    return 1
}

# What lies at $1 in the container: one "type<TAB>size<TAB>full path" line per entry at or below it (type d for a
# folder, f for a file or a link). A path that cannot be read lists nothing.
entries_at() {
    local src=$1 parent
    parent=$(dirname -- "$src")
    [ "$parent" = / ] && parent=""
    docker cp "$container:$src" - 2>/dev/null | tar -tvf - 2>/dev/null |
        awk -v parent="$parent" '{
            type = substr($1, 1, 1); size = $3
            $1 = $2 = $3 = $4 = $5 = ""; sub(/^ +/, "")
            if (type == "l") sub(/ -> .*/, "")
            sub(/\/$/, "")
            print (type == "d" ? "d" : "f") "\t" size "\t" parent "/" $0
        }'
}

listing=""   # "size<TAB>full path<TAB>A|C" per file
unreadable=()   # entries of yours that could not be read: they count as yours, never as nothing
for path in "${added[@]+"${added[@]}"}"; do
    # Something added inside an added folder is listed with that folder.
    [ -n "${is_added[$(dirname -- "$path")]:-}" ] && continue
    entries=$(entries_at "$path")
    if [ -z "$entries" ]; then
        unreadable+=("$path:A")
        continue
    fi
    # Only files are listed: an empty folder of yours holds nothing to lose.
    listing+=$(printf '%s\n' "$entries" | awk -F'\t' '$1 == "f" { print $2 "\t" $3 "\tA" }')$'\n'
done
for path in "${changed[@]+"${changed[@]}"}"; do
    is_parent "$path" && continue
    entry=$(entries_at "$path" | awk -F'\t' -v path="$path" '$3 == path')
    case "${entry%%$'\t'*}" in
        d) ;;   # a folder whose own details changed: its files did not
        f) listing+=$(printf '%s' "$entry" | awk -F'\t' '{ print $2 "\t" $3 "\tC" }')$'\n' ;;
        *) unreadable+=("$path:C") ;;
    esac
done

found=0
while IFS=$'\t' read -r size path kind; do
    [ -n "${path:-}" ] || continue
    found=1
    human=$(numfmt --to=iec --suffix=B "$size" 2>/dev/null || echo "${size} bytes")
    printf '  %s (file %s, %s): %s\n' "$path" "$(basename -- "$path")" "$human" "$(what_it_is "$path" "$kind")"
done < <(printf '%s' "$listing" | sort -t$'\t' -k2,2 -u)
for item in "${unreadable[@]+"${unreadable[@]}"}"; do
    found=1
    path=${item%:*}
    printf '  %s (%s, size unknown: it could not be read): %s\n' "$path" "$(basename -- "$path")" \
        "$(what_it_is "$path" "${item##*:}")"
done

[ "$found" = 1 ] && exit 3
exit 0
