# Gezielte Dokumentnachpflege

Die Pipelinekorrektur aus PR 11 ist seit 13.11.2026 in main. Der normale
Enrichment-Lauf bleibt auf 25 Verfahren begrenzt. Ein erfolgreicher MariaDB-Sync
bestätigt die Übertragung des gespeicherten Bestands, nicht den vollständigen
Abruf fehlender amtlicher Dokumente.

## Beobachtung am 16.11.2026

Abgleich der 861 Fälle aus dem Quellenaudit vom 13.11. gegen Datencommit
`61b103ffca1046148fd10a1769de660a99f14731`: 247 Fälle enthalten inzwischen alle
damals nachgewiesenen Gutachten-Datei-IDs, bei 614 fehlen sie noch. Diese 614
Verfahren sind im gelesenen Stand weder aufgehoben noch zeitlich abgelaufen.
Dies ist ein Vergleich gespeicherter Metadaten, kein erneuter Quellenabruf.

Der Sync-Lauf 43819784654 hat den Snapshot am 16.11. um 07:59 Uhr MEZ bestätigt:
3448 Verfahren, 1804 aktualisiert, 1644 unverändert, 1 deaktiviert. Beide
gemeldeten Fälle he-55568 und he-55577 enthalten weiterhin nur die Bekanntmachung.
Der ausgelieferte app-8103.js ist bytegleich zu Website-PR 9
(Git-Blob bd8f2f31d9185f0aeb3c1335aeccb1f0afcb7fad). Beide Hessen-Detailseiten
zeigen bereits „in ZVGPro nicht erfasst“ und Quellenlinks mit origin-Referer.
Das ist keine vollständige Abnahme sämtlicher PHP-Dateien.

## Ausführung

1. Nach Übernahme dieses Patches unter GitHub Actions den manuellen Workflow
   **ZVG Dokumente gezielt nachpflegen** auf main starten. Die frisch abgeglichenen
   Objekt-IDs ins Pflichtfeld einsetzen. Maximal 1000 eindeutige IDs pro Lauf.
2. Der Job verwendet die bestehende Schreibsperre und liest main erst beim
   Checkout. Der vorhandene Parser prüft Bundesland, Verfahren und Anhänge.
   Es werden nur die gewählten Objekte und die Anreicherungsmetadaten geändert.
   Quellenfehler behalten vorhandene Anlagen. Kein PDF-Download, keine Fotogenerierung.
3. Plan, vollständiges Collector-Protokoll und Ergebnisdatei stehen im Artefakt
   `dokumentnachpflege-<Lauf-ID>`. Die Zusammenfassung zählt erfolgreiche Prüfungen,
   Fehler, Objekte mit neuen Gutachten und neue Gutachtenlinks getrennt.
4. Ein Quellenfehler verhindert nicht das Speichern anderer erfolgreicher Fälle.
   Abbruch des Collectors, unversuchte Ziele, Bestandsänderung oder ein abgewiesener
   Push verhindern einen als erfolgreich gemeldeten Datenupload. Bei einem
   konkurrierenden Datencommit kein Force-Push und kein automatisches Rebase.
5. Nach erfolgreichem Daten-Commit einen **neuen** Lauf von **ZVGPro MariaDB
   synchronisieren** auf main starten. Keinen alten Lauf wiederholen: dessen
   Snapshot bleibt unverändert.
6. Anschließend Live-Gutachtenflags, die beiden Hessen-Detailseiten und
   tatsächliche PDF-Aufrufe prüfen. Ungeklärte Restfälle getrennt weiterführen.

Zeitpläne, bestehender 25er-Lauf, Parser, Medienverarbeitung, Datenformat und
Sync-Protokoll werden nicht geändert. Der neue Workflow hat keinen Zeitplan
und startet keinen Datenbankimport. Die beauftragte Aktivierung und Live-Abnahme
sind erst nach erfolgreichen Läufen erledigt.

## Prüfung

Der Offline-Wrapper enthält zusätzlich sieben synthetische Regressionstests:
Teilfehler mit vollständigem Erhalt anderer Objekte, unzulässige Eingaben,
1000er-Grenze, unbekannte/fremde Quellen, Collector-Abbruch, ausbleibender Versuch
und unerwartete Bestandsänderung. Tests führen keine Produktivabrufe aus.

Am 16.11.2026 bestanden alle 154 unittest-Fälle im gesamten Offline-Wrapper
sowie Parser-Smokes, Länderprüfung, Python-Kompilierung und git diff --check.
Der manuelle Nachlauf und sein anschließender Produktionssync wurden damit
noch nicht ausgeführt. Arbeitsauftrag: Issue 12.
