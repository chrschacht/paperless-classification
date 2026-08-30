# Paperless Classification 2.1.1

Version 2.1.1 verbessert die Darstellung der aus Paperless-ngx übernommenen Markenidentität.

## Behoben

- Individuelle Paperless-Logos werden auf einer kontrastreichen, nahezu weißen Fläche mit Kontur und Schatten dargestellt.
- Helle, dunkle und farbige Logos bleiben dadurch vor der getönten Liquid-Glass-Navigation besser erkennbar.
- Der Anwendungstitel nutzt die vollständige Breite des Seitenmenüs.
- Die Schriftgröße wird anhand der tatsächlich verfügbaren Breite dynamisch angepasst; der vollständige Titel bleibt ohne Auslassungspunkte sichtbar.
- Die mobile Titelzeile verwendet dieselbe automatische Größenanpassung.

## Container

```text
ghcr.io/chrschacht/paperless-classification-backend:2.1.1
ghcr.io/chrschacht/paperless-classification-frontend:2.1.1
```

Die Images werden für `linux/amd64` und `linux/arm64` veröffentlicht. Nach erfolgreichem Produktions-Build verweist auch `latest` auf Version 2.1.1.

## Aktualisierung

```bash
cd /opt/paperless-classification
docker compose pull
docker compose up -d
docker compose ps
```

Für dieses Patch-Update ist keine Datenbankmigration erforderlich.
