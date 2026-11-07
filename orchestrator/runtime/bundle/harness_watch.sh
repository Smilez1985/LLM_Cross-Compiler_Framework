#!/bin/sh
# llmcf harness watch (ROADMAP 3.4.0 "An agent harness on the board as an opt-in bundle component"): runs the person's
# harness installer on the board, detached from the SSH session (like tmux: when the connection drops the installer
# goes on, and the host attaches again and reads on from where it was), and records what it did, in two ways
# together: traced with strace where the board has it, and always a comparison before and after (packages, files in
# the system's places, accounts, listening ports). It never blocks what the installer does; llmcf reads the records
# on the host and reports (orchestrator/runtime/bundle/harness_report.py). POSIX sh, BusyBox included.
#
#   harness_watch.sh start MODE TREE INSTALLER OUTDIR [NAME=VALUE] [-- ARG...]
#   harness_watch.sh attach OUTDIR [OFFSET]   the installer's output from byte OFFSET, live until it ends
#   harness_watch.sh answer OUTDIR            one line from this command's input goes to the installer
#   harness_watch.sh status OUTDIR            "running", or "ended <exit code>"
#   harness_watch.sh stop OUTDIR              ends the installer (the person's cancel)
#
# MODE is "dry" (the installer's own dry run, its arguments after --, no strace) or "run". In a run without
# NAME=VALUE the installer is interactive: it gets a terminal of its own on the board (script), its questions show
# in its output and the person's answers come in through `answer`, each one as the installer asks it. NAME=VALUE is
# the harness's own "assume yes" switch, an opt-in only. TREE is the harness's folder, INSTALLER its path inside
# TREE, OUTDIR a new folder for the records. `start` returns at once; exit 90 when the watch itself cannot start.
# For tests, LLMCF_WATCH_DIRS (space-separated) and LLMCF_WATCH_PASSWD replace the watched places and the account
# file, and LLMCF_WATCH_KEEP names variables the installer keeps.
set -u

q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
die() { echo "$1" >&2; exit 90; }

CMD_=${1:-}
[ $# -gt 0 ] && shift

case $CMD_ in
attach)
	OUT=${1:?OUTDIR}
	OFF=${2:-0}
	case $OFF in *[!0-9]*|"") OFF=0 ;; esac
	[ -d "$OUT" ] || die "no such run"
	while [ ! -f "$OUT/installer.log" ] && [ ! -f "$OUT/ended" ]; do sleep 1; done
	# Read on from OFFSET until the run ended and everything was shown.
	while :; do
		size=$(wc -c <"$OUT/installer.log" 2>/dev/null || echo 0)
		size=$((size + 0))
		if [ "$size" -gt "$OFF" ]; then
			tail -c +"$((OFF + 1))" "$OUT/installer.log" | head -c "$((size - OFF))"
			OFF=$size
		elif [ -f "$OUT/ended" ]; then
			exit 0
		else
			sleep 1
		fi
	done ;;
answer)
	OUT=${1:?OUTDIR}
	[ -p "$OUT/input" ] || die "this run takes no answers"
	[ -f "$OUT/ended" ] && die "the installer has ended"
	IFS= read -r line || line=""
	printf '%s\n' "$line" >"$OUT/input"
	exit 0 ;;
status)
	OUT=${1:?OUTDIR}
	[ -d "$OUT" ] || die "no such run"
	if [ -f "$OUT/ended" ]; then echo "ended $(cat "$OUT/exit_code" 2>/dev/null || echo 90)"; else echo running; fi
	exit 0 ;;
stop)
	OUT=${1:?OUTDIR}
	[ -f "$OUT/pid" ] || die "no such run (or it has not begun yet)"
	[ -f "$OUT/ended" ] && die "the installer has ended"
	pid=$(cat "$OUT/pid")
	case $pid in *[!0-9]*|"") die "no such run" ;; esac
	# Every process below the watch, whatever session the installer's terminal gave it; the watch itself stays to
	# record the end (exit code 143) and the comparison after.
	kids=$(ps -eo pid=,ppid= 2>/dev/null | awk -v root="$pid" '
		{ parent[$1] = $2 }
		END { n = 1; todo[1] = root
		      while (n > 0) { p = todo[n--]; for (c in parent) if (parent[c] == p) { print c; todo[++n] = c } } }')
	[ -n "$kids" ] || die "nothing to stop"
	# shellcheck disable=SC2086  # a list of process numbers
	kill -TERM $kids 2>/dev/null
	exit 0 ;;
start) ;;
*) die "usage: harness_watch.sh start|attach|answer|status|stop ..." ;;
esac

[ $# -ge 4 ] || die "usage: harness_watch.sh start MODE TREE INSTALLER OUTDIR [NAME=VALUE] [-- ARG...]"
MODE=$1 TREE=$2 INSTALLER=$3 OUT=$4
shift 4
YES=""
if [ $# -gt 0 ] && [ "$1" != "--" ]; then YES=$1; shift; fi
[ $# -gt 0 ] && [ "$1" = "--" ] && shift

case $MODE in dry|run) ;; *) die "MODE is dry or run" ;; esac
case $INSTALLER in /*|*..*|*[!A-Za-z0-9._/-]*|"") die "INSTALLER must be a path inside TREE (letters, digits, . _ - /)" ;; esac
case $YES in "") ;; *=*) name=${YES%%=*}
	case $name in *[!A-Z0-9_]*|"") die "the assume-yes switch must be NAME=VALUE" ;; esac
	case ${YES#*=} in *[!A-Za-z0-9_.:-]*) die "the assume-yes switch must be NAME=VALUE" ;; esac ;;
	*) die "the assume-yes switch must be NAME=VALUE" ;; esac
[ -f "$TREE/$INSTALLER" ] || die "the installer is not there"
[ -e "$OUT" ] && die "OUTDIR exists already"
mkdir -p "$OUT/before" "$OUT/after" || exit 90
chmod 700 "$OUT"

if [ -x "$TREE/$INSTALLER" ]; then CMD="./$INSTALLER"; else CMD="sh ./$INSTALLER"; fi
STRACE=""
if [ "$MODE" = run ] && command -v strace >/dev/null 2>&1; then
	STRACE="strace -f -qq -s 512 -o $(q "$OUT/trace.txt") -e trace=connect,execve,open,openat,creat,rename,renameat,renameat2,unlink,unlinkat,chmod,fchmodat,chown,fchownat --"
	echo strace >"$OUT/watch"
else
	echo compare >"$OUT/watch"
fi
LIMIT=""
if command -v timeout >/dev/null 2>&1; then
	# --foreground (GNU) keeps the installer in this process group: it may then read from its terminal, and `stop`
	# reaches every process of the run. BusyBox's timeout never moves it to another group.
	if timeout --foreground 5 true 2>/dev/null; then
		LIMIT="timeout --foreground ${LLMCF_WATCH_SECONDS:-3600}"
	else
		LIMIT="timeout ${LLMCF_WATCH_SECONDS:-3600}"
	fi
fi

# The installer runs in its own folder with a clean environment: PATH, HOME, LANG, TERM and, only when the person
# chose it, its own "assume yes" switch; nothing of the session that started it. Every value is quoted into a small
# runner file.
KEEP=""
for v in ${LLMCF_WATCH_KEEP:-}; do
	case $v in *[!A-Z0-9_]*|"") continue ;; esac
	val=""
	eval "val=\${$v:-}"
	KEEP="$KEEP $v=$(q "$val")"
done
ARGS=""
for a in "$@"; do ARGS="$ARGS $(q "$a")"; done
INTERACTIVE=0
[ "$MODE" = run ] && [ -z "$YES" ] && INTERACTIVE=1
TERMV=dumb
[ "$INTERACTIVE" = 1 ] && TERMV=xterm
{
	echo "cd $(q "$TREE") || exit 90"
	echo "exec env -i PATH=$(q "${PATH:-/usr/sbin:/usr/bin:/sbin:/bin}") HOME=$(q "${HOME:-/root}") LANG=C.UTF-8 TERM=$TERMV$KEEP $YES $LIMIT $STRACE $CMD$ARGS"
} >"$OUT/run.sh"

DIRS=${LLMCF_WATCH_DIRS:-"/etc /opt /usr/local /srv /boot /root /home /var/lib/systemd /lib/modules"}
PASSWD=${LLMCF_WATCH_PASSWD:-/etc/passwd}
TERMINAL=none
[ "$INTERACTIVE" = 1 ] && command -v script >/dev/null 2>&1 && TERMINAL=pty
[ "$INTERACTIVE" = 1 ] && mkfifo "$OUT/input"
echo "$TERMINAL" >"$OUT/terminal"

# The detached part: its own session (setsid where the board has it), so a dropped SSH connection does not end it.
{
	cat <<EOF
OUT=$(q "$OUT")
DIRS=$(q "$DIRS")
PASSWD=$(q "$PASSWD")
INTERACTIVE=$INTERACTIVE
TERMINAL=$TERMINAL
EOF
	cat <<'EOF'
snapshot() {   # $1: before or after
	d=$OUT/$1
	if command -v dpkg-query >/dev/null 2>&1; then dpkg-query -W -f='${Package}\t${Version}\n' 2>/dev/null
	elif command -v apk >/dev/null 2>&1; then apk info -v 2>/dev/null
	elif command -v rpm >/dev/null 2>&1; then rpm -qa 2>/dev/null
	elif command -v pacman >/dev/null 2>&1; then pacman -Q 2>/dev/null
	fi | sort >"$d/packages.txt"
	cut -d: -f1,3,4,7 "$PASSWD" 2>/dev/null | sort >"$d/accounts.txt"
	for f in /proc/net/tcp /proc/net/tcp6; do
		[ -r "$f" ] && awk -v p="${f##*/}" 'NR>1 && $4=="0A" {print p, $2}' "$f"
	done | sort -u >"$d/ports.txt"
	for f in /proc/net/udp /proc/net/udp6; do
		[ -r "$f" ] && awk -v p="${f##*/}" 'NR>1 && $4=="07" {print p, $2}' "$f"
	done | sort -u >>"$d/ports.txt"
	for dir in $DIRS; do
		[ -d "$dir" ] || continue
		find "$dir" -xdev \( -type f -o -type l \) -exec stat -c '%n|%s|%Y|%a' {} + 2>/dev/null
	done | sort >"$d/files.txt"
}
# `stop` ends every process below this one.
echo $$ >"$OUT/pid.tmp" && mv "$OUT/pid.tmp" "$OUT/pid"
snapshot before
date +%s >"$OUT/started"
: >"$OUT/installer.log"
KEEPER=""
if [ "$INTERACTIVE" = 1 ]; then
	# Holds the input open between answers, so the installer never reads an end of input while it waits.
	tail -f /dev/null >"$OUT/input" &
	KEEPER=$!
fi
if [ "$TERMINAL" = pty ]; then
	script -q -f -c "sh '$OUT/run.sh'; echo \$? >'$OUT/exit_code'" /dev/null <"$OUT/input" >>"$OUT/installer.log" 2>&1
elif [ "$INTERACTIVE" = 1 ]; then
	sh "$OUT/run.sh" <"$OUT/input" >>"$OUT/installer.log" 2>&1
	echo $? >"$OUT/exit_code"
else
	sh "$OUT/run.sh" </dev/null >>"$OUT/installer.log" 2>&1
	echo $? >"$OUT/exit_code"
fi
[ -n "$KEEPER" ] && kill "$KEEPER" 2>/dev/null
[ -s "$OUT/exit_code" ] || echo 143 >"$OUT/exit_code"
snapshot after
date +%s >"$OUT/ended"
EOF
} >"$OUT/detached.sh"

if command -v setsid >/dev/null 2>&1; then
	setsid sh "$OUT/detached.sh" </dev/null >/dev/null 2>&1 &
else
	nohup sh "$OUT/detached.sh" </dev/null >/dev/null 2>&1 &
fi
echo started
exit 0
