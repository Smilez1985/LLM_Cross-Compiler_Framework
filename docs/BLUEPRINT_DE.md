Version 3.1.0 · Stand 2026-10-29

[English](BLUEPRINT.md) · **Deutsch**

# Bauplan

Der Bauplan des LLM Cross-Compiler Frameworks besteht aus drei Dateien, jede auf Englisch mit einem deutschen Zwilling:

| Datei | Was sie enthält |
|---|---|
| [docs/DESIGN_DE.md](DESIGN_DE.md) | Was das Framework ist, jeder Baustein im Detail und jede Entscheidung mit dem, was sie sagt, wie der Code sie umsetzt und warum |
| [docs/ROADMAP_DE.md](ROADMAP_DE.md) | Was kommt: geplante Arbeit und Arbeit in Arbeit, nichts Fertiges |
| [CHANGELOG_DE.md](../CHANGELOG_DE.md) | Was war: nur fertige, verdrahtete, getestete und committete Arbeit, ein Abschnitt je Version (Keep a Changelog 1.1.0), jeder Eintrag mit Zeitstempel und Version |

Eine Änderung wird gegen alle drei gebaut und aktualisiert alle drei im selben Commit: ihren Changelog-Eintrag unter der
Version, in der sie landet, ihre Entscheidung im Design und die Roadmap-Zeile, die sie abschließt, entfernt. Es gibt
keinen Abschnitt „Unreleased“; Kommendes steht nur in der Roadmap.

## Wohin die alten Abschnitte gezogen sind

Diese Datei enthielt früher Design und Roadmap in einem Dokument. Links auf ihre alten Abschnitte funktionieren weiter:
Jeder alte Anker steht hier mit seinem neuen Ort.

| Alter Anker | Neuer Ort |
|---|---|
| <a id="inhalt"></a>`#inhalt` | [DESIGN_DE: Inhalt](DESIGN_DE.md#inhalt) |
| <a id="kurz-gesagt"></a>`#kurz-gesagt` | [DESIGN_DE: Kurz gesagt](DESIGN_DE.md#kurz-gesagt) |
| <a id="zweck-und-grundsätze"></a>`#zweck-und-grundsätze` | [DESIGN_DE: Zweck und Grundsätze](DESIGN_DE.md#zweck-und-grundsätze) |
| <a id="architektur-im-überblick"></a>`#architektur-im-überblick` | [DESIGN_DE: Architektur im Überblick](DESIGN_DE.md#architektur-im-überblick) |
| <a id="schichten-unveröffentlicht"></a>`#schichten-unveröffentlicht` | [DESIGN_DE: Schichten](DESIGN_DE.md#schichten) |
| <a id="bausteine"></a>`#bausteine` | [DESIGN_DE: Bausteine](DESIGN_DE.md#bausteine) |
| <a id="wo-was-liegt"></a>`#wo-was-liegt` | [DESIGN_DE: Wo was liegt](DESIGN_DE.md#wo-was-liegt) |
| <a id="datenflüsse"></a>`#datenflüsse` | [DESIGN_DE: Datenflüsse](DESIGN_DE.md#datenflüsse) |
| <a id="ein-build-vom-auftrag-bis-zum-gerät"></a>`#ein-build-vom-auftrag-bis-zum-gerät` | [DESIGN_DE: Ein Build, vom Auftrag bis zum Gerät](DESIGN_DE.md#ein-build-vom-auftrag-bis-zum-gerät) |
| <a id="ein-tool-aufruf-eines-mcp-clients"></a>`#ein-tool-aufruf-eines-mcp-clients` | [DESIGN_DE: Ein Tool-Aufruf eines MCP-Clients](DESIGN_DE.md#ein-tool-aufruf-eines-mcp-clients) |
| <a id="eine-ki-app-im-offenen-fenster-unveröffentlicht"></a>`#eine-ki-app-im-offenen-fenster-unveröffentlicht` | [DESIGN_DE: Eine KI-App im offenen Fenster](DESIGN_DE.md#eine-ki-app-im-offenen-fenster) |
| <a id="eine-frage-an-ditto-unveröffentlicht"></a>`#eine-frage-an-ditto-unveröffentlicht` | [DESIGN_DE: Eine Frage an Ditto](DESIGN_DE.md#eine-frage-an-ditto) |
| <a id="freigabe-beim-self-healing"></a>`#freigabe-beim-self-healing` | [DESIGN_DE: Freigabe beim Self-Healing](DESIGN_DE.md#freigabe-beim-self-healing) |
| <a id="das-lokale-modell-für-den-offline-modus-unveröffentlicht"></a>`#das-lokale-modell-für-den-offline-modus-unveröffentlicht` | [DESIGN_DE: Das lokale Modell für den Offline-Modus](DESIGN_DE.md#das-lokale-modell-für-den-offline-modus) |
| <a id="offline-modus-und-das-eigene-netz-unveröffentlicht"></a>`#offline-modus-und-das-eigene-netz-unveröffentlicht` | [DESIGN_DE: Offline-Modus und das eigene Netz](DESIGN_DE.md#offline-modus-und-das-eigene-netz) |
| <a id="deep-ingest-von-der-webseite-in-die-wissensbasis-unveröffentlicht"></a>`#deep-ingest-von-der-webseite-in-die-wissensbasis-unveröffentlicht` | [DESIGN_DE: Deep Ingest: von der Webseite in die Wissensbasis](DESIGN_DE.md#deep-ingest-von-der-webseite-in-die-wissensbasis) |
| <a id="eine-suche-in-der-wissensbasis-unveröffentlicht"></a>`#eine-suche-in-der-wissensbasis-unveröffentlicht` | [DESIGN_DE: Eine Suche in der Wissensbasis](DESIGN_DE.md#eine-suche-in-der-wissensbasis) |
| <a id="der-speicher-der-wissensbasis-qdrant-edge-unveröffentlicht"></a>`#der-speicher-der-wissensbasis-qdrant-edge-unveröffentlicht` | [DESIGN_DE: Der Speicher der Wissensbasis: Qdrant Edge](DESIGN_DE.md#der-speicher-der-wissensbasis-qdrant-edge) |
| <a id="community-pakete-format-vertrauen-und-austausch-unveröffentlicht"></a>`#community-pakete-format-vertrauen-und-austausch-unveröffentlicht` | [DESIGN_DE: Community-Pakete: Format, Vertrauen und Austausch](DESIGN_DE.md#community-pakete-format-vertrauen-und-austausch) |
| <a id="zuletzt-verwendete-eingaben-unveröffentlicht"></a>`#zuletzt-verwendete-eingaben-unveröffentlicht` | [DESIGN_DE: Zuletzt verwendete Eingaben](DESIGN_DE.md#zuletzt-verwendete-eingaben) |
| <a id="sicherheitsmodell"></a>`#sicherheitsmodell` | [DESIGN_DE: Sicherheitsmodell](DESIGN_DE.md#sicherheitsmodell) |
| <a id="bekannte-lücken"></a>`#bekannte-lücken` | [DESIGN_DE: Bekannte Lücken](DESIGN_DE.md#bekannte-lücken) |
| <a id="wichtige-entscheidungen"></a>`#wichtige-entscheidungen` | [DESIGN_DE: Wichtige Entscheidungen](DESIGN_DE.md#wichtige-entscheidungen) |
| <a id="versionierung-und-dokumentationsregeln"></a>`#versionierung-und-dokumentationsregeln` | [DESIGN_DE: Versionierung und Dokumentationsregeln](DESIGN_DE.md#versionierung-und-dokumentationsregeln) |
| <a id="roadmap"></a>`#roadmap` | [ROADMAP_DE](ROADMAP_DE.md) |
| <a id="310---ditto-neubau-anmeldung-und-lokale-ki-in-arbeit"></a>`#310---ditto-neubau-anmeldung-und-lokale-ki-in-arbeit` | [ROADMAP_DE](ROADMAP_DE.md) |
| <a id="als-nächstes---build-dienst-warteschlange-und-modi-geplant-beginnt-direkt-nach-der-architektur-konsolidierung"></a>`#als-nächstes---build-dienst-warteschlange-und-modi-geplant-beginnt-direkt-nach-der-architektur-konsolidierung` | [ROADMAP_DE: Als Nächstes - Build-Dienst, Warteschlange und Modi (geplant; beginnt direkt nach der Architektur-Konsolidierung)](ROADMAP_DE.md#als-nächstes---build-dienst-warteschlange-und-modi-geplant-beginnt-direkt-nach-der-architektur-konsolidierung) |
| <a id="320---laufzeit-auf-dem-gerät-und-die-hardware-des-maintainers-geplant"></a>`#320---laufzeit-auf-dem-gerät-und-die-hardware-des-maintainers-geplant` | [ROADMAP_DE: 3.2.0 - Dittos Kontext, das Board-Bündel, die Laufzeit auf dem Gerät und mehr Hardware (in Arbeit)](ROADMAP_DE.md#320---dittos-kontext-das-board-bündel-die-laufzeit-auf-dem-gerät-und-mehr-hardware-in-arbeit) |
| <a id="330---lora-und-große-modelle-geplant"></a>`#330---lora-und-große-modelle-geplant` | [ROADMAP_DE: 3.3.0 - LoRA und große Modelle (geplant)](ROADMAP_DE.md#330---lora-und-große-modelle-geplant) |
| <a id="340---lieferkette-und-community-geplant"></a>`#340---lieferkette-und-community-geplant` | [ROADMAP_DE: 3.4.0 - Lieferkette und Community (geplant)](ROADMAP_DE.md#340---lieferkette-und-community-geplant) |
| <a id="rust-teile-geplant"></a>`#rust-teile-geplant` | [ROADMAP_DE: Rust-Teile (geplant)](ROADMAP_DE.md#rust-teile-geplant) |
| <a id="später-geplant"></a>`#später-geplant` | [ROADMAP_DE: Später (geplant)](ROADMAP_DE.md#später-geplant) |
| <a id="geschichte"></a>`#geschichte` | [DESIGN_DE: Geschichte](DESIGN_DE.md#geschichte) |
| <a id="glossar"></a>`#glossar` | [DESIGN_DE: Glossar](DESIGN_DE.md#glossar) |
