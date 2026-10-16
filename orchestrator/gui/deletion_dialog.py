#!/usr/bin/env python3
"""
The person's question before any deletion in the GUI (delegation design 3.10, round 5 f; round 6 10.4).

`DeletionConfirmDialog` shows a DeletionPlan item by item: the speaking name first, then the full path (selectable,
with "Show in file manager") or the Docker id, the file name, the size and what it is. Two modes:

- list: exactly the plan is deleted when the person says yes;
- multiple choice: every item has a check box, none is ticked; the plan is rebuilt from exactly the ticked items
  (orchestrator.Core.deletion.choose), so its sha256 covers them and nothing else, and the button names how many
  items and how much.

When the plan offers the trash, a choice between "Move to the trash" (the default: restorable, the space stays used)
and "Delete for good" (it says how much is freed). "No, keep everything" is the default button; the yes button only
reacts after a short moment, so a double click that opened the dialog cannot also confirm it. Every text is plain
text. The dialog deletes nothing itself: `accepted_deletion()` hands the person's yes to the deleting code, which
looks the plan up again and deletes only when it is unchanged.
"""

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QRadioButton, QScrollArea, QVBoxLayout, QWidget)

from orchestrator.Core.deletion import AcceptedDeletion, accept, choose, size_text
from orchestrator.gui.plain import button_text, one_line, plain_label
from orchestrator.utils.localization import current_language, ui_text

ARM_DELAY_MS = 600   # the yes button reacts only after this long (round 6 7.8: clicks count after 600 ms)


class DeletionConfirmDialog(QDialog):
    """Asks before deleting exactly `plan` (or, with `multiple`, the items the person ticks)."""

    def __init__(self, plan, parent: Optional[QWidget] = None, *, multiple: bool = False,
                 title: Optional[str] = None, intro: str = "", lang: Optional[str] = None):
        super().__init__(parent)
        self.plan = plan
        self.multiple = multiple
        self.lang = lang or current_language()
        self.setWindowTitle(title or self._t("deletion.title"))
        self.setMinimumWidth(640)
        self.checks: List[QCheckBox] = []
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(intro or self._t("deletion.intro.multiple" if multiple else
                                                      "deletion.intro.list")))
        inner = QWidget()
        rows = QVBoxLayout(inner)
        for item in plan.items:
            rows.addWidget(self._item(item))
        rows.addStretch(1)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)
        self.radio_trash = self.radio_permanent = None
        offered = tuple(plan.mode_offered)
        if "trash" in offered and "permanent" in offered:
            self.radio_trash = QRadioButton(self._t("deletion.mode.trash"))
            self.radio_permanent = QRadioButton(self._t("deletion.mode.permanent", size=size_text(0)))
            group = QButtonGroup(self)
            group.addButton(self.radio_trash)
            group.addButton(self.radio_permanent)
            self.radio_trash.setChecked(True)   # the default: restorable
            for radio in (self.radio_trash, self.radio_permanent):
                radio.toggled.connect(self._update)
                layout.addWidget(radio)
        else:
            layout.addWidget(plain_label(self._t("deletion.only_trash" if offered == ("trash",) else
                                                 "deletion.only_permanent")))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_yes = QPushButton()
        self.btn_yes.setAutoDefault(False)
        self.btn_no = QPushButton(button_text(self._t("deletion.no")))
        self.btn_no.setAutoDefault(True)
        self.btn_no.setDefault(True)   # Enter and Escape keep everything
        self.btn_yes.clicked.connect(self._yes)
        self.btn_no.clicked.connect(self.reject)
        buttons.addWidget(self.btn_yes)
        buttons.addWidget(self.btn_no)
        layout.addLayout(buttons)
        self._armed = False
        self._update()
        self.btn_no.setFocus(Qt.OtherFocusReason)
        QTimer.singleShot(ARM_DELAY_MS, self._arm)

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _item(self, item) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        box = QVBoxLayout(frame)
        top = QHBoxLayout()
        if self.multiple:
            check = QCheckBox()
            check.setChecked(False)   # nothing is preselected
            check.setAccessibleName(one_line(item.speaking_name))
            check.toggled.connect(self._update)
            self.checks.append(check)
            top.addWidget(check)
        name = plain_label(one_line(item.speaking_name))
        font = QFont(name.font())
        font.setBold(True)
        name.setFont(font)
        top.addWidget(name, stretch=1)
        box.addLayout(top)
        if item.full_path:
            where = QHBoxLayout()
            path = plain_label(self._t("deletion.path", path=one_line(item.full_path)))
            path.setFont(QFont("monospace"))
            where.addWidget(path, stretch=1)
            show = QPushButton(button_text(self._t("deletion.show")))
            show.setAutoDefault(False)
            show.clicked.connect(lambda _=False, p=item.full_path: self._show(p))
            where.addWidget(show)
            box.addLayout(where)
        else:
            ident = plain_label(self._t("deletion.id", id=one_line(item.file_name)))
            ident.setFont(QFont("monospace"))
            box.addWidget(ident)
        box.addWidget(plain_label(self._t("deletion.file", name=one_line(item.file_name))))
        box.addWidget(plain_label(self._t("deletion.size", size=size_text(item.size_bytes))))
        box.addWidget(plain_label(self._t("deletion.what", what=one_line(item.what))))
        return frame

    @staticmethod
    def _show(path: str) -> None:
        folder = Path(path) if Path(path).is_dir() else Path(path).parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _arm(self) -> None:
        self._armed = True
        self._update()

    def ticked(self) -> List[int]:
        return [i for i, check in enumerate(self.checks) if check.isChecked()]

    def chosen_plan(self):
        """The plan of exactly what is chosen now: the ticked items (multiple choice) or the whole plan."""
        return choose(self.plan, self.ticked()) if self.multiple else self.plan

    def mode(self) -> str:
        if self.radio_permanent is not None and self.radio_permanent.isChecked():
            return "permanent"
        return self.plan.mode_offered[0]

    def _update(self, *_args) -> None:
        chosen = self.chosen_plan()
        if self.radio_permanent is not None:
            self.radio_permanent.setText(self._t("deletion.mode.permanent",
                                                 size=size_text(chosen.total_bytes if chosen else 0)))
        if chosen is None:
            self.btn_yes.setText(button_text(self._t("deletion.yes.none")))
        else:
            key = "deletion.yes.trash" if self.mode() == "trash" else "deletion.yes.permanent"
            self.btn_yes.setText(button_text(self._t(key, n=len(chosen.items), size=size_text(chosen.total_bytes))))
        self.btn_yes.setEnabled(self._armed and chosen is not None)

    def _yes(self) -> None:
        if self._armed and self.chosen_plan() is not None:
            self.accept()

    def accepted_deletion(self) -> Optional[AcceptedDeletion]:
        """The person's yes to exactly the chosen items in the chosen mode; None unless the dialog was accepted."""
        if self.result() != QDialog.Accepted:
            return None
        chosen = self.chosen_plan()
        return accept(chosen, "gui", self.mode()) if chosen is not None else None

    def plain_text(self) -> str:
        return "\n".join(label.text() for label in self.findChildren(QLabel))


def ask_deletion(parent: Optional[QWidget], plan, **kwargs) -> Optional[AcceptedDeletion]:
    """Shows the dialog and returns the person's accepted plan, or None for "No"."""
    dialog = DeletionConfirmDialog(plan, parent, **kwargs)
    try:
        if not dialog.exec():
            return None
        return dialog.accepted_deletion()
    finally:
        dialog.deleteLater()


def free_framework_images(parent: Optional[QWidget], framework, lang: Optional[str] = None) -> Optional[object]:
    """Tools > Framework Build Images... (and the healing dialog's "Free Docker space..."): the one deletion path for
    the framework's Docker images, the same as 'llm-cli doctor --fix --tier 2'. Lists every framework build image
    (name, id, size, what it is) with nothing ticked and deletes exactly the ticked ones after the person's yes.
    Returns what was deleted (mojo.docker_objects.ImageDeletion), or None."""
    from orchestrator.Core.mojo.docker_objects import (DockerUnavailable, ImagePlanChanged, delete_framework_images,
                                                       docker_storage, framework_image_plan)
    from orchestrator.gui.plain import plain_box
    lang = lang or current_language()
    title = ui_text("images.title", lang)
    try:
        plan = framework_image_plan(framework)
    except DockerUnavailable as e:
        plain_box(parent, "warning", title, ui_text("images.unavailable", lang, reason=one_line(str(e))))
        return None
    if plan is None:
        plain_box(parent, "information", title, ui_text("images.none", lang))
        return None
    root = (docker_storage(framework) or {}).get("root")
    intro = ui_text("images.intro", lang, root=one_line(root)) if root else ui_text("images.intro_no_root", lang)
    accepted = ask_deletion(parent, plan, multiple=True, title=title, intro=intro, lang=lang)
    if accepted is None:
        return None
    try:
        result = delete_framework_images(framework, accepted.plan, actor="gui")
    except ImagePlanChanged:
        plain_box(parent, "warning", title, ui_text("deletion.changed", lang))
        return None
    except (DockerUnavailable, PermissionError, RuntimeError) as e:
        plain_box(parent, "warning", title, ui_text("deletion.failed", lang, reason=one_line(str(e))))
        return None
    lines = [ui_text("deletion.done.permanent", lang, name=one_line(n)) for n in result.deleted]
    lines += [ui_text("deletion.kept", lang, name=one_line(n), reason=one_line(r)) for n, r in result.failed]
    lines.append(ui_text("images.done", lang, n=len(result.deleted), size=size_text(result.freed_bytes)))
    plain_box(parent, "information", title, "\n".join(lines))
    return result


__all__ = ["ARM_DELAY_MS", "DeletionConfirmDialog", "ask_deletion", "free_framework_images"]
