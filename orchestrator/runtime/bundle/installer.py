#!/usr/bin/env python3
"""
The bundle's on-board installer of the unprivileged steps: one fixed POSIX sh program (INSTALLER_SH, the same text
for every bundle) and the bundle's compiled steps (render_steps: one tab-separated, typed step per line, made from the
manifest). Nothing a variable holds is ever pasted into shell text: the generator checked each value against its type,
the steps carry them as data, and the installer checks every field again with its own patterns before it acts and
quotes what it writes (DECISIONS: POSIX sh only where it is safe; every root step goes through the Rust helper
llmcf-install, not this program).

The framework's signing key signs install.sh, steps.tsv and manifest.json; the board's gate (board.py, operations
bundle-run and bundle-kept) copies them into a private folder of their own, checks the three signatures there and
only then runs `sh install.sh ACTION RUNDIR SOURCE BUNDLE_ID`. The installer then:

- plan: checks every step against the board's state and reports what it would do (already, would, needs-root,
  skipped), with every file of the bundle hash-checked and the free space measured; changes nothing;
- apply: checks everything first (the steps, the manifest's sha256, each file's sha256 before it is used, the free
  space) and refuses with nothing changed when one check fails; then runs each step that is not done yet. Each step
  checks the state first (a file with the right sha256, an unpacked runtime whose SHA256SUMS pass, the start script
  with exactly its text), so a second run changes nothing; a run cut off in a step continues at that step (the
  marker of a step is written only after it is done, a copy lands under its name only after its sha256 passed).
  Root actions stop it before them with the plain words that they need llmcf-install, unless the bundle is the
  "unprivileged only" variant (then they are skipped and reported);
- status: what is in place, without changing anything;
- rollback: undoes the steps of the last apply run, newest first;
- uninstall / plan-uninstall: undoes every step this bundle did (its journal), newest first, and removes its own
  state; the plan only lists it.

Every line it prints starts with `llmcf-bundle: ` (report lines, the last one `result=...`); report() reads them.
Exit status: 0 done, 3 refused or failed, 4 stopped before root actions.

The layout on the board, in the account's home: ~/.local/share/llm-framework/bundle/<bundle id>/components/<component>
(the files, the unpacked runtime, start.sh), .state/ (step markers, the journal, the last run, a lock), .kept/ (the
signed installer, steps and manifest of the last apply, kept before its first step, for status, rollback and
uninstall later, also after a run that was cut off).
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from orchestrator.runtime.bundle.manifest import BundleManifest, ManifestError
from orchestrator.runtime.server_policy import POSIX_CLEARED_PATH

STEPS_MAGIC = "#llmcf-bundle-steps 1"
BUNDLE_REL = ".local/share/llm-framework/bundle"
REPORT_PREFIX = "llmcf-bundle: "
ACTIONS = ("plan", "apply", "status")
KEPT_ACTIONS = ("status", "rollback", "uninstall", "plan-uninstall")
EXIT_OK, EXIT_REFUSED, EXIT_ROOT = 0, 3, 4
# llmcf-install's exit status when a root action failed after others were done (its journal says what: roll back or
# run it again); its other statuses are the installer's (0 done, 3 refused with nothing changed).
EXIT_ROOT_FAILED = 5
SIGNED_FILES = ("install.sh", "steps.tsv", "manifest.json")
# The root helper a bundle with root actions carries next to them (signed like them; the installer keeps it with them
# in .kept/, where the gate operation bundle-root takes it from).
ROOT_HELPER_FILE = "llmcf-install"
ROOT_ACTIONS = ("plan", "apply", "status", "rollback", "uninstall", "plan-uninstall")
SEP = "--"   # a staged file of the bundle is <component>--<file name> in the upload folder


def staged_name(component: str, name: str) -> str:
    return f"{component}{SEP}{name}"


def _runtime_id(component) -> Optional[str]:
    if component.start is None:
        return None
    parts = component.start.program.split("/")
    if len(parts) >= 3 and parts[0] == "runtime" and re.fullmatch(r"[0-9a-f]{16}", parts[1]):
        return parts[1]
    return None


def render_steps(manifest: BundleManifest, manifest_sha256: str) -> str:
    """The bundle's steps for the installer, from the checked manifest (one typed step per line, tab-separated, the
    root actions last)."""
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256 or ""):
        raise ManifestError("the manifest's sha256 is needed for its steps")
    lines = [STEPS_MAGIC, f"bundle\t{manifest.bundle_id}", f"manifest\t{manifest_sha256}", f"mode\t{manifest.mode}"]
    for comp in manifest.components:
        lines.append(f"dir\t{comp.id}")
    for comp in manifest.components:
        for f in comp.files:
            row = [f.source == "online" and "fetch" or "file", comp.id, f.name, f.sha256, str(f.size), f.mode]
            if f.source == "online":
                row.append(f.url or "")
            lines.append("\t".join(row))
    for comp in manifest.components:
        id16 = _runtime_id(comp)
        runtime = [f for f in comp.files if f.role == "runtime"]
        if runtime:
            if id16 is None:
                raise ManifestError(f"component {comp.id}: its runtime package needs its program in runtime/<build>/")
            lines.append(f"unpack\t{comp.id}\t{runtime[0].name}\t{id16}")
        if comp.keygen:
            if id16 is None:
                raise ManifestError(f"component {comp.id}: the key file comes from a runtime package")
            lines.append(f"keygen\t{comp.id}\t{id16}")
        if comp.start is not None:
            lines.append("\t".join(["start", comp.id, comp.start.program, *comp.start.args]))
    for action in manifest.root_actions:
        values = [f"{k}={v}" for k, v in action.model_dump(exclude_none=True).items()
                  if k not in ("action", "component")]
        lines.append("\t".join(["root", action.action, action.component, *values]))
    for line in lines[1:]:
        if any(ch in line for ch in "\n\r'\"\\$`;&|<>(){}*?[]!~#^ ") and not line.startswith("fetch\t"):
            raise ManifestError("a step holds a character the steps never take")   # pragma: no cover - model checks
    return "\n".join(lines) + "\n"


# The installer. Its fields are checked by its own patterns (the same sets as manifest.py); the only text it writes is
# start.sh, whose arguments it quotes one by one after their check.
INSTALLER_SH = r'''#!/bin/sh
# llmcf-bundle: the installer of the unprivileged steps of the LLM Cross-Compiler Framework. The same program for every
# bundle; what it does comes from the signed steps file next to it. Never edit it on the board: the gate checks its
# signature before it runs it.
set -u
umask 077
LC_ALL=C; export LC_ALL
set -f
TAB=$(printf '\t')
act=${1-}; run=${2-}; src=${3-}; bid=${4-}
say() { printf 'llmcf-bundle: %s\n' "$*"; }
stop() { say "result=$1 changed=${changed:-0} $2"; exit "${3:-3}"; }
changed=0
case $act in plan|apply|status|rollback|uninstall|plan-uninstall) ;; *) stop refused "unknown action" ;; esac
case $bid in ""|-*|.*|*[!a-z0-9._-]*) stop refused "not a bundle id" ;; esac
[ -f "$run/steps.tsv" ] && [ -f "$run/manifest.json" ] || stop refused "the steps or the manifest are missing"
[ -n "${HOME-}" ] && [ -d "$HOME" ] || stop refused "no home folder"
B="$HOME/@BUNDLE_REL@"
D="$B/$bid"; S="$D/.state"; K="$D/.kept"; C="$D/components"

k_word() { case $1 in ""|-*|.*|*[!A-Za-z0-9._-]*) return 1 ;; esac; [ ${#1} -le 128 ]; }
k_sha() { case $1 in *[!0-9a-f]*) return 1 ;; esac; [ ${#1} -eq 64 ]; }
k_id16() { case $1 in *[!0-9a-f]*) return 1 ;; esac; [ ${#1} -eq 16 ]; }
k_uint() { case $1 in ""|*[!0-9]*) return 1 ;; esac; [ ${#1} -le 15 ]; }
k_mode() { case $1 in 0600|0644|0700|0755) return 0 ;; esac; return 1; }
k_url() {
  case $1 in https://*|http://*) ;; *) return 1 ;; esac
  case $1 in *[!A-Za-z0-9._~+,=:@%/-]*) return 1 ;; esac
  [ ${#1} -le 2048 ]
}
k_prog() {
  case $1 in ""|/*|*//*|*/|.*|*/.*|*[!A-Za-z0-9._/-]*) return 1 ;; esac
  [ ${#1} -le 256 ]
}
k_arg() {
  case $1 in @DIR@/*) a=${1#@DIR@/} ;; *) a=$1 ;; esac
  case $a in ""|*[!A-Za-z0-9._+/:=,-]*) return 1 ;; esac
  case /$a/ in */../*|*/./*) return 1 ;; esac
  [ ${#a} -le 256 ]
}
k_kv() {
  case $1 in [a-z]*=*) ;; *) return 1 ;; esac
  case $1 in *[!A-Za-z0-9._=-]*) return 1 ;; esac
}
k_root() {
  case $1 in create_user|install_unit|set_cgroup|place_root_file|remove_user|remove_unit|remove_cgroup|\
remove_root_file) return 0 ;; esac
  return 1
}
sha_of() { sha256sum "$1" 2>/dev/null | cut -d ' ' -f 1; }

# --- the steps file: its header, then every line checked before anything happens
mode=
root_seen=
check_steps() {
  n=0
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    case $n in
      1) [ "$line" = "@MAGIC@" ] || return 1; continue ;;
      2) [ "$line" = "bundle${TAB}$bid" ] || return 1; continue ;;
      3) case $line in "manifest${TAB}"*) msha=${line#manifest"$TAB"} ;; *) return 1 ;; esac
         k_sha "$msha" || return 1
         [ "$(sha_of "$run/manifest.json")" = "$msha" ] || return 1; continue ;;
      4) case $line in "mode${TAB}full") mode=full ;; "mode${TAB}unprivileged") mode=unprivileged ;; *) return 1 ;; esac
         continue ;;
    esac
    check_line "$line" || { bad=$line; return 1; }
  done < "$run/steps.tsv"
  [ "$n" -ge 5 ] && [ -n "$mode" ]
}
check_line() {
  IFS=$TAB
  # shellcheck disable=SC2086 # the line is split at its tabs on purpose (globbing is off)
  set -- $1
  unset IFS
  t=${1-}; [ $# -ge 2 ] || return 1
  if [ "$t" = root ]; then root_seen=1; else [ -z "$root_seen" ] || return 1; fi
  case $t in
    dir) [ $# -eq 2 ] && k_word "$2" ;;
    file) [ $# -eq 6 ] && k_word "$2" && k_word "$3" && k_sha "$4" && k_uint "$5" && k_mode "$6" ;;
    fetch) [ $# -eq 7 ] && k_word "$2" && k_word "$3" && k_sha "$4" && k_uint "$5" && k_mode "$6" && k_url "$7" ;;
    unpack) [ $# -eq 4 ] && k_word "$2" && k_word "$3" && k_id16 "$4" ;;
    keygen) [ $# -eq 3 ] && k_word "$2" && k_id16 "$3" ;;
    start) [ $# -ge 3 ] && k_word "$2" && k_prog "$3" || return 1
           shift 3; for a_ in "$@"; do k_arg "$a_" || return 1; done ;;
    root) [ $# -ge 3 ] && k_root "$2" && k_word "$3" || return 1
          shift 3; for a_ in "$@"; do k_kv "$a_" || return 1; done ;;
    *) return 1 ;;
  esac
}

key_of() { printf '%s' "$1" | sha256sum | cut -c1-16; }

# --- the start script a component gets (its arguments checked above, each one quoted)
render_start() {
  comp_=$1; prog_=$2; shift 2
  printf '%s\n' '#!/bin/sh' "# start script of the bundle component $comp_ (written by the bundle installer)"
  printf '%s\n' 'case $0 in */*) llmcf_dir=${0%/*} ;; *) llmcf_dir=. ;; esac'
  printf '%s\n' 'llmcf_dir=$(cd "$llmcf_dir" && pwd) || exit 1'
  case $prog_ in *.sh) runner='/bin/sh ' ;; *) runner= ;; esac
  printf 'exec env -i HOME="$HOME" PATH=@PATH@ LANG=C LC_ALL=C %s"$llmcf_dir"/%s' "$runner" "$prog_"
  for a_ in "$@"; do
    case $a_ in
      @DIR@/*) printf ' "$llmcf_dir"/%s' "${a_#@DIR@/}" ;;
      *) printf " '%s'" "$a_" ;;
    esac
  done
  printf ' "$@"\n'
}

# --- one step: is it done, do it, undo it
step_done() {
  case $t in
    dir) [ -d "$C/$1" ] ;;
    file|fetch) [ -f "$C/$1/$2" ] && [ "$(sha_of "$C/$1/$2")" = "$3" ] ;;
    unpack) [ -f "$C/$1/runtime/.unpacked-$3" ] && [ -d "$C/$1/runtime/$3" ] &&
            (cd "$C/$1/runtime/$3" && sha256sum -c SHA256SUMS >/dev/null 2>&1) ;;
    keygen) [ -s "$HOME/.config/llm-framework/llama-server.key" ] ;;
    start) c_=$1; shift; [ -f "$C/$c_/start.sh" ] &&
           [ "$(render_start "$c_" "$@" | sha256sum | cut -d ' ' -f 1)" = "$(sha_of "$C/$c_/start.sh")" ] ;;
    *) return 1 ;;
  esac
}
step_run() {
  case $t in
    dir) mkdir -p "$C/$1" ;;
    file|fetch)
      out_="$C/$1/$2"; rm -f "$out_.part"
      if [ -f "$src/$1--$2" ]; then
        cp -- "$src/$1--$2" "$out_.part" || return 1
      elif [ "$t" = fetch ]; then
        if command -v curl >/dev/null 2>&1; then curl -fsSL --proto =https,http -o "$out_.part" -- "$6" || return 1
        elif command -v wget >/dev/null 2>&1; then wget -q -O "$out_.part" -- "$6" || return 1
        else say "failed fetch $1 $2 (neither curl nor wget on the board)"; return 1; fi
      else
        return 1
      fi
      [ "$(sha_of "$out_.part")" = "$3" ] || { rm -f "$out_.part"; say "failed $t $1 $2 (its sha256 is not the manifest's)"; return 1; }
      chmod "$5" "$out_.part" && mv -f "$out_.part" "$out_" ;;
    unpack)
      r_="$C/$1/runtime"; mkdir -p "$r_" || return 1
      rm -rf "$r_/.tmp-$3"; mkdir "$r_/.tmp-$3" || return 1
      tar -xzf "$C/$1/$2" -C "$r_/.tmp-$3" || return 1
      [ "$(ls -A "$r_/.tmp-$3")" = "$3" ] || { say "failed unpack $1 (the package holds more than its build)"; return 1; }
      (cd "$r_/.tmp-$3/$3" && sha256sum -c SHA256SUMS >/dev/null) || { say "failed unpack $1 (SHA256SUMS)"; return 1; }
      chmod -R go-rwx "$r_/.tmp-$3"
      rm -rf "${r_:?}/${3:?}"; mv "$r_/.tmp-$3/$3" "$r_/$3" && rmdir "$r_/.tmp-$3" && : > "$r_/.unpacked-$3" ;;
    keygen) sh "$C/$1/runtime/$2/llama-server.sh" keygen >/dev/null ;;
    start) c_=$1; shift; render_start "$c_" "$@" > "$C/$c_/start.sh.part" && chmod 0700 "$C/$c_/start.sh.part" &&
           mv -f "$C/$c_/start.sh.part" "$C/$c_/start.sh" ;;
  esac
}
step_undo() {
  case $t in
    dir) rmdir "$C/$1" 2>/dev/null || { say "kept dir $1 (not empty)"; return 0; } ;;
    file|fetch) rm -f "$C/$1/$2" "$C/$1/$2.part" ;;
    unpack) rm -rf "$C/$1/runtime/$3" "$C/$1/runtime/.tmp-$3" "$C/$1/runtime/.unpacked-$3"
            rmdir "$C/$1/runtime" 2>/dev/null || true ;;
    keygen) say "kept keygen $1 (the runtime's key file is shared with deploy way 2)"; return 0 ;;
    start) rm -f "$C/$1/start.sh" "$C/$1/start.sh.part" ;;
  esac
  say "removed $t $1 ${2-}"
}
what_of() {
  case $t in
    file|fetch|unpack) printf '%s %s' "$1" "$2" ;;
    *) printf '%s' "$1" ;;
  esac
}

# --- the actions
lock() {
  mkdir -p "$S" || stop failed "the state folder cannot be made"
  mkdir "$S/.lock" 2>/dev/null || stop refused "another run of this bundle's installer is going on (or one stopped: remove the folder .state/.lock in the bundle's folder $bid on the board)"
  trap 'rmdir "$S/.lock" 2>/dev/null' EXIT
}

precheck() {   # every file of the bundle hash-checked before anything changes, and the free space
  need=0
  while IFS= read -r line; do
    IFS=$TAB
    # shellcheck disable=SC2086
    set -- $line
    unset IFS
    t=$1; shift
    case $t in file|fetch) ;; *) continue ;; esac
    if step_done "$@"; then continue; fi
    if [ -f "$src/$1--$2" ]; then
      [ "$(sha_of "$src/$1--$2")" = "$3" ] || stop refused "the bundle's file $2 of $1 does not match its sha256 (nothing was changed)"
    elif [ "$t" = file ]; then
      stop refused "the bundle's file $2 of $1 is not on the board (nothing was changed)"
    fi
    need=$((need + $4))
  done < "$steps_body"
  df_at=$B; [ -d "$B" ] || df_at=$HOME
  avail=$(df -Pk "$df_at" 2>/dev/null | awk 'NR == 2 { print $4 }')
  if [ -n "$avail" ] && [ $((need / 1024 + 1)) -gt "$avail" ]; then
    stop refused "the board has $((avail / 1024)) MB free, the bundle needs $((need / 1048576 + 1)) MB (nothing was changed)"
  fi
}

each_step() {   # $1: plan, apply or status
  pending=0; roots=0
  while IFS= read -r line; do
    IFS=$TAB
    # shellcheck disable=SC2086
    set -- $line
    unset IFS
    t=$1; shift
    if [ "$t" = root ]; then
      roots=$((roots + 1))
      if [ "$mode" = unprivileged ]; then say "skipped root $2 $1 (the unprivileged-only variant)"
      else say "needs-root root $2 $1 (llmcf-install)"; fi
      continue
    fi
    if step_done "$@"; then
      say "already $t $(what_of "$@")"
      if [ "$act" = apply ] && [ ! -f "$S/done/$(key_of "$line")" ]; then
        mkdir -p "$S/done" && : > "$S/done/$(key_of "$line")" && printf '%s\t%s\n' "$(key_of "$line")" "$line" >> "$S/journal"
      fi
      continue
    fi
    case $act in
      apply)
        if step_run "$@"; then
          mkdir -p "$S/done" && : > "$S/done/$(key_of "$line")"
          printf '%s\t%s\n' "$(key_of "$line")" "$line" >> "$S/journal"
          printf '%s\t%s\n' "$(key_of "$line")" "$line" >> "$S/last-run"
          changed=$((changed + 1)); say "done $t $(what_of "$@")"
        else
          say "failed $t $(what_of "$@")"
          stop failed "a step failed; run the deploy again to continue where it stopped, or roll back" 3
        fi ;;
      plan) say "would $t $(what_of "$@")"; pending=$((pending + 1)) ;;
      *) say "pending $t $(what_of "$@")"; pending=$((pending + 1)) ;;
    esac
  done < "$steps_body"
}

undo_lines() {   # the journal file $1, newest first
  [ -f "$1" ] || return 0
  awk '{ l[NR] = $0 } END { for (i = NR; i > 0; i--) print l[i] }' "$1" > "$S/.undo"
  while IFS= read -r entry; do
    line=${entry#*"$TAB"}
    root_seen=
    check_line "$line" || stop refused "the journal holds a line the installer never wrote"
    IFS=$TAB
    # shellcheck disable=SC2086
    set -- $line
    unset IFS
    t=$1; shift
    if [ "$act" = plan-uninstall ]; then say "would-remove $t $(what_of "$@")"; continue; fi
    step_undo "$@" && changed=$((changed + 1))
    rm -f "$S/done/${entry%%"$TAB"*}"
  done < "$S/.undo"
  rm -f "$S/.undo"
}

case $act in
  rollback|uninstall|plan-uninstall)
    [ -d "$S" ] || stop absent "this bundle is not installed on the board" 0
    root_seen=
    check_steps || stop refused "the steps file is not the one of this bundle"
    if [ "$act" = plan-uninstall ]; then
      mkdir -p "$S"; undo_lines "$S/journal"; stop plan "" 0
    fi
    lock
    if [ "$act" = rollback ]; then
      undo_lines "$S/last-run"
      if [ -f "$S/journal" ] && [ -s "$S/last-run" ]; then
        awk -F "$TAB" 'NR == FNR { k[$1]; next } !($1 in k)' "$S/last-run" "$S/journal" > "$S/.journal" &&
          mv -f "$S/.journal" "$S/journal"
      fi
      rm -f "$S/last-run"
      stop ok "the last run was rolled back" 0
    fi
    undo_lines "$S/journal"
    rm -rf "$K"; rm -f "$S/journal" "$S/last-run"; rm -rf "$S/done"
    rmdir "$S/.lock" 2>/dev/null; trap - EXIT; rmdir "$S" 2>/dev/null
    rmdir "$C" 2>/dev/null; rmdir "$D" 2>/dev/null
    stop ok "uninstalled" 0 ;;
esac

root_seen=
check_steps || stop refused "the steps are not valid (a field of a type it does not take${bad:+: line $n}); nothing was changed"
steps_body="$run/.body"
tail -n +5 "$run/steps.tsv" > "$steps_body"
case $act in
  plan)
    precheck
    each_step
    say "needs=$((need / 1048576 + 1))MB"
    stop plan "pending=$pending root=$roots" 0 ;;
  status)
    [ -d "$S" ] || stop absent "this bundle is not installed on the board" 0
    each_step
    if [ "$pending" -eq 0 ]; then stop installed "root=$roots" 0; fi
    stop partial "pending=$pending" 0 ;;
  apply)
    precheck
    lock
    : > "$S/last-run"
    # kept before the first step changes anything: a run cut off can be rolled back or uninstalled, not only continued
    mkdir -p "$K" || stop failed "the installer could not be kept on the board (nothing was changed)"
    for f_ in install.sh steps.tsv manifest.json @ROOT_FILES@; do
      if [ "$f_" = @ROOT_FILES@ ] && [ ! -f "$run/$f_" ]; then continue; fi
      cp -- "$run/$f_" "$K/$f_.part" && mv -f "$K/$f_.part" "$K/$f_" || stop failed "the installer could not be kept on the board (nothing was changed)"
      if [ -f "$run/$f_.sig" ]; then cp -- "$run/$f_.sig" "$K/$f_.sig.part" && mv -f "$K/$f_.sig.part" "$K/$f_.sig"; fi
    done
    each_step
    if [ "$roots" -gt 0 ] && [ "$mode" = full ]; then
      stop stopped-root "the unprivileged steps are done; the $roots root action(s) above run only through llmcf-install, the framework's root helper, after your own yes for them (or choose the unprivileged-only variant to skip them)" 4
    fi
    stop ok "" 0 ;;
esac
'''


def installer_sh() -> str:
    """The installer program as the bundle ships it (INSTALLER_SH with its constants in place)."""
    text = (INSTALLER_SH.replace("@BUNDLE_REL@", BUNDLE_REL).replace("@MAGIC@", STEPS_MAGIC)
            .replace("@PATH@", POSIX_CLEARED_PATH).replace("@ROOT_FILES@", ROOT_HELPER_FILE))
    if re.search(r"@[A-Z_]+@", text.replace("@DIR@", "")):  # pragma: no cover - template check
        raise RuntimeError("the installer template has an unfilled placeholder")
    return text


@dataclass
class Report:
    """What the installer said: its result word, the changed count, the rest of its last line, and every report line
    (states such as already, would, done, failed, needs-root, skipped, removed)."""
    result: str = ""
    changed: int = 0
    detail: str = ""
    lines: List[Dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result in ("ok", "plan", "installed", "partial", "absent", "stopped-root")

    @property
    def needs_password(self) -> bool:
        """llmcf-install was not run: the board's sudo asks for a password (gate operation bundle-root)."""
        return self.result == "needs-password"

    @property
    def helper(self) -> str:
        """The version llmcf-install named in its first line ("" when this is the unprivileged installer's report)."""
        for line in self.lines:
            if line["state"] == "helper" and line["step"] == ROOT_HELPER_FILE:
                return line["what"].split(" ")[0][:20]
        return ""

    def count(self, state: str) -> int:
        return sum(1 for line in self.lines if line["state"] == state)


def report(text: str) -> Report:
    """The report of the installer or of llmcf-install (both write the same `llmcf-bundle: ` lines, the last one
    `result=WORD changed=N ...`) from their output; anything else is ignored."""
    out = Report()
    for raw in (text or "").splitlines():
        if not raw.startswith(REPORT_PREFIX):
            continue
        body = raw[len(REPORT_PREFIX):].strip()
        m = re.match(r"result=([a-z-]+) changed=([0-9]+) ?(.*)$", body)
        if m:
            out.result, out.changed, out.detail = m.group(1), int(m.group(2)), m.group(3)[:500]
            continue
        parts = body.split(" ")
        state = parts[0] if parts else ""
        if re.fullmatch(r"[a-z-]{2,20}", state):
            out.lines.append({"state": state, "step": parts[1] if len(parts) > 1 else "",
                              "what": " ".join(parts[2:])[:300]})
        elif body.startswith("needs="):
            out.detail = body[:100]
    return out


__all__ = ["ACTIONS", "BUNDLE_REL", "EXIT_OK", "EXIT_REFUSED", "EXIT_ROOT", "EXIT_ROOT_FAILED", "INSTALLER_SH",
           "KEPT_ACTIONS", "REPORT_PREFIX", "ROOT_ACTIONS", "ROOT_HELPER_FILE", "Report", "SIGNED_FILES", "STEPS_MAGIC",
           "installer_sh", "render_steps", "report", "staged_name"]
