@echo off
rem Doppelklick startet den Kicktipp-Tipper. Alles Weitere (Update, Einrichtung, Start) macht start.py.
chcp 65001 >nul
cd /d "%~dp0"
title Kicktipp-Tipper
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python ist nicht installiert. Es wird jetzt installiert ...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  py -3 --version >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo.
  echo Python konnte nicht automatisch installiert werden.
  echo Bitte von https://www.python.org/downloads/ installieren
  echo und dabei unten im Installer "Add python.exe to PATH" anhaken.
  echo Danach diese Datei erneut doppelklicken.
  pause
  exit /b 1
)
%PY% start.py
pause
