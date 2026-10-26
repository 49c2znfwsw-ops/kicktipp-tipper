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

**Lohnt sich das Anpassen?** Oben auf dem Reiter „Tipps“ steht, wie viele erwartete Punkte das Umstellen deiner Tipps auf die Empfehlungen insgesamt bringt, mit den größten Posten einzeln. Dazu kommt, was offene Spiele ohne Tipp wert sind. Ein Knopf wählt nur die lohnenden Änderungen (ab +0,05) und die fehlenden Tipps aus.

**Abgegebene Tipps:** Vor Anpfiff zeigt Kicktipp in der Tippübersicht, wer schon getippt hat. Die Tipps selbst bleiben verdeckt („-:-“). Das Tool zeigt je Spiel „abgegeben: 4 von 25“ und rechnet damit genauer. Aus der Zahl der Tipps und der Rundenquote ergibt sich die bisherige Verteilung, bei wenigen Tipps exakt, bei vielen auf ±1–2 Tipps genau. Prognostiziert werden nur noch die Mitspieler, die noch nicht getippt haben.

**Modell einstellen:** Oben auf dem Reiter „Tipps“ gibt es zwei Schieber:
- Wettquoten gegen Ergebnismodell
- angezeigte Rundenquote gegen Mitspieler-Prognose (bestimmt, ob die noch fehlenden Tipper eher so tippen wie die bisherigen oder wie ihr Profil)

Die Empfehlungen rechnen sich beim Schieben sofort neu, ohne Kicktipp erneut abzufragen. Die Einstellung bleibt gespeichert, „Standard wiederherstellen“ setzt sie auf die Werte aus der `config.toml` zurück.

**Geänderte Empfehlungen:** Ändert sich eine Empfehlung, nachdem du sie gesehen hast, etwa weil sich die Rundenquote oder die Wettquoten bewegt haben, wird das Spiel markiert. Die Markierung zeigt den vorherigen und den neuen Tipp sowie den Grund. Sie bleibt, bis du auf „Gesehen“ klickst oder den Tipp freigibst. Der Browser-Tab zeigt die Zahl der Änderungen, z. B. „(2) Kicktipp-Tipper“.

### Reiter „Mitspieler“

Nach Ablauf der Tippzeit zeigt Kicktipp allen Mitgliedern die Tipps der anderen in der Tippübersicht. Das Tool lädt diese Übersicht für alle bisherigen Spieltage. Abgeschlossene Spieltage lädt es nur einmal, danach prüft es alle 6 Stunden auf Neues. Daraus entsteht ein Profil je Mitspieler:

- Tendenz-Anteile im Vergleich zur übrigen Runde
- wie oft jemand mit der Mehrheit tippt
- Lieblingsergebnisse und Ø Tore
- Trefferquote und Teilnahme
- auffällige Vereinsvorlieben
- eine Typ-Einordnung, z. B. „Mitläufer“, „Querdenker“ oder „Remis-Fan“

Daraus folgt eine **Prognose je Mitspieler und offenem Spiel**. Diese Prognosen ersetzen in den Empfehlungen die grobe Schätzung des Rundenverhaltens. Sie liefern außerdem die erwartete Zahl aktiver Tipper, denn für die Quote zählen nur abgegebene Tipps. Die Daten bleiben lokal in der Datenbank auf deinem Rechner.

Die Spalte **„Prognose trifft“** zeigt je Mitspieler, wie oft die Prognose seine Tipptendenz an den bisherigen Spieltagen richtig vorhergesagt hat, mit der Zahl der bewerteten Tipps in Klammern. Grün heißt: mindestens 5 Prozentpunkte besser, als einfach auf die Mehrheit der Runde zu setzen. Rot heißt: schlechter. Grundlage ist dieselbe Rechnung wie im Abgleich, also die vor Anpfiff gespeicherte Prognose oder, für ältere Spieltage, eine nachträgliche Prognose nur aus den Spieltagen davor.

**Abgleich:** Für den letzten Spieltag stellt der Reiter die Prognose den tatsächlich abgegebenen Tipps gegenüber. Er zeigt die Trefferquote der Tendenz, zum Vergleich die Messlatte „tippt wie die Mehrheit“ und die erwartete gegen die echte Rundenquote. Das Tool speichert dafür vor jedem Anpfiff seine letzte Prognose. Für ältere Spieltage rechnet es nachträglich, mit Profilen nur aus den Spieltagen davor.

### Reiter „Rückblick“

Je Spieltag drei Zahlen nebeneinander:
- die Punkte, die die letzte Empfehlung vor Anpfiff geholt hätte
- deine tatsächlichen Punkte
- der Schnitt und der Beste der Runde

Für die Empfehlung wird die Rundenquote mit allen Tipps exakt nachgerechnet: dein Tipp heraus, die Empfehlung hinein. An Spieltag 6 ergibt diese Rechnung für alle 201 Tipps genau die Punkte von Kicktipp. Mit einem Klick auf einen Spieltag siehst du jedes Spiel im Detail.

### Reiter „Quoten“

Wettquoten vieler Buchmacher über [The Odds API](https://the-odds-api.com) neben der eigenen Prognose. Der Reiter zeigt:
- den Markt-Median
- die Abweichung in Prozentpunkten, farbig: blau, wenn die Prognose höher liegt als der Markt, orange, wenn der Markt höher liegt; je kräftiger, desto größer. Ein roter Rand markiert Spiele ab 5 Punkten Abweichung, ein gelber ab 2,5. Auf Wunsch lässt sich nach der Abweichung sortieren.
- die Bewegung der Kicktipp-Wettquote (bwin) aus den laufenden Aktualisierungen: eine Minigrafik und die Veränderung in Prozentpunkten in den letzten 24 Stunden und seit Beginn des Spieltags. Grün heißt, die Tendenz der Empfehlung wird wahrscheinlicher, rot heißt, sie wird unwahrscheinlicher. Diese Anzeige kostet keine Abfrage.
- die Bewegung des Markt-Medians zwischen den eigenen Abrufen (ab dem zweiten Abruf)
- bwin
- die Spanne der Buchmacher
- die besten Quoten
- die Wahrscheinlichkeit für über 2,5 Tore

Jeder Abruf wird zusätzlich **komplett archiviert**: alle Spiele, die die Odds API gerade anbietet (auch die künftiger Spieltage), mit den Quoten jedes einzelnen Buchmachers (1X2 und Über/Unter 2,5) und deren Zeitstempel. Das Archiv lässt sich im Reiter als CSV herunterladen (öffnet sich in Excel) und ist der Grundstock, um später zu prüfen, welche Quellen am treffsichersten waren.

Der kostenlose API-Schlüssel wird im Reiter eingetragen und nur lokal gespeichert. Abgerufen wird **nur auf Klick**: Ein Abruf kostet 2 der 500 Abfragen im Monat. Diese Quoten fließen nicht in die Empfehlungen ein.

### Reiter „So funktioniert's“

Rechnet ein Beispielspiel oder ein echtes Spiel des Spieltags Schritt für Schritt durch: von den Wettquoten über die Ergebnis-Wahrscheinlichkeiten, die Rundenquote und die Mitspieler-Prognose bis zu den erwarteten Punkten je Tipp. Alle Zahlen stammen aus derselben Programmlogik wie die Empfehlungen.

### Reiter „Verlauf“

Gesamtpunkte und Platzierung je Spieltag als Liniendiagramme. Du bist hervorgehoben, und bis zu zwei Mitspieler kannst du zum Vergleich anklicken. Fährst du mit der Maus über das Diagramm, siehst du die Werte. Darunter steht derselbe Verlauf als Tabelle.

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
