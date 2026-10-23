#!/usr/bin/env python3
"""
Saving golden artifacts in the GUI (DECISIONS, eighth round, answer 3), and the question about an identical earlier
build (N11).

- `ask_and_save`: a native folder dialog opens at the output folder for a finished build the person started; OK saves
  it there (or in the folder the person picked) in a worker thread, Cancel saves nothing elsewhere and deletes
  nothing: the build stays in the framework's build folder, and "Save..." in the Deployment tab saves it later.
- `ask_earlier`: before a build, an earlier build with 100 % the same options is named with its full path; the person
  uses it or builds again (Cancel builds nothing). Nothing is skipped by itself.
- `ask_and_use`: "Use" (DECISIONS round 23, item 9): the folder dialog opens at the output folder, as for a finished
  build, and the earlier build is copied there in a worker thread (artifact_output.plan_copy, copy_artifact: the one
  disk rule first, every copy checked by sha256; the earlier build stays where it is), as `llm-cli build start`'s
  "use" copies it to --output. The identical build there already is not copied again; anything else under its name
  is replaced only after the person accepted the full list in the deletion dialog ("No" is the default).

Every text is plain text; paths are the person's own and shown in full.
"""

from pathlib import Path
from typing import Any, Callable, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox

from orchestrator.utils.localization import current_language, ui_text


def choose_folder(parent, title: str, start: str) -> str:
    """The operating system's own folder dialog, opened at `start`; "" when the person cancels (tests replace it)."""
    return QFileDialog.getExistingDirectory(parent, title, start, QFileDialog.ShowDirsOnly)


def result_text(result: Any, lang: Optional[str] = None) -> str:
    """What a save did, for the person: where the model is now and how it got there."""
    from orchestrator.Core.knowledge_snapshots import size_text
    key = "save.done.renamed" if result.renamed else "save.done.kept" if getattr(result, "kept", False) \
        else "save.done.copied"
    lines = [ui_text(key, lang, path=str(result.folder), files=result.files, size=size_text(result.bytes))]
    if result.zip is not None:
        lines.append(ui_text("save.done.zip", lang, path=str(result.zip)))
    lines += list(result.notes)
    return "\n".join(lines)


class SaveWorker(QThread):
    """Saves one artifact (artifact_output.save_artifact) off the GUI thread: a copy onto another disk can take long."""
    done = Signal(bool, str)

    def __init__(self, framework: Any, folder: Path, destination: str, lang: Optional[str] = None):
        super().__init__()
        self.framework, self.folder, self.destination = framework, Path(folder), destination
        self.lang = lang or current_language()

    def run(self):
        from orchestrator.Core.artifact_output import DestinationRefused, save_artifact
        try:
            result = save_artifact(self.framework, self.folder, self.destination)
        except DestinationRefused as e:
            self.done.emit(False, ui_text("save.failed", self.lang, reason=str(e), next=e.next or ""))
            return
        except Exception as e:  # noqa: BLE001 - shown to the person; the model stays where it was
            self.done.emit(False, ui_text("save.failed", self.lang, reason=str(e), next=""))
            return
        self.done.emit(True, result_text(result, self.lang))


def ask_and_save(parent, framework: Any, folder: Path, on_done: Callable[[bool, str], None],
                 lang: Optional[str] = None) -> Optional[SaveWorker]:
    """The save dialog for one finished build in the build folder: returns the running worker (keep a reference), or
    None when the person cancelled (`on_done` then gets the "kept in the build folder" text) or kept a build that
    already lies in the output folder (when the output folder is the build folder; OK keeps it there). The output
    folder is made first when it does not exist yet, so the dialog opens there."""
    from orchestrator.Core.artifact_output import output_folder, refusal
    lang = lang or current_language()
    folder = Path(folder)
    out = output_folder(framework)
    try:
        if not out.exists() and not refusal(framework, out):
            out.mkdir(parents=True, exist_ok=True)
        in_output = out.resolve() in folder.resolve().parents
    except OSError:
        in_output = False
    chosen = choose_folder(parent, ui_text("save.title", lang, name=folder.name), str(out))
    if not chosen:
        on_done(in_output, ui_text("save.in_output" if in_output else "save.kept", lang, path=str(folder)))
        return None
    try:
        keep = in_output and Path(chosen).resolve() == out.resolve()
    except OSError:
        keep = False
    if keep:
        on_done(True, ui_text("save.in_output", lang, path=str(folder)))
        return None
    worker = SaveWorker(framework, folder, chosen, lang)
    worker.done.connect(on_done)
    worker.start()
    return worker


class CopyWorker(QThread):
    """Copies an earlier build into the chosen folder (artifact_output.copy_artifact) off the GUI thread: a copy onto
    another disk can take long. `accepted`: the person's yes to what the copy replaces (None: the name was free)."""
    done = Signal(bool, str)

    def __init__(self, framework: Any, plan: Any, accepted: Any = None, lang: Optional[str] = None):
        super().__init__()
        self.framework, self.plan, self.accepted = framework, plan, accepted
        self.lang = lang or current_language()

    def run(self):
        from orchestrator.Core.artifact_output import DestinationRefused, copy_artifact
        from orchestrator.Core.deletion import PlanChanged
        from orchestrator.Core.knowledge_snapshots import size_text
        try:
            trash = None
            if self.accepted is not None and self.accepted.mode == "trash":
                from orchestrator.Core.trash import Trash
                trash = Trash.for_framework(self.framework)
            audit = getattr(getattr(self.framework, "tool_registry", None), "audit", None)
            result = copy_artifact(self.framework, self.plan, replace=self.accepted, trash=trash, audit=audit)
        except DestinationRefused as e:
            self.done.emit(False, ui_text("earlier.copy_failed", self.lang, reason=str(e), next=e.next or ""))
            return
        except (PlanChanged, PermissionError) as e:
            self.done.emit(False, ui_text("earlier.copy_failed", self.lang, reason=str(e), next=""))
            return
        except Exception as e:  # noqa: BLE001 - shown to the person; the earlier build stays where it was
            self.done.emit(False, ui_text("earlier.copy_failed", self.lang, reason=str(e), next=""))
            return
        lines = [ui_text("earlier.copied", self.lang, path=str(result.folder), files=result.files,
                         size=size_text(result.bytes), source=str(self.plan.source))]
        if result.zip is not None:
            lines.append(ui_text("save.done.zip", self.lang, path=str(result.zip)))
        lines += list(result.notes)
        self.done.emit(True, "\n".join(lines))


def ask_and_use(parent, framework: Any, earlier: Any, on_done: Callable[[bool, str], None],
                lang: Optional[str] = None) -> Optional[CopyWorker]:
    """"Use" of the earlier build `earlier` (build_identity.EarlierBuild): the folder dialog at the output folder (made
    first when it does not exist yet); the build is copied into the chosen folder at its relative path (its label's)
    in a worker thread, returned (keep a reference). None when nothing is copied: the person cancelled the folder
    dialog (nothing more is said: the caller named the earlier build already), cancelled the replacement, the folder
    is refused, or the identical build lies there already; `on_done` then gets the text that says so."""
    from orchestrator.Core.artifact_output import DestinationRefused, output_folder, plan_copy, refusal
    lang = lang or current_language()
    source = str(earlier.info.get("path") or "")
    if not source:
        return None
    out = output_folder(framework)
    try:
        if not out.exists() and not refusal(framework, out):
            out.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    chosen = choose_folder(parent, ui_text("earlier.use_title", lang, name=Path(source).name), str(out))
    if not chosen:
        return None
    relative = earlier.label.split(":", 1)[1] if ":" in earlier.label else None
    try:
        plan = plan_copy(framework, source, chosen, relative=relative)
    except DestinationRefused as e:
        on_done(False, ui_text("earlier.copy_failed", lang, reason=str(e), next=e.next or ""))
        return None
    if plan.there:
        on_done(True, ui_text("earlier.there", lang, path=str(plan.target)))
        return None
    accepted = None
    if plan.replaces is not None:
        from orchestrator.gui.deletion_dialog import ask_deletion
        accepted = ask_deletion(parent, plan.replaces, lang=lang, title=ui_text("earlier.replace_title", lang),
                                intro=ui_text("earlier.replace_intro", lang, path=str(plan.target)))
        if accepted is None:
            on_done(False, ui_text("earlier.not_replaced", lang, path=str(plan.target), source=source))
            return None
    worker = CopyWorker(framework, plan, accepted, lang)
    worker.done.connect(on_done)
    worker.start()
    return worker


def ask_earlier(parent, found: List[Any], lang: Optional[str] = None) -> str:
    """The person's choice about earlier builds with the same options (build_identity.EarlierBuild): "use" (nothing is
    built), "again" (build anyway) or "cancel". The full path of the earlier build is shown; "Use" is the default."""
    from orchestrator.gui.plain import _exec
    lang = lang or current_language()
    first = found[0]
    key = "earlier.identical" if first.match == "identical" else "earlier.unchecked"
    text = ui_text(key, lang, path=str(first.info.get("path") or first.label), created=first.created or "?",
                   commit=(first.revision or "?")[:12])
    box = QMessageBox(QMessageBox.Question, ui_text("earlier.title", lang), "", QMessageBox.NoButton,
                      parent if parent is not None else None)
    box.setTextFormat(Qt.PlainText)
    box.setText(text)
    use = box.addButton(ui_text("earlier.use", lang).replace("&", "&&"), QMessageBox.AcceptRole)
    again = box.addButton(ui_text("earlier.again", lang).replace("&", "&&"), QMessageBox.DestructiveRole)
    box.addButton(QMessageBox.Cancel)
    box.setDefaultButton(use)
    _exec(box)
    clicked = box.clickedButton()
    return "use" if clicked is use else "again" if clicked is again else "cancel"


__all__ = ["CopyWorker", "SaveWorker", "ask_and_save", "ask_and_use", "ask_earlier", "choose_folder", "result_text"]
