# Paperless Classification 2.2.0

Version 2.2.0 erweitert die dokumenttypabhängige Pflichtprüfung und verbessert die Klassifizierung privater Steuer- und Rechnungsunterlagen.

## Änderungen

- Pflichtfelder stehen direkt unter jedem Dokumenttyp als kompakte Chip-Auswahl zur Verfügung.
- Ausgewählte Pflichtfelder werden automatisch für die Extraktion des Dokumenttyps aktiviert.
- Pflichtwerte mit Buchstaben aus fremden Schriftsystemen gelten als ungültig und lösen den vorhandenen einmaligen Force-OCR-/Review-Ablauf aus.
- Das Feld „Referenznummer“ kann Steuer-, Akten-, Geschäfts-, Vertrags- und Kundennummern aufnehmen; Rechnungsnummern bleiben separat.
- Steuerberater-Begleitschreiben werden vom zugrunde liegenden Finanzamtsbescheid unterschieden.
- Gutschriften und Rechnungskorrekturen ohne andere Zahlungsinformation verwenden „Überweisung“, statt unnötig `forceocr` anzufordern.
- Nicht im Dokument belegte Bankwerte werden verworfen.
- Veraltete Speicherpfad-Profile einer anderen Paperless-Instanz werden nicht mehr aufgrund gleicher numerischer IDs verwendet.

## Container

```text
ghcr.io/chrschacht/paperless-classification-backend:2.2.0
ghcr.io/chrschacht/paperless-classification-frontend:2.2.0
```

Die Images werden für `linux/amd64` und `linux/arm64` veröffentlicht. `latest` wird mit dem Produktionsstand auf `main` aktualisiert.

Vor dem Update wird wie immer eine Sicherung des persistenten `data/`-Verzeichnisses empfohlen.
