#!/usr/bin/env python3
"""
Language and the German form of address (wave 6, f-words; DECISIONS round 10, Q1).

German has two forms: "du" (the default text of every window) and "Sie" (each text module's FORMAL overlay, see
utils/localization.py). The setting `form_of_address` ("du" or "sie") chooses; set_form_of_address() applies it and
every text read afterwards follows it.

* `FormOfAddressChooser`        the two choices as radio buttons (File > Settings..., General).
* `LanguageAndFormDialog`       language and form of address in one small dialog; its texts follow the chosen
                                language at once.
* `ask_language_and_form()`     shows the dialog; (language, form) or None when the person cancelled.
* `apply_language_and_form()`   saves both settings in config.yml, applies the form and switches the language
                                (LocalizationManager.language_changed, so windows that retranslate do it).
* `first_run_language_and_form(config, parent)`
                                THE FIRST-RUN HOOK: asks, saves and applies; None (nothing changed) on Cancel. The
                                first-run guidance (chain ditto-setup) calls it.
* `apply_saved_form(config)`    reads the setting at start-up (main.py) and applies it.
"""

from typing import Any, Optional, Tuple

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QRadioButton, QVBoxLayout, QWidget)

from orchestrator.gui.plain import plain_label
from orchestrator.utils.localization import (current_language, form_of_address, get_instance, set_form_of_address,
                                             ui_text)

LANGUAGES = ("en", "de")


class FormOfAddressChooser(QWidget):
    """"du" or "Sie" as two radio buttons with a label and a note; `changed(form)` when the person picks one."""

    changed = Signal(str)

    def __init__(self, form: Optional[str] = None, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName("form_of_address_chooser")
        self.lang = lang or current_language()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(self)
        self.radio_du = QRadioButton(self)
        self.radio_du.setObjectName("radio_form_du")
        self.radio_sie = QRadioButton(self)
        self.radio_sie.setObjectName("radio_form_sie")
        self.group = QButtonGroup(self)
        self.group.addButton(self.radio_du)
        self.group.addButton(self.radio_sie)
        self.label.setBuddy(self.radio_du)
        self.note = plain_label("", self)
        for widget in (self.label, self.radio_du, self.radio_sie):
            layout.addWidget(widget)
        layout.addWidget(self.note, 1)
        self.set_form(form or form_of_address())
        self.radio_du.toggled.connect(self._toggled)
        self.retranslate(self.lang)

    def retranslate(self, lang: str) -> None:
        self.lang = lang
        self.label.setText(ui_text("words.address.form", lang))
        self.radio_du.setText(ui_text("words.address.du", lang))
        self.radio_sie.setText(ui_text("words.address.sie", lang))
        self.note.setText(ui_text("words.address.only_de", lang))

    def form(self) -> str:
        return "sie" if self.radio_sie.isChecked() else "du"

    def set_form(self, form: Optional[str]) -> None:
        value = str(form or "").strip().lower()
        (self.radio_sie if value == "sie" else self.radio_du).setChecked(True)

    def _toggled(self, _checked: bool) -> None:
        self.changed.emit(self.form())


class LanguageAndFormDialog(QDialog):
    """Language (English, Deutsch) and the German form of address; OK keeps the choice, Cancel changes nothing."""

    def __init__(self, parent: Optional[QWidget] = None, *, language: Optional[str] = None,
                 form: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName("language_and_form_dialog")
        start = language if language in LANGUAGES else current_language()
        layout = QVBoxLayout(self)
        self.intro = plain_label("", self)
        layout.addWidget(self.intro)
        rows = QFormLayout()
        self.combo_language = QComboBox(self)
        self.combo_language.setObjectName("combo_language")
        for code in LANGUAGES:
            self.combo_language.addItem(ui_text(f"words.lang.{code}", code), code)
        self.combo_language.setCurrentIndex(max(0, self.combo_language.findData(start)))
        self.lbl_language = QLabel(self)
        self.lbl_language.setBuddy(self.combo_language)
        rows.addRow(self.lbl_language, self.combo_language)
        layout.addLayout(rows)
        self.chooser = FormOfAddressChooser(form, self, start)
        layout.addWidget(self.chooser)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.combo_language.currentIndexChanged.connect(lambda _i: self.retranslate(self.language()))
        self.retranslate(start)

    def retranslate(self, lang: str) -> None:
        self.setWindowTitle(ui_text("words.address.title", lang))
        self.intro.setText(ui_text("words.address.intro", lang))
        self.lbl_language.setText(ui_text("words.address.language", lang))
        self.combo_language.setAccessibleName(ui_text("words.address.language", lang).replace("&", "").rstrip(":"))
        self.chooser.retranslate(lang)

    def language(self) -> str:
        return str(self.combo_language.currentData() or "en")

    def form(self) -> str:
        return self.chooser.form()


def _exec(dialog: QDialog) -> int:
    """Shows the dialog modally (tests replace this to answer without a window)."""
    return dialog.exec()


def ask_language_and_form(parent: Optional[QWidget] = None, *, language: Optional[str] = None,
                          form: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """Asks for the language and the German form of address; (language, form) or None when cancelled."""
    dialog = LanguageAndFormDialog(parent, language=language, form=form)
    if _exec(dialog) != QDialog.Accepted:
        return None
    return dialog.language(), dialog.form()


def apply_language_and_form(config: Any, language: str, form: str, *, save: bool = True) -> bool:
    """Uses `language` and `form` from now on: both settings set (and saved in config.yml with `save`), the form
    applied to every text, the language switched (windows that follow language_changed retranslate). Returns False
    when the settings could not be set or saved (they still apply until the program ends)."""
    language = language if language in LANGUAGES else "en"
    form = set_form_of_address(form)
    ok = True
    if config is not None:
        ok = bool(config.set("language", language)) and bool(config.set("form_of_address", form))
        if ok and save:
            ok = bool(config.save_user_config())
    get_instance(language).set_language(language)
    return ok


def first_run_language_and_form(config: Any, parent: Optional[QWidget] = None) -> Optional[Tuple[str, str]]:
    """The first-run hook: asks for the language and the form of address, saves and applies them. Returns the
    choice, or None when the person cancelled (nothing is changed then)."""
    current = (config.get("language", None) if config is not None else None) or current_language()
    form = (config.get("form_of_address", None) if config is not None else None) or form_of_address()
    choice = ask_language_and_form(parent, language=current, form=form)
    if choice is not None:
        apply_language_and_form(config, *choice)
    return choice


def apply_saved_form(config: Any) -> str:
    """Applies the saved setting form_of_address (at start-up); returns the form in use ("du" when unset)."""
    return set_form_of_address(config.get("form_of_address", "du") if config is not None else None)


__all__ = ["FormOfAddressChooser", "LANGUAGES", "LanguageAndFormDialog", "apply_language_and_form",
           "apply_saved_form", "ask_language_and_form", "first_run_language_and_form"]
