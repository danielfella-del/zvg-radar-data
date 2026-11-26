# Private Laufzeitdaten: kontrollierte Umstellung

Dieser Ablauf beschreibt die Umstellung. Die vorhandenen Codeänderungen allein
beweisen weder ein Deployment noch einen erfolgreichen Produktivimport.
Das Repository `danielfella-del/zvg-radar-data` bleibt öffentlich und enthält
anschließend Pipeline-Code und synthetische Tests. Verfahrens-JSON, Dokumente,
Fotos und personenbezogene Diagnoseausgaben werden nicht mehr eingecheckt.

## Verträge

- STRATO speichert den Laufzeitbestand außerhalb des öffentlich erreichbaren
  Webroots. Das authentifizierte API `/api/runtime-storage.php` verwendet
  `private-runtime-v1` und das vorhandene Secret `ZVGPRO_DB_SYNC_TOKEN`.
- Ein Snapshot ist durch Generation, Bytezahl und SHA256 gebunden. Jeder Export
  prüft diese Werte; jeder Schreibvorgang verlangt die gelesene Generation als
  Vergleichswert. Ein konkurrierender Austausch führt zum Abbruch.
- Medien werden vor dem Snapshot übertragen und durch SHA256 identifiziert.
  Die Website liefert sie nur über die kontrollierte Medienroute aus. Die Route
  prüft die aktuelle Verfahrens-/Anhangszuordnung und Veröffentlichungsrücknahmen.
- `field-chunks-v1`, vollständige Objekte, Hash-Bestätigungen und die
  Vollständigkeitskontrolle des MariaDB-Sync bleiben erhalten. Der Sync nutzt die
  vollständig geprüften Bytes einer privaten Generation; der verwendete Client
  stammt aus dem beim Checkout festgehaltenen Code-Commit.
- Collector, Anreicherung, Nachpflege, Sync, Diagnose und Umstellung verwenden
  dieselbe Actions-Concurrency-Gruppe `zvg-data-writer`. Die API-Generationsprüfung
  schützt zusätzlich gegen Schreiber außerhalb dieser Gruppe.
- Die regulären Workflows verlangen eine bestätigte aktive private Speicherung.
  Fehlt sie oder schlägt eine Prüfung fehl, brechen sie ab. Es gibt keinen
  Rückfall auf JSON oder Medien aus GitHub, Actions-Artefakten oder Caches.
- Jeder Lauf lädt die maßgeblichen Rücknahme-Hashes authentifiziert. Alle
  Collector-Unterprozesse verwenden diese Richtlinie; unmittelbar vor dem
  Speichern wird sie erneut geladen. Das Backend prüft sie beim Abschluss
  unabhängig. Fehlende oder ungültige Richtlinien brechen den Lauf ab.
- Die optionale gezielte Dokumentnachpflege liest ihre Objektliste aus dem
  separaten Secret `ZVGPRO_BACKFILL_IDS`. Ohne diese Liste bricht sie ab. Keine
  Objekt-IDs als öffentlich einsehbare Workflow-Eingaben verwenden. Dieses
  optionale Ziel-Secret ist kein neuer Zugangs-Token.
- Auch zusammengefasste Diagnose-Dateien werden nicht im öffentlichen Repository
  abgelegt. Produktionslogs enthalten ausschließlich geprüfte technische Metadaten.

## Vorbereitungen

1. Aktuellen Code und Quellstand prüfen. Die alte Einmal-Historienwartung bleibt
   deaktiviert; ihre historischen Ref-Leases dürfen nicht wiederverwendet werden.
2. Private STRATO-Speicherung samt kontrollierter Medienauslieferung bereitstellen.
   Den konfigurierten absoluten Speicherpfad außerhalb des Webroots auf
   Schreibbarkeit und nichtöffentliche Erreichbarkeit prüfen. `health` muss
   `storage_ready=true` und das vereinbarte Protokoll bestätigen. Vor dem
   kontrollierten Cutover bleibt der Aktivierungsmarker aus.
3. Für das Cutover-Fenster die bisherigen datenpublizierenden Workflows anhalten
   und bereits laufende Schreiber geordnet abschließen lassen. Auch wartende alte
   Workflow-Versionen dürfen nach dem Cutover nicht mehr schreiben. Aktuellen
   Laufstatus direkt prüfen; alte Statusnotizen sind kein Beleg.
4. Neue Workflow-Versionen übernehmen. Sie besitzen ausschließlich
   `contents: read`, laden nur Code und schreiben keine Laufzeit-Artefakte.
   Der bestehende Sync-Token bleibt ein GitHub-Secret; seinen Wert weder anzeigen
   noch in Dateien oder Eingaben kopieren.

## Bestand vorbereiten und prüfen

1. Den aktuellen zulässigen Datenstand als vollständigen 40-stelligen Commit-SHA
   prüfen und festhalten. Danach keine Legacy-Schreibläufe mehr zulassen.
2. `ZVG privaten Laufzeitbestand umstellen` mit `operation=bootstrap` und genau
   diesem `source_sha` starten. Nur diese einmalige Bootstrap-Funktion darf den
   bisherigen Snapshot und die zugehörigen Medien aus GitHub lesen. Sie liest
   nicht die vollständige Git-Historie.
3. Die Veröffentlichungsrichtlinie muss bereits vor dem Import greifen:
   zurückgezogene Verfahren einschließlich des gezielten bisherigen Falls dürfen
   weder als Datensätze noch als Medien übernommen werden. Nur tatsächlich im
   Snapshot referenzierte, zulässige Anhänge übertragen.
4. Bootstrap legt zunächst den privaten Bestand an. Alle Medien und der gesamte
   Snapshot müssen mit bestätigten Bytezahlen und SHA256 vorliegen. Technische
   Belege enthalten nur Summen, Hashes, Generationen und den Quellcommit; keine
   Objekt-IDs, Adressen, Originaldateien oder Serverantwortkörper veröffentlichen.
5. Generation und SHA256 des vorbereiteten Snapshots festhalten. Fehlende Medien,
   eine abweichende Generation, Hashfehler oder Transportfehler stoppen den
   Cutover. Nicht durch einen leeren Ersatzbestand oder einen unkontrollierten
   Wiederholungslauf umgehen.

## Aktivieren und öffentliches Arbeitsverzeichnis bereinigen

1. Den Umstellungsworkflow mit `operation=activate`, der bestätigten `generation`
   und deren `sha256` starten. Der Runner exportiert genau diesen privaten
   Snapshot, prüft seine vollständigen Bytes, synchronisiert ihn über das
   bestehende DB-Protokoll und verlangt den abgeschlossenen Importbeleg.
2. Erst danach darf das Backend den privaten Aktivierungsmarker schreiben. Die
   Aktivierung bestätigt zusätzlich die Medienzuordnungen. Bei Fehler bleibt
   der Marker unverändert; ein fehlender Importbeleg ist kein erfolgreicher
   Abschluss.
3. API-Zustand, aktiven Marker, vollständigen DB-Import und die Website prüfen.
   Die Prüfung muss sowohl ein zulässiges Objekt als auch die Ablehnung eines
   zurückgezogenen Objekts und seiner direkten Medien-URLs umfassen. Eine
   Anmeldeseite, `403`, `429` oder ein Netzwerkfehler beweist keine Löschung.
4. Erst nach diesen Belegen `data/`, `media/` und personenbezogene Diagnose-
   oder Exporteinträge aus dem aktuellen öffentlichen Git-Baum entfernen. Die
   neue `.gitignore` verhindert versehentliche erneute Aufnahme; sie entfernt
   bereits versionierte Dateien nicht selbst. Das öffentliche Repository bleibt
   bestehen. Dieser Commit ist keine Historienumschreibung.
5. Reguläre Workflows mit der neuen Version wieder zulassen. Einen neuen, gezielt
   erforderlichen Produktionslauf prüfen: privater Snapshot, erfolgreicher
   Import und ausschließlich technische Logs. Keine alten Läufe erneut starten.

## Fehler und Wiederaufnahme

- Ein fehlgeschlagener Bootstrap oder Upload aktiviert den Bestand nicht.
  Bestehende veröffentlichte Generationen bleiben maßgeblich. Vor einer
  Wiederaufnahme den tatsächlichen API-Zustand prüfen und erst dann entscheiden,
  ob Uploads fortgesetzt oder ein neuer geprüfter Import nötig sind.
- Bei einem erneut ausdrücklich gestarteten Bootstrap wird jede Quelldatei
  wieder gegen den unveränderlichen Git-Blob geprüft. Bereits vorhandene Medien
  werden nur dann nicht erneut übertragen, wenn der private Speicher genau ihre
  Bytezahl und SHA256 bestätigt. `403`, `409`, `429`, `503` und Netzwerkfehler
  werden dabei nicht als fehlende Datei behandelt. Der Snapshot wird zuletzt
  unter Generationsschutz geschrieben; ein Teilimport aktiviert nichts.
- Ein Generationskonflikt verlangt einen neuen Export und Abgleich. Niemals
  einen alten Hash oder eine alte Generation als Freigabe wiederverwenden.
- Bei verlorenem Sync-Abschluss den vorhandenen Importbeleg abfragen; keinen
  parallelen zweiten Import starten. Ein grüner Transporttest allein reicht
  nicht.
- Nach Aktivierung darf kein Rollback auf die frühere GitHub-Datenquelle
  erfolgen. Reparaturen betreffen den privaten Speicher oder den Code. Der
  Aktivierungsmarker liegt getrennt von den Snapshot-Dateien und wird nicht
  durch eine normale Zustandswiederherstellung zurückgesetzt.
- Zusätzlich bleiben das unabhängige Aktivierungssiegel
  `private/runtime-storage-required.json` und die Konfiguration
  `runtime_storage_required=true` bei Deployments und Wiederherstellungen
  erhalten. Fehlen danach Speicher oder Marker, muss die Website geschlossen
  fehlschlagen, statt wieder GitHub-Daten abzurufen.
- Runner-Arbeitsdateien liegen nur im temporären Actions-Verzeichnis
  `zvg-private-runtime`. Runner und abschließender Workflow-Schritt entfernen sie;
  keine Upload-Artefakte oder Daten-Caches anlegen. Alte Actions-Artefakte und
  Hosting-Backups benötigen bei Bedarf eine eigene gezielte Bereinigung.

## Grenzen der Löschbestätigung

Die Umstellung verhindert neue öffentliche Git-Versionen der Laufzeitdaten.
Sie entfernt keine bereits gespeicherten GitHub-PR-Refs, unerreichbaren Git-
Objekte, Raw-/Blob-/Commit-Caches oder fremde Kopien. Für den bereits gemeldeten
Fall bleibt die separate GitHub-Support-Bereinigung samt Prüfung beider konkreten
PDF-URLs erforderlich. Erst Supportabschluss und bestätigte Nichterreichbarkeit
sind ein Abschlussbeleg. STRATO-Backups, Laufzeitdaten und kundenseitige Notizen
dürfen ohne gesonderten Nachweis nicht als vollständig gelöscht bezeichnet werden.
