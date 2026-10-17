#!/usr/bin/env python3
"""
Next steps of the boards and devices (Boards > Boards and Devices..., the Builder's remote scan; chain boards refines
them). Seeded by chain a0 from the wave-6 audit (items 12, 24-31, 55, 56).
"""

from orchestrator.utils.next_steps import Param, Step

_BOARDS = "act_boards_devices"
_DEVICE = Param("label")

MOUSE_LABELS = {
    "btn_boards_add": (_BOARDS, "boards.btn.add"),
    "btn_boards_settings": (_BOARDS, "boards.btn.settings"),
    "btn_boards_prune_board": (_BOARDS, "boards.btn.prune_board"),
    "btn_boards_build": (_BOARDS, "boards.btn.build"),
    "btn_boards_deploy": (_BOARDS, "boards.btn.deploy"),
    "btn_boards_verify": (_BOARDS, "boards.btn.verify"),
    "btn_boards_check": (_BOARDS, "boards.btn.check"),
}

STEPS = (
    Step("board.trust", cli="deploy devices add", cli_args=("{host}", "--user", "{user}"),
         params={"host": _DEVICE, "user": _DEVICE}, person_only="host_key", window="boards", target="btn_boards_add"),
    Step("board.probe", cli="scan --device", cli_args=("--device", "{host}", "--user", "{user}"),
         params={"host": _DEVICE, "user": _DEVICE}, tool="probe_remote_device", window="builder",
         target="act_deploy_scan_remote"),
    Step("board.pair", cli="device runtime deploy", cli_args=("{device}",), params={"device": _DEVICE},
         person_only="secret", window="boards",
         target="btn_boards_deploy"),
    Step("board.settings", cli="device settings", cli_args=("{device}",), params={"device": _DEVICE},
         person_only="permission", window="boards",
         target="btn_boards_settings"),
    Step("board.prune_on_device", cli="device runtime prune --on-device", cli_args=("{device}", "--on-device"),
         params={"device": _DEVICE}, person_only="deletion", window="boards",
         target="btn_boards_prune_board"),
    Step("board.runtime_build", cli="device runtime build", cli_args=("{device}",), params={"device": _DEVICE},
         person_only="permission", window="boards",
         target="btn_boards_build"),
    Step("board.runtime_deploy", cli="device runtime deploy", cli_args=("{device}",), params={"device": _DEVICE},
         tool="deploy_device_runtime", window="boards",
         target="btn_boards_deploy"),
    Step("board.verify_bench", cli="device runtime verify --bench-model",
         cli_args=("{device}", "--bench-model", "{model}"), params={"device": _DEVICE, "model": Param("path")},
         tool="run_device_benchmark",
         window="boards", target="btn_boards_verify"),
    Step("board.check", cli="mojo --board", cli_args=("--board", "{device}"), params={"device": _DEVICE},
         tool="run_mojo_oracle", window="mojo", target="btn_mojo_check_board", runnable=True),
    Step("boards.list", cli="device list", tool="list_devices", window="boards", target=_BOARDS, runnable=True),
    # Q5 (chain boards): forget a board's old identity after a re-flashed card, old and new fingerprint shown
    Step("board.forget_key", person_only="host_key", window="boards", target="btn_boards_forget_key", planned=True),
    Step("board.restart_server", person_only="board", window="boards", target="btn_boards_restart", planned=True),
    # what only the board itself can change
    Step("board.power_on", person_only="board"),
    Step("board.ssh_enable", person_only="board", literal="sudo systemctl enable --now ssh"),
    Step("board.login", person_only="secret", window="boards", target="btn_boards_settings"),
)

TEXTS = {
    "en": {
        "step.board.trust": "Trust the board (compare its fingerprint)",
        "step.board.probe": "Probe the board over SSH",
        "step.board.pair": "Pair the board once with its password",
        "step.board.settings": "Change the board's deploy settings",
        "step.board.prune_on_device": "Remove older builds on the board (you tick what goes)",
        "step.board.runtime_build": "Build the model program for the board",
        "step.board.runtime_deploy": "Install the model program on the board",
        "step.board.verify_bench": "Verify the board with a benchmark",
        "step.board.check": "Check the board with Mojo",
        "step.board.check.what": "Mojo checks the board {device} over SSH and lists what is wrong; it changes "
                                 "nothing.",
        "step.board.check.why": "A recorded problem points to this board.",
        "step.boards.list": "Look at the boards",
        "step.boards.list.what": "Lists every stored board with its system, deploy settings and installed model "
                                 "program.",
        "step.board.forget_key": "Forget the board's old identity and trust it again",
        "step.board.restart_server": "Restart the model program on the board",
        "step.board.power_on": "Switch the board on and connect it to the same network; for a name like "
                               "myboard.local try its IP address",
        "step.board.ssh_enable": "Switch the remote login (SSH) on",
        "step.board.login": "Check the board's user name and password",
    },
    "de": {
        "step.board.trust": "Dem Board vertrauen (seinen Fingerabdruck vergleichen)",
        "step.board.probe": "Das Board per SSH prüfen",
        "step.board.pair": "Das Board einmal mit seinem Passwort verbinden",
        "step.board.settings": "Die Deploy-Einstellungen des Boards ändern",
        "step.board.prune_on_device": "Ältere Builds auf dem Board entfernen (du hakst an, was gelöscht wird)",
        "step.board.runtime_build": "Das Modellprogramm für das Board bauen",
        "step.board.runtime_deploy": "Das Modellprogramm auf dem Board installieren",
        "step.board.verify_bench": "Das Board mit einem Benchmark prüfen",
        "step.board.check": "Das Board mit Mojo prüfen",
        "step.board.check.what": "Mojo prüft das Board {device} über SSH und listet, was nicht stimmt; es ändert "
                                 "nichts.",
        "step.board.check.why": "Ein aufgezeichnetes Problem weist auf dieses Board hin.",
        "step.boards.list": "Die Boards ansehen",
        "step.boards.list.what": "Listet jedes gespeicherte Board mit System, Deploy-Einstellungen und installiertem "
                                 "Modellprogramm.",
        "step.board.forget_key": "Die alte Identität des Boards vergessen und ihm neu vertrauen",
        "step.board.restart_server": "Das Modellprogramm auf dem Board neu starten",
        "step.board.power_on": "Das Board einschalten und mit demselben Netzwerk verbinden; bei einem Namen wie "
                               "myboard.local seine IP-Adresse versuchen",
        "step.board.ssh_enable": "Die Fernanmeldung (SSH) einschalten",
        "step.board.login": "Benutzernamen und Passwort des Boards prüfen",
    },
}
