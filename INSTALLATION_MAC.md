# Installation auf einem weiteren Mac

Zielverzeichnis: `~/docker/paperless-classification`

## Voraussetzungen

- Docker Desktop mit laufender Docker Engine
- Git
- Netzwerkzugriff auf die gewünschte Paperless-ngx-Instanz
- Netzwerkzugriff auf Ollama und das installierte Klassifizierungsmodell

Die Images unterstützen sowohl Intel-Macs (`amd64`) als auch Apple Silicon
(`arm64`). Docker wählt die Architektur automatisch.

## Erstinstallation

```bash
mkdir -p ~/docker
cd ~/docker
git clone https://github.com/chrschacht/paperless-classification.git paperless-classification
cd paperless-classification
cp .env.example .env
docker compose config
docker compose pull
docker compose up -d
docker compose ps
```

Anschließend ist die Weboberfläche unter
<http://localhost:3001> erreichbar.

Die Backend-API wird nicht auf dem Mac veröffentlicht. Das Frontend leitet
`/api` intern an den Backend-Container weiter.

## Konfiguration übertragen

1. In der Testinstanz unter **KI-Klassifikation → Einstellungen** alle Änderungen
   speichern und anschließend **Gespeicherte Konfiguration exportieren** wählen.
2. In der Paperless-ngx-Zielinstanz zuerst die benötigten Dokumenttypen, Tags,
   Speicherpfade und Custom Fields anlegen. Die Namen müssen mit der
   Ausgangsinstanz übereinstimmen.
3. Im neuen Organizer zunächst Paperless und den LLM-/Ollama-Anbieter unter
   **Einstellungen** verbinden. Tokens, Schlüssel, Hosts und Modelle sind bewusst
   nicht Bestandteil der Exportdatei.
4. Unter **KI-Klassifikation → Einstellungen → Konfiguration übertragen** die
   JSON-Datei importieren. Der Organizer löst Paperless-Objekte anhand ihrer Namen
   auf und meldet fehlende Einträge.
5. Fehlende Einträge ergänzen, Regeln kontrollieren und einige Dokumente manuell
   testen. Die Auto-Klassifizierung bleibt nach jedem Import deaktiviert und wird
   erst danach bewusst eingeschaltet.

## Ollama

Für eine Ollama-Instanz auf einem anderen Rechner wird deren vollständige URL in
der Organizer-Oberfläche eingetragen, beispielsweise:

```text
http://ollama.example.lan:11434
```

Läuft Ollama direkt auf demselben Mac, verwendet der Container:

```text
http://host.docker.internal:11434
```

Ollama muss Verbindungen über die Docker-Desktop-Hostbrücke akzeptieren. Das
benötigte Modell muss vor dem Funktionstest bereits installiert sein.

## Zugriff im lokalen Netzwerk

Standardmäßig bindet Docker die Oberfläche nur an `127.0.0.1`. Das ist für
vertrauliche Dokumentdaten die sichere Voreinstellung.

Eine Freigabe im LAN erfolgt bewusst in `.env`:

```dotenv
ORGANIZER_BIND_ADDRESS=0.0.0.0
ORGANIZER_PORT=3001
```

Danach:

```bash
docker compose up -d
```

Vor einer LAN- oder Internetfreigabe ist eine serverseitige Authentifizierung
mit HTTPS erforderlich. Die reine WebUI-Passwortabfrage ersetzt diesen Schutz
nicht.

## Status und Logs

```bash
cd ~/docker/paperless-classification
docker compose ps
docker compose logs --tail=200 backend
docker compose logs --tail=200 frontend
```

## Update

```bash
cd ~/docker/paperless-classification
git pull --ff-only
docker compose pull
docker compose up -d
```

Vor jedem Update sollte die Organizer-Datenbank gesichert werden.

## Backup

Die vollständige Laufzeitkonfiguration einschließlich Prompts und Zuordnungen
liegt in `data/organizer.db`. API-Tokens sind darin ebenfalls enthalten; Backups
müssen deshalb vertraulich behandelt werden.

Konsistentes Datenbank-Backup:

```bash
cd ~/docker/paperless-classification
mkdir -p backups
docker compose stop backend
cp data/organizer.db "backups/organizer-$(date +%Y%m%d-%H%M%S).db"
docker compose start backend
```

Für RAG und OCR sollte bei Nutzung zusätzlich das gesamte Verzeichnis
`data/` gesichert werden.

## Wiederherstellung

```bash
cd ~/docker/paperless-classification
docker compose down
cp backups/organizer-YYYYMMDD-HHMMSS.db data/organizer.db
docker compose up -d
```

## Deinstallation

```bash
cd ~/docker/paperless-classification
docker compose down
```

Das Verzeichnis `data/` bleibt dabei erhalten. Erst dessen bewusstes Löschen
entfernt Konfiguration, Historie und Zugangsdaten endgültig.
