#!/usr/bin/env python3
"""
Texts of the pairing during the hardware probe, both ways (the probe kit's result file and the short code), English
and German: why a pairing was refused (remote/pairing.py PairingRefused.text), what the board said when it did not
pair (BOARD_REASONS), and what the CLI and the GUI tell the person after a pairing or when the probe could not be
stored after it. LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused
there, so these never replace another text.

Every key has the same {placeholders} in both languages (tests/test_pair_integration.py). German terms: "koppeln"
(pair), "Kopplung" (pairing), "Host-Key", "Einmal-Zahl" (nonce). This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        # PairingRefused, by its kind
        "pairing.refused.channel": "Only you can pair a board, in the CLI or the GUI; an AI cannot.",
        "pairing.refused.invalid": "The pairing in this result cannot be used: it is damaged or incomplete. The "
                                   "hardware data is kept.",
        "pairing.refused.skipped": "The board did not pair: {why}.",
        "pairing.refused.failed": "The board did not pair: {why}.",
        "pairing.refused.unknown": "Pairing refused: this result does not belong to an open pairing of this "
                                   "framework (made for another framework, or the offer was withdrawn). The hardware "
                                   "data is kept.",
        "pairing.refused.expired": "Pairing refused: the pairing offer has expired (a probe kit is valid for 24 "
                                   "hours); make a new probe kit. The hardware data is kept.",
        "pairing.refused.used": "Pairing refused: this pairing was already completed once; a probe kit pairs one "
                                "board once. The hardware data is kept.",
        "pairing.refused.audit": "The pairing could not be written to the audit log; nothing was paired.",
        "pairing.refused.no-key": "The framework has no public key or no signing key to offer the board; nothing "
                                  "was made.",
        # what the board said when it did not pair (PAIR_REASON)
        "pairing.board.not-root": "the probe did not run with sudo, so pairing was skipped (the hardware data is "
                                  "complete)",
        "pairing.board.no-kit": "the probe found no pairing files next to it",
        "pairing.board.opted-out": "pairing was switched off for this probe",
        "pairing.board.windows": "pairing of a Windows board is not there yet (the hardware data is complete)",
        "pairing.board.account": "the probe could not tell which board account to pair (run it with sudo or doas "
                                 "from that account)",
        "pairing.board.no-host-keys": "the board has no SSH host key the probe could read",
        "pairing.board.other": "the board's pairing step answered '{word}'; nothing of the framework's key was "
                               "changed there",
        # after a pairing
        "pairing.import.already": "Already paired: this board's host key was pinned by this pairing before (device "
                                  "{device_id}); the hardware data was imported again.",
        "pairing.import.store_failed": "The board IS paired (device {device_id}, its host key is pinned), but its "
                                       "hardware data could not be stored ({reason}). Import the same result file "
                                       "again: only the hardware data is imported then.",
        "pairing.code.done": "Paired with {board}: the board proved {count} SSH host key(s) with the code (device "
                             "{device_id}); no host-key question follows. The board's probe installs the "
                             "framework's key and signer files now; its console says when it is done.",
        "pairing.code.address": "Reach the board at {address}.",
    },
    "de": {
        "pairing.refused.channel": "Nur du kannst ein Board koppeln, im CLI oder in der GUI; eine KI kann es nicht.",
        "pairing.refused.invalid": "Die Kopplung in diesem Ergebnis ist nicht verwendbar: Sie ist beschädigt oder "
                                   "unvollständig. Die Hardware-Daten bleiben erhalten.",
        "pairing.refused.skipped": "Das Board wurde nicht gekoppelt: {why}.",
        "pairing.refused.failed": "Das Board wurde nicht gekoppelt: {why}.",
        "pairing.refused.unknown": "Kopplung abgelehnt: Dieses Ergebnis gehört zu keiner offenen Kopplung dieses "
                                   "Frameworks (für ein anderes Framework gemacht, oder das Angebot wurde "
                                   "zurückgezogen). Die Hardware-Daten bleiben erhalten.",
        "pairing.refused.expired": "Kopplung abgelehnt: Das Kopplungsangebot ist abgelaufen (ein Probe-Kit gilt 24 "
                                   "Stunden); erstelle ein neues Probe-Kit. Die Hardware-Daten bleiben erhalten.",
        "pairing.refused.used": "Kopplung abgelehnt: Diese Kopplung wurde schon einmal abgeschlossen; ein Probe-Kit "
                                "koppelt ein Board einmal. Die Hardware-Daten bleiben erhalten.",
        "pairing.refused.audit": "Die Kopplung konnte nicht ins Audit-Log geschrieben werden; nichts wurde "
                                 "gekoppelt.",
        "pairing.refused.no-key": "Das Framework hat keinen öffentlichen Schlüssel oder keinen Signierschlüssel, den "
                                  "es dem Board anbieten kann; nichts wurde erstellt.",
        "pairing.board.not-root": "Die Probe lief nicht mit sudo, also wurde die Kopplung übersprungen (die "
                                  "Hardware-Daten sind vollständig)",
        "pairing.board.no-kit": "Die Probe fand keine Kopplungsdateien neben sich",
        "pairing.board.opted-out": "Die Kopplung war für diese Probe abgeschaltet",
        "pairing.board.windows": "Ein Windows-Board zu koppeln gibt es noch nicht (die Hardware-Daten sind "
                                 "vollständig)",
        "pairing.board.account": "Die Probe konnte nicht erkennen, welches Board-Konto gekoppelt werden soll "
                                 "(starte sie mit sudo oder doas aus diesem Konto)",
        "pairing.board.no-host-keys": "Das Board hat keinen SSH-Host-Key, den die Probe lesen konnte",
        "pairing.board.other": "Der Kopplungsschritt des Boards antwortete '{word}'; am Schlüssel des Frameworks "
                               "wurde dort nichts geändert",
        "pairing.import.already": "Schon gekoppelt: Der Host-Key dieses Boards wurde durch diese Kopplung schon "
                                  "gepinnt (Gerät {device_id}); die Hardware-Daten wurden erneut importiert.",
        "pairing.import.store_failed": "Das Board IST gekoppelt (Gerät {device_id}, sein Host-Key ist gepinnt), aber "
                                       "seine Hardware-Daten konnten nicht gespeichert werden ({reason}). Importiere "
                                       "dieselbe Ergebnisdatei noch einmal: Dann werden nur die Hardware-Daten "
                                       "importiert.",
        "pairing.code.done": "Mit {board} gekoppelt: Das Board hat {count} SSH-Host-Key(s) mit dem Code bewiesen "
                             "(Gerät {device_id}); es folgt keine Host-Key-Frage. Die Probe des Boards legt jetzt "
                             "den Schlüssel und die Signer-Dateien des Frameworks ab; ihre Konsole sagt, wenn sie "
                             "fertig ist.",
        "pairing.code.address": "Das Board ist unter {address} erreichbar.",
    },
}
