#!/usr/bin/env python3
"""
Texts of the Boards menu and its window Boards > Boards and Devices... (W1' of 2026-10-16: every action of `llmcf deploy
devices` and `llmcf device` also by mouse), English and German. LocalizationManager merges them
(localization.TEXT_MODULES); a key that exists already is refused there, so these never replace another text.

Every key has the same {placeholders} in both languages (tests/test_boards_window.py). A text names a place with
{path:<objectName>} (wave 6: the menus are translated). German
terms: "Board", "vertrauen" (trust), "koppeln" (pair), "Laufzeit" (runtime), "Deploy-Einstellungen" (deploy
settings). This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        # the menu and the window
        "boards.menu": "Boards",
        "boards.menu.devices": "Boards and Devices...",
        "boards.menu.bundle": "Board bundle...",
        # Boards > Board bundle... (orchestrator/gui/bundle_window.py; the same steps as `llmcf bundle`)
        "bundle.title": "Board bundle",
        "bundle.intro": "Pick a board, a preset or the building blocks, their variables and files, and look at the "
                        "plan before anything is built or sent. A bundle is signed with the framework's key; its "
                        "installer on the board runs the unprivileged steps; root actions run only through "
                        "llmcf-install, after a second yes for them.",
        "bundle.board": "Board:",
        "bundle.board.none": "No probed board yet: probe one first ({path:act_deploy_scan_remote}).",
        "bundle.preset": "Preset:",
        "bundle.preset.none": "(choose the blocks)",
        "bundle.blocks": "Building blocks:",
        "bundle.block.template": "{title} ({id}, documented template, not buildable yet)",
        "bundle.block.row": "{title} ({id}, {kind}, {trust})",
        "bundle.vars": "Variables (double-click a value to change it; each is checked against its type):",
        "bundle.col.component": "Component",
        "bundle.col.variable": "Variable",
        "bundle.col.type": "Type",
        "bundle.col.value": "Value",
        "bundle.col.role": "File",
        "bundle.col.formats": "Formats",
        "bundle.col.source": "Local file or https address",
        "bundle.col.sha256": "sha256 (online)",
        "bundle.col.size": "Size in bytes (online)",
        "bundle.col.licence": "Licence",
        "bundle.files": "Files (a local file is hashed and copied into the bundle; an online item is pinned):",
        "bundle.var.bad": "{component}.{name}: {reason}",
        "bundle.var.ok": "{component}.{name} = {value}",
        "bundle.unprivileged": "Unprivileged only (no autostart unit, no root action)",
        "bundle.store": "Stored bundles:",
        "bundle.col.bundle": "Bundle",
        "bundle.col.board": "Board",
        "bundle.col.components": "Components",
        "bundle.col.mode": "Mode",
        "bundle.col.bytes": "Size",
        "bundle.col.created": "Created",
        "bundle.col.last": "Last run",
        "bundle.btn.file": "Choose file...",
        "bundle.btn.plan": "Plan",
        "bundle.btn.build": "Build...",
        "bundle.btn.deploy": "Deploy...",
        "bundle.btn.dry_run": "Dry run on the board",
        "bundle.btn.status": "Status on the board",
        "bundle.btn.rollback": "Roll back...",
        "bundle.btn.root_rollback": "Roll back root actions...",
        "bundle.btn.uninstall": "Uninstall...",
        "bundle.btn.delete": "Delete stored bundles...",
        "bundle.btn.block_new": "New block...",
        "bundle.btn.refresh": "Refresh",
        "bundle.btn.cancel": "Cancel",
        "bundle.need_board": "Choose a board first.",
        "bundle.need_bundle": "Choose a stored bundle first.",
        "bundle.need_file_row": "Choose the file row first.",
        "bundle.new.no_folder": "The framework's folder for community blocks is not known here.",
        "bundle.planning": "Planning the bundle",
        "bundle.building": "Building and signing the bundle",
        "bundle.built": "Built bundle {bundle} for board {device_id}. Deploy it from the list of stored bundles.",
        "bundle.not_built": "Nothing was built: the plan has problems.",
        "bundle.own.ask": "Use your own unsigned blocks {blocks}? Nobody else checked them.",
        "bundle.running": "Running the installer on the board ({action})",
        "bundle.ask.apply": "Send bundle {bundle} ({size}) to board {board} and run its installer there? It installs "
                            "the unprivileged steps; root actions stop before they run (they run through "
                            "llmcf-install only after a second yes for them).",
        "bundle.ask.root_apply": "Run the {count} root action(s) of bundle {bundle} on board {board} as root now, "
                                 "through llmcf-install (the framework's signed root helper)? It does only what the "
                                 "signed manifest lists in the output pane and keeps a journal for rollback and "
                                 "uninstall.",
        "bundle.ask.root_uninstall": "Remove what llmcf-install made for bundle {bundle} on board {board} (the {count} "
                                     "entries in the output pane), as root? Only what its journal says it made is "
                                     "removed.",
        "bundle.ask.root_rollback": "Roll back the last run of llmcf-install for bundle {bundle} on board {board}, as "
                                    "root? Its journal says exactly what that run made or changed.",
        "bundle.ask.uninstall": "Uninstall bundle {bundle} from board {board}? The installer the board kept removes "
                                "only the bundle's own steps, newest first.",
        "bundle.ask.rollback": "Roll back the last run of bundle {bundle} on board {board}? The installer the board "
                               "kept undoes the steps of its last apply.",
        "bundle.dry_failed": "The installer's dry run did not pass; nothing was changed.",
        "bundle.revoked": "This bundle is not sent to the board: a building block of it was revoked in the signed "
                          "index since it was built. Build the bundle again without it. ({reason})",
        "bundle.unchanged": "Nothing was changed on the board.",
        "bundle.staging": "The apply did not finish; the next deploy continues in the same upload folder.",
        "bundle.root": "The unprivileged steps are done; the root actions follow through llmcf-install, after its "
                       "plan and your own yes for them.",
        "bundle.root.none": "The installer stopped before the root actions, and this bundle carries no llmcf-install "
                            "(it was built before): build it again, or build the unprivileged-only variant.",
        "bundle.root.none_here": "This bundle has no root actions.",
        "bundle.root.running": "Running llmcf-install on the board as root ({action})",
        "bundle.btn.prepare_root": "Prepare root...",
        "bundle.btn.unprepare_root": "Remove root readiness...",
        "bundle.ask.ready": "Set up the root readiness of board {board} now, as listed in the window? It changes only what is missing (sudo, llmcf-install at its root-owned place, its sudo rule), as root once; nothing else on the board is changed.",
        "bundle.ask.unready": "Remove the root readiness of board {board}? Exactly these files are removed, nothing else:\n{paths}\nBundles with root actions cannot run them there afterwards until it is set up again.",
        "bundle.ready.checking": "Reading what llmcf-install needs on the board ...",
        "bundle.ready.running": "Setting up the board's root readiness ...",
        "bundle.ready.removing": "Removing the board's root readiness ...",
        "bundle.ready.there": "The board's root readiness is there: the root actions run llmcf-install from its root-owned place.",
        "bundle.ready.nothing": "The board holds no root readiness of the framework; nothing to remove.",
        "bundle.ready.status_unread": "The status of the root actions was not read: the board's root readiness for llmcf-install is not set up (Prepare root...).",
        "bundle.ready.cannot": "The board's root readiness cannot be set up here: {reason}.",
        "bundle.ready.failed": "The board's root readiness could not be set up. {reason}",
        "bundle.ready.declined": "Nothing was set up. The root actions of bundle {bundle} cannot run on board {board} until its root readiness is there; set it up with Prepare root... (or deploy again and say yes).",
        "bundle.ready.password_sudo": "The board account's password for sudo, for this setup only (never kept):",
        "bundle.ready.password_su": "The board's root password for su, for this setup only (never kept; the board has no sudo yet):",
        "bundle.root.password_intro": "The board's sudo asks for a password. Type the board account's password for "
                                      "this one run of llmcf-install; it is sent to sudo on the board and never kept.",
        "bundle.root.password": "Board password:",
        "bundle.root.plan_failed": "llmcf-install's plan did not pass; nothing was changed.",
        "bundle.root.unchanged": "Nothing was changed on the board by llmcf-install.",
        "bundle.root.failed": "A root action failed after others were done; llmcf-install's journal says what. Roll "
                              "back root actions... undoes that run.",
        "bundle.delete.title": "Stored board bundles",
        "bundle.delete.intro": "Tick the bundles to delete from this framework's store (nothing is ticked). A board "
                               "keeps what it installed.",
        "bundle.delete.reading": "Reading the stored bundles",
        "bundle.delete.deleting": "Deleting the bundles",
        "bundle.delete.none": "No stored bundle to delete.",
        "bundle.new.title": "New building block",
        "bundle.new.intro": "Makes your own block (block.yml and its guide README.md) in community/blocks/. It is "
                            "not signed: private mode asks before it is used, enterprise mode takes it only once it "
                            "is published and installed from the signed index.",
        "bundle.new.id": "Block id:",
        "bundle.new.kind": "Kind:",
        "bundle.new.title_field": "Title:",
        "bundle.new.summary": "Summary:",
        "bundle.new.from": "Start from:",
        "bundle.new.from.none": "(an empty template)",
        "bundle.new.done": "Made block {id} in {path}. Fill in block.yml and the guide's '## Models' section, then "
                           "publish it in the Community Hub.",
        "boards.title": "Boards and Devices",
        "boards.intro": "Every board you probed or trusted, once. Only the trusted boards may be named by AI tools "
                        "for a deploy, and you still confirm each deploy. Every contact with a board runs in the "
                        "background.",
        "boards.empty": "No board yet: probe one ({path:act_deploy_scan_remote}) or add one you trusted before (Add a "
                        "trusted board...).",
        "boards.col.board": "Board",
        "boards.col.id": "Device ID",
        "boards.col.address": "Address",
        "boards.col.user": "User",
        "boards.col.key": "Host key",
        "boards.col.paired": "Paired",
        "boards.col.trusted": "Trusted since",
        "boards.col.last": "Last deploy",
        "boards.col.system": "System",
        "boards.col.settings": "Deploy settings",
        "boards.col.runtime": "Deployed runtime",
        "boards.col.tier": "Key tier",
        "boards.tier.root": "root-owned key line",
        "boards.tier.dropbear": "Dropbear tier",
        "boards.tier.home": "home gate",
        "boards.tier.password": "no key (password)",
        "boards.tier.unrestricted": "unrestricted key",
        "boards.tier.weaker": "{tier} (weaker)",
        "boards.tier.refused": "{tier} (refused in enterprise mode)",
        "boards.key.known": "{key_type} {fingerprint} (the trusted key)",
        "boards.key.changed": "{key_type} {fingerprint} (CHANGED in known_hosts)",
        "boards.key.unknown": "{key_type} {fingerprint} (not in known_hosts)",
        "boards.not_trusted": "not trusted",
        "boards.not_probed": "not probed",
        "boards.yes": "yes",
        "boards.no": "no",
        "boards.dash": "-",
        "boards.last": "{at} ({result})",
        "boards.last.ok": "ok",
        "boards.last.failed": "failed",
        "boards.system": "{os} {version} {arch}, {libc} {libc_version}, init {init}",
        "boards.system.missing": "probe missing",
        "boards.settings.row": "{install} as {user}",
        "boards.settings.damaged": "damaged; set them again",
        "boards.runtime.row": "{build} ({install}, {at})",
        "boards.btn.add": "Add a trusted board...",
        "boards.btn.edit": "Edit address or key...",
        "boards.btn.remove_trust": "Remove trust...",
        "boards.btn.settings": "Settings...",
        "boards.btn.check": "Check this board (Mojo)",
        "boards.btn.refresh": "Refresh",
        "boards.btn.plan": "Runtime plan",
        "boards.btn.build": "Build runtime...",
        "boards.btn.deploy": "Deploy runtime...",
        "boards.btn.verify": "Verify...",
        "boards.btn.status": "Status",
        "boards.btn.prune": "Remove older runtime packages...",
        "boards.btn.prune_board": "Remove older builds on the board...",
        "boards.btn.cancel": "Cancel",
        "boards.busy": "{what} ... (in the background; the window stays usable)",
        "boards.busy_already": "Another step is running. Wait for it to end or cancel it.",
        "boards.cancelling": "Cancelling ...",
        "boards.cancelled": "Cancelled.",
        "boards.need_selection": "Select a board in the table first.",
        "boards.need_trusted": "This board is not trusted: add it first (Add a trusted board...).",
        "boards.need_stored": "This board has no stored probe: probe it first ({path:act_deploy_scan_remote}).",
        "boards.need_user": "No SSH user is set for this board: set one in Settings...",
        "boards.need_address": "The board has no usable stored address: probe it again ({reason}).",
        "boards.no_pin": "configs/llama_runtime.yml has no llama_cpp.source (the pinned commit).",
        # errors of a board contact
        "boards.err.no_paramiko": "Reaching a board needs the 'paramiko' package.",
        "boards.err.unreachable": "Could not reach {address}: {reason}",
        "boards.err.not_trusted": "{address} is not trusted yet: probe it first ({path:act_deploy_scan_remote}).",
        "boards.err.other_device": "{address} answers with the host key of another board than {device_id}; probe it "
                                   "again before you deploy to it.",
        "boards.err.login_failed": "The login to {user} at {address} failed: {reason}",
        "boards.err.pair": "Pair the board once with its password (Deploy runtime... asks for it) or with "
                           "{path:act_deploy_scan_remote}",
        "boards.err.key_install": "The framework's key could not be installed on the board: {reason}",
        "boards.err.switched": "The board now runs OpenSSH on port {port} with the root-owned key line, and the "
                               "framework reaches it there from now on, as device {device_id} (its data moved "
                               "there). This step ran over Dropbear and stopped: run it again.",
        "boards.err.weaker_tier": "Enterprise mode refuses to use this board: the framework's key there sits on a "
                                  "weaker tier (the Dropbear tier or the home gate), which enterprise mode counts as "
                                  "too insecure. Nothing was done on the board.\n\nWays on: scan the board again "
                                  "with its password ({path:act_deploy_scan_remote}) and choose the switch to "
                                  "OpenSSH (the board account needs sudo) or password login; or switch the "
                                  "framework to private mode ({path:act_file_settings}).",
        "boards.err.failed": "The step failed: {reason}",
        # add a trusted board (deploy devices add)
        "boards.add.title": "Add a trusted board",
        "boards.add.intro": "Adds a board whose host key is already in the framework's known_hosts, because you "
                            "trusted it before (for example with a deploy or a remote scan). Nothing is trusted here "
                            "that was not trusted before.",
        "boards.add.host": "Address or host name:",
        "boards.add.user": "User on the board:",
        "boards.add.port": "SSH port:",
        "boards.add.label": "Your name for it (optional):",
        "boards.add.bad_entry": "Enter an address or a host name (e.g. 192.168.1.50, fe80::1 without brackets or "
                                "myboard.local) and an account name.",
        "boards.add.bad_label": "The name has 1 to 64 printable characters.",
        "boards.add.reading": "Reading the board's host key",
        "boards.add.not_known": "{address} is not trusted yet (its host key is {status}). Trust it first with "
                                "{path:btn_deploy_run} or {path:act_deploy_scan_remote}, which show the fingerprint "
                                "for you to compare.",
        "boards.add.question": "{address} presents the trusted key {key_type} {fingerprint}.\n\nAllow AI tools to "
                               "deploy to {user} at {host} (each deploy is still confirmed by you)? The trust is "
                               "written to the audit log.",
        "boards.add.nothing": "Nothing was added.",
        "boards.add.pairing": "Testing whether the framework's key logs in",
        "boards.add.done": "Trusted board {device_id} ({label}): AI tools may deploy to it with your confirmation.",
        "boards.add.not_paired": "The framework's SSH key does not log in there yet: pair the board once with "
                                 "{path:btn_deploy_run} (it asks for the password one time).",
        "boards.add.key_gone": "The trusted key is not in the framework's known_hosts.",
        "boards.add.failed": "The board record could not be written.",
        # remove trust (deploy devices remove)
        "boards.remove.title": "Remove trust",
        "boards.remove.question": "Remove the trust of this board?\n\nBoard: {label}\nDevice ID: {device_id}\n"
                                  "Address: {address}\nUser: {user}\nHost key: {key_type} {fingerprint}\n\nAI tools "
                                  "can then no longer name this board for a deploy. Its entry in the framework's "
                                  "known_hosts, its probe and its deploy settings stay. The removal is written to the "
                                  "audit log.",
        "boards.remove.done": "Board {label} ({device_id}) was removed from the trusted boards.",
        "boards.remove.gone": "There is no trusted board {device_id} any more.",
        # a trusted board's address and host key (deploy devices edit, Q5)
        "boards.edit.title": "Edit address or key",
        "boards.edit.intro": "Board {label} ({device_id}): change its host name or IP address, its SSH port or its "
                             "host key in the framework's known_hosts, for example after its address changed or it "
                             "was reinstalled. Paste the new key (copy the line of /etc/ssh/ssh_host_ed25519_key.pub "
                             "on the board) or let the framework read it from the board. Empty: the key stays.",
        "boards.edit.host": "Host name or IP address:",
        "boards.edit.port": "SSH port:",
        "boards.edit.key": "New host key (paste):",
        "boards.edit.key_hint": "Paste the board's host key line here, e.g. ssh-ed25519 AAAA... from "
                                "/etc/ssh/ssh_host_ed25519_key.pub. Empty: the key stays.",
        "boards.edit.read": "Read the key from the board (at the address above) instead",
        "boards.edit.checking": "Checking the change",
        "boards.edit.reading": "Reading the board's host key",
        "boards.edit.question": "Change board {label} ({device_id}) like this?",
        "boards.edit.now": "Now",
        "boards.edit.after": "After the change",
        "boards.edit.row.address": "Address:",
        "boards.edit.row.key": "Host key:",
        "boards.edit.row.device": "Device ID:",
        "boards.edit.displaced": "The framework's known_hosts holds another {key_type} key for {address} "
                                 "({fingerprint}); it is replaced too.",
        "boards.edit.new_id": "The board's device ID becomes {new_id}: it derives from the host key. Its data moves "
                              "there from {old_id} (its stored probe, deploy settings, deployed runtime, deploy record "
                              "and benchmark results, 'tested on'): each part is copied and checked first, and the "
                              "old copies go only after the change.",
        "boards.edit.compare": "Compare the new fingerprint with the board itself (ssh-keygen -lf "
                               "/etc/ssh/ssh_host_ed25519_key.pub there). Your answer is written to the audit log.",
        "boards.edit.nothing": "Nothing was changed.",
        "boards.edit.changing": "Changing the board's address and key",
        "boards.edit.done": "Board {label} ({device_id}): {address}, host key {key_type} {fingerprint}.",
        "boards.edit.probe_again": "If the board was set up anew, probe it again ({path:act_deploy_scan_remote}) so "
                                   "its stored system is current.",
        "boards.edit.moved": "Moved to the new device ID {new_id}: {what}.",
        "boards.edit.kept": "Kept under {old_id}, because {new_id} has its own: {what}.",
        "boards.edit.left": "Could not be removed under {old_id} (the copies under {new_id} are complete): {what}.",
        "boards.edit.data.probe": "stored probe",
        "boards.edit.data.deploy_settings": "deploy settings",
        "boards.edit.data.runtime": "deployed runtime",
        "boards.edit.data.deploy_record": "deploy record",
        "boards.edit.data.tested_on": "benchmark results ('tested on')",
        "boards.edit.record_failed": "The framework's known_hosts holds the change, but the board's record could not "
                                     "be written: add the board again (Add a trusted board...).",
        "boards.edit.err.no_board": "This board is not trusted any more; the list is read again.",
        "boards.edit.err.address": "Enter an address or a host name (e.g. 192.168.1.50, fe80::1 without brackets or "
                                   "myboard.local) and a port from 1 to 65535.",
        "boards.edit.err.unchanged": "Nothing would change: the board already has this address and this host key.",
        "boards.edit.err.no_key": "The framework's known_hosts no longer holds the board's trusted key for its "
                                  "address: paste the board's host key or let the framework read it from the board.",
        "boards.edit.err.stale": "The framework's known_hosts changed meanwhile; nothing was changed. Open Edit address "
                                 "or key... again.",
        "boards.edit.err.audit": "The change could not be written to the audit log; nothing was changed.",
        "boards.edit.err.not_moved": "The board's data could not be copied to its new device ID; nothing was "
                                     "changed. Check the free space and the rights of the framework's folder and try "
                                     "again.",
        "boards.edit.err.other_board": "The new host key is the key of the trusted board {label} ({device_id}); nothing "
                                       "was changed. Change or remove that board instead.",
        "boards.edit.err.key_empty": "No host key was pasted.",
        "boards.edit.err.key_several": "The pasted text holds more than one line or more than one key: paste the one "
                                       "key line.",
        "boards.edit.err.key_not_a_key": "The pasted text is not an SSH host key line (for example the line of "
                                         "/etc/ssh/ssh_host_ed25519_key.pub on the board).",
        "boards.edit.err.key_no_paramiko": "Reading a host key needs the 'paramiko' package.",
        # a change that something on this computer stopped (the CLI prints the same local_problem reason)
        "boards.edit.err.lock": "The framework's known_hosts is being changed by another part of the framework (another "
                                "window, the command line or an MCP server); nothing was changed. Try again when it has "
                                "finished.",
        "boards.edit.err.local": "What failed on this computer: {detail}. The table shows the board's record now.",
        "boards.edit.err.stopped": "The change stopped: {reason}. The table shows the board's record now.",
        # the framework's own SSH settings (round 25 Q4)
        "boards.btn.ssh": "SSH settings...",
        "boards.ssh.title": "The framework's SSH settings",
        "boards.ssh.intro": "Every connection the framework makes with this computer's OpenSSH client (the deploy "
                            "without paramiko) reads only this file ({file}), never your ~/.ssh/config or "
                            "/etc/ssh/ssh_config; connections over paramiko (when it is installed) do not use it, so a "
                            "board whose settings here name a ProxyJump or ProxyCommand always goes through OpenSSH. "
                            "Add "
                            "what a board needs, for example a ProxyJump for a board behind a jump host. The "
                            "framework's own trust settings (its known_hosts, its key) always win over this file.",
        "boards.ssh.file": "The file now:",
        "boards.ssh.host": "Board (address or host name):",
        "boards.ssh.lines": "Its settings, one per line (e.g. ProxyJump jump.example):",
        "boards.ssh.import": "Import from ~/.ssh/config",
        "boards.ssh.imported": "~/.ssh/config holds for {host}:\n{lines}\n\nThe lines are in the field now; nothing "
                               "is written before you confirm.",
        "boards.ssh.refused": "Nothing was written: the framework never uses these lines (its own trust settings stay "
                              "on OpenSSH's command line).\n\n{problems}",
        "boards.ssh.why.trust": "{line}: it would change how a board's host key is checked or which machine is "
                                "reached; the framework's own known_hosts decides that.",
        "boards.ssh.why.command": "{line}: it would run a command or load code on this computer or the board.",
        "boards.ssh.why.files": "{line}: it would read another configuration file (such as ~/.ssh/config), which "
                                "this file keeps out.",
        "boards.ssh.why.reuse": "{line}: it would reuse or forward the connection; the framework opens its own "
                                "connection each time.",
        "boards.ssh.why.session": "{line}: it would change how the framework's commands run on the board.",
        "boards.ssh.why.keys": "{line}: it would choose, load or hand out login keys other than the framework's own "
                               "key.",
        "boards.ssh.why.syntax": "{line}: this is no 'Option value' line of a board's settings.",
        "boards.ssh.why.jump_path": "{line}: OpenSSH hands the path of this file to the jump host's ssh without "
                                    "quotes, and the framework's folder has a space, a '%' or another sign a shell "
                                    "reads in its path, so a ProxyJump cannot work from there. Write the jump as a "
                                    "ProxyCommand instead, for example: ProxyCommand ssh -W [%h]:%p jump.example.",
        "boards.ssh.what.proxyjump": "{line}: connects to the board through another computer (the jump host). "
                                     "OpenSSH first logs in there with this file's settings for the jump host and its "
                                     "defaults: the jump host's key is checked against your own ~/.ssh/known_hosts and "
                                     "your own SSH keys log in there. The board's own host key is still checked "
                                     "against the framework's known_hosts.",
        "boards.ssh.what.proxycommand": "{line}: runs this command on this computer for every connection to the "
                                        "board, and the connection goes through it. The board's own host key is still "
                                        "checked against the framework's known_hosts.",
        "boards.ssh.knowingly": "These lines connect through another computer or run a command:\n\n{lines}\n\nAdd "
                                "them anyway?",
        "boards.ssh.question": "Write these settings for {host}?\n\nNow: {old}\nAfter the change: {new}",
        "boards.ssh.none": "no settings",
        "boards.ssh.done": "Written to the framework's SSH settings: {host}.",
        "boards.ssh.unchanged": "These are the settings already; nothing was changed.",
        "boards.ssh.nothing": "Nothing was changed.",
        "boards.ssh.err.ssh_host": "Enter a host name, an address or a pattern OpenSSH reads (letters, digits, '.', "
                                   "'-', ':', '_' and the patterns '*', '?', '!').",
        "boards.ssh.err.ssh_stale": "The file changed meanwhile; nothing was changed. Open SSH settings... again.",
        "boards.ssh.err.ssh_import_none": "~/.ssh/config has no Host block for {host}.",
        "boards.ssh.err.ssh_import_unreadable": "~/.ssh/config cannot be read: {reason}",
        "boards.ssh.err.file": "The framework's SSH settings cannot be read or written: {reason}",
        # deploy settings (device settings)
        "boards.settings.title": "Deploy settings of {label}",
        "boards.settings.current": "Current settings\nInstall: {install}\nSSH user: {user}\nPort: {port}\nModel for "
                                   "the systemd unit: {model}\nKV cache: {kv}",
        "boards.settings.install_user": "user (start script in the user's home)",
        "boards.settings.install_systemd": "systemd (systemd unit; sudo confirmed {at})",
        "boards.settings.not_set": "not set",
        "boards.settings.problems": "This cannot be deployed yet:",
        "boards.settings.install": "Install:",
        "boards.settings.opt_user": "Start script in the user's home (user)",
        "boards.settings.opt_systemd": "systemd unit, written with sudo (systemd)",
        "boards.settings.user": "SSH user (never root):",
        "boards.settings.port": "Port of the server (1024-65535):",
        "boards.settings.model": "Model for the systemd unit:",
        "boards.settings.model_hint": "path on the board, e.g. models/model.gguf (relative: in the home folder)",
        "boards.settings.kv": "KV cache:",
        "boards.settings.kv_auto": "Automatic: {kind} ({why})",
        "boards.settings.sudo_question": "The systemd unit is written to /etc/systemd/system and enabled with sudo on "
                                         "the device (sudo -n: it must work without a password, the deployment never "
                                         "asks). Allow deployments to this device to run these sudo commands? Your "
                                         "answer is written to the audit log.",
        "boards.settings.no_systemd": "This board does not run systemd (its probe reports {init}); the start script "
                                      "is the way to run the runtime there. The settings were not changed.",
        "boards.settings.no_init": "no init system",
        "boards.settings.sudo_declined": "The systemd unit needs your confirmation that deployments may use sudo on "
                                         "the board; the settings were not changed.",
        "boards.settings.consent_failed": "The consent could not be written to the audit log ({reason}); nothing was "
                                          "changed.",
        "boards.settings.not_saved": "Could not save the deploy settings: {reason}",
        "boards.settings.bad_value": "This value cannot be used: {reason}",
        "boards.settings.damaged_text": "The deploy settings of this board cannot be used: {reason}",
        # the CLI's words (person_steps.DAMAGED_QUESTION)
        "boards.settings.damaged_question": "The deploy settings of device {device_id} cannot be used ({reason}). Set "
                                            "them again, starting from the defaults? The damaged file {path} ({size}) "
                                            "is overwritten with the new settings; the board's probe, its trust and "
                                            "its runtime stay as they are.",
        "boards.settings.saved": "Deploy settings of {label} saved.",
        "boards.settings.unchanged": "Nothing was changed.",
        # runtime plan and status (device runtime plan|status)
        "boards.plan.title": "Runtime plan of {label}",
        "boards.plan.text": "Board: {os}/{arch}, {libc} {libc_version}\nBuilder: container {base} ({blibc} "
                            "{blibc_version}, GCC {gcc}), without network\nCPU: {cpu}, OpenMP {openmp}, C++ runtime "
                            "linked statically: {static}\nPrograms: {programs}\nBuild ID: {build_id}",
        "boards.plan.cpu_all": "all variants",
        "boards.plan.cpu_x86": "x86 features of the probe",
        "boards.plan.features_used": "CPU features used: {features}",
        "boards.plan.features_base": "none beyond the base level",
        "boards.plan.feature_unusable": "The board has {feature}, the build cannot use it: {reason}",
        "boards.plan.built": "Built: yes, {path}",
        "boards.plan.not_built": "Built: no (Build runtime...)",
        "boards.plan.none": "No runtime can be planned for this board: {reason}",
        "boards.status.title": "Runtime status of {label}",
        "boards.status.planned": "Planned build {build} ({built})",
        "boards.status.built": "built",
        "boards.status.not_built": "not built yet",
        "boards.status.nothing": "Deployed: nothing yet",
        "boards.status.deployed": "Deployed: build {build} on {at} ({install}), checks on the board {checks} "
                                  "({verified})",
        "boards.status.passed": "passed",
        "boards.status.failed": "failed",
        "boards.status.kv": "KV cache at that deployment: {kv}",
        "boards.status.old": "The deployed build is not the one planned now (the probe, the pin or the recipe "
                             "changed): build and deploy again.",
        # build (device runtime build)
        "boards.build.title": "Build the runtime of {label}",
        "boards.build.intro": "Cross-builds the pinned llama.cpp for this board in the builder container (without "
                              "network) and packages it. Cancel stops after the current step; a build that runs in "
                              "the container runs to its end.",
        "boards.build.jobs": "Parallel compile jobs (0: automatic):",
        "boards.build.mirror": "Source mirror (optional):",
        "boards.build.mirror_hint": "URL of a mirror of the llama.cpp repository",
        "boards.build.checking": "Looking for the pinned source",
        "boards.build.source_question": "The pinned llama.cpp source ({tag}, commit {commit}, about 215 MB) is not "
                                        "cached yet. Fetch it now?",
        "boards.build.source_needed": "The build needs the pinned llama.cpp source; nothing was built. On a computer "
                                      "without internet, import the source first.",
        "boards.build.running": "Building the runtime",
        "boards.build.fetching": "Fetching the pinned llama.cpp source",
        "boards.build.building": "Building llama.cpp for {label} in the builder container",
        "boards.build.stored": "Build {build} for this board is already stored; packaging it.",
        "boards.build.done": "Board runtime ready: {path}\nsha256 {sha256}",
        "boards.build.older": "{n} older runtime package(s) of this board are kept; the framework deletes none by "
                              "itself (Remove older runtime packages... lists them).",
        "boards.build.cancelled": "The build was cancelled; nothing was packaged.",
        "boards.build.failed": "The build failed: {reason}",
        "boards.build.logs": "Logs: {path}",
        # older packages on this computer (device runtime prune)
        "boards.prune.title": "Runtime packages of {label}",
        "boards.prune.none": "No older runtime packages of this board: the one of the build deployed last is never "
                             "listed.",
        "boards.prune.intro": "The older runtime packages of this board on this computer (the one of the build "
                              "deployed last is never listed). Tick what you want to delete; nothing is ticked.",
        "boards.prune.reading": "Reading the runtime packages of this board",
        "boards.prune.deleting": "Deleting the ticked runtime packages",
        # older builds on the board (device runtime prune --on-device)
        "boards.prune_board.title": "Runtime builds on {label}",
        "boards.prune_board.reading": "Reading the builds on the board",
        "boards.prune_board.busy": "Not listed, because a running program uses them: {builds}.",
        "boards.prune_board.none": "No older runtime builds on this board to remove: the active build is never "
                                   "listed.",
        "boards.prune_board.intro": "The older runtime builds on the board (never the active one, never one a running "
                                    "program uses). A board has no trash: what you tick is removed for good. Nothing "
                                    "is ticked.",
        "boards.prune_board.removing": "Removing the ticked builds on the board",
        "boards.prune_board.freed": "{size} freed on the board.",
        # deploy (device runtime deploy)
        "boards.deploy.title": "Deploy the runtime to {label}",
        "boards.deploy.intro": "Installs the built runtime on the board as its deploy settings say, checks the new "
                               "build there and activates it only when it passes. Every older build stays on the "
                               "board.",
        "boards.deploy.user": "SSH user:",
        "boards.deploy.password": "Password (only for a board that is not paired yet):",
        "boards.deploy.password_hint": "used once to install the framework's key, never stored",
        "boards.bench.model": "Model for a short benchmark (optional):",
        "boards.bench.hint": "path on the board, e.g. models/model.gguf (relative: in the home folder)",
        # asked only after the board named the model's sha256 and it is not a catalog model: the CLI's words
        # (person_steps.UNTRUSTED_QUESTION)
        "boards.untrusted_question": "{path} is not a catalog model, so it is untrusted. llama-bench loads it "
                                     "natively on the device as {user} (no sandbox). Run the short benchmark with "
                                     "it? Your answer is written to the audit log.",
        "boards.deploy.not_built": "The runtime for this board is not built yet (or the board's probe changed): "
                                   "Build runtime... first.",
        "boards.deploy.needs_source": "The tarball must be packaged again, which needs the pinned source (its "
                                      "licence text): Build runtime... first.",
        "boards.deploy.problems": "Nothing was deployed; change the deploy settings first (Settings...):",
        "boards.deploy.root": "The SSH user on the board is root: the runtime never runs as root. Deploy as an "
                              "unprivileged user (Settings...).",
        "boards.deploy.preparing": "Checking the build and its package",
        "boards.deploy.running": "Deploying to the board",
        "boards.deploy.done": "Deployed build {build} to {root}.",
        "boards.deploy.previous": "The previous build {previous} is kept.",
        "boards.deploy.failed": "Build {build} did not pass its checks on the board; it was not activated.",
        "boards.deploy.server_user": "The server listens on 127.0.0.1:{port}; its key is in "
                                     "~/.config/llm-framework/llama-server.key. stop, status and serve (in the "
                                     "foreground) work the same way as the start command above.",
        "boards.deploy.server_systemd": "systemd unit: {state}; the server listens on 127.0.0.1:{port}; its key is in "
                                        "~/.config/llm-framework/llama-server.key.",
        "boards.deploy.unit_started": "started",
        "boards.deploy.unit_enabled": "enabled",
        "boards.checks": "Checks on the board:",
        "boards.check_line": "{name}: {result} - {detail}",
        "boards.check.passed": "passed",
        "boards.check.failed": "failed",
        "boards.check.skipped": "skipped",
        "boards.bench.missing": "No readable model at {path} on the board; the short benchmark is skipped.",
        "boards.kv": "KV cache: {kv}",
        # verify (device runtime verify)
        "boards.verify.title": "Verify the runtime on {label}",
        "boards.verify.intro": "Checks the deployed runtime on the board again as the unprivileged user (never with "
                               "sudo); with a model, also a short benchmark.",
        "boards.verify.nothing": "Nothing was deployed to this board yet (Deploy runtime...).",
        "boards.verify.root": "The SSH user is root; the checks run as the unprivileged user only.",
        "boards.verify.running": "Checking on the board",
        "boards.verify.result": "Board {device_id}, build {build}:",
        # Mojo's check of one board
        "boards.check.running": "Checking the board (one connection to its SSH port, no login)",
    },
    "de": {
        "boards.menu": "Boards",
        "boards.menu.devices": "Boards und Geräte...",
        "boards.menu.bundle": "Board-Bündel...",
        "bundle.title": "Board-Bündel",
        "bundle.intro": "Wähle ein Board, eine Vorlage oder die Bausteine, ihre Variablen und Dateien, und sieh dir "
                        "den Plan an, bevor etwas gebaut oder gesendet wird. Ein Bündel wird mit dem Schlüssel des "
                        "Frameworks signiert; sein Installer auf dem Board führt die Schritte ohne Sonderrechte aus, "
                        "Root-Aktionen laufen nur über llmcf-install, nach einem zweiten Ja dafür.",
        "bundle.board": "Board:",
        "bundle.board.none": "Noch kein geprüftes Board: prüfe zuerst eins ({path:act_deploy_scan_remote}).",
        "bundle.preset": "Vorlage:",
        "bundle.preset.none": "(Bausteine selbst wählen)",
        "bundle.blocks": "Bausteine:",
        "bundle.block.template": "{title} ({id}, dokumentierte Vorlage, noch nicht baubar)",
        "bundle.block.row": "{title} ({id}, {kind}, {trust})",
        "bundle.vars": "Variablen (Doppelklick auf einen Wert ändert ihn; jeder wird gegen seinen Typ geprüft):",
        "bundle.col.component": "Komponente",
        "bundle.col.variable": "Variable",
        "bundle.col.type": "Typ",
        "bundle.col.value": "Wert",
        "bundle.col.role": "Datei",
        "bundle.col.formats": "Formate",
        "bundle.col.source": "Lokale Datei oder https-Adresse",
        "bundle.col.sha256": "sha256 (online)",
        "bundle.col.size": "Größe in Bytes (online)",
        "bundle.col.licence": "Lizenz",
        "bundle.files": "Dateien (eine lokale Datei wird gehasht und ins Bündel kopiert; ein Online-Element ist "
                        "festgelegt):",
        "bundle.var.bad": "{component}.{name}: {reason}",
        "bundle.var.ok": "{component}.{name} = {value}",
        "bundle.unprivileged": "Nur ohne Sonderrechte (keine Autostart-Unit, keine Root-Aktion)",
        "bundle.store": "Gespeicherte Bündel:",
        "bundle.col.bundle": "Bündel",
        "bundle.col.board": "Board",
        "bundle.col.components": "Komponenten",
        "bundle.col.mode": "Modus",
        "bundle.col.bytes": "Größe",
        "bundle.col.created": "Erstellt",
        "bundle.col.last": "Letzter Lauf",
        "bundle.btn.file": "Datei wählen...",
        "bundle.btn.plan": "Planen",
        "bundle.btn.build": "Bauen...",
        "bundle.btn.deploy": "Ausrollen...",
        "bundle.btn.dry_run": "Probelauf auf dem Board",
        "bundle.btn.status": "Status auf dem Board",
        "bundle.btn.rollback": "Zurückrollen...",
        "bundle.btn.root_rollback": "Root-Aktionen zurückrollen...",
        "bundle.btn.uninstall": "Deinstallieren...",
        "bundle.btn.delete": "Gespeicherte Bündel löschen...",
        "bundle.btn.block_new": "Neuer Baustein...",
        "bundle.btn.refresh": "Aktualisieren",
        "bundle.btn.cancel": "Abbrechen",
        "bundle.need_board": "Wähle zuerst ein Board.",
        "bundle.need_bundle": "Wähle zuerst ein gespeichertes Bündel.",
        "bundle.need_file_row": "Wähle zuerst die Zeile der Datei.",
        "bundle.new.no_folder": "Der Ordner des Frameworks für Community-Bausteine ist hier nicht bekannt.",
        "bundle.planning": "Das Bündel wird geplant",
        "bundle.building": "Das Bündel wird gebaut und signiert",
        "bundle.built": "Bündel {bundle} für Board {device_id} gebaut. Rolle es aus der Liste der gespeicherten "
                        "Bündel aus.",
        "bundle.not_built": "Nichts wurde gebaut: der Plan hat Probleme.",
        "bundle.own.ask": "Deine eigenen, nicht signierten Bausteine {blocks} verwenden? Niemand sonst hat sie "
                          "geprüft.",
        "bundle.running": "Der Installer läuft auf dem Board ({action})",
        "bundle.ask.apply": "Bündel {bundle} ({size}) an Board {board} senden und seinen Installer dort ausführen? Er "
                            "installiert die Schritte ohne Sonderrechte; vor Root-Aktionen hält er an (sie laufen "
                            "über llmcf-install erst nach einem zweiten Ja dafür).",
        "bundle.ask.root_apply": "Die {count} Root-Aktion(en) von Bündel {bundle} auf Board {board} jetzt als root "
                                 "ausführen, über llmcf-install (den signierten Root-Helfer des Frameworks)? Er tut "
                                 "nur, was das signierte Manifest in der Ausgabe auflistet, und führt ein Journal für "
                                 "Zurückrollen und Deinstallieren.",
        "bundle.ask.root_uninstall": "Entfernen, was llmcf-install für Bündel {bundle} auf Board {board} angelegt hat "
                                     "(die {count} Einträge in der Ausgabe), als root? Entfernt wird nur, was laut "
                                     "seinem Journal von ihm stammt.",
        "bundle.ask.root_rollback": "Den letzten Lauf von llmcf-install für Bündel {bundle} auf Board {board} "
                                    "zurückrollen, als root? Sein Journal sagt genau, was dieser Lauf angelegt oder "
                                    "geändert hat.",
        "bundle.ask.uninstall": "Bündel {bundle} von Board {board} deinstallieren? Der Installer, den das Board "
                                "behalten hat, entfernt nur die eigenen Schritte des Bündels, die neuesten zuerst.",
        "bundle.ask.rollback": "Den letzten Lauf von Bündel {bundle} auf Board {board} zurückrollen? Der Installer, "
                               "den das Board behalten hat, macht die Schritte seines letzten Apply rückgängig.",
        "bundle.dry_failed": "Der Probelauf des Installers hat nicht bestanden; nichts wurde geändert.",
        "bundle.revoked": "Dieses Bündel wird nicht an das Board gesendet: einer seiner Bausteine wurde seit dem "
                          "Bauen im signierten Index zurückgezogen. Baue das Bündel ohne ihn neu. ({reason})",
        "bundle.unchanged": "Auf dem Board wurde nichts geändert.",
        "bundle.staging": "Das Apply wurde nicht fertig; das nächste Ausrollen macht im selben Upload-Ordner weiter.",
        "bundle.root": "Die Schritte ohne Sonderrechte sind fertig; die Root-Aktionen folgen über llmcf-install, "
                       "nach seinem Plan und deinem eigenen Ja dafür.",
        "bundle.root.none": "Der Installer hat vor den Root-Aktionen angehalten, und dieses Bündel enthält kein "
                            "llmcf-install (es wurde früher gebaut): baue es neu, oder baue die Variante ohne "
                            "Sonderrechte.",
        "bundle.root.none_here": "Dieses Bündel hat keine Root-Aktionen.",
        "bundle.root.running": "llmcf-install läuft auf dem Board als root ({action})",
        "bundle.btn.prepare_root": "Root vorbereiten...",
        "bundle.btn.unprepare_root": "Root-Bereitschaft entfernen...",
        "bundle.ask.ready": "Die Root-Bereitschaft von Board {board} jetzt einrichten, wie im Fenster aufgelistet? Sie ändert nur, was fehlt (sudo, llmcf-install an seinem Ort in Root-Besitz, seine sudo-Regel), einmal als root; sonst wird auf dem Board nichts geändert.",
        "bundle.ask.unready": "Die Root-Bereitschaft von Board {board} entfernen? Genau diese Dateien werden entfernt, sonst nichts:\n{paths}\nBundles mit Root-Aktionen können sie dort danach erst wieder ausführen, wenn sie neu eingerichtet ist.",
        "bundle.ready.checking": "Lese, was llmcf-install auf dem Board braucht ...",
        "bundle.ready.running": "Richte die Root-Bereitschaft des Boards ein ...",
        "bundle.ready.removing": "Entferne die Root-Bereitschaft des Boards ...",
        "bundle.ready.there": "Die Root-Bereitschaft des Boards ist da: Die Root-Aktionen führen llmcf-install von seinem Ort in Root-Besitz aus.",
        "bundle.ready.nothing": "Das Board hat keine Root-Bereitschaft des Frameworks; es gibt nichts zu entfernen.",
        "bundle.ready.status_unread": "Der Status der Root-Aktionen wurde nicht gelesen: Die Root-Bereitschaft des Boards für llmcf-install ist nicht eingerichtet (Root vorbereiten...).",
        "bundle.ready.cannot": "Die Root-Bereitschaft des Boards lässt sich hier nicht einrichten: {reason}.",
        "bundle.ready.failed": "Die Root-Bereitschaft des Boards ließ sich nicht einrichten. {reason}",
        "bundle.ready.declined": "Nichts wurde eingerichtet. Die Root-Aktionen von Bundle {bundle} können auf Board {board} erst laufen, wenn seine Root-Bereitschaft da ist; richte sie mit Root vorbereiten... ein (oder deploye erneut und sag ja).",
        "bundle.ready.password_sudo": "Das Passwort des Board-Kontos für sudo, nur für diese Einrichtung (wird nie gespeichert):",
        "bundle.ready.password_su": "Das root-Passwort des Boards für su, nur für diese Einrichtung (wird nie gespeichert; das Board hat noch kein sudo):",
        "bundle.root.password_intro": "sudo auf dem Board fragt nach einem Passwort. Gib das Passwort des "
                                      "Board-Kontos für diesen einen Lauf von llmcf-install ein; es geht an sudo auf "
                                      "dem Board und wird nie gespeichert.",
        "bundle.root.password": "Board-Passwort:",
        "bundle.root.plan_failed": "Der Plan von llmcf-install hat nicht bestanden; nichts wurde geändert.",
        "bundle.root.unchanged": "llmcf-install hat auf dem Board nichts geändert.",
        "bundle.root.failed": "Eine Root-Aktion ist fehlgeschlagen, nachdem andere schon erledigt waren; das Journal "
                              "von llmcf-install sagt, welche. Root-Aktionen zurückrollen... macht diesen Lauf "
                              "rückgängig.",
        "bundle.delete.title": "Gespeicherte Board-Bündel",
        "bundle.delete.intro": "Hake die Bündel an, die aus dem Speicher dieses Frameworks gelöscht werden (nichts "
                               "ist angehakt). Ein Board behält, was es installiert hat.",
        "bundle.delete.reading": "Die gespeicherten Bündel werden gelesen",
        "bundle.delete.deleting": "Die Bündel werden gelöscht",
        "bundle.delete.none": "Kein gespeichertes Bündel zum Löschen.",
        "bundle.new.title": "Neuer Baustein",
        "bundle.new.intro": "Legt deinen eigenen Baustein (block.yml und seine Anleitung README.md) in "
                            "community/blocks/ an. Er ist nicht signiert: der private Modus fragt, bevor er verwendet "
                            "wird, der Enterprise-Modus nimmt ihn erst, wenn er veröffentlicht und aus dem signierten "
                            "Index installiert ist.",
        "bundle.new.id": "Baustein-ID:",
        "bundle.new.kind": "Art:",
        "bundle.new.title_field": "Titel:",
        "bundle.new.summary": "Zusammenfassung:",
        "bundle.new.from": "Ausgehend von:",
        "bundle.new.from.none": "(eine leere Vorlage)",
        "bundle.new.done": "Baustein {id} in {path} angelegt. Fülle block.yml und den Abschnitt '## Models' der "
                           "Anleitung aus und veröffentliche ihn dann im Community Hub.",
        "boards.title": "Boards und Geräte",
        "boards.intro": "Jedes Board, das du geprüft oder dem du vertraut hast, einmal. Nur vertraute Boards dürfen "
                        "KI-Werkzeuge für ein Deployment nennen, und du bestätigst jedes Deployment. Jeder Kontakt "
                        "mit einem Board läuft im Hintergrund.",
        "boards.empty": "Noch kein Board: prüfe eines ({path:act_deploy_scan_remote}) oder füge eines hinzu, dem du "
                        "früher vertraut hast (Vertrautes Board hinzufügen...).",
        "boards.col.board": "Board",
        "boards.col.id": "Geräte-ID",
        "boards.col.address": "Adresse",
        "boards.col.user": "Benutzer",
        "boards.col.key": "Host-Schlüssel",
        "boards.col.paired": "Gekoppelt",
        "boards.col.trusted": "Vertraut seit",
        "boards.col.last": "Letztes Deployment",
        "boards.col.system": "System",
        "boards.col.settings": "Deploy-Einstellungen",
        "boards.col.runtime": "Installierte Laufzeit",
        "boards.col.tier": "Schlüsselstufe",
        "boards.tier.root": "root-eigene Schlüsselzeile",
        "boards.tier.dropbear": "Dropbear-Stufe",
        "boards.tier.home": "Home-Gate",
        "boards.tier.password": "kein Schlüssel (Passwort)",
        "boards.tier.unrestricted": "uneingeschränkter Schlüssel",
        "boards.tier.weaker": "{tier} (schwächer)",
        "boards.tier.refused": "{tier} (im Enterprise-Modus abgelehnt)",
        "boards.key.known": "{key_type} {fingerprint} (der vertraute Schlüssel)",
        "boards.key.changed": "{key_type} {fingerprint} (in known_hosts GEÄNDERT)",
        "boards.key.unknown": "{key_type} {fingerprint} (nicht in known_hosts)",
        "boards.not_trusted": "nicht vertraut",
        "boards.not_probed": "nicht geprüft",
        "boards.yes": "ja",
        "boards.no": "nein",
        "boards.dash": "-",
        "boards.last": "{at} ({result})",
        "boards.last.ok": "ok",
        "boards.last.failed": "fehlgeschlagen",
        "boards.system": "{os} {version} {arch}, {libc} {libc_version}, Init {init}",
        "boards.system.missing": "Prüfprofil fehlt",
        "boards.settings.row": "{install} als {user}",
        "boards.settings.damaged": "beschädigt; neu setzen",
        "boards.runtime.row": "{build} ({install}, {at})",
        "boards.btn.add": "Vertrautes Board hinzufügen...",
        "boards.btn.edit": "Adresse oder Schlüssel ändern...",
        "boards.btn.remove_trust": "Vertrauen entfernen...",
        "boards.btn.settings": "Einstellungen...",
        "boards.btn.check": "Dieses Board prüfen (Mojo)",
        "boards.btn.refresh": "Aktualisieren",
        "boards.btn.plan": "Laufzeit-Plan",
        "boards.btn.build": "Laufzeit bauen...",
        "boards.btn.deploy": "Laufzeit installieren...",
        "boards.btn.verify": "Prüfen...",
        "boards.btn.status": "Status",
        "boards.btn.prune": "Ältere Laufzeit-Pakete entfernen...",
        "boards.btn.prune_board": "Ältere Builds auf dem Board entfernen...",
        "boards.btn.cancel": "Abbrechen",
        "boards.busy": "{what} ... (im Hintergrund; das Fenster bleibt bedienbar)",
        "boards.busy_already": "Ein anderer Schritt läuft. Warte, bis er fertig ist, oder brich ihn ab.",
        "boards.cancelling": "Wird abgebrochen ...",
        "boards.cancelled": "Abgebrochen.",
        "boards.need_selection": "Wähle zuerst ein Board in der Tabelle.",
        "boards.need_trusted": "Diesem Board wird nicht vertraut: füge es zuerst hinzu (Vertrautes Board "
                               "hinzufügen...).",
        "boards.need_stored": "Für dieses Board gibt es kein gespeichertes Prüfprofil: prüfe es zuerst "
                              "({path:act_deploy_scan_remote}).",
        "boards.need_user": "Für dieses Board ist kein SSH-Benutzer gesetzt: setze einen unter Einstellungen...",
        "boards.need_address": "Das Board hat keine brauchbare gespeicherte Adresse: prüfe es erneut ({reason}).",
        "boards.no_pin": "configs/llama_runtime.yml hat keine llama_cpp.source (den festgelegten Commit).",
        "boards.err.no_paramiko": "Um ein Board zu erreichen, wird das Paket 'paramiko' gebraucht.",
        "boards.err.unreachable": "{address} ist nicht erreichbar: {reason}",
        "boards.err.not_trusted": "{address} ist noch nicht vertraut: prüfe es zuerst ({path:act_deploy_scan_remote}).",
        "boards.err.other_device": "{address} antwortet mit dem Host-Schlüssel eines anderen Boards als {device_id}; "
                                   "prüfe es erneut, bevor du darauf installierst.",
        "boards.err.login_failed": "Die Anmeldung als {user} bei {address} ist fehlgeschlagen: {reason}",
        "boards.err.pair": "Kopple das Board einmal mit seinem Passwort (Laufzeit installieren... fragt danach) oder "
                           "mit {path:act_deploy_scan_remote}",
        "boards.err.key_install": "Der Schlüssel des Frameworks konnte nicht auf dem Board eingerichtet werden: "
                                  "{reason}",
        "boards.err.switched": "Das Board läuft jetzt mit OpenSSH auf Port {port} und der Schlüsselzeile in "
                               "Root-Besitz, und das Framework erreicht es ab jetzt dort, als Gerät {device_id} "
                               "(seine Daten sind dorthin umgezogen). Dieser Schritt lief über Dropbear und hat "
                               "angehalten: Starte ihn erneut.",
        "boards.err.weaker_tier": "Der Enterprise-Modus lehnt es ab, dieses Board zu benutzen: Der Schlüssel des "
                                  "Frameworks liegt dort auf einer schwächeren Stufe (der Dropbear-Stufe oder dem "
                                  "Home-Gate), die der Enterprise-Modus als zu unsicher wertet. Auf dem Board ist "
                                  "nichts geschehen.\n\nWege weiter: Scanne das Board erneut mit seinem Passwort "
                                  "({path:act_deploy_scan_remote}) und wähle die Umstellung auf OpenSSH (das Konto "
                                  "braucht sudo) oder die Anmeldung mit Passwort; oder stelle das Framework auf den "
                                  "privaten Modus um ({path:act_file_settings}).",
        "boards.err.failed": "Der Schritt ist fehlgeschlagen: {reason}",
        "boards.add.title": "Vertrautes Board hinzufügen",
        "boards.add.intro": "Fügt ein Board hinzu, dessen Host-Schlüssel schon in der known_hosts-Datei des "
                            "Frameworks steht, weil du ihm früher vertraut hast (zum Beispiel bei einem Deployment "
                            "oder einem Remote-Scan). Hier wird nichts vertraut, was nicht schon vorher vertraut war.",
        "boards.add.host": "Adresse oder Hostname:",
        "boards.add.user": "Benutzer auf dem Board:",
        "boards.add.port": "SSH-Port:",
        "boards.add.label": "Dein Name dafür (optional):",
        "boards.add.bad_entry": "Gib eine Adresse oder einen Hostnamen ein (z. B. 192.168.1.50, fe80::1 ohne Klammern "
                                "oder myboard.local) und einen Kontonamen.",
        "boards.add.bad_label": "Der Name hat 1 bis 64 druckbare Zeichen.",
        "boards.add.reading": "Host-Schlüssel des Boards wird gelesen",
        "boards.add.not_known": "{address} ist noch nicht vertraut (sein Host-Schlüssel ist {status}). Vertraue ihm "
                                "zuerst mit {path:btn_deploy_run} oder {path:act_deploy_scan_remote}; dort siehst du "
                                "den Fingerabdruck zum Vergleichen.",
        "boards.add.question": "{address} zeigt den vertrauten Schlüssel {key_type} {fingerprint}.\n\nDürfen "
                               "KI-Werkzeuge auf {user} bei {host} installieren (jedes Deployment bestätigst du "
                               "weiterhin selbst)? Das Vertrauen wird im Audit-Log vermerkt.",
        "boards.add.nothing": "Es wurde nichts hinzugefügt.",
        "boards.add.pairing": "Es wird geprüft, ob der Schlüssel des Frameworks sich anmeldet",
        "boards.add.done": "Vertrautes Board {device_id} ({label}): KI-Werkzeuge dürfen mit deiner Bestätigung darauf "
                           "installieren.",
        "boards.add.not_paired": "Der SSH-Schlüssel des Frameworks meldet sich dort noch nicht an: kopple das Board "
                                 "einmal mit {path:btn_deploy_run} (es fragt einmal nach dem Passwort).",
        "boards.add.key_gone": "Der vertraute Schlüssel steht nicht in der known_hosts-Datei des Frameworks.",
        "boards.add.failed": "Der Eintrag des Boards konnte nicht geschrieben werden.",
        "boards.remove.title": "Vertrauen entfernen",
        "boards.remove.question": "Das Vertrauen zu diesem Board entfernen?\n\nBoard: {label}\nGeräte-ID: "
                                  "{device_id}\nAdresse: {address}\nBenutzer: {user}\nHost-Schlüssel: {key_type} "
                                  "{fingerprint}\n\nKI-Werkzeuge können dieses Board dann nicht mehr für ein "
                                  "Deployment nennen. Sein Eintrag in der known_hosts-Datei des Frameworks, sein "
                                  "Prüfprofil und seine Deploy-Einstellungen bleiben. Das Entfernen wird im Audit-Log "
                                  "vermerkt.",
        "boards.remove.done": "Board {label} ({device_id}) wurde aus den vertrauten Boards entfernt.",
        "boards.remove.gone": "Es gibt kein vertrautes Board {device_id} mehr.",
        "boards.edit.title": "Adresse oder Schlüssel ändern",
        "boards.edit.intro": "Board {label} ({device_id}): ändere seinen Hostnamen oder seine IP-Adresse, seinen "
                             "SSH-Port oder seinen Host-Schlüssel in der known_hosts-Datei des Frameworks, zum "
                             "Beispiel nachdem sich seine Adresse geändert hat oder es neu installiert wurde. Füge den "
                             "neuen Schlüssel ein (kopiere die Zeile aus /etc/ssh/ssh_host_ed25519_key.pub auf dem "
                             "Board) oder lass das Framework ihn vom Board lesen. Leer: der Schlüssel bleibt.",
        "boards.edit.host": "Hostname oder IP-Adresse:",
        "boards.edit.port": "SSH-Port:",
        "boards.edit.key": "Neuer Host-Schlüssel (einfügen):",
        "boards.edit.key_hint": "Füge hier die Zeile des Host-Schlüssels ein, z. B. ssh-ed25519 AAAA... aus "
                                "/etc/ssh/ssh_host_ed25519_key.pub. Leer: der Schlüssel bleibt.",
        "boards.edit.read": "Stattdessen den Schlüssel vom Board lesen (unter der Adresse oben)",
        "boards.edit.checking": "Die Änderung wird geprüft",
        "boards.edit.reading": "Host-Schlüssel des Boards wird gelesen",
        "boards.edit.question": "Board {label} ({device_id}) so ändern?",
        "boards.edit.now": "Jetzt",
        "boards.edit.after": "Nach der Änderung",
        "boards.edit.row.address": "Adresse:",
        "boards.edit.row.key": "Host-Schlüssel:",
        "boards.edit.row.device": "Geräte-ID:",
        "boards.edit.displaced": "Die known_hosts-Datei des Frameworks hält für {address} einen anderen "
                                 "{key_type}-Schlüssel ({fingerprint}); er wird ebenfalls ersetzt.",
        "boards.edit.new_id": "Die Geräte-ID des Boards wird {new_id}: sie leitet sich vom Host-Schlüssel ab. Seine "
                              "Daten ziehen von {old_id} dorthin (seine gespeicherte Prüfung, die "
                              "Deploy-Einstellungen, die deployte Laufzeit, der Deploy-Eintrag und die "
                              "Benchmark-Ergebnisse, \"getestet auf\"): Jeder Teil wird zuerst kopiert und geprüft, und "
                              "die alten Kopien gehen erst nach der Änderung.",
        "boards.edit.compare": "Vergleiche den neuen Fingerabdruck mit dem Board selbst (dort ssh-keygen -lf "
                               "/etc/ssh/ssh_host_ed25519_key.pub). Deine Antwort wird ins Audit-Log geschrieben.",
        "boards.edit.nothing": "Es wurde nichts geändert.",
        "boards.edit.changing": "Adresse und Schlüssel des Boards werden geändert",
        "boards.edit.done": "Board {label} ({device_id}): {address}, Host-Schlüssel {key_type} {fingerprint}.",
        "boards.edit.probe_again": "Wurde das Board neu eingerichtet, prüfe es erneut ({path:act_deploy_scan_remote}), "
                                   "damit sein gespeichertes System aktuell ist.",
        "boards.edit.moved": "Zur neuen Geräte-ID {new_id} verschoben: {what}.",
        "boards.edit.kept": "Unter {old_id} behalten, weil {new_id} eigene hat: {what}.",
        "boards.edit.left": "Unter {old_id} nicht entfernbar (die Kopien unter {new_id} sind vollständig): {what}.",
        "boards.edit.data.probe": "gespeicherte Prüfung",
        "boards.edit.data.deploy_settings": "Deploy-Einstellungen",
        "boards.edit.data.runtime": "deployte Laufzeit",
        "boards.edit.data.deploy_record": "Deploy-Eintrag",
        "boards.edit.data.tested_on": "Benchmark-Ergebnisse (\"getestet auf\")",
        "boards.edit.record_failed": "Die known_hosts-Datei des Frameworks enthält die Änderung, aber der Eintrag des "
                                     "Boards konnte nicht geschrieben werden: füge das Board erneut hinzu (Vertrautes "
                                     "Board hinzufügen...).",
        "boards.edit.err.no_board": "Dieses Board ist nicht mehr vertraut; die Liste wird neu gelesen.",
        "boards.edit.err.address": "Gib eine Adresse oder einen Hostnamen ein (z. B. 192.168.1.50, fe80::1 ohne "
                                   "Klammern oder myboard.local) und einen Port von 1 bis 65535.",
        "boards.edit.err.unchanged": "Es würde sich nichts ändern: das Board hat schon diese Adresse und diesen "
                                     "Host-Schlüssel.",
        "boards.edit.err.no_key": "Die known_hosts-Datei des Frameworks hält den vertrauten Schlüssel des Boards für "
                                  "seine Adresse nicht mehr: füge den Host-Schlüssel des Boards ein oder lass das "
                                  "Framework ihn vom Board lesen.",
        "boards.edit.err.stale": "Die known_hosts-Datei des Frameworks hat sich inzwischen geändert; es wurde nichts "
                                 "geändert. Öffne Adresse oder Schlüssel ändern... erneut.",
        "boards.edit.err.audit": "Die Änderung konnte nicht ins Audit-Log geschrieben werden; es wurde nichts "
                                 "geändert.",
        "boards.edit.err.not_moved": "Die Daten des Boards konnten nicht zu seiner neuen Geräte-ID kopiert werden; es "
                                     "wurde nichts geändert. Prüfe den freien Platz und die Rechte im Ordner des "
                                     "Frameworks und versuche es erneut.",
        "boards.edit.err.other_board": "Der neue Host-Schlüssel ist der Schlüssel des vertrauten Boards {label} "
                                       "({device_id}); es wurde nichts geändert. Ändere oder entferne stattdessen "
                                       "dieses Board.",
        "boards.edit.err.key_empty": "Es wurde kein Host-Schlüssel eingefügt.",
        "boards.edit.err.key_several": "Der eingefügte Text hat mehr als eine Zeile oder mehr als einen Schlüssel: füge "
                                       "die eine Schlüsselzeile ein.",
        "boards.edit.err.key_not_a_key": "Der eingefügte Text ist keine SSH-Host-Schlüsselzeile (zum Beispiel die "
                                         "Zeile aus /etc/ssh/ssh_host_ed25519_key.pub auf dem Board).",
        "boards.edit.err.key_no_paramiko": "Um einen Host-Schlüssel zu lesen, wird das Paket 'paramiko' gebraucht.",
        "boards.edit.err.lock": "Die known_hosts des Frameworks wird gerade von einem anderen Teil des Frameworks "
                                "geändert (einem anderen Fenster, der Kommandozeile oder einem MCP-Server); es wurde "
                                "nichts geändert. Versuche es erneut, wenn das fertig ist.",
        "boards.edit.err.local": "Was auf diesem Computer fehlschlug: {detail}. Die Tabelle zeigt jetzt den Eintrag "
                                 "des Boards.",
        "boards.edit.err.stopped": "Die Änderung wurde abgebrochen: {reason}. Die Tabelle zeigt jetzt den Eintrag des "
                                   "Boards.",
        "boards.btn.ssh": "SSH-Einstellungen...",
        "boards.ssh.title": "SSH-Einstellungen des Frameworks",
        "boards.ssh.intro": "Jede Verbindung, die das Framework mit dem OpenSSH-Client dieses Computers aufbaut "
                            "(das Deploy ohne paramiko), liest nur diese Datei ({file}), nie deine ~/.ssh/config oder "
                            "/etc/ssh/ssh_config; Verbindungen über paramiko (wenn es installiert ist) nutzen sie "
                            "nicht, deshalb geht ein Board, dessen Einstellungen hier einen ProxyJump oder ein "
                            "ProxyCommand nennen, immer über OpenSSH. Trage ein, was ein Board braucht, zum Beispiel einen ProxyJump für ein Board "
                            "hinter einem Jump-Host. Die eigenen Vertrauens-Einstellungen des Frameworks (seine "
                            "known_hosts, sein Schlüssel) gelten immer vor dieser Datei.",
        "boards.ssh.file": "Die Datei jetzt:",
        "boards.ssh.host": "Board (Adresse oder Hostname):",
        "boards.ssh.lines": "Seine Einstellungen, eine pro Zeile (z. B. ProxyJump jump.example):",
        "boards.ssh.import": "Aus ~/.ssh/config übernehmen",
        "boards.ssh.imported": "~/.ssh/config enthält für {host}:\n{lines}\n\nDie Zeilen stehen jetzt im Feld; "
                               "geschrieben wird erst, wenn du bestätigst.",
        "boards.ssh.refused": "Es wurde nichts geschrieben: das Framework verwendet diese Zeilen nie (seine eigenen "
                              "Vertrauens-Einstellungen bleiben auf der Kommandozeile von OpenSSH).\n\n{problems}",
        "boards.ssh.why.trust": "{line}: würde ändern, wie der Host-Schlüssel eines Boards geprüft oder welcher "
                                "Rechner erreicht wird; das entscheidet die eigene known_hosts des Frameworks.",
        "boards.ssh.why.command": "{line}: würde einen Befehl ausführen oder Code laden, auf diesem Computer oder dem "
                                  "Board.",
        "boards.ssh.why.files": "{line}: würde eine andere Konfigurationsdatei lesen (etwa ~/.ssh/config), die diese "
                                "Datei gerade fernhält.",
        "boards.ssh.why.reuse": "{line}: würde die Verbindung wiederverwenden oder weiterleiten; das Framework öffnet "
                                "jedes Mal seine eigene Verbindung.",
        "boards.ssh.why.session": "{line}: würde ändern, wie die Befehle des Frameworks auf dem Board laufen.",
        "boards.ssh.why.keys": "{line}: würde andere Anmeldeschlüssel als den eigenen Schlüssel des Frameworks "
                               "wählen, laden oder weitergeben.",
        "boards.ssh.why.syntax": "{line}: das ist keine Zeile 'Option Wert' für die Einstellungen eines Boards.",
        "boards.ssh.why.jump_path": "{line}: OpenSSH gibt den Pfad dieser Datei ohne Anführungszeichen an das ssh des "
                                    "Jump-Hosts weiter, und der Ordner des Frameworks hat ein Leerzeichen, ein '%' "
                                    "oder ein anderes Zeichen in seinem Pfad, das eine Shell liest; ein ProxyJump kann "
                                    "von dort nicht funktionieren. Schreibe den Sprung stattdessen als ProxyCommand, "
                                    "zum Beispiel: ProxyCommand ssh -W [%h]:%p jump.example.",
        "boards.ssh.what.proxyjump": "{line}: verbindet über einen anderen Rechner (den Jump-Host) mit dem Board. "
                                     "OpenSSH meldet sich zuerst dort an, mit den Einstellungen dieser Datei für den "
                                     "Jump-Host und seinen Standardwerten: der Schlüssel des Jump-Hosts wird gegen "
                                     "deine eigene ~/.ssh/known_hosts geprüft, und deine eigenen SSH-Schlüssel melden "
                                     "sich dort an. Der Host-Schlüssel des Boards selbst wird weiter gegen die "
                                     "known_hosts des Frameworks geprüft.",
        "boards.ssh.what.proxycommand": "{line}: führt diesen Befehl bei jeder Verbindung zum Board auf diesem "
                                        "Computer aus, und die Verbindung läuft durch ihn. Der Host-Schlüssel des "
                                        "Boards selbst wird weiter gegen die known_hosts des Frameworks geprüft.",
        "boards.ssh.knowingly": "Diese Zeilen verbinden über einen anderen Rechner oder führen einen Befehl "
                                "aus:\n\n{lines}\n\nTrotzdem hinzufügen?",
        "boards.ssh.question": "Diese Einstellungen für {host} schreiben?\n\nJetzt: {old}\nNach der Änderung: {new}",
        "boards.ssh.none": "keine Einstellungen",
        "boards.ssh.done": "In die SSH-Einstellungen des Frameworks geschrieben: {host}.",
        "boards.ssh.unchanged": "Das sind schon die Einstellungen; es wurde nichts geändert.",
        "boards.ssh.nothing": "Es wurde nichts geändert.",
        "boards.ssh.err.ssh_host": "Gib einen Hostnamen, eine Adresse oder ein Muster ein, das OpenSSH liest "
                                   "(Buchstaben, Ziffern, '.', '-', ':', '_' und die Muster '*', '?', '!').",
        "boards.ssh.err.ssh_stale": "Die Datei hat sich inzwischen geändert; es wurde nichts geändert. Öffne "
                                    "SSH-Einstellungen... erneut.",
        "boards.ssh.err.ssh_import_none": "~/.ssh/config hat keinen Host-Block für {host}.",
        "boards.ssh.err.ssh_import_unreadable": "~/.ssh/config kann nicht gelesen werden: {reason}",
        "boards.ssh.err.file": "Die SSH-Einstellungen des Frameworks können nicht gelesen oder geschrieben werden: "
                               "{reason}",
        "boards.settings.title": "Deploy-Einstellungen von {label}",
        "boards.settings.current": "Aktuelle Einstellungen\nInstallation: {install}\nSSH-Benutzer: {user}\nPort: "
                                   "{port}\nModell für die systemd-Unit: {model}\nKV-Cache: {kv}",
        "boards.settings.install_user": "user (Startskript im Home-Ordner des Benutzers)",
        "boards.settings.install_systemd": "systemd (systemd-Unit; sudo bestätigt {at})",
        "boards.settings.not_set": "nicht gesetzt",
        "boards.settings.problems": "So kann noch nicht installiert werden:",
        "boards.settings.install": "Installation:",
        "boards.settings.opt_user": "Startskript im Home-Ordner des Benutzers (user)",
        "boards.settings.opt_systemd": "systemd-Unit, mit sudo geschrieben (systemd)",
        "boards.settings.user": "SSH-Benutzer (nie root):",
        "boards.settings.port": "Port des Servers (1024-65535):",
        "boards.settings.model": "Modell für die systemd-Unit:",
        "boards.settings.model_hint": "Pfad auf dem Board, z. B. models/model.gguf (relativ: im Home-Ordner)",
        "boards.settings.kv": "KV-Cache:",
        "boards.settings.kv_auto": "Automatisch: {kind} ({why})",
        "boards.settings.sudo_question": "Die systemd-Unit wird mit sudo nach /etc/systemd/system geschrieben und auf "
                                         "dem Gerät aktiviert (sudo -n: es muss ohne Passwort gehen, das Deployment "
                                         "fragt nie). Dürfen Deployments auf dieses Gerät diese sudo-Befehle "
                                         "ausführen? Deine Antwort wird im Audit-Log vermerkt.",
        "boards.settings.no_systemd": "Dieses Board nutzt kein systemd (sein Prüfprofil meldet {init}); dort läuft "
                                      "die Laufzeit über das Startskript. Die Einstellungen wurden nicht geändert.",
        "boards.settings.no_init": "kein Init-System",
        "boards.settings.sudo_declined": "Die systemd-Unit braucht deine Bestätigung, dass Deployments sudo auf dem "
                                         "Board nutzen dürfen; die Einstellungen wurden nicht geändert.",
        "boards.settings.consent_failed": "Die Zustimmung konnte nicht im Audit-Log vermerkt werden ({reason}); es "
                                          "wurde nichts geändert.",
        "boards.settings.not_saved": "Die Deploy-Einstellungen konnten nicht gespeichert werden: {reason}",
        "boards.settings.bad_value": "Dieser Wert ist nicht verwendbar: {reason}",
        "boards.settings.damaged_text": "Die Deploy-Einstellungen dieses Boards sind nicht verwendbar: {reason}",
        "boards.settings.damaged_question": "Die Deploy-Einstellungen von Gerät {device_id} sind nicht verwendbar "
                                            "({reason}). Neu festlegen, ausgehend von den Vorgaben? Die beschädigte "
                                            "Datei {path} ({size}) wird mit den neuen Einstellungen überschrieben; "
                                            "die Probe des Boards, das Vertrauen und seine Laufzeit bleiben, wie sie "
                                            "sind.",
        "boards.settings.saved": "Deploy-Einstellungen von {label} gespeichert.",
        "boards.settings.unchanged": "Es wurde nichts geändert.",
        "boards.plan.title": "Laufzeit-Plan von {label}",
        "boards.plan.text": "Board: {os}/{arch}, {libc} {libc_version}\nBuilder: Container {base} ({blibc} "
                            "{blibc_version}, GCC {gcc}), ohne Netzwerk\nCPU: {cpu}, OpenMP {openmp}, C++-Laufzeit "
                            "statisch gelinkt: {static}\nProgramme: {programs}\nBuild-ID: {build_id}",
        "boards.plan.cpu_all": "alle Varianten",
        "boards.plan.cpu_x86": "x86-Merkmale aus dem Prüfprofil",
        "boards.plan.features_used": "Genutzte CPU-Merkmale: {features}",
        "boards.plan.features_base": "keine über die Grundstufe hinaus",
        "boards.plan.feature_unusable": "Das Board hat {feature}, der Build kann es nicht nutzen: {reason}",
        "boards.plan.built": "Gebaut: ja, {path}",
        "boards.plan.not_built": "Gebaut: nein (Laufzeit bauen...)",
        "boards.plan.none": "Für dieses Board lässt sich keine Laufzeit planen: {reason}",
        "boards.status.title": "Laufzeit-Status von {label}",
        "boards.status.planned": "Geplanter Build {build} ({built})",
        "boards.status.built": "gebaut",
        "boards.status.not_built": "noch nicht gebaut",
        "boards.status.nothing": "Installiert: noch nichts",
        "boards.status.deployed": "Installiert: Build {build} am {at} ({install}), Prüfungen auf dem Board {checks} "
                                  "({verified})",
        "boards.status.passed": "bestanden",
        "boards.status.failed": "fehlgeschlagen",
        "boards.status.kv": "KV-Cache bei diesem Deployment: {kv}",
        "boards.status.old": "Der installierte Build ist nicht der jetzt geplante (Prüfprofil, festgelegte Version "
                             "oder Rezept haben sich geändert): neu bauen und installieren.",
        "boards.build.title": "Laufzeit von {label} bauen",
        "boards.build.intro": "Baut das festgelegte llama.cpp für dieses Board im Builder-Container (ohne Netzwerk) "
                              "und packt es. Abbrechen hält nach dem laufenden Schritt an; ein Build, der im "
                              "Container läuft, läuft zu Ende.",
        "boards.build.jobs": "Parallele Compile-Jobs (0: automatisch):",
        "boards.build.mirror": "Quell-Spiegel (optional):",
        "boards.build.mirror_hint": "URL eines Spiegels des llama.cpp-Repositorys",
        "boards.build.checking": "Festgelegte Quelle wird gesucht",
        "boards.build.source_question": "Die festgelegte llama.cpp-Quelle ({tag}, Commit {commit}, etwa 215 MB) ist "
                                        "noch nicht zwischengespeichert. Jetzt herunterladen?",
        "boards.build.source_needed": "Der Build braucht die festgelegte llama.cpp-Quelle; es wurde nichts gebaut. "
                                      "Auf einem Rechner ohne Internet importiere die Quelle zuerst.",
        "boards.build.running": "Laufzeit wird gebaut",
        "boards.build.fetching": "Festgelegte llama.cpp-Quelle wird heruntergeladen",
        "boards.build.building": "llama.cpp für {label} wird im Builder-Container gebaut",
        "boards.build.stored": "Build {build} für dieses Board ist schon gespeichert; er wird gepackt.",
        "boards.build.done": "Board-Laufzeit fertig: {path}\nsha256 {sha256}",
        "boards.build.older": "{n} ältere Laufzeit-Paket(e) dieses Boards bleiben erhalten; das Framework löscht "
                              "keines von selbst (Ältere Laufzeit-Pakete entfernen... listet sie).",
        "boards.build.cancelled": "Der Build wurde abgebrochen; es wurde nichts gepackt.",
        "boards.build.failed": "Der Build ist fehlgeschlagen: {reason}",
        "boards.build.logs": "Logs: {path}",
        "boards.prune.title": "Laufzeit-Pakete von {label}",
        "boards.prune.none": "Keine älteren Laufzeit-Pakete dieses Boards: das Paket des zuletzt installierten Builds "
                             "wird nie aufgeführt.",
        "boards.prune.intro": "Die älteren Laufzeit-Pakete dieses Boards auf diesem Rechner (das Paket des zuletzt "
                              "installierten Builds wird nie aufgeführt). Hake an, was du löschen willst; nichts ist "
                              "angehakt.",
        "boards.prune.reading": "Die Laufzeit-Pakete dieses Boards werden gelesen",
        "boards.prune.deleting": "Die angehakten Laufzeit-Pakete werden gelöscht",
        "boards.prune_board.title": "Laufzeit-Builds auf {label}",
        "boards.prune_board.reading": "Builds auf dem Board werden gelesen",
        "boards.prune_board.busy": "Nicht aufgeführt, weil ein laufendes Programm sie nutzt: {builds}.",
        "boards.prune_board.none": "Keine älteren Laufzeit-Builds auf diesem Board zu entfernen: der aktive Build "
                                   "wird nie aufgeführt.",
        "boards.prune_board.intro": "Die älteren Laufzeit-Builds auf dem Board (nie der aktive, nie einer, den ein "
                                    "laufendes Programm nutzt). Ein Board hat keinen Papierkorb: was du anhakst, wird "
                                    "endgültig entfernt. Nichts ist angehakt.",
        "boards.prune_board.removing": "Die angehakten Builds auf dem Board werden entfernt",
        "boards.prune_board.freed": "{size} auf dem Board frei geworden.",
        "boards.deploy.title": "Laufzeit auf {label} installieren",
        "boards.deploy.intro": "Installiert die gebaute Laufzeit so auf dem Board, wie seine Deploy-Einstellungen es "
                               "sagen, prüft den neuen Build dort und aktiviert ihn nur, wenn er besteht. Jeder ältere "
                               "Build bleibt auf dem Board.",
        "boards.deploy.user": "SSH-Benutzer:",
        "boards.deploy.password": "Passwort (nur für ein noch nicht gekoppeltes Board):",
        "boards.deploy.password_hint": "einmal genutzt, um den Schlüssel des Frameworks einzurichten, nie gespeichert",
        "boards.bench.model": "Modell für einen kurzen Benchmark (optional):",
        "boards.bench.hint": "Pfad auf dem Board, z. B. models/model.gguf (relativ: im Home-Ordner)",
        "boards.untrusted_question": "{path} ist kein Katalogmodell und daher nicht vertrauenswürdig. llama-bench "
                                     "lädt es nativ auf dem Gerät als {user} (ohne Sandbox). Den kurzen Benchmark "
                                     "damit ausführen? Deine Antwort wird im Audit-Log vermerkt.",
        "boards.deploy.not_built": "Die Laufzeit für dieses Board ist noch nicht gebaut (oder sein Prüfprofil hat "
                                   "sich geändert): zuerst Laufzeit bauen...",
        "boards.deploy.needs_source": "Das Paket muss neu gepackt werden, dafür wird die festgelegte Quelle gebraucht "
                                      "(ihr Lizenztext): zuerst Laufzeit bauen...",
        "boards.deploy.problems": "Es wurde nichts installiert; ändere zuerst die Deploy-Einstellungen "
                                  "(Einstellungen...):",
        "boards.deploy.root": "Der SSH-Benutzer auf dem Board ist root: die Laufzeit läuft nie als root. Installiere "
                              "als Benutzer ohne Sonderrechte (Einstellungen...).",
        "boards.deploy.preparing": "Build und Paket werden geprüft",
        "boards.deploy.running": "Installation auf dem Board",
        "boards.deploy.done": "Build {build} nach {root} installiert.",
        "boards.deploy.previous": "Der vorherige Build {previous} bleibt erhalten.",
        "boards.deploy.failed": "Build {build} hat seine Prüfungen auf dem Board nicht bestanden; er wurde nicht "
                                "aktiviert.",
        "boards.deploy.server_user": "Der Server lauscht auf 127.0.0.1:{port}; sein Schlüssel liegt in "
                                     "~/.config/llm-framework/llama-server.key. stop, status und serve (im "
                                     "Vordergrund) funktionieren genauso wie der Startbefehl oben.",
        "boards.deploy.server_systemd": "systemd-Unit: {state}; der Server lauscht auf 127.0.0.1:{port}; sein "
                                        "Schlüssel liegt in ~/.config/llm-framework/llama-server.key.",
        "boards.deploy.unit_started": "gestartet",
        "boards.deploy.unit_enabled": "aktiviert",
        "boards.checks": "Prüfungen auf dem Board:",
        "boards.check_line": "{name}: {result} - {detail}",
        "boards.check.passed": "bestanden",
        "boards.check.failed": "fehlgeschlagen",
        "boards.check.skipped": "übersprungen",
        "boards.bench.missing": "Kein lesbares Modell unter {path} auf dem Board; der kurze Benchmark entfällt.",
        "boards.kv": "KV-Cache: {kv}",
        "boards.verify.title": "Laufzeit auf {label} prüfen",
        "boards.verify.intro": "Prüft die installierte Laufzeit auf dem Board erneut als Benutzer ohne Sonderrechte "
                               "(nie mit sudo); mit einem Modell auch einen kurzen Benchmark.",
        "boards.verify.nothing": "Auf diesem Board wurde noch nichts installiert (Laufzeit installieren...).",
        "boards.verify.root": "Der SSH-Benutzer ist root; die Prüfungen laufen nur als Benutzer ohne Sonderrechte.",
        "boards.verify.running": "Prüfung auf dem Board",
        "boards.verify.result": "Board {device_id}, Build {build}:",
        "boards.check.running": "Das Board wird geprüft (eine Verbindung zu seinem SSH-Port, keine Anmeldung)",
    },
}
