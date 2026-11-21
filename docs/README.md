# FotoDünner – Projektplan

Lokales Windows-Programm, das Urlaubs-Fotoalben (500–2.000 JPEG-Bilder, 10–20 MB pro Bild) intelligent ausdünnt: exakte Kopien, Serienbilder und technisch schlechte Bilder werden erkannt, ähnliche Bilder bzw. gleiche Motive gruppiert, pro Gruppe die besten behalten, Bilder mit Personen bevorzugt. Standard ist der behutsame Modus „Nur Doppelte & Schlechte“; bei Bedarf dünnt der Modus „Zielquote“ stärker aus. Am Ende prüft der Nutzer alle Entscheidungen in einer Bündel-Übersicht, kann jede umdrehen und übernimmt dann. **Es wird nie etwas gelöscht** – aussortierte Bilder werden in einen Unterordner verschoben und können per Knopfdruck zurückgeholt werden.

## Dokumente

| Datei | Inhalt |
|---|---|
| `../CLAUDE.md` | Arbeitsanweisungen für Claude Code (im Projektwurzelverzeichnis) |
| `DECISIONS.md` | Entscheidungen und Abweichungen – **geht allen anderen Dokumenten vor** |
| `01_Anforderungen.md` | Ziele, Nutzer, Funktionsumfang, Nicht-Ziele, Rahmenbedingungen |
| `02_Architektur_und_Technik.md` | Technologie-Stack, Modulstruktur, Datenmodell, Cache, Performance, Windows-Fallstricke, Paketierung |
| `03_Analyse_und_Auswahllogik.md` | Bild-Einlesen, Merkmale (Embeddings, Gesichter, Schärfe, Belichtung, Ästhetik, Zero-Shot), Gruppierung, Bewertung, Modi |
| `04_Benutzeroberflaeche.md` | Gestaltung, Bildschirme, Bündel-Übersicht, Interaktionen, Tastatur, Übernehmen/Rückgängig |
| `05_Umsetzungsplan_und_Tests.md` | Meilensteine mit Abnahmekriterien, Teststrategie, Benchmarks, Tuning |

## Vorgehen mit Claude Code

1. Claude Code im Projektordner starten.
2. Meilenstein für Meilenstein nach `05_Umsetzungsplan_und_Tests.md` vorgehen, beginnend mit M0.
3. Nach jedem Meilenstein mit einer **Kopie** eines echten Urlaubsordners testen.

## Eckdaten auf einen Blick

- **Nutzer:** Entwickler selbst + Freunde; Verteilung als ZIP über öffentliche GitHub-Releases.
- **Zielrechner:** Office-Laptop, nur Windows 10/11, keine Grafikkarte, typ. 4–8 CPU-Kerne, 8–16 GB RAM.
- **Zieldauer:** ≤ 10 Minuten Analyse für 2.000 Bilder à 15 MB beim ersten Lauf; Wiederholung in Sekunden.
- **Bedienung:** Startdatei doppelklicken → Ordner wählen → Analyse → Bündel prüfen → „Übernehmen“.
- **Stack:** Python 3.12, ONNX Runtime (CPU), OpenCV, Pillow, FastAPI, pywebview-Fenster (WebView2), SQLite-Cache, PyInstaller.
- **Offline:** Nach der Installation keine Internetverbindung nötig, keine Bilder verlassen den Rechner.
- **Lizenz:** MIT.
