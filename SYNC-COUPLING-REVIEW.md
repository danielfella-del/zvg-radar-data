# Direkte Kopplung zwischen Daten-Push und DB-Sync

Der geprüfte Main-Quellstand ist `6404f4aed603a7e722cbca37eb554e25c723426d`.
Dieser Entwurf ändert nur GitHub-Workflows und einen Offline-Regressionstest.
Er wurde nicht auf Main veröffentlicht oder in Produktion ausgeführt.

## Belegtes Problem

Nach der Historienbereinigung wurden erfolgreiche Update- und Enrichment-Läufe
beobachtet, ohne anschließend sichtbaren `workflow_run`-DB-Sync. Die Namen,
Branch- und Ereignisfilter stimmen mit diesen erfolgreichen Läufen überein.
Ein konkreter GitHub-interner Grund ist nicht nachgewiesen.

Der geplante DB-Sync
[45783709810](https://github.com/danielfella-del/zvg-radar-data/actions/runs/45783709810)
lief am 26.11.2026 automatisch erfolgreich: Serverlauf 1807 bestätigte
3383/3383 Verfahren, alle unverändert, um 09:35:38 Uhr Europe/Berlin.
Danach endete Enrichment
[45797003484](https://github.com/danielfella-del/zvg-radar-data/actions/runs/45797003484)
um 11:05:03 Uhr erfolgreich; in der anschließend gelesenen Runliste war kein
dazugehöriger `workflow_run`-DB-Sync vorhanden.

## Änderung

- Update und Enrichment liefern nach erfolgreichem Commit/Push ihre tatsächlich
  veröffentlichte HEAD-SHA als Joboutput. Bei unveränderten Daten ist das die
  bereits veröffentlichte ausgecheckte SHA.
- Ein separater Folgejob ruft den vorhandenen DB-Sync über `workflow_call` auf.
  Ein fehlgeschlagener Producer löst diesen Job nicht aus. Das vorhandene
  `ZVGPRO_DB_SYNC_TOKEN` wird ausdrücklich weitergegeben; es gibt kein neues Secret.
- Die gemeinsame Schreibsperre gilt nur für den Producer-Job. Der DB-Sync hat
  weiterhin seine eigene gemeinsame Sperre. Kein Job hält die Schreibsperre,
  während er auf die Sync-Sperre wartet.
- Erst nachdem ein Sync-Job seine Sperre erhalten hat, checkt er den aktuellen
  Main aus. Ein älterer wartender Aufrufer kann daher keinen alten Snapshot
  zurückschreiben. Seine veröffentlichte SHA wird validiert und zur Zuordnung
  protokolliert. Hat Main sich inzwischen fortentwickelt, wird ausdrücklich der
  neue Main verwendet.
- Datensatz, Veröffentlichungsschutz und Sync-Client stammen aus demselben
  unveränderlichen Checkout. Der Veröffentlichungsschutz bleibt vor jeder
  DB-Anfrage erhalten. Keine private Speicherarchitektur wird aktiviert.
- `workflow_run` entfällt, um den direkten Aufruf nicht zusätzlich auszulösen.
  Der bestehende 15-Minuten-Cron und manuelle Aufrufe bleiben erhalten.

Producer, Consumer und Caller sind auf das eigene Repository, Main sowie
`schedule`/`workflow_dispatch` beschränkt. Ein manueller Aufruf auf einem
Featurebranch startet deshalb keine Verarbeitung oder Schreibzugriffe auf Main.
Wiederverwendbare Workflows erben
Ereignis und Ref des Aufrufers. Die Caller geben nur Leserechte weiter.

## Validierung und Grenze

`python tests/test_direct_db_sync_workflows.py`: **12 Tests erfolgreich**.
Geprüft wurden die Workflow-Verknüpfung, getrennte Sperren, Rechte und Secret,
alle Shellblöcke mit `bash -n`, unveränderte Daten, tatsächliche SHA nach einem
lokalen Rebase/Push, ungültige Eingaben ohne Shellausführung, ein verspäteter
Aufrufer bei inzwischen neuerem Main und der Abbruch bei zurückgezogenen Daten.
Die Git-Integrationstests verwenden ausschließlich temporäre lokale Repositories;
der echte Sync-Client wird nicht ausgeführt.

Der unabhängige Review wiederholte alle 12 Tests erfolgreich. Zusätzlich prüfte
`actionlint` 1.7.12 alle drei Workflow-Dateien ohne Befund (Exit 0). Das offizielle
Linux-amd64-Release wurde gegen die Release-Prüfsumme verifiziert; SHA256:
`8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`.
Shellcheck und Pyflakes waren dabei nicht angebunden; Bashsyntax wurde separat
mit `bash -n` geprüft. Die GitHub-Ausführung des neuen Pfads ist erst nach einer
ausdrücklichen Aktivierung belegbar.

Diese Änderung beseitigt die Abhängigkeit vom separaten `workflow_run`-Ereignis
nach einem erfolgreichen Producer. Sie kann einen vollständig ausgebliebenen
GitHub-Scheduler-Start weder erzeugen noch garantieren. Die weiterhin beobachteten
mehrstündigen Lücken in allen drei geplanten Workflows bleiben eine gesonderte
Ursachenfrage; Cron-Frequenzen wurden deshalb nicht ohne Befund verändert.

## Dateien

Zu veröffentlichen: `.github/workflows/update-zvg.yml`,
`.github/workflows/enrich-zvg.yml`, `.github/workflows/sync-zvgpro-db.yml`,
`tests/test_direct_db_sync_workflows.py` und diese Reviewnotiz.

`collector/publication_policy.py` liegt hier nur als unveränderte Testgrundlage
aus Main, Blob `a897872cd3e8e3820f0c0edc1c1569446cb7efcd`; sie ist keine Änderung.
