# Versionierung von Paperless Classification

Paperless Classification verwendet eine eigenständige Versionslinie.

Es gilt Semantic Versioning:

- Patch (`1.0.1`): Fehlerkorrektur ohne Konfigurationsänderung.
- Minor (`1.1.0`): neue, abwärtskompatible Funktion.
- Major (`2.0.0`): inkompatible Änderung oder notwendige Migration.

Vor einem produktiven Git-Tag müssen die Version in `frontend/package.json` und die API-Version in `backend/app/main.py` übereinstimmen.
