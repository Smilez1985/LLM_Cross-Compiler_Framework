#!/usr/bin/env python3
"""
Texts of the Deployment tab's new mouse paths and of the Builder tab's hardware scan (W1' of 2026-10-16: every
action of `llmcf deploy package`, `llmcf deploy run` and `llmcf scan` also by mouse; GUI parity wave 5a, chain
"deploy"), and of the host-key question (gui/host_key.py), English and German. LocalizationManager merges them
(localization.TEXT_MODULES); a key that exists already is refused there, so these never replace another text.

Every key has the same {placeholders} in both languages (tests/test_deploy_parity.py). A text names a place with
{path:<objectName>} (wave 6: the menus are translated). The host-key question uses the words of
the CLI: compare the fingerprint with ssh-keygen on the board, then trust it. This module imports nothing from the
framework.
"""

TEXTS = {
    "en": {
        # the connection fields of the Deployment tab (deploy run --port, --label, --wait)
        "deploy.port": "Port:",
        "deploy.port.tip": "The board's SSH port: 22 unless its SSH server listens on another port.",
        "deploy.label": "Name:",
        "deploy.label.hint": "your name for the board (optional)",
        "deploy.label.tip": "Your name for the board, shown to you and to AI tools. Empty: the name it already has, "
                            "or its address.",
        "deploy.label.bad": "A board's name has 1 to 64 printable characters.",
        "deploy.wait": "Wait for the board:",
        "deploy.wait.suffix": " s",
        "deploy.wait.tip": "When the network drops during the deploy (a Wi-Fi board), wait this many seconds for the "
                           "board to answer again; the upload then continues where it stopped. 0: do not wait.",
        "deploy.bad_address": "Enter the board's address or host name, e.g. 192.168.1.50 or myboard.local (an IPv6 "
                              "address without brackets, e.g. fe80::1).",
        "deploy.bad_user": "Enter the SSH user on the board (letters, digits, '.', '_' or '-'; it starts with a "
                           "letter or '_').",
        "deploy.password.title": "SSH login",
        "deploy.password.text": "Password for {user}@{address}\n(only the first time: used once to install the "
                                "framework's SSH key, never stored; leave empty if the key is already installed)",
        "deploy.reading_key": "Reading the host key of {address}:{port}...",
        "deploy.resumed": "The network dropped {count} time(s); the deploy waited {seconds} s for the board and "
                          "continued where it stopped.",
        "deploy.trusted": "Trusted board {device_id} ({label}): AI tools may deploy to it with your confirmation.",
        "deploy.package_failed": "Package generation failed: {reason}",

        # Create package only... (deploy package)
        "deploy.package.button": "Create package only…",
        "deploy.package.tip": "Makes the deployment ZIP without contacting any board: the model, deploy.sh and "
                              "checksums; with Docker also the container image, for a board without network "
                              "(air-gapped).",
        "deploy.package.title": "Create package only",
        "deploy.package.choose_model": "No build is selected: choose the model file to package",
        "deploy.package.models": "Model files ({patterns})",
        "deploy.package.no_profile": "Choose a target hardware profile first.",
        "deploy.package.folder_title": "Where should the package of {name} go?",
        "deploy.package.no_folder": "No folder was chosen; no package was made.",
        "deploy.package.running": "Making the package of {name} in {folder}...",
        "deploy.package.done": "Package created:\n{path}\n\nCopy it to the board (for example on a USB stick) and run "
                               "deploy.sh there, or deploy it later with \"Deploy existing package…\".",
        "deploy.package.failed": "No package was made: {reason}",
        # Other build or model file... (deploy package ARTIFACT / deploy run: any path, not only a listed build)
        "deploy.source.button": "Other build or model file…",
        "deploy.source.tip": "Adds a build folder or a model file from anywhere to the list above; select it, then "
                             "Create package only… or Deploy to Target.",
        "deploy.source.folder": "Build Folder…",
        "deploy.source.model": "Model File…",
        "deploy.source.folder_title": "Choose the build folder to package or deploy",
        "deploy.source.model_title": "Choose the model file to package or deploy",
        "deploy.source.chosen": "chosen by you",

        # Deploy existing package... (deploy run PACKAGE_PATH)
        "deploy.existing.button": "Deploy existing package…",
        "deploy.existing.tip": "Uploads a deployment ZIP made earlier (here with {path:btn_deploy_package}) to the "
                               "board named above and runs its deploy.sh.",
        "deploy.existing.title": "Choose the deployment package",
        "deploy.existing.filter": "Deployment packages (*.zip)",

        # the containers of a package with Docker images (the "Use Docker Containerization?" group)
        "deploy.containers.component": "Container",
        "deploy.containers.image": "Image",
        "deploy.containers.access": "Hardware access on the board",
        "deploy.containers.inference": "Inference engine",
        "deploy.containers.access_none": "none (it runs on the CPU)",
        "deploy.containers.tip": "The containers a package with Docker images carries for the chosen profile: the "
                                 "image comes from the setting image_inference_runtime, the hardware access (an NPU "
                                 "or a GPU passed into the container) from the profile's target. Ticked containers "
                                 "go into the package; untick one to leave it out.",
        "deploy.containers.none": "No container is ticked: tick at least one in the Docker table, or switch Docker "
                                  "off for this package. Nothing was packaged.",

        # the host-key question (trust on first use)
        "deploy.hostkey.title": "New device",
        "deploy.hostkey.question": "First connection to {address}:{port}.\n\nKey type: {key_type}\nFingerprint: "
                                   "{fingerprint}\n\nCompare it with the output of\n  ssh-keygen -lf "
                                   "/etc/ssh/ssh_host_ed25519_key.pub\non the device. Trust this device?",
        "deploy.hostkey.changed_title": "SSH host key changed",
        "deploy.hostkey.unreachable": "Could not reach {address}:{port}: {error}",

        # the hardware scan (Builder tab > Scan / Import...)
        "deploy.scan.script": "💻 Scan Local Host with the Fallback Script",
        "deploy.scan.save": "💻 Scan Local Host and Save the Profile As...",
        "deploy.scan.script_save": "💻 Scan Local Host with the Fallback Script and Save the Profile As...",
        "deploy.scan.save_title": "Save the hardware profile of this computer as",
        "deploy.scan.save_filter": "Probe file, KEY=VALUE lines (*.txt);;JSON view (*.json)",
        "deploy.scan.running": "Running the {how} on this computer...",
        "deploy.scan.how.python": "Python probe",
        "deploy.scan.how.script": "fallback script",
        "deploy.scan.done": "Probe successful ({how}). Profile written: {path}",
        "deploy.scan.stored": "Stored as this computer's profile (probe id local): {path}",
        "deploy.scan.note": "Note: {note}",
        "deploy.scan.reprobe": "This profile lacks facts a build needs; run the probe again before a build.",
        "deploy.scan.failed": "The probe failed: {reason}",
        "deploy.scan.write_failed": "The profile could not be written to {path}: {reason}",
        # the access facts of a probe (HardwareProfile.access(), probe 3.3.0): `llmcf scan`, the Builder tab's scan
        "deploy.access.line": "Access: {facts}",
        "deploy.access.ssh.openssh": "SSH server OpenSSH{version}",
        "deploy.access.ssh.dropbear": "SSH server Dropbear{version}",
        "deploy.access.ssh.other": "another SSH server",
        "deploy.access.ssh.none": "no SSH server found",
        "deploy.access.source.socket": "listening on port 22",
        "deploy.access.source.process": "running",
        "deploy.access.source.service": "enabled, not seen running",
        "deploy.access.source.binary": "installed, not seen running",
        "deploy.access.also.openssh": "OpenSSH installed as well",
        "deploy.access.also.dropbear": "Dropbear installed as well",
        "deploy.access.sudo.root": "the probe ran as root (no sudo needed)",
        "deploy.access.sudo.nopasswd": "sudo without a password",
        "deploy.access.sudo.password": "sudo asks for a password (or does not allow this account)",
        "deploy.access.sudo.absent": "no sudo",
        "deploy.access.sudo.other": "administrator rights through Windows",
        "deploy.access.keygen.yes": "ssh-keygen {version} (checks signatures)",
        "deploy.access.keygen.no": "ssh-keygen {version} (too old to check signatures)",
        "deploy.access.keygen.unknown": "ssh-keygen (version not determined)",
        "deploy.access.keygen.none": "no ssh-keygen",
        "deploy.access.init": "init {init}",
        "deploy.access.packages": "packages through {manager}",
        "deploy.access.dietpi": "dietpi-software",
        "deploy.remote.title": "Remote Hardware Probe (SSH)",
        "deploy.remote.running": "Connecting to {user}@{address}:{port}...",
        "deploy.remote.cancel": "Cancel scan",
        "deploy.remote.cancel.tip": "Ends the board scan, also while it waits for the board after a network drop; "
                                    "nothing is stored.",
        "deploy.remote.cancelling": "Cancelling the scan of {address}...",
        "deploy.remote.cancelled": "The scan of {address} was cancelled; nothing was stored.",
        "deploy.remote.tool.python": "Python probe",
        "deploy.remote.tool.sh": "shell probe",
        "deploy.remote.done": "Remote scan successful from {address} ({tool}; stored as device {device_id}).",
        "deploy.remote.login_failed": "SSH login failed: {reason}",
        "deploy.remote.probe_failed": "The probe on {address} failed: {reason}",
        "deploy.remote.save_failed": "The profile could not be saved: {reason}",
        "deploy.import.label_title": "Name the board",
        "deploy.import.label_text": "Your name for this board in the probe store (optional; empty uses the board "
                                    "model from the probe file):",
        "deploy.import.cancelled": "Import cancelled; nothing was stored.",
        "deploy.import.done": "Stored as probe {probe_id} ({label}): AI clients and Ditto can use the board's probe "
                              "by this id.",
        "deploy.import.failed": "Not stored as a probe: {reason}",
        "deploy.import.reprobe": "This probe was converted from an older probe file and lacks facts; probe the "
                                 "board again with the current script when you can.",
    },
    "de": {
        "deploy.port": "Port:",
        "deploy.port.tip": "Der SSH-Port des Boards: 22, außer sein SSH-Server lauscht auf einem anderen Port.",
        "deploy.label": "Name:",
        "deploy.label.hint": "dein Name für das Board (optional)",
        "deploy.label.tip": "Dein Name für das Board, für dich und für KI-Werkzeuge sichtbar. Leer: der Name, den es "
                            "schon hat, oder seine Adresse.",
        "deploy.label.bad": "Der Name eines Boards hat 1 bis 64 druckbare Zeichen.",
        "deploy.wait": "Auf das Board warten:",
        "deploy.wait.suffix": " s",
        "deploy.wait.tip": "Bricht das Netz während des Deployments ab (ein Board im WLAN), so viele Sekunden "
                           "warten, bis das Board wieder antwortet; der Upload geht dann dort weiter, wo er stand. "
                           "0: nicht warten.",
        "deploy.bad_address": "Gib die Adresse oder den Hostnamen des Boards ein, z. B. 192.168.1.50 oder "
                              "myboard.local (eine IPv6-Adresse ohne Klammern, z. B. fe80::1).",
        "deploy.bad_user": "Gib den SSH-Benutzer auf dem Board ein (Buchstaben, Ziffern, '.', '_' oder '-'; "
                           "er beginnt mit einem Buchstaben oder '_').",
        "deploy.password.title": "SSH-Anmeldung",
        "deploy.password.text": "Passwort für {user}@{address}\n(nur beim ersten Mal: einmal benutzt, um den "
                                "SSH-Schlüssel des Frameworks einzurichten, nie gespeichert; leer lassen, wenn der "
                                "Schlüssel schon eingerichtet ist)",
        "deploy.reading_key": "Lese den Host-Schlüssel von {address}:{port}...",
        "deploy.resumed": "Das Netz ist {count}-mal abgebrochen; das Deployment hat {seconds} s auf das Board "
                          "gewartet und dort weitergemacht, wo es stand.",
        "deploy.trusted": "Vertrauenswürdiges Board {device_id} ({label}): KI-Werkzeuge dürfen mit deiner "
                          "Bestätigung darauf deployen.",
        "deploy.package_failed": "Das Paket konnte nicht erstellt werden: {reason}",

        "deploy.package.button": "Nur Paket erstellen…",
        "deploy.package.tip": "Erstellt das Deployment-ZIP, ohne ein Board zu kontaktieren: das Modell, deploy.sh "
                              "und Prüfsummen; mit Docker auch das Container-Image, für ein Board ohne Netz "
                              "(air-gapped).",
        "deploy.package.title": "Nur Paket erstellen",
        "deploy.package.choose_model": "Kein Build ausgewählt: wähle die Modelldatei für das Paket",
        "deploy.package.models": "Modelldateien ({patterns})",
        "deploy.package.no_profile": "Wähle zuerst ein Ziel-Hardwareprofil.",
        "deploy.package.folder_title": "Wohin soll das Paket von {name}?",
        "deploy.package.no_folder": "Kein Ordner gewählt; es wurde kein Paket erstellt.",
        "deploy.package.running": "Erstelle das Paket von {name} in {folder}...",
        "deploy.package.done": "Paket erstellt:\n{path}\n\nKopiere es auf das Board (zum Beispiel per "
                               "USB-Stick) und starte dort deploy.sh, oder deploye es später mit "
                               "\"Vorhandenes Paket deployen…\".",
        "deploy.package.failed": "Es wurde kein Paket erstellt: {reason}",

        "deploy.source.button": "Anderer Build oder Modelldatei…",
        "deploy.source.tip": "Fügt der Liste oben einen Build-Ordner oder eine Modelldatei von irgendwo hinzu; wähle "
                             "ihn aus, dann Nur Paket erstellen… oder Deploy to Target.",
        "deploy.source.folder": "Build-Ordner…",
        "deploy.source.model": "Modelldatei…",
        "deploy.source.folder_title": "Build-Ordner zum Packen oder Deployen wählen",
        "deploy.source.model_title": "Modelldatei zum Packen oder Deployen wählen",
        "deploy.source.chosen": "von dir gewählt",

        "deploy.existing.button": "Vorhandenes Paket deployen…",
        "deploy.existing.tip": "Lädt ein früher erstelltes Deployment-ZIP (hier mit {path:btn_deploy_package}) auf "
                               "das oben genannte Board und startet dort sein deploy.sh.",
        "deploy.existing.title": "Deployment-Paket wählen",
        "deploy.existing.filter": "Deployment-Pakete (*.zip)",
        "deploy.containers.component": "Container",
        "deploy.containers.image": "Image",
        "deploy.containers.access": "Hardware-Zugriff auf dem Board",
        "deploy.containers.inference": "Inference-Engine",
        "deploy.containers.access_none": "keiner (läuft auf der CPU)",
        "deploy.containers.tip": "Die Container, die ein Paket mit Docker-Images für das gewählte Profil enthält: das "
                                 "Image kommt aus der Einstellung image_inference_runtime, der Hardware-Zugriff (eine "
                                 "NPU oder GPU, die in den Container durchgereicht wird) aus dem Target des Profils. "
                                 "Angehakte Container kommen ins Paket; nimm den Haken weg, um einen wegzulassen.",
        "deploy.containers.none": "Kein Container ist angehakt: hake in der Docker-Tabelle mindestens einen an oder "
                                  "schalte Docker für dieses Paket aus. Es wurde nichts verpackt.",

        "deploy.hostkey.title": "Neues Gerät",
        "deploy.hostkey.question": "Erste Verbindung zu {address}:{port}.\n\nSchlüsseltyp: {key_type}\n"
                                   "Fingerabdruck: {fingerprint}\n\nVergleiche ihn mit der Ausgabe von\n  "
                                   "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub\nauf dem Gerät. Diesem Gerät "
                                   "vertrauen?",
        "deploy.hostkey.changed_title": "SSH-Host-Schlüssel geändert",
        "deploy.hostkey.unreachable": "{address}:{port} ist nicht erreichbar: {error}",

        "deploy.scan.script": "💻 Lokalen Rechner mit dem Ersatz-Skript scannen",
        "deploy.scan.save": "💻 Lokalen Rechner scannen und Profil speichern unter...",
        "deploy.scan.script_save": "💻 Lokalen Rechner mit dem Ersatz-Skript scannen und Profil speichern "
                                   "unter...",
        "deploy.scan.save_title": "Hardware-Profil dieses Rechners speichern unter",
        "deploy.scan.save_filter": "Probe-Datei, KEY=VALUE-Zeilen (*.txt);;JSON-Ansicht (*.json)",
        "deploy.scan.running": "{how} läuft auf diesem Rechner...",
        "deploy.scan.how.python": "Python-Probe",
        "deploy.scan.how.script": "Ersatz-Skript",
        "deploy.scan.done": "Probe erfolgreich ({how}). Profil geschrieben: {path}",
        "deploy.scan.stored": "Als Profil dieses Rechners gespeichert (Probe-ID local): {path}",
        "deploy.scan.note": "Hinweis: {note}",
        "deploy.scan.reprobe": "Diesem Profil fehlen Angaben, die ein Build braucht; lass die Probe vor einem "
                               "Build noch einmal laufen.",
        "deploy.scan.failed": "Die Probe ist fehlgeschlagen: {reason}",
        "deploy.scan.write_failed": "Das Profil konnte nicht nach {path} geschrieben werden: {reason}",
        # die Zugangsangaben einer Probe (HardwareProfile.access(), Probe 3.3.0): `llmcf scan`, Scan im Builder-Tab
        "deploy.access.line": "Zugang: {facts}",
        "deploy.access.ssh.openssh": "SSH-Server OpenSSH{version}",
        "deploy.access.ssh.dropbear": "SSH-Server Dropbear{version}",
        "deploy.access.ssh.other": "ein anderer SSH-Server",
        "deploy.access.ssh.none": "kein SSH-Server gefunden",
        "deploy.access.source.socket": "lauscht auf Port 22",
        "deploy.access.source.process": "läuft",
        "deploy.access.source.service": "aktiviert, nicht laufend gesehen",
        "deploy.access.source.binary": "installiert, nicht laufend gesehen",
        "deploy.access.also.openssh": "OpenSSH ist auch installiert",
        "deploy.access.also.dropbear": "Dropbear ist auch installiert",
        "deploy.access.sudo.root": "die Probe lief als root (kein sudo nötig)",
        "deploy.access.sudo.nopasswd": "sudo ohne Passwort",
        "deploy.access.sudo.password": "sudo fragt nach einem Passwort (oder erlaubt dieses Konto nicht)",
        "deploy.access.sudo.absent": "kein sudo",
        "deploy.access.sudo.other": "Administratorrechte über Windows",
        "deploy.access.keygen.yes": "ssh-keygen {version} (prüft Signaturen)",
        "deploy.access.keygen.no": "ssh-keygen {version} (zu alt, um Signaturen zu prüfen)",
        "deploy.access.keygen.unknown": "ssh-keygen (Version nicht ermittelt)",
        "deploy.access.keygen.none": "kein ssh-keygen",
        "deploy.access.init": "Init {init}",
        "deploy.access.packages": "Pakete über {manager}",
        "deploy.access.dietpi": "dietpi-software",
        "deploy.remote.title": "Hardware-Probe auf dem Board (SSH)",
        "deploy.remote.running": "Verbinde mit {user}@{address}:{port}...",
        "deploy.remote.cancel": "Scan abbrechen",
        "deploy.remote.cancel.tip": "Beendet den Scan des Boards, auch während er nach einem Netzabbruch auf das "
                                    "Board wartet; nichts wird gespeichert.",
        "deploy.remote.cancelling": "Der Scan von {address} wird abgebrochen...",
        "deploy.remote.cancelled": "Der Scan von {address} wurde abgebrochen; nichts wurde gespeichert.",
        "deploy.remote.tool.python": "Python-Probe",
        "deploy.remote.tool.sh": "Shell-Probe",
        "deploy.remote.done": "Scan von {address} erfolgreich ({tool}; als Gerät {device_id} gespeichert).",
        "deploy.remote.login_failed": "SSH-Anmeldung fehlgeschlagen: {reason}",
        "deploy.remote.probe_failed": "Die Probe auf {address} ist fehlgeschlagen: {reason}",
        "deploy.remote.save_failed": "Das Profil konnte nicht gespeichert werden: {reason}",
        "deploy.import.label_title": "Board benennen",
        "deploy.import.label_text": "Dein Name für dieses Board in der Probe-Ablage (optional; leer nimmt das "
                                    "Board-Modell aus der Probe-Datei):",
        "deploy.import.cancelled": "Import abgebrochen; nichts wurde gespeichert.",
        "deploy.import.done": "Als Probe {probe_id} gespeichert ({label}): KI-Clients und Ditto können die Probe "
                              "des Boards unter dieser ID verwenden.",
        "deploy.import.failed": "Nicht als Probe gespeichert: {reason}",
        "deploy.import.reprobe": "Diese Probe wurde aus einer älteren Probe-Datei umgewandelt und hat Lücken; lass "
                                 "die Probe auf dem Board mit dem aktuellen Skript noch einmal laufen, wenn du "
                                 "kannst.",
    },
}
