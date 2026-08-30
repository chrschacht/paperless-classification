# OCR-Modell-Vergleich & KI-Qualitätsbewertung

Dieser Ordner dient zur Dokumentation des integrierten **OCR-Benchmarks** im Paperless Classification.

## Funktionen im Tool

- **OCR Modell-Vergleich**: Gleiches Dokument mit mehreren Ollama-Vision-Modellen (z. B. qwen3-vl:4b-instruct, qwen3-vl:8b-instruct, glm-ocr, minicpm-v) verarbeiten und Ergebnisse nebeneinander anzeigen.
- **KI-Qualitätsbewertung**: Cloud-LLM (z. B. GPT-4o) bewertet die OCR-Ergebnisse nach Kriterien wie Namen, IBAN, Beträge, Vollständigkeit und gibt eine strukturierte Empfehlung.

## Screenshots

Benchmark-Screenshots sind in der öffentlichen Distribution bewusst nicht enthalten, weil OCR-Ergebnisse regelmäßig Namen, Adressen, Kontodaten oder andere Dokumentinhalte zeigen. Für eigene Dokumentation ausschließlich synthetische Testdokumente verwenden; bloßes Schwärzen einzelner Felder genügt für eine sichere Veröffentlichung häufig nicht.

## Empfohlene Modelle

- **qwen3-vl:4b-instruct** – guter Kompromiss aus Qualität und Geschwindigkeit.
- **huihui_ai/qwen3-vl-abliterated:8b** – 8B ohne Safety-Filter, erkennt IBANs und sensible Felder zuverlässiger.
- **glm-ocr** – schlank, schnell, gut für Standard-OCR.

Die Standard-Version **qwen3-vl:8b-instruct** filtert sensible Felder (z. B. IBAN) stark; für vollständige Transkription die abliterated-Variante oder 4b-instruct nutzen.
