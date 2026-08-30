# Benutzerhandbuch

Dieses Handbuch beschreibt die Inbetriebnahme und die wichtigsten Arbeitsabläufe von Paperless Classification 2.0. Die Anwendung ergänzt eine bestehende Paperless-ngx-Installation; sie ersetzt weder Paperless-ngx noch dessen Backup-, Benutzer- oder Berechtigungskonzept.

## 1. Orientierung

![Dashboard ohne personenbezogene Dokumentdaten](screenshots/dashboard-v2.jpg)

Das Dashboard fasst den Zustand des verbundenen Archivs zusammen:

- **Archiv-Übersicht**: Anteil OCR-fertiger, klassifizierter, zu prüfender oder ignorierter Dokumente
- **KI-Klassifikation**: angewendete Klassifikationen und offene Analysen
- **OCR Engine**: abgeschlossene, zu prüfende und ignorierte Dokumente
- **Aufräumen**: Korrespondenten, Tags und Dokumenttypen sowie bisherige Bereinigungen
- **Schnellzugriff**: direkte Navigation in die wichtigsten Arbeitsbereiche

Dashboard-Zahlen werden teilweise live aus Paperless-ngx und teilweise aus der lokalen Historie ermittelt. Das Zurücksetzen einer Historie entfernt nicht die in Paperless-ngx vorhandenen Dokumente oder Bearbeitungs-Tags.

## 2. Erste Konfiguration

### Paperless-ngx verbinden

1. **Einstellungen** öffnen.
2. Basis-URL der Paperless-ngx-Instanz eintragen.
3. Einen Paperless-ngx-API-Token eintragen.
4. Verbindung testen und speichern.

Der Token benötigt die Rechte für alle Aktionen, die später ausgeführt werden sollen. Für reine Tests empfiehlt sich ein eigener Paperless-Benutzer mit möglichst begrenzten Rechten.

### LLM auswählen

Paperless Classification unterstützt zwei Provider:

- **Ollama**: lokal oder im eigenen Netzwerk; kein API-Schlüssel erforderlich
- **OpenAI**: Cloud-Dienst; API-Schlüssel erforderlich

Die Aufgaben Klassifikation, Metadaten-Bereinigung und Dokumenten-Chat können unterschiedliche Modelle verwenden. Nach einem Modellwechsel sollte eine kleine manuelle Testserie durchgeführt werden.

### Hintergrundbild

Unter **Einstellungen → Darstellung** kann ein eigenes Hintergrundbild hochgeladen oder das mitgelieferte Bild wiederhergestellt werden. Das Bild wird ohne zusätzliche Seitenüberlagerung angezeigt; die Kacheln verwenden den zentral definierten Glass-Hintergrund.

## 3. KI-Klassifikation

![Klassifikationseinstellungen](screenshots/classification-settings.jpg)

Die Klassifikation kann Titel, Tags, Korrespondent, Dokumenttyp, Speicherpfad, Erstelldatum und Custom Fields vorschlagen. Jedes Feld lässt sich separat aktivieren und über einen eigenen Prompt einschränken.

### Empfohlener Start

1. **Review-Modus: Immer Review vor Anwendung** wählen.
2. Zunächst nur Titel, Tags, Korrespondent und Dokumenttyp aktivieren.
3. Ein repräsentatives Testdokument über **Klassifizieren** analysieren.
4. Vorschau und tatsächliche Anwendung vergleichen.
5. Erst danach Custom Fields, Speicherpfade und automatische Anwendung ergänzen.

### Feld- und Filterkonfiguration

Jede Zeile unter **Aktive Felder & Filter** lässt sich aufklappen. Dort stehen abhängig vom Feld zur Verfügung:

- eigener Prompt zusätzlich zum Systemprompt
- ausschließlich vorhandene oder auch neue Zielwerte
- Ausschlusslisten
- Verhalten bei bereits vorhandenen Paperless-Werten
- Pflichtfeld- und Dokumenttyp-Zuordnungen

Die Schaltfläche **Paperless sync** lädt Tags, Korrespondenten, Dokumenttypen, Speicherpfade und Custom Fields neu aus Paperless-ngx.

### Status-Tags

![Status-Tags und zusätzliche Regeln](screenshots/status-tag-rules.jpg)

Die drei grundlegenden Tags steuern den Bearbeitungsstatus:

- **Klassifiziert**: wird nach erfolgreicher Anwendung gesetzt
- **Prüfen**: kennzeichnet eine notwendige manuelle Prüfung
- **Tag-Ideen**: kennzeichnet noch nicht bestätigte Tag-Vorschläge

Unter **Zusätzliche Status-Tag-Regeln** können vorhandene Paperless-Tags deterministisch anhand des erkannten Dokumenttyps gesetzt werden. Ein optionaler Sperr-Tag verhindert, dass eine Regel auf bereits abgeschlossene Dokumente angewendet wird.

### Pflichtfelder und Force-OCR

Custom Fields können je Dokumenttyp als Pflichtfeld markiert werden. Fehlt nach der Klassifikation ein Pflichtwert:

1. Die Anwendung entfernt `ocrfinish`.
2. Sie setzt `forceocr` für eine erneute OCR-Verarbeitung.
3. Nach erfolgreicher OCR wird `ocrfinish` wieder gesetzt und die Klassifikation erneut gestartet.
4. Fehlt das Pflichtfeld weiterhin, wird keine Endlosschleife erzeugt; das Dokument erhält `KI-prüfen`.

Bereits vorhandene gültige Paperless-Werte werden berücksichtigt und nicht unnötig überschrieben.

### Manuelle Vorschau und Anwendung

Die manuelle Analyse zeigt das Ergebnis so, wie es angewendet würde. Dazu gehören auch das Entfernen von Auslöser-Tags und das Hinzufügen konfigurierter Status-Tags. OCR-Bearbeitungstags werden nur durch den OCR-Workflow verändert.

## 4. OCR

![OCR-Verarbeitung](screenshots/ocr-processing.jpg)

Die OCR arbeitet mit Ollama-Vision-Modellen. Sie kann einzelne Dokumente, einen tagbasierten Batch oder einen automatischen Watchdog verarbeiten.

### Smart-Skip

Smart-Skip verhindert unnötige Vision-OCR. Vorhandener Paperless-Text wird übernommen, wenn er ausreichend lang, plausibel und frei von typischen Modellartefakten ist. Eine erneute OCR erfolgt insbesondere bei:

- fehlendem oder sehr kurzem Text
- auffällig vielen unlesbaren Zeichen
- erkennbaren Modell-Denktexten oder Wiederholungsschleifen
- explizitem `forceocr`

### Tagbasierter Ablauf

Der Standardablauf verwendet technische Tags:

| Tag | Bedeutung |
|---|---|
| `runocr` | reguläre OCR anfordern |
| `forceocr` | vorhandenen Text ignorieren und OCR erzwingen |
| `ocrfinish` | OCR erfolgreich abgeschlossen |
| `ocrpruefen` | Ergebnis benötigt manuelle Prüfung |
| `ocrfehler` | Verarbeitung nach Wiederholungen fehlgeschlagen |

### Dateigröße und Watchdog

Die maximale OCR-Dateigröße ist konfigurierbar. Der Watchdog verarbeitet neue Trigger in einem festgelegten Intervall. Ein kurzes Intervall ist nur sinnvoll, wenn Ollama und Paperless-ngx genügend Reserven haben. Bei nicht erreichbarem Ollama wird ein Zyklus übersprungen, statt alle Dokumente sofort als dauerhaft fehlerhaft zu markieren.

### OCR-Verlauf zurücksetzen

Der Button unter **OCR → Einstellungen** löscht nur die lokale OCR-Historie und zugehörige Warteschlangeneinträge. Dokumente und bereits übernommener Text in Paperless-ngx bleiben erhalten.

## 5. Aufräumen

Der Menüpunkt **Aufräumen** bündelt Korrespondenten, Dokumenttypen, Tags, unerwünschte Dokumente, Duplikate und Prompts.

### Korrespondenten, Dokumenttypen und Tags

Die KI analysiert Metadatennamen und bildet Gruppen möglicher Dubletten. Eine Zusammenführung erfolgt erst nach Bestätigung. Vorher sollte geprüft werden:

- Sind die Einträge fachlich wirklich identisch?
- Ist das gewählte Ziel korrekt geschrieben?
- Sind die zugehörigen Dokumente plausibel?
- Gibt es Automatisierungen in Paperless-ngx, die den alten Namen verwenden?

### Tag Cleanup Wizard

Der Assistent führt durch leere, unsinnige, korrespondentenartige, dokumenttypartige und ähnliche Tags. Jede Lösch- oder Merge-Aktion bleibt bestätigungspflichtig.

### Duplikate

![Konfiguration der Duplikatsuche](screenshots/duplicate-scan.jpg)

Es gibt drei Verfahren:

1. **Exakte Duplikate** über Dateiprüfsummen
2. **Ähnliche Dokumente** über RAG-Embeddings
3. **Doppelte Rechnungen** über Rechnungsnummern und weitere Merkmale

Die Suche löscht keine Dokumente automatisch. Ähnlichkeitssuche setzt einen aufgebauten RAG-Index voraus; exakte Duplikate funktionieren ohne Index.

## 6. Dokumenten-Chat

Der Dokumenten-Chat baut einen lokalen Suchindex aus Paperless-OCR-Texten auf. Die Suche kombiniert lexikalische und semantische Treffer und liefert Quellen zu den Antworten.

Vor der Verwendung:

1. RAG unter **Einstellungen** aktivieren.
2. Embedding-Modell und Chat-Modell auswählen.
3. Vollindex aufbauen.
4. Mit Fragen testen, deren korrekte Quelle bekannt ist.
5. Auto-Index erst danach aktivieren.

Bei OpenAI können Suchanfrage und gefundene Textabschnitte den eigenen Server verlassen. Für vollständig lokale Verarbeitung müssen Embeddings und Chat über Ollama laufen.

## 7. Konfiguration übertragen

Unter **KI-Klassifikation → Einstellungen** kann die gespeicherte Klassifikationskonfiguration exportiert und in eine andere Installation importiert werden. Enthalten sind Prompts, Regeln, Filter, Speicherpfad-Profile und Custom-Field-Zuordnungen. Nicht exportiert werden:

- Paperless-ngx-URL und Token
- OpenAI-Schlüssel
- Ollama-Endpunkte und Zugangsdaten
- interne Paperless-Objekt-IDs

Beim Import werden Paperless-Objekte anhand ihrer Namen neu zugeordnet. Die Auto-Klassifikation bleibt aus Sicherheitsgründen deaktiviert, bis die Zuordnung geprüft wurde.

## 8. Backup und Wiederherstellung

Mindestens zu sichern sind:

- Paperless-ngx-Datenbank, Medien und Konfiguration
- das gesamte `data/`-Verzeichnis von Paperless Classification

Vor einem Restore werden die Container gestoppt. Eine ältere Anwendungsversion sollte nicht mit einer bereits auf 2.0 migrierten Datenbank gestartet werden; stattdessen ist das passende Backup wiederherzustellen.

Weitere Informationen:

- [Installation auf macOS](../INSTALLATION_MAC.md)
- [Upgrade auf 2.0](UPGRADE_V2.md)
- [Sicherheit und Datenschutz](SECURITY_AND_PRIVACY.md)
- [Abnahmecheckliste](../INSTANCE_CHECKLIST.md)
