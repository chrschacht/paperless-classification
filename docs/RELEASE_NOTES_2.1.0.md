# Paperless Classification 2.1.0

Version 2.1.0 übernimmt die visuelle Identität der verbundenen Paperless-ngx-Instanz in Paperless Classification.

## Neu

- Der in Paperless-ngx konfigurierte Anwendungstitel wird automatisch aus den UI-Einstellungen gelesen.
- Das individuelle Paperless-Anwendungslogo erscheint auf großen Bildschirmen im dauerhaft sichtbaren Seitenmenü und auf kleinen Bildschirmen kompakt in der Titelzeile.
- Die Paperless-Designfarbe tönt Seitenmenü und Titelzeile in kontrastreicher Liquid-Glass-Optik.
- Der Browser-Seitentitel enthält zusätzlich den Namen der verbundenen Paperless-Instanz.

## Sicherheit und Kompatibilität

- Das Logo wird über Paperless Classification ausgeliefert; Paperless-API-Token und interne URL werden nicht an den Browser weitergegeben.
- Logo-URLs anderer Hosts, Nicht-Bilddateien und Dateien über 5 MB werden abgewiesen.
- Designfarben werden ausschließlich im Format `#RRGGBB` akzeptiert und für eine lesbare Darstellung kontrolliert abgedunkelt.
- Wenn Titel, Logo oder Farbe nicht verfügbar sind, verwendet die Oberfläche sichere Standardwerte.

Die Umsetzung verwendet die offizielle Paperless-ngx-Schnittstelle `/api/ui_settings/`.

## Container

```text
ghcr.io/chrschacht/paperless-classification-backend:2.1.0
ghcr.io/chrschacht/paperless-classification-frontend:2.1.0
```

Die Images werden für `linux/amd64` und `linux/arm64` veröffentlicht. Nach erfolgreichem Produktions-Build verweist auch `latest` auf Version 2.1.0.

## Aktualisierung

```bash
cd /opt/paperless-classification
docker compose pull
docker compose up -d
docker compose ps
```

Für dieses Feature-Update ist keine Datenbankmigration erforderlich.
