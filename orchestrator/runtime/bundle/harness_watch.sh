#!/bin/sh
# llmcf harness watch (ROADMAP 3.4.0 "An agent harness on the board as an opt-in bundle component"): runs the person's
# harness installer on the board and records what it did, in two ways together: traced with strace where the board
# has it, and always a comparison before and after (packages, files in the system's places, accounts, listening
# ports). It never blocks what the installer does; llmcf reads the records on the host and reports
# (orchestrator/runtime/bundle/harness_report.py). POSIX sh, BusyBox included.
#
#   harness_watch.sh MODE TREE INSTALLER OUTDIR [NAME=VALUE] [-- ARG...]
#
# MODE is "dry" (the installer's own dry run, its arguments after --, no strace) or "run". TREE is the harness's
# folder, INSTALLER its path inside TREE, OUTDIR a new folder for the records. NAME=VALUE is the harness's own
# "assume yes" switch, given only after the person's yes. Exit code: the installer's own, or 90 when the watch
# itself could not start. For tests, LLMCF_WATCH_DIRS (space-separated) and LLMCF_WATCH_PASSWD replace the watched
# places and the account file.
set -u

[ $# -ge 4 ] || { echo "usage: harness_watch.sh MODE TREE INSTALLER OUTDIR [NAME=VALUE] [-- ARG...]" >&2; exit 90; }
MODE=$1 TREE=$2 INSTALLER=$3 OUT=$4
shift 4
YES=""
if [ $# -gt 0 ] && [ "$1" != "--" ]; then YES=$1; shift; fi
[ $# -gt 0 ] && [ "$1" = "--" ] && shift

case $MODE in dry|run) ;; *) echo "MODE is dry or run" >&2; exit 90 ;; esac
case $INSTALLER in /*|*..*|*[!A-Za-z0-9._/-]*|"") echo "INSTALLER must be a path inside TREE (letters, digits, . _ - /)" >&2; exit 90 ;; esac
case $YES in "") ;; *=*) name=${YES%%=*}
	case $name in *[!A-Z0-9_]*|"") echo "the assume-yes switch must be NAME=VALUE" >&2; exit 90 ;; esac ;;
	*) echo "the assume-yes switch must be NAME=VALUE" >&2; exit 90 ;; esac
[ -f "$TREE/$INSTALLER" ] || { echo "the installer is not there" >&2; exit 90; }
[ -e "$OUT" ] && { echo "OUTDIR exists already" >&2; exit 90; }
mkdir -p "$OUT/before" "$OUT/after" || exit 90
chmod 700 "$OUT"

DIRS=${LLMCF_WATCH_DIRS:-"/etc /opt /usr/local /srv /boot /root /home /var/lib/systemd /lib/modules"}
PASSWD=${LLMCF_WATCH_PASSWD:-/etc/passwd}

snapshot() {   # $1: before or after
	d=$OUT/$1
	if command -v dpkg-query >/dev/null 2>&1; then dpkg-query -W -f='${Package}\t${Version}\n' 2>/dev/null
	elif command -v apk >/dev/null 2>&1; then apk info -v 2>/dev/null
	elif command -v rpm >/dev/null 2>&1; then rpm -qa 2>/dev/null
	elif command -v pacman >/dev/null 2>&1; then pacman -Q 2>/dev/null
	fi | sort >"$d/packages.txt"
	# name:uid:gid:shell of every account
	cut -d: -f1,3,4,7 "$PASSWD" 2>/dev/null | sort >"$d/accounts.txt"
	# listening sockets straight from the kernel (state 0A = LISTEN for tcp; udp sockets bound to a port)
	for f in /proc/net/tcp /proc/net/tcp6; do
		[ -r "$f" ] && awk -v p="${f##*/}" 'NR>1 && $4=="0A" {print p, $2}' "$f"
	done | sort -u >"$d/ports.txt"
	for f in /proc/net/udp /proc/net/udp6; do
		[ -r "$f" ] && awk -v p="${f##*/}" 'NR>1 && $4=="07" {print p, $2}' "$f"
	done | sort -u >>"$d/ports.txt"
	# path|size|mtime|mode of every file and link in the watched places (same filesystem only)
	for dir in $DIRS; do
		[ -d "$dir" ] || continue
		find "$dir" -xdev \( -type f -o -type l \) -exec stat -c '%n|%s|%Y|%a' {} + 2>/dev/null
	done | sort >"$d/files.txt"
}

snapshot before
date +%s >"$OUT/started"

if [ -x "$TREE/$INSTALLER" ]; then CMD="./$INSTALLER"; else CMD="sh ./$INSTALLER"; fi
STRACE=""
if [ "$MODE" = run ] && command -v strace >/dev/null 2>&1; then
	STRACE="strace -f -qq -s 512 -o $OUT/trace.txt -e trace=connect,execve,open,openat,creat,rename,renameat,renameat2,unlink,unlinkat,chmod,fchmodat,chown,fchownat --"
	echo strace >"$OUT/watch"
else
	echo compare >"$OUT/watch"
fi
LIMIT=""
command -v timeout >/dev/null 2>&1 && LIMIT="timeout ${LLMCF_WATCH_SECONDS:-3600}"

# The installer runs in its own folder; its "assume yes" switch only when the person said yes.
(
	cd "$TREE" || exit 90
	# A clean environment: the installer gets PATH, HOME, LANG, TERM and its own "assume yes" switch, nothing of
	# the session that started it (LLMCF_WATCH_KEEP names test variables to keep; empty on a board).
	KEEP=""
	for v in ${LLMCF_WATCH_KEEP:-}; do eval "KEEP=\"\$KEEP $v=\${$v:-}\""; done
	# shellcheck disable=SC2086  # STRACE, LIMIT, CMD, KEEP and YES are words built above, never the person's text
	exec env -i PATH="${PATH:-/usr/sbin:/usr/bin:/sbin:/bin}" HOME="${HOME:-/root}" LANG=C.UTF-8 TERM=dumb \
		$KEEP $YES $LIMIT $STRACE $CMD "$@"
) >"$OUT/installer.log" 2>&1 </dev/null
RC=$?
echo "$RC" >"$OUT/exit_code"
date +%s >"$OUT/ended"

snapshot after
exit "$RC"
