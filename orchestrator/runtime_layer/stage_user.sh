# stage_user.sh - the stage account of the build sandbox, sourced by the stage runner runner.sh (never run on its own)
#
# Part of the framework's runtime layer (tools.sh says what that is). Every stage module of the build sandbox (the host
# sets STAGE_ISOLATION=user) starts through ONE helper, run_as_stage, which switches to the stage account
# (STAGE_UID:STAGE_GID, other ids than the runner's) with the layer's own static setpriv: no capabilities (empty
# permitted, effective, inheritable and ambient sets) and no-new-privileges, so a setuid file or file capabilities in
# the image give nothing back. The kernel then refuses a module the runner's memory, its environment, its signals and
# the end folder (only the runner's uid may write it: no key is needed and none exists in the container).
#
# The runner keeps only what that switch needs, CAP_SETUID and CAP_SETGID, nothing else. stage_preflight checks
# before the first stage that the rights are really there and no more (its own effective, permitted, bounding,
# inheritable and ambient sets, no-new-privileges, other ids than the stage account), and that a test child switched to
# the stage account has the stage ids, no capabilities, cannot open /proc/<runner>/mem, can write the folders the
# modules write and read the build's inputs. It also tries to write the end folder: on a host whose mounts keep owners
# and modes that is refused (the end folder is protected); a mount that keeps none (Docker Desktop on Windows) lets it
# write, and the runner says so ("unprotected") in its log and its end record instead of refusing the build. Anything
# else refuses the build with one reason from a fixed vocabulary (STAGE_USER_EXIT in orchestrator/Core/build_end.py,
# the exit code of the run, the end record "failed" at stage "preflight"); the runner never falls back to running a
# module as itself.
#
# What the modules may write (stage_folders): the work folder ($BUILD_SCRATCH_DIR with tmp, home and cache), the job's
# stage folder, the output folder and the importance matrix folder. They are made group-writable for the runner's
# group, which the stage account gets as its supplementary group, with setgid and the sticky bit on the work and
# stage folders, so a module can add files there but never change, rename or remove the runner's own (resume markers,
# loop counts). One shared group for both ids (stage_share): after each module the stage account itself gives its
# own files there to the runner's group, readable and writable for it. Nothing is chowned.
#
# POSIX sh: it runs on the layer's busybox sh (and on any POSIX sh when started by hand).

STAGE_UID=64010
STAGE_GID=64010
STAGE_RIGHTS_MASK=$(( (1 << 6) | (1 << 7) ))   # CAP_SETGID (6), CAP_SETUID (7)
CAP_NAMES="chown dac_override dac_read_search fowner fsetid kill setgid setuid setpcap linux_immutable
net_bind_service net_broadcast net_admin net_raw ipc_lock ipc_owner sys_module sys_rawio sys_chroot sys_ptrace
sys_pacct sys_admin sys_boot sys_nice sys_resource sys_time sys_tty_config mknod lease audit_write audit_control
setfcap mac_override mac_admin syslog wake_alarm block_suspend audit_read perfmon bpf checkpoint_restore"

# The fixed vocabulary of the preflight (orchestrator/Core/build_end.py STAGE_USER_EXIT has the same codes)
refusal_exit() {
    case "$1" in
        rights_missing) echo 80 ;;
        rights_extra) echo 81 ;;
        no_new_privileges) echo 82 ;;
        same_account) echo 83 ;;
        no_setpriv) echo 84 ;;
        switch_failed) echo 85 ;;
        stage_has_rights) echo 86 ;;
        reads_runner) echo 87 ;;
        folders) echo 88 ;;
        inputs) echo 89 ;;
        no_layer) echo 90 ;;
        *) echo 85 ;;
    esac
}

stage_isolated() { [ "${STAGE_ISOLATION:-}" = "user" ]; }

cap_list() {  # MASK: the capability names in it, "CAP_X, CAP_Y"
    _mask="$1"; _out=""; _i=0
    for _name in $CAP_NAMES; do
        if [ $(( (_mask >> _i) & 1 )) -eq 1 ]; then
            _out="${_out:+$_out, }CAP_$(printf '%s' "$_name" | tr '[:lower:]' '[:upper:]')"
        fi
        _i=$((_i + 1))
    done
    while [ "$_i" -lt 63 ]; do
        if [ $(( (_mask >> _i) & 1 )) -eq 1 ]; then
            _out="${_out:+$_out, }capability $_i"
        fi
        _i=$((_i + 1))
    done
    printf '%s' "$_out"
}

status_value() {  # FILE NAME: the first field of NAME: in a /proc/<pid>/status file
    sed -n "s/^$2:[[:space:]]*\([^[:space:]]*\).*/\1/p" "$1" | head -n 1
}

hex_mask() {  # HEX: its value, or -1 when it is no capability set
    case "$1" in
        ''|*[!0-9a-fA-F]*) echo -1 ;;
        *) echo $(( 0x$1 & 0x7fffffffffffffff )) ;;
    esac
}

# STATUS_FILE: the problem of the runner's own rights as "REASON text", nothing when they are exactly the switch's
stage_rights_problem() {
    _file="$1"
    _uid="$(status_value "$_file" Uid)"
    _gid="$(status_value "$_file" Gid)"
    _eff="$(hex_mask "$(status_value "$_file" CapEff)")"
    _prm="$(hex_mask "$(status_value "$_file" CapPrm)")"
    _bnd="$(hex_mask "$(status_value "$_file" CapBnd)")"
    _inh="$(hex_mask "$(status_value "$_file" CapInh)")"
    _amb="$(hex_mask "$(status_value "$_file" CapAmb)")"
    _nnp="$(status_value "$_file" NoNewPrivs)"
    if [ "$_eff" -lt 0 ] || [ "$_prm" -lt 0 ] || [ "$_bnd" -lt 0 ]; then
        echo "rights_missing The stage runner could not read its own capability sets, so it cannot show that it may switch to the stage account."
        return 0
    fi
    [ "$_inh" -ge 0 ] || _inh=0
    [ "$_amb" -ge 0 ] || _amb=0
    if [ "$_uid" = "$STAGE_UID" ] || [ "$_gid" = "$STAGE_GID" ]; then
        echo "same_account The stage runner runs with the stage account's ids ($STAGE_UID:$STAGE_GID), so the stage modules would not run under a user of their own."
        return 0
    fi
    _missing=$(( STAGE_RIGHTS_MASK & ~(_eff & _prm) ))
    if [ "$_missing" -ne 0 ]; then
        echo "rights_missing The stage runner lacks the rights to switch to the stage account: $(cap_list "$_missing") (it needs exactly CAP_SETUID and CAP_SETGID)."
        return 0
    fi
    _extra=$(( (_eff | _prm | _bnd | _inh | _amb) & ~STAGE_RIGHTS_MASK ))
    if [ "$_extra" -ne 0 ]; then
        echo "rights_extra The stage runner has rights beyond the switch to the stage account: $(cap_list "$_extra") (it may have exactly CAP_SETUID and CAP_SETGID)."
        return 0
    fi
    if [ "$_nnp" != "1" ]; then
        echo "no_new_privileges The stage runner runs without no-new-privileges, which the build sandbox sets."
        return 0
    fi
}

stage_groups() {  # the stage account's supplementary groups: the runner's own (its group owns the stage folders)
    id -G | tr ' ' ','
}

# The folders the stage modules write (stage_folders, named by stage_preflight), one per line
STAGE_SHARED_FOLDERS=""
stage_shared_gid() { id -g; }

# as_stage COMMAND...: COMMAND as the stage account, without any capability, with umask 0002, through the layer's
# own setpriv (never one of the image)
as_stage() {
    (
        umask 0002
        exec "$SETPRIV" --reuid="$STAGE_UID" --regid="$STAGE_GID" --groups="$(stage_groups)" \
            --inh-caps=-all --ambient-caps=-all --no-new-privs -- "$@"
    )
}

# What stage_share runs as the stage account (arguments: the busybox, the shared gid, then the folders): its own
# files and folders there go to the shared group, readable and writable for it, its folders setgid and never sticky.
# Links are never followed; whatever is not its own it cannot change, and that stays as it is.
STAGE_SHARE='b="$1"; gid="$2"; shift 2
[ $# -gt 0 ] || exit 0
if [ -x "$b" ]; then t="$b"; else t=""; fi
$t chmod -R u+rwX,g+rwX,-t "$@" 2>/dev/null
me="$($t id -u)"
$t find "$@" -user "$me" ! -type l ! -group "$gid" -exec $t chgrp -h "$gid" {} + 2>/dev/null
$t find "$@" -user "$me" -type d ! -perm -2000 -exec $t chmod g+s {} + 2>/dev/null
exit 0'

# The layer's sh for a child of the stage account (the system's when started by hand without the layer's tools)
layer_sh() {
    if layer_has_tools; then
        printf '%s' "$BB"
    else
        printf '%s' ""
    fi
}

stage_sh() {  # SCRIPT ARGS...: a small script of the layer as the stage account
    if layer_has_tools; then
        as_stage "$BB" sh -c "$@"
    else
        as_stage /bin/sh -c "$@"
    fi
}

# stage_share: after a stage module returned, the stage account gives what it wrote to the shared group (see the
# header). Nothing outside the sandbox, and nothing before the preflight named the folders.
stage_share() {
    stage_isolated || return 0
    [ -n "$STAGE_SHARED_FOLDERS" ] || return 0
    IFS='
'
    # shellcheck disable=SC2086 - one folder per line
    set -- $STAGE_SHARED_FOLDERS
    unset IFS
    stage_sh "$STAGE_SHARE" _ "$(layer_sh)" "$(stage_shared_gid)" "$@" >/dev/null 2>&1 || true
}

# run_as_stage COMMAND...: the one way a stage module starts in the build sandbox: as the stage account, without any
# capability, with umask 0002; when it returns (also with an error) what it wrote goes to the shared group
# (stage_share), and its exit status is returned. Outside the sandbox (a run by hand, the legacy mode) it runs COMMAND
# as it is.
run_as_stage() {
    if ! stage_isolated; then
        "$@"
        return
    fi
    _rc=0
    as_stage "$@" || _rc=$?
    stage_share
    return "$_rc"
}

# The test child of the preflight (run as the stage account): its ids, capability sets, whether it could open the
# runner's memory and environment, whether it could write the end folder, then "write-fail NAME" for a folder it
# cannot write in and "read-fail NAME" for an input it cannot read. Arguments: BUSYBOX, RUNNER_PID, END_DIR, folders,
# "--", then inputs.
STAGE_PROBE='b="$1"; pid="$2"; end="$3"; shift 3
if [ -x "$b" ]; then t="$b"; else t=""; fi
run() { $t "$@"; }
run id -u; run id -g
for n in CapEff CapPrm CapAmb CapInh; do run sed -n "s/^$n:[[:space:]]*//p" /proc/self/status; done
if (exec 3< "/proc/$pid/mem") 2>/dev/null; then echo mem-open; else echo mem-refused; fi
if (exec 3< "/proc/$pid/environ") 2>/dev/null; then echo environ-open; else echo environ-refused; fi
if [ -z "$end" ]; then echo end-none
elif probe="$(run mktemp "$end/.stage-probe.XXXXXX" 2>/dev/null)"; then run rm -f "$probe"; echo end-open
else echo end-refused; fi
while [ $# -gt 0 ] && [ "$1" != "--" ]; do
    probe="$(run mktemp "$1/.stage-probe.XXXXXX" 2>/dev/null)" && run rm -f "$probe" || echo "write-fail ${1##*/}"
    shift
done
[ $# -gt 0 ] && shift
for input in "$@"; do
    [ -n "$input" ] || continue
    if [ -d "$input" ]; then
        bad=""
        if [ -r "$input" ] && [ -x "$input" ]; then
            bad="$(run find "$input" ! -type l -exec $t sh -c "for f; do [ -r \"\$f\" ] || { echo x; exit; }; done" _ {} + 2>/dev/null | run head -n 1)"
        else
            bad=x
        fi
        [ -z "$bad" ] || echo "read-fail ${input##*/}"
    elif [ -e "$input" ]; then
        [ -r "$input" ] || echo "read-fail ${input##*/}"
    fi
done'

STAGE_END_PROTECTION="none"   # protected | unprotected | none (no end folder: a run by hand)

# PROBE_OUTPUT: the problem of the test child's answer as "REASON text", nothing when it is the stage account without
# rights that cannot read the runner and can write and read what a module needs. Sets STAGE_END_PROTECTION.
stage_switch_problem() {
    _n=0; _bad=""
    IFS='
'
    # shellcheck disable=SC2086 - one answer per line
    set -- $1
    unset IFS
    if [ $# -lt 9 ] || [ "$1" != "$STAGE_UID" ] || [ "$2" != "$STAGE_GID" ]; then
        echo "switch_failed The test switch to the stage account ($STAGE_UID:$STAGE_GID) did not work, so no stage module was started."
        return 0
    fi
    for _line in "$3" "$4" "$5" "$6"; do
        _mask="$(hex_mask "$_line")"
        if [ "$_mask" -ne 0 ]; then
            [ "$_mask" -ge 0 ] || _mask=0
            echo "stage_has_rights The stage account still had capabilities after the switch ($(cap_list "$_mask")), so no stage module was started."
            return 0
        fi
    done
    if [ "$7" != "mem-refused" ] || [ "$8" != "environ-refused" ]; then
        echo "reads_runner A test child under the stage account could open the stage runner's memory or environment; no stage module was started."
        return 0
    fi
    case "$9" in
        end-refused) STAGE_END_PROTECTION="protected" ;;
        end-open) STAGE_END_PROTECTION="unprotected" ;;
        *) STAGE_END_PROTECTION="none" ;;
    esac
    shift 9
    for _line in "$@"; do
        case "$_line" in
            "write-fail "*)
                echo "folders The stage account cannot write the folder ${_line#write-fail } that the stage modules write, so no stage module was started."
                return 0 ;;
            "read-fail "*)
                echo "inputs The stage account cannot read ${_line#read-fail } (an input of the build); its files must be readable for your group. No stage module was started."
                return 0 ;;
        esac
    done
}

# stage_folders WORK STAGE_DIR FOLDER...: prints the folders the modules write (those the runner can write) after
# making them writable for the runner's group (see the header). Only folders the runner owns are changed.
stage_folders() {
    _work="$1"; _stage_dir="$2"
    shift 2
    for _folder in "$_work" "$_work/tmp" "$_work/home" "$_work/cache" "$_stage_dir" "$@"; do
        [ -d "$_folder" ] && [ ! -L "$_folder" ] && [ -w "$_folder" ] || continue
        _mode=2775
        case "$_folder" in "$_work"|"$_work/tmp"|"$_work/home"|"$_work/cache"|"$_stage_dir") _mode=3775 ;; esac
        if [ -O "$_folder" ]; then
            chmod "$_mode" "$_folder" 2>/dev/null || true
        fi
        printf '%s\n' "$_folder"
    done
    # the stages folder itself only for the runner: a module can neither rename nor replace a job folder
    if [ -d "$_work/stages" ] && [ -O "$_work/stages" ]; then
        chmod 0755 "$_work/stages" 2>/dev/null || true
    fi
}

stage_refuse() {  # "REASON text": says why and exits with the reason's code (the EXIT trap writes "failed")
    _reason="${1%% *}"; _text="${1#* }"
    echo "ERROR: Stage user check failed ($_reason): $_text" >&2
    echo "=== Stage user check failed: $_reason ===" >&2
    exit "$(refusal_exit "$_reason")"
}

# stage_preflight WORK STAGE_DIR FOLDER...: in the build sandbox, before the first stage (see the header). The inputs
# the test child reads are the build's: MODEL_SOURCE, DATASET_PATH, IMATRIX_PATH and HARDWARE_CONFIG_FILE.
stage_preflight() {
    stage_isolated || return 0
    if ! layer_has_tools; then
        stage_refuse "no_layer The framework's runtime layer has no tools (bin/busybox), so the stage runner cannot run without the image's programs."
    fi
    _problem="$(stage_rights_problem /proc/self/status)"
    [ -z "$_problem" ] || stage_refuse "$_problem"
    if [ ! -f "$SETPRIV" ] || [ ! -x "$SETPRIV" ]; then
        stage_refuse "no_setpriv The framework's runtime layer has no setpriv, so the stage runner cannot switch to the stage account."
    fi
    STAGE_SHARED_FOLDERS="$(stage_folders "$@")"
    IFS='
'
    # shellcheck disable=SC2086 - one folder per line
    set -- $STAGE_SHARED_FOLDERS
    unset IFS
    _probe="$(stage_sh "$STAGE_PROBE" _ "$(layer_sh)" "$$" "${LLMCF_END_DIR:-}" "$@" -- "${MODEL_SOURCE:-}" \
        "${DATASET_PATH:-}" "${IMATRIX_PATH:-}" "${HARDWARE_CONFIG_FILE:-}" 2>/dev/null || true)"
    _problem="$(stage_switch_problem "$_probe")"
    [ -z "$_problem" ] || stage_refuse "$_problem"
    # stage_switch_problem ran in a subshell: the end folder's answer once more, here
    stage_switch_problem "$_probe" >/dev/null
    echo "=== Isolation: stage modules run under their own user (stage account $STAGE_UID:$STAGE_GID, no capabilities) ==="
    case "$STAGE_END_PROTECTION" in
        protected) echo "=== End folder: protected (only the stage runner can write it) ===" ;;
        unprotected) echo "=== End folder: unprotected (this host's mounts keep no owner or mode; the end counts by the exit status and the inspect check only) ===" ;;
    esac
}
