# CLAUDE.md – Arbeitsanweisungen für FotoDünner

Du baust **FotoDünner**, ein lokales Windows-Programm zum Ausdünnen von Urlaubs-Fotoalben. Die vollständige Spezifikation liegt in `docs/`. Lies vor jeder Aufgabe die relevanten Dokumente; bei Widersprüchen gilt: `docs/DECISIONS.md` > `01_Anforderungen.md` > `03_Analyse_und_Auswahllogik.md` > übrige.

## Unverhandelbare Regeln

1. **Niemals Dateien des Nutzers löschen oder überschreiben.** Einzige erlaubte Dateioperation auf Fotos: Verschieben in `<Album>\_Aussortiert\` (und zurück beim Rückgängigmachen). Jede Verschiebung wird vorher in ein Journal geschrieben (siehe 02, Abschnitt „Übernehmen und Rückgängig“).
2. **Originale nur lesen.** Keine EXIF-Änderungen, kein Neuspeichern von Originalen. Alle abgeleiteten Daten (Cache, Vorschauen) liegen in `%LOCALAPPDATA%\FotoDuenner\` – nicht im Album. Einzige Ausnahme: die Protokoll-Kopie `_Aussortiert\_FotoDuenner_Protokoll.json` beim Übernehmen (DECISIONS T4).
3. **Vollständig offline.** Zur Laufzeit keine Netzwerkzugriffe außer `127.0.0.1`. Modelle werden mitgeliefert. Der Webserver bindet nur an `127.0.0.1`.
4. **CPU-only.** Keine Abhängigkeit von PyTorch, CUDA oder einer GPU zur Laufzeit. Inferenz über ONNX Runtime (CPU). PyTorch darf höchstens in Entwicklungs-Skripten (z. B. Modell-Export) vorkommen, nicht im Paket.
5. **Windows-Pfade:** Pfade mit Umlauten, Leerzeichen und > 260 Zeichen müssen funktionieren. `cv2.imread`/`cv2.imwrite` niemals mit Pfaden verwenden (scheitert bei Nicht-ASCII) – stattdessen Pillow oder `np.fromfile` + `cv2.imdecode`. Immer `pathlib.Path`.
6. **UI-Sprache Deutsch**, Code, Bezeichner, Kommentare und Commit-Messages Englisch.

## Konventionen

- Python 3.12, Abhängigkeits- und Umgebungsverwaltung mit `uv` (`pyproject.toml`, `uv.lock`).
- Paketstruktur siehe `02_Architektur_und_Technik.md`. Typ-Annotationen überall, `ruff` (lint + format), `pytest`.
- Konfigurierbare Schwellen und Gewichte stehen zentral in `fotoduenner/config.py` (Dataclass mit Defaults aus 03) – keine Magic Numbers im Code verstreut.
- Logging über `logging` in `%LOCALAPPDATA%\FotoDuenner\logs\`, rotierend. Fehler bei einzelnen Bildern dürfen den Lauf nie abbrechen: Bild als `error` markieren, weiter.
- Jede Analyse-Stufe ist cachebar und idempotent (Schlüssel: Pfad + Dateigröße + mtime + Algorithmus-Version; Wiederfinden zusätzlich über den Datei-Fingerabdruck Name + Größe + mtime + EXIF-Zeit, DECISIONS T13).

## Vorgehen

- Arbeite meilensteinweise nach `docs/05_Umsetzungsplan_und_Tests.md`. Einen Meilenstein erst als fertig melden, wenn seine Abnahmekriterien erfüllt und Tests grün sind.
- Vor Einsatz eines Modells: Download-Quelle, Lizenz, Ein-/Ausgabe-Tensoren und Vorverarbeitung **prüfen** und in `models/MODELS.md` dokumentieren (Name, Quelle-URL, Lizenz, SHA256, Input-Shape, Normalisierung). Die in 03 genannten Modelle sind die Planung – wenn sich etwas als falsch herausstellt (Ausgabename, Format, Lizenz), gleichwertigen Ersatz wählen und dokumentieren.
- Performance regelmäßig mit `scripts/benchmark.py` messen (siehe 05). Ziel: ≤ 0,3 s pro Bild Gesamt-Analyse auf einem 4-Kern-Office-Laptop.
- Bei Unklarheiten in der Spezifikation: sinnvolle Annahme treffen, im Code als `# ASSUMPTION:` markieren und in `docs/DECISIONS.md` eintragen.
