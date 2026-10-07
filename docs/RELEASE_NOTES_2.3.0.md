# Paperless Classification 2.3.0

Version 2.3.0 erweitert die bearbeitbare Klassifizierung um frei auswählbare Custom Fields und korrigiert die Statusanzeige der Prüfwarteschlange.

## Änderungen

- Zusätzliche Paperless Custom Fields können sowohl nach einer manuellen Klassifizierung als auch beim Prüfen eines Dokuments ausgewählt und befüllt werden.
- Manuell ausgewählte Felder können bewusst auch dann angewendet werden, wenn sie nicht für den erkannten Dokumenttyp zur automatischen Extraktion konfiguriert sind.
- Die bestehenden Feldvalidierungen bleiben für manuell ergänzte Werte aktiv.
- „Zur Prüfung“ zeigt die tatsächliche Anzahl offener Prüfeinträge statt eines kumulierten Laufzählers.
- Die Statusanzeige wird nach Änderungen an der Prüfwarteschlange sowie beim erneuten Aktivieren der Anwendung unmittelbar aktualisiert.

## Container

```text
ghcr.io/chrschacht/paperless-classification-backend:2.3.0
ghcr.io/chrschacht/paperless-classification-frontend:2.3.0
```

Die Images werden für `linux/amd64` und `linux/arm64` veröffentlicht. Vor dem Update wird eine Sicherung des persistenten `data/`-Verzeichnisses empfohlen.
