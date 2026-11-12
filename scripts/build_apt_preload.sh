#!/bin/sh
# The build's preload's distribution packages inside an image build (docs/DESIGN.md, Installer, platforms and
# dependencies). The framework stages this file with the file repository it made from a checked preload
# (orchestrator/Core/build_preload.file_repository) into the build context's sources/apt/; a target Dockerfile copies
# that folder to /tmp/llmcf-apt and runs this script only with the build argument APT_PRELOAD.
#
#   sh /tmp/llmcf-apt/llmcf-apt.sh use       the image's own package sources set aside, the file repository (signed
#                                           by the pinned archive keys in keyring.gpg) as the one source
#   sh /tmp/llmcf-apt/llmcf-apt.sh restore   the image's own sources back; the repository and the setting removed
#
# apt itself checks the Release signature with the pinned keys and every list and package against it.
set -eu

repo=${LLMCF_APT_REPO:-/tmp/llmcf-apt}
apt=${LLMCF_APT_ROOT:-}/etc/apt
saved="$apt/llmcf-saved-sources"

case "${1:-}" in
    use)
        if [ ! -f "$repo/llmcf-preload.sources" ] || [ ! -f "$repo/keyring.gpg" ]; then
            echo "llmcf-apt: the build context holds no file repository of the build's preload" >&2
            exit 1
        fi
        mkdir -p "$saved/sources.list.d"
        if [ -f "$apt/sources.list" ]; then mv "$apt/sources.list" "$saved/sources.list"; fi
        for f in "$apt"/sources.list.d/*; do
            if [ -e "$f" ]; then mv "$f" "$saved/sources.list.d/"; fi
        done
        cp "$repo/llmcf-preload.sources" "$apt/sources.list.d/llmcf-preload.sources"
        # no index translations; the lists uncompressed first (the repository holds only those: the signed Release
        # names their SHA-256 too)
        printf '%s\n' 'Acquire::Languages "none";' 'Acquire::CompressionTypes::uncompressed ".";' \
            'Acquire::CompressionTypes::Order { "uncompressed"; };' > "$apt/apt.conf.d/01llmcf-preload"
        ;;
    restore)
        rm -f "$apt/sources.list.d/llmcf-preload.sources" "$apt/apt.conf.d/01llmcf-preload"
        if [ -f "$saved/sources.list" ]; then mv "$saved/sources.list" "$apt/sources.list"; fi
        for f in "$saved"/sources.list.d/*; do
            if [ -e "$f" ]; then mv "$f" "$apt/sources.list.d/"; fi
        done
        rm -rf "$saved"
        ;;
    *)
        echo "usage: llmcf-apt.sh use|restore" >&2
        exit 2
        ;;
esac
