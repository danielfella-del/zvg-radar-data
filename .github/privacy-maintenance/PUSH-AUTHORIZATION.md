# Einmalige Push-Berechtigung

Der Wartungslauf benötigt wegen der historischen Workflow-Dateien eine separate Push-Berechtigung. Der normale `GITHUB_TOKEN` bleibt für Actions-Steuerung, Klon, Refprüfungen und Packmessung zuständig. Ein vorhandenes Secret beweist noch keine wirksame Berechtigung.

Der Repository-Owner richtet direkt bei GitHub einen **fine-grained personal access token** ein:

- Resource owner: `danielfella-del`; Repository access: **Only select repositories**, ausschließlich `zvg-radar-data`.
- Repository permissions: **Contents: Read and write**, **Workflows: Write**; nur das automatisch erforderliche **Metadata: Read** zusätzlich. Keine Actions-, Account- oder Administrationsrechte hinzufügen.
- Ablauf spätestens nach einem Tag festlegen.
- Den Wert direkt unter Repository **Settings → Secrets and variables → Actions → New repository secret** als `PRIVACY_MAINTENANCE_PUSH_TOKEN` speichern. Nicht in Chat, Dateien, Befehlsargumente oder Logs kopieren.

Erst nach sicherer Einrichtung und erneuter Prüfung des vollständigen Refstands darf APPLY mit dessen aktuellem Freigabehash starten. Ohne Secret hält APPLY vor Workflow-Pause und Klon an; Preflight bleibt ohne Secret möglich. Vorhandene Stage-Refs benötigen weiterhin eine gesonderte Recovery.

Nach verifiziertem Erfolg den Token bei GitHub widerrufen und das Repository-Secret entfernen. Keine vorhandene Berechtigung wird durch diese Anleitung bestätigt.

GitHub-Dokumentation: [Personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens), [Actions secrets](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions).
