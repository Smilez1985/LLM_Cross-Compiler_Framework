"""Plain text outside the chat panel's bubbles (delegation design 5.2 R1, SEC-M2; work package WP6).

Message boxes, tooltips, desktop notifications and button labels guess or interpret markup in Qt: text an AI, an app
or a file name brought in must never become rich text, a link or a keyboard shortcut there. gui/plain.py keeps it
literal, and a source scan holds every new and touched window to it.
"""

import ast
import re
from pathlib import Path

import pytest

from orchestrator.gui import plain

pytestmark = pytest.mark.usefixtures("qapp")

REPO_ROOT = Path(__file__).resolve().parent.parent
GUI = REPO_ROOT / "orchestrator" / "gui"
# The new and touched windows (design 5.2: "scan extended to every new and touched window"); a package that touches
# another window adds it here.
SCANNED = sorted((GUI / "chat_panel").glob("*.py")) + [GUI / n for n in ("plain.py", "gui_state.py", "chat_window.py",
                                                                         "main_window.py",
                                                                         "knowledge_snapshots_window.py",
                                                                         "form_surface.py", "builder_parts.py",
                                                                         "knowledge_sources_window.py",
                                                                         "menus/knowledge.py")]
STATIC_BOXES = {"question", "information", "warning", "critical", "about"}
TRANSLATION_CALLS = {"t", "ui_text", "tr", "text", "feed_text"}
MARKUP = '<b>bold</b> & <a href="https://evil.example">link</a><img src=x>'


def _constant_text(node: ast.AST) -> bool:
    """A literal, or a translation of a literal key (framework text from the language tables)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.Call):
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        return name in TRANSLATION_CALLS and all(isinstance(a, ast.Constant) for a in node.args) and not node.keywords
    return False


def box_offenders(source: str, allow_constructor: bool = False):
    """Static QMessageBox helpers whose text is not constant (they guess rich text), and QMessageBox objects built
    outside plain.py."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "QMessageBox"
                and func.attr in STATIC_BOXES):
            text = node.args[2] if len(node.args) > 2 else next((k.value for k in node.keywords if k.arg == "text"),
                                                                 None)
            if text is None or not _constant_text(text):
                found.append(f"line {node.lineno}: QMessageBox.{func.attr} with non-constant text")
        elif isinstance(func, ast.Name) and func.id == "QMessageBox" and not allow_constructor:
            found.append(f"line {node.lineno}: QMessageBox built outside plain.py")
    return found


def tooltip_offenders(source: str):
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setToolTip":
            arg = node.args[0] if node.args else None
            name = (arg.func.attr if isinstance(arg.func, ast.Attribute) else getattr(arg.func, "id", "")) \
                if isinstance(arg, ast.Call) else ""
            if name != "plain_tooltip":
                found.append(f"line {node.lineno}: setToolTip without plain_tooltip")
    return found


def test_no_autotext_message_boxes_with_untrusted_text(monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(plain, "_exec", lambda box: shown.append(box) or QMessageBox.No)
    assert plain.ask(None, "Question", MARKUP) is False
    box = shown[0]
    assert box.textFormat() == Qt.PlainText and box.text() == MARKUP
    assert box.defaultButton() is box.button(QMessageBox.No), "No is the default of every question"
    plain.plain_box(None, "warning", "Title", MARKUP)
    assert shown[1].textFormat() == Qt.PlainText
    offenders = {p.name: box_offenders(p.read_text(encoding="utf-8"), allow_constructor=p.name == "plain.py")
                 for p in SCANNED}
    assert {k: v for k, v in offenders.items() if v} == {}
    # The scan catches what the rule forbids.
    sample = ('QMessageBox.critical(self, "Error", f"failed: {e}")\nQMessageBox.information(self, "t", str(x))\n'
              'QMessageBox(QMessageBox.Warning, "t", text)\nQMessageBox.warning(self, "t", "constant")\n'
              'QMessageBox.question(self, t("a"), t("history.clear.question"))\n')
    assert len(box_offenders(sample)) == 3


def test_tooltips_escape():
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import QPushButton
    original = MARKUP + "\nsecond line" + chr(0x202E)
    tip = plain.plain_tooltip(original)
    assert "<b>" not in tip and "<a " not in tip and "<img" not in tip
    document = QTextDocument()
    document.setHtml(tip)
    assert document.toPlainText() == plain.display_text(original), "the person reads exactly the text"
    assert "\\u202e" in document.toPlainText()
    button = QPushButton("x")
    button.setToolTip(tip)
    assert button.toolTip() == tip
    offenders = {p.name: tooltip_offenders(p.read_text(encoding="utf-8")) for p in SCANNED}
    assert {k: v for k, v in offenders.items() if v} == {}
    assert len(tooltip_offenders('w.setToolTip(f"{name}")\nw.setToolTip(plain_tooltip(name))\n')) == 1


def test_notification_body_has_no_markup():
    class Tray:
        def __init__(self):
            self.messages = []

        def showMessage(self, title, body):
            self.messages.append((title, body))

    tray = Tray()
    body = plain.notify(tray, "LLM <i>Framework</i>", f"3 calls wait {MARKUP}" + chr(0x200B))
    title, shown = tray.messages[0]
    assert shown == body
    for text in (title, shown):
        assert not set(text) & set("<>&"), text
    assert "bold" in shown and "\\u200b" in shown and "3 calls wait" in shown
    source = (GUI / "main_window.py").read_text(encoding="utf-8")
    assert "tray.showMessage(" not in source and "notify(tray" in source, \
        "the main window's desktop notification goes through notify()"


def test_ampersand_in_names_creates_no_mnemonic():
    from PySide6.QtWidgets import QPushButton
    raw = QPushButton("Undo: Save & Exit")
    safe = QPushButton(plain.button_text("Undo: {action}", action="Save & Exit"))
    if raw.shortcut().isEmpty():
        pytest.skip("this platform creates no mnemonics from '&'")
    assert safe.shortcut().isEmpty(), "a name with '&' never becomes a keyboard shortcut"
    assert safe.text() == "Undo: Save && Exit"
    assert plain.button_text("Run {name}", name="A&B\nZ") == "Run A&&B\\u000aZ", "one line, no mnemonic"
    # Every button of the chat panel package is labelled through button_text (or a constant without "&").
    for path in sorted((GUI / "chat_panel").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("QPushButton", "QToolButton"):
                arg = node.args[0] if node.args else None
                ok = arg is None or not isinstance(arg, ast.JoinedStr) and (
                    isinstance(arg, ast.Constant) and "&" not in str(arg.value)
                    or isinstance(arg, ast.Call) and getattr(arg.func, "id", "") == "button_text"
                    or isinstance(arg, ast.Name))
                assert ok, f"{path.name} line {node.lineno}"
    assert re.search(r"(?<!&)&(?!&)", plain.button_text("Q & A")) is None
