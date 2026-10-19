#!/bin/bash
# Doppelklick startet den Kicktipp-Tipper. Alles Weitere (Update, Einrichtung, Start) macht start.py.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python fehlt. Bitte von https://www.python.org/downloads/ installieren und diese Datei erneut öffnen."
  read -r -p "Enter zum Schließen"; exit 1
fi
python3 start.py || read -r -p "Enter zum Schließen"
