# 05 – Umsetzungsplan und Tests

Jeder Meilenstein endet mit lauffähigem Stand, grünen Tests und erfüllten Abnahmekriterien. Reihenfolge so gewählt, dass früh etwas Sichtbares entsteht und die riskanten Teile (Geschwindigkeit, Paketierung, Augen-Modell) früh geprüft werden.

## Meilensteine

### M0 – Projektgerüst + Spikes
- Entwicklungsumgebung: `UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\uv-envs\fotoduenner` als Benutzer-Umgebungsvariable (D11); Python 3.12 über `uv python install 3.12`.
- `uv init`, `pyproject.toml` mit Abhängigkeiten (02), `ruff`, `pytest`, Verzeichnisstruktur (02 §3), `config.py` mit allen Parametern aus 03 §10. `LICENSE` (MIT, D14).
- `scripts/fetch_models.py`: lädt YuNet, CLIP-ONNX (Bild- und für die Entwicklung Text-Encoder), Ästhetik-Gewichte, Augenmodell; prüft SHA256; schreibt `models/MODELS.md`. Ästhetik-`.pth` → `.npz` konvertieren (einmalig, im Skript; PyTorch nur als optionale Dev-Abhängigkeit oder Gewichte via `torch`-freiem Pickle-Laden prüfen). Modelldateien sind in `.gitignore`.
- `fotoduenner/__main__.py`: Server-Thread + pywebview-Fenster mit leerer, dunkler Startseite.
- **Spike Augen offen (D6):** MediaPipe Face Landmarker vs. reines ONNX-Landmarkenmodell. Prüfen: Lässt sich der Paketkonflikt `opencv-contrib-python` ↔ `opencv-python-headless` lösen? Funktioniert ein Mini-PyInstaller-Build? Genauigkeit an einer Handvoll Testbilder, Laufzeit je Gesicht. Entscheidung in DECISIONS.md.
- **Spike Paketierung:** Mini-PyInstaller-Build (onedir, windowed) mit pywebview + onnxruntime startet auf dem Entwicklungsrechner.
- **Abnahme:** `uv run pytest` läuft (Platzhalter-Test), `uv run python -m fotoduenner` öffnet ein eigenes Fenster mit leerer Startseite; Spike-Ergebnisse dokumentiert.

### M1 – Scannen, Einlesen, Vorschauen, Cache
- `scanner/*`: JPEG-Dateisuche, Ausschlüsse (`_Aussortiert`, versteckte Dateien, `Thumbs.db`, andere Formate), `.xmp`-Sidecars, EXIF-Zeit/Orientierung/Kamera/Rating/ExposureBias, Datei-Hash.
- `pipeline/decode.py` (draft-Skalierung nach T9), `thumbs.py` (JPEG), `runner.py` (ProcessPool, Priorität, Abbruch, Fortsetzen), `store/db.py` inkl. Fingerabdruck-Cache (T13).
- **Abnahme:**
  - 2.000 JPEGs à 24 MP: Einlesen + Vorschauen ≤ 3 min auf Zielhardware-Äquivalent (Benchmark-Skript, siehe unten).
  - Abbruch nach 50 %, Neustart → nur Rest wird verarbeitet.
  - Umlaut-Pfad, Pfad > 260 Zeichen (ohne `LongPathsEnabled`), Hochformat-Bild (EXIF-Orientierung 6/8), defekte Datei, `.xmp`-Sidecar mit Rating, exakte Kopie (gleicher Hash) – alle korrekt (Tests).
  - Album-Ordner umbenannt → Merkmale werden über den Fingerabdruck wiederverwendet.

### M2 – Minimal-Oberfläche
- Start-, Vorprüfungs-, Fortschrittsbildschirm, „Alle“-Raster mit Vorschauen (noch ohne Entscheidungen), Großansicht. Dunkles Design-Grundgerüst (D9).
- Token-Absicherung (Cookie `SameSite=Strict`, eigener POST-Header, T12), Port-Wahl, Einzelinstanz (zweiter Start holt Fenster nach vorn), Fenster schließen beendet sauber, WebView2-Prüfung (A3).
- **Mockup der Bündel-Ansicht** zur Abstimmung mit dem Nutzer (A5).
- **Abnahme:** Ordner wählen → Analyse mit Live-Fortschritt → flüssig scrollbares Raster mit 2.000 Kacheln.

### M3 – Merkmale
- `quality.py`, `hashing.py`, `faces.py` (YuNet + Augen offen nach Spike-Entscheidung, zweiter Durchgang T6), `embed.py` (CLIP ONNX), `aesthetic.py`, `zeroshot.py` (+ `scripts/make_text_embeddings.py`).
- Debug-Ansicht in der Großansicht: alle Rohwerte, Zero-Shot-Scores + Gesichtsboxen einblendbar (Taste D).
- ~~**CLIP vs. DINOv2-small für die Gruppierung** vergleichen (T8)~~ → verschoben in M7, entschieden an den Korrekturen des Nutzers (D19).
- **Pipeline-Variante (T11):** einfache Variante (alles pro Worker, ONNX 1 Thread) messen; getrennte CLIP-Stufe nur, wenn messbar schneller.
- **Abnahme:**
  - Gesamtanalyse 2.000 Bilder ≤ 10 min (Ziel 6) auf 4-Kern-Laptop ohne GPU (inkl. zweitem Durchgang).
  - INT8- vs. FP32-CLIP: mittlere Kosinus-Ähnlichkeit ≥ 0,98 (sonst FP32 nehmen).
  - Gesichtserkennung auf Testset (siehe unten) ≥ 95 % Trefferquote für Gesichter > 3 % Bildbreite; Augen-offen-Klassifikation ≥ 85 % korrekt bei Gesichtern ≥ 4 %.
  - Zero-Shot-Labels: Trefferquote je Label an Testbildern dokumentiert; unzuverlässige Labels abgeschaltet (D13).

### M4 – Gruppierung und Auswahl (Kernlogik)
- `grouping/sequences.py`, `grouping/cluster.py`, `selection/*` exakt nach 03.
- Reine Funktionen ohne I/O, vollständig unit-getestet mit synthetischen Merkmalen.
- **Abnahme (Unit-Tests):**
  - 10 fast identische Bilder in 20 s → 1 Serie; im Modus `clean` bleibt das schärfste (bzw. alle hinreichend verschiedenen), im Modus `quota` 50 % mindestens das schärfste.
  - Modus `clean` dünnt Motivgruppen nie aus; exakte Kopien werden in beiden Modi aussortiert.
  - Belichtungsreihe (3 Bilder, ±1 EV, 1 s Abstand) und Panorama-Folge (5 Bilder, 40 % Versatz) bleiben vollständig erhalten.
  - Unschärfe mit `BLUR_ABS = None` führt nur zu „Bitte prüfen“, nie zum Aussortieren (T5).
  - Einzelbild wird bei jeder Quote behalten (außer Option „Einzelbilder mit geringem Wert“).
  - Personenbild ist nie das einzige verworfene Bild einer Gruppe ohne besseres Personenbild.
  - Rating ≥ 1 wird nie aussortiert; manuelle Entscheidungen überleben jeden Regler- und Moduswechsel.
  - **T1:** Ein manueller Klick ändert die Entscheidung keines anderen Bildes.
  - Quote 50 % auf synthetischem Album (100 Gruppen à 4 + 200 Einzelbilder) → 50 % ± 2 %.
  - Mega-Gruppen-Aufteilung greift bei > 30 Bildern.
  - Neuberechnung 2.000 Bilder < 300 ms.

### M5 – Prüf-Übersicht vollständig
- Reiter, **Bündel** (zugeklappt/aufgeklappt, nach abgestimmtem Mockup), Kachel-Zustände, Begründungen, Modus-Umschalter + Regler mit Diff-Updates, Tastatur, Vergleichsansicht, 100 %-Zoom, Filter, dezente Animationen (nur `transform`/`opacity`).
- Nicht in v1 (T14): synchroner Zoom in der Vergleichsansicht, Taste F, Tastenkombis `G` + Taste.
- **Abnahme:** Kompletter Durchlauf an einem echten Album (Kopie) ohne Ruckeln; jede Entscheidung per Klick umkehrbar; Begründung für jedes aussortierte Bild sichtbar.

### M6 – Übernehmen und Rückgängig
- `apply/*` nach 02 §6 (`os.rename`, T3; Protokoll-Kopie in `_Aussortiert`, T4; Status „übernommen“, T2), Dialoge nach 04 §3.
- **Abnahme (Tests mit temporären Ordnern):**
  - Verschieben inkl. `.xmp`-Sidecars, Unterordnerstruktur erhalten, Namenskonflikte gelöst (Ziel existiert → `FileExistsError` → Suffix, nie überschreiben).
  - Absturz-Simulation (Prozess nach 30 % beenden) → Neustart repariert Journal; Undo stellt alles her.
  - Undo ohne `%LOCALAPPDATA%`-Cache allein über `_FotoDuenner_Protokoll.json` funktioniert.
  - Gesperrte Datei → `failed`, Rest erfolgreich.
  - Erneutes Öffnen nach Übernehmen → Status „übernommen“, kein automatisches erneutes Ausdünnen.
  - **Invariante:** Nach Übernehmen + Undo ist der Ordnerinhalt bytegleich zum Ausgangszustand (Test vergleicht Dateiliste + Größen + SHA256).
  - Keine einzige Lösch-Operation im Code auf Nutzerdateien (Code-Review-Check: `grep` nach `os.remove`, `unlink`, `rmtree`, `send2trash` – nur auf Cache, Protokoll-Kopie und leere Ordner erlaubt).

### M7 – Kalibrierung an echten Alben
- In-App-Modus **„Bewertung erfassen“** (D10): alle Bilder zunächst behalten, Nutzer klickt aus, was er selbst aussortieren würde – für 300–500 zusammenhängende Bilder eines Albums. Zusätzlich visuelle Prüfung von ~30 Gruppen („gleiches Motiv?“ ja/nein). **Nicht** über Sterne im Explorer (würde Originale ändern und kollidiert mit dem Sterne-Schutz).
- Gruppierungs-Embedding (D19): Wirken Gruppen „zu thematisch“, DINOv2-small statt CLIP prüfen (`scripts/compare_grouping_embeddings.py`).
- `scripts/calibrate.py`: Rastersuche über Kernparameter (`MOTIF_THRESHOLDS`, `MMR_LAMBDA`, `CLEAN_DISTINCT_COS`, Gewichte in 03 §6, `BLUR_ABS`) gegen die Nutzerauswahl. Metriken:
  - **Verlust wichtiger Bilder:** Anteil vom Nutzer behaltener Bilder, die FotoDünner aussortiert (Ziel ≤ 10 %, Personenbilder ≤ 5 %).
  - **Übereinstimmung** gesamt (F1 auf „behalten“).
  - Gruppierungsqualität: ≥ 90 % der geprüften Gruppen „gleiches Motiv“.
- Mit kalibriertem `BLUR_ABS` wird Unschärfe-Aussortierung aktiv (T5).
- Ergebnis-Parameter in `config.py`, Entscheidungen in `DECISIONS.md`. Hinweis: Lieber weniger aggressiv als zu aggressiv – im Zweifel behalten.

### M8 – Paketierung Windows
- `fotoduenner.spec`, `scripts/build_windows.ps1`, `ANLEITUNG.txt`, `THIRD_PARTY_LICENSES.txt` (D14). Build-Ausgabe außerhalb von Google Drive (A8).
- Veröffentlichung als ZIP über GitHub-Releases (D1, D8).
- **Abnahme:** ZIP auf sauberem Windows 11 (Windows-Sandbox, kein Python) entpacken → Doppelklick → vollständiger Durchlauf inkl. Übernehmen/Undo; Installationspfad mit Umlaut und Leerzeichen funktioniert; Fenster schließen beendet das Programm sauber (keine Restprozesse); kein Netzwerkzugriff (Test mit getrennter Verbindung).

### M9 – Feinschliff (optional)
- Papierkorb-Option, „Best-of“-Export, Personen-Körperdetektor, synchroner Zoom, Taste F, `G`-Tastenkombis, HEIC/PNG, Code-Signing.

## Teststrategie

### Testdaten
- **Synthetisch (im Repo, klein):** `tests/fixtures/make_fixtures.py` erzeugt JPEGs per Pillow/NumPy: scharf/unscharf (Gaußfilter), über-/unterbelichtet, Serien (gleiches Bild mit kleinem Versatz/Rauschen), exakte Kopien, Belichtungsreihen (`ExposureBiasValue`), Panorama-Folgen (Ausschnitte eines breiten Bildes mit Versatz), EXIF-Zeiten (via `piexif`), Orientierungen, Umlaut-Dateinamen, `.xmp`-Sidecars mit Rating. Erzeugte Dateien unter `tests/fixtures/generated/` (nicht im Git).
- **Gesichter:** wenige frei lizenzierte Porträtfotos (CC0 bzw. Lizenz geprüft), Lizenz in `tests/fixtures/LICENSES.md`. **Keine echten privaten Fotos im Repo** (D8).
- **Echte Alben (nicht im Repo):** Pfad über Umgebungsvariable `FOTODUENNER_REAL_ALBUM`; Tests mit `@pytest.mark.real` nur lokal.

### Testarten
| Art | Inhalt |
|---|---|
| Unit | EXIF-Parsing, Sidecars, Hashes, Schärfe-Monotonie (unschärfer ⇒ kleiner), Belichtung, Sequenzen, Clustering, MMR, Modi, Quote, Begründungen |
| Integration | Pipeline auf Fixture-Ordner → DB-Inhalte plausibel; API-Endpunkte mit `httpx`/`TestClient` |
| Datensicherheit | Apply/Undo-Invariante, Absturz-Reparatur, Undo über Protokoll-Kopie, gesperrte Dateien, Namenskonflikte, Long Paths |
| Performance | `scripts/benchmark.py` |
| Manuell | Checkliste unten |

### Benchmark (`scripts/benchmark.py`)
- Erzeugt oder nutzt Ordner mit N (Standard 300) 24-MP-JPEGs à ~12–15 MB (z. B. hochskalierte, verrauschte Fotos, damit die Dateigröße realistisch ist).
- Misst je Stufe ms/Bild (Lesen, Hash, Dekodieren, Vorschauen, Qualität, Gesichter, Augen, CLIP) und Gesamtdurchsatz bei 1, 2, 3 Workern; Spitzen-RAM.
- Gibt Hochrechnung für 2.000 Bilder aus. Ziel siehe 01 §6. Ergebnis in `docs/BENCHMARKS.md` protokollieren (mit CPU-Modell).
- Der Entwicklungsrechner hat 16 Threads: Worker standardmäßig auf 3 begrenzen (≈ 4-Kern-Laptop) und die ungebremste Zahl zusätzlich melden (A7).

### Manuelle Abnahme-Checkliste (vor Auslieferung)
- [ ] Album auf USB-Festplatte analysiert, Laptop bleibt dabei bedienbar.
- [ ] Hochformatbilder richtig gedreht, Gesichter erkannt.
- [ ] Serie von 10 Gruppenfotos: behaltenes Bild hat alle Augen offen.
- [ ] Sonnenuntergangs-Serie: nicht als „unterbelichtet“ aussortiert.
- [ ] Porträt mit unscharfem Hintergrund: nicht als „unscharf“ aussortiert.
- [ ] Panorama-Serie und Belichtungsreihe bleiben vollständig erhalten.
- [ ] Modus „Nur Doppelte & Schlechte“ lässt Motivgruppen unangetastet.
- [ ] Regler 30 % ↔ 90 % und Moduswechsel: Übersicht reagiert sofort, manuelle Klicks bleiben.
- [ ] Scrollen mit 2.000 Bildern bleibt trotz Animationen flüssig.
- [ ] Übernehmen → Explorer zeigt `_Aussortiert` → Undo → alles wieder da.
- [ ] Programm zweimal gestartet → nur eine Instanz, vorhandenes Fenster kommt nach vorn.
- [ ] Fenster geschlossen → keine FotoDünner-Prozesse mehr im Task-Manager.

## Risiken und Gegenmaßnahmen

| Risiko | Gegenmaßnahme |
|---|---|
| Analyse auf Office-Laptop zu langsam | `draft`-Dekodierung, INT8-CLIP, Augenprüfung nur bei großen Gesichtern, zweiter Durchgang nur für Serien mit Gesichtern; Benchmark ab M1; notfalls Vorschau-Größe L auf 1280 px senken |
| Augen-Modell: MediaPipe-Paketkonflikt / PyInstaller | Spike in M0; Fallback reines ONNX-Landmarkenmodell (03 §3.5) |
| WebView2 fehlt auf altem Windows 10 | Prüfung beim Start, Meldung mit Link (A3) |
| Gruppierung zu grob/zu fein | Regler + Mega-Gruppen-Teilung + CLIP/DINOv2-Vergleich (M3) + Kalibrierung M7 |
| Falsche „Beste“-Wahl in Serien | Vorschläge sind nur Vorschläge; Klick dreht um; Vergleichsansicht; zweiter Durchgang für Gesichtsschärfe |
| Zero-Shot-Labels unzuverlässig | Nie alleiniger Grund zum Aussortieren; einzeln abschaltbar (D13) |
| Datenverlust | Nur Verschieben (`os.rename`), Journal + Protokoll-Kopie, Undo, Invarianten-Test, kein Löschcode |
| Modell-Lizenz/Quelle falsch | Prüfpflicht in CLAUDE.md, `MODELS.md`, `THIRD_PARTY_LICENSES.txt` |
| Virenscanner meldet `.exe` | onedir statt onefile (weniger Fehlalarme), Anleitung; optional Signierung |
| Projekt in Google Drive (Sync-Konflikte in `.git`, große Build-Ordner) | Umgebung, Build und Modelle außerhalb von Drive (D11, A8); Git-Remote auf GitHub als Sicherung |
