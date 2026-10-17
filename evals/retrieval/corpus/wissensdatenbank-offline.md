---
title: Wissensdatenbank ohne Internet
lang: de
---
# Wissensdatenbank ohne Internet

Die Wissensdatenbank des Frameworks läuft vollständig lokal. Texte werden mit einem lokalen Embedding-Modell
(FastEmbed auf ONNX Runtime, auf der CPU) in Vektoren umgerechnet und in einem eingebetteten Qdrant-Speicher im
Benutzerverzeichnis abgelegt. Es braucht keinen Datenbankserver und keinen Cloud-Dienst.

Nur einmal ist eine Verbindung nötig: Beim ersten Gebrauch lädt das Framework das Embedding-Modell in den
Modellordner, für das kleine mehrsprachige Standardmodell etwa ein halbes Gigabyte. Danach funktionieren Suche
und Import auch im Offline-Modus. Ist der Offline-Modus eingeschaltet, bevor das Modell geladen wurde, lädt das
Framework nichts herunter, sondern meldet, welches Modell fehlt und wie groß es ist.

Einen Qdrant-Server nutzt die Wissensdatenbank nur, wenn `qdrant_url` gesetzt ist. Entfernte Server brauchen
https und einen API-Schlüssel im Schlüsselbund.
