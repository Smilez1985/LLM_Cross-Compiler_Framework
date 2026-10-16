#!/usr/bin/env python3
"""
The facts catalogue (delegation design 4.1-4.3, work package WP4): every hard blocker and every warning the framework
can find before a question, with its fixed kind, and which of them belong to which tool.

Three kinds of findings decide who answers a question (DECISIONS round 5 a):
- **Blockers** refuse the call before any question, for the AI and the person alike. Each has a reason code
  (normalize.REASON_CODES), separate texts for the AI (`next`) and for the person (`person_action`, what they can do,
  GUI place first), and, when an AI tool clears it, that tool (`resolved_by`; the reason code is then
  missing_information, never needs_person).
- **Situation warnings** (disk tight but enough, slow hardware, a long build, a large download): an AI with a
  delegation may decide them itself; each has a rule for when to answer yes, no or ask_person.
- **Security and legal warnings** (pickle files, a base image from outside Docker's official collection, more vendor
  downloads than the soft cap, facts that could not be checked, gated models, licences): always the person. An AI
  never accepts a licence.

The kind of a code lives here only, never in a tool (CallWarning.kind reads it). A code the catalogue does not know
counts as a security warning: unknown is never "no problem". A snapshot test of SECURITY_LEGAL turns every
reclassification into a reviewed change and bumps DELEGATION_RULES_VERSION, which outdates every delegation made
under the old rules (WP11).

The person's texts are localized (orchestrator/utils/l10n_facts.py: `warning.<code>`, `blocker.<code>`, the
`action.*` and `safer.*` keys); the AI keeps the English templates below, filled only with closed numbers, never with
external text.
"""

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Literal, Mapping, Optional, Tuple

from orchestrator.tools.spec import CallWarning

# Raised whenever SECURITY_LEGAL or a blocker's reason code changes (the snapshot test pins it): every delegation made
# under an older version is outdated and must be renewed by the person.
DELEGATION_RULES_VERSION = 1

# Starting values (design [A7]), tuned with the evals.
GB = 1000 ** 3
LARGE_DOWNLOAD_BYTES = 20 * GB
LARGE_TRANSFER_BYTES = 4 * GB
MANY_PAGES = 100          # pages a crawl may store in all, from which it says so
SIZE_CLASSES = (5, 20, 50, 200)


@dataclass(frozen=True)
class BlockerSpec:
    """A hard fact that refuses a call before any question. `reason_code` is the code the refusal carries; `also`
    lists the other reason codes a site may use for the same fact (today's offline refusals of packs and crawls say
    not_supported, a build's needs_person). `resolved_by`: a registered tool the AI may call to clear it (then the
    reason code is missing_information). `person_action`: the ui_text key of what the person can do."""
    reason_code: str
    resolved_by: Optional[str] = None
    person_action: str = ""
    also: Tuple[str, ...] = ()


@dataclass(frozen=True)
class WarningSpec:
    """A warning before a question. `kind` decides who may answer (situation: an AI with a delegation; security and
    legal: always the person). `ai_template`: the English text an AI gets, filled with the warning's numbers only.
    `ai_rule`: when an AI with a delegation answers yes, no or ask_person (situation warnings). `safer`: the ui_text
    key of a safer alternative for the person. `if_unsure`: what an AI does when it cannot tell."""
    kind: Literal["situation", "security", "legal"]
    ai_template: str
    ai_rule: str = ""
    safer: str = ""
    if_unsure: Literal["no", "ask_person"] = "ask_person"


# --- blockers (design 4.2) -------------------------------------------------------------------------------------------

_PAIR = "action.pair_device"
BLOCKERS: Dict[str, BlockerSpec] = {
    "disk_full": BlockerSpec("insufficient_resources", person_action="action.free_disk"),
    "offline_mode": BlockerSpec("needs_person", person_action="action.offline_off", also=("not_supported",)),
    "sandbox_off": BlockerSpec("needs_person", person_action="action.sandbox_on"),
    "knowledge_off": BlockerSpec("not_supported", person_action="action.knowledge_on", also=("needs_person",)),
    "target_revoked": BlockerSpec("not_supported", person_action="action.remove_target"),
    "pack_revoked": BlockerSpec("not_supported"),
    "pack_rejected": BlockerSpec("not_supported"),
    "no_convertible_weights": BlockerSpec("not_supported"),
    "not_gguf": BlockerSpec("not_supported"),
    "model_unpinned": BlockerSpec("not_supported"),
    "unsafe_filename": BlockerSpec("not_supported"),
    "size_unknown": BlockerSpec("not_supported"),
    "image_problem": BlockerSpec("not_supported", person_action="action.pin_image"),
    "gated_no_access": BlockerSpec("needs_person", person_action="action.hf_access", also=("permission_denied",)),
    "unknown_repository": BlockerSpec("unknown_id", also=("invalid_value",)),
    "pack_unknown": BlockerSpec("unknown_id"),
    "artifact_unknown": BlockerSpec("unknown_id"),
    "snapshot_unknown": BlockerSpec("unknown_id"),
    "trash_unknown": BlockerSpec("unknown_id"),
    "proposal_missing": BlockerSpec("unknown_id", also=("failed",)),
    "file_not_in_repo": BlockerSpec("invalid_value"),
    "url_not_public": BlockerSpec("invalid_value"),
    "crawl_caps": BlockerSpec("invalid_value"),
    "setting_invalid": BlockerSpec("invalid_value"),
    "index_stale": BlockerSpec("failed", person_action="action.update_index"),
    "ingest_notice_unread": BlockerSpec("needs_person", person_action="action.ingest_notice"),
    "crawl_quota": BlockerSpec("limit_reached"),
    "crawl_running": BlockerSpec("limit_reached"),
    "device_busy": BlockerSpec("limit_reached"),
    "artifact_busy": BlockerSpec("limit_reached"),
    "device_not_trusted": BlockerSpec("needs_person", person_action=_PAIR),
    "host_key_unknown": BlockerSpec("needs_person", person_action=_PAIR),
    "host_key_changed": BlockerSpec("needs_person", person_action="action.check_device"),
    "device_unreachable": BlockerSpec("hardware_unavailable", person_action="action.device_on"),
    "toolchain_missing": BlockerSpec("needs_person", person_action="action.install_tools"),
    "runtime_not_built": BlockerSpec("missing_information", resolved_by="build_local_runtime"),
    # The catalog model of a tier is fetched by build_local_runtime(model_tier=...) (the design named
    # download_hf_file; the local runtime fetches its pinned catalog models itself).
    "model_not_downloaded": BlockerSpec("missing_information", resolved_by="build_local_runtime"),
    "no_device_runtime": BlockerSpec("missing_information", resolved_by="deploy_device_runtime"),
    "no_device_model": BlockerSpec("missing_information", resolved_by="deploy_to_device"),
    # NEW: the deploy settings name no model for a board benchmark; only the person sets them.
    "device_model_unset": BlockerSpec("needs_person", person_action="action.device_settings"),
    "setting_loosens": BlockerSpec("permission_denied"),
    "no_signing_key": BlockerSpec("needs_person", person_action="action.signing_key"),
    "commit_required": BlockerSpec("needs_person", person_action="action.commit_required"),
    "trash_not_yours": BlockerSpec("needs_person", person_action="action.restore_yourself"),
    "file_outside_folders": BlockerSpec("needs_person", person_action="action.add_folder"),
    "transfer_unsupported": BlockerSpec("not_supported"),
    "file_changed": BlockerSpec("unknown_id"),
}


# --- warnings (design 4.2) -------------------------------------------------------------------------------------------

_ASKED = "yes when the person asked for this"
WARNINGS: Dict[str, WarningSpec] = {
    # situation: an AI with a delegation may decide these itself
    "disk_tight": WarningSpec(
        "situation", "Disk: enough, but tight: {free_after_gb} GB left after this, {reserve_gb} GB must stay free.",
        _ASKED),
    "download_large": WarningSpec(
        "situation", "Large download: about {download_gb} GB from huggingface.co.",
        "yes when the person asked for this model"),
    "host_fit_tight": WarningSpec(
        "situation", "Conversion on this computer: tight ({needed_gb} GB needed, {usable_gb} GB usable; estimate).",
        _ASKED),
    "host_fit_does_not_fit": WarningSpec(
        "situation", "Conversion on this computer: does not fit ({needed_gb} GB needed, {usable_gb} GB usable): it "
                     "will probably swap or be stopped.", "ask_person", safer="safer.smaller_model"),
    "target_fit_tight": WarningSpec(
        "situation", "On the board: tight ({needed_gb} GB needed, {usable_gb} GB).",
        "yes when the person asked for this board"),
    "target_fit_does_not_fit": WarningSpec(
        "situation", "On the board: does not fit ({needed_gb} GB needed, {usable_gb} GB): the model will not run "
                     "there.", "no, unless the person said to build anyway; suggest a smaller quantization",
        safer="safer.smaller_quantization", if_unsure="no"),
    "image_build_needed": WarningSpec(
        "situation", "First build for this target: its image is built first from Docker Official Images (network).",
        _ASKED),
    "long_build": WarningSpec(
        "situation", "About {minutes} minutes (median of {n} earlier builds of this kind).", _ASKED),
    "embedding_model_download": WarningSpec(
        "situation", "The embedding model (about {size_gb} GB) is downloaded once.",
        "yes when the person asked for Deep Ingest"),
    "many_pages": WarningSpec(
        "situation", "Up to {max_pages} pages from {sites} sites.", "yes when the person named these sites"),
    "device_runtime_replaced": WarningSpec(
        "situation", "Replaces the runtime on the board; the previous one stays as fallback.",
        "yes when the person asked for this board"),
    "device_model_replaced": WarningSpec(
        "situation", "Replaces the model on the board.", _ASKED),
    "untested_module": WarningSpec(
        "situation", "Nobody has run this module on the board yet; its builds say so.", "yes"),
    "large_transfer": WarningSpec(
        "situation", "Copies about {size_gb} GB to the other computer.", _ASKED),
    "slow_link": WarningSpec(
        "situation", "The connection to the other computer moved {mbps} MB/s last time: about {minutes} minutes.",
        _ASKED),
    # security: always the person
    "pickle_weights": WarningSpec(
        "security", "The weights are PyTorch pickle files (.bin/.pt): loading them can run code.",
        safer="safer.safetensors"),
    "base_image_unofficial": WarningSpec(
        "security", "A base image comes from another registry ({registry}): a prebuilt image, not checked; its image "
                    "build runs with network.", safer="safer.official_image"),
    "base_image_unpinned": WarningSpec("security", "A base image is not pinned by digest."),
    "vendor_soft_cap_exceeded": WarningSpec(
        "security",
        "{what}: {count} entries, more than the usual {cap} (soft limit; only the person can override it)."),
    "target_unreviewed": WarningSpec(
        "security", "The target's scripts were not reviewed (imported from a file or transferred from another "
                    "computer)."),
    "target_pack_community": WarningSpec(
        "security", "Community target pack: scripts reviewed, not reproduced independently."),
    "first_build_unconfirmed": WarningSpec(
        "security", "First build of a target, recipe or module an AI created or installed: its build steps run with "
                    "network; the person sees it once."),
    "preflight_error": WarningSpec(
        "security", "The pre-flight check found an error ({check}); building anyway overrides it."),
    "untrusted_model_native": WarningSpec(
        "security", "Runs a model that is not a catalog model natively, with the user's rights, or in the Docker "
                    "sandbox the person has not allowed for AIs.",
        safer="safer.sandbox"),
    "signs_as_you": WarningSpec("security", "The pack is signed with the person's community key."),
    "facts_unknown": WarningSpec(
        "security", "Could not be checked: {unknown} (for example Hugging Face did not answer); licence, gating or "
                    "file format may be unsafe, so only the person decides."),
    "artifact_from_other_computer": WarningSpec(
        "security", "This model was built on another computer; the person confirms it once before it goes onto a "
                    "board."),
    # legal: always the person; an AI never accepts a licence
    "gated_model": WarningSpec(
        "legal", "Gated model: its conditions were accepted on huggingface.co with the person's account."),
    "base_model_gated": WarningSpec("legal", "Its base model is gated."),
    "license_restrictive": WarningSpec("legal", "The licence restricts use (see 'Licence', repository text)."),
    "license_unknown": WarningSpec("legal", "The licence is not on the permissive list."),
    "vendor_licence": WarningSpec(
        "legal", "Installing the vendor tools means accepting the vendor's licence."),
}

SECURITY_LEGAL: FrozenSet[str] = frozenset(c for c, w in WARNINGS.items() if w.kind != "situation")

# Names a warning's number may not have: the person's text is filled by ui_text(key, lang, **numbers).
RESERVED_NUMBERS = frozenset({"code", "key", "lang", "values"})

# What facts_unknown names (a closed list: the AI text never carries free words).
UNKNOWN_FACTS = ("huggingface", "licence", "base_model", "weights_format")


# --- numbers per tool (design 4.4): (stable keys, volatile keys) ----------------------------------------------------

_BUILD_NUMBERS = (frozenset({"size_class", "host_fit", "target_fit", "parameters"}),
                  frozenset({"download_bytes", "cached_bytes", "needed_bytes", "free_after_bytes", "reserve_bytes",
                             "host_needed_bytes", "host_usable_bytes", "target_needed_bytes", "target_usable_bytes",
                             "est_minutes"}))
NUMBER_KEYS: Dict[str, Tuple[FrozenSet[str], FrozenSet[str]]] = {
    "start_build": _BUILD_NUMBERS,
    "start_recipe_build": _BUILD_NUMBERS,
    "download_hf_file": (frozenset({"size_class"}), frozenset({"size_bytes", "free_after_bytes", "reserve_bytes"})),
    "crawl_docs_site": (frozenset({"start_urls", "max_pages", "max_depth"}), frozenset()),
    "transfer_object": (frozenset({"size_class", "method"}), frozenset({"size_bytes", "free_after_bytes", "mbps"})),
    "add_pack_from_index": (frozenset({"size_class"}), frozenset({"size_bytes", "free_after_bytes", "reserve_bytes"})),
    "create_deploy_package": (frozenset({"size_class"}),
                              frozenset({"needed_bytes", "free_after_bytes", "reserve_bytes"})),
    "build_local_runtime": (frozenset({"builds"}), frozenset({"needed_bytes", "free_after_bytes"})),
    "update_settings": (frozenset({"changes"}), frozenset()),
}


# --- the per-tool classification (design 4.3) -----------------------------------------------------------------------

@dataclass(frozen=True)
class ToolFacts:
    """One row of the per-tool classification: the blockers, situation warnings and security or legal warnings a
    tool's look-up may raise, whether a delegation may cover it (a group, "never", "not_needed" for calls that never
    ask, "own_work" for stopping one's own work) and what undo the chat offers. "Delegable" always means: only without
    a security or legal warning and with a live delegation of that group (design 3.2 step 6)."""
    blockers: FrozenSet[str] = frozenset()
    situation: FrozenSet[str] = frozenset()
    security: FrozenSet[str] = frozenset()
    delegable: str = "never"
    undo: str = ""
    deletes: bool = False          # its facts can carry a deletion plan (the person only, design 3.10)

    def codes(self) -> FrozenSet[str]:
        return self.blockers | self.situation | self.security


_FS = frozenset
_DEVICE = _FS({"device_not_trusted", "device_unreachable", "host_key_unknown", "host_key_changed", "device_busy"})
_BUILD_BLOCKERS = _FS({"target_revoked", "sandbox_off", "offline_mode", "gated_no_access", "unknown_repository",
                       "no_convertible_weights", "disk_full", "commit_required"})
_BUILD_SITUATION = _FS({"disk_tight", "download_large", "host_fit_tight", "host_fit_does_not_fit", "target_fit_tight",
                        "target_fit_does_not_fit", "image_build_needed", "long_build"})
_BUILD_SECURITY = _FS({"pickle_weights", "base_image_unofficial", "base_image_unpinned", "target_unreviewed",
                       "first_build_unconfirmed", "preflight_error", "facts_unknown", "gated_model", "base_model_gated",
                       "license_restrictive", "license_unknown"})
TOOL_FACTS: Dict[str, ToolFacts] = {
    "start_build": ToolFacts(_BUILD_BLOCKERS, _BUILD_SITUATION, _BUILD_SECURITY, "builds",
                             "running: stop the build; done: move the result to the trash"),
    "start_recipe_build": ToolFacts(_BUILD_BLOCKERS, _BUILD_SITUATION, _BUILD_SECURITY, "builds",
                                    "as start_build"),
    "download_hf_file": ToolFacts(
        _FS({"gated_no_access", "file_not_in_repo", "unsafe_filename", "size_unknown", "disk_full", "offline_mode",
             "commit_required", "unknown_repository"}),
        _FS({"disk_tight", "download_large"}),
        _FS({"pickle_weights", "facts_unknown", "gated_model", "base_model_gated", "license_restrictive",
             "license_unknown"}), "downloads", "running: stop; done: move the file to the trash"),
    "add_pack_from_index": ToolFacts(
        _FS({"offline_mode", "index_stale", "pack_unknown", "pack_revoked", "pack_rejected", "disk_full"}),
        _FS({"disk_tight"}),
        _FS({"target_pack_community", "base_image_unofficial", "base_image_unpinned", "vendor_soft_cap_exceeded"}),
        "downloads", "remove the pack (deletion dialog)"),
    "export_pack": ToolFacts(_FS({"no_signing_key"}), _FS(), _FS({"signs_as_you"}), "never",
                             "delete the exported file (deletion dialog)"),
    "crawl_docs_site": ToolFacts(
        _FS({"ingest_notice_unread", "offline_mode", "knowledge_off", "url_not_public", "crawl_caps", "crawl_quota",
             "crawl_running"}),
        # no warning counts knowledge snapshots: they have no limit and none is deleted by itself (sixth round)
        _FS({"embedding_model_download", "many_pages"}), _FS(), "knowledge",
        "running: stop; done: restore the snapshot taken before (backup first)"),
    "restore_knowledge_snapshot": ToolFacts(_FS({"snapshot_unknown", "knowledge_off", "disk_full"}), _FS(), _FS(),
                                            "knowledge", "restore the backup snapshot"),
    "create_deploy_package": ToolFacts(_FS({"disk_full", "image_problem", "artifact_unknown"}), _FS({"disk_tight"}),
                                       _FS({"artifact_from_other_computer"}), "builds",
                                       "delete the package (deletion dialog)"),
    "deploy_to_device": ToolFacts(_DEVICE, _FS({"device_model_replaced"}), _FS({"artifact_from_other_computer"}),
                                  "devices", "cannot: the files are on the board; remove them there"),
    "probe_local_hardware": ToolFacts(delegable="hardware", undo="nothing to undo"),
    "probe_remote_device": ToolFacts(_DEVICE, delegable="devices", undo="nothing to undo"),
    "create_target_module": ToolFacts(_FS(), _FS({"untested_module"}),
                                      _FS({"base_image_unofficial", "base_image_unpinned",
                                           "vendor_soft_cap_exceeded"}), "hardware",
                                      "move the module to the trash"),
    "propose_healing_fix": ToolFacts(delegable="builds"),
    # no healing strategy deletes anything: a full Docker disk (docker_prune) is a manual step that points to the
    # person's list in 'llm-cli doctor --fix --tier 2' (framework_tools.DESTRUCTIVE_STRATEGIES is empty)
    "resolve_healing": ToolFacts(_FS({"proposal_missing"}), delegable="builds",
                                 undo="config_adjustment: set the setting back"),
    "cancel_build": ToolFacts(delegable="own_work"),
    "cancel_job": ToolFacts(delegable="own_work"),
    "update_settings": ToolFacts(_FS({"setting_loosens", "setting_invalid"}), delegable="settings",
                                 undo="set the old value back"),
    "delete_artifact": ToolFacts(_FS({"artifact_unknown", "artifact_busy"}), deletes=True, delegable="never",
                                 undo="restore from the trash"),
    "restore_from_trash": ToolFacts(_FS({"trash_unknown", "trash_not_yours"}), delegable="trash",
                                    undo="move it back (deletion dialog)"),
    "build_local_runtime": ToolFacts(_FS({"toolchain_missing", "offline_mode", "disk_full", "model_unpinned"}),
                                     _FS({"long_build", "download_large"}), _FS(), "runtime"),
    "start_local_runtime": ToolFacts(_FS({"runtime_not_built", "model_not_downloaded"}), delegable="runtime",
                                     undo="stop it again"),
    "stop_local_runtime": ToolFacts(delegable="runtime", undo="start it again"),
    "run_benchmark": ToolFacts(_FS({"not_gguf", "artifact_unknown", "model_not_downloaded"}), _FS(),
                               _FS({"untrusted_model_native", "artifact_from_other_computer"}), "runtime"),
    "deploy_device_runtime": ToolFacts(_DEVICE | {"offline_mode"}, _FS({"device_runtime_replaced"}), _FS(),
                                       "devices", "switch back to the previous runtime"),
    "run_device_benchmark": ToolFacts(_DEVICE | {"no_device_runtime", "no_device_model", "device_model_unset"}, _FS(),
                                      _FS({"untrusted_model_native", "artifact_from_other_computer"}), "devices"),
    "transfer_object": ToolFacts(_FS({"disk_full", "transfer_unsupported", "file_changed", "file_outside_folders"}),
                                 _FS({"disk_tight", "large_transfer", "slow_link"}),
                                 _FS({"pickle_weights", "target_unreviewed", "base_image_unofficial",
                                      "vendor_soft_cap_exceeded", "facts_unknown", "gated_model"}), "transfers",
                                 "a fetched model: move it to the trash"),
    "answer_approval": ToolFacts(delegable="never"),
    # Only harmless repairs, every call asked. Extension point M3a/M6 (with WP11): the blockers repair_manual,
    # repair_needs_person and wrong_account, and the delegation group "repairs" for harmless repairs only.
    "run_mojo_miracle": ToolFacts(delegable="never", undo="set the folder's mode or the active build back"),
}


# --- helpers for the tools' look-ups ---------------------------------------------------------------------------------

def warn(code: str, **numbers: Any) -> CallWarning:
    """A warning of the catalogue with its numbers (closed values only). A code the catalogue does not know is a
    programming error: it is refused here, so it can never slip through as a warning of no kind."""
    if code not in WARNINGS:
        raise ValueError(f"unknown warning code {code!r}: add it to orchestrator/tools/facts_catalog.py first")
    clash = sorted(set(numbers) & RESERVED_NUMBERS)
    if clash:  # they would collide with the text lookup's own parameters (ui_text(key, lang, **values))
        raise ValueError(f"warning {code}: {', '.join(clash)} cannot be a number's name")
    return CallWarning(code, dict(numbers))


def gb_number(value: Optional[float]) -> float:
    """Bytes as decimal gigabytes with one decimal, for a warning's numbers."""
    return round(float(value or 0) / GB, 1)


def size_class(size_bytes: Optional[int]) -> str:
    """The smallest size class that holds `size_bytes` ("5gb" ... "200gb", "any" above, "unknown" without a size): a
    stable number for the decision digest, so a cache that grows never makes a question new."""
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
        return "unknown"
    for limit in SIZE_CLASSES:
        if size_bytes <= limit * GB:
            return f"{limit}gb"
    return "any"


def person_action_text(code: str, lang: Optional[str] = None) -> str:
    """What the person can do about a blocker, in their language ("" when nothing but waiting helps)."""
    spec = BLOCKERS.get(code)
    if spec is None or not spec.person_action:
        return ""
    from orchestrator.utils.localization import ui_text
    text = ui_text(spec.person_action, lang)
    return "" if text == spec.person_action else text


def blocked(code: str, message: str, next: str = "", *, reason: str = "", field: str = "", quoted: str = "",
            details: Optional[Mapping[str, Any]] = None):
    """The refusal of a blocker (a ToolError): the catalogue's reason code (or one of its `also` codes for a site that
    has always said so), the blocker code, the AI's next step and the person's action (English framework text; the
    chat localizes it from the code). A resolving tool makes the next step name it."""
    from orchestrator.tools.registry import ToolError
    spec = BLOCKERS.get(code)
    if spec is None:
        raise ValueError(f"unknown blocker code {code!r}: add it to orchestrator/tools/facts_catalog.py first")
    if reason and reason != spec.reason_code and reason not in spec.also:
        raise ValueError(f"blocker {code} is refused with {spec.reason_code}, not {reason}")
    if spec.resolved_by and spec.resolved_by not in next:
        next = (next + " " if next else "") + f"Call {spec.resolved_by} first."
    error = ToolError(message, code=reason or spec.reason_code, next=next, field=field, quoted=quoted,
                      blocker=code, person_action=person_action_text(code, "en"),
                      details=dict(details) if details is not None else None)
    return error


def with_blocker(error: Any, code: str) -> Any:
    """An existing refusal marked as the blocker `code` (its message and next step stay; the person's action is added).
    Returns the error."""
    if code not in BLOCKERS:
        raise ValueError(f"unknown blocker code {code!r}")
    error.blocker = code
    error.person_action = person_action_text(code, "en")
    return error


def warnings_of(facts: Any) -> Tuple[List[CallWarning], List[CallWarning]]:
    """(situation warnings, security and legal warnings) of a call's facts."""
    situation, person = [], []
    for w in getattr(facts, "warnings", ()) or ():
        (situation if w.kind == "situation" else person).append(w)
    return situation, person


def facts_unknown(*what: str) -> CallWarning:
    """The security warning for facts a security or legal check needs and could not get (design 4.6): `what` from
    UNKNOWN_FACTS (Hugging Face did not answer, no licence, unknown weight format, base model unknown)."""
    names = [w for w in UNKNOWN_FACTS if w in what]
    if not names or len(names) != len(set(what)):
        raise ValueError(f"facts_unknown names one or more of {', '.join(UNKNOWN_FACTS)}")
    if "huggingface" in names:  # the Hub did not answer: its licence and base model are unknown with it
        names = [w for w in names if w not in ("licence", "base_model")]
    return warn("facts_unknown", unknown="+".join(names))


# Closed words in a warning's numbers and the text keys the person reads instead (orchestrator/utils/l10n_facts.py):
# the AI keeps the word ("base_model+licence", "pip_packages"), the person never sees a code.
PERSON_WORDS: Dict[str, Tuple[str, str]] = {"facts_unknown": ("unknown", "unknown."),
                                            "vendor_soft_cap_exceeded": ("what", "vendor_list.")}


def person_words(code: str, numbers: Mapping[str, Any], lang: Optional[str] = None) -> Dict[str, str]:
    """The person's words for the closed words of a warning's numbers (CallWarning.person_text uses them): each word
    of a '+'-joined value by its localized text, a word without one as "something else"; {} for every other code."""
    entry = PERSON_WORDS.get(code)
    value = numbers.get(entry[0]) if entry is not None else None
    if not isinstance(value, str) or not value:
        return {}
    from orchestrator.utils.localization import ui_text
    name, prefix = entry
    words = []
    for word in value.split("+"):
        text = ui_text(prefix + word, lang)
        words.append(text if text != prefix + word else ui_text(prefix + "other", lang))
    return {name: ", ".join(dict.fromkeys(words))}


__all__ = ["BLOCKERS", "BlockerSpec", "DELEGATION_RULES_VERSION", "LARGE_DOWNLOAD_BYTES", "LARGE_TRANSFER_BYTES",
           "MANY_PAGES", "NUMBER_KEYS", "PERSON_WORDS", "SECURITY_LEGAL", "TOOL_FACTS", "ToolFacts", "UNKNOWN_FACTS",
           "WARNINGS", "WarningSpec", "blocked", "facts_unknown", "gb_number", "person_action_text", "person_words",
           "size_class", "warn", "warnings_of", "with_blocker"]
