# Lokale Entwicklung mit Docker Desktop

Die Entwicklungsumgebung läuft vollständig getrennt von den produktiven
Installationen. Sie verwendet eigene Container, ein eigenes Docker-Netzwerk und
ein eigenes persistentes Daten-Volume.

## Start

```bash
docker compose -f docker-compose.dev.yml up --build
```

Danach sind die Dienste erreichbar unter:

- Weboberfläche: <http://localhost:18088>
- Backend/API: <http://localhost:18081>
- API-Dokumentation: <http://localhost:18081/docs>

Backend- und Frontend-Quellcode sind eingebunden. Änderungen werden durch
Uvicorn beziehungsweise Vite automatisch neu geladen.

## Stoppen

```bash
docker compose -f docker-compose.dev.yml down
```

Das Entwicklungs-Volume bleibt dabei erhalten. Nur wenn die lokale Testdatenbank
bewusst verworfen werden soll:

```bash
docker compose -f docker-compose.dev.yml down --volumes
```

Dieser letzte Befehl löscht ausschließlich die Volumes des Compose-Projekts
`paperless-classification-dev`.

## Externe Dienste

Paperless-ngx und Ollama werden später in der Weboberfläche konfiguriert. Für
lokale Dienste auf dem Mac ist aus Docker heraus `host.docker.internal` statt
`localhost` zu verwenden. Zugangsdaten und API-Tokens gehören nur in die lokale
Organizer-Datenbank und niemals in Git.

## Produktionsprinzip

Die Produktivumgebung verwendet nicht diese Dev-Datei und keine Quellcode-
Bind-Mounts. Für Releases werden unveränderliche Backend- und Frontend-Images
aus einem markierten Git-Stand gebaut. Instanzbezogene Daten, Prompts, URLs und
Tokens bleiben außerhalb der Images.
