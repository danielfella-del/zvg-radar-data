# Dokumentabgleich 1.4.23

## Problem und Verhalten

Die amtlichen Suchlisten enthalten nicht sämtliche Gutachten. Der bisherige
Detailabruf wiederholte dieselben Fehler am Anfang der Warteschlange. Außerdem
wies die Aktenzeichenprüfung gültige Portalformate ohne Abteilung, mit
Buchstabenzusatz oder doppeltem K ab. Dadurch blieb ein großer Teil des Bestands
ohne vollständigen Dokumentabgleich.

In der regionalen BW-Quelle wurden Werte und Termine aus benachbarten Objekten
übernommen. Dort fehlte zudem die getrennte Prüfung, ob die externe Quelle ein
Gutachten anbietet. Die Änderung behandelt diese Fehler an ihrer jeweiligen
Quelle und markiert externe Verfügbarkeit nicht als importiertes Dokument.

## Portalabgleich

- Nummer und Jahr des Aktenzeichens müssen übereinstimmen; eine in der Suchliste
  vorhandene Abteilung muss ebenfalls passen. Der Vergleich liest nur die erste
  Zeile der amtlichen Detailtabelle, nicht Verweise im Beschreibungstext.
- Falsche Verfahrens-/Länderzuordnungen, ungültige Anhangs-IDs und abgeschnittene
  HTML-Antworten werden verworfen, ohne vorhandene Anlagen zu löschen.
- Die Warteschlange sortiert nach dem letzten Versuch. Fehler warten mindestens
  sechs Stunden, erfolgreiche Details werden nach sieben Tagen erneut geprüft.
- Sicher vergangene und aufgehobene Verfahren bleiben in normalen Läufen aus;
  vorhandene Daten bleiben erhalten. `--object-id` erlaubt eine gezielte Prüfung.
- `detail_attempted_at` wird beim Suchlistenimport erhalten. Nur
  `detail_fetched_at` belegt einen erfolgreichen Detailabruf.
- Kurze Portalantworten `error` bleiben ungeprüft. Daraus wird keine konkrete
  Ursache wie ein fehlender Referer und kein fehlendes Gutachten abgeleitet.

## Regionale BW-Quelle

- Objekte werden einzeln aus den strukturierten Quelldaten gelesen. Externe ID,
  Objektlink, Gericht und vollständiges Aktenzeichen müssen zusammenpassen.
- Seitenzahl, Folgeseite und Gesamtzahl werden gemeinsam geprüft. Erst eine
  vollständig gelesene Gerichtsübersicht gilt als verifiziert. Fehlgeschlagene
  oder unvollständige Quellen behalten ihre vorherigen Datensätze unverändert.
- Bestehende IDs bleiben nur bei eindeutiger Zuordnung erhalten. Mehrere Lose
  eines Verfahrens bleiben anhand ihrer externen IDs getrennt. Mehrdeutige alte
  Zuordnungen behalten ihre ID, erhalten aber keine erfundenen aktuellen Werte,
  Termine, Dokumente oder Aufhebungsbehauptungen. Der schmale Prüfbackup enthält
  ausschließlich öffentliche Basisangaben. Eine wiederverwendete externe ID mit
  abweichendem Verfahren wird einzeln isoliert, damit unbeteiligte Objekte
  weiterhin aktualisiert werden können.
- `source_report_available` beschreibt ausschließlich das externe Angebot.
  `external_report_access` unterscheidet frei, Anmeldung, kostenpflichtig und
  unbekannt. Ein Link wird nur übernommen, wenn er auf der geprüften Quelle
  tatsächlich vorhanden ist. `attachments` und `has_report` werden hierdurch
  nicht als importiert oder freigeschaltet markiert.
- Reine Dokumentmetadaten werden begrenzt und fair nachgepflegt: höchstens
  20 Objekte und 60 Sekunden zusätzliche Laufzeit pro Aufruf, sechs Stunden
  Fehlerabstand und sieben Tage Aktualisierungsintervall. Ein letzter positiver
  Befund bleibt bei einem Quellenfehler erhalten, zusammen mit dem Fehlerstatus.
- Bereits vor dem ersten Netzwerkabruf wird die vorherige BW-Quelle atomar
  gesichert, auch bei Standalone-Aufrufen mit vorhandenem Output. So bleiben
  Altzeilen bei Discovery-Fehlern oder einem äußeren Timeout erhalten.
- Verifizierte Listenangaben werden vor optionalen Metadatenabrufen gespeichert;
  jeder abgeschlossene Metadatenversuch erhält einen eigenen Checkpoint.

## Regionale SH-Quelle

Die SH-Bekanntmachungen werden spaltenweise in Leserichtung gelesen. Ein Fall
beginnt bei seiner Termineinleitung und endet am eigenen Aktenzeichen mit
Gerichtsfooter. Gericht und Termin des nächsten Falls können nicht mehr in den
vorherigen Datensatz gelangen. Eine Ausgabe mit unklaren Grenzen bleibt als
Ganzes ungeprüft; ihre alten Daten werden erhalten.

Alte IDs werden nur bei eindeutiger Zuordnung innerhalb derselben Ausgabe und
zum vollständigen Aktenzeichen übernommen. Mehrdeutige alte IDs bleiben im
Prüfstatus erhalten; getrennte neue Bekanntmachungen werden nicht über
Gericht/Aktenzeichen/Tag zusammengeführt. Der gemeinsame Merge ersetzt nur
same-ID-Zeilen des SH-Adapters und lässt Portal und andere Quellen unangetastet.
Auch SH sichert den alten Bestand atomar vor dem ersten Netzwerkabruf.
Details stehen in `docs/SH-FALLGRENZEN-1.4.23.md`.

## Herkunft und Umfang

Basis ist der Datenrepo-Commit
`a731e20748d74cf8522eb99dcfac0bfff2ba3281`. Produktiv geändert sind
`collector/collect.py`, `collector/enrich_details.py`,
`collector/collect_bw_official.py`, `collector/collect_sh_official.py` und
`collector/merge_supplemental.py`. Vier neue Testdateien, Aufrufe in vorhandenen Testschritten und diese Dokumentation
ergänzen die Änderung. Die BW- und SH-Aufrufe erhalten den bereits vorhandenen Snapshot
`/tmp/auctions-before.json` zur sicheren Wiederverwendung alter IDs und Daten.

Zeitpläne, Concurrency, bestehende Portal-Mengenlimits, Medienverarbeitung,
HH/MV-Verarbeitung und Sync-Protokoll bleiben unverändert. Der unabhängige
Preis-PR #10 ist nicht enthalten. Produktionsdaten, Audit-HTML, PDFs und Medien
sind nicht Bestandteil des Patches.

## Ausgeführte Prüfungen

Der kombinierte Offline-Wrapper wird mit `bash scripts/test-offline.sh`
ausgeführt; alternativ kann `PYTHON_BIN` eine vorhandene Python-Umgebung wählen.
Er umfasst Python-Kompilierung, Suchlisten- und Detailparser-Smokes,
Bundesländerabdeckung sowie folgende synthetische Regressionen:

- 28 Portal-Warteschlangen-, Identitäts- und Erhaltungstests;
- 46 BW-Tests für getrennte Objekte, Pagination, stabile IDs, Quellenfehler,
  getrennte Gutachtenverfügbarkeit und Checkpoints;
- 25 SH-Tests für Spalten, Fallgrenzen, Alt-ID-Erhalt und Abbruchsicherung;
- 6 Tests des tatsächlichen SH-Merges mit temporären JSON-Dateien;
- 11 bestehende HH/MV-Tests;
- 7 bestehende Reportfoto-Warteschlangentests;
- 23 bestehende Sync-Client-Tests.

Alle 146 unittest-Fälle sowie die Parser-Smokes und Länderprüfung bestanden
am kombinierten Stand. Diese Prüfungen verwenden temporäre Testdaten und simulierte Netzwerkantworten.
Es werden keine Live-Workflows, Produktionsimporte, Mails oder Medienläufe
ausgeführt.

Separat wurden am 13.11.2026 alle 2.920 ausgewählten Portalobjekte des gelesenen
Snapshots lesend abgefragt. Dieser Quellenabgleich prüft veröffentlichte
Dokumentlinks, keine vollständigen PDF-Inhalte. Ergänzend bestätigte je ein
Gutachten-Transporttest in zwölf Ländern HTTP 200, PDF-Content-Type und
PDF-Dateisignatur mit `Referer: https://zvgpro.de/`. Jeder Abruf war auf zwei MiB
begrenzt. Für Länder ohne Gutachten-Link im Portalabgleich wurde kein Download
erfunden; regionale Quellen sind gesondert zu prüfen. Offene Quellenfehler sind
kein Nachweis für fehlende Gutachten.

Der SH-Parser wurde zusätzlich unabhängig am bereits gesicherten amtlichen PDF
verglichen: 27 Verfahren stimmten bei Gericht und Termin mit dem separaten Audit
überein, alle 25 bisherigen IDs blieben erhalten. Ein zweiter Abgleich ergab
denselben Bestand. Es wurden keine PDF-Inhalte als neue Testfixtures eingecheckt.

## Aktivierung und verbleibende Grenze

Ein grüner Codetest ändert keinen Produktionsdatensatz. Nach Merge müssen
Nachpflege, Import und Website-Ausgabe getrennt geprüft werden. Die bestehenden
begrenzten Portalabrufe holen den Rückstand nicht sofort auf. Ein zusätzlicher
Datenlauf benötigt daher eine eigene Ergebnis- und Fehlerzählung.

Externe Verfügbarkeit, importierte Metadaten, geschützte Dateien und tatsächlich
öffnungsfähige Dokumente bleiben unterschiedliche Zustände. Ungeprüfte,
veraltete oder fehlgeschlagene Daten belegen nicht, dass die Quelle kein
Gutachten veröffentlicht hat.
