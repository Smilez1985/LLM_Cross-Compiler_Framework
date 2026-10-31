#!/bin/bash
# stage_user.sh - the stage account of the build sandbox, sourced by the stage runner build.sh (never run on its own)
#
# The stage runner seals the end record with a one-run key it holds in memory. A stage module that ran as the same
# user could read that key from the runner's memory (/proc/<runner>/mem) wherever the host kernel allows it (Yama
# ptrace_scope 0, or no Yama); seccomp and dropped capabilities do not stop it. So in the build sandbox (the
# BuildEngine sets STAGE_ISOLATION=user) every stage module is started through ONE helper, run_as_stage, that switches
# to the image's stage account (STAGE_UID:STAGE_GID, other ids than the runner's) with no capabilities: the kernel then
# refuses the module the runner's memory, its environment and its signals, whatever Yama says.
#
# The runner keeps only what that switch needs, CAP_SETUID and CAP_SETGID (setresuid and setgroups), nothing else.
# no-new-privileges stays on: dropping to a lower-privileged uid needs no new privileges. The bounding set is not
# cut down further by the switch (that needs CAP_SETPCAP, which the runner does not get); the stage account has empty
# permitted, effective, inheritable and ambient sets, and no-new-privileges keeps a setuid file or file capabilities
# from giving any back.
#
# stage_preflight checks, before the first stage, that the rights are really there and no more (its own effective,
# permitted, bounding, inheritable and ambient sets, no-new-privileges, other ids than the stage account), and that a
# test child switched to the stage account has the stage ids, no capabilities and cannot open /proc/<runner>/mem.
# Anything else refuses the build with one reason from a fixed vocabulary (STAGE_USER_EXIT in
# orchestrator/Core/build_end.py, the exit code of the run, the end record "failed" at stage "preflight"); the runner
# never falls back to running a module as itself.
#
# What the modules may write (stage_folders): the work folder ($BUILD_SCRATCH_DIR with tmp, home and cache), the job's
# stage folder (the build plan and the other files of the stages), the output folder and the importance matrix folder,
# where they are writable. They are made group-writable for the runner's group, which the stage account gets as its
# supplementary group, with setgid (new files keep the group) and the sticky bit on the work and stage folders, so a
# module can add files there but never change, rename or remove the runner's own (the resume markers, the loop counts,
# the end record); the stages folder itself stays the runner's. What they read (the model, the calibration dataset,
# the importance matrix, the hardware profile) must be readable for the runner's group. The test child checks both: it
# writes and removes a file in every one of these folders and reads every input; a folder it cannot write or an input
# it cannot read refuses the build (folders, inputs) before the first stage.
#
# One shared group for both ids (stage_share): the runner's own group (its gid from STAGE_RUNNER_IDS; outside the
# container the person's group, also under a rootless engine, where the runner's group 0 is the person's) is the
# group of everything a build writes. The stage modules run with umask 0002, and after each module returns the stage
# account itself (it owns what it wrote, so no capability is needed by anyone) gives its own files and folders in the
# folders the modules write to that group, readable and writable for it, and its folders setgid: a tool that writes a
# 0600 file, a 0555 folder or a sticky folder leaves nothing the runner or the person cannot read or remove. Nothing
# is chowned.
#
# The helper is the framework's (every build context carries this file, whatever the target ships under its name), and
# the runner that sources it must not replace any part of it: a build.sh that defines one of its functions (or
# setpriv) again or changes one of its variables (STAGE_ISOLATION, STAGE_UID, ...) is refused before its container is
# made (orchestrator/utils/sandbox.stage_runner_problem).

STAGE_UID=64010
STAGE_GID=64010
STAGE_RIGHTS_MASK=$(( (1 << 6) | (1 << 7) ))   # CAP_SETGID (6), CAP_SETUID (7)
CAP_NAMES=(chown dac_override dac_read_search fowner fsetid kill setgid setuid setpcap linux_immutable
           net_bind_service net_broadcast net_admin net_raw ipc_lock ipc_owner sys_module sys_rawio sys_chroot
           sys_ptrace sys_pacct sys_admin sys_boot sys_nice sys_resource sys_time sys_tty_config mknod lease
           audit_write audit_control setfcap mac_override mac_admin syslog wake_alarm block_suspend audit_read perfmon
           bpf checkpoint_restore)
# The fixed vocabulary of the preflight (orchestrator/Core/build_end.py STAGE_USER_EXIT has the same codes)
declare -A STAGE_REFUSAL_EXIT=(
    [rights_missing]=80
    [rights_extra]=81
    [no_new_privileges]=82
    [same_account]=83
    [no_setpriv]=84
    [switch_failed]=85
    [stage_has_rights]=86
    [reads_runner]=87
    [folders]=88
    [inputs]=89
    [no_layer]=90
)

stage_isolated() { [ "${STAGE_ISOLATION:-}" = "user" ]; }

cap_list() {  # MASK: the capability names in it, "CAP_X, CAP_Y"
    local mask="$1" i out=""
    for i in "${!CAP_NAMES[@]}"; do
        if (( (mask >> i) & 1 )); then
            out="${out:+$out, }CAP_$(printf '%s' "${CAP_NAMES[$i]}" | tr '[:lower:]' '[:upper:]')"
        fi
    done
    for (( i = ${#CAP_NAMES[@]}; i < 63; i++ )); do
        if (( (mask >> i) & 1 )); then
            out="${out:+$out, }capability $i"
        fi
    done
    printf '%s' "$out"
}

status_value() {  # FILE NAME: the first field of NAME: in a /proc/<pid>/status file
    sed -n "s/^$2:[[:space:]]*\([^[:space:]]*\).*/\1/p" "$1" | head -n 1
}

hex_mask() {  # HEX: its value, or -1 when it is no capability set
    case "$1" in
        ''|*[!0-9a-fA-F]*) echo -1 ;;
        *) echo $(( 16#$1 & 0x7fffffffffffffff )) ;;
    esac
}

# STATUS_FILE: the problem of the runner's own rights as "REASON text", nothing when they are exactly the switch's
stage_rights_problem() {
    local file="$1" uid gid eff prm bnd inh amb nnp extra missing name
    uid="$(status_value "$file" Uid)"
    gid="$(status_value "$file" Gid)"
    eff="$(hex_mask "$(status_value "$file" CapEff)")"
    prm="$(hex_mask "$(status_value "$file" CapPrm)")"
    bnd="$(hex_mask "$(status_value "$file" CapBnd)")"
    inh="$(hex_mask "$(status_value "$file" CapInh)")"
    amb="$(hex_mask "$(status_value "$file" CapAmb)")"
    nnp="$(status_value "$file" NoNewPrivs)"
    if [ "$eff" -lt 0 ] || [ "$prm" -lt 0 ] || [ "$bnd" -lt 0 ]; then
        echo "rights_missing The stage runner could not read its own capability sets, so it cannot show that it may switch to the stage account."
        return 0
    fi
    [ "$inh" -ge 0 ] || inh=0
    [ "$amb" -ge 0 ] || amb=0
    if [ "$uid" = "$STAGE_UID" ] || [ "$gid" = "$STAGE_GID" ]; then
        echo "same_account The stage runner runs with the stage account's ids ($STAGE_UID:$STAGE_GID), so the stage modules would not run under a user of their own."
        return 0
    fi
    missing=$(( STAGE_RIGHTS_MASK & ~(eff & prm) ))
    if [ "$missing" -ne 0 ]; then
        echo "rights_missing The stage runner lacks the rights to switch to the stage account: $(cap_list "$missing") (it needs exactly CAP_SETUID and CAP_SETGID)."
        return 0
    fi
    extra=$(( (eff | prm | bnd | inh | amb) & ~STAGE_RIGHTS_MASK ))
    if [ "$extra" -ne 0 ]; then
        name="$(cap_list "$extra")"
        echo "rights_extra The stage runner has rights beyond the switch to the stage account: $name (it may have exactly CAP_SETUID and CAP_SETGID)."
        return 0
    fi
    if [ "$nnp" != "1" ]; then
        echo "no_new_privileges The stage runner runs without no-new-privileges, which the build sandbox sets."
        return 0
    fi
}

stage_groups() {  # the stage account's supplementary groups: the runner's own (its group owns the stage folders)
    id -G | tr ' ' ','
}

# The folders the stage modules write (stage_folders, named by stage_preflight) and the shared group of both ids
STAGE_SHARED_FOLDERS=()
stage_shared_gid() { id -g; }

# as_stage COMMAND...: COMMAND as the stage account, without any capability, with umask 0002
as_stage() {
    (
        umask 0002
        exec setpriv --reuid="$STAGE_UID" --regid="$STAGE_GID" --groups="$(stage_groups)" \
            --inh-caps=-all --ambient-caps=-all --no-new-privs -- "$@"
    )
}

# What stage_share runs as the stage account (arguments: the shared gid, then the folders): its own files and
# folders there go to the shared group, readable and writable for it (u+rwX too, so a folder it made 0000 or 0555 can
# be walked), its folders setgid and never sticky (a sticky folder of the stage account would keep the runner and the
# person from removing what is in it). Links are never followed (find -P; chmod -R and chgrp -h do not follow them
# either); whatever is not its own it cannot change, and that stays as it is (the runner's sticky folders stay sticky).
STAGE_SHARE='gid="$1"; shift
[ $# -gt 0 ] || exit 0
chmod -R u+rwX,g+rwX,-t -- "$@" 2>/dev/null
find -P "$@" -user "$(id -u)" ! -type l ! -group "$gid" -exec chgrp -h -- "$gid" {} + 2>/dev/null
find -P "$@" -user "$(id -u)" -type d ! -perm -2000 -exec chmod g+s -- {} + 2>/dev/null
exit 0'

# stage_share: after a stage module returned, the stage account gives what it wrote to the shared group (see the
# header). Nothing outside the sandbox, and nothing before the preflight named the folders.
stage_share() {
    stage_isolated || return 0
    [ "${#STAGE_SHARED_FOLDERS[@]}" -gt 0 ] || return 0
    as_stage bash -c "$STAGE_SHARE" _ "$(stage_shared_gid)" "${STAGE_SHARED_FOLDERS[@]}" >/dev/null 2>&1 || true
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
    local rc=0
    as_stage "$@" || rc=$?
    stage_share
    return "$rc"
}

# The test child of the preflight (run as the stage account): its ids, capability sets, whether it could open the
# runner's memory and environment, then "write-fail NAME" for a folder it cannot write in and "read-fail NAME" for an
# input it cannot read. Arguments: RUNNER_PID, then folders, "--", then inputs.
STAGE_PROBE='id -u; id -g; for n in CapEff CapPrm CapAmb CapInh; do sed -n "s/^$n:[[:space:]]*//p" /proc/self/status; done
if (exec 3< "/proc/$1/mem") 2>/dev/null; then echo mem-open; else echo mem-refused; fi
if (exec 3< "/proc/$1/environ") 2>/dev/null; then echo environ-open; else echo environ-refused; fi
shift
while [ $# -gt 0 ] && [ "$1" != "--" ]; do
    probe="$(mktemp "$1/.stage-probe.XXXXXX" 2>/dev/null)" && rm -f "$probe" || echo "write-fail ${1##*/}"
    shift
done
[ $# -gt 0 ] && shift
for input in "$@"; do
    if [ -d "$input" ]; then
        [ -r "$input" ] && [ -x "$input" ] && [ -z "$(find -L "$input" ! -readable -print -quit 2>/dev/null)" ] \
            || echo "read-fail ${input##*/}"
    elif [ -e "$input" ]; then
        [ -r "$input" ] || echo "read-fail ${input##*/}"
    fi
done'

# PROBE_OUTPUT: the problem of the test child's answer as "REASON text", nothing when it is the stage account without
# rights that cannot read the runner and can write and read what a module needs
stage_switch_problem() {
    local lines=() line mask
    while IFS= read -r line; do lines+=("$line"); done <<< "$1"
    if [ "${#lines[@]}" -lt 8 ] || [ "${lines[0]}" != "$STAGE_UID" ] || [ "${lines[1]}" != "$STAGE_GID" ]; then
        echo "switch_failed The test switch to the stage account ($STAGE_UID:$STAGE_GID) did not work, so no stage module was started."
        return 0
    fi
    for line in "${lines[@]:2:4}"; do
        mask="$(hex_mask "$line")"
        if [ "$mask" -ne 0 ]; then
            echo "stage_has_rights The stage account still had capabilities after the switch ($(cap_list "$((mask < 0 ? 0 : mask))")), so no stage module was started."
            return 0
        fi
    done
    if [ "${lines[6]}" != "mem-refused" ] || [ "${lines[7]}" != "environ-refused" ]; then
        echo "reads_runner A test child under the stage account could open the stage runner's memory or environment, so the key of the end record would not be safe; no stage module was started."
        return 0
    fi
    for line in "${lines[@]:8}"; do
        case "$line" in
            "write-fail "*)
                echo "folders The stage account cannot write the folder ${line#write-fail } that the stage modules write, so no stage module was started."
                return 0 ;;
            "read-fail "*)
                echo "inputs The stage account cannot read ${line#read-fail } (an input of the build); its files must be readable for your group. No stage module was started."
                return 0 ;;
        esac
    done
}

# stage_folders WORK STAGE_DIR FOLDER...: prints the folders the modules write (those the runner can write) after
# making them writable for the runner's group (see the header). Only folders the runner owns are changed; a mode that
# cannot be set (a mount of a host that keeps its own modes) is left to the test child, which tries to write there.
stage_folders() {
    local work="$1" stage_dir="$2" folder mode
    shift 2
    for folder in "$work" "$work/tmp" "$work/home" "$work/cache" "$stage_dir" "$@"; do
        [ -d "$folder" ] && [ ! -L "$folder" ] && [ -w "$folder" ] || continue
        mode=2775
        case "$folder" in "$work"|"$work/tmp"|"$work/home"|"$work/cache"|"$stage_dir") mode=3775 ;; esac
        if [ -O "$folder" ]; then
            chmod "$mode" "$folder" 2>/dev/null || true
        fi
        printf '%s\n' "$folder"
    done
    # the stages folder itself only for the runner: a module can neither rename nor replace a job folder
    if [ -d "$work/stages" ] && [ -O "$work/stages" ]; then
        chmod 0755 "$work/stages" 2>/dev/null || true
    fi
}

stage_refuse() {  # "REASON text": says why and exits with the reason's code (the EXIT trap writes "failed")
    local reason="${1%% *}" text="${1#* }"
    echo "ERROR: Stage user check failed ($reason): $text" >&2
    echo "=== Stage user check failed: $reason ===" >&2
    exit "${STAGE_REFUSAL_EXIT[$reason]:-85}"
}

# stage_preflight WORK STAGE_DIR FOLDER...: in the build sandbox, before the first stage (see the header). The inputs
# the test child reads are the build's: MODEL_SOURCE, DATASET_PATH, IMATRIX_PATH and HARDWARE_CONFIG_FILE.
stage_preflight() {
    local problem probe folders=() folder
    stage_isolated || return 0
    problem="$(stage_rights_problem /proc/self/status)"
    [ -z "$problem" ] || stage_refuse "$problem"
    if ! command -v setpriv >/dev/null 2>&1; then
        stage_refuse "no_setpriv The build image has no setpriv (util-linux), so the stage runner cannot switch to the stage account."
    fi
    while IFS= read -r folder; do folders+=("$folder"); done < <(stage_folders "$@")
    probe="$(as_stage bash -c "$STAGE_PROBE" _ "$$" "${folders[@]}" -- "${MODEL_SOURCE:-}" "${DATASET_PATH:-}" \
        "${IMATRIX_PATH:-}" "${HARDWARE_CONFIG_FILE:-}" 2>/dev/null || true)"
    problem="$(stage_switch_problem "$probe")"
    [ -z "$problem" ] || stage_refuse "$problem"
    STAGE_SHARED_FOLDERS=("${folders[@]}")
    echo "=== Isolation: stage modules run under their own user (stage account $STAGE_UID:$STAGE_GID, no capabilities) ==="
}
