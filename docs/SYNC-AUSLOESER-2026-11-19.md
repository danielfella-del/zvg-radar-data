# Datenbankabgleich nach Datenaufbereitung

Quelle: öffentlicher Pipeline-Stand `4e1771f40f9bfb57ef22a3e9760e4532f63f3c4f`.
Anlass: Admin-Screenshot vom 19.11.2026 mit erfolgreichem, aber 119 Minuten altem
letzten Check. Der Screenshot belegt keine aktuelle Störung der DB-Verbindung.

## Befund

Der DB-Workflow ist für vier Starts pro Stunde konfiguriert. In der gelesenen
Actions-Historie folgte auf den erfolgreichen DB-Lauf 44457060093 (Abschluss
19.11., 05:21 Uhr Europe/Berlin) bis zum Abruf kein weiterer DB-Lauf. Die
Datensammlung 44518533758 wurde dagegen um 13:02 Uhr erfolgreich beendet.
Die genaue Ursache fehlender Zeitplanereignisse ist nicht belegt; GitHub kann
geplante Läufe verzögern oder verwerfen. Der Website-Check kann auch durch den
lokalen Import erfolgen und ist daher nicht mit dem GitHub-Job gleichzusetzen.

Der bisherige Workflow hatte ausschließlich Zeitplan und manuellen Start.
Die erfolgreiche Datensammlung/Anreicherung löste keinen direkten DB-Sync aus.

## Änderung und Grenzen

Zusätzlicher `workflow_run`-Auslöser nach erfolgreichem Abschluss der beiden
bestehenden Daten-Workflows. Zulässig sind nur Hauptzweig, eigenes Repository
und regulär oder manuell gestartete Quellenläufe. Fremde Forks, PR-Läufe,
andere Zweige und fehlgeschlagene/abgebrochene Quellenläufe werden abgewiesen.
Manueller Produktiv-Sync ist auf `main` begrenzt.

Zeitplan, Schreibsperre/Concurrency und `contents: read` bleiben erhalten.
Client und Datensatz werden weiterhin aus demselben festen `GITHUB_SHA` geladen.
Bei `workflow_run` bezeichnet dieser den Standardzweig beim Folgeereignis;
`workflow_run.head_sha` wäre der ältere Eingangsstand des Quellenlaufs.
Kein Bezug auf wechselnde `main`-Download-URLs, keine Artefakte/Fork-Programme.

Die Änderung reduziert die Abhängigkeit von einem weiteren Cron-Ereignis,
garantiert aber keine 15-Minuten-Ausführung bei GitHub-Ausfällen. Der
90-Minuten-Hinweis im Backend bleibt unverändert. Die Aktivierung beseitigt
keinen bereits bestehenden Rückstand ohne anschließend erfolgreichen Sync.

## Offline-Prüfung

- 23 bestehende Sync-Client-Tests erfolgreich: ausschließlich simulierte
  Übertragung, keine Produktivdatenbank oder echte Nachrichten.
- 16 Ereignis-/Berechtigungsfälle mit `@actions/expressions` 0.3.61 geprüft.
- YAML, fester Snapshot, Read-Berechtigung, unveränderter Zeitplan und
  Serialisierung geprüft. Client, Collector, Daten und Medien bytegleich.
- Keine Website-/Darstellungsdatei verändert; kein Strato-Upload erforderlich.

Reproduktion mit Python/PyYAML und Node; die Ausdrucksbibliothek nur lokal
für QA bereitstellen, keine Produktionsabhängigkeit:

```sh
python3 tests/test_sync_client.py
GITHUB_EXPRESSIONS_MODULE=/pfad/zu/@actions/expressions/dist/index.js node tests/test_sync_trigger.mjs
```

GitHub-Referenz:
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run

Aktivierung erst durch Übernahme des PR in `main`. Danach einen neuen Lauf auf
`main` verwenden; kein Re-run eines alten Daten-Snapshots. Produktionsabschluss
an vollständigem Sync-Beleg und anschließendem Backend-Zeitstempel bestätigen.
Rückweg: die zwei neuen Auslöser-/Jobfilter-Blöcke zurücknehmen; vorhandene
Zeitpläne und gespeicherte Verfahrensdaten bleiben erhalten.
