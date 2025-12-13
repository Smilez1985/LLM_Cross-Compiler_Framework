#!/bin/bash
set -e  # Bricht das Skript ab, wenn ein Befehl fehlerhaft ist

echo "🚀 Starte Installation..."

# 1. Prüfen, ob Python 3 installiert ist
if ! command -v python3 &> /dev/null; then
    echo "❌ Fehler: Python 3 wurde nicht gefunden. Bitte installiere Python 3."
    exit 1
fi

# 2. Prüfen, ob Poetry installiert ist
if ! command -v poetry &> /dev/null; then
    echo "⚠️  Poetry nicht gefunden. Installiere Poetry automatisch..."
    
    # Offizieller Installer von Poetry (isoliert)
    curl -sSL https://install.python-poetry.org | python3 -
    
    # Poetry zum Pfad hinzufügen für diese Session
    export PATH="$HOME/.local/bin:$PATH"
    
    echo "✅ Poetry wurde installiert."
else
    echo "✅ Poetry ist bereits installiert."
fi

# 3. Projektabhängigkeiten installieren
echo "📦 Installiere Abhängigkeiten aus poetry.lock..."

# --no-root: Installiert nur die Libs, nicht das Projekt selbst als Package (meistens gewünscht)
# --sync: Stellt sicher, dass das venv exakt mit dem lock-file übereinstimmt (löscht überflüssiges)
poetry install --no-root --sync

echo "🎉 Installation abgeschlossen!"
echo "ℹ️  Du kannst das Programm starten mit: poetry run python main.py"
