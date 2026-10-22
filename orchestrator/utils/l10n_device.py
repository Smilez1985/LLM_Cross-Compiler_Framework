#!/usr/bin/env python3
"""
The person's texts of the board deployment (eighth round N4-N7, seventh round 2'), in English and German.

- `device.reason.<code>` and `device.next.<code>`: what happened and what the person can do, for the four reason
  codes of orchestrator/remote/reasons.py and its local_problem (a problem on this computer, not the board); a test
  keeps both languages complete, with the same placeholders.
- `device.wait.*`: the visible "waiting for the board" state of a deploy whose network dropped.
- `device.kv.*`: the KV-cache choice of the deploy settings.
- `device.bench.*`: the benchmark window, which measures on the board.

Plain words for laypeople. The placeholders are values the framework computed or the person entered (the board's
address or host name, a port, times, sizes); never the device account's name and never text from the board. This
module imports nothing from the framework (utils/localization.py merges it).
"""

TEXTS = {
    "en": {
        # --- why a board cannot be reached (N6) ---
        "device.reason.device_host_down": "The board at {address} does not answer.",
        "device.next.device_host_down": "Check that the board is switched on and on the same network as this "
                                        "computer (a board with Wi-Fi only: is its Wi-Fi connected?). If you entered "
                                        "a host name such as myboard.local, check the spelling or enter the board's "
                                        "IP address instead: not every network resolves .local names. A firewall "
                                        "that drops the connection looks the same.",
        "device.reason.device_port_closed": "The board at {address} answers, but refuses the remote login (SSH) on "
                                            "port {port}.",
        "device.next.device_port_closed": "Check that the SSH server runs on the board (on most boards: sudo "
                                          "systemctl enable --now ssh), that {port} is its SSH port, and that no "
                                          "firewall on the board or in the network blocks this port.",
        "device.reason.device_auth_failed": "The board at {address} refused the login.",
        "device.next.device_auth_failed": "Check the user name and the password. A board that was paired before may "
                                          "have lost the framework's key (for example after a new installation): "
                                          "connect once with the password to pair it again.",
        "device.reason.device_host_key_changed": "The identity (host key) of the board at {address} has changed.",
        "device.next.device_host_key_changed": "This happens when the board was installed again, but it can also "
                                               "mean that another device answers in its place. Check the board. If "
                                               "it was installed again, remove its old line from the framework's "
                                               "known_hosts file and connect again to compare the new fingerprint.",
        "device.reason.local_problem": "Something on this computer stopped the contact, not the board at {address}: "
                                       "one of the framework's own files could not be read or written, or this "
                                       "computer does not allow the connection.",
        "device.next.local_problem": "Free disk space on this computer and check that you may write to the "
                                     "framework's state folder (its SSH key and known_hosts are there) and that no "
                                     "security program on this computer blocks the connection. Then try again.",
        # --- waiting for the board during a deploy (N5) ---
        "device.wait.status": "Waiting for the board at {address}: {elapsed} of at most {limit}. The upload "
                              "continues where it stopped as soon as the board answers again.",
        "device.wait.status_install": "Waiting for the board at {address}: {elapsed} of at most {limit}. The "
                                      "installation runs on the board on its own; it is followed again as soon as "
                                      "the board answers.",
        "device.wait.back": "The board answers again: the upload continues at {done} of {total}.",
        "device.wait.install": "The board answers again: checking whether the installation on the board has "
                               "finished (it is never started twice).",
        "device.wait.cancel": "Cancel",
        "device.wait.cancelled": "Cancelled while waiting for the board. The package on the board was not "
                                 "installed.",
        "device.deploy.uploading": "Uploading the package: {done} of {total}",
        "device.deploy.installing": "Installing on the board...",
        "device.deploy.done": "Deployment successful. Package: {name}",
        "device.deploy.failed": "The deployment failed: {reason}",
        "device.deploy.old_sidecar": "The board still has the container qdrant-sidecar ({state}) from an earlier "
                                     "package with Docker images. Nothing uses it any more: the knowledge base stays "
                                     "on this computer. The framework does not remove it. To remove it, run on the "
                                     "board: docker rm -f qdrant-sidecar (the data folder data/qdrant of that old "
                                     "package stays until you delete it).",
        "device.deploy.state.running": "running",
        "device.deploy.state.stopped": "stopped",
        "device.address.label": "Address or host name:",
        "device.address.hint": "e.g. 192.168.1.50 or myboard.local",
        # --- the KV cache (N7) ---
        "device.kv.label": "KV cache:",
        "device.kv.auto": "Automatic: {kind} ({why})",
        "device.kv.chosen": "{kind} (your choice)",
        "device.kv.why_little": "little memory is left next to the model on this board ({free} GiB)",
        "device.kv.why_enough": "enough memory on this board",
        "device.kv.why_unknown": "the board's memory is not known yet: probe it first",
        "device.kv.f16": "f16: full precision (llama.cpp's default)",
        "device.kv.q8_0": "q8_0: about half the memory, almost no quality loss",
        "device.kv.q4_0": "q4_0: about a quarter of the memory, some quality loss",
        "device.kv.explain": "The KV cache holds the conversation the model keeps in memory. A quantized cache needs "
                             "less memory, so a longer conversation fits on a small board; q4_0 can make the answers "
                             "a little worse. It needs flash attention, which llama.cpp switches on by itself where "
                             "the board supports it; otherwise the server does not start and says so (then choose "
                             "f16).",
        # --- benchmarks on the board (seventh round 2', N3) ---
        "device.bench.title": "Benchmark on a board",
        "device.bench.intro": "Models built for a board are measured on the board itself, for real values. Nothing "
                              "runs on this computer. Deploy the model first; the board needs the framework's model "
                              "server ({path:btn_boards_deploy}).",
        "device.bench.device": "Board:",
        "device.bench.model": "Model on the board:",
        "device.bench.model_hint": "path on the board, e.g. models/model.gguf (relative: in the home folder)",
        "device.bench.none": "No trusted board yet: pair one in the Deployment window first.",
        "device.bench.start": "Measure on the board",
        "device.bench.running": "Measuring on the board...",
        "device.bench.untrusted": "Measure {model} on the board {board}. A model that is not one of the framework's "
                                  "catalog models (checked by its sha256 on the board) is loaded by llama-bench on "
                                  "the board as the device account, outside a "
                                  "sandbox. Allow that for this run? Your yes is written to the audit log. With No, "
                                  "only a catalog model is measured.",
        "device.bench.missing": "Choose a board and enter the path of the model on the board.",
        "device.bench.after_deploy": "Measure it on the board for real values: the board and the model are filled in "
                                     "under \"Benchmark on a board\".",
        "device.bench.prompt": "Prompt processing",
        "device.bench.gen": "Text generation",
        "device.bench.checks": "Checks on the board",
        "device.bench.recorded": "Recorded as \"tested on {board}\" with {count} build(s) of this model.",
        "device.bench.not_recorded": "No build of this model was found in the output folder, so the result is kept "
                                     "only with the board.",
        "device.tested_on": "tested on {boards}",
    },
    "de": {
        # --- warum ein Board nicht erreichbar ist (N6) ---
        "device.reason.device_host_down": "Das Board unter {address} antwortet nicht.",
        "device.next.device_host_down": "Prüfe, ob das Board eingeschaltet ist und im selben Netzwerk wie dieser "
                                        "Computer hängt (ein Board nur mit WLAN: ist das WLAN verbunden?). Wenn du "
                                        "einen Hostnamen wie myboard.local eingegeben hast, prüfe die Schreibweise "
                                        "oder gib stattdessen die IP-Adresse des Boards ein: nicht jedes Netzwerk "
                                        "löst .local-Namen auf. Eine Firewall, die die Verbindung verwirft, sieht "
                                        "genauso aus.",
        "device.reason.device_port_closed": "Das Board unter {address} antwortet, lehnt aber die Fernanmeldung (SSH) "
                                            "auf Port {port} ab.",
        "device.next.device_port_closed": "Prüfe, ob der SSH-Server auf dem Board läuft (auf den meisten Boards: "
                                          "sudo systemctl enable --now ssh), ob {port} sein SSH-Port ist und ob "
                                          "keine Firewall auf dem Board oder im Netzwerk diesen Port sperrt.",
        "device.reason.device_auth_failed": "Das Board unter {address} hat die Anmeldung abgelehnt.",
        "device.next.device_auth_failed": "Prüfe Benutzernamen und Passwort. Ein früher gekoppeltes Board kann den "
                                          "Schlüssel des Frameworks verloren haben (zum Beispiel nach einer neuen "
                                          "Installation): verbinde dich einmal mit dem Passwort, um es neu zu "
                                          "koppeln.",
        "device.reason.device_host_key_changed": "Die Identität (der Host-Schlüssel) des Boards unter {address} hat "
                                                 "sich geändert.",
        "device.next.device_host_key_changed": "Das passiert, wenn das Board neu installiert wurde, kann aber auch "
                                               "heißen, dass ein anderes Gerät an seiner Stelle antwortet. Prüfe das "
                                               "Board. Wurde es neu installiert, entferne seine alte Zeile aus der "
                                               "known_hosts-Datei des Frameworks und verbinde dich erneut, um den "
                                               "neuen Fingerabdruck zu vergleichen.",
        "device.reason.local_problem": "Etwas auf diesem Computer hat die Verbindung verhindert, nicht das Board "
                                       "unter {address}: Eine der eigenen Dateien des Frameworks ließ sich nicht "
                                       "lesen oder schreiben, oder dieser Computer erlaubt die Verbindung nicht.",
        "device.next.local_problem": "Gib Speicherplatz auf diesem Computer frei und prüfe, ob du in den Statusordner "
                                     "des Frameworks schreiben darfst (dort liegen sein SSH-Schlüssel und "
                                     "known_hosts) und ob kein Sicherheitsprogramm auf diesem Computer die "
                                     "Verbindung blockiert. Versuche es dann erneut.",
        # --- warten auf das Board während eines Deployments (N5) ---
        "device.wait.status": "Warte auf das Board unter {address}: {elapsed} von höchstens {limit}. Der Upload "
                              "geht dort weiter, wo er stehen blieb, sobald das Board wieder antwortet.",
        "device.wait.status_install": "Warte auf das Board unter {address}: {elapsed} von höchstens {limit}. Die "
                                      "Installation läuft auf dem Board für sich; sie wird wieder verfolgt, sobald "
                                      "das Board antwortet.",
        "device.wait.back": "Das Board antwortet wieder: der Upload geht bei {done} von {total} weiter.",
        "device.wait.install": "Das Board antwortet wieder: prüfe, ob die Installation auf dem Board fertig ist "
                               "(sie wird nie zweimal gestartet).",
        "device.wait.cancel": "Abbrechen",
        "device.wait.cancelled": "Beim Warten auf das Board abgebrochen. Das Paket wurde auf dem Board nicht "
                                 "installiert.",
        "device.deploy.uploading": "Paket wird hochgeladen: {done} von {total}",
        "device.deploy.installing": "Installation auf dem Board...",
        "device.deploy.done": "Deployment erfolgreich. Paket: {name}",
        "device.deploy.failed": "Das Deployment ist fehlgeschlagen: {reason}",
        "device.deploy.old_sidecar": "Auf dem Board ist noch der Container qdrant-sidecar ({state}) aus einem "
                                     "früheren Paket mit Docker-Images. Nichts nutzt ihn mehr: Die Wissensbasis "
                                     "bleibt auf diesem Computer. Das Framework entfernt ihn nicht. Um ihn zu "
                                     "entfernen, führe auf dem Board aus: docker rm -f qdrant-sidecar (der "
                                     "Datenordner data/qdrant dieses alten Pakets bleibt, bis du ihn löschst).",
        "device.deploy.state.running": "läuft",
        "device.deploy.state.stopped": "gestoppt",
        "device.address.label": "Adresse oder Hostname:",
        "device.address.hint": "z. B. 192.168.1.50 oder myboard.local",
        # --- der KV-Cache (N7) ---
        "device.kv.label": "KV-Cache:",
        "device.kv.auto": "Automatisch: {kind} ({why})",
        "device.kv.chosen": "{kind} (deine Wahl)",
        "device.kv.why_little": "neben dem Modell bleibt auf diesem Board wenig Speicher frei ({free} GiB)",
        "device.kv.why_enough": "genug Speicher auf diesem Board",
        "device.kv.why_unknown": "der Speicher des Boards ist noch nicht bekannt: erst das Board prüfen",
        "device.kv.f16": "f16: volle Genauigkeit (Standard von llama.cpp)",
        "device.kv.q8_0": "q8_0: etwa halber Speicher, fast kein Qualitätsverlust",
        "device.kv.q4_0": "q4_0: etwa ein Viertel des Speichers, etwas Qualitätsverlust",
        "device.kv.explain": "Der KV-Cache hält das Gespräch, das sich das Modell merkt. Ein quantisierter Cache "
                             "braucht weniger Speicher, so passt auf ein kleines Board ein längeres Gespräch; q4_0 "
                             "kann die Antworten etwas verschlechtern. Er braucht Flash Attention, das llama.cpp "
                             "selbst einschaltet, wo das Board es kann; sonst startet der Server nicht und sagt es "
                             "(dann f16 wählen).",
        # --- Benchmarks auf dem Board (siebte Runde 2', N3) ---
        "device.bench.title": "Benchmark auf einem Board",
        "device.bench.intro": "Für ein Board gebaute Modelle werden auf dem Board selbst gemessen, für echte Werte. "
                              "Auf diesem Computer läuft nichts. Erst das Modell deployen; das Board braucht den "
                              "Modellserver des Frameworks ({path:btn_boards_deploy}).",
        "device.bench.device": "Board:",
        "device.bench.model": "Modell auf dem Board:",
        "device.bench.model_hint": "Pfad auf dem Board, z. B. models/model.gguf (relativ: im Home-Ordner)",
        "device.bench.none": "Noch kein vertrautes Board: erst eines im Deployment-Fenster koppeln.",
        "device.bench.start": "Auf dem Board messen",
        "device.bench.running": "Messe auf dem Board...",
        "device.bench.untrusted": "{model} auf dem Board {board} messen. Ein Modell, das keines der Katalogmodelle "
                                  "des Frameworks ist (geprüft über seine sha256 auf dem Board), lädt llama-bench auf "
                                  "dem Board als Gerätekonto, außerhalb "
                                  "einer Sandbox. Das für diesen Lauf erlauben? Dein Ja wird ins Audit-Log "
                                  "geschrieben. Mit Nein wird nur ein Katalogmodell gemessen.",
        "device.bench.missing": "Wähle ein Board und gib den Pfad des Modells auf dem Board ein.",
        "device.bench.after_deploy": "Miss es auf dem Board, für echte Werte: Board und Modell sind unter "
                                     "\"Benchmark auf einem Board\" eingetragen.",
        "device.bench.prompt": "Prompt-Verarbeitung",
        "device.bench.gen": "Textgenerierung",
        "device.bench.checks": "Prüfungen auf dem Board",
        "device.bench.recorded": "Als \"getestet auf {board}\" bei {count} Build(s) dieses Modells vermerkt.",
        "device.bench.not_recorded": "Im Ausgabeordner wurde kein Build dieses Modells gefunden, daher bleibt das "
                                     "Ergebnis nur beim Board.",
        "device.tested_on": "getestet auf {boards}",
    },
}
