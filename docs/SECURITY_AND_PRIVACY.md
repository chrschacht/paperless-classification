# Sicherheit und Datenschutz

Paperless Classification verarbeitet Dokumentmetadaten und – abhängig von der aktivierten Funktion – auch OCR-Volltext. Eine Installation sollte deshalb wie Paperless-ngx selbst als vertrauliches System behandelt werden.

## Datenflüsse

### Paperless-ngx

Die Anwendung liest Dokumente, Metadaten, Tags, Korrespondenten, Dokumenttypen, Speicherpfade und Custom Fields über die Paperless-ngx-API. Beim Anwenden einer Klassifikation oder einer Bereinigungsaktion werden diese Daten in Paperless-ngx geändert.

### Ollama

Bei einem lokalen Ollama-Server bleiben die an das Modell übertragenen Inhalte innerhalb der eigenen Infrastruktur. Entscheidend ist, dass auch der konfigurierte Ollama-Endpunkt tatsächlich unter eigener Kontrolle steht.

### OpenAI

Wird OpenAI ausgewählt, können je nach Funktion Metadaten oder OCR-Texte an OpenAI übertragen werden. Vor der Aktivierung sind die eigenen Datenschutz-, Vertrags- und Geheimhaltungspflichten zu prüfen.

| Funktion | Typisch verarbeitete Daten |
|---|---|
| Metadaten-Bereinigung | Namen von Tags, Korrespondenten und Dokumenttypen |
| KI-Klassifikation | OCR-Text, bestehende Metadaten und mögliche Zielwerte |
| OCR-Qualitätsbewertung | OCR-Ergebnisse und gegebenenfalls Dokumentinhalte |
| Dokumenten-Chat | Suchanfrage, gefundene Textabschnitte und Quellenmetadaten |

## Lokale Speicherung

Die Datei `data/organizer.db` enthält Konfiguration, Historien und – sofern eingetragen – Zugangsdaten. RAG-Indizes und OCR-Artefakte liegen ebenfalls unter `data/`. Dieses Verzeichnis darf nicht veröffentlicht und sollte verschlüsselt gesichert werden.

Das Repository ignoriert Datenbanken, Laufzeitdaten, lokale `.env`-Dateien, temporäre Dokumente und interne Design-Prüfungen. Vor jedem öffentlichen Beitrag sollte trotzdem geprüft werden:

```bash
git status --short
git ls-files | grep -E '\.(db|sqlite|pdf)$'
```

## Netzwerk und Authentifizierung

- Die mitgelieferte Compose-Konfiguration bindet die Weboberfläche standardmäßig nur an `127.0.0.1`.
- Für Zugriffe aus einem LAN oder dem Internet sind HTTPS und eine vorgeschaltete Authentifizierung erforderlich.
- API-Tokens und OpenAI-Schlüssel gehören ausschließlich in die lokale Konfiguration, niemals in Compose-Dateien oder Git.
- Der Backend-Port wird in der Produktionskonfiguration nicht direkt veröffentlicht.

## Sichere Inbetriebnahme

1. Vollständiges Paperless-ngx-Backup anlegen.
2. In einer Testinstanz mit repräsentativen, aber datenschutzkonform bereitgestellten Dokumenten beginnen.
3. Review-Modus zunächst auf „Immer Review“ stellen.
4. Auto-Anwenden erst nach dokumentiertem Abnahmetest aktivieren.
5. Lösch-, Merge- und Verlauf-zurücksetzen-Funktionen nur mit aktuellem Backup verwenden.

Die vollständige Abnahmecheckliste steht in [INSTANCE_CHECKLIST.md](../INSTANCE_CHECKLIST.md).
