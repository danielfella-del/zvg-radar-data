# Sync-Fehlerdiagnose und Regressionen – Issue #8

Ausgangsstand nach Abgleich mit aktuellem `main`:
`0047a571cb5c47d15ec9ee7778add037b820846f`.
Die Änderungen betreffen ausschließlich den Python-Sync-Client, seine neuen
Mock-Regressionen und deren Einbindung in die Offline-Kette.

## Belegte Beobachtungen

Die Job-Logs beider im [Issue #8](https://github.com/danielfella-del/zvg-radar-data/issues/8)
genannten Läufe wurden gelesen:

- [Lauf 40315491019](https://github.com/danielfella-del/zvg-radar-data/actions/runs/40315491019),
  Job 121546928363, Commit `da00d70d4f6833ba274917208c3cd276c89e73af`:
  Vorprüfung mit 3.649 Verfahren und 417 Paketen. Am 26.10.2026 um 12:30:26 Uhr
  Europe/Berlin meldete der Client nur `Keine gültige Antwort für batch: URLError`.
  Auch der Abbruch von Lauf 1650 konnte nicht bestätigt werden.
- [Lauf 40370376066](https://github.com/danielfella-del/zvg-radar-data/actions/runs/40370376066),
  Job 121739185707: Am selben Tag um 18:44:14 Uhr Europe/Berlin wurden 3.689
  Verfahren vollständig bestätigt: 47 neu, 2.214 aktualisiert, 1.428 unverändert,
  sieben deaktiviert.

Die historische Netzwerkursache ist damit nicht bestimmt. Weder DNS, TLS,
Hosting noch `IncompleteRead` sind als Ursache des damaligen `URLError` belegt.
Der spätere Erfolg belegt keinen anhaltenden Ausfall. PR #7 betrifft einen
anderen Abrufweg; der GitHub-Download war im fehlgeschlagenen Lauf bereits
erfolgreich und ist nicht die nachgewiesene Ursache.

## Nachgewiesene Client-Probleme und Korrekturen

1. **Unvollständiges Lesen umgeht die vorhandenen Retries.** Ein vollständig
   simulierter `opener` wirft zuerst `http.client.IncompleteRead(b'')` und würde
   anschließend `{"ok": true}` liefern. Der unveränderte Client brach nach genau
   einem Mock-Aufruf ab. Die Ausnahme wird jetzt gezielt in die vorhandene
   Retry-Behandlung aufgenommen, auch wenn sie erst bei `response.read()` entsteht.
2. **HTTP-Fehlertexte sind keine sichere Diagnosequelle.** Der Client las bisher
   das JSON-Feld `error` und ersetzte lediglich den exakten Token. Andere
   Objektinhalte, kodierte Tokens oder fremde Log-Kommandos konnten unverändert
   ausgegeben werden; das Lesen selbst konnte ebenfalls `IncompleteRead` auslösen.
   HTTP-Fehlertexte werden jetzt weder gelesen noch geloggt. Statuscode und
   Anfragekontext reichen für die sichere Einordnung aus.
3. **Antwortwerte können in Ausnahmen oder Erfolgslogs gelangen.** Beispielsweise
   gibt `int()` einen ungültigen Serverwert im Fehlertext wieder. Numerische
   Antwortfelder erhalten jetzt einen festen, inhaltsfreien Fehlertext. Im
   Abschlusslog erscheinen ausschließlich ganzzahlige Zähler oder `null`.
4. **Abbruch- und Abschlussmeldungen waren ungenau.** Die Abort-Antworten des
   verfügbaren PHP-Vertrags unterscheiden `aborted`, `already_completed`,
   `not_active` und `not_running`. Diese Fälle werden jetzt separat gemeldet;
   ein allgemeines `ok` bestätigt keinen Abbruch. Ein Abschlussbeleg wird erst
   nach Prüfung von Ergebnis, Lauf-ID und vollständiger Anzahl als bestätigt
   gemeldet. Bei fehlender gültiger Lauf-ID wird kein unbekannter Lauf abgebrochen.

Eine Fehlerzeile enthält nun Aktion, absoluten Offset (falls vorhanden),
Versuchsnummer, Kategorie, gegebenenfalls numerischen HTTP-/OS-/TLS-Code sowie
Paketgröße und Anzahl der JSON-Werte. Es werden keine URLs, Authentifizierungs-
Header, Tokens, Objektinhalte oder Exception-/Server-Freitexte übernommen.
Kategorien wie `name_resolution`, `tls_certificate`, `connection_reset`,
`timeout`, `incomplete_response` oder `invalid_response` beschreiben ausschließlich
die konkret erhaltene Ausnahme. Ein `URLError` ohne typisierten Grund bleibt
`url_error`; daraus wird keine Ursache geraten.

## Abgleich mit dem verfügbaren PHP-Vertrag

Read-only geprüft wurden `api/github-sync.php`, `api/_remote-sync.php` und
`api/_remote-sync-transfer.php` aus dem gelieferten lokalen Website-Code
(Importcommit `19f7f9c34993ede09674dee231e036c538ca04d0`). Diese Dateien wurden
weder geändert noch in das öffentliche Repository übernommen. Der tatsächliche
aktuelle Strato-Dateistand wurde nicht live verglichen.

Der geprüfte Vertrag erlaubt unveränderte Wiederholungen bestätigter Batches
und Objektteile. `status` liefert einen gespeicherten Abschlussbeleg für die
angefragte Lauf-ID und den Dataset-Hash; `abort` ist auf diesen Lauf und Snapshot
beschränkt. Die Mock-Antworten bilden diese Rückmeldungen nach, keine MariaDB.

Unverändert bleiben drei Versuche, 60 Sekunden Request-Timeout, Wartezeiten von
drei und sechs Sekunden sowie die bisherige HTTP-Retry-Auswahl: 408/429 und
5xx werden wiederholt, andere HTTP-Fehler nicht. Ebenso unverändert:
`field-chunks-v1`, vollständige Objekte, Paketlimits, exakte Teil-Hashes,
Vollständigkeitskontrolle, Authentifizierungsheader und Redirect-Sperre. Workflow,
unveränderlicher `GITHUB_SHA`, Zeitpläne und Concurrency wurden nicht angefasst.

## Reproduktion und tatsächliche Tests

```bash
bash scripts/setup-dev.sh
bash scripts/test-offline.sh
# Einzelne neue Suite:
.venv/bin/python tests/test_sync_client.py
```

Am 26.10.2026 mit Python 3.12.14 bestanden Setup und die vollständige Offline-Kette.
Der Wrapper startet nun sechs Skripte: die bisherigen Parser-/Bundesländer-Tests,
elf HH/MV-Fälle, sieben Medien-Queue-Fälle sowie 23 neue Sync-Testfälle. Die HH/MV-
Fälle laufen weiterhin nur einmal über das bestehende Bekanntmachungs-Testskript.
Die vorhandene `fitz`-Deprecation-Warnung war kein Testfehler.

Die neue Suite verwendet ausschließlich synthetische 500-Objekt-Snapshots,
Mock-Transporte und simulierte Antworten. Sie prüft vorübergehende/endgültige
Fehler, unveränderte Wiederholungen verlorener Batch-Antworten, `IncompleteRead`
beim Öffnen und Lesen, ungelesene HTTP-Fehlertexte, vorhandene Retry-/Timeout-
Grenzen, Redirect-Schutz, Fehlerkategorien und Secret-Schutz. Weitere Fälle
decken eigene/fremde bzw. unbestätigte Abort-Rückmeldungen, ungültige Lauf-IDs,
drei verlorene Finalize-Antworten mit anschließender Statusbestätigung, fehlende/
ungültige/fremde Abschlussbelege und verweigerte Objektteil-Hashes ab.

Kein produktiver Request, Collector-Lauf, MariaDB-Import, Mailversand, alter
Workflow-Re-run oder Merge wurde gestartet. Ein echter PHP-/MariaDB-End-to-End-
Test und die historische Netzwerkursache bleiben offen. Eine spätere produktive
Abnahme wäre ein getrennter, ausdrücklich beauftragter aktueller Lauf auf `main`.
