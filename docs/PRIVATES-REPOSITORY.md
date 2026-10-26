# Abrufwege für ein privates Repository vorbereiten

Bezug: [Issue #6](https://github.com/danielfella-del/zvg-radar-data/issues/6).
Die Sichtbarkeit bleibt bis zur getrennten Abnahme öffentlich. Diese Vorbereitung
ist weder eine Privatstellung noch eine Strato-Bereitstellung.

## GitHub-Sync

Der Workflow `sync-zvgpro-db.yml` verwendet für den Download jetzt
`actions/checkout@v4` mit seinem automatisch bereitgestellten `GITHUB_TOKEN` und
der vorhandenen Berechtigung `contents: read`. Ein weiteres GitHub-Secret ist
dafür nicht erforderlich.

- `ref: ${{ github.sha }}` bindet Datensatz und Client an denselben unveränderlichen
  Commit. Ein alter Re-run lädt weiterhin den alten Snapshot.
- `fetch-depth: 1` und Non-cone-Sparse-Checkout wählen ausschließlich
  `/data/auctions.json` und `/.github/scripts/sync-zvgpro-db.py` aus.
- `persist-credentials: false` verhindert, dass die Checkout-Zugangsdaten in der
  lokalen Git-Konfiguration für spätere Schritte erhalten bleiben.
- Die Dateien werden unter die bisherigen Namen `auctions.json` und
  `sync-zvgpro-db.py` kopiert. Der eigentliche MariaDB-Sync, `field-chunks-v1`,
  Hash-Bestätigungen und Vollständigkeitskontrolle bleiben unverändert.
- Zeitplan, Concurrency, Timeout und Daten-/Medienformate bleiben unverändert.

Eine lokale Git-Fixture mit zwei verschiedenen Commits prüfte den tatsächlichen
Sparse-Checkout und die Kopierschritte: Obwohl `main` bereits auf Commit B stand,
stammten beide Dateien bytegleich aus Commit A. Neben-Dateien und Medien fehlten
im Checkout. Dabei wurden weder der Sync-Client noch GitHub Actions gestartet.
Die GitHub-Authentifizierung eines privaten Repositorys ist damit nicht live
bewiesen und gehört zur späteren Abnahme.

Zusätzlich bestand `bash scripts/test-offline.sh` am 26.10.2026 erneut vollständig:
Syntaxprüfung und alle fünf Testskripte, darin elf HH/MV- und sieben Medien-Queue-
Fälle sowie Skript-Assertions. `git diff --check` und die lokalen
Dokumentationslinks wurden geprüft. Es waren keine Parser- oder Datenkorrekturen
erforderlich.

## Website und Medien getrennt vorbereiten

Die gelieferte private Website-Codebasis enthält weitere öffentliche GitHub-
Abrufe. Diese lassen sich nicht durch eine reine Workflow-Änderung reparieren:

| Bereich | Für die spätere Umstellung erforderlich |
| --- | --- |
| SEO-Feed und serverseitige Feed-Aktualisierung | Authentifizierter serverseitiger Abruf; vorhandene gültige Caches bei Quellfehlern erhalten |
| Archivierte Medien und Dokumente | Serverseitiger Abruf; bestehende Objektberechtigungen, öffentliche Bekanntmachungen und amtlichen Portal-Fallback erhalten |
| Freie Foto-Vorschau | Browser lädt über die Website; nur die bisher freie, aktuell serverseitig zugeordnete erste Bildvorschau freigeben |
| Dokumentvergleich | Authentifizierter Archivabruf; aktuelles PDF und Fehlerfälle weiterhin prüfen |
| Bestehender Alert-Feed | Nur den Datenabruf anpassen; Versand, Aktivierung und Suchlogik unverändert lassen |
| Admin-Diagnose | Privaten GitHub-Zugriff prüfen können, ohne einen öffentlichen Zugriff auf Workflow-Code vorauszusetzen |

Website-Code und Konfiguration gehören nicht in dieses öffentliche Repository.
Die Website-Anpassungen werden separat als privates Prüf-/Installationspaket
bereitgestellt. Die kleine Feed-Ansicht `strato/` ist weiterhin kein vollständiger
Website-Stand; ihre bisherige öffentliche Raw-URL funktioniert nach einer
Privatstellung ohne Anpassung ebenfalls nicht mehr.

Die Website benötigt einen ausschließlich serverseitig hinterlegten Lesezugang,
beispielsweise einen Fine-grained Personal Access Token mit Auswahl nur dieses
Repositorys und `Contents: read`. Kein Token in Browser-JavaScript, URLs,
öffentliche Dateien, Chat oder Git-Commits aufnehmen. Es wird hier kein echtes
Token erstellt, gespeichert oder eingerichtet.

Die vorbereitete Website-Variante verwendet die GitHub-Contents-API mit Raw-
Antworten für Feed/Medien. Der Zugriff ist auf die bekannten Feed- und
Medienpfade beschränkt; Tokens werden nicht an Portal-/CDN-Hosts oder Redirect-
Ziele weitergeleitet. GitHub unterstützt an diesem Endpunkt keine Dateien über
100 MB. Am geprüften Commit `3acdf11f1a0a793b3a2c322d7d03d77502f28590`
enthielt der Medienbaum 9.296 Dateien; die größte Datei einschließlich Feed war
18.741.648 Byte groß. Künftige große Dateien benötigen eine gesonderte Lösung.

## Reihenfolge und noch offene Abnahme

1. Workflow-PR prüfen und übernehmen. Das führt noch keinen zusätzlichen
   Collector-, Import-, Mail- oder Deploy-Lauf aus. Die vorhandenen Zeitpläne
   bleiben davon unabhängig aktiv.
2. Private Website-Anpassungen mit dem tatsächlichen Strato-Stand vergleichen,
   betroffene Dateien sichern und separat bereitstellen.
3. Begrenzten GitHub-Lesezugang ausschließlich auf Strato konfigurieren. Die
   Website-Vorlage mit leerem Wert ersetzt keine vorhandene Konfiguration.
4. Bei weiterhin öffentlichem Repository den authentifizierten Website-Abruf
   prüfen: Feed und SEO, ein noch nicht gecachtes Bild und Dokument, freie
   Vorschau, angemeldeter Zugriff sowie Abweisung fremder Objektpfade. Gültige
   Caches alleine belegen die Funktion des neuen Abrufwegs nicht.
5. Einen ausdrücklich beauftragten Sync-Lauf getrennt prüfen. Bestehende
   Offline-Tests starten diesen produktiven Schritt nicht.
6. Vor der Sichtbarkeitsänderung den Codex-/GitHub-App-Zugriff und das verfügbare
   Kontingent für private GitHub Actions prüfen.
7. Erst nach diesen Prüfungen die Sichtbarkeit in GitHub auf privat ändern und
   die Abrufwege erneut prüfen. Bei Fehlern die Umstellung anhalten bzw. anhand
   der gesicherten Dateien zurücknehmen.

Die Produktionsabnahme, ein echter Lesezugang, die Privatstellung und aktuelle
Live-Vollständigkeit sind noch nicht bestätigt. Ohne diese Schritte darf die
Vorbereitung nicht als bereits produktiv funktionierende Umstellung gelten.

## Referenzen

- [GitHub: Repository-Inhalte und unterstützte Raw-Antworten](https://docs.github.com/en/rest/repos/contents#get-repository-content)
- [GitHub: Fine-grained Personal Access Tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
- [actions/checkout](https://github.com/actions/checkout)
