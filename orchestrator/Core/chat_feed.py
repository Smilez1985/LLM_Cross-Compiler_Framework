#!/usr/bin/env python3
"""
The chat feed (delegation design 5.3): one store for what arrives in the chat panel - the person's messages, Ditto's
answers, notices, questions that wait, decisions an AI made with its reason, results of work an AI started and work
it could not do. It is the only store of an AI's recorded reason; the audit log keeps only its hash.

`chat_feed.sqlite` lives in the per-user folder (mode 0600, the folder 0700). Every framework process (the GUI, the
CLI, each MCP server) appends to it in a `BEGIN IMMEDIATE` transaction, so appends from many processes never mix.
Each row is signed on its own (`signing.sign_row`, row kind "chat_feed") with the key the tool permissions use; a row
that does not verify - changed outside the framework, written with another key, unsigned - is never shown, and the
reader is told how many were skipped. The pending-approvals store stays the authority for a question's live state
and the audit log the authority for what happened: feed rows only link to them.

What is stored is decided here, not by the producers: every string is redacted (known secret formats), AI text is
also cleaned of invisible characters, and an entry has no field for a tool's output or a call's raw arguments (and a
producer cannot add one). `facts_ai` is the only part of a row a model-bound context may ever use; `facts_person`
may hold what only the person sees.

Reading never blocks the GUI: reads take a short timeout and report a busy feed instead of waiting. A write the
caller depends on (`required=True`, an AI's decision) raises when it cannot be written - nothing may run then -
while every other write fails open.

Clearing the chat deletes the conversation rows only after the person accepted exactly the plan that lists them
(full path, file name, size, what they are), audits it first and overwrites the deleted text; the records of what AIs
did are hidden from the panel, not deleted. Nothing in this module deletes rows by itself: removing rows past a
retention period waits for the maintainer's decision on the framework's own bookkeeping (design question Q6).
"""

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from orchestrator.Core.job_service import JOB_ID_RE
from orchestrator.tools.approvals import APPROVAL_ID_RE
from orchestrator.tools.delegation import DELEGATION_ID_RE   # the delegation package owns the id (design 2.4 row 5)
from orchestrator.tools.grants import GRANT_ID_RE
from orchestrator.tools.signing import SignatureError, sign_row, verify_row
from orchestrator.tools.spec import WARNING_CODE_RE, CallWarning, WarningKind, number_problems
from orchestrator.tools.tool_windows import MAX_STEPS, STEP_ID_RE, WINDOW_ID_RE   # the window and step vocabulary
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger
from orchestrator.utils.untrusted import sanitize_untrusted

logger = get_logger("ChatFeed")

FILE_NAME = "chat_feed.sqlite"
ENTRY_ID_RE = r"^fe_[0-9a-f]{16}$"
MESSAGE_ID_RE = r"^msg_[0-9a-f]{16}$"
REQUEST_ID_RE = r"^req_[0-9a-f]{8}$"
CALLER_RE = r"^(?:ditto|mcp|mcp:[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?)?$"   # "" for the person and the framework

FEED_KINDS = ("person_message", "ditto_answer", "ditto_tool_note", "notice", "approval", "approval_end",
              "delegated_decision", "grant_used", "job_result", "blocked", "explanation", "person_action",
              "relay_question", "client_reply", "feed_cleared", "ai_action")
FeedKind = Literal["person_message", "ditto_answer", "ditto_tool_note", "notice", "approval", "approval_end",
                   "delegated_decision", "grant_used", "job_result", "blocked", "explanation", "person_action",
                   "relay_question", "client_reply", "feed_cleared", "ai_action"]
# "Clear chat" deletes the conversation; the records of what AIs did (and what the person did about them) are only
# hidden from the panel and stay readable in the approvals window and on the command line.
CONVERSATION_KINDS = frozenset({"person_message", "ditto_answer", "ditto_tool_note", "notice", "explanation",
                                "relay_question", "client_reply", "feed_cleared"})
RECORD_KINDS = frozenset({"approval", "approval_end", "delegated_decision", "grant_used", "job_result", "blocked",
                          "person_action", "ai_action"})
# The rows the AI activity producer writes (Core/activity.py): what Ditto or an AI app started, changed, ended, used,
# asked or could not do. ai_action: an AI started or changed something (a call that changed something ended ok).
ACTIVITY_KINDS = ("ai_action", "job_result", "grant_used", "approval", "approval_end", "blocked")
PROGRAMS = ("gui", "cli", "mcp", "ditto")

MAX_PARAMS = 20
MAX_PARAM_CHARS = 400
MAX_FACTS = 40
MAX_AI_TEXT = 32_000
MAX_PERSON_TEXT = 8_000
MAX_AUDIT_IDS = 8
# Parameter names that would carry what never belongs in the feed: a tool's output or a call's raw arguments.
FORBIDDEN_PARAMS = frozenset({"arguments", "args", "raw_arguments", "input", "output", "result", "results",
                              "stdout", "stderr", "log", "logs", "raw", "content", "structured_content"})

READ_TIMEOUT = 2.0     # seconds a read waits for another process's write lock (the panel's watcher passes 0.2)
WRITE_TIMEOUT = 5.0
VERIFY_LIMIT = 5_000   # rows the doctor verifies (the newest)

# Texts the feed renders itself (EN and DE; the chat panel's own keys follow in its l10n module).
TEXTS: Dict[str, Dict[str, str]] = {
    "en": {
        "chat.feed.unverified": "{n} chat entries could not be verified (changed outside the framework, or written "
                                "with another key); they are not shown.",
        "chat.feed.unknown": "{n} chat entries were written by a newer version of the framework; update this "
                             "program to see them.",
        "chat.feed.busy": "The chat is being written by another part of the framework; new messages appear in a "
                          "moment.",
        "chat.feed.disk_full": "The chat could not be saved (disk full); {who} asks you instead of deciding.",
        "chat.feed.cleared": "You cleared the chat ({rows} messages). What AI apps did stays in the approvals window.",
        "chat.clear.what": "chat messages: your messages, Ditto's answers and tool notes, notices, explanations, "
                           "questions to AI apps and their replies",
        "chat.history.line": "{text}",
        "chat.explain.framework": "The framework's record (no AI involved)",
        "chat.explain.who": "Decided by: {who}",
        "chat.explain.when": "When: {when}",
        "chat.explain.what": "What: {what}",
        "chat.explain.answer": "Answer: {answer}",
        "chat.explain.weighed": "Warnings it weighed for you: {warnings}",
        "chat.explain.left": "Left for you: {warnings}",
        "chat.explain.facts": "What the framework had checked:",
        "chat.explain.external": "{label}: {value} (from {source}, not checked by the framework)",
        "chat.answer.yes": "yes, do it",
        "chat.answer.no": "no, not after all",
        "chat.answer.ask_person": "left for you to decide",
        "chat.relay.no_ditto": "Ditto is not set up. Your question waits until {who} connects again. Until then, "
                               "this is what it wrote when it decided:",
        "chat.ai_text.caption": "In its own words (AI text, not checked by the framework)",
        "caller.ditto": "Ditto",
        "caller.shared": "an AI app on this computer (shared connection)",
        "caller.token": "the AI app with your connection '{name}'",
        "caller.self_name": "calls itself '{client}'",
        "caller.framework": "the framework",
        # What Ditto or an AI app did (Core/activity.py): the row's sender is named by the bubble's header, so a text
        # starts with what it did. Parameters are closed framework values (setting keys, labels, counts).
        "activity.action": "Changed something.",
        "activity.tool.start_build": "Started a build.",
        "activity.tool.start_recipe_build": "Started a build from a recipe.",
        "activity.tool.cancel_build": "Cancelled a build.",
        "activity.tool.resolve_healing": "Decided on a fix proposal for a build.",
        "activity.tool.propose_healing_fix": "Proposed a fix for a failed build; you decide whether to use it.",
        "activity.tool.cancel_job": "Stopped a running task.",
        "activity.tool.download_hf_file": "Started a download from Hugging Face.",
        "activity.tool.crawl_docs_site": "Started reading a documentation website into the knowledge base (Deep "
                                         "Ingest).",
        "activity.tool.index_framework_docs": "Started adding the framework's documentation to the knowledge base.",
        "activity.tool.add_pack_from_index": "Started installing a pack from the Community Hub.",
        "activity.tool.export_pack": "Started exporting a pack to a file.",
        "activity.tool.restore_knowledge_snapshot": "Started restoring a knowledge snapshot; the current state is "
                                                    "saved first.",
        "activity.tool.delete_artifact": "Moved a finished model to the trash.",
        "activity.tool.restore_from_trash": "Put something back from the trash.",
        "activity.tool.build_local_runtime": "Started building Ditto's local model program.",
        "activity.tool.start_local_runtime": "Started Ditto's local model program.",
        "activity.tool.stop_local_runtime": "Stopped Ditto's local model program.",
        "activity.tool.run_benchmark": "Started a speed test of a local model.",
        "activity.tool.probe_remote_device": "Started checking a board's hardware.",
        "activity.tool.deploy_device_runtime": "Started installing the model program on a board.",
        "activity.tool.run_device_benchmark": "Started a speed test on a board.",
        "activity.tool.create_deploy_package": "Started creating a deployment package.",
        "activity.tool.deploy_to_device": "Started deploying a model to a board.",
        "activity.tool.probe_local_hardware": "Checked this computer's hardware.",
        "activity.tool.create_target_module": "Created a new target.",
        "activity.tool.run_mojo_miracle": "Repaired something with Mojo Miracle.",
        "activity.tool.update_settings": "Changed settings: {keys}.",
        "activity.tool.gui": "Filled in fields in an open window.",
        "activity.job.done": "Work it started has finished.",
        "activity.job.not_done": "Work it started did not finish.",
        "activity.job.crawl.done": "Deep Ingest finished: the website is in the knowledge base.",
        "activity.job.crawl.not_done": "Deep Ingest did not finish.",
        "activity.job.deploy.done": "The deployment to the board finished.",
        "activity.job.deploy.not_done": "The deployment to the board did not finish.",
        "activity.job.package.done": "The deployment package is ready.",
        "activity.job.package.not_done": "The deployment package was not created.",
        "activity.job.probe.done": "The hardware check finished.",
        "activity.job.probe.not_done": "The hardware check did not finish.",
        "activity.job.pack_import.done": "The pack is installed.",
        "activity.job.pack_import.not_done": "The pack was not installed.",
        "activity.job.pack_export.done": "The pack was exported to a file.",
        "activity.job.pack_export.not_done": "The pack was not exported.",
        "activity.job.snapshot_restore.done": "The knowledge snapshot is restored.",
        "activity.job.snapshot_restore.not_done": "The knowledge snapshot was not restored.",
        "activity.job.hf_download.done": "The download from Hugging Face finished.",
        "activity.job.hf_download.not_done": "The download from Hugging Face did not finish.",
        "activity.job.runtime_build.done": "Ditto's local model program is built.",
        "activity.job.runtime_build.not_done": "Building Ditto's local model program did not finish.",
        "activity.job.runtime_start.done": "Ditto's local model program is running.",
        "activity.job.runtime_start.not_done": "Ditto's local model program did not start.",
        "activity.job.benchmark.done": "The speed test finished.",
        "activity.job.benchmark.not_done": "The speed test did not finish.",
        "activity.job.docs_index.done": "The framework's documentation is in the knowledge base.",
        "activity.job.docs_index.not_done": "Adding the framework's documentation did not finish.",
        "activity.build.completed": "The build finished.",
        "activity.build.saved": "The build finished and was saved as {saved}.",
        "activity.build.partial": "The build finished with errors: {completed} done, {failed} failed.",
        "activity.build.error": "The build failed.",
        "activity.build.cancelled": "The build was cancelled.",
        "activity.grant_used": "Ran a call under your standing approval, without asking you.",
        "activity.approval": "Asks you to approve a call.",
        "activity.approval_end": "A question for your approval has ended.",
        "activity.approval_end.done": "You approved it, and it ran.",
        "activity.approval_end.declined": "You declined it; nothing ran.",
        "activity.approval_end.expired": "Nobody answered in time; nothing ran.",
        "activity.approval_end.withdrawn": "The question was withdrawn; nothing ran.",
        "activity.approval_end.failed": "It did not work.",
        "activity.blocked": "Could not do something.",
        "activity.blocked.missing_information": "Could not do it: information is missing.",
        "activity.blocked.not_supported": "Could not do it: the framework does not support this.",
        "activity.blocked.no_tool": "Could not do it: there is no tool for this; only you can do it.",
        "activity.blocked.permission_denied": "Could not do it: your tool permissions do not allow it.",
        "activity.blocked.declined": "Did not do it: you declined it.",
        "activity.blocked.needs_person": "Could not do it: you need to decide or act.",
        "activity.blocked.hardware_unavailable": "Could not do it: the hardware is not available.",
        "activity.blocked.insufficient_resources": "Could not do it: not enough disk space or memory.",
        "activity.blocked.limit_reached": "Could not do it: a limit was reached.",
        "activity.blocked.failed": "Could not do it: it failed.",
    },
    "de": {
        "chat.feed.unverified": "{n} Chat-Einträge konnten nicht geprüft werden (außerhalb des Frameworks geändert "
                                "oder mit einem anderen Schlüssel geschrieben); sie werden nicht angezeigt.",
        "chat.feed.unknown": "{n} Chat-Einträge stammen von einer neueren Version des Frameworks; aktualisiere "
                             "dieses Programm, um sie zu sehen.",
        "chat.feed.busy": "Ein anderer Teil des Frameworks schreibt gerade in den Chat; neue Nachrichten erscheinen "
                          "gleich.",
        "chat.feed.disk_full": "Der Chat konnte nicht gespeichert werden (Platte voll); {who} fragt dich, statt "
                               "selbst zu entscheiden.",
        "chat.feed.cleared": "Du hast den Chat geleert ({rows} Nachrichten). Was KI-Apps getan haben, bleibt im "
                             "Freigabe-Fenster.",
        "chat.clear.what": "Chat-Nachrichten: deine Nachrichten, Dittos Antworten und Werkzeug-Hinweise, Hinweise, "
                           "Erklärungen, Fragen an KI-Apps und ihre Antworten",
        "chat.history.line": "{text}",
        "chat.explain.framework": "Der Eintrag des Frameworks (ohne KI)",
        "chat.explain.who": "Entschieden von: {who}",
        "chat.explain.when": "Wann: {when}",
        "chat.explain.what": "Was: {what}",
        "chat.explain.answer": "Antwort: {answer}",
        "chat.explain.weighed": "Hinweise, die es für dich abgewogen hat: {warnings}",
        "chat.explain.left": "Für dich übrig gelassen: {warnings}",
        "chat.explain.facts": "Was das Framework geprüft hatte:",
        "chat.explain.external": "{label}: {value} (aus {source}, vom Framework nicht geprüft)",
        "chat.answer.yes": "ja, mach es",
        "chat.answer.no": "nein, doch nicht",
        "chat.answer.ask_person": "dir zur Entscheidung überlassen",
        "chat.relay.no_ditto": "Ditto ist nicht eingerichtet. Deine Frage wartet, bis sich {who} wieder verbindet. "
                               "Bis dahin steht hier, was es bei der Entscheidung geschrieben hat:",
        "chat.ai_text.caption": "In eigenen Worten (KI-Text, vom Framework nicht geprüft)",
        "caller.ditto": "Ditto",
        "caller.shared": "eine KI-App auf diesem Computer (gemeinsame Verbindung)",
        "caller.token": "die KI-App mit deiner Verbindung „{name}“",
        "caller.self_name": "nennt sich „{client}“",
        "caller.framework": "das Framework",
        "activity.action": "Hat etwas geändert.",
        "activity.tool.start_build": "Hat einen Build gestartet.",
        "activity.tool.start_recipe_build": "Hat einen Build nach einem Rezept gestartet.",
        "activity.tool.cancel_build": "Hat einen Build abgebrochen.",
        "activity.tool.resolve_healing": "Hat über einen Lösungsvorschlag für einen Build entschieden.",
        "activity.tool.propose_healing_fix": "Hat eine Lösung für einen fehlgeschlagenen Build vorgeschlagen; du "
                                             "entscheidest, ob sie verwendet wird.",
        "activity.tool.cancel_job": "Hat eine laufende Aufgabe gestoppt.",
        "activity.tool.download_hf_file": "Hat einen Download von Hugging Face gestartet.",
        "activity.tool.crawl_docs_site": "Hat begonnen, eine Dokumentations-Website in die Wissensbasis einzulesen "
                                         "(Deep Ingest).",
        "activity.tool.index_framework_docs": "Hat begonnen, die Dokumentation des Frameworks in die Wissensbasis "
                                              "aufzunehmen.",
        "activity.tool.add_pack_from_index": "Hat begonnen, ein Paket aus dem Community Hub zu installieren.",
        "activity.tool.export_pack": "Hat begonnen, ein Paket in eine Datei zu exportieren.",
        "activity.tool.restore_knowledge_snapshot": "Hat begonnen, einen Wissens-Snapshot wiederherzustellen; der "
                                                    "aktuelle Stand wird vorher gesichert.",
        "activity.tool.delete_artifact": "Hat ein fertiges Modell in den Papierkorb verschoben.",
        "activity.tool.restore_from_trash": "Hat etwas aus dem Papierkorb zurückgeholt.",
        "activity.tool.build_local_runtime": "Hat begonnen, Dittos lokales Modellprogramm zu bauen.",
        "activity.tool.start_local_runtime": "Hat Dittos lokales Modellprogramm gestartet.",
        "activity.tool.stop_local_runtime": "Hat Dittos lokales Modellprogramm gestoppt.",
        "activity.tool.run_benchmark": "Hat einen Geschwindigkeitstest eines lokalen Modells gestartet.",
        "activity.tool.probe_remote_device": "Hat begonnen, die Hardware eines Boards zu prüfen.",
        "activity.tool.deploy_device_runtime": "Hat begonnen, das Modellprogramm auf einem Board zu installieren.",
        "activity.tool.run_device_benchmark": "Hat einen Geschwindigkeitstest auf einem Board gestartet.",
        "activity.tool.create_deploy_package": "Hat begonnen, ein Bereitstellungspaket zu erstellen.",
        "activity.tool.deploy_to_device": "Hat begonnen, ein Modell auf ein Board zu übertragen.",
        "activity.tool.probe_local_hardware": "Hat die Hardware dieses Computers geprüft.",
        "activity.tool.create_target_module": "Hat ein neues Target angelegt.",
        "activity.tool.run_mojo_miracle": "Hat etwas mit Mojo Miracle repariert.",
        "activity.tool.update_settings": "Hat Einstellungen geändert: {keys}.",
        "activity.tool.gui": "Hat Felder in einem offenen Fenster ausgefüllt.",
        "activity.job.done": "Eine Arbeit, die es gestartet hat, ist fertig.",
        "activity.job.not_done": "Eine Arbeit, die es gestartet hat, wurde nicht abgeschlossen.",
        "activity.job.crawl.done": "Deep Ingest ist fertig: die Website ist in der Wissensbasis.",
        "activity.job.crawl.not_done": "Deep Ingest wurde nicht abgeschlossen.",
        "activity.job.deploy.done": "Die Übertragung auf das Board ist fertig.",
        "activity.job.deploy.not_done": "Die Übertragung auf das Board wurde nicht abgeschlossen.",
        "activity.job.package.done": "Das Bereitstellungspaket ist fertig.",
        "activity.job.package.not_done": "Das Bereitstellungspaket wurde nicht erstellt.",
        "activity.job.probe.done": "Die Hardware-Prüfung ist fertig.",
        "activity.job.probe.not_done": "Die Hardware-Prüfung wurde nicht abgeschlossen.",
        "activity.job.pack_import.done": "Das Paket ist installiert.",
        "activity.job.pack_import.not_done": "Das Paket wurde nicht installiert.",
        "activity.job.pack_export.done": "Das Paket wurde in eine Datei exportiert.",
        "activity.job.pack_export.not_done": "Das Paket wurde nicht exportiert.",
        "activity.job.snapshot_restore.done": "Der Wissens-Snapshot ist wiederhergestellt.",
        "activity.job.snapshot_restore.not_done": "Der Wissens-Snapshot wurde nicht wiederhergestellt.",
        "activity.job.hf_download.done": "Der Download von Hugging Face ist fertig.",
        "activity.job.hf_download.not_done": "Der Download von Hugging Face wurde nicht abgeschlossen.",
        "activity.job.runtime_build.done": "Dittos lokales Modellprogramm ist gebaut.",
        "activity.job.runtime_build.not_done": "Der Bau von Dittos lokalem Modellprogramm wurde nicht abgeschlossen.",
        "activity.job.runtime_start.done": "Dittos lokales Modellprogramm läuft.",
        "activity.job.runtime_start.not_done": "Dittos lokales Modellprogramm ist nicht gestartet.",
        "activity.job.benchmark.done": "Der Geschwindigkeitstest ist fertig.",
        "activity.job.benchmark.not_done": "Der Geschwindigkeitstest wurde nicht abgeschlossen.",
        "activity.job.docs_index.done": "Die Dokumentation des Frameworks ist in der Wissensbasis.",
        "activity.job.docs_index.not_done": "Die Aufnahme der Framework-Dokumentation wurde nicht abgeschlossen.",
        "activity.build.completed": "Der Build ist fertig.",
        "activity.build.saved": "Der Build ist fertig und wurde als {saved} gespeichert.",
        "activity.build.partial": "Der Build ist mit Fehlern fertig: {completed} geschafft, {failed} fehlgeschlagen.",
        "activity.build.error": "Der Build ist fehlgeschlagen.",
        "activity.build.cancelled": "Der Build wurde abgebrochen.",
        "activity.grant_used": "Hat einen Aufruf mit deiner Dauerfreigabe ausgeführt, ohne dich zu fragen.",
        "activity.approval": "Bittet dich, einen Aufruf freizugeben.",
        "activity.approval_end": "Eine Frage zu deiner Freigabe ist beendet.",
        "activity.approval_end.done": "Du hast es freigegeben, und es wurde ausgeführt.",
        "activity.approval_end.declined": "Du hast es abgelehnt; nichts wurde ausgeführt.",
        "activity.approval_end.expired": "Niemand hat rechtzeitig geantwortet; nichts wurde ausgeführt.",
        "activity.approval_end.withdrawn": "Die Frage wurde zurückgezogen; nichts wurde ausgeführt.",
        "activity.approval_end.failed": "Es hat nicht funktioniert.",
        "activity.blocked": "Konnte etwas nicht tun.",
        "activity.blocked.missing_information": "Konnte es nicht tun: es fehlen Angaben.",
        "activity.blocked.not_supported": "Konnte es nicht tun: das Framework unterstützt das nicht.",
        "activity.blocked.no_tool": "Konnte es nicht tun: dafür gibt es kein Werkzeug; nur du kannst es tun.",
        "activity.blocked.permission_denied": "Konnte es nicht tun: deine Werkzeug-Berechtigungen erlauben es nicht.",
        "activity.blocked.declined": "Hat es nicht getan: du hast es abgelehnt.",
        "activity.blocked.needs_person": "Konnte es nicht tun: dafür musst du entscheiden oder handeln.",
        "activity.blocked.hardware_unavailable": "Konnte es nicht tun: die Hardware ist nicht verfügbar.",
        "activity.blocked.insufficient_resources": "Konnte es nicht tun: zu wenig Speicherplatz oder Arbeitsspeicher.",
        "activity.blocked.limit_reached": "Konnte es nicht tun: eine Grenze wurde erreicht.",
        "activity.blocked.failed": "Konnte es nicht tun: es ist fehlgeschlagen.",
    },
}


def feed_text(key: str, lang: str = "en", **values: Any) -> str:
    """The text of `key` in `lang` (English when missing) with its {placeholders} filled."""
    text = TEXTS.get(lang, {}).get(key) or TEXTS["en"].get(key, key)
    return text.format(**values) if values else text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_entry_id() -> str:
    return f"fe_{secrets.token_hex(8)}"


# --- records -------------------------------------------------------------------------------------------------------

class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WarningRow(_Model):
    """A warning as a feed row keeps it: the catalogue code, its kind and its numbers; the texts come from the
    catalogue at display time, in the person's language (design 4.1). Code, kind and numbers follow the shared
    vocabulary of `tools/spec.py` (CallWarning): the same code format, the same three kinds and the same closed
    numbers, never free text. `of()` records a CallWarning with the kind the catalogue gives it."""
    code: str = Field(pattern=WARNING_CODE_RE)
    kind: WarningKind
    numbers: Dict[str, Any] = Field(default_factory=dict, max_length=20)

    @field_validator("numbers")
    @classmethod
    def _closed_numbers(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        problems = number_problems(value, "warning numbers")
        if problems:
            raise ValueError("; ".join(problems))
        return value

    @classmethod
    def of(cls, warning: CallWarning) -> "WarningRow":
        """The row of a warning the framework found: the kind comes from the catalogue (an unknown code counts as a
        security warning), never from the producer."""
        return cls(code=warning.code, kind=warning.kind, numbers=dict(warning.numbers))


class FeedSender(_Model):
    """Who a row is from. Styling comes from `role` and `caller`, which the framework sets (never from text);
    `client` is the app's own claim about its name, already cleaned (short ASCII)."""
    role: Literal["person", "ditto", "client", "framework"]
    caller: str = Field(default="", pattern=CALLER_RE)
    caller_label: str = Field(default="", max_length=200)
    client: str = Field(default="", max_length=128)

    @field_validator("caller_label")
    @classmethod
    def _one_line(cls, value: str) -> str:
        if any(ch in value for ch in "\r\n"):
            raise ValueError("a caller label is one line")
        return value

    @field_validator("client")
    @classmethod
    def _cleaned(cls, value: str) -> str:
        if value and not (value.isascii() and value.isprintable()):
            raise ValueError("the client's self-reported name must be cleaned first (short printable ASCII)")
        return value

    @model_validator(mode="after")
    def _role_matches_caller(self) -> "FeedSender":
        if self.role == "person" and (self.caller or self.client):
            raise ValueError("the person has no caller id")
        if self.role == "ditto" and self.caller != "ditto":
            raise ValueError("a row from Ditto has the caller 'ditto'")
        if self.role == "client" and not self.caller.startswith("mcp"):
            raise ValueError("a row from an AI app has its MCP caller id")
        return self


class FeedFact(_Model):
    label: str = Field(max_length=200)
    value: str = Field(max_length=2000)
    source: str = Field(default="", max_length=200)   # "" = framework; otherwise where the text comes from


class FeedRefs(_Model):
    approval_id: Optional[str] = Field(None, pattern=APPROVAL_ID_RE)
    job_id: Optional[str] = Field(None, pattern=JOB_ID_RE.pattern)
    request_id: Optional[str] = Field(None, pattern=REQUEST_ID_RE)
    grant_id: Optional[str] = Field(None, pattern=GRANT_ID_RE)
    delegation_id: Optional[str] = Field(None, pattern=DELEGATION_ID_RE)
    message_id: Optional[str] = Field(None, pattern=MESSAGE_ID_RE)
    audit_ids: List[int] = Field(default_factory=list, max_length=MAX_AUDIT_IDS)
    reply_to: Optional[str] = Field(None, pattern=ENTRY_ID_RE)
    to_caller: str = Field(default="", pattern=CALLER_RE)   # relay: the deciding caller
    to_owner: str = Field(default="", max_length=120)       # relay: the deciding session while it lives
    # The window that shows what the row is about (tool_windows.WINDOW_IDS, a closed list): the GUI's [Show] opens it
    # on the person's click, nothing ever opens or runs from a row by itself.
    window: Optional[str] = Field(None, pattern=WINDOW_ID_RE)
    # Next steps by their catalogue ids ("<area>.<name>", e.g. runtime.install); the GUI renders them.
    steps: List[str] = Field(default_factory=list, max_length=MAX_STEPS)
    # "gui-chat": the person asked Ditto in this window's chat for it (the window may come up at once); "" otherwise.
    asked_in: Literal["", "gui-chat"] = ""

    @field_validator("steps")
    @classmethod
    def _step_ids(cls, value: List[str]) -> List[str]:
        bad = [step for step in value if not isinstance(step, str) or not re.fullmatch(STEP_ID_RE, step)]
        if bad:
            raise ValueError(f"steps are catalogue ids (<area>.<name>), not {bad!r}")
        return value


class FeedEntry(_Model):
    """One row of the feed. Framework text is a localized key with parameters (re-rendered when the language
    changes); AI text and the person's text are kept apart from it and from each other."""
    entry_id: str = Field(pattern=ENTRY_ID_RE)
    ts: str
    kind: FeedKind
    sender: FeedSender
    text_key: str = Field(default="", pattern=r"^(?:[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*)?$", max_length=80)
    params: Dict[str, str] = Field(default_factory=dict, max_length=MAX_PARAMS)
    param_origin: Dict[str, Literal["framework", "external"]] = Field(default_factory=dict)
    facts_person: List[FeedFact] = Field(default_factory=list, max_length=MAX_FACTS)
    facts_ai: List[FeedFact] = Field(default_factory=list, max_length=MAX_FACTS)
    ai_text: str = Field(default="", max_length=MAX_AI_TEXT + 40)
    ai_text_kind: Literal["", "answer", "reason", "note", "reply"] = ""
    person_text: str = Field(default="", max_length=MAX_PERSON_TEXT + 40)
    answer: Optional[Literal["yes", "no", "ask_person"]] = None
    warnings_decided: List[WarningRow] = Field(default_factory=list, max_length=40)
    warnings_for_person: List[WarningRow] = Field(default_factory=list, max_length=40)
    level: Literal["info", "warning"] = "info"
    refs: FeedRefs = Field(default_factory=FeedRefs)
    program: Literal["gui", "cli", "mcp", "ditto"]
    migrated: bool = False

    @field_validator("ts")
    @classmethod
    def _iso_time(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("ts is an ISO 8601 time") from None
        return value

    @field_validator("params")
    @classmethod
    def _plain_params(cls, value: Dict[str, str]) -> Dict[str, str]:
        for name, text in value.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
                raise ValueError(f"parameter name {name!r} is not a plain key")
            if name in FORBIDDEN_PARAMS:
                raise ValueError(f"'{name}' would carry a tool's output or raw arguments, which the feed never stores")
            if len(text) > MAX_PARAM_CHARS:
                raise ValueError(f"parameter '{name}' is longer than {MAX_PARAM_CHARS} characters")
            if any(ch in text for ch in "\r\n\x0b\x0c\x85\u2028\u2029"):
                # A value on its own line could pose as a line of the framework's own text (design 5.2 R2).
                raise ValueError(f"parameter '{name}' must be one line")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "FeedEntry":
        unknown = set(self.param_origin) - set(self.params)
        if unknown:
            raise ValueError(f"param_origin names unknown parameters {sorted(unknown)}")
        if bool(self.ai_text) != bool(self.ai_text_kind):
            raise ValueError("AI text and its kind go together")
        if self.ai_text and self.sender.role == "person":
            raise ValueError("the person's words are person_text, never AI text")
        if self.person_text and self.sender.role != "person":
            raise ValueError("only the person writes person_text")
        if self.answer is not None and self.kind != "delegated_decision":
            raise ValueError("only an AI's decision carries an answer")
        if self.kind == "delegated_decision" and (self.answer is None or self.sender.role not in ("ditto", "client")):
            raise ValueError("an AI's decision names its answer and comes from the AI that decided")
        if self.sender.role == "person" and self.kind not in ("person_message", "relay_question", "person_action"):
            raise ValueError(f"the person does not write {self.kind} rows")
        if self.sender.role == "person" and self.program not in ("gui", "cli"):
            # The person has no caller id: a row in the person's name comes only from the person's own windows,
            # never from a process that serves an AI or from Ditto.
            raise ValueError("the person writes only in the GUI or at the terminal")
        if self.refs.asked_in and (self.sender.caller != "ditto" or self.program != "gui"):
            # Only Ditto, asked in the GUI's own chat, works for a turn the person typed into this window.
            raise ValueError("only Ditto's rows written by the GUI are asked in its chat")
        return self

    def canonical(self) -> str:
        """The signed text: canonical JSON (keys sorted, no spaces)."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False)


@dataclass(frozen=True)
class StoredEntry:
    row_id: int          # the store's cursor (grows with every append, across processes)
    entry: FeedEntry


@dataclass
class FeedPage:
    """What a read returned. `unverified`: rows skipped because their signature failed; `unknown`: rows with a valid
    signature in a format this version does not know (a newer framework wrote them). `busy`: another process held
    the feed longer than the read waited; nothing was read, try again. `problem`: the feed cannot be used."""
    entries: List[StoredEntry] = field(default_factory=list)
    last_id: int = 0
    unverified: int = 0
    unknown: int = 0
    more: bool = False
    busy: bool = False
    problem: Optional[str] = None

    def notices(self, lang: str = "en") -> List[str]:
        """One line per kind of skipped row (the panel shows them once), framework text only."""
        lines = []
        if self.unverified:
            lines.append(feed_text("chat.feed.unverified", lang, n=self.unverified))
        if self.unknown:
            lines.append(feed_text("chat.feed.unknown", lang, n=self.unknown))
        if self.busy:
            lines.append(feed_text("chat.feed.busy", lang))
        return lines


class ClearPlan(_Model):
    """What "Clear chat" deletes, for the person's question: the full path and name of the file the rows are in,
    how many conversation rows and how many bytes of text, and what they are. The records of what AIs did that the
    clear hides (not deletes) are counted apart. `ids_sha256` binds the exact rows, so a plan accepted earlier never
    deletes rows that came later or a different set."""
    full_path: str
    file_name: str
    up_to_id: int = Field(ge=0)
    rows: int = Field(ge=0)
    size_bytes: int = Field(ge=0)
    hidden_records: int = Field(ge=0)
    ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    what_key: str = "chat.clear.what"

    def what(self, lang: str = "en") -> str:
        return feed_text(self.what_key, lang)

    def sha256(self) -> str:
        text = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


@dataclass
class FeedCheck:
    """The doctor's view of the feed."""
    exists: bool
    writable: bool
    keyed: bool
    rows: int = 0
    checked: int = 0
    unverified: int = 0
    unknown: int = 0
    problem: Optional[str] = None


class FeedWriteError(RuntimeError):
    """A row could not be written. `reason`: disk_full, busy, unwritable, damaged, audit, plan_changed."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class ClearPlanChanged(FeedWriteError):
    """The chat changed after the person was shown the plan: nothing was deleted, the person is asked again."""

    def __init__(self):
        super().__init__("plan_changed", "The chat changed since you were asked; nothing was deleted. Look at the "
                                         "list again and confirm it.")


# --- the store -----------------------------------------------------------------------------------------------------

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id TEXT NOT NULL UNIQUE,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        body TEXT NOT NULL,
        sig TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS entries_kind ON entries (kind, id)",
    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL, sig TEXT NOT NULL)",
)
# The statements are constants (the kind lists are spelled out; a test keeps them equal to the sets above).
_SQL_AFTER = "SELECT id, entry_id, ts, kind, body, sig FROM entries WHERE id > ? ORDER BY id LIMIT ?"
_SQL_AFTER_RECORDS = ("SELECT id, entry_id, ts, kind, body, sig FROM entries WHERE id > ? AND kind IN ('ai_action', "
                      "'approval', 'approval_end', 'blocked', 'delegated_decision', 'grant_used', 'job_result', "
                      "'person_action') ORDER BY id LIMIT ?")
_SQL_PAGE = "SELECT id, entry_id, ts, kind, body, sig FROM entries WHERE id < ? AND id > ? ORDER BY id DESC LIMIT ?"
_SQL_PAGE_RECORDS = ("SELECT id, entry_id, ts, kind, body, sig FROM entries WHERE id < ? AND id > ? AND kind IN "
                     "('ai_action', 'approval', 'approval_end', 'blocked', 'delegated_decision', 'grant_used', "
                     "'job_result', 'person_action') ORDER BY id DESC LIMIT ?")
_SQL_NEWEST = "SELECT id, entry_id, ts, kind, body, sig FROM entries ORDER BY id DESC LIMIT ?"
_SQL_PLAN_ROWS = ("SELECT id, LENGTH(CAST(body AS BLOB)) FROM entries WHERE id <= ? AND kind IN ('client_reply', "
                  "'ditto_answer', 'ditto_tool_note', 'explanation', 'feed_cleared', 'notice', 'person_message', "
                  "'relay_question') ORDER BY id")
_SQL_PLAN_HIDDEN = ("SELECT COUNT(*) FROM entries WHERE id <= ? AND id > ? AND kind IN ('ai_action', 'approval', "
                    "'approval_end', 'blocked', 'delegated_decision', 'grant_used', 'job_result', 'person_action')")
_SQL_DELETE_CONVERSATION = ("DELETE FROM entries WHERE id <= ? AND kind IN ('client_reply', 'ditto_answer', "
                            "'ditto_tool_note', 'explanation', 'feed_cleared', 'notice', 'person_message', "
                            "'relay_question')")
META_CLEARED = "cleared_before"
META_IMPORTED = "chat_history_imported"


def _write_failure(e: BaseException) -> FeedWriteError:
    text = str(e).lower()
    if isinstance(e, OSError) and getattr(e, "errno", None) == 28 or "disk is full" in text or "no space" in text:
        return FeedWriteError("disk_full", "The chat could not be saved: the disk is full.")
    if "locked" in text or "busy" in text:
        return FeedWriteError("busy", "The chat is being written by another part of the framework; try again.")
    if "readonly" in text or "read-only" in text or "permission" in text or isinstance(e, PermissionError):
        return FeedWriteError("unwritable", "The chat file cannot be written (permissions).")
    if "not a database" in text or "malformed" in text:
        return FeedWriteError("damaged", "The chat file is damaged.")
    return FeedWriteError("unwritable", f"The chat could not be saved ({type(e).__name__}).")


def _reason(e: BaseException) -> str:
    """Why an operation failed, without a host path (an OSError's message names the file)."""
    if isinstance(e, OSError):
        return f"{type(e).__name__}: {e.strerror or 'error'}"
    return f"{type(e).__name__}: {e}"


def _rollback(db: sqlite3.Connection) -> None:
    try:
        if db.in_transaction:
            db.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _redacted(value: Any) -> Any:
    """Every string in a JSON value with known secret formats replaced."""
    if isinstance(value, str):
        return redact_secrets(value) if value else value
    if isinstance(value, list):
        return [_redacted(v) for v in value]
    if isinstance(value, dict):
        return {k: _redacted(v) for k, v in value.items()}
    return value


class FeedStore:
    """The feed in the per-user folder. Opened on first use, so a damaged or unreachable file never stops the
    framework from starting. `key`: the signing key (the audit key from the OS keyring, None without one);
    `refuse_unkeyed`: asked once per read, when there is no key, whether unkeyed rows must be refused (the audit log
    is kept with a key)."""

    def __init__(self, state_dir: Path, key: Optional[bytes] = None,
                 refuse_unkeyed: Optional[Callable[[], bool]] = None):
        self.path = Path(state_dir) / FILE_NAME
        self.key = key
        self.refuse_unkeyed = refuse_unkeyed
        self.problem: Optional[str] = None
        self._reader: Optional[sqlite3.Connection] = None
        self._reader_identity: Optional[Tuple[int, int]] = None
        self._reader_lock = threading.Lock()

    # --- connections ---------------------------------------------------------------------------------------------

    def _prepare_file(self) -> None:
        """The folder (0700 when it is created) and the file (0600), never through a link; files other processes
        or an earlier version left too open are tightened."""
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise OSError(f"{self.path.name} is a link; the chat feed is never kept behind one")
        if not self.path.exists():
            flags = os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            os.close(os.open(self.path, flags, 0o600))
        if os.name == "posix":
            for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
                try:
                    mode = os.lstat(path).st_mode
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(mode) and mode & 0o077:
                    os.chmod(path, 0o600)

    def _connect(self, timeout: float) -> sqlite3.Connection:
        self._prepare_file()
        db = sqlite3.connect(self.path, timeout=timeout, isolation_level=None, check_same_thread=False)
        try:
            try:
                db.execute("PRAGMA journal_mode=WAL")  # readers never wait for a writer
            except sqlite3.DatabaseError as e:
                text = str(e).lower()
                if any(word in text for word in ("not a database", "malformed", "locked", "busy")):
                    raise  # damaged, or held by another process: the caller reports it (a read never waits twice)
                # a file system without shared memory (network folders): the default journal works too
            # On every connect: the file may have been moved aside since the last one (a new feed starts).
            for statement in _SCHEMA:
                db.execute(statement)
        except BaseException:
            db.close()
            raise
        return db

    def _identity(self) -> Optional[Tuple[int, int]]:
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return st.st_dev, st.st_ino

    def _read_connection(self, timeout: float) -> sqlite3.Connection:
        """The long-lived read connection (PRAGMA data_version is per connection); opened again when the file was
        replaced. Called with _reader_lock held."""
        identity = self._identity()
        if self._reader is not None and identity != self._reader_identity:
            self._close_reader()
        if self._reader is None:
            self._reader = self._connect(timeout)
            self._reader_identity = self._identity()
        self._reader.execute(f"PRAGMA busy_timeout = {max(0, int(timeout * 1000))}")
        return self._reader

    def _close_reader(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            except sqlite3.Error:
                pass
        self._reader, self._reader_identity = None, None

    def close(self) -> None:
        with self._reader_lock:
            self._close_reader()

    def _refuse(self) -> Optional[Callable[[], bool]]:
        """refuse_unkeyed, asked once for a whole read (it may open the audit log)."""
        if self.key or self.refuse_unkeyed is None:
            return None
        try:
            refused = bool(self.refuse_unkeyed())
        except Exception:
            refused = True
        return lambda: refused

    # --- rows and meta -------------------------------------------------------------------------------------------

    def _entry(self, row: Sequence[Any], refuse: Optional[Callable[[], bool]]) -> Tuple[str, Optional[FeedEntry]]:
        """("ok", entry), ("unverified", None) or ("unknown", None) for one stored row."""
        _rid, entry_id, ts, kind, body, sig = row
        try:
            verify_row("chat_feed", f"{entry_id}\n{body}", sig, self.key, refuse)
        except (SignatureError, TypeError):
            return "unverified", None
        try:
            entry = FeedEntry.model_validate(json.loads(body))
        except (ValueError, ValidationError, RecursionError):
            return "unknown", None   # signed by the framework, in a format this version does not read
        if entry.entry_id != entry_id or entry.kind != kind or entry.ts != ts:
            return "unverified", None
        return "ok", entry

    def _meta_get(self, db: sqlite3.Connection, name: str, refuse: Optional[Callable[[], bool]]) -> Optional[str]:
        row = db.execute("SELECT v, sig FROM meta WHERE k = ?", (name,)).fetchone()
        if row is None:
            return None
        try:
            verify_row("chat_feed_meta", f"{name}\n{row[0]}", row[1], self.key, refuse)
        except (SignatureError, TypeError):
            return None
        return row[0]

    def _meta_set(self, db: sqlite3.Connection, name: str, value: str) -> None:
        db.execute("INSERT OR REPLACE INTO meta (k, v, sig) VALUES (?, ?, ?)",
                   (name, value, sign_row("chat_feed_meta", f"{name}\n{value}", self.key)))

    def _cleared_before(self, db: sqlite3.Connection, refuse: Optional[Callable[[], bool]]) -> int:
        """Where the last "Clear chat" ended: the panel shows only newer rows. A value that does not verify counts
        as no clear (the panel then shows more, never less)."""
        value = self._meta_get(db, META_CLEARED, refuse)
        return int(value) if value is not None and value.isdigit() and len(value) < 19 else 0

    def _insert(self, db: sqlite3.Connection, entry: FeedEntry) -> StoredEntry:
        """Writes one row inside the caller's transaction: every string redacted once more (the last guard: a
        secret never reaches the file, whoever built the entry), then signed over its id and canonical JSON."""
        entry = FeedEntry.model_validate(_redacted(entry.model_dump(mode="json")))
        body = entry.canonical()
        sig = sign_row("chat_feed", f"{entry.entry_id}\n{body}", self.key)
        cur = db.execute("INSERT INTO entries (entry_id, ts, kind, body, sig) VALUES (?, ?, ?, ?, ?)",
                         (entry.entry_id, entry.ts, entry.kind, body, sig))
        return StoredEntry(int(cur.lastrowid), entry)

    # --- writing -------------------------------------------------------------------------------------------------

    def append(self, entry: FeedEntry, *, timeout: Optional[float] = None) -> StoredEntry:
        """Appends one row (every string redacted, signed) and returns it as stored, with its row id;
        FeedWriteError when it cannot be written."""
        return self.append_many([entry], timeout=timeout)[0]

    def append_many(self, entries: Sequence[FeedEntry], *, timeout: Optional[float] = None,
                    once: str = "") -> List[StoredEntry]:
        """Appends rows in one transaction. `once`: a meta name; when it is set already nothing is appended (an
        import that may only happen once, whichever process is first), otherwise it is set with the rows."""
        try:
            db = self._connect(WRITE_TIMEOUT if timeout is None else timeout)
        except (sqlite3.Error, OSError) as e:
            failure = _write_failure(e)
            self.problem = str(failure)
            raise failure from None
        try:
            db.execute("BEGIN IMMEDIATE")
            if once and db.execute("SELECT 1 FROM meta WHERE k = ?", (once,)).fetchone() is not None:
                _rollback(db)
                return []
            stored = [self._insert(db, entry) for entry in entries]
            if once:
                self._meta_set(db, once, _now())
            db.execute("COMMIT")
            self.problem = None
            return stored
        except (sqlite3.Error, OSError) as e:
            _rollback(db)
            raise _write_failure(e) from None
        finally:
            db.close()

    # --- reading -------------------------------------------------------------------------------------------------

    def _page_of(self, rows: Sequence[Sequence[Any]], refuse: Optional[Callable[[], bool]]) -> FeedPage:
        page = FeedPage()
        for row in rows:
            page.last_id = max(page.last_id, int(row[0]))
            state, entry = self._entry(row, refuse)
            if entry is not None:
                page.entries.append(StoredEntry(int(row[0]), entry))
            elif state == "unknown":
                page.unknown += 1
            else:
                page.unverified += 1
        return page

    def _reading(self, read: Callable[[sqlite3.Connection, Optional[Callable[[], bool]]], FeedPage],
                 timeout: Optional[float]) -> FeedPage:
        """Runs `read` on the read connection; a feed another process holds longer than `timeout` gives a busy page,
        a feed that cannot be read a page with the problem - never an exception, never a long wait."""
        try:
            with self._reader_lock:
                db = self._read_connection(READ_TIMEOUT if timeout is None else timeout)
                page = read(db, self._refuse())
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                return FeedPage(busy=True)
            with self._reader_lock:
                self._close_reader()
            self.problem = f"The chat feed {self.path.name} cannot be read ({_reason(e)})."
            return FeedPage(problem=self.problem)
        except (sqlite3.Error, OSError) as e:
            with self._reader_lock:
                self._close_reader()
            self.problem = f"The chat feed {self.path.name} cannot be read ({_reason(e)})."
            return FeedPage(problem=self.problem)
        self.problem = None
        return page

    def after(self, last_id: int, limit: int = 200, *, timeout: Optional[float] = None,
              view: Literal["panel", "records", "all"] = "panel") -> FeedPage:
        """Rows after `last_id`, oldest first (the watcher's cursor is `last_id` of the result, which also passes
        rows that were skipped). `view`: "panel" leaves out what "Clear chat" hid, "records" returns only the records
        of what AIs did (hidden ones included), "all" everything."""
        limit = max(1, min(int(limit), 1000))
        if view not in ("panel", "records", "all"):
            raise ValueError(f"unknown view {view!r}")

        def read(db: sqlite3.Connection, refuse: Optional[Callable[[], bool]]) -> FeedPage:
            start = max(int(last_id), self._cleared_before(db, refuse) if view == "panel" else 0)
            rows = db.execute(_SQL_AFTER_RECORDS if view == "records" else _SQL_AFTER, (start, limit + 1)).fetchall()
            page = self._page_of(rows[:limit], refuse)
            page.more = len(rows) > limit
            page.last_id = max(page.last_id, start)
            return page
        return self._reading(read, timeout)

    def page(self, before_id: Optional[int] = None, limit: int = 100, *, timeout: Optional[float] = None,
             view: Literal["panel", "records", "all"] = "panel") -> FeedPage:
        """The newest `limit` rows before `before_id` (None: the newest), oldest first; `more` when older ones
        exist ("Show earlier messages")."""
        limit = max(1, min(int(limit), 1000))
        before = int(before_id) if before_id is not None else 2 ** 62
        if view not in ("panel", "records", "all"):
            raise ValueError(f"unknown view {view!r}")

        def read(db: sqlite3.Connection, refuse: Optional[Callable[[], bool]]) -> FeedPage:
            floor = self._cleared_before(db, refuse) if view == "panel" else 0
            rows = db.execute(_SQL_PAGE_RECORDS if view == "records" else _SQL_PAGE,
                              (before, floor, limit + 1)).fetchall()
            page = self._page_of(list(reversed(rows[:limit])), refuse)
            page.more = len(rows) > limit
            return page
        return self._reading(read, timeout)

    def max_id(self, *, timeout: Optional[float] = None) -> Optional[int]:
        """The newest row id (0 for an empty feed); None while the feed is busy or cannot be read."""
        try:
            with self._reader_lock:
                db = self._read_connection(READ_TIMEOUT if timeout is None else timeout)
                row = db.execute("SELECT MAX(id) FROM entries").fetchone()
        except (sqlite3.Error, OSError):
            return None
        return int(row[0] or 0)

    def data_version(self, *, timeout: float = 0.2) -> Optional[Tuple[int, int, int]]:
        """A value that changes whenever another connection (another process, or a writer of this one) committed to
        the feed, or the file was replaced: the watcher reads rows only when it changed. None while the feed is busy
        or cannot be read; never waits longer than `timeout`."""
        try:
            with self._reader_lock:
                db = self._read_connection(timeout)
                version = db.execute("PRAGMA data_version").fetchone()[0]
                identity = self._reader_identity or (0, 0)
        except (sqlite3.Error, OSError):
            return None
        return identity[0], identity[1], int(version)

    # --- clearing the chat (a person's action) -------------------------------------------------------------------

    def _plan(self, db: sqlite3.Connection, up_to_id: int) -> ClearPlan:
        rows = db.execute(_SQL_PLAN_ROWS, (int(up_to_id),)).fetchall()
        floor = self._cleared_before(db, self._refuse())
        hidden = db.execute(_SQL_PLAN_HIDDEN, (int(up_to_id), floor)).fetchone()[0]
        ids = hashlib.sha256(",".join(str(r[0]) for r in rows).encode("ascii")).hexdigest()
        return ClearPlan(full_path=str(self.path.resolve()), file_name=self.path.name, up_to_id=int(up_to_id),
                         rows=len(rows), size_bytes=sum(int(r[1] or 0) for r in rows), hidden_records=int(hidden),
                         ids_sha256=ids)

    def clear_plan(self, *, timeout: Optional[float] = None) -> ClearPlan:
        """What "Clear chat" would delete now, for the person's question (it deletes nothing)."""
        db = self._connect(READ_TIMEOUT if timeout is None else timeout)
        try:
            top = db.execute("SELECT MAX(id) FROM entries").fetchone()[0] or 0
            return self._plan(db, int(top))
        finally:
            db.close()

    def clear_conversation(self, plan: ClearPlan, actor: str, audit: Any, *,
                           timeout: Optional[float] = None) -> int:
        """Deletes exactly the conversation rows of `plan`, which the person accepted in the framework's own window
        (actor "gui" or "cli"; an AI never clears the chat), and hides the records of what AIs did from the panel.
        The clear is audited before anything is deleted (`feed_cleared`; nothing is deleted when the audit cannot
        be written), and the deleted text is overwritten in the file. Returns the number of deleted rows;
        ClearPlanChanged when the chat no longer matches the plan."""
        if actor not in ("gui", "cli"):
            raise PermissionError("Only the person clears the chat, in the GUI or at a terminal.")
        if audit is None or not callable(getattr(audit, "record", None)):
            raise FeedWriteError("audit", "The audit log is not available, so the chat is not cleared.")
        try:
            db = self._connect(WRITE_TIMEOUT if timeout is None else timeout)
        except (sqlite3.Error, OSError) as e:
            raise _write_failure(e) from None
        try:
            try:
                db.execute("PRAGMA secure_delete = ON")   # the deleted text does not stay readable in free pages
                db.execute("BEGIN IMMEDIATE")
                if self._plan(db, plan.up_to_id).sha256() != plan.sha256():
                    raise ClearPlanChanged()
                try:  # the accepted plan is bound by its hash in the audit's deletion field (design 3.11)
                    audit.record(actor, "chat_feed", "framework", "person", "feed_cleared", approved=True,
                                 arguments={"rows": plan.rows, "bytes": plan.size_bytes, "up_to": plan.up_to_id,
                                            "hidden_records": plan.hidden_records},
                                 detail=f"{plan.rows} chat rows deleted, {plan.hidden_records} records hidden",
                                 extra={"deletion_sha256": plan.sha256(), "answered_by": "person",
                                        "confirm_channel": f"framework-{actor}"})
                except Exception as e:   # the reason without a host path (an OSError's text names its file)
                    raise FeedWriteError("audit", f"The audit log cannot record the clear "
                                                  f"({redact_secrets(_reason(e))}); nothing was deleted.") from None
                deleted = db.execute(_SQL_DELETE_CONVERSATION, (plan.up_to_id,)).rowcount
                floor = self._cleared_before(db, self._refuse())
                self._meta_set(db, META_CLEARED, str(max(floor, plan.up_to_id)))
                self._insert(db, FeedEntry(entry_id=new_entry_id(), ts=_now(), kind="feed_cleared",
                                           sender=FeedSender(role="framework"), text_key="chat.feed.cleared",
                                           params={"rows": str(deleted)}, program=actor))
                db.execute("COMMIT")
            except FeedWriteError:
                _rollback(db)
                raise
            except (sqlite3.Error, OSError) as e:
                _rollback(db)
                raise _write_failure(e) from None
            try:  # copies of the deleted rows in the write-ahead log go too
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
        finally:
            db.close()
        return int(deleted)

    # --- health --------------------------------------------------------------------------------------------------

    def check(self) -> FeedCheck:
        """Whether the feed can be written and the newest rows verify (for `llm-cli doctor`); changes nothing."""
        result = FeedCheck(exists=self.path.exists(), writable=False, keyed=bool(self.key))
        if not result.exists and not self.path.is_symlink():   # nothing written yet: never created by a check
            folder = next((p for p in (self.path.parent, *self.path.parent.parents) if p.exists()), None)
            result.writable = folder is not None and folder.is_dir() and os.access(folder, os.W_OK)
            if not result.writable:
                result.problem = f"The folder for the chat feed {self.path.name} cannot be written."
            return result
        try:
            db = self._connect(READ_TIMEOUT)
        except (sqlite3.Error, OSError) as e:
            result.problem = f"The chat feed {self.path.name} cannot be opened ({_reason(e)})."
            return result
        try:
            refuse = self._refuse()
            result.rows = int(db.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
            rows = db.execute(_SQL_NEWEST, (VERIFY_LIMIT,)).fetchall()
            page = self._page_of(rows, refuse)
            result.checked, result.unverified, result.unknown = len(rows), page.unverified, page.unknown
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT OR REPLACE INTO meta (k, v, sig) VALUES ('doctor_probe', '', '')")
                result.writable = True
            except sqlite3.Error as e:
                result.problem = f"The chat feed {self.path.name} cannot be written ({_reason(e)})."
            finally:
                _rollback(db)
        except sqlite3.Error as e:
            result.problem = f"The chat feed {self.path.name} cannot be read ({_reason(e)})."
        finally:
            db.close()
        return result


# --- writing for producers -----------------------------------------------------------------------------------------

def clean_client_name(value: Any, limit: int = 64) -> str:
    """An app's own claim about its name as one short line (as the MCP server cleans it): secrets redacted first,
    control and non-ASCII characters escaped, cut without splitting an escape."""
    parts, size = [], 0
    for char in redact_secrets(str(value or "")[:16 * limit]):
        escaped = json.dumps(char, ensure_ascii=True)[1:-1]
        if size + len(escaped) > limit:
            break
        parts.append(escaped)
        size += len(escaped)
    return "".join(parts)


def _one_line(text: str, limit: int) -> str:
    """Line breaks and other separators become spaces; cut to `limit` characters."""
    flat = " ".join(re.split(r"[\r\n\t\x0b\x0c\x85\u2028\u2029]+", text)).strip()
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[... truncated ...]"


def _outside(text: str) -> str:
    """Outside text normalized and cleaned of invisible characters, whole: cutting it here, before the redaction,
    would split a key in two and leave its first part readable (secrets are redacted before anything is cut)."""
    return sanitize_untrusted(text or "", max_len=sys.maxsize)


def clean_ai_text(text: str, limit: int = MAX_AI_TEXT) -> str:
    """AI text as the feed keeps it: invisible characters removed and normalized first (so a secret spelled with
    look-alike characters is redacted too), then secrets redacted, and only then capped."""
    return _cut(redact_secrets(_outside(text)), limit)


def reason_sha256(text: str) -> str:
    """The hash of an AI's reason as the feed stores it (cleaned and redacted first), which the audit records."""
    return hashlib.sha256(clean_ai_text(text).encode("utf-8", "surrogatepass")).hexdigest()


class FeedWriter:
    """Builds and writes rows for one program (gui, cli, mcp, ditto). Every string is redacted, AI text cleaned,
    parameters made one line and capped, and text from outside the framework marked as such. A write fails open
    (None, logged, `problem` set) unless `required`: then FeedWriteError, and the caller must not go on (an AI's
    decision is recorded before it takes effect, or nothing runs)."""

    def __init__(self, store: FeedStore, program: Literal["gui", "cli", "mcp", "ditto"]):
        if program not in PROGRAMS:
            raise ValueError(f"unknown program {program!r}")
        self.store = store
        self.program = program
        self.problem: Optional[str] = None

    @staticmethod
    def sender(role: str, caller: str = "", caller_label: str = "", client: str = "") -> FeedSender:
        return FeedSender(role=role, caller=caller, caller_label=_one_line(redact_secrets(caller_label or ""), 200),
                          client=clean_client_name(client) if client else "")

    @staticmethod
    def _params(params: Optional[Mapping[str, Any]], external: Iterable[str]) -> Tuple[Dict[str, str], Dict[str, str]]:
        external = set(external)
        cleaned, origin = {}, {}
        for name, value in (params or {}).items():   # names that would carry a tool's output: FeedEntry refuses them
            text = str(value)
            if name in external:
                text = _outside(text)
            cleaned[name] = _one_line(redact_secrets(text), MAX_PARAM_CHARS)
            origin[name] = "external" if name in external else "framework"
        unknown = external - set(cleaned)
        if unknown:
            raise ValueError(f"external names unknown parameters {sorted(unknown)}")
        return cleaned, origin

    @staticmethod
    def _facts(facts: Iterable[Any], *, for_ai: bool = False) -> List[FeedFact]:
        """`for_ai`: the facts a model-bound context may use (facts_ai, SEC-M1). A fact the framework marked
        person_only (a full path, user@address, a fingerprint) is left out of them, whoever built the list."""
        out = []
        for fact in facts:
            if isinstance(fact, FeedFact):
                label, value, source, person_only = fact.label, fact.value, fact.source, False
            elif isinstance(fact, Mapping):
                label, value, source = fact.get("label", ""), fact.get("value", ""), fact.get("source", "")
                person_only = bool(fact.get("person_only", False))
            else:  # the tools' own Fact (label, value, source, person_only)
                label, value, source = (getattr(fact, "label", ""), getattr(fact, "value", ""),
                                        getattr(fact, "source", ""))
                person_only = bool(getattr(fact, "person_only", False))
            if for_ai and person_only:
                continue
            value = str(value)
            if source:
                value = _outside(value)
            out.append(FeedFact(label=_one_line(redact_secrets(str(label)), 200),
                                value=redact_secrets(value)[:2000],
                                source=_one_line(redact_secrets(str(source or "")), 200)))
        return out

    def entry(self, kind: str, sender: FeedSender, *, text_key: str = "", params: Optional[Mapping[str, Any]] = None,
              external: Iterable[str] = (), facts_person: Iterable[Any] = (), facts_ai: Iterable[Any] = (),
              ai_text: str = "", ai_text_kind: str = "", person_text: str = "", answer: Optional[str] = None,
              warnings_decided: Iterable[Any] = (), warnings_for_person: Iterable[Any] = (), level: str = "info",
              refs: Optional[Any] = None) -> FeedEntry:
        """The cleaned row (not written yet); ValueError for what a row may not hold."""
        cleaned, origin = self._params(params, external)
        person = _cut(redact_secrets(person_text or ""), MAX_PERSON_TEXT)   # redacted before it is cut
        text = clean_ai_text(ai_text) if ai_text else ""
        return FeedEntry(
            entry_id=new_entry_id(), ts=_now(), kind=kind, sender=sender, text_key=text_key, params=cleaned,
            param_origin=origin, facts_person=self._facts(facts_person),
            facts_ai=self._facts(facts_ai, for_ai=True),
            ai_text=text, ai_text_kind=ai_text_kind if text else "", person_text=person, answer=answer,
            warnings_decided=[self._warning(w) for w in warnings_decided],
            warnings_for_person=[self._warning(w) for w in warnings_for_person],
            level=level, refs=refs if isinstance(refs, FeedRefs) else FeedRefs.model_validate(refs or {}),
            program=self.program)

    @staticmethod
    def _warning(warning: Any) -> WarningRow:
        """A warning row from a CallWarning (its kind from the catalogue), a WarningRow or its mapping."""
        if isinstance(warning, WarningRow):
            return warning
        if isinstance(warning, CallWarning):
            return WarningRow.of(warning)
        return WarningRow.model_validate(warning)

    def write(self, kind: str, sender: FeedSender, *, required: bool = False, timeout: Optional[float] = None,
              **content: Any) -> Optional[StoredEntry]:
        """Writes one row (see entry() for the content). Fails open unless `required`."""
        entry = self.entry(kind, sender, **content)
        try:
            stored = self.store.append(entry, timeout=timeout)
        except FeedWriteError as e:
            self.problem = str(e)
            if required:
                raise
            logger.warning(f"A chat entry was not saved ({e.reason}): {e}")
            return None
        self.problem = None
        return stored

    def activity_entry(self, kind: str, sender: FeedSender, *, text_key: str,
                       params: Optional[Mapping[str, Any]] = None, refs: Optional[Any] = None,
                       level: str = "info") -> FeedEntry:
        """A row about an AI's work (Core/activity.py), not written yet: one of ACTIVITY_KINDS, the framework's text by
        key with closed framework parameters (never outside text, never AI text), from the AI's trusted caller id or
        the framework, never in the person's name. ValueError for anything else."""
        if kind not in ACTIVITY_KINDS:
            raise ValueError(f"{kind!r} is not a row about an AI's work")
        if sender.role == "person":
            raise ValueError("a row about an AI's work is never in the person's name")
        return self.entry(kind, sender, text_key=text_key, params=params, refs=refs, level=level)

    def write_entry(self, entry: FeedEntry, *, timeout: Optional[float] = None) -> StoredEntry:
        """Writes a row built with entry() and fails open: a row that could not be saved comes back with row id -1
        (logged, `problem` set), so who shows it can say it was not saved."""
        try:
            stored = self.store.append(entry, timeout=timeout)
        except FeedWriteError as e:
            self.problem = str(e)
            logger.warning(f"A chat entry was not saved ({e.reason}): {e}")
            return StoredEntry(-1, entry)
        self.problem = None
        return stored

    def person_message(self, text: str, *, reply_to: Optional[str] = None,
                       timeout: Optional[float] = None) -> Optional[StoredEntry]:
        return self.write("person_message", FeedSender(role="person"), person_text=text,
                          refs=FeedRefs(reply_to=reply_to), timeout=timeout)

    def delegated_answer(self, sender: FeedSender, answer: Literal["yes", "no", "ask_person"], reason: str, *,
                         timeout: Optional[float] = None, **content: Any) -> Tuple[StoredEntry, str]:
        """Records an AI's answer to its own question (design 3.3 steps 7, 8 and 15) before the answer takes effect:
        returns the row and the hash of the reason exactly as stored (for the audit's `reason_sha256`).
        FeedWriteError when it cannot be written: then nothing may run and the person is asked instead."""
        if sender.role not in ("ditto", "client"):
            raise ValueError("a decision comes from the AI that decided")
        stored = self.write("delegated_decision", sender, required=True, timeout=timeout, answer=answer,
                            ai_text=reason, ai_text_kind="reason", **content)
        assert stored is not None  # required writes raise instead  # nosec B101
        return stored, hashlib.sha256(stored.entry.ai_text.encode("utf-8", "surrogatepass")).hexdigest()


# --- Ditto's earlier chat ------------------------------------------------------------------------------------------

def import_chat_history(store: FeedStore, history: Any, *, program: Literal["gui", "cli"] = "gui") -> int:
    """Takes Ditto's transcript (ChatHistory) into the feed once, marked as migrated; later calls, from any process,
    import nothing. Ditto's API conversation stays in its own file. Returns the number of imported rows.

    The transcript file was never signed, and a tool note can quote a handler's error (a server reply, a log line),
    so its tool notes and notices are kept as outside text, never as the framework's own words."""
    writer = FeedWriter(store, program)
    entries: List[FeedEntry] = []
    for item in list(getattr(history, "transcript", None) or []):
        role, text = getattr(item, "role", ""), str(getattr(item, "text", "") or "")
        if not text.strip():
            continue
        if role == "user":
            entry = writer.entry("person_message", FeedSender(role="person"), person_text=text)
        elif role == "assistant":
            entry = writer.entry("ditto_answer", FeedSender(role="ditto", caller="ditto"), ai_text=text,
                                 ai_text_kind="answer")
        elif role == "tool":
            entry = writer.entry("ditto_tool_note", FeedSender(role="ditto", caller="ditto"),
                                 text_key="chat.history.line", params={"text": text}, external=["text"])
        elif role == "notice":
            entry = writer.entry("notice", FeedSender(role="framework"), text_key="chat.history.line",
                                 params={"text": text}, external=["text"])
        else:
            continue
        entries.append(entry.model_copy(update={"migrated": True}))
    return len(store.append_many(entries, once=META_IMPORTED))


# --- the framework's own explanation of a decision -----------------------------------------------------------------

@dataclass(frozen=True)
class Explanation:
    """A decision record as framework text, for the person when no AI explains it (design 5.7): the header and lines
    are framework words; `ai_text` is the deciding AI's recorded reason, unchanged, to be shown in an AI text box under
    `ai_caption` (with speaking names at display time, never as rich text)."""
    header: str
    lines: List[str]
    closing: str
    ai_text: str
    ai_caption: str


def caller_words(sender: FeedSender, lang: str = "en") -> str:
    """Who a row is from, as a person reads it: a label the framework set, else words for the caller id; the app's
    own name only as its claim."""
    if sender.caller_label:
        who = sender.caller_label
    elif sender.caller == "ditto":
        who = feed_text("caller.ditto", lang)
    elif sender.caller == "mcp":
        who = feed_text("caller.shared", lang)
    elif sender.caller.startswith("mcp:"):
        who = feed_text("caller.token", lang, name=sender.caller[4:])
    else:
        who = feed_text("caller.framework", lang)
    if sender.client and sender.caller != "ditto":
        who += f" ({feed_text('caller.self_name', lang, client=sender.client)})"
    return who


def _when(ts: str) -> str:
    try:
        moment = datetime.fromisoformat(ts)
    except ValueError:
        return ts
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _warning_words(row: WarningRow, lang: str) -> str:
    return row.code.replace("_", " ")


def framework_explanation(entry: FeedEntry, lang: str = "en", *,
                          warning_text: Optional[Callable[[WarningRow, str], str]] = None) -> Explanation:
    """The record of an AI's decision in framework words, built from the row alone (no model is asked): who decided,
    when, what, the answer, the warnings it weighed and those left for the person, the facts the framework had (the
    person's view), and the recorded reason as the AI's own words. `warning_text` renders a warning for the person
    (the facts catalogue's texts); without it the code is shown in words. It is for the person only: it holds the
    person's view of the facts (full paths may be among them) and never goes to a model, which gets facts_ai."""
    render = warning_text or _warning_words
    who = caller_words(entry.sender, lang)
    lines = [feed_text("chat.explain.who", lang, who=who), feed_text("chat.explain.when", lang, when=_when(entry.ts))]
    for name in ("summary", "tool", "title"):
        if entry.params.get(name):
            what = entry.params[name]
            external = entry.param_origin.get(name) == "external"   # quoted: an outside name is never framework words
            lines.append(feed_text("chat.explain.what", lang, what=f"'{what}'" if external else what))
            break
    if entry.answer:
        lines.append(feed_text("chat.explain.answer", lang, answer=feed_text(f"chat.answer.{entry.answer}", lang)))
    if entry.warnings_decided:
        lines.append(feed_text("chat.explain.weighed", lang,
                               warnings="; ".join(render(w, lang) for w in entry.warnings_decided)))
    if entry.warnings_for_person:
        lines.append(feed_text("chat.explain.left", lang,
                               warnings="; ".join(render(w, lang) for w in entry.warnings_for_person)))
    if entry.facts_person:
        lines.append(feed_text("chat.explain.facts", lang))
        for fact in entry.facts_person:
            # One line each, and an outside value quoted: a value with line breaks (a licence or model-card text)
            # could otherwise add lines that read as the framework's own (design 5.2 R2).
            label, value = _one_line(fact.label, 200), _one_line(fact.value, 2000)
            lines.append("  " + (feed_text("chat.explain.external", lang, label=label, value=f"'{value}'",
                                           source=_one_line(fact.source, 200))
                                 if fact.source else f"{label}: {value}"))
    # An AI app's decision waits for that app (the message box); Ditto's own decisions Ditto explains once set up.
    waits = bool(entry.ai_text) and entry.sender.role == "client"
    return Explanation(header=feed_text("chat.explain.framework", lang), lines=lines,
                       closing=feed_text("chat.relay.no_ditto", lang, who=who) if waits else "",
                       ai_text=entry.ai_text, ai_caption=feed_text("chat.ai_text.caption", lang))


# --- the framework's feed ------------------------------------------------------------------------------------------

def open_chat_feed(framework: Any) -> FeedStore:
    """The framework's feed in its per-user folder, signed with the key the tool permissions are signed with (the
    audit key from the OS keyring; without one, unkeyed rows are refused while the audit log is kept with a key).
    Opens nothing yet."""
    state_dir = Path(framework.config.user_state_dir)
    registry = getattr(framework, "tool_registry", None)
    policy_store = getattr(registry, "policy_store", None)
    if policy_store is not None:
        return FeedStore(state_dir, key=getattr(policy_store, "key", None),
                         refuse_unkeyed=getattr(policy_store, "refuse_unkeyed", None))
    from orchestrator.tools.audit import AuditLog
    from orchestrator.tools.framework_tools import _audit_key
    key = _audit_key(framework, state_dir)
    return FeedStore(state_dir, key=key, refuse_unkeyed=None if key else AuditLog(state_dir / "audit.db").kept_with_key)


__all__ = ["FEED_KINDS", "CONVERSATION_KINDS", "RECORD_KINDS", "ACTIVITY_KINDS", "TEXTS", "feed_text", "WarningRow",
           "FeedSender", "FeedFact", "FeedRefs", "FeedEntry", "StoredEntry", "FeedPage", "ClearPlan", "FeedCheck",
           "FeedWriteError", "ClearPlanChanged", "FeedStore", "FeedWriter", "clean_client_name", "clean_ai_text",
           "reason_sha256", "import_chat_history", "Explanation", "caller_words", "framework_explanation",
           "open_chat_feed"]
