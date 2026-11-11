# shellcheck shell=sh
# tune.sh - the stage tune of the tuning job (part of the runtime layer; tools.sh says what that is)
#
# Auto-Tuning (orchestrator/Core/tuning.py, DESIGN "Targets, hardware and templates"): the stage runner runs this file as
# the last stage of JOB_TYPE=tune, as the stage account and like a module, after the target's stages source, config and
# convert wrote the F16 GGUF the quantizer starts from (a GGUF input is that file itself). It never runs in a build and
# no target ships it: it uses only the image's pinned llama.cpp tools, llama-quantize and llama-perplexity in
# $LLAMA_CPP_PATH/build/bin.
#
#   1. The reference: the F16 (TUNE_REFERENCE=F16), or a Q8_0 quantized from it (TUNE_REFERENCE=Q8_0, chosen by the host
#      when the F16 does not fit into this computer's memory).
#   2. The reference's logits on the evaluation text (TUNE_TEXT), recorded once in the work folder:
#      llama-perplexity --kl-divergence-base, TUNE_CHUNKS chunks of TUNE_CTX tokens.
#   3. Every candidate of TUNE_CANDIDATES ("TYPE:i" with the importance matrix IMATRIX_PATH, "TYPE:-" without;
#      "TYPE:i:ID" or "TYPE:-:ID" with the per-tensor types of TUNE_TT_ID, PATTERN=TYPE entries separated by spaces,
#      as orchestrator/Core/tensor_types.py checked them), in turn: quantized from the F16 as a build quantizes it,
#      measured with llama-perplexity --kl-divergence against the recorded logits, and deleted again. One failing
#      candidate is recorded as failed; the others go on.
#
# Results: $BUILD_CACHE_DIR/output/tuning/, per candidate LABEL.info (type, importance matrix, bytes, seconds, status,
# exit, inputs) and LABEL.log (the statistic lines of llama-perplexity, which the host parses into numbers), and
# reference.info and reference.log. A candidate whose .info has the same inputs is not measured again: a run continued
# after a stop measures only what is missing (the reference's logits too, by reference.done in the work folder).
#
# POSIX sh (the layer's busybox sh in the container; any POSIX sh by hand).

set -eu

die() { echo "ERROR: $*" >&2; exit 1; }

OUT="${BUILD_CACHE_DIR:-/build-cache}/output"
WORK="${BUILD_SCRATCH_DIR:-${BUILD_CACHE_DIR:-/build-cache}}/tune"
RESULTS="$OUT/tuning"
BIN="${LLAMA_CPP_PATH:-/usr/src/llama.cpp}/build/bin"
QUANTIZE="$BIN/llama-quantize"
PERPLEXITY="$BIN/llama-perplexity"
CTX="${TUNE_CTX:-512}"
CHUNKS="${TUNE_CHUNKS:-16}"
REFERENCE="${TUNE_REFERENCE:-F16}"
CANDIDATES="${TUNE_CANDIDATES:-}"
# The lines of llama-perplexity's output the host reads (its statistics; the per-chunk table is not kept)
STATS='Final estimate|computing over|calculating perplexity over|Mean PPL|Mean ln|Cor\(ln|Mean +KLD|Same top p|RMS|Mean +.p'

case "$CTX" in ''|*[!0-9]*) die "TUNE_CTX must be a number of tokens." ;; esac
case "$CHUNKS" in ''|*[!0-9]*) die "TUNE_CHUNKS must be a number of chunks." ;; esac
case "$REFERENCE" in F16|Q8_0) ;; *) die "TUNE_REFERENCE must be F16 or Q8_0." ;; esac
[ -n "$CANDIDATES" ] || die "No candidate types to measure (TUNE_CANDIDATES is empty)."
[ -x "$QUANTIZE" ] || die "llama-quantize is not in this target's image ($BIN); the tuning measures with the image's own llama.cpp tools."
[ -x "$PERPLEXITY" ] || die "llama-perplexity is not in this target's image ($BIN); the tuning measures with the image's own llama.cpp tools."
[ -f "${TUNE_TEXT:-}" ] || die "The evaluation text is missing (TUNE_TEXT)."

mkdir -p "$WORK" "$RESULTS"

# The F16 the candidates are quantized from: the build's F16 intermediate (F16_FILE: the one the convert stage wrote,
# or the one kept from an earlier build of this model), a GGUF input itself, else the one F16 the convert stage wrote
# into the output folder
SOURCE=""
if [ -n "${F16_FILE:-}" ] && [ -s "$F16_FILE" ]; then
    SOURCE="$F16_FILE"
fi
case "${MODEL_SOURCE:-}" in
    *.gguf|*.GGUF)
        if [ -z "$SOURCE" ] && [ -f "$MODEL_SOURCE" ]; then
            SOURCE="$MODEL_SOURCE"
        fi ;;
esac
if [ -z "$SOURCE" ]; then
    for f in "$OUT"/*-F16.gguf; do
        if [ -f "$f" ]; then
            [ -z "$SOURCE" ] || die "The convert stage left more than one F16 model in the output folder."
            SOURCE="$f"
        fi
    done
    [ -n "$SOURCE" ] || die "The convert stage left no F16 model (*-F16.gguf) for the tuning."
fi

digest() { sha256sum | cut -d' ' -f1; }
file_facts() { printf '%s %s' "${1##*/}" "$(stat -L -c '%s %Y' "$1" 2>/dev/null || wc -c < "$1")"; }
INPUTS="$( { printf 'tune 2\n'; file_facts "$SOURCE"; printf '\nreference=%s ctx=%s chunks=%s\n' "$REFERENCE" "$CTX" "$CHUNKS"
             sha256sum "$TUNE_TEXT" | cut -d' ' -f1
             if [ -f "${IMATRIX_PATH:-}" ]; then sha256sum "$IMATRIX_PATH" | cut -d' ' -f1; fi; } | digest)"

# run_measured LOG COMMAND...: COMMAND with its output shown as it goes (the build's log; a long pass is no idle step)
# and kept in LOG; the exit status of COMMAND
run_measured() {
    _log="$1"
    shift
    rm -f "$WORK/rc"
    { _rc=0; "$@" 2>&1 || _rc=$?; echo "$_rc" > "$WORK/rc"; } | tee "$_log"
    _rc="$(cat "$WORK/rc" 2>/dev/null || echo 1)"
    rm -f "$WORK/rc"
    return "$_rc"
}

stats_of() { grep -E "$STATS" "$1" || true; }

# perplexity_pass LOG MODEL [TEXT]: the one KLD invocation of llama-perplexity (orchestrator/Core/tuning.py
# perplexity_args gives the same arguments to Ditto's measurement, evals/tier_check.py; a test keeps both the same).
# With TEXT the logits of MODEL on TEXT are recorded in $LOGITS; without it MODEL is compared with them (the tokens
# come from the logits file). The batch is the context, one sequence per batch: a hybrid model with recurrent layers
# gives garbage with several chunks in one batch.
perplexity_pass() {
    _pp_log="$1"
    _pp_model="$2"
    if [ "$#" -ge 3 ]; then
        run_measured "$_pp_log" "$PERPLEXITY" -m "$_pp_model" -f "$3" -c "$CTX" -b "$CTX" --chunks "$CHUNKS" \
            --kl-divergence-base "$LOGITS"
    else
        run_measured "$_pp_log" "$PERPLEXITY" -m "$_pp_model" -c "$CTX" -b "$CTX" --chunks "$CHUNKS" \
            --kl-divergence-base "$LOGITS" --kl-divergence
    fi
}

# --- 1. and 2. The reference and its logits --------------------------------------------------------------------------
LOGITS="$WORK/reference.kld"
if [ -s "$LOGITS" ] && [ "$(cat "$WORK/reference.done" 2>/dev/null)" = "$INPUTS" ] && [ -f "$RESULTS/reference.info" ]; then
    echo "=== Tuning: the reference's logits resumed ==="
else
    rm -f "$LOGITS" "$WORK/reference.done" "$RESULTS/reference.info" "$RESULTS/reference.log"
    REF="$SOURCE"
    if [ "$REFERENCE" = Q8_0 ]; then
        REF="$WORK/reference-Q8_0.gguf"
        rm -f "$REF"
        echo "=== Tuning: the Q8_0 reference (the F16 does not fit into this computer's memory) ==="
        "$QUANTIZE" "$SOURCE" "$REF" Q8_0 || die "llama-quantize could not make the Q8_0 reference."
        [ -s "$REF" ] || die "llama-quantize wrote no Q8_0 reference."
    fi
    echo "=== Tuning: the reference's logits ($REFERENCE, $CHUNKS chunks of $CTX tokens) ==="
    started="$(date +%s)"
    rc=0
    perplexity_pass "$WORK/reference.out" "$REF" "$TUNE_TEXT" || rc=$?
    [ "$rc" -eq 0 ] || die "llama-perplexity failed on the reference (exit $rc)."
    [ -s "$LOGITS" ] || die "llama-perplexity wrote no logits of the reference."
    stats_of "$WORK/reference.out" > "$RESULTS/reference.log"
    printf 'type %s\nbytes %s\nseconds %s\nlogits_bytes %s\n' "$REFERENCE" "$(wc -c < "$REF" | tr -d ' ')" \
        "$(( $(date +%s) - started ))" "$(wc -c < "$LOGITS" | tr -d ' ')" > "$RESULTS/reference.info"
    if [ "$REF" != "$SOURCE" ]; then
        rm -f "$REF"
    fi
    printf '%s\n' "$INPUTS" > "$WORK/reference.done"
fi

# --- 3. Every candidate ----------------------------------------------------------------------------------------------
total=0
for spec in $CANDIDATES; do
    total=$((total + 1))
done
n=0
for spec in $CANDIDATES; do
    n=$((n + 1))
    type="${spec%%:*}"
    rest="${spec#*:}"
    flag="${rest%%:*}"
    tt=""
    case "$rest" in *:*) tt="${rest#*:}" ;; esac
    case "$type" in ''|*[!A-Za-z0-9_]*) die "'$type' is not a quantization type." ;; esac
    case "$flag" in i|-) ;; *) die "'$spec' is not a candidate (TYPE:i or TYPE:-, then :ID for per-tensor types)." ;; esac
    label="$type"
    if [ "$flag" = i ]; then
        [ -f "${IMATRIX_PATH:-}" ] || die "$type is to be measured with the importance matrix, but there is none."
        label="$type-imatrix"
    fi
    # The per-tensor types of the candidate (TUNE_TT_ID): llama-quantize's own options for output and token_embd,
    # --tensor-type for every other pattern; set as the positional parameters, so no pattern is expanded by the shell
    set --
    entries=""
    if [ -n "$tt" ]; then
        case "$tt" in *[!0-9a-f]*) die "'$spec' names no per-tensor types (ID: hexadecimal)." ;; esac
        eval "entries=\${TUNE_TT_$tt:-}"
        [ -n "$entries" ] || die "The per-tensor types $tt of $type are missing (TUNE_TT_$tt)."
        label="$label+$tt"
        set -f
        for entry in $entries; do
            case "$entry" in ?*=?*) ;; *) die "'$entry' is not a per-tensor type (PATTERN=TYPE)." ;; esac
            case "${entry%=*}" in
                output) set -- "$@" --output-tensor-type "${entry##*=}" ;;
                token_embd) set -- "$@" --token-embedding-type "${entry##*=}" ;;
                *) set -- "$@" --tensor-type "$entry" ;;
            esac
        done
        set +f
    fi
    key="$(printf '%s %s %s\n' "$INPUTS" "$spec" "$entries" | digest)"
    if [ "$(sed -n 's/^inputs //p' "$RESULTS/$label.info" 2>/dev/null)" = "$key" ]; then
        echo "=== Tuning $n/$total: $label resumed ==="
        continue
    fi
    rm -f "$RESULTS/$label.info" "$RESULTS/$label.log"
    echo "=== Tuning $n/$total: $label ==="
    candidate="$WORK/candidate.gguf"
    rm -f "$candidate"
    started="$(date +%s)"
    rc=0
    if [ "$flag" = i ]; then
        "$QUANTIZE" --imatrix "$IMATRIX_PATH" "$@" "$SOURCE" "$candidate" "$type" || rc=$?
    else
        "$QUANTIZE" "$@" "$SOURCE" "$candidate" "$type" || rc=$?
    fi
    status="done"
    bytes=0
    if [ "$rc" -ne 0 ] || [ ! -s "$candidate" ]; then
        status="quantize_failed"
    else
        bytes="$(wc -c < "$candidate" | tr -d ' ')"
        perplexity_pass "$WORK/candidate.out" "$candidate" || rc=$?
        stats_of "$WORK/candidate.out" > "$RESULTS/$label.log"
        if [ "$rc" -ne 0 ]; then
            status="measure_failed"
        fi
    fi
    rm -f "$candidate" "$WORK/candidate.out"
    printf 'type %s\nimatrix %s\nbytes %s\nseconds %s\nstatus %s\nexit %s\ninputs %s\n' "$type" \
        "$([ "$flag" = i ] && echo 1 || echo 0)" "$bytes" "$(( $(date +%s) - started ))" "$status" "$rc" "$key" \
        > "$RESULTS/$label.info"
    echo "=== Tuning $n/$total: $label $status ==="
done

# The stage's files for the runner's resume marker (stage_env.sh's stage_output, which a sh stage does not get)
if [ -n "${STAGE_DIR:-}" ] && [ -d "$STAGE_DIR" ]; then
    for f in "$RESULTS"/*; do
        if [ -f "$f" ]; then
            printf '%s\n' "$f" >> "$STAGE_DIR/${STAGE_NAME:-tune}.outputs"
        fi
    done
fi
echo "Tuning: $total candidates measured against the $REFERENCE reference."
