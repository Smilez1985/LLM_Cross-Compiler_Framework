"""The wording toolkit's GUI parts (wave 6, f-words; DECISIONS round 10, V6, Q1, Q7), offscreen.

The hover card explains a technical term while the mouse rests on it, stays while the mouse is on the card and opens
its link only on a click; F1 and a "?" button open it for the keyboard. The error box says what happened in the
person's language, folds the English technical details away (redacted, copyable) and never draws HTML from data.
Empty states, mnemonics and accessible names; the German form of address in File > Settings... and the first-run
hook; the Builder's Format and Quantization labels as the first real places.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from orchestrator.gui import error_dialog, explain, plain
from orchestrator.utils import localization
from orchestrator.utils.l10n_glossary import TERMS

pytestmark = pytest.mark.usefixtures("qapp")
MARKUP = '<b>bold</b> & <a href="https://evil.example">link</a>'


@pytest.fixture
def opened(monkeypatch):
    """Records what would open in the web browser; nothing really opens."""
    urls = []
    monkeypatch.setattr(explain, "_open_url", lambda url: urls.append(url) or True)
    yield urls
    explain.hover_controller().close_card()


@pytest.fixture
def language_state(monkeypatch):
    """The UI language and the form of address are back as they were after the test."""
    instance = localization._instance
    lang = instance.current_lang if instance is not None else None
    monkeypatch.setattr(localization, "_instance", instance)
    monkeypatch.setattr(localization, "_form_of_address", "du")
    yield
    if instance is not None:
        instance.current_lang = lang


def _send(widget, kind):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    return QApplication.sendEvent(widget, QEvent(kind))


def _shown_label(text, *terms):
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    holder = QWidget()
    label = explain.term_label(text, *terms, parent=holder)
    QVBoxLayout(holder).addWidget(label)
    holder.show()
    return holder, label


# --- the hover card -------------------------------------------------------------------------------------------------

def test_the_card_shows_after_resting_stays_on_the_card_and_hides_after_leaving(opened):
    from PySide6.QtCore import QEvent, Qt
    holder, label = _shown_label("Quantization:", "quantization")
    controller = explain.hover_controller()
    _send(label, QEvent.Enter)
    assert controller.show_timer.isActive() and controller.pending is label and controller.card is None
    controller.show_timer.timeout.emit()
    card = controller.card
    assert card is not None and card.isVisible() and card.terms == ["quantization"]
    assert card.windowFlags() & Qt.ToolTip and not card.pinned, "a hover card, never a focus-taking window"
    assert card.testAttribute(Qt.WA_ShowWithoutActivating)
    assert opened == [], "nothing opens before the person clicks"
    _send(label, QEvent.Leave)
    assert controller.hide_timer.isActive()
    _send(card, QEvent.Enter)
    assert not controller.hide_timer.isActive(), "the card stays while the mouse is on it"
    _send(card, QEvent.Leave)
    assert controller.hide_timer.isActive()
    controller.hide_timer.timeout.emit()
    assert controller.card is None
    holder.close()


def test_leaving_before_the_delay_shows_nothing_and_a_click_closes_the_card(opened):
    from PySide6.QtCore import QEvent
    holder, label = _shown_label("KV cache", "kv_cache")
    controller = explain.hover_controller()
    _send(label, QEvent.Enter)
    _send(label, QEvent.Leave)
    assert not controller.show_timer.isActive() and controller.pending is None and controller.card is None
    controller.show_for(label)
    assert controller.owner is label
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    QApplication.sendEvent(label, QMouseEvent(QEvent.MouseButtonPress, QPointF(2, 2), label.mapToGlobal(QPointF(2, 2)),
                                              Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert controller.card is None, "clicking the control itself puts the card away"
    holder.close()


def test_the_link_opens_only_on_a_click_and_only_glossary_links(opened):
    from PySide6.QtWidgets import QPushButton
    card = explain.HoverCard(["qdrant"], lang="en")
    link = card.findChild(QPushButton, "glossary_link_qdrant")
    assert "qdrant.tech" in link.text() and link.accessibleDescription() == TERMS["qdrant"].link
    assert link.focusPolicy() != 0 and link.accessibleName() == "Learn more: qdrant.tech"
    assert opened == []
    link.click()
    assert opened == [TERMS["qdrant"].link]
    assert explain.open_link("http://en.wikipedia.org/wiki/SHA-2") is False
    assert explain.open_link("https://evil.example/") is False
    assert opened == [TERMS["qdrant"].link], "an address outside the glossary never opens"


def test_the_card_speaks_the_persons_language_and_shows_text_literally(opened):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QPushButton
    card = explain.HoverCard(["npu"], context=MARKUP, lang="de")
    texts = [label.text() for label in card.findChildren(QLabel)]
    assert texts[0] == MARKUP and texts[1] == "NPU" and texts[2].startswith("Neural Processing Unit: ein Teil")
    assert all(label.textFormat() == Qt.PlainText for label in card.findChildren(QLabel))
    link = card.findChild(QPushButton, "glossary_link_npu")
    assert link.property(explain.URL_PROPERTY) == "https://de.wikipedia.org/wiki/KI-Beschleuniger"
    assert link.text().startswith("Mehr dazu: de.wikipedia.org")
    assert card.accessibleName() == "Erklärung: NPU"


def test_the_card_replaces_the_tooltip_and_keeps_its_text(opened):
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel
    combo = QComboBox()
    combo.setToolTip(plain.plain_tooltip("Pick a type <b>now</b>"))
    explain.explain(combo, "q4_k_m")
    combo.show()
    QApplication.sendEvent(combo, QHelpEvent(QEvent.ToolTip, QPoint(2, 2), combo.mapToGlobal(QPoint(2, 2))))
    card = explain.hover_controller().card
    assert card is not None and card.terms == ["q4_k_m"]
    assert card.findChildren(QLabel)[0].text() == "Pick a type <b>now</b>", "the widget's own tooltip comes first"
    combo.close()


def test_f1_and_the_help_button_open_a_pinned_card_for_the_keyboard(opened):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication, QComboBox
    combo = QComboBox()
    label = explain.term_label("&Format:", "model_format", buddy=combo)
    assert label.buddy() is combo and combo.accessibleName() == "Format"
    assert "GGUF runs with llama.cpp" in combo.accessibleDescription()
    combo.show()
    QApplication.sendEvent(combo, QKeyEvent(QEvent.KeyPress, Qt.Key_F1, Qt.NoModifier))
    card = explain.hover_controller().card
    assert card is not None and card.pinned and card.windowFlags() & Qt.Popup == Qt.Popup
    assert card.terms == ["model_format"]
    QApplication.sendEvent(card, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not card.isVisible()
    button = explain.help_button("kv_cache")
    assert button.text() == "?" and button.accessibleName() == "Explain KV cache"
    assert button.focusPolicy() == Qt.StrongFocus
    button.show()
    button.click()
    card = explain.hover_controller().card
    assert card is not None and card.pinned and card.terms == ["kv_cache"]
    combo.close()
    button.close()


def test_explain_marks_the_widget_for_screen_readers_and_refuses_unknown_terms():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    label = QLabel("GGUF")
    assert explain.explain(label, "gguf") is label
    assert label.property(explain.TERMS_PROPERTY) == ["gguf"]
    assert TERMS["gguf"].link in label.accessibleDescription()
    assert label.accessibleDescription().startswith("The file format llama.cpp uses")
    assert label.cursor().shape() == Qt.WhatsThisCursor
    explain.explain(label, "llama_cpp", "gguf")
    assert label.property(explain.TERMS_PROPERTY) == ["gguf", "llama_cpp"], "each term once"
    with pytest.raises(KeyError):
        explain.explain(label, "gguff")
    with pytest.raises(ValueError):
        explain.explain(label)


def test_mark_terms_finds_terms_in_labels_buttons_and_group_boxes():
    from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget
    window = QWidget()
    layout = QVBoxLayout(window)
    text = QLabel("Build a GGUF with an importance matrix")
    button = QPushButton("&Token")
    box = QGroupBox("Confirm the SSH host key")
    for widget in (text, button, box):
        layout.addWidget(widget)
    assert explain.mark_terms(text) == ["gguf", "imatrix"]
    assert explain.mark_terms(button) == [], "a word with several meanings is never marked by itself"
    assert explain.mark_terms_in(window) == 2
    assert box.property(explain.TERMS_PROPERTY) == ["ssh_host_key"]


# --- mnemonics and accessible names ---------------------------------------------------------------------------------

def test_mnemonic_text_keeps_the_templates_mnemonic_and_escapes_values():
    from PySide6.QtWidgets import QPushButton
    assert plain.mnemonic_text("&Save {name}", name="A&B") == "&Save A&&B"
    assert plain.mnemonic_text("&Language:") == "&Language:"
    assert plain.mnemonic_text("&Run {x}", x="a\nz") == "&Run a\\u000az", "a value stays on one line"
    assert plain.button_text("Q & A") == "Q && A", "bubble buttons keep having no mnemonic at all"
    button = QPushButton(plain.mnemonic_text("&Save {name}", name="A&B"))
    raw = QPushButton("&Save")
    if raw.shortcut().isEmpty():
        pytest.skip("this platform creates no mnemonics from '&'")
    assert not button.shortcut().isEmpty() and button.shortcut().toString().endswith("S")


def test_accessibility_helpers():
    from PySide6.QtWidgets import QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget
    from orchestrator.gui import accessible
    assert accessible.accessible_text("&Language:") == "Language"
    assert accessible.accessible_text("Q && A") == "Q & A"
    window = QWidget()
    layout = QVBoxLayout(window)
    icon = QToolButton()
    icon.setObjectName("btn_icon")
    field = QLineEdit()
    field.setObjectName("edit_name")
    label = QLabel("&Name:")
    for widget in (label, field, icon):
        layout.addWidget(widget)
    problems = accessible.accessibility_problems(window)
    assert any(p.startswith("btn_icon:") for p in problems) and any(p.startswith("edit_name:") for p in problems)
    accessible.pair(label, field)
    accessible.name_icon_button(icon, "Refresh the list")
    assert field.accessibleName() == "Name" and label.buddy() is field
    assert icon.toolTip().startswith(plain.TOOLTIP_PREFIX)
    assert accessible.accessibility_problems(window) == []


# --- the error box --------------------------------------------------------------------------------------------------

@pytest.fixture
def boxes(monkeypatch):
    shown = []
    monkeypatch.setattr(error_dialog, "_exec", lambda dialog: shown.append(dialog) or 0)
    return shown


def test_the_error_box_says_what_happened_and_folds_the_redacted_details_away(boxes):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    token = "hf_" + "Q7x" * 12   # the shape of a Hugging Face token, made up
    home_file = str(Path.home() / "logs" / "build.log")
    error_dialog.show_error(None, MARKUP, why="The board did not answer.", next_step="Check its network cable.",
                            details=f"ConnectionError: timed out\n token={token}\n see {home_file}", lang="en")
    box = boxes[-1]
    assert box.windowTitle() == "Something went wrong"
    assert box.summary.text() == MARKUP and box.summary.textFormat() == Qt.PlainText, "never HTML from data"
    assert box.why.text() == "The board did not answer." and box.next.text() == "Next step: Check its network cable."
    assert box.btn_details.text() == "Technical details" and not box.btn_details.isChecked()
    assert box.details.isHidden(), "the details are folded away"
    assert token not in box.details_text and str(Path.home()) not in box.details_text
    assert "build.log" in box.details_text and "ConnectionError: timed out" in box.details_text
    box.btn_details.click()
    assert not box.details.isHidden() and box.details.toPlainText() == box.details_text
    box.btn_copy.click()
    assert QApplication.clipboard().text() == box.details_text
    assert box.copied.text() == "Copied to the clipboard."


def test_the_error_box_in_german_with_one_button(boxes, language_state):
    calls = []
    plain.plain_error(None, "Der Build ist fehlgeschlagen.", "Das Board antwortet nicht.",
                      action=("&Open log folder", lambda: calls.append("open")), details="Traceback ...", lang="de")
    box = boxes[-1]
    assert box.windowTitle() == "Etwas ist schiefgelaufen" and box.btn_details.text() == "Technische Details"
    assert box.btn_copy.text() == "Details kopieren"
    assert box.details_hint.text().startswith("Auf Englisch, ohne Geheimnisse und ohne deinen Home-Ordner")
    assert box.btn_action.text() == "&Open log folder", "the button keeps its mnemonic"
    box.btn_action.click()
    assert calls == ["open"] and box.result() == 1
    localization.set_form_of_address("sie")
    plain.plain_error(None, "x", details="y", lang="de")
    assert "ohne Ihren Home-Ordner" in boxes[-1].details_hint.text(), "the person's form of address"


def test_the_error_box_for_an_exception(boxes):
    try:
        raise ValueError("bad value for the port")
    except ValueError as e:
        e.steps = ["Open the board's settings and choose another port."]
        error_dialog.show_exception(None, e, lang="en")
    box = boxes[-1]
    assert box.summary.text() == "Something went wrong" and not box.details.isHidden(), \
        "without a localized summary the facts are visible at once"
    assert "Traceback" in box.details_text and "ValueError: bad value for the port" in box.details_text
    assert box.next.text() == "Next step: Open the board's settings and choose another port."
    error_dialog.show_error(None, "Nothing went wrong in the details.", lang="en")
    assert boxes[-1].btn_details is None, "no details, no toggle"


# --- empty states ---------------------------------------------------------------------------------------------------

def test_an_empty_state_has_one_sentence_and_one_button():
    from PySide6.QtWidgets import QTableWidget
    from orchestrator.gui.empty_state import EmptyState, EmptyStateStack
    calls = []
    empty = EmptyState("No boards yet.", "&Scan for a board...", lambda: calls.append("scan"))
    assert empty.sentence.text() == "No boards yet." and empty.button.text() == "&Scan for a board..."
    assert not empty.button.isHidden()
    empty.button.click()
    assert calls == ["scan"]
    empty.set_action(None, None)
    assert empty.button.isHidden()
    stack = EmptyStateStack(QTableWidget(), empty)
    stack.set_empty(True)
    assert stack.is_empty() and stack.currentWidget() is empty
    stack.set_empty(False)
    assert not stack.is_empty()


# --- the German form of address -------------------------------------------------------------------------------------

def test_the_chooser_and_the_dialog_follow_the_chosen_language(language_state):
    from orchestrator.gui.form_of_address import FormOfAddressChooser, LanguageAndFormDialog
    chooser = FormOfAddressChooser("sie", lang="de")
    assert chooser.radio_sie.isChecked() and chooser.form() == "sie"
    assert chooser.radio_du.text() == "du (persönlich, Standard)" and chooser.label.text() == "&Anrede auf Deutsch:"
    seen = []
    chooser.changed.connect(seen.append)
    chooser.radio_du.click()
    assert chooser.form() == "du" and seen[-1] == "du"
    dialog = LanguageAndFormDialog(language="en", form="du")
    assert dialog.windowTitle() == "Language and form of address"
    dialog.combo_language.setCurrentIndex(dialog.combo_language.findData("de"))
    assert dialog.windowTitle() == "Sprache und Anrede" and dialog.language() == "de"
    assert dialog.chooser.radio_sie.text() == "Sie (förmlich)"


def test_the_first_run_hook_asks_saves_and_applies(tmp_path, monkeypatch, language_state):
    from PySide6.QtWidgets import QDialog
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.gui import form_of_address

    def answer(dialog):
        dialog.combo_language.setCurrentIndex(dialog.combo_language.findData("de"))
        dialog.chooser.set_form("sie")
        return QDialog.Accepted

    config = ConfigManager(tmp_path / "cfg")
    monkeypatch.setattr(form_of_address, "_exec", lambda dialog: QDialog.Rejected)
    assert form_of_address.first_run_language_and_form(config) is None
    assert not (tmp_path / "cfg" / "config.yml").exists() and localization.form_of_address() == "du", \
        "Cancel changes nothing"
    monkeypatch.setattr(form_of_address, "_exec", answer)
    assert form_of_address.first_run_language_and_form(config) == ("de", "sie")
    saved = yaml.safe_load((tmp_path / "cfg" / "config.yml").read_text(encoding="utf-8"))
    assert saved["language"] == "de" and saved["form_of_address"] == "sie"
    assert localization.form_of_address() == "sie" and localization.current_language() == "de"
    assert "Sie wählen" in localization.ui_text("term.target")
    localization.set_form_of_address("du")
    assert form_of_address.apply_saved_form(ConfigManager(tmp_path / "cfg")) == "sie", "read back at start-up"
    assert config.set("form_of_address", "Ihr") is False, "only du or sie"


def test_file_settings_general_changes_the_form_of_address(tmp_path, language_state):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.gui.settings_window import SettingsDialog
    config = ConfigManager(tmp_path / "cfg")
    dialog = SettingsDialog(SimpleNamespace(config=config), lang="de")
    assert dialog.tabs.tabText(0) == "Allgemein" and dialog.tabs.widget(0).objectName() == "tab_settings_general"
    assert dialog.chooser_form.form() == "du"
    dialog.chooser_form.radio_sie.click()
    assert config.get("form_of_address") == "sie" and localization.form_of_address() == "sie"
    saved = yaml.safe_load((tmp_path / "cfg" / "config.yml").read_text(encoding="utf-8"))
    assert saved["form_of_address"] == "sie"
    assert "die Sie ab jetzt öffnen" in dialog.lbl_general_result.text()
    rows = [dialog.table_effective.item(r, 0).text() for r in range(dialog.table_effective.rowCount())]
    assert "form_of_address" in rows
    dialog.close()


# --- the first real places: the Builder ----------------------------------------------------------------------------

def test_the_builders_format_and_quantization_labels_explain_their_terms(booted_framework, opened):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    from orchestrator.gui.builder_tab import BuilderTab
    tab = BuilderTab(booted_framework)
    assert tab.lbl_format.property(explain.TERMS_PROPERTY) == ["model_format"]
    assert tab.lbl_quant.property(explain.TERMS_PROPERTY) == ["quantization"]
    assert tab.lbl_format.buddy() is tab.format_combo and tab.lbl_quant.buddy() is tab.quant_combo
    assert tab.lbl_quant.text() == "Quantization:", "technical terms stay English"
    tab.show()
    QApplication.sendEvent(tab.quant_combo, QKeyEvent(QEvent.KeyPress, Qt.Key_F1, Qt.NoModifier))
    card = explain.hover_controller().card
    assert card is not None and card.terms == ["quantization"] and opened == []
    explain.hover_controller().close_card()
    tab.close()
    tab.deleteLater()
