# Paperless Classification 2.0.1

Version 2.0.1 behebt einen Fehler beim Speichern der Status-Tag-Einstellungen.

## Behoben

- Die Aktivierungszustände der Status-Tags „Klassifiziert“, „Prüfen“ und „Tag-Ideen“ werden nun über den gemeinsamen Button „Einstellungen speichern“ persistiert.
- Namen der Status-Tags und zusätzliche dokumenttypabhängige Status-Tag-Regeln werden weiterhin gemeinsam gespeichert.
- Die gespeicherten Werte bleiben nach einem vollständigen Neuladen der Benutzeroberfläche erhalten.

## Container

```text
ghcr.io/chrschacht/paperless-classification-backend:2.0.1
ghcr.io/chrschacht/paperless-classification-frontend:2.0.1
```

Die Images werden für `linux/amd64` und `linux/arm64` veröffentlicht. Das Tag `latest` verweist nach erfolgreichem Produktions-Build ebenfalls auf diese Version.

## Aktualisierung

```bash
cd /opt/paperless-classification
docker compose pull
docker compose up -d
docker compose ps
```

Für dieses Patch-Update ist keine Datenbankmigration erforderlich.
