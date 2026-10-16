#!/usr/bin/env python3
"""
Saving golden artifacts in the GUI (DECISIONS, eighth round, answer 3), and the question about an identical earlier
build (N11).

- `ask_and_save`: a native folder dialog opens at the output folder for a finished build the person started; OK saves
  it there (or in the folder the person picked) in a worker thread, Cancel saves nothing elsewhere and deletes
  nothing: the build stays in the framework's build folder, and "Save..." in the Deployment tab saves it later.
- `ask_earlier`: before a build, an earlier build with 100 % the same options is named with its full path; the person
  uses it or builds again (Cancel builds nothing). Nothing is skipped by itself.

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


__all__ = ["SaveWorker", "ask_and_save", "ask_earlier", "choose_folder", "result_text"]
