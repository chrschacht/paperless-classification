# Paperless Classification 2.0.0

Version 2.0.0 ist die erste öffentliche Veröffentlichung unter dem Namen **Paperless Classification**.

## Höhepunkte

- eigenständiges Projekt und öffentliche Distribution unter `chrschacht/paperless-classification`
- vollständig überarbeitete Liquid-Glass-Oberfläche mit responsiven Einstellungsrastern
- KI-Klassifikation mit Ollama oder OpenAI
- konfigurierbare Custom Fields und dokumenttypabhängige Pflichtfelder
- einmalige Force-OCR-Wiederholung bei fehlenden Pflichtwerten
- OCR Smart-Skip, Watchdog, Größenlimit und Verlauf-zurücksetzen
- zusätzliche deterministische Status-Tag-Regeln
- Metadaten-Bereinigung, Dokumenten-Chat und Duplikatsuche
- Cloud Sync / Import und nicht mehr unterstützte Provider entfernt
- bereinigte Datenbankmigration für die eigenständige Anwendung
- neue Benutzer-, Datenschutz-, Upgrade- und Abnahmedokumentation

## Container

```text
ghcr.io/chrschacht/paperless-classification-backend:2.0.0
ghcr.io/chrschacht/paperless-classification-frontend:2.0.0
```

Die Images werden für `linux/amd64` und `linux/arm64` veröffentlicht.

## Upgrade

Version 2.0 enthält Datenbank- und Konfigurationsänderungen. Vor dem Upgrade muss das gesamte Datenverzeichnis gesichert werden. Die vollständige Anleitung steht in [UPGRADE_V2.md](UPGRADE_V2.md).

## Herkunft und Lizenz

Paperless Classification ist eine eigenständige Weiterentwicklung auf Basis von [AI Paperless Organizer](https://github.com/syberx/AI-Paperless-Organizer). Der ursprüngliche MIT-Copyright- und Lizenzhinweis bleibt in `LICENSE` erhalten. Details stehen in `THIRD_PARTY_NOTICES.md`.
