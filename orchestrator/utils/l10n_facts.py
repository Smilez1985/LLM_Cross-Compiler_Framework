#!/usr/bin/env python3
"""
The person's texts of the facts catalogue (delegation design 4.1, 4.5, 8.4; work package WP4), in English and German.

- `warning.<code>` and `blocker.<code>`: the short layer a person reads, for every code of
  orchestrator/tools/facts_catalog.py (a test keeps both languages complete, with the same placeholders).
- `action.*`: what the person can do about a blocker, the place in the GUI first.
- `safer.*`: a safer option next to a warning.
- `summary.*`: the lines of the short summary at the top of a question (confirmation.person_summary).

Plain words for laypeople: no ids, no codes, no jargon. The placeholders are numbers the framework computed (and, in
`summary.what.*`, speaking names); never text from outside the framework. This module imports nothing from the
framework (utils/localization.py merges it).
"""

TEXTS = {
    "en": {
        # --- situation warnings ---
        "warning.disk_tight": "The disk gets tight: {free_after_gb} GB stay free, {reserve_gb} GB must stay free.",
        "warning.download_large": "A large download: about {download_gb} GB.",
        "warning.host_fit_tight": "This computer's memory is tight for the conversion ({needed_gb} GB needed, "
                                  "{usable_gb} GB usable).",
        "warning.host_fit_does_not_fit": "This computer probably has too little memory for the conversion "
                                         "({needed_gb} GB needed, {usable_gb} GB usable).",
        "warning.target_fit_tight": "The board's memory is tight for this model ({needed_gb} GB needed, {usable_gb} "
                                    "GB).",
        "warning.target_fit_does_not_fit": "The model will not run on the board: too little memory ({needed_gb} GB "
                                           "needed, {usable_gb} GB).",
        "warning.image_build_needed": "The first build for this hardware prepares its build environment first "
                                      "(needs the internet).",
        "warning.long_build": "Takes about {minutes} minutes (from {n} earlier builds).",
        "warning.embedding_model_download": "The search model (about {size_gb} GB) is downloaded once.",
        "warning.many_pages": "Up to {max_pages} pages from {sites} websites.",
        "warning.device_runtime_replaced": "Replaces the model server on the board; the previous one stays as a "
                                           "fallback.",
        "warning.device_model_replaced": "Replaces the model on the board.",
        "warning.untested_module": "Nobody has tried this hardware module on the board yet.",
        # --- security warnings ---
        "warning.pickle_weights": "The model files are in an old format (.bin/.pt) that can run hidden code when "
                                  "loaded.",
        "warning.base_image_unofficial": "The build environment comes from {registry}, not from Docker's checked "
                                         "collection; nobody checked it.",
        "warning.base_image_unpinned": "The build environment is not fixed to one exact version.",
        "warning.vendor_soft_cap_exceeded": "More vendor downloads than usual ({what}: {count}, usually up to {cap}).",
        "warning.target_unreviewed": "Nobody reviewed the build scripts of this hardware module.",
        "warning.target_pack_community": "A hardware module from the community: reviewed, but not rebuilt "
                                         "independently.",
        "warning.first_build_unconfirmed": "An AI made or installed this build code; you see its first build once.",
        "warning.preflight_error": "The check before the build found a problem ({check}); building anyway ignores it.",
        "warning.untrusted_model_native": "Runs a model that is not from the framework's list directly on a board "
                                          "(as its device account), or in the sandbox of this computer that you have "
                                          "not allowed for AIs.",
        "warning.signs_as_you": "The pack is signed with your personal key.",
        "warning.facts_unknown": "Some facts could not be checked ({unknown}), so the model may be unsafe or "
                                 "restricted.",
        # --- legal warnings ---
        "warning.gated_model": "This model has conditions you accepted on huggingface.co with your account.",
        "warning.base_model_gated": "The model it is based on has conditions of use.",
        "warning.license_restrictive": "The licence restricts how the model may be used.",
        "warning.license_unknown": "The licence is not one of the known free licences: read it first.",
        "warning.vendor_licence": "Installing the vendor tools means accepting the vendor's licence.",
        # --- blockers ---
        "blocker.disk_full": "There is not enough free disk space.",
        "blocker.offline_mode": "Offline mode is on.",
        "blocker.sandbox_off": "The build sandbox is switched off.",
        "blocker.module_flags_undecided": "The module sets CMake options outside the allowed list; only you decide "
                                          "whether they are used.",
        "blocker.knowledge_off": "The knowledge base is switched off.",
        "blocker.target_revoked": "This hardware module was withdrawn by the community.",
        "blocker.pack_revoked": "This pack was withdrawn by the community.",
        "blocker.pack_rejected": "This pack failed the framework's checks.",
        "blocker.no_convertible_weights": "This model has no files the framework can convert.",
        "blocker.not_gguf": "Only models in the GGUF format can be measured.",
        "blocker.model_unpinned": "This model is not fixed to an exact file, so it is not used.",
        "blocker.unsafe_filename": "The file name cannot be used on this computer.",
        "blocker.size_unknown": "The size of the file is unknown, so it is not downloaded.",
        "blocker.image_problem": "A program image is not fixed to an exact version and was not built here.",
        "blocker.gated_no_access": "This model has conditions, and your Hugging Face sign-in has no access yet.",
        "blocker.unknown_repository": "This model does not exist on huggingface.co.",
        "blocker.pack_unknown": "There is no such pack.",
        "blocker.artifact_unknown": "There is no such built model.",
        "blocker.snapshot_unknown": "There is no such backup of the knowledge base.",
        "blocker.trash_unknown": "There is no such item in the trash.",
        "blocker.proposal_missing": "There is no fix proposal for this build.",
        "blocker.file_not_in_repo": "The model has no file with this name.",
        "blocker.url_not_public": "This address is not a public website.",
        "blocker.crawl_caps": "More pages or deeper links than your settings allow.",
        "blocker.setting_invalid": "This value is not allowed for the setting.",
        "blocker.index_stale": "The community list could not be updated.",
        "blocker.ingest_notice_unread": "You have not read the Deep Ingest notice yet.",
        "blocker.crawl_quota": "Today's limit of read pages is used up.",
        "blocker.crawl_running": "Another website reading is running.",
        "blocker.device_busy": "Something else is being installed on this board right now.",
        "blocker.artifact_busy": "This built model is in use right now.",
        "blocker.device_not_trusted": "This board is not paired yet.",
        "blocker.host_key_unknown": "This board's identity is not confirmed yet.",
        "blocker.device_host_key_changed": "This board's identity has changed.",
        "blocker.device_host_down": "The board does not answer.",
        "blocker.device_port_closed": "The board answers, but refuses the remote login.",
        "blocker.device_auth_failed": "The board refused the login.",
        "blocker.toolchain_missing": "The build tools are not installed yet.",
        "blocker.runtime_not_built": "Ditto's model server is not built yet.",
        "blocker.model_not_downloaded": "The model is not downloaded yet.",
        "blocker.no_device_runtime": "The board has no model server yet.",
        "blocker.no_device_model": "The model is not on the board yet.",
        "blocker.device_model_unset": "No model is chosen for the board yet.",
        "blocker.setting_loosens": "Only you can make this change to the setting.",
        "blocker.no_signing_key": "You have no personal signing key yet.",
        "blocker.commit_required": "The exact model version could not be found out.",
        "blocker.trash_not_yours": "You deleted this yourself, so only you can restore it.",
        "blocker.file_outside_folders": "The file is outside the folders you shared.",
        "blocker.file_changed": "The file changed in the meantime.",
        "blocker.snapshot_no_room": "The backup of the knowledge base taken first does not fit on the disk.",
        "blocker.gui_not_open": "The framework window is not open.",
        # --- what the person can do ---
        "action.free_disk": "Free disk space (old builds, the model cache, the trash), or choose a smaller model.",
        # a call the disk cannot hold (Core/disk_plan.person_text: the one disk rule, A4)
        "disk.no_room": "Not enough disk space: {needed} needed, {free} free, {reserve} must stay free.",
        "disk.no_room.reserved": "A running build or download has reserved {reserved} on that disk: try again once "
                                 "it has finished (its reservation ends with it, also when its program has ended), or "
                                 "free disk space.",
        "disk.no_room.trash": "The trash holds {trash} on that disk; emptying it ({path:act_file_trash}) frees it.",
        "action.free_snapshots": "Delete or export knowledge snapshots you no longer need "
                                 "({path:act_knowledge_snapshots}), or free space on that disk otherwise.",
        "action.offline_off": "Switch offline mode off ({path:act_ditto_settings}), or use a model already on this "
                              "computer.",
        "action.sandbox_on": "Switch the build sandbox on: today only in the settings file; "
                             "{path:act_file_settings} shows the setting.",
        "action.decide_module_flags": "Start the build yourself in the Builder: it shows the module's CMake options "
                                      "outside the allowed list, what they do and a recommendation, and you decide.",
        "action.knowledge_on": "Switch the knowledge base on ({path:act_ditto_settings}).",
        "action.remove_target": "Remove the hardware module in the Community Hub.",
        "action.pin_image": "Give the program image with its exact version in "
                            "{path:act_tools_module_wizard}, or package without images.",
        "action.hf_access": "Sign in to Hugging Face and accept the model's conditions on huggingface.co, then save "
                            "your sign-in in the framework.",
        "action.update_index": "Update the community list in the Community Hub.",
        "action.ingest_notice": "Read the Deep Ingest notice ({path:act_tools_mcp_crawl_notice}).",
        "action.pair_device": "Pair the board once with its password ({path:btn_boards_deploy}).",
        "action.check_device": "Check the board and confirm its identity again ({path:act_boards_devices}).",
        "action.device_on": "Check that the board is switched on and on the same network; if you entered a host "
                            "name such as myboard.local, try the board's IP address.",
        "action.device_ssh": "Check that the remote login (SSH) runs on the board, that the port is right and that no "
                             "firewall blocks it.",
        "action.device_login": "Check the user name and the password, then pair the board again "
                               "({path:btn_boards_deploy}).",
        "action.install_tools": "Install the build tools (an administrator does this), or build in a container "
                                "({path:cb_ditto_runtime_placement}).",
        "action.device_settings": "Choose the model in the board's deploy settings.",
        "action.signing_key": "Create your personal keys in the Community Hub.",
        "action.commit_required": "Try again when Hugging Face answers, or start the build yourself.",
        "action.restore_yourself": "Restore it yourself in the trash.",
        "action.add_folder": "Share the folder with the framework (Add folder).",
        "action.open_window": "Open the framework window.",
        # --- safer options ---
        "safer.smaller_model": "a smaller model",
        "safer.smaller_quantization": "a smaller quantization",
        "safer.safetensors": "a model with safetensors files",
        "safer.official_image": "an image from Docker's checked collection",
        "safer.sandbox": "run it in the sandbox",
        # --- the short summary of a question ---
        "summary.title": "In short:",
        "summary.what": "Run “{title}”.",
        "summary.what.start_build": "Build {model} for {target}.",
        "summary.what.start_recipe_build": "Build {model} for {target} with a community recipe.",
        "summary.what.download_hf_file": "Download a file of {model}.",
        "summary.what.add_pack_from_index": "Install a pack from the community list.",
        "summary.what.export_pack": "Create a pack signed with your personal key.",
        "summary.what.crawl_docs_site": "Read {start_urls} websites into the knowledge base.",
        "summary.what.create_deploy_package": "Package a built model for a board.",
        "summary.what.deploy_to_device": "Copy a model to {device} and install it there.",
        "summary.what.probe_remote_device": "Look at the hardware of {device}.",
        "summary.what.deploy_device_runtime": "Install the model server on {device}.",
        "summary.what.run_device_benchmark": "Check the model server on {device} and measure its speed.",
        "summary.what.build_local_runtime": "Build Ditto's model server for this computer.",
        "summary.what.start_local_runtime": "Start Ditto's model server.",
        "summary.what.run_benchmark": "Measure how fast a model runs on this computer.",
        "summary.what.create_target_module": "Add a new hardware module.",
        "summary.what.update_settings": "Change framework settings.",
        "summary.what.resolve_healing": "Decide about a fix for a failed build.",
        "summary.download": "About {gb} GB to download.",
        "summary.disk": "About {gb} GB of disk space needed.",
        "summary.minutes": "Takes about {minutes} minutes.",
        "summary.needs_you": "Needs your decision because: {reasons}",
        "summary.situation": "To weigh: {items}",
        "summary.safer": "Safer option: {option}.",
        "summary.only_you": "Only you can decide this.",
        "summary.delegated": "{who} may decide this itself under your delegation “{label}”.",
        "summary.recommends": "{who} recommends (its own words): {note}",
        "summary.you_decide": "You decide.",
        # --- closed words inside a warning (facts_catalog.person_words): what could not be checked, which list ---
        "unknown.huggingface": "Hugging Face did not answer",
        "unknown.licence": "no licence is named",
        "unknown.base_model": "the model it is based on",
        "unknown.weights_format": "the format of the model files",
        "unknown.other": "something else",
        "vendor_list.package_sources": "vendor package sources",
        "vendor_list.pip_packages": "Python packages",
        "vendor_list.pinned_downloads": "vendor downloads",
        "vendor_list.other": "vendor software",
    },
    "de": {
        # --- Situationshinweise ---
        "warning.disk_tight": "Der Speicherplatz wird knapp: {free_after_gb} GB bleiben frei, {reserve_gb} GB "
                              "müssen frei bleiben.",
        "warning.download_large": "Ein großer Download: etwa {download_gb} GB.",
        "warning.host_fit_tight": "Der Arbeitsspeicher dieses Computers ist für die Umwandlung knapp ({needed_gb} GB "
                                  "nötig, {usable_gb} GB nutzbar).",
        "warning.host_fit_does_not_fit": "Dieser Computer hat für die Umwandlung wahrscheinlich zu wenig "
                                         "Arbeitsspeicher ({needed_gb} GB nötig, {usable_gb} GB nutzbar).",
        "warning.target_fit_tight": "Der Speicher des Boards ist für dieses Modell knapp ({needed_gb} GB nötig, "
                                    "{usable_gb} GB).",
        "warning.target_fit_does_not_fit": "Das Modell läuft auf dem Board nicht: zu wenig Speicher ({needed_gb} GB "
                                           "nötig, {usable_gb} GB).",
        "warning.image_build_needed": "Der erste Build für diese Hardware richtet zuerst seine Build-Umgebung ein "
                                      "(braucht Internet).",
        "warning.long_build": "Dauert etwa {minutes} Minuten (aus {n} früheren Builds).",
        "warning.embedding_model_download": "Das Suchmodell (etwa {size_gb} GB) wird einmal heruntergeladen.",
        "warning.many_pages": "Bis zu {max_pages} Seiten von {sites} Websites.",
        "warning.device_runtime_replaced": "Ersetzt den Modellserver auf dem Board; der bisherige bleibt als Reserve.",
        "warning.device_model_replaced": "Ersetzt das Modell auf dem Board.",
        "warning.untested_module": "Niemand hat dieses Hardware-Modul schon auf dem Board ausprobiert.",
        # --- Sicherheitshinweise ---
        "warning.pickle_weights": "Die Modelldateien haben ein altes Format (.bin/.pt), das beim Laden versteckten "
                                  "Code ausführen kann.",
        "warning.base_image_unofficial": "Die Build-Umgebung kommt von {registry}, nicht aus Dockers geprüfter "
                                         "Sammlung; niemand hat sie geprüft.",
        "warning.base_image_unpinned": "Die Build-Umgebung ist nicht auf eine genaue Version festgelegt.",
        "warning.vendor_soft_cap_exceeded": "Mehr Hersteller-Downloads als üblich ({what}: {count}, üblich bis "
                                            "{cap}).",
        "warning.target_unreviewed": "Niemand hat die Build-Skripte dieses Hardware-Moduls geprüft.",
        "warning.target_pack_community": "Ein Hardware-Modul aus der Community: geprüft, aber nicht unabhängig "
                                         "nachgebaut.",
        "warning.first_build_unconfirmed": "Eine KI hat diesen Build-Code erstellt oder installiert; den ersten "
                                           "Build siehst du einmal selbst.",
        "warning.preflight_error": "Die Prüfung vor dem Build hat ein Problem gefunden ({check}); trotzdem bauen "
                                   "übergeht es.",
        "warning.untrusted_model_native": "Führt ein Modell, das nicht aus der Liste des Frameworks stammt, direkt "
                                          "auf einem Board aus (als dessen Gerätekonto), oder in der Sandbox dieses "
                                          "Computers, die du für KIs nicht freigegeben hast.",
        "warning.signs_as_you": "Das Paket wird mit deinem persönlichen Schlüssel signiert.",
        "warning.facts_unknown": "Einiges konnte nicht geprüft werden ({unknown}); das Modell könnte unsicher oder "
                                 "eingeschränkt sein.",
        # --- rechtliche Hinweise ---
        "warning.gated_model": "Für dieses Modell hast du auf huggingface.co mit deinem Konto Bedingungen "
                               "akzeptiert.",
        "warning.base_model_gated": "Das Modell, auf dem es beruht, hat Nutzungsbedingungen.",
        "warning.license_restrictive": "Die Lizenz schränkt ein, wie das Modell genutzt werden darf.",
        "warning.license_unknown": "Die Lizenz ist keine der bekannten freien Lizenzen: zuerst lesen.",
        "warning.vendor_licence": "Wer die Hersteller-Werkzeuge installiert, akzeptiert die Lizenz des Herstellers.",
        # --- harte Hindernisse ---
        "blocker.disk_full": "Es ist nicht genug Speicherplatz frei.",
        "blocker.offline_mode": "Der Offline-Modus ist an.",
        "blocker.sandbox_off": "Die Build-Sandbox ist ausgeschaltet.",
        "blocker.module_flags_undecided": "Das Modul setzt CMake-Optionen außerhalb der erlaubten Liste; nur du "
                                          "entscheidest, ob sie genutzt werden.",
        "blocker.knowledge_off": "Die Wissensbasis ist ausgeschaltet.",
        "blocker.target_revoked": "Dieses Hardware-Modul wurde von der Community zurückgezogen.",
        "blocker.pack_revoked": "Dieses Paket wurde von der Community zurückgezogen.",
        "blocker.pack_rejected": "Dieses Paket hat die Prüfungen des Frameworks nicht bestanden.",
        "blocker.no_convertible_weights": "Dieses Modell hat keine Dateien, die das Framework umwandeln kann.",
        "blocker.not_gguf": "Nur Modelle im GGUF-Format können gemessen werden.",
        "blocker.model_unpinned": "Dieses Modell ist nicht auf eine genaue Datei festgelegt und wird nicht benutzt.",
        "blocker.unsafe_filename": "Der Dateiname ist auf diesem Computer nicht verwendbar.",
        "blocker.size_unknown": "Die Größe der Datei ist unbekannt, deshalb wird sie nicht heruntergeladen.",
        "blocker.image_problem": "Ein Programm-Image ist nicht auf eine genaue Version festgelegt und wurde nicht "
                                 "hier gebaut.",
        "blocker.gated_no_access": "Dieses Modell hat Bedingungen, und deine Hugging-Face-Anmeldung hat noch keinen "
                                   "Zugang.",
        "blocker.unknown_repository": "Dieses Modell gibt es auf huggingface.co nicht.",
        "blocker.pack_unknown": "Dieses Paket gibt es nicht.",
        "blocker.artifact_unknown": "Dieses gebaute Modell gibt es nicht.",
        "blocker.snapshot_unknown": "Diese Sicherung der Wissensbasis gibt es nicht.",
        "blocker.trash_unknown": "Diesen Eintrag gibt es im Papierkorb nicht.",
        "blocker.proposal_missing": "Für diesen Build gibt es keinen Reparaturvorschlag.",
        "blocker.file_not_in_repo": "Das Modell hat keine Datei mit diesem Namen.",
        "blocker.url_not_public": "Diese Adresse ist keine öffentliche Website.",
        "blocker.crawl_caps": "Mehr Seiten oder tiefere Links, als deine Einstellungen erlauben.",
        "blocker.setting_invalid": "Dieser Wert ist für die Einstellung nicht erlaubt.",
        "blocker.index_stale": "Die Community-Liste konnte nicht aktualisiert werden.",
        "blocker.ingest_notice_unread": "Du hast den Hinweis zu Deep Ingest noch nicht gelesen.",
        "blocker.crawl_quota": "Das heutige Limit gelesener Seiten ist aufgebraucht.",
        "blocker.crawl_running": "Es läuft schon ein anderes Einlesen von Websites.",
        "blocker.device_busy": "Auf diesem Board wird gerade etwas anderes installiert.",
        "blocker.artifact_busy": "Dieses gebaute Modell wird gerade benutzt.",
        "blocker.device_not_trusted": "Dieses Board ist noch nicht gekoppelt.",
        "blocker.host_key_unknown": "Die Identität dieses Boards ist noch nicht bestätigt.",
        "blocker.device_host_key_changed": "Die Identität dieses Boards hat sich geändert.",
        "blocker.device_host_down": "Das Board antwortet nicht.",
        "blocker.device_port_closed": "Das Board antwortet, lehnt aber die Fernanmeldung ab.",
        "blocker.device_auth_failed": "Das Board hat die Anmeldung abgelehnt.",
        "blocker.toolchain_missing": "Die Build-Werkzeuge sind noch nicht installiert.",
        "blocker.runtime_not_built": "Dittos Modellserver ist noch nicht gebaut.",
        "blocker.model_not_downloaded": "Das Modell ist noch nicht heruntergeladen.",
        "blocker.no_device_runtime": "Das Board hat noch keinen Modellserver.",
        "blocker.no_device_model": "Das Modell ist noch nicht auf dem Board.",
        "blocker.device_model_unset": "Für das Board ist noch kein Modell gewählt.",
        "blocker.setting_loosens": "Nur du kannst diese Änderung an der Einstellung vornehmen.",
        "blocker.no_signing_key": "Du hast noch keinen persönlichen Signaturschlüssel.",
        "blocker.commit_required": "Die genaue Modellversion ließ sich nicht feststellen.",
        "blocker.trash_not_yours": "Du hast das selbst gelöscht, deshalb kannst nur du es wiederherstellen.",
        "blocker.file_outside_folders": "Die Datei liegt außerhalb der Ordner, die du freigegeben hast.",
        "blocker.file_changed": "Die Datei hat sich inzwischen geändert.",
        "blocker.snapshot_no_room": "Die Sicherung der Wissensbasis, die zuerst angelegt wird, passt nicht auf die "
                                    "Festplatte.",
        "blocker.gui_not_open": "Das Framework-Fenster ist nicht offen.",
        # --- was die Person tun kann ---
        "action.free_disk": "Speicherplatz freigeben (alte Builds, den Modell-Cache, den Papierkorb) oder ein "
                            "kleineres Modell wählen.",
        # ein Aufruf, für den der Speicherplatz nicht reicht (Core/disk_plan.person_text: die eine Regel, A4)
        "disk.no_room": "Nicht genug Speicherplatz: {needed} nötig, {free} frei, {reserve} müssen frei bleiben.",
        "disk.no_room.reserved": "Ein laufender Build oder Download hat {reserved} auf dieser Festplatte reserviert: "
                                 "noch einmal versuchen, sobald er fertig ist (seine Reservierung endet mit ihm, auch "
                                 "wenn sein Programm beendet wurde), oder Speicherplatz freigeben.",
        "disk.no_room.trash": "Im Papierkorb liegen {trash} auf dieser Festplatte; ihn zu leeren "
                              "({path:act_file_trash}) gibt sie frei.",
        "action.free_snapshots": "Wissens-Snapshots, die du nicht mehr brauchst, löschen oder exportieren "
                                 "({path:act_knowledge_snapshots}) oder auf dieser Festplatte anders Platz schaffen.",
        "action.offline_off": "Den Offline-Modus ausschalten ({path:act_ditto_settings}) oder ein Modell nehmen, das "
                              "schon auf diesem Computer ist.",
        "action.sandbox_on": "Die Build-Sandbox einschalten: derzeit nur in der Einstellungsdatei; "
                             "{path:act_file_settings} zeigt die Einstellung.",
        "action.decide_module_flags": "Den Build selbst im Builder starten: Er zeigt die CMake-Optionen des Moduls "
                                      "außerhalb der erlaubten Liste, was sie tun, und eine Empfehlung, und du "
                                      "entscheidest.",
        "action.knowledge_on": "Die Wissensbasis einschalten ({path:act_ditto_settings}).",
        "action.remove_target": "Das Hardware-Modul im Community-Hub entfernen.",
        "action.pin_image": "Das Programm-Image mit seiner genauen Version unter "
                            "{path:act_tools_module_wizard} angeben oder ohne Images verpacken.",
        "action.hf_access": "Bei Hugging Face anmelden und die Bedingungen des Modells auf huggingface.co "
                            "akzeptieren, dann die Anmeldung im Framework speichern.",
        "action.update_index": "Die Community-Liste im Community-Hub aktualisieren.",
        "action.ingest_notice": "Den Hinweis zu Deep Ingest lesen ({path:act_tools_mcp_crawl_notice}).",
        "action.pair_device": "Das Board einmal mit seinem Passwort verbinden ({path:btn_boards_deploy}).",
        "action.check_device": "Das Board prüfen und seine Identität neu bestätigen ({path:act_boards_devices}).",
        "action.device_on": "Prüfen, ob das Board eingeschaltet und im selben Netzwerk ist; bei einem Hostnamen wie "
                            "myboard.local die IP-Adresse des Boards versuchen.",
        "action.device_ssh": "Prüfen, ob die Fernanmeldung (SSH) auf dem Board läuft, der Port stimmt und keine "
                             "Firewall sie sperrt.",
        "action.device_login": "Benutzernamen und Passwort prüfen, dann das Board neu verbinden "
                               "({path:btn_boards_deploy}).",
        "action.install_tools": "Die Build-Werkzeuge installieren (das macht jemand mit Administratorrechten) oder in "
                                "einem Container bauen ({path:cb_ditto_runtime_placement}).",
        "action.device_settings": "Das Modell in den Deploy-Einstellungen des Boards wählen.",
        "action.signing_key": "Deine persönlichen Schlüssel im Community-Hub erstellen.",
        "action.commit_required": "Es noch einmal versuchen, wenn Hugging Face antwortet, oder den Build selbst "
                                  "starten.",
        "action.restore_yourself": "Es selbst im Papierkorb wiederherstellen.",
        "action.add_folder": "Den Ordner für das Framework freigeben (Ordner hinzufügen).",
        "action.open_window": "Das Framework-Fenster öffnen.",
        # --- sicherere Wahl ---
        "safer.smaller_model": "ein kleineres Modell",
        "safer.smaller_quantization": "eine kleinere Quantisierung",
        "safer.safetensors": "ein Modell mit safetensors-Dateien",
        "safer.official_image": "ein Image aus Dockers geprüfter Sammlung",
        "safer.sandbox": "in der Sandbox ausführen",
        # --- die Kurzfassung einer Frage ---
        "summary.title": "Kurz gesagt:",
        "summary.what": "„{title}“ ausführen.",
        "summary.what.start_build": "{model} für {target} bauen.",
        "summary.what.start_recipe_build": "{model} für {target} mit einem Community-Rezept bauen.",
        "summary.what.download_hf_file": "Eine Datei von {model} herunterladen.",
        "summary.what.add_pack_from_index": "Ein Paket aus der Community-Liste installieren.",
        "summary.what.export_pack": "Ein Paket erstellen, das mit deinem persönlichen Schlüssel signiert ist.",
        "summary.what.crawl_docs_site": "{start_urls} Websites in die Wissensbasis einlesen.",
        "summary.what.create_deploy_package": "Ein gebautes Modell für ein Board verpacken.",
        "summary.what.deploy_to_device": "Ein Modell auf {device} kopieren und dort installieren.",
        "summary.what.probe_remote_device": "Die Hardware von {device} ansehen.",
        "summary.what.deploy_device_runtime": "Den Modellserver auf {device} installieren.",
        "summary.what.run_device_benchmark": "Den Modellserver auf {device} prüfen und seine Geschwindigkeit messen.",
        "summary.what.build_local_runtime": "Dittos Modellserver für diesen Computer bauen.",
        "summary.what.start_local_runtime": "Dittos Modellserver starten.",
        "summary.what.run_benchmark": "Messen, wie schnell ein Modell auf diesem Computer läuft.",
        "summary.what.create_target_module": "Ein neues Hardware-Modul hinzufügen.",
        "summary.what.update_settings": "Einstellungen des Frameworks ändern.",
        "summary.what.resolve_healing": "Über eine Reparatur für einen fehlgeschlagenen Build entscheiden.",
        "summary.download": "Etwa {gb} GB herunterzuladen.",
        "summary.disk": "Etwa {gb} GB Speicherplatz nötig.",
        "summary.minutes": "Dauert etwa {minutes} Minuten.",
        "summary.needs_you": "Braucht deine Entscheidung, weil: {reasons}",
        "summary.situation": "Abzuwägen: {items}",
        "summary.safer": "Sicherere Wahl: {option}.",
        "summary.only_you": "Nur du kannst das entscheiden.",
        "summary.delegated": "{who} darf das mit deiner Vollmacht „{label}“ selbst entscheiden.",
        "summary.recommends": "{who} empfiehlt (eigene Worte): {note}",
        "summary.you_decide": "Du entscheidest.",
        # --- feste Wörter in einer Warnung (facts_catalog.person_words): was nicht geprüft werden konnte, welche Liste
        "unknown.huggingface": "Hugging Face hat nicht geantwortet",
        "unknown.licence": "keine Lizenz angegeben",
        "unknown.base_model": "das Modell, auf dem es beruht",
        "unknown.weights_format": "das Format der Modelldateien",
        "unknown.other": "etwas anderes",
        "vendor_list.package_sources": "Paketquellen von Herstellern",
        "vendor_list.pip_packages": "Python-Pakete",
        "vendor_list.pinned_downloads": "Hersteller-Downloads",
        "vendor_list.other": "Hersteller-Software",
    },
}
