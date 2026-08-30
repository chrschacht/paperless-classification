# Upgrade auf Paperless Classification 2.0

Version 2.0 ist eine eigenständige Hauptversion. Vor dem Upgrade muss das komplette Datenverzeichnis gesichert werden.

## Wesentliche Änderungen

- Projekt- und Image-Name: `paperless-classification`
- Unterstützte LLM-Provider: ausschließlich Ollama und OpenAI
- Cloud Sync / Import entfernt; verarbeitet werden nur Dokumente aus Paperless-ngx
- Veraltete Provider-Felder und Cloud-Import-Tabellen werden aus der SQLite-Datenbank entfernt
- Neue Oberfläche und Navigation
- Konfigurierbare Hintergrundbilder und vereinheitlichte Glass-Kacheln
- Neue Verlauf-zurücksetzen-Funktionen für Klassifikation und OCR

## Upgrade mit Docker Compose

1. Laufende Automatik stoppen und Backup erstellen.
2. Compose-Datei auf die neuen Images umstellen:

```yaml
backend:
  image: ghcr.io/chrschacht/paperless-classification-backend:2.0.0
frontend:
  image: ghcr.io/chrschacht/paperless-classification-frontend:2.0.0
```

3. Images laden und Container neu erstellen:

```bash
docker compose pull
docker compose up -d
docker compose ps
```

4. Im Frontend Paperless-ngx, Provider, OCR und Status-Tags kontrollieren.
5. Einige Dokumente manuell klassifizieren und anwenden.
6. Erst danach Watchdog oder Auto-Klassifikation wieder aktivieren.

## Datenbankmigration

Die Migration läuft beim Backend-Start automatisch. Unterstützte Ollama- und OpenAI-Werte werden übernommen. Entfernte Provider- und Cloud-Import-Felder werden anschließend gelöscht. Eine Rückkehr zu einer älteren Version sollte deshalb nur durch Wiederherstellung des vorherigen Backups erfolgen.

## Repository-Wechsel

Die öffentliche Distribution befindet sich unter:

```text
https://github.com/chrschacht/paperless-classification
```

Bestehende Installationen sollten den Git-Remote beziehungsweise die Clone-URL auf dieses Repository umstellen.
