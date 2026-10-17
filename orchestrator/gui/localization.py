"""The GUI's language object (Qt).

The texts themselves and every helper the command line, the MCP server and the core use (ui_text, tr,
current_language, the du/Sie overlay) live in orchestrator/utils/localization.py without Qt. Here is what only the
window needs:

* `LocalizationManager`      the text table's view in the window's language plus the `language_changed` signal the
                             windows retranslate on (menus, tabs, the chat dock, ...).
* `get_instance(lang)`       the one instance of the window (made with the first call); orchestrator.utils.localization
                             resolves both names to this module on first use, so their 50 importers did not change.
* `install_qt_translation(lang)`
                             Qt's own translation of its standard buttons and dialogs (qtbase_<lang>, shipped with
                             PySide6): without it a German window showed Qt's English "Close" and "Cancel". The
                             instance installs it with its language and swaps it on every switch.
"""

from typing import Optional

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QObject, QTranslator, Signal

from orchestrator.utils import localization as texts
from orchestrator.utils.localization import TextTable

_qt_translator: Optional[QTranslator] = None


class LocalizationManager(QObject, TextTable):
    """
    Verwaltet Übersetzungen und Sprachstatus der Fenster (TextTable plus das Change-Signal).
    Singleton: get_instance().
    """

    language_changed = Signal(str)  # Signalisiert Sprachwechsel an UI-Komponenten

    def __init__(self, initial_lang: str = "en"):
        QObject.__init__(self)
        TextTable.__init__(self, initial_lang)

    def language_set(self, lang_code: str) -> None:
        if self is texts._instance:   # the window's instance: Qt's own buttons follow it (a test's copy leaves them)
            install_qt_translation(lang_code)
        self.language_changed.emit(lang_code)


def get_instance(initial_lang: str = "en") -> LocalizationManager:
    """The window's LocalizationManager; the first call makes it in `initial_lang` (later calls return it as it is)."""
    if texts._instance is None:
        texts._instance = LocalizationManager(initial_lang)
        install_qt_translation(texts._instance.current_lang)
    return texts._instance


def install_qt_translation(lang: str) -> bool:
    """Qt's own texts (standard buttons such as Close, Cancel, Yes and No, and its built-in dialogs) in `lang`:
    qtbase_<lang> from Qt's translations folder replaces the one installed before. English is Qt's own language and
    needs none. False without an application or when this Qt has no such translation (the buttons stay English)."""
    global _qt_translator
    app = QCoreApplication.instance()
    if app is None:
        return False
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator.deleteLater()
        _qt_translator = None
    if not lang or lang == "en":
        return False
    translator = QTranslator(app)
    if not translator.load(f"qtbase_{lang}", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        translator.deleteLater()
        return False
    app.installTranslator(translator)
    _qt_translator = translator
    return True


def qt_translator() -> Optional[QTranslator]:
    """The Qt translation installed now (None: Qt's English)."""
    return _qt_translator
