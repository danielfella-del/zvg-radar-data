# Veröffentlichungsrücknahmen in der Datenpipeline

Die zentrale Regel in `collector/publication_policy.py` speichert nur SHA-256-
Prüfwerte normalisierter technischer Objekt-IDs. Namen, Anschriften, Aktenzeichen,
Beschwerdetexte und Originalunterlagen gehören nicht in Issues oder Testdaten.
Die Prüfsummen sind Sperrmerkmale; sie sind kein Nachweis einer Anonymisierung.

## Anwendung

- Sammlung: Rücknahmen aus Altbeständen und frischen Ergebnissen entfernen.
- Zusammenführung und Historie: auch verschachtelte Stichproben und Verweise
  entfernen, damit entfernte Verfahren nicht als `removed_sample` fortbestehen.
- Details, Medien, Gutachtenfotos und Analysen: Rücknahmen vor Verarbeitung
  ausschließen. Eine ausdrücklich angeforderte Nachpflege wird abgelehnt.
- Schreibende Haupt-Workflows: Daten und zugehörige Medienordner vor Verarbeitung
  und vor Commit bereinigen. Nach einem Rebase vor dem Push erneut prüfen.
- MariaDB-Sync: denselben unveränderlichen Commit für Daten, Client und Regel
  authentifiziert mit `actions/checkout` laden. Bei einem gesperrten Inhalt den
  gesamten Sync vor dem Import abbrechen; den Snapshot nicht still verändern.

Der authentifizierte Checkout funktioniert auch bei einem privaten Repository.
Das bereitet allein den GitHub-Sync vor. Die öffentlichen GitHub-Abrufe der
Website und ihre Medienzugriffe müssen vor einer Privatstellung separat
umgestellt und auf Strato geprüft werden.

## Grenzen und historische Bereinigung

Ein Commit mit dieser Sperre löscht keine älteren Commits, alten Branch-Stände,
Pull-Request-Referenzen, Releases, Actions-Artefakte oder externen Caches.
Alte Workflow-Re-runs können alten Code enthalten. Nach einer historischen
Bereinigung keine alten Klone oder Branches wieder zusammenführen und keine
alten Jobs erneut ausführen. Vorher Schreibprozesse koordiniert anhalten.

Für ein Löschverfahren separat dokumentieren:

1. Alle Branches/Tags, historischen Datenversionen, Medien und inhaltlich
   identischen Dateiobjekte prüfen; interne PR-Referenzen mit erfassen.
2. Nur die betroffenen Datensätze und Dateien aus der Historie entfernen;
   andere Verfahrensdaten und aktuelle Quellcodeänderungen erhalten.
3. Nach Prüfung die umgeschriebenen Referenzen kontrolliert übernehmen. Das
   ändert Commit-IDs und benötigt eine gesonderte Freigabe für den Eingriff.
4. GitHub Support um Prüfung der verbliebenen PR-Referenzen, zwischengespeicherten
   Ansichten und nicht mehr referenzierten Objekte bitten. Eine lokale
   Umschreibung allein beweist deren Entfernung nicht.
5. Rohdatei-, Blob-, Commit- und Website-Zugriffe ohne Anmeldung nachprüfen.
6. Weitere eigene Kopien und Sicherungen inventarisieren und ihren tatsächlichen
   Löschstand dokumentieren. Ungeprüfte Sicherungen nie als gelöscht bestätigen.

Referenz: [GitHub: Entfernen sensibler Daten](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

## Prüfstand 23.11.2026

- `bash scripts/test-offline.sh`: bestanden, einschließlich 15 neuer synthetischer
  Rücknahmefälle und bestehender Parser-, Nachpflege-, Medien- und Sync-Tests.
- 16 Routing-Fälle mit GitHubs Expression-Evaluator: bestanden.
- Tatsächlicher lokaler Sparse-Checkout mit zwei verschiedenen Commits: Daten
  und Sync-Client stammen gemeinsam aus dem angeforderten alten Commit;
  der Medienbestand ist nicht Teil des Checkouts.
- YAML, Shell-Syntax und `git diff --check`: bestanden.

Diese Prüfungen starten keinen Produktionsimport und versenden keine E-Mails.
Sie bestätigen weder die Bereinigung der GitHub-Historie noch einen aktuellen
Löschstand sämtlicher Sicherungen.
