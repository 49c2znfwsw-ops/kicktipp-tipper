# Kicktipp-Tipper

Berechnet laufend für jedes Spiel deiner Kicktipp-Tipprunde den Tipp mit den **meisten erwarteten Punkten**, zugeschnitten auf die Punktevergabe deiner Runde. Über eine Web-Oberfläche (oder die Kommandozeile) prüfst du die Empfehlungen, änderst sie bei Bedarf und gibst sie per Klick bei Kicktipp ab.

## So entsteht eine Empfehlung

1. **Wettquoten.** Aus den 1X2-Quoten wird die Buchmachermarge herausgerechnet. Danach werden die Torerwartungen beider Teams (xG) so gewählt, dass ein Poisson-Modell mit Dixon-Coles-Korrektur genau diese Wahrscheinlichkeiten ergibt. Mögliche Quellen:
   - die Quoten, die Kicktipp selbst in der Tippabgabe zeigt (dafür muss der Admin der Runde die Wettquoten-Anzeige aktiviert haben),
   - optional [The Odds API](https://the-odds-api.com) (kostenloser Schlüssel): Median über viele Buchmacher, dazu der Markt „Über/Unter 2,5 Tore“, der die erwartete Torzahl genauer festlegt.
2. **Ergebnismodell.** Aus den Ergebnissen der laufenden und der vorigen Saison ([OpenLigaDB](https://www.openligadb.de), frei zugänglich) werden Angriffs- und Abwehrstärke jedes Teams sowie der Heimvorteil geschätzt. Ältere Spiele zählen dabei weniger (Halbwertszeit einstellbar).
3. **Mischung.** Beide Schätzungen werden gewichtet kombiniert, Standard ist 80 % Quoten. Ohne Quoten zählt allein das Ergebnismodell.
4. **Punkteoptimierung.** Für jeden möglichen Tipp von 0:0 bis 5:5 rechnet das Tool die erwarteten Punkte nach den Regeln deiner Runde aus. Empfohlen wird der Tipp mit dem höchsten Erwartungswert, die nächstbesten werden mit angezeigt.

### Quoten-Punkteregel (Rundenquote)

Bei der Kicktipp-Quotenregel bekommt man für die richtige Tendenz `MAX / (10·T/N) − MAX/10 + MIN` Punkte, gerundet und auf [MIN, MAX] begrenzt. T ist die Zahl der Tipps mit dieser Tendenz, N die Zahl aller abgegebenen Tipps. Dazu kommen die Bonuspunkte für Tendenz, Tordifferenz oder Ergebnis. Bei MIN 3, MAX 10 und dem Bonus 2/3/4 ergibt das 5 bis 14 Punkte.

- **Rundenquote lesen.** Bei jeder Aktualisierung liest das Tool die aktuelle Rundenquote jedes Spiels („Heim-Remis-Gast“, z. B. 3-7-10) neu von der Kicktipp-Seite. Die Quote ändert sich mit jedem abgegebenen Tipp.
- **Eigenen Tipp herausrechnen.** Dein schon abgegebener Tipp steckt selbst in der Quote und wird deshalb herausgerechnet.
- **Bewegung bis zum Tippschluss schätzen.** Bis zum Anpfiff tippen noch weitere Mitglieder. Deshalb wird die angezeigte Quote mit einem Modell des Tippverhaltens deiner Runde gemischt (Standard: 75 % angezeigte Quote). Das Modell geht davon aus, dass Favoriten überproportional getippt werden und Remis selten.
- **Erwartete Quote berechnen.** Daraus ergibt sich die erwartete Quote zum Tippschluss, falls du die jeweilige Tendenz tippst. Dein eigener Tipp verschiebt die Quote ebenfalls und ist eingerechnet.
- **Tippverhalten lernen.** Nach Ablauf eines Spiels merkt sich das Tool die endgültige Rundenquote. Ab 10 beobachteten Spielen passt es das Modell des Tippverhaltens daran an.
- **Häufiger aktualisieren.** In den 3 Stunden vor einem Anpfiff aktualisiert das Tool alle 5 Minuten, weil sich die Quote dann noch bewegt. Für die Empfehlung zählt der letzte Stand vor der Abgabe.

Außenseiter- und Remis-Tipps werden so attraktiver, je einseitiger die Runde tippt. Bei hoher Siegwahrscheinlichkeit bleibt der Favorit trotzdem oft die bessere Wahl.

### Strategieverbot

Ab 9 Spielen pro Spieltag darf höchstens 2/3 der Tipps dasselbe Ergebnis haben, wobei 2:1 und 1:2 als gleich gelten. Das Tool wählt deshalb die Tipps aller offenen Spiele gemeinsam. Bereits geschlossene Spiele zählen mit. Wird ein Ergebnis zu häufig, weicht es dort aus, wo am wenigsten Punkte verloren gehen, und zeigt beim betroffenen Spiel einen Hinweis. Eine Freigabe, die gegen das Verbot verstoßen würde, wird abgelehnt, bevor etwas an Kicktipp geht.

**Kickernoten** fließen bewusst nicht ein. Es gibt dafür keine offene Schnittstelle, und gegenüber den Wettquoten bringen sie kaum zusätzliche Information: Die Märkte haben Form, Verletzungen und Aufstellungen bereits eingepreist.

## Start (ohne Vorkenntnisse)

**Windows:** Doppelklick auf `Kicktipp-Tipper-starten-Windows.bat`.
**Mac:** Rechtsklick auf `Kicktipp-Tipper-starten-Mac.command` → „Öffnen“. Beim ersten Mal musst du den Start noch einmal bestätigen.

Beim ersten Start richtet sich das Programm selbst ein. Das dauert ein bis zwei Minuten. Dabei installiert es Python, falls es fehlt, und fragt nach deiner Kicktipp-E-Mail und deinem Passwort. Danach öffnet sich die Oberfläche im Browser. Ab dem zweiten Start reicht der Doppelklick. Solange das schwarze Fenster offen ist, läuft das Programm. Schließt du das Fenster, wird es beendet.

**Updates kommen automatisch:** Bei jedem Start prüft das Programm, ob es auf GitHub eine neuere Version gibt, und lädt sie herunter. Dein Login (`.env`), deine Einstellungen (`config.toml`), die installierten Bausteine (`.venv`) und der Verlauf bleiben dabei erhalten. Ohne Internet startet einfach die vorhandene Version.

Fehlt die `config.toml`, fragt das Programm beim ersten Start nach dem Namen der Tipprunde und der Zahl der Tipper. Ändern kannst du sie per Rechtsklick → „Öffnen mit“ → Editor (Mac: TextEdit). Den Login änderst du, indem du die Datei `.env` löschst und neu startest.

## Einrichtung von Hand (für Fortgeschrittene)

```bash
pip install .
cp config.example.toml config.toml   # Tipprunde, Liga und Punkteregeln
cp .env.example .env                 # Kicktipp-Login, optional ODDS_API_KEY und UI_PASSWORD
```

## Benutzung

```bash
kicktipp-tipper serve                    # Oberfläche auf http://127.0.0.1:8000, aktualisiert alle 30 Min.
kicktipp-tipper empfehlen                # Empfehlungen des aktuellen Spieltags als Tabelle
kicktipp-tipper empfehlen --spieltag 9
kicktipp-tipper freigeben                # alle Empfehlungen abgeben, mit Rückfrage
kicktipp-tipper freigeben --nur-leere    # nur Spiele, die du noch nicht getippt hast
```

In der Oberfläche sind die Spiele vorausgewählt, deren abgegebener Tipp von der Empfehlung abweicht. Du kannst Tipps überschreiben und Spiele an- oder abwählen. Ein Klick auf **„Auswahl freigeben“** gibt die Tipps nach einer Rückfrage ab. Anschließend lädt das Tool die Seite neu und prüft, ob Kicktipp jeden Tipp übernommen hat. Jede Freigabe landet im Protokoll.

**Geänderte Empfehlungen:** Ändert sich eine Empfehlung, nachdem du sie gesehen hast, etwa weil sich die Rundenquote oder die Wettquoten bewegt haben, wird das Spiel markiert. Die Markierung zeigt den vorherigen und den neuen Tipp sowie den Grund. Sie bleibt, bis du auf „Gesehen“ klickst oder den Tipp freigibst. Der Browser-Tab zeigt die Zahl der Änderungen, z. B. „(2) Kicktipp-Tipper“.

**Sicherheitsnetz (optional):** Mit `auto_freigabe_minuten_vor_anpfiff = 60` werden Spiele, für die eine Stunde vor Anpfiff noch kein Tipp abgegeben ist, automatisch mit der Empfehlung getippt. Bereits abgegebene Tipps werden dabei nie überschrieben.

## Betrieb auf einem Server / NAS

```bash
docker compose up -d --build
```

Die Oberfläche ist dann nur lokal auf Port 8000 erreichbar. Wenn du sie von außen nutzen willst, zum Beispiel vom Handy, setze `UI_PASSWORD` und stelle einen Reverse-Proxy mit HTTPS davor. Dein Kicktipp-Passwort steht nur in `.env` und verlässt den Server ausschließlich in Richtung kicktipp.de.

## Hinweise

- Kicktipp bietet keine offizielle API. Das Tool bedient die normale Webseite, so wie es ein Browser tun würde. Ändert Kicktipp das HTML, muss `kicktipp_tipper/sources/kicktipp.py` angepasst werden. Starte bei Problemen mit `-v`, dann erscheinen ausführliche Fehlermeldungen.
- Wer in der Tabelle hinten liegt, kann bewusst von der Empfehlung abweichen, um Punkte gegenüber dem Feld aufzuholen. Das Tool maximiert dagegen immer den eigenen Erwartungswert.

## Tests

```bash
pip install '.[dev]' && pytest
```
