# Prüf- und Abnahmecheckliste je Installation

Diese Checkliste ist für eine neue oder wesentlich geänderte Installation von Paperless Classification gedacht. Vor automatischen Änderungen an einem produktiven Paperless-ngx-Archiv sollte jeder Abschnitt geprüft werden.

## 1. Sicherheit und Ausgangszustand

- [ ] Vollständiges Paperless-ngx-Backup erstellt und Wiederherstellung getestet
- [ ] `data/` beziehungsweise die Organizer-SQLite-Datenbank gesichert
- [ ] Eingesetzte Version oder Docker-Image-Digests dokumentiert
- [ ] Reverse Proxy, TLS und vorgeschaltete Authentifizierung geprüft
- [ ] Auto-Klassifikation und OCR-Watchdog während der Umstellung gestoppt
- [ ] Bestehende Prüf-Warteschlangen dokumentiert

## 2. Verbindungen und Modelle

- [ ] Paperless-ngx-Verbindung erfolgreich getestet
- [ ] Ollama oder OpenAI bewusst ausgewählt und getestet
- [ ] Klassifikationsmodell getestet
- [ ] OCR-Modell getestet, falls OCR verwendet wird
- [ ] RAG-Embedding- und Chat-Modell getestet, falls Dokumenten-Chat verwendet wird
- [ ] Nicht benötigte API-Schlüssel entfernt

## 3. Taxonomie

- [ ] Korrespondenten, Dokumenttypen und Tags fachlich eindeutig
- [ ] Themen-Tags und technische Bearbeitungs-Tags getrennt
- [ ] Ausschluss- und Sperr-Tags geprüft
- [ ] Speicherpfade in Paperless-ngx vollständig angelegt
- [ ] Speicherpfad-Profile synchronisiert und verständlich beschrieben

## 4. KI-Klassifikation

- [ ] Zu bearbeitende Felder bewusst aktiviert
- [ ] Dokumenttyp- und Feld-Prompts mit einem repräsentativen Testkorpus geprüft
- [ ] Bestehende Tags behalten/ersetzen bewusst festgelegt
- [ ] Korrespondenten-Neuanlage und Smart Match bewusst konfiguriert
- [ ] Review-Modus während der Testphase auf „Immer Review“ gestellt
- [ ] Status-Tags und zusätzliche Status-Tag-Regeln geprüft
- [ ] Pflichtfelder je Dokumenttyp festgelegt
- [ ] Force-OCR-Wiederholung und Eskalation zu `KI-prüfen` getestet

## 5. Custom Fields

- [ ] Datentypen und erlaubte Dokumenttypen stimmen mit Paperless-ngx überein
- [ ] Rechnungsnummer nur aus einer ausdrücklich belegten Rechnungs- oder Bonkennung
- [ ] Betrag numerisch und ohne Währungssymbol
- [ ] Fälligkeits- und Zahlungsdatum fachlich getrennt
- [ ] Nicht relevante Felder werden weder vorgeschlagen noch leer angelegt
- [ ] Eigene IBANs, Steuer- oder Kontonummern als Ignore-Werte hinterlegt, falls erforderlich

## 6. OCR

- [ ] Smart-Skip aktiviert und mit gutem vorhandenem Paperless-OCR getestet
- [ ] Maximale Dateigröße passend zur Infrastruktur gesetzt
- [ ] Watchdog-Intervall passend zur Serverleistung gewählt
- [ ] `runocr`, `forceocr`, `ocrfinish`, Prüf- und Fehler-Tags konsistent
- [ ] Fehler-Retry und optionaler Failover-Server getestet
- [ ] Mindestens ein mehrseitiges und ein qualitativ schwieriges Dokument geprüft

## 7. Dokumenten-Chat und Duplikate

- [ ] RAG-Index vollständig aufgebaut
- [ ] Beispielabfragen liefern die erwarteten Quellen
- [ ] Exakte Duplikatsuche getestet
- [ ] Ähnlichkeitssuche nur nach erfolgreichem Indexaufbau aktiviert
- [ ] Lösch- und Merge-Aktionen werden zunächst ausschließlich manuell bestätigt

## 8. Abnahmetest

Das Testkorpus sollte die tatsächlich vorkommenden Dokumentarten enthalten, aber ausschließlich in einer geschützten Testinstanz verarbeitet werden. Pro Dokument sollten erwartete und tatsächliche Werte für Titel, Korrespondent, Dokumenttyp, Tags, Datum, Speicherpfad und Custom Fields verglichen werden.

Freigabekriterien für Auto-Anwenden:

- [ ] Keine kritische Fehlzuordnung im Testkorpus
- [ ] Pflichtfelder erreichen die intern festgelegte Mindestqualität
- [ ] Keine neuen Korrespondenten-Dubletten
- [ ] Keine falschen Workflow-Tags oder Speicherpfade
- [ ] Review- und Wiederherstellungsprozess ist dokumentiert
