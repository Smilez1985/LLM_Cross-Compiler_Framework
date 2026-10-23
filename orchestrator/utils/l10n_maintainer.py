#!/usr/bin/env python3
"""
Texts of the maintainer actions of the community repository, English and German (decision 2026-10-22: the
maintainer's work as a few clicks): Tools > Open Community Hub > Maintainer and `llmcf zoo maintainer key|review|
revoke` show the same words. orchestrator/Core/community_maintainer.py reports every step and every refusal as a key of
this table with its values (zoo.maint.step.*, zoo.maint.err.* with its zoo.maint.next.*, zoo.maint.state.*); the CLI
prints the English text, the GUI the text of its language. LocalizationManager merges them (localization.TEXT_MODULES);
a key that exists already is refused there. Every key has the same {placeholders} in both languages
(tests/test_zoo_maintainer.py). This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        # the Maintainer tab of the Community Hub
        "zoo.maint.tab": "Maintainer",
        "zoo.maint.tab.packs": "Packs",
        "zoo.maint.intro": "For the maintainer of the community repository {repo} only. Every change goes to GitHub as "
                           "a pull request under your own GitHub CLI login (gh), and the index is signed with the key "
                           "in this computer's keyring. The framework never reads, stores or shows a token, and no AI "
                           "can start any of this.",
        "zoo.maint.key_name": "Signing key name:",
        "zoo.maint.key_name.hint": "the name you chose at Create signing key, for example lcf-community-2026",
        "zoo.maint.key": "1. Create signing key...",
        "zoo.maint.key.tip": "Once: a new index signing key in this computer's keyring; shows its public entry for "
                             "configs/community_keys.yml and saves it as a text file you choose",
        "zoo.maint.load": "2. Review and publish: load contributions",
        "zoo.maint.load.tip": "The open pull requests of the community repository that add packs, and packs that are "
                              "merged but not yet published",
        "zoo.maint.review": "Review selected",
        "zoo.maint.review.tip": "Fetches the packs of the selected contribution, checks them (zoo verify) and shows "
                                "every file; nothing is merged",
        "zoo.maint.publish": "Publish...",
        "zoo.maint.publish.tip": "After the question: merges the contribution, moves the pack to packs/, adds its "
                                 "index entry, signs the index and merges that change as a pull request",
        "zoo.maint.revoke": "3. Revoke a published pack...",
        "zoo.maint.revoke.tip": "Adds a pack to the revoked list of the signed index, through a pull request",
        "zoo.maint.cancel": "Cancel",
        "zoo.maint.cancel.tip": "Stops before the next step; a merge that has started is not undone",
        "zoo.maint.trust": "Trust:",
        "zoo.maint.title": "Title:",
        "zoo.maint.title.hint": "shown in the hub (one pack only); empty: the pack id",
        "zoo.maint.col.item": "Contribution",
        "zoo.maint.col.title": "Title",
        "zoo.maint.col.author": "Author",
        "zoo.maint.col.packs": "Packs",
        "zoo.maint.col.check": "Check validate",
        "zoo.maint.check.pass": "passed",
        "zoo.maint.check.fail": "failed",
        "zoo.maint.check.pending": "running",
        "zoo.maint.check.none": "not run (approve it in the pull request)",
        "zoo.maint.check.merged": "merged, not published",
        "zoo.maint.files": "Files of the packs",
        "zoo.maint.file": "File",
        "zoo.maint.log": "Progress",
        "zoo.maint.none": "No open contribution adds a pack, and no merged pack waits under contributions/.",
        "zoo.maint.loaded": "{n} contribution(s) with packs.",
        "zoo.maint.choose_first": "Choose a contribution in the list first.",
        "zoo.maint.reviewing": "Fetching and checking the packs of {item}...",
        "zoo.maint.reviewed.ok": "{item}: every pack is acceptable (zoo verify). Read every file before you publish.",
        "zoo.maint.reviewed.bad": "{item} would be rejected:\n{problems}\nIt cannot be published.",
        "zoo.maint.binary": "(not a text file: {size} bytes)",
        "zoo.maint.busy": "Another maintainer action is running; wait for it or cancel it.",
        "zoo.maint.cancelling": "Cancelling before the next step...",
        "zoo.maint.publish.title": "Publish",
        "zoo.maint.publish.ask": "Publish?\n\n{packs}\n\nThis merges {item}, moves the pack(s) to packs/, adds the "
                                 "index entries (trust {trust}), raises the index version, signs it with the key "
                                 "{key} and merges that as a pull request after the check validate passed. Users "
                                 "can install the pack(s) after that.",
        "zoo.maint.publish.no": "Not published. Nothing was changed.",
        "zoo.maint.revoke.title": "Revoke a published pack",
        "zoo.maint.revoke.loading": "Reading the signed index on main...",
        "zoo.maint.revoke.pack": "Pack:",
        "zoo.maint.revoke.scope": "Revoke:",
        "zoo.maint.revoke.scope.file": "exactly this file (sha256)",
        "zoo.maint.revoke.scope.version": "this version",
        "zoo.maint.revoke.scope.all": "every version of this id",
        "zoo.maint.revoke.reason": "Reason:",
        "zoo.maint.revoke.reason.hint": "shown to every user, at most 300 characters",
        "zoo.maint.revoke.no_reason": "Enter the reason; every user sees it.",
        "zoo.maint.revoke.empty": "The signed index lists no pack yet.",
        "zoo.maint.revoke.ask": "Revoke {what}?\n\nReason: {reason}\n\nThis adds it to the revoked list, raises the "
                                "index version, signs it with the key {key} and merges that as a pull request after "
                                "the check validate passed. Installing it is then blocked and installed copies are "
                                "flagged.",
        "zoo.maint.revoke.no": "Not revoked. Nothing was changed.",
        "zoo.maint.key.title": "Create signing key",
        "zoo.maint.key.ask_name": "Name of the new index signing key (not your personal name; it appears in the "
                                  "public entry):",
        "zoo.maint.key.creating": "Creating the signing key {name}...",
        "zoo.maint.key.done": "The signing key {name} (key id {key_id}) is in this computer's keyring. Add this entry "
                              "to configs/community_keys.yml and ship it with a framework release:",
        "zoo.maint.key.save": "Save...",
        "zoo.maint.key.save.title": "Save the public key entry",
        "zoo.maint.key.saved": "Saved: {path}",
        "zoo.maint.key.not_saved": "Not saved: {reason}",
        "zoo.maint.done.title": "Maintainer",
        # the steps (every one is also an audit row)
        "zoo.maint.step.started": "Started: {op}.",
        "zoo.maint.step.tools": "Checking the GitHub CLI and git...",
        "zoo.maint.step.clone": "Cloning {repo} into {folder}...",
        "zoo.maint.step.update": "Updating the local clone of {repo}...",
        "zoo.maint.step.list": "Reading the open pull requests of {repo}...",
        "zoo.maint.step.fetch": "Fetching {path} of {item}...",
        "zoo.maint.step.verify": "Checking {name} (zoo verify)...",
        "zoo.maint.step.check": "The required check {check} on {item}: {state}.",
        "zoo.maint.step.merge_contribution": "Merging the contribution {item} into main...",
        "zoo.maint.step.merged_contribution": "Merged {item}; the pack is under contributions/ on main.",
        "zoo.maint.step.move": "Moving {source} to {target}...",
        "zoo.maint.step.entry": "Index entry: {kind} {id} {version}, trust {trust}, {url}",
        "zoo.maint.step.revocation": "Revocation: {what} ({reason})",
        "zoo.maint.step.version": "Index version {old} -> {new}, time {time}.",
        "zoo.maint.step.sign": "Signed index.json with the key {key_id}.",
        "zoo.maint.step.commit": "Committed on the new branch {branch}.",
        "zoo.maint.step.push": "Pushed the branch {branch} (never main).",
        "zoo.maint.step.pr": "Pull request opened: {url}",
        "zoo.maint.step.wait": "Waiting for the required check {check} on #{pr} ({elapsed} s)...",
        "zoo.maint.step.merge": "Merging #{pr} into main...",
        "zoo.maint.step.published": "Done: index version {version} is on main ({url}). raw.githubusercontent.com can "
                                    "serve the old index for a few minutes; downloading the index again ({refresh}) "
                                    "shows the new one after that.",
        "zoo.maint.step.note_untrusted_key": "Note: the key {key_id} is not in configs/community_keys.yml of this "
                                             "framework. Users accept this index only after a framework release "
                                             "ships that key.",
        "zoo.maint.step.note_no_entry": "Note: the signed index lists no entry that this revocation matches; it is "
                                        "added anyway.",
        "zoo.maint.op.publish": "review and publish {item}",
        "zoo.maint.op.revoke": "revoke {what}",
        "zoo.maint.op.key": "create the signing key {name}",
        # the refusals: a plain reason (err), what happened so far (state) and the next step (next)
        "zoo.maint.next_step": "Next step: {step}",
        "zoo.maint.err.no_repo": "No community repository is set (setting community_repo, owner/name).",
        "zoo.maint.next.no_repo": "set community_repo in {path:act_file_settings} (or config.yml), then try again.",
        "zoo.maint.err.no_gh": "The GitHub CLI (gh) is not installed.",
        "zoo.maint.next.no_gh": "install it from https://cli.github.com, log in with 'gh auth login' (gh keeps its "
                                "own credentials), then try again.",
        "zoo.maint.err.no_git": "git is not installed.",
        "zoo.maint.next.no_git": "install git (https://git-scm.com), then try again.",
        "zoo.maint.err.gh_login": "The GitHub CLI is not logged in.",
        "zoo.maint.next.gh_login": "log in with 'gh auth login' (gh keeps its own credentials), then try again.",
        "zoo.maint.err.offline": "GitHub cannot be reached: {detail}",
        "zoo.maint.next.offline": "check the internet connection, then try again.",
        "zoo.maint.err.tool_failed": "{tool} failed: {detail}",
        "zoo.maint.next.tool_failed": "read the message above; fix what it names (for example the git name and "
                                      "e-mail with 'git config --global user.name', git's login to GitHub with 'gh "
                                      "auth setup-git', or your write access to the repository), then try again.",
        "zoo.maint.err.bad_answer": "The GitHub CLI gave an answer that cannot be read ({what}).",
        "zoo.maint.next.bad_answer": "update the GitHub CLI, then try again.",
        "zoo.maint.err.keyring": "The keyring of this computer cannot be used: {detail}",
        "zoo.maint.next.keyring": "set up the keyring of your operating system (on Linux a Secret Service such as "
                                  "GNOME Keyring or KWallet), then try again.",
        "zoo.maint.err.bad_key_name": "{name} cannot be a key name: use 1 to 64 letters, digits, dots, hyphens or "
                                      "underscores.",
        "zoo.maint.next.bad_key_name": "choose a name like lcf-community-2026.",
        "zoo.maint.err.key_exists": "A key named {name} exists already in this computer's keyring; keys are never "
                                    "replaced.",
        "zoo.maint.next.key_exists": "choose another name.",
        "zoo.maint.err.no_key_name": "No signing key is named.",
        "zoo.maint.next.no_key_name": "give the name you chose at Create signing key (once).",
        "zoo.maint.err.no_key": "There is no signing key named {name} in this computer's keyring (service "
                                "lcf-community-index).",
        "zoo.maint.next.no_key": "check the name, or create the key once with Create signing key.",
        "zoo.maint.err.file_exists": "{path} exists already; it is never replaced.",
        "zoo.maint.next.file_exists": "choose another file name.",
        "zoo.maint.err.audit": "The audit log is not available, so nothing is changed.",
        "zoo.maint.next.audit": "check that it is intact ({verify}), then try again.",
        "zoo.maint.err.clone_damaged": "{folder} is not a git clone of the community repository.",
        "zoo.maint.next.clone_damaged": "move that folder away yourself (nothing is deleted by the framework), then "
                                        "try again; a new clone is made.",
        "zoo.maint.err.clone_dirty": "The local clone {folder} has changes of its own.",
        "zoo.maint.next.clone_dirty": "look at them there with 'git status'; commit, move or remove them yourself "
                                      "(nothing is deleted by the framework), then try again.",
        "zoo.maint.err.not_found": "{item} is neither an open pull request with a pack nor a merged pack waiting "
                                   "under contributions/.",
        "zoo.maint.next.not_found": "list the contributions again ({review}).",
        "zoo.maint.err.no_packs": "{item} adds no pack under contributions/<kind>/.",
        "zoo.maint.next.no_packs": "choose a contribution that adds a pack.",
        "zoo.maint.err.not_a_pack": "{path} is not in {item}.",
        "zoo.maint.next.not_a_pack": "list the contributions again and review the current state.",
        "zoo.maint.err.too_large": "{path} has {size} bytes; the check allows at most {limit}.",
        "zoo.maint.next.too_large": "ask the contributor for a smaller pack.",
        "zoo.maint.err.changed": "{item} changed since it was listed or reviewed (its head is now {head}).",
        "zoo.maint.next.changed": "review it again.",
        "zoo.maint.err.not_open": "#{pr} is not open (state {state}).",
        "zoo.maint.next.not_open": "list the contributions again.",
        "zoo.maint.err.dir_not_empty": "{folder} is not empty.",
        "zoo.maint.next.dir_not_empty": "name a new or empty folder.",
        "zoo.maint.err.rejected": "The review found problems, so nothing is published:\n{problems}",
        "zoo.maint.next.rejected": "ask the contributor for a fixed pack in the pull request.",
        "zoo.maint.err.title_one_pack": "A title applies to a contribution with one pack; {item} has {n}.",
        "zoo.maint.next.title_one_pack": "publish without a title (the pack ids are used).",
        "zoo.maint.err.check_not_passed": "The required check {check} has not passed on #{pr} ({state}).",
        "zoo.maint.next.check_not_passed": "merge only after it passes; for a first-time contributor approve the "
                                           "workflow run in the pull request first.",
        "zoo.maint.err.exists": "{path} exists already on main: a published pack file is never replaced.",
        "zoo.maint.next.exists": "ask the contributor for a new version (it gets a new file name).",
        "zoo.maint.err.entry_exists": "The index lists {what} already.",
        "zoo.maint.next.entry_exists": "ask the contributor for a new version.",
        "zoo.maint.err.revoke_exists": "The index revokes {what} already.",
        "zoo.maint.next.revoke_exists": "nothing to do.",
        "zoo.maint.err.bad_revocation": "This revocation is not valid: {detail}",
        "zoo.maint.next.bad_revocation": "give kind, id and a reason of at most 300 characters (and a version or "
                                         "the sha256 of the file).",
        "zoo.maint.err.no_index": "There is no signed index.json on main yet, so there is nothing to revoke.",
        "zoo.maint.next.no_index": "publish the first pack first.",
        "zoo.maint.err.bad_index": "index.json on main cannot be used: {detail}",
        "zoo.maint.next.bad_index": "look at the history of index.json in the repository before you sign anything "
                                    "on top of it.",
        "zoo.maint.err.version_not_higher": "The version must grow: {new} <= {old}.",
        "zoo.maint.next.version_not_higher": "raise \"version\" in index.json.",
        "zoo.maint.err.merged_differs": "{path} on main is not the file that was reviewed.",
        "zoo.maint.next.merged_differs": "look at the repository's history; review the pack on main again.",
        "zoo.maint.err.check_failed": "The required check {check} failed on {url}.",
        "zoo.maint.next.check_failed": "look at the check in that pull request, then close it on GitHub.",
        "zoo.maint.err.check_timeout": "The required check {check} did not finish within {minutes} minutes on {url}.",
        "zoo.maint.next.check_timeout": "look at the pull request on GitHub; merge it there once the check passed, "
                                        "or close it.",
        "zoo.maint.err.cancelled": "Cancelled.",
        "zoo.maint.err.failed": "{detail}",
        "zoo.maint.state.nothing": "Nothing was changed on GitHub.",
        "zoo.maint.state.merged_waiting": "{item} is merged: its pack waits under contributions/ on main and is "
                                          "listed again in Review and publish (as merged, not published).",
        "zoo.maint.state.branch_pushed": "The branch {branch} was pushed, but no pull request was opened; delete the "
                                         "branch on GitHub or try again.",
        "zoo.maint.state.pr_open": "The pull request {url} stays open: merge it on GitHub once the check passed, or "
                                   "close it.",
        "zoo.maint.state.pr_merged": "The pull request {url} is merged into main.",
        "zoo.maint.state.merging_contribution": "The merge of {item} was started, but its result is not known: look "
                                                "at {item} on GitHub. If it is merged, its pack waits under "
                                                "contributions/ on main and is listed again in Review and publish (as "
                                                "merged, not published).",
        "zoo.maint.state.pushing": "The push of the branch {branch} was started, but its result is not known: look on "
                                   "GitHub whether the branch exists, and delete it there.",
        "zoo.maint.state.merging_pr": "The merge of {url} was started, but its result is not known: look there "
                                      "whether it is merged.",
        "zoo.maint.state.withdrawn": "The pull request {url} of this publication was closed and its branch {branch} "
                                     "deleted: nothing of it was published.",
        "zoo.maint.state.branch_deleted": "The branch {branch} of this publication was deleted (no pull request was "
                                          "open): nothing of it was published.",
        "zoo.maint.state.pr_closed": "The pull request {url} of this publication was closed; its branch {branch} is "
                                     "still on GitHub: delete it there.",
        "zoo.maint.step.pr_closed": "Closed the pull request {url}.",
        "zoo.maint.step.branch_deleted": "Deleted the branch {branch} on GitHub.",
        "zoo.maint.err.not_main": "#{pr} is a pull request into {base}, not into main.",
        "zoo.maint.next.not_main": "ask the contributor to open it against main; only main is published.",
        # a review problem: the pull request changes more than it adds packs
        "zoo.maint.problem.other_change": "The pull request also changes {path} ({change}); only new packs under "
                                          "contributions/<kind>/ are merged here, so it cannot be published from "
                                          "here. Look at it on GitHub and ask the contributor for a pull request "
                                          "that only adds the pack.",
        "zoo.maint.change.A": "added",
        "zoo.maint.change.M": "modified",
        "zoo.maint.change.D": "deleted",
        "zoo.maint.change.T": "type changed",
        "zoo.maint.change.X": "changed",
    },
    "de": {
        "zoo.maint.tab": "Maintainer",
        "zoo.maint.tab.packs": "Packs",
        "zoo.maint.intro": "Nur für den Maintainer des Community-Repositorys {repo}. Jede Änderung geht als Pull "
                           "Request unter deinem eigenen Login der GitHub CLI (gh) zu GitHub, und der Index wird mit "
                           "dem Schlüssel im Schlüsselbund dieses Computers signiert. Das Framework liest, speichert "
                           "und zeigt nie einen Token, und keine KI kann irgendetwas davon starten.",
        "zoo.maint.key_name": "Name des Signaturschlüssels:",
        "zoo.maint.key_name.hint": "der Name, den du bei Signaturschlüssel anlegen gewählt hast, zum Beispiel "
                                   "lcf-community-2026",
        "zoo.maint.key": "1. Signaturschlüssel anlegen...",
        "zoo.maint.key.tip": "Einmalig: ein neuer Signaturschlüssel für den Index im Schlüsselbund dieses Computers; "
                             "zeigt seinen öffentlichen Eintrag für configs/community_keys.yml und speichert ihn als "
                             "Textdatei, die du wählst",
        "zoo.maint.load": "2. Prüfen und veröffentlichen: Beiträge laden",
        "zoo.maint.load.tip": "Die offenen Pull Requests des Community-Repositorys, die Packs hinzufügen, und Packs, "
                              "die gemergt, aber noch nicht veröffentlicht sind",
        "zoo.maint.review": "Auswahl prüfen",
        "zoo.maint.review.tip": "Holt die Packs des gewählten Beitrags, prüft sie (zoo verify) und zeigt jede Datei; "
                                "nichts wird gemergt",
        "zoo.maint.publish": "Veröffentlichen...",
        "zoo.maint.publish.tip": "Nach der Frage: mergt den Beitrag, verschiebt das Pack nach packs/, trägt es in den "
                                 "Index ein, signiert den Index und mergt diese Änderung als Pull Request",
        "zoo.maint.revoke": "3. Veröffentlichtes Pack zurückziehen...",
        "zoo.maint.revoke.tip": "Trägt ein Pack über einen Pull Request in die Liste der zurückgezogenen Packs des "
                                "signierten Index ein",
        "zoo.maint.cancel": "Abbrechen",
        "zoo.maint.cancel.tip": "Hält vor dem nächsten Schritt an; ein begonnener Merge wird nicht rückgängig gemacht",
        "zoo.maint.trust": "Vertrauen:",
        "zoo.maint.title": "Titel:",
        "zoo.maint.title.hint": "im Hub angezeigt (nur bei einem Pack); leer: die ID des Packs",
        "zoo.maint.col.item": "Beitrag",
        "zoo.maint.col.title": "Titel",
        "zoo.maint.col.author": "Autor",
        "zoo.maint.col.packs": "Packs",
        "zoo.maint.col.check": "Prüfung validate",
        "zoo.maint.check.pass": "bestanden",
        "zoo.maint.check.fail": "fehlgeschlagen",
        "zoo.maint.check.pending": "läuft",
        "zoo.maint.check.none": "nicht gelaufen (im Pull Request freigeben)",
        "zoo.maint.check.merged": "gemergt, nicht veröffentlicht",
        "zoo.maint.files": "Dateien der Packs",
        "zoo.maint.file": "Datei",
        "zoo.maint.log": "Fortschritt",
        "zoo.maint.none": "Kein offener Beitrag fügt ein Pack hinzu, und kein gemergtes Pack wartet unter "
                          "contributions/.",
        "zoo.maint.loaded": "{n} Beitrag/Beiträge mit Packs.",
        "zoo.maint.choose_first": "Wähle zuerst einen Beitrag in der Liste.",
        "zoo.maint.reviewing": "Hole und prüfe die Packs von {item}...",
        "zoo.maint.reviewed.ok": "{item}: jedes Pack ist annehmbar (zoo verify). Lies jede Datei, bevor du "
                                 "veröffentlichst.",
        "zoo.maint.reviewed.bad": "{item} würde abgelehnt:\n{problems}\nEs kann nicht veröffentlicht werden.",
        "zoo.maint.binary": "(keine Textdatei: {size} Bytes)",
        "zoo.maint.busy": "Eine andere Maintainer-Aktion läuft; warte darauf oder brich sie ab.",
        "zoo.maint.cancelling": "Breche vor dem nächsten Schritt ab...",
        "zoo.maint.publish.title": "Veröffentlichen",
        "zoo.maint.publish.ask": "Veröffentlichen?\n\n{packs}\n\nDas mergt {item}, verschiebt das Pack/die Packs nach "
                                 "packs/, trägt sie in den Index ein (Vertrauen {trust}), erhöht die Version des "
                                 "Index, signiert ihn mit dem Schlüssel {key} und mergt das als Pull Request, sobald "
                                 "die Prüfung validate bestanden ist. Danach können alle das Pack/die Packs "
                                 "installieren.",
        "zoo.maint.publish.no": "Nicht veröffentlicht. Nichts wurde geändert.",
        "zoo.maint.revoke.title": "Veröffentlichtes Pack zurückziehen",
        "zoo.maint.revoke.loading": "Lese den signierten Index auf main...",
        "zoo.maint.revoke.pack": "Pack:",
        "zoo.maint.revoke.scope": "Zurückziehen:",
        "zoo.maint.revoke.scope.file": "genau diese Datei (sha256)",
        "zoo.maint.revoke.scope.version": "diese Version",
        "zoo.maint.revoke.scope.all": "jede Version dieser ID",
        "zoo.maint.revoke.reason": "Grund:",
        "zoo.maint.revoke.reason.hint": "allen angezeigt, höchstens 300 Zeichen",
        "zoo.maint.revoke.no_reason": "Gib den Grund ein; alle sehen ihn.",
        "zoo.maint.revoke.empty": "Der signierte Index listet noch kein Pack.",
        "zoo.maint.revoke.ask": "{what} zurückziehen?\n\nGrund: {reason}\n\nDas trägt es in die Liste der "
                                "zurückgezogenen Packs ein, erhöht die Version des Index, signiert ihn mit dem "
                                "Schlüssel {key} und mergt das als Pull Request, sobald die Prüfung validate "
                                "bestanden ist. Danach ist die Installation gesperrt, und installierte Kopien werden "
                                "markiert.",
        "zoo.maint.revoke.no": "Nicht zurückgezogen. Nichts wurde geändert.",
        "zoo.maint.key.title": "Signaturschlüssel anlegen",
        "zoo.maint.key.ask_name": "Name des neuen Signaturschlüssels für den Index (nicht dein persönlicher Name; er "
                                  "steht im öffentlichen Eintrag):",
        "zoo.maint.key.creating": "Lege den Signaturschlüssel {name} an...",
        "zoo.maint.key.done": "Der Signaturschlüssel {name} (Key-ID {key_id}) liegt im Schlüsselbund dieses "
                              "Computers. Trage diesen Eintrag in configs/community_keys.yml ein und liefere ihn mit "
                              "einem Framework-Release aus:",
        "zoo.maint.key.save": "Speichern...",
        "zoo.maint.key.save.title": "Öffentlichen Schlüsseleintrag speichern",
        "zoo.maint.key.saved": "Gespeichert: {path}",
        "zoo.maint.key.not_saved": "Nicht gespeichert: {reason}",
        "zoo.maint.done.title": "Maintainer",
        "zoo.maint.step.started": "Gestartet: {op}.",
        "zoo.maint.step.tools": "Prüfe die GitHub CLI und git...",
        "zoo.maint.step.clone": "Klone {repo} nach {folder}...",
        "zoo.maint.step.update": "Aktualisiere den lokalen Klon von {repo}...",
        "zoo.maint.step.list": "Lese die offenen Pull Requests von {repo}...",
        "zoo.maint.step.fetch": "Hole {path} von {item}...",
        "zoo.maint.step.verify": "Prüfe {name} (zoo verify)...",
        "zoo.maint.step.check": "Die Pflichtprüfung {check} auf {item}: {state}.",
        "zoo.maint.step.merge_contribution": "Merge den Beitrag {item} in main...",
        "zoo.maint.step.merged_contribution": "{item} gemergt; das Pack liegt unter contributions/ auf main.",
        "zoo.maint.step.move": "Verschiebe {source} nach {target}...",
        "zoo.maint.step.entry": "Index-Eintrag: {kind} {id} {version}, Vertrauen {trust}, {url}",
        "zoo.maint.step.revocation": "Rückzug: {what} ({reason})",
        "zoo.maint.step.version": "Index-Version {old} -> {new}, Zeit {time}.",
        "zoo.maint.step.sign": "index.json mit dem Schlüssel {key_id} signiert.",
        "zoo.maint.step.commit": "Auf dem neuen Branch {branch} committet.",
        "zoo.maint.step.push": "Branch {branch} gepusht (nie main).",
        "zoo.maint.step.pr": "Pull Request geöffnet: {url}",
        "zoo.maint.step.wait": "Warte auf die Pflichtprüfung {check} auf #{pr} ({elapsed} s)...",
        "zoo.maint.step.merge": "Merge #{pr} in main...",
        "zoo.maint.step.published": "Fertig: Index-Version {version} liegt auf main ({url}). raw.githubusercontent.com "
                                    "kann einige Minuten lang noch den alten Index liefern; den Index erneut laden "
                                    "({refresh}) zeigt danach den neuen.",
        "zoo.maint.step.note_untrusted_key": "Hinweis: Der Schlüssel {key_id} steht nicht in "
                                             "configs/community_keys.yml dieses Frameworks. Alle nehmen diesen Index "
                                             "erst an, wenn ein Framework-Release diesen Schlüssel ausliefert.",
        "zoo.maint.step.note_no_entry": "Hinweis: Der signierte Index listet keinen Eintrag, auf den dieser Rückzug "
                                        "passt; er wird trotzdem eingetragen.",
        "zoo.maint.op.publish": "{item} prüfen und veröffentlichen",
        "zoo.maint.op.revoke": "{what} zurückziehen",
        "zoo.maint.op.key": "den Signaturschlüssel {name} anlegen",
        "zoo.maint.next_step": "Nächster Schritt: {step}",
        "zoo.maint.err.no_repo": "Kein Community-Repository ist eingestellt (Einstellung community_repo, "
                                 "Besitzer/Name).",
        "zoo.maint.next.no_repo": "stelle community_repo unter {path:act_file_settings} (oder in config.yml) ein und "
                                  "versuche es erneut.",
        "zoo.maint.err.no_gh": "Die GitHub CLI (gh) ist nicht installiert.",
        "zoo.maint.next.no_gh": "installiere sie von https://cli.github.com, melde dich mit 'gh auth login' an (gh "
                                "verwaltet seine Zugangsdaten selbst) und versuche es erneut.",
        "zoo.maint.err.no_git": "git ist nicht installiert.",
        "zoo.maint.next.no_git": "installiere git (https://git-scm.com) und versuche es erneut.",
        "zoo.maint.err.gh_login": "Die GitHub CLI ist nicht angemeldet.",
        "zoo.maint.next.gh_login": "melde dich mit 'gh auth login' an (gh verwaltet seine Zugangsdaten selbst) und "
                                   "versuche es erneut.",
        "zoo.maint.err.offline": "GitHub ist nicht erreichbar: {detail}",
        "zoo.maint.next.offline": "prüfe die Internetverbindung und versuche es erneut.",
        "zoo.maint.err.tool_failed": "{tool} ist fehlgeschlagen: {detail}",
        "zoo.maint.next.tool_failed": "lies die Meldung oben und behebe, was sie nennt (zum Beispiel Name und E-Mail "
                                      "für git mit 'git config --global user.name', das Login von git bei GitHub mit "
                                      "'gh auth setup-git' oder deine Schreibrechte am Repository), dann versuche es "
                                      "erneut.",
        "zoo.maint.err.bad_answer": "Die GitHub CLI hat eine Antwort gegeben, die sich nicht lesen lässt ({what}).",
        "zoo.maint.next.bad_answer": "aktualisiere die GitHub CLI und versuche es erneut.",
        "zoo.maint.err.keyring": "Der Schlüsselbund dieses Computers lässt sich nicht nutzen: {detail}",
        "zoo.maint.next.keyring": "richte den Schlüsselbund deines Betriebssystems ein (unter Linux einen Secret "
                                  "Service wie GNOME Keyring oder KWallet) und versuche es erneut.",
        "zoo.maint.err.bad_key_name": "{name} kann kein Schlüsselname sein: nimm 1 bis 64 Buchstaben, Ziffern, "
                                      "Punkte, Bindestriche oder Unterstriche.",
        "zoo.maint.next.bad_key_name": "wähle einen Namen wie lcf-community-2026.",
        "zoo.maint.err.key_exists": "Im Schlüsselbund dieses Computers gibt es schon einen Schlüssel namens {name}; "
                                    "Schlüssel werden nie ersetzt.",
        "zoo.maint.next.key_exists": "wähle einen anderen Namen.",
        "zoo.maint.err.no_key_name": "Kein Signaturschlüssel ist genannt.",
        "zoo.maint.next.no_key_name": "gib den Namen an, den du bei Signaturschlüssel anlegen (einmalig) gewählt hast.",
        "zoo.maint.err.no_key": "Im Schlüsselbund dieses Computers gibt es keinen Signaturschlüssel namens {name} "
                                "(Dienst lcf-community-index).",
        "zoo.maint.next.no_key": "prüfe den Namen, oder lege den Schlüssel einmal mit Signaturschlüssel anlegen an.",
        "zoo.maint.err.file_exists": "{path} gibt es schon; die Datei wird nie ersetzt.",
        "zoo.maint.next.file_exists": "wähle einen anderen Dateinamen.",
        "zoo.maint.err.audit": "Das Audit-Log ist nicht verfügbar, deshalb wird nichts geändert.",
        "zoo.maint.next.audit": "prüfe, ob es unversehrt ist ({verify}), dann versuche es erneut.",
        "zoo.maint.err.clone_damaged": "{folder} ist kein git-Klon des Community-Repositorys.",
        "zoo.maint.next.clone_damaged": "verschiebe diesen Ordner selbst (das Framework löscht nichts) und versuche "
                                        "es erneut; dann wird neu geklont.",
        "zoo.maint.err.clone_dirty": "Der lokale Klon {folder} hat eigene Änderungen.",
        "zoo.maint.next.clone_dirty": "sieh sie dir dort mit 'git status' an; committe, verschiebe oder entferne sie "
                                      "selbst (das Framework löscht nichts), dann versuche es erneut.",
        "zoo.maint.err.not_found": "{item} ist weder ein offener Pull Request mit einem Pack noch ein gemergtes "
                                   "Pack, das unter contributions/ wartet.",
        "zoo.maint.next.not_found": "liste die Beiträge erneut auf ({review}).",
        "zoo.maint.err.no_packs": "{item} fügt kein Pack unter contributions/<kind>/ hinzu.",
        "zoo.maint.next.no_packs": "wähle einen Beitrag, der ein Pack hinzufügt.",
        "zoo.maint.err.not_a_pack": "{path} ist nicht in {item}.",
        "zoo.maint.next.not_a_pack": "liste die Beiträge erneut und prüfe den aktuellen Stand.",
        "zoo.maint.err.too_large": "{path} hat {size} Bytes; die Prüfung erlaubt höchstens {limit}.",
        "zoo.maint.next.too_large": "bitte den Beitragenden um ein kleineres Pack.",
        "zoo.maint.err.changed": "{item} hat sich geändert, seit es gelistet oder geprüft wurde (sein Stand ist jetzt "
                                 "{head}).",
        "zoo.maint.next.changed": "prüfe es erneut.",
        "zoo.maint.err.not_open": "#{pr} ist nicht offen (Status {state}).",
        "zoo.maint.next.not_open": "liste die Beiträge erneut.",
        "zoo.maint.err.dir_not_empty": "{folder} ist nicht leer.",
        "zoo.maint.next.dir_not_empty": "nenne einen neuen oder leeren Ordner.",
        "zoo.maint.err.rejected": "Die Prüfung hat Probleme gefunden, deshalb wird nichts veröffentlicht:\n{problems}",
        "zoo.maint.next.rejected": "bitte den Beitragenden im Pull Request um ein korrigiertes Pack.",
        "zoo.maint.err.title_one_pack": "Ein Titel gilt für einen Beitrag mit einem Pack; {item} hat {n}.",
        "zoo.maint.next.title_one_pack": "veröffentliche ohne Titel (dann gelten die IDs der Packs).",
        "zoo.maint.err.check_not_passed": "Die Pflichtprüfung {check} ist auf #{pr} nicht bestanden ({state}).",
        "zoo.maint.next.check_not_passed": "merge erst, wenn sie bestanden ist; bei Erstbeitragenden gib den "
                                           "Workflow-Lauf zuerst im Pull Request frei.",
        "zoo.maint.err.exists": "{path} gibt es schon auf main: eine veröffentlichte Pack-Datei wird nie ersetzt.",
        "zoo.maint.next.exists": "bitte den Beitragenden um eine neue Version (sie bekommt einen neuen Dateinamen).",
        "zoo.maint.err.entry_exists": "Der Index listet {what} schon.",
        "zoo.maint.next.entry_exists": "bitte den Beitragenden um eine neue Version.",
        "zoo.maint.err.revoke_exists": "Der Index zieht {what} schon zurück.",
        "zoo.maint.next.revoke_exists": "es ist nichts zu tun.",
        "zoo.maint.err.bad_revocation": "Dieser Rückzug ist ungültig: {detail}",
        "zoo.maint.next.bad_revocation": "gib Art, ID und einen Grund mit höchstens 300 Zeichen an (und eine Version "
                                         "oder die sha256 der Datei).",
        "zoo.maint.err.no_index": "Auf main gibt es noch keinen signierten index.json, also nichts zurückzuziehen.",
        "zoo.maint.next.no_index": "veröffentliche zuerst das erste Pack.",
        "zoo.maint.err.bad_index": "index.json auf main lässt sich nicht verwenden: {detail}",
        "zoo.maint.next.bad_index": "sieh dir die Geschichte von index.json im Repository an, bevor du etwas darauf "
                                    "signierst.",
        "zoo.maint.err.version_not_higher": "Die Version muss wachsen: {new} <= {old}.",
        "zoo.maint.next.version_not_higher": "erhöhe \"version\" in index.json.",
        "zoo.maint.err.merged_differs": "{path} auf main ist nicht die Datei, die geprüft wurde.",
        "zoo.maint.next.merged_differs": "sieh dir die Geschichte des Repositorys an; prüfe das Pack auf main erneut.",
        "zoo.maint.err.check_failed": "Die Pflichtprüfung {check} ist auf {url} fehlgeschlagen.",
        "zoo.maint.next.check_failed": "sieh dir die Prüfung in diesem Pull Request an und schließe ihn dann auf "
                                       "GitHub.",
        "zoo.maint.err.check_timeout": "Die Pflichtprüfung {check} ist auf {url} nicht innerhalb von {minutes} Minuten "
                                       "fertig geworden.",
        "zoo.maint.next.check_timeout": "sieh dir den Pull Request auf GitHub an; merge ihn dort, sobald die Prüfung "
                                        "bestanden ist, oder schließe ihn.",
        "zoo.maint.err.cancelled": "Abgebrochen.",
        "zoo.maint.err.failed": "{detail}",
        "zoo.maint.state.nothing": "Auf GitHub wurde nichts geändert.",
        "zoo.maint.state.merged_waiting": "{item} ist gemergt: sein Pack wartet unter contributions/ auf main und "
                                          "steht wieder in Prüfen und veröffentlichen (als gemergt, nicht "
                                          "veröffentlicht).",
        "zoo.maint.state.branch_pushed": "Der Branch {branch} wurde gepusht, aber kein Pull Request geöffnet; lösche "
                                         "den Branch auf GitHub oder versuche es erneut.",
        "zoo.maint.state.pr_open": "Der Pull Request {url} bleibt offen: merge ihn auf GitHub, sobald die Prüfung "
                                   "bestanden ist, oder schließe ihn.",
        "zoo.maint.state.pr_merged": "Der Pull Request {url} ist in main gemergt.",
        "zoo.maint.state.merging_contribution": "Der Merge von {item} wurde gestartet, sein Ergebnis ist aber nicht "
                                                "bekannt: sieh dir {item} auf GitHub an. Ist er gemergt, wartet sein "
                                                "Pack unter contributions/ auf main und steht wieder in Prüfen und "
                                                "veröffentlichen (als gemergt, nicht veröffentlicht).",
        "zoo.maint.state.pushing": "Der Push des Branches {branch} wurde gestartet, sein Ergebnis ist aber nicht "
                                   "bekannt: sieh auf GitHub nach, ob es den Branch gibt, und lösche ihn dort.",
        "zoo.maint.state.merging_pr": "Der Merge von {url} wurde gestartet, sein Ergebnis ist aber nicht bekannt: "
                                      "sieh dort nach, ob er gemergt ist.",
        "zoo.maint.state.withdrawn": "Der Pull Request {url} dieser Veröffentlichung wurde geschlossen und sein Branch "
                                     "{branch} gelöscht: nichts davon wurde veröffentlicht.",
        "zoo.maint.state.branch_deleted": "Der Branch {branch} dieser Veröffentlichung wurde gelöscht (kein Pull "
                                          "Request war offen): nichts davon wurde veröffentlicht.",
        "zoo.maint.state.pr_closed": "Der Pull Request {url} dieser Veröffentlichung wurde geschlossen; sein Branch "
                                     "{branch} ist noch auf GitHub: lösche ihn dort.",
        "zoo.maint.step.pr_closed": "Pull Request {url} geschlossen.",
        "zoo.maint.step.branch_deleted": "Branch {branch} auf GitHub gelöscht.",
        "zoo.maint.err.not_main": "#{pr} ist ein Pull Request nach {base}, nicht nach main.",
        "zoo.maint.next.not_main": "bitte den Beitragenden, ihn gegen main zu öffnen; veröffentlicht wird nur main.",
        "zoo.maint.problem.other_change": "Der Pull Request ändert auch {path} ({change}); hier werden nur neue Packs "
                                          "unter contributions/<kind>/ gemergt, also kann er von hier aus nicht "
                                          "veröffentlicht werden. Sieh ihn dir auf GitHub an und bitte den "
                                          "Beitragenden um einen Pull Request, der nur das Pack hinzufügt.",
        "zoo.maint.change.A": "hinzugefügt",
        "zoo.maint.change.M": "geändert",
        "zoo.maint.change.D": "gelöscht",
        "zoo.maint.change.T": "Typ geändert",
        "zoo.maint.change.X": "geändert",
    },
}


# The framework steps the texts name, as a {placeholder} of the text: filled per audience from the next-step catalogue
# (orchestrator/utils/next_steps), the command at a terminal ("cli": llmcf zoo list --refresh) or the place in the
# window ("gui": its mouse path). A text of the person never writes a command or a menu path itself
# (tests/test_no_command_texts.py).
STEP_PLACES = {"refresh": "hub.refresh", "verify": "audit.verify", "review": "maint.review"}
AUDIENCES = ("cli", "gui")


def places(lang: str = "en", audience: str = "cli") -> dict:
    """The values of STEP_PLACES as `audience` reads them in `lang`."""
    if audience not in AUDIENCES:
        raise ValueError(f"unknown audience {audience!r}")
    from orchestrator.utils import next_steps
    found = {}
    for name, step_id in STEP_PLACES.items():
        step = next_steps.get(step_id)
        found[name] = (next_steps.mouse_path(step.target, lang) if audience == "gui"
                       else next_steps.command_line(step))
    return found


def text(key: str, lang: str = "en", /, audience: str = "cli", **values) -> str:
    """The text of `key` in `lang` (English when it has none) with its {path:<objectName>} places filled (the mouse
    path in `lang`, utils/next_steps), the framework steps it names as `audience` reads them (STEP_PLACES), and then
    its {placeholders}; a missing value stays as its placeholder, so a message is never lost to a formatting error."""
    template = TEXTS.get(lang, {}).get(key) or TEXTS["en"].get(key, key)
    if "{path:" in template:
        from orchestrator.utils.next_steps import fill_paths
        template = fill_paths(template, lang)
    if any("{" + name + "}" in template for name in STEP_PLACES):
        values = {**places(lang, audience), **values}

    class _Keep(dict):
        def __missing__(self, name):
            return "{" + name + "}"
    return template.format_map(_Keep(values))
