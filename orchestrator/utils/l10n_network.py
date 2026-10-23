#!/usr/bin/env python3
"""
Texts of offline mode's locality rule, English and German (DECISIONS round 24, L-NET and "L-NET final";
orchestrator/utils/locality.py): why a host counts as local or not, the VPN check, and File > Settings... > Local
network with the person's opt-outs (orchestrator/Core/local_network.py; `llmcf config local-network` prints the same
texts). LocalizationManager merges TEXTS and FORMAL (localization.TEXT_MODULES); a key that exists already is refused
there.

Every key has the same {placeholders} in both languages. German texts say "du" (Q1); FORMAL holds the "Sie" form of
every German text here that addresses the person. This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        # why a host counts as local or not (Locality.why)
        "net.why.this_computer": "{host} is this computer",
        "net.why.own_network": "{host} is in your own network",
        "net.why.own_vpn": "{host} is in your own Headscale VPN (its control server and every relay are in your "
                           "network)",
        "net.why.opt_out_host": "you count {host} as local",
        "net.why.opt_out_range": "you count the address range of {host} as local",
        "net.why.opt_out_vpn": "you count the VPN of {host} as local",
        "net.why.public": "{host} resolves to {addresses} on the internet",
        "net.why.mixed": "{host} resolves to addresses in your network and on the internet ({addresses})",
        "net.why.vpn": "{host} is a Tailscale address ({addresses}), and the VPN is not a Headscale in your own network "
                       "with only own relays: {detail}",
        "net.why.unresolved": "{host} could not be resolved{detail}",
        "net.why.no_host": "there is no server address",
        "net.why.not_checked": "{host} was not checked yet",
        "net.why.an_address": "an address",
        "net.refusal": "Offline mode is on, and {subject} is not on this computer or in your own network: {why}.",
        "net.refusal.opt_out": " If it is part of your own network (a reverse proxy, a VPN), the person can count it "
                               "as local in the framework's settings (only the person can; an AI cannot).",
        "net.subject": "the address",
        # the VPN check (VpnCheck.why)
        "net.vpn.own_headscale": "its control server {control} and every relay are in your own network",
        "net.vpn.no_cli": "the tailscale command is not installed here, so the VPN cannot be checked",
        "net.vpn.unreadable": "the VPN's settings could not be read{detail}",
        "net.vpn.tailscale_control": "it is coordinated by Tailscale's own servers",
        "net.vpn.control_outside": "its control server {control} is not in your own network",
        "net.vpn.relay_outside": "it uses relays outside your own network: {relays}",
        "net.vpn.not_checked": "the VPN was not checked yet",
        # File > Settings... > Local network
        "net.tab": "Local network",
        "net.intro": "In offline mode the framework reaches only this computer and your own network. A name counts "
                     "by every address it resolves to (a name alone, such as .local or .fritz.box, never makes an "
                     "address on the internet local); an address of a Tailscale or Headscale VPN counts only when its "
                     "control server and every relay are in your own network. You decide what else counts as local: "
                     "a VPN, a host or an address range. Only you can do that here; an AI cannot.",
        "net.empty": "Nothing else counts as local: only this computer and your own network.",
        "net.col.kind": "What",
        "net.col.value": "Counts as local",
        "net.col.means": "What can then leave your network",
        "net.kind.vpn": "VPN",
        "net.kind.host": "Host",
        "net.kind.range": "Address range",
        "net.kind.label": "Kind:",
        "net.value.label": "Host or range:",
        "net.value.hint": "a host name such as myhome.example.org, or a range such as 203.0.113.0/24",
        "net.add": "Count as local…",
        "net.remove": "Remove",
        "net.check": "Check",
        "net.check.label": "Check an address:",
        "net.check.hint": "a host name or an address, for example nas.lan or 192.168.1.20",
        "net.check.running": "Checking {host}…",
        "net.check.local": "{host} counts as local: {why}.",
        "net.check.not_local": "{host} does not count as local: {why}.",
        "net.check.addresses": "Addresses: {addresses}",
        "net.vpn.state": "VPN of this computer (Tailscale or Headscale): {state}.",
        "net.vpn.state.local": "counts as local, {why}",
        "net.vpn.state.not_local": "does not count as local, {why}",
        "net.vpn.checking": "VPN of this computer (Tailscale or Headscale): checking…",
        "net.vpn.tailscale": "Tailscale or Headscale (100.64.0.0/10, fd7a:115c:a1e0::/48)",
        "net.explain.vpn": "Every address of your Tailscale or Headscale VPN (100.64.0.0/10, fd7a:115c:a1e0::/48) "
                           "then counts as local in offline mode, also when the VPN is coordinated by Tailscale's "
                           "servers or uses relays outside your network. Prompts, knowledge excerpts, files and keys "
                           "the framework sends to a server in the VPN can then pass those relays (encrypted by the "
                           "VPN), and the coordination server knows which of your devices take part.",
        "net.explain.host": "{value} then counts as local in offline mode, whatever addresses it resolves to. "
                            "Prompts, knowledge excerpts, files and keys the framework sends to it go wherever that "
                            "name points: if it is a public name (a reverse proxy, a dynamic DNS name), they leave "
                            "your network on the way, and whoever controls the name decides where they arrive.",
        "net.explain.range": "Every address in {value} then counts as local in offline mode. Prompts, knowledge "
                             "excerpts, files and keys the framework sends to a server there leave your network if "
                             "the range is not part of it.",
        "net.ask.title": "Count as local",
        "net.ask": "{explanation}\n\nCount it as local?",
        "net.added": "{value} now counts as local in offline mode.",
        "net.removed": "{value} no longer counts as local.",
        "net.not_found": "{value} was not counted as local.",
        "net.remove_question": "{value} will no longer count as local in offline mode; the change is saved in {path}. "
                               "Remove it?",
        "net.pick": "Choose a row first.",
        "net.error.kind": "Choose VPN, host or address range.",
        "net.error.vpn": "Unknown VPN {value!r}: the framework knows tailscale (Tailscale and Headscale).",
        "net.error.host": "{value!r} is not a host name.",
        "net.error.host_is_address": "{value} is an address: count it as an address range.",
        "net.error.range": "{value!r} is not an address or address range (e.g. 203.0.113.0/24).",
        "net.error.not_saved": "Not saved: {path} could not be updated (see the log); nothing changed.",
        # the person's Deep Ingest in offline mode
        "net.crawl.title": "Deep Ingest",
        "net.crawl.offline": "Offline mode is on, so the framework reads no web sites: the crawler reads only public "
                             "sites, and those are outside your own network. To read documentation sites, switch "
                             "offline mode off in {path:act_ditto_settings}.",
    },
    "de": {
        "net.why.this_computer": "{host} ist dieser Computer",
        "net.why.own_network": "{host} ist in deinem eigenen Netz",
        "net.why.own_vpn": "{host} ist in deinem eigenen Headscale-VPN (sein Steuerserver und jedes Relay sind in "
                           "deinem Netz)",
        "net.why.opt_out_host": "du zählst {host} als lokal",
        "net.why.opt_out_range": "du zählst den Adressbereich von {host} als lokal",
        "net.why.opt_out_vpn": "du zählst das VPN von {host} als lokal",
        "net.why.public": "{host} löst zu {addresses} im Internet auf",
        "net.why.mixed": "{host} löst zu Adressen in deinem Netz und im Internet auf ({addresses})",
        "net.why.vpn": "{host} ist eine Tailscale-Adresse ({addresses}), und das VPN ist kein Headscale in deinem "
                       "eigenen Netz mit nur eigenen Relays: {detail}",
        "net.why.unresolved": "{host} ließ sich nicht auflösen{detail}",
        "net.why.no_host": "es gibt keine Serveradresse",
        "net.why.not_checked": "{host} wurde noch nicht geprüft",
        "net.why.an_address": "eine Adresse",
        "net.refusal": "Der Offline-Modus ist an, und {subject} ist nicht auf diesem Computer oder in deinem eigenen "
                       "Netz: {why}.",
        "net.refusal.opt_out": " Gehört es zu deinem eigenen Netz (ein Reverse Proxy, ein VPN), kann die Person es in "
                               "den Einstellungen des Frameworks als lokal zählen (nur die Person kann das, keine KI).",
        "net.subject": "die Adresse",
        "net.vpn.own_headscale": "sein Steuerserver {control} und jedes Relay sind in deinem eigenen Netz",
        "net.vpn.no_cli": "der Befehl tailscale ist hier nicht installiert, daher lässt sich das VPN nicht prüfen",
        "net.vpn.unreadable": "die Einstellungen des VPN ließen sich nicht lesen{detail}",
        "net.vpn.tailscale_control": "es wird von Tailscales eigenen Servern koordiniert",
        "net.vpn.control_outside": "sein Steuerserver {control} ist nicht in deinem eigenen Netz",
        "net.vpn.relay_outside": "es nutzt Relays außerhalb deines eigenen Netzes: {relays}",
        "net.vpn.not_checked": "das VPN wurde noch nicht geprüft",
        "net.tab": "Lokales Netz",
        "net.intro": "Im Offline-Modus erreicht das Framework nur diesen Computer und dein eigenes Netz. Ein Name zählt "
                     "nach jeder Adresse, zu der er auflöst (ein Name allein wie .local oder .fritz.box macht nie eine "
                     "Adresse im Internet lokal); eine Adresse eines Tailscale- oder Headscale-VPN zählt nur, wenn sein "
                     "Steuerserver und jedes Relay in deinem eigenen Netz sind. Was sonst als lokal zählt, entscheidest "
                     "du: ein VPN, ein Host oder ein Adressbereich. Das kannst nur du hier festlegen, keine KI.",
        "net.empty": "Sonst zählt nichts als lokal: nur dieser Computer und dein eigenes Netz.",
        "net.col.kind": "Was",
        "net.col.value": "Zählt als lokal",
        "net.col.means": "Was dann dein Netz verlassen kann",
        "net.kind.vpn": "VPN",
        "net.kind.host": "Host",
        "net.kind.range": "Adressbereich",
        "net.kind.label": "Art:",
        "net.value.label": "Host oder Bereich:",
        "net.value.hint": "ein Hostname wie myhome.example.org oder ein Bereich wie 203.0.113.0/24",
        "net.add": "Als lokal zählen…",
        "net.remove": "Entfernen",
        "net.check": "Prüfen",
        "net.check.label": "Eine Adresse prüfen:",
        "net.check.hint": "ein Hostname oder eine Adresse, zum Beispiel nas.lan oder 192.168.1.20",
        "net.check.running": "Prüfe {host}…",
        "net.check.local": "{host} zählt als lokal: {why}.",
        "net.check.not_local": "{host} zählt nicht als lokal: {why}.",
        "net.check.addresses": "Adressen: {addresses}",
        "net.vpn.state": "VPN dieses Computers (Tailscale oder Headscale): {state}.",
        "net.vpn.state.local": "zählt als lokal, {why}",
        "net.vpn.state.not_local": "zählt nicht als lokal, {why}",
        "net.vpn.checking": "VPN dieses Computers (Tailscale oder Headscale): wird geprüft…",
        "net.vpn.tailscale": "Tailscale oder Headscale (100.64.0.0/10, fd7a:115c:a1e0::/48)",
        "net.explain.vpn": "Jede Adresse deines Tailscale- oder Headscale-VPN (100.64.0.0/10, fd7a:115c:a1e0::/48) "
                           "zählt dann im Offline-Modus als lokal, auch wenn das VPN von Tailscales Servern koordiniert "
                           "wird oder Relays außerhalb deines Netzes nutzt. Prompts, Wissensauszüge, Dateien und "
                           "Schlüssel, die das Framework an einen Server im VPN schickt, können dann über diese Relays "
                           "laufen (vom VPN verschlüsselt), und der Koordinationsserver weiß, welche deiner Geräte "
                           "teilnehmen.",
        "net.explain.host": "{value} zählt dann im Offline-Modus als lokal, egal zu welchen Adressen der Name auflöst. "
                            "Prompts, Wissensauszüge, Dateien und Schlüssel, die das Framework dorthin schickt, gehen "
                            "dorthin, wohin der Name zeigt: Ist es ein öffentlicher Name (ein Reverse Proxy, ein "
                            "Dynamic-DNS-Name), verlassen sie unterwegs dein Netz, und wer den Namen kontrolliert, "
                            "entscheidet, wo sie ankommen.",
        "net.explain.range": "Jede Adresse in {value} zählt dann im Offline-Modus als lokal. Prompts, Wissensauszüge, "
                             "Dateien und Schlüssel, die das Framework an einen Server dort schickt, verlassen dein "
                             "Netz, wenn der Bereich nicht dazugehört.",
        "net.ask.title": "Als lokal zählen",
        "net.ask": "{explanation}\n\nAls lokal zählen?",
        "net.added": "{value} zählt jetzt im Offline-Modus als lokal.",
        "net.removed": "{value} zählt nicht mehr als lokal.",
        "net.not_found": "{value} wurde nicht als lokal gezählt.",
        "net.remove_question": "{value} zählt dann im Offline-Modus nicht mehr als lokal; die Änderung wird in {path} "
                               "gespeichert. Entfernen?",
        "net.pick": "Wähle zuerst eine Zeile.",
        "net.error.kind": "Wähle VPN, Host oder Adressbereich.",
        "net.error.vpn": "Unbekanntes VPN {value!r}: das Framework kennt tailscale (Tailscale und Headscale).",
        "net.error.host": "{value!r} ist kein Hostname.",
        "net.error.host_is_address": "{value} ist eine Adresse: zähle sie als Adressbereich.",
        "net.error.range": "{value!r} ist keine Adresse und kein Adressbereich (z. B. 203.0.113.0/24).",
        "net.error.not_saved": "Nicht gespeichert: {path} ließ sich nicht aktualisieren (siehe Log); nichts wurde "
                               "geändert.",
        "net.crawl.title": "Deep Ingest",
        "net.crawl.offline": "Der Offline-Modus ist an, daher liest das Framework keine Webseiten: Der Crawler liest "
                             "nur öffentliche Seiten, und die liegen außerhalb deines eigenen Netzes. Um "
                             "Dokumentationsseiten zu lesen, schalte den Offline-Modus in {path:act_ditto_settings} "
                             "aus.",
    },
}

FORMAL = {
    "de": {
        "net.why.own_network": "{host} ist in Ihrem eigenen Netz",
        "net.why.own_vpn": "{host} ist in Ihrem eigenen Headscale-VPN (sein Steuerserver und jedes Relay sind in "
                           "Ihrem Netz)",
        "net.why.opt_out_host": "Sie zählen {host} als lokal",
        "net.why.opt_out_range": "Sie zählen den Adressbereich von {host} als lokal",
        "net.why.opt_out_vpn": "Sie zählen das VPN von {host} als lokal",
        "net.why.mixed": "{host} löst zu Adressen in Ihrem Netz und im Internet auf ({addresses})",
        "net.why.vpn": "{host} ist eine Tailscale-Adresse ({addresses}), und das VPN ist kein Headscale in Ihrem "
                       "eigenen Netz mit nur eigenen Relays: {detail}",
        "net.refusal": "Der Offline-Modus ist an, und {subject} ist nicht auf diesem Computer oder in Ihrem eigenen "
                       "Netz: {why}.",
        "net.refusal.opt_out": " Gehört es zu Ihrem eigenen Netz (ein Reverse Proxy, ein VPN), kann die Person es in "
                               "den Einstellungen des Frameworks als lokal zählen (nur die Person kann das, keine KI).",
        "net.vpn.own_headscale": "sein Steuerserver {control} und jedes Relay sind in Ihrem eigenen Netz",
        "net.vpn.control_outside": "sein Steuerserver {control} ist nicht in Ihrem eigenen Netz",
        "net.vpn.relay_outside": "es nutzt Relays außerhalb Ihres eigenen Netzes: {relays}",
        "net.intro": "Im Offline-Modus erreicht das Framework nur diesen Computer und Ihr eigenes Netz. Ein Name zählt "
                     "nach jeder Adresse, zu der er auflöst (ein Name allein wie .local oder .fritz.box macht nie eine "
                     "Adresse im Internet lokal); eine Adresse eines Tailscale- oder Headscale-VPN zählt nur, wenn sein "
                     "Steuerserver und jedes Relay in Ihrem eigenen Netz sind. Was sonst als lokal zählt, entscheiden "
                     "Sie: ein VPN, ein Host oder ein Adressbereich. Das können nur Sie hier festlegen, keine KI.",
        "net.empty": "Sonst zählt nichts als lokal: nur dieser Computer und Ihr eigenes Netz.",
        "net.col.means": "Was dann Ihr Netz verlassen kann",
        "net.explain.vpn": "Jede Adresse Ihres Tailscale- oder Headscale-VPN (100.64.0.0/10, fd7a:115c:a1e0::/48) "
                           "zählt dann im Offline-Modus als lokal, auch wenn das VPN von Tailscales Servern koordiniert "
                           "wird oder Relays außerhalb Ihres Netzes nutzt. Prompts, Wissensauszüge, Dateien und "
                           "Schlüssel, die das Framework an einen Server im VPN schickt, können dann über diese Relays "
                           "laufen (vom VPN verschlüsselt), und der Koordinationsserver weiß, welche Ihrer Geräte "
                           "teilnehmen.",
        "net.explain.host": "{value} zählt dann im Offline-Modus als lokal, egal zu welchen Adressen der Name auflöst. "
                            "Prompts, Wissensauszüge, Dateien und Schlüssel, die das Framework dorthin schickt, gehen "
                            "dorthin, wohin der Name zeigt: Ist es ein öffentlicher Name (ein Reverse Proxy, ein "
                            "Dynamic-DNS-Name), verlassen sie unterwegs Ihr Netz, und wer den Namen kontrolliert, "
                            "entscheidet, wo sie ankommen.",
        "net.explain.range": "Jede Adresse in {value} zählt dann im Offline-Modus als lokal. Prompts, Wissensauszüge, "
                             "Dateien und Schlüssel, die das Framework an einen Server dort schickt, verlassen Ihr "
                             "Netz, wenn der Bereich nicht dazugehört.",
        "net.pick": "Wählen Sie zuerst eine Zeile.",
        "net.error.kind": "Wählen Sie VPN, Host oder Adressbereich.",
        "net.error.host_is_address": "{value} ist eine Adresse: zählen Sie sie als Adressbereich.",
        "net.crawl.offline": "Der Offline-Modus ist an, daher liest das Framework keine Webseiten: Der Crawler liest "
                             "nur öffentliche Seiten, und die liegen außerhalb Ihres eigenen Netzes. Um "
                             "Dokumentationsseiten zu lesen, schalten Sie den Offline-Modus in "
                             "{path:act_ditto_settings} aus.",
    },
}
