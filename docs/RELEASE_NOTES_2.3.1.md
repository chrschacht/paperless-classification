# Paperless Classification 2.3.1

Version 2.3.1 korrigiert die Laufzeitabhängigkeiten des Backend-Containers von Version 2.3.0.

## Behoben

- Die SQLAlchemy-Async-Abhängigkeit installiert `greenlet` nun ausdrücklich mit.
- Das Backend startet dadurch auch aus einem vollständig neu gebauten Containerimage zuverlässig.

## Container

```text
ghcr.io/chrschacht/paperless-classification-backend:2.3.1
ghcr.io/chrschacht/paperless-classification-frontend:2.3.1
```

Version 2.3.0 sollte nicht produktiv eingesetzt werden; fachlich entspricht 2.3.1 derselben Funktionsversion mit korrigiertem Backend-Paket.
