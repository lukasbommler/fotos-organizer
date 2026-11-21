# 02 – Architektur und Technik

## 1. Überblick

```
┌──────────────────────────── FotoDuenner.exe (PyInstaller, onedir) ───────────────────────────┐
│                                                                                              │
│  Launcher ──► startet FastAPI/uvicorn auf 127.0.0.1:<freier Port> (Thread)                   │
│           ──► öffnet eigenes Fenster (pywebview / Edge WebView2) mit Start-URL + Token       │
│                                                                                              │
│  ┌─────────── Backend (Python) ────────────┐        ┌──────── Frontend (statisch) ────────┐  │
│  │ api/        REST + Server-Sent Events   │◄──────►│ index.html, app.js, styles.css      │  │
│  │ scanner/    Dateien finden, Sidecars,   │  HTTP  │ Bündel-Übersicht, Großansicht,      │  │
│  │             EXIF, Datei-Hash            │        │ Regler, Fortschritt                 │  │
│  │ pipeline/   Einlesen + Merkmale         │        └─────────────────────────────────────┘  │
│  │ grouping/   Sequenzen, Serien, Motive   │                                                 │
│  │ selection/  Bewertung, Auswahl, Modi    │                                                 │
│  │ apply/      Verschieben, Journal, Undo  │                                                 │
│  │ store/      SQLite + Vorschau-Cache     │                                                 │
│  │ models/     ONNX-Modelle (mitgeliefert) │                                                 │
│  └─────────────────────────────────────────┘                                                 │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
          Daten: %LOCALAPPDATA%\FotoDuenner\  (Datenbank, Vorschauen, Logs)
          Album: nur lesen; einzig erlaubte Änderung: Verschieben nach _Aussortiert\
```

**Warum HTML-Oberfläche in eigenem Fenster?** Ein Kachel-/Bündel-Raster mit tausenden Vorschaubildern, Lazy Loading, Zoom, Animationen und Tastatursteuerung ist in HTML/CSS/JS deutlich einfacher und flüssiger umzusetzen als in Tkinter/Qt. Ab v1 läuft die Oberfläche in einem eigenen Fenster über `pywebview` (Edge WebView2, auf Windows 10/11 in der Regel vorhanden), kein Browser-Tab (D4). Der lokale Server und das Sitzungs-Token bleiben, damit das Frontend normales HTTP nutzen kann.

## 2. Technologie-Stack

| Zweck | Wahl | Begründung |
|---|---|---|
| Sprache | Python 3.12 (64 Bit) | Bildverarbeitungs-Ökosystem; alle Pakete haben Windows-Wheels für 3.12 |
| Umgebung | `uv` | Schnell, reproduzierbar (`uv.lock`); Umgebung außerhalb von Google Drive (D11) |
| Fenster | `pywebview` (Edge WebView2) | Eigenes Fenster, nativer Ordnerdialog |
| Bild-Einlesen | Pillow ≥ 10 (JPEG-`draft`-Modus) | Schnelles verkleinertes Dekodieren; nur JPEG in v1 (D2) |
| EXIF / XMP | Pillow `getexif()`, eingebettetes XMP; `.xmp`-Sidecar per XML-Parser | Aufnahmezeit, Orientierung, Kamera, Rating, `ExposureBiasValue` |
| Bildoperationen | `opencv-python-headless` ≥ 4.9, NumPy | Laplace-Schärfe, YuNet-Gesichtsdetektor, Phasenkorrelation |
| ML-Inferenz | `onnxruntime` (CPU) | Klein (~50 MB), schnell auf CPU, kein PyTorch nötig |
| Augen offen | MediaPipe Face Mesh V2 + Blendshape V2, **nach ONNX konvertiert**, über `onnxruntime` (D17) | Kein `mediapipe`-Paket → kein OpenCV-Konflikt, kleineres Paket |
| Perzeptueller Hash | eigene pHash-Implementierung mit NumPy | Schnelle Erkennung fast identischer Bilder |
| Datei-Hash | `hashlib` (BLAKE2b) über den Dateiinhalt | Exakte Duplikate (T7) |
| Clustering | NumPy/SciPy (`scipy.cluster.hierarchy`) | 2.000×2.000 Distanzmatrix ist trivial; scikit-learn nicht nötig |
| Web-Backend | FastAPI + uvicorn | Einfach, async, SSE für Fortschritt |
| Frontend | Vanilla JS (ES Modules) + CSS, kein Build-Schritt | Keine Node-Toolchain nötig, leicht paketierbar |
| Datenbank | SQLite (`sqlite3`, WAL-Modus) | Ein File pro Album, robust |
| Prozesse | `psutil` | Prozesspriorität, physische Kerne (T12) |
| Paketierung | PyInstaller (onedir) | Ein Ordner mit `FotoDuenner.exe`, ohne Python-Installation |
| Tests | pytest, pytest-benchmark (optional) | |

Geschätzte Paketgröße: 300–600 MB entpackt (OpenCV, ONNX Runtime, ggf. MediaPipe, Modelle). Akzeptabel.

## 3. Modul- und Verzeichnisstruktur

```
FotoDuenner/
├── CLAUDE.md
├── LICENSE                     # MIT (D14)
├── pyproject.toml / uv.lock
├── docs/                       # dieser Plan + DECISIONS.md + BENCHMARKS.md
├── models/                     # MODELS.md im Git; Modell-Dateien per fetch_models.py (nicht im Git)
├── scripts/
│   ├── fetch_models.py         # lädt Modelle einmalig (Entwicklung), prüft SHA256
│   ├── make_text_embeddings.py # Zero-Shot-Text-Embeddings mit CLIP-Text-Encoder (nur Entwicklung)
│   ├── benchmark.py            # misst Durchsatz je Stufe
│   └── build_windows.ps1       # PyInstaller-Build + ZIP + THIRD_PARTY_LICENSES
├── fotoduenner/
│   ├── __main__.py             # Launcher: Einzelinstanz, Port, Server-Thread, pywebview-Fenster
│   ├── config.py               # alle Schwellen/Gewichte (Dataclass, Defaults aus 03)
│   ├── paths.py                # %LOCALAPPDATA%, \\?\-Long-Path-Handling
│   ├── scanner/
│   │   ├── discover.py         # JPEG-Dateien finden, Ausschlüsse
│   │   ├── sidecars.py         # .xmp-Sidecars zuordnen → "PhotoItem"
│   │   └── exif.py             # Zeit, Orientierung, Kamera, Rating, ExposureBias
│   ├── pipeline/
│   │   ├── decode.py           # schnelles verkleinertes Laden (JPEG-draft)
│   │   ├── thumbs.py           # Vorschauen 320 px + 1600 px (JPEG)
│   │   ├── quality.py          # Schärfe, Belichtung
│   │   ├── faces.py            # YuNet + Augen offen
│   │   ├── embed.py            # CLIP-Embedding (ONNX)
│   │   ├── aesthetic.py        # Ästhetik-Kopf auf CLIP-Embedding
│   │   ├── zeroshot.py         # Zero-Shot-Labels aus Embedding + vorab berechneten Text-Embeddings
│   │   ├── hashing.py          # pHash + Datei-Hash
│   │   └── runner.py           # Orchestrierung, Prozess-Pool, Fortschritt, Abbruch
│   ├── grouping/
│   │   ├── sequences.py        # Belichtungsreihen, Panoramen
│   │   └── cluster.py          # Distanz, Serien, Motivgruppen
│   ├── selection/
│   │   ├── scoring.py          # Bildwert-Score
│   │   ├── select.py           # Rangfolge je Gruppe (MMR), Qualitäts-Aussortierung
│   │   ├── modes.py            # Modus "clean" und "quota" (globale Zielquote)
│   │   └── reasons.py          # deutsche Begründungstexte
│   ├── apply/
│   │   ├── mover.py            # Verschieben mit Journal
│   │   └── undo.py
│   ├── store/
│   │   ├── db.py               # Schema, Migrationen
│   │   └── cache.py            # Vorschau-Dateien
│   ├── api/
│   │   ├── server.py           # FastAPI-App, statische Dateien, Token
│   │   ├── routes_*.py
│   │   └── events.py           # SSE-Fortschritt
│   └── web/                    # index.html, app.js, styles.css, icons
└── tests/
    ├── fixtures/               # Generator + kleine CC0-Testbilder (siehe 05)
    └── test_*.py
```

## 4. Datenhaltung

### Speicherort
`%LOCALAPPDATA%\FotoDuenner\albums\<album_id>\`
- `album.db` – SQLite
- `thumbs\<photo_id>_s.jpg` (320 px lange Kante, für Kacheln/Bündel)
- `thumbs\<photo_id>_l.jpg` (1600 px, für Großansicht; der 100 %-Zoom lädt das Original über die API)

Vorschauen als JPEG statt WebP (deutlich schnellere Kodierung, T10).

`album_id` = SHA1 des normalisierten absoluten Ordnerpfads. Zusätzlich `albums\index.json` mit Pfad → ID und letztem Zugriff (für „Zuletzt geöffnet“). Cache-Aufräumen: Alben, deren Ordner nicht mehr existiert oder > 180 Tage nicht geöffnet wurden, können in den Einstellungen gelöscht werden.

**Merkmal-Cache über Datei-Fingerabdruck (T13):** Merkmale werden zusätzlich über den Fingerabdruck (Dateiname + Größe + mtime + EXIF-Zeit) wiedergefunden, nicht nur über den Album-Pfad. So bleiben sie z. B. nach Umbenennen/Verschieben des Album-Ordners gültig.

**Warum nicht im Album?** Das Album bleibt unberührt, und der Cache liegt auf der schnellen internen SSD, auch wenn die Fotos auf einer USB-Platte sind. Einzige Ausnahme: die Protokoll-Kopie `_Aussortiert\_FotoDuenner_Protokoll.json` beim Übernehmen (T4).

### Schema (Kern)

```sql
photos(
  id INTEGER PRIMARY KEY,
  rel_path TEXT UNIQUE,          -- JPEG relativ zum Album
  companions TEXT,               -- JSON-Liste weiterer Dateien (.xmp)
  size INTEGER, mtime REAL,      -- Cache-Schlüssel
  fingerprint TEXT,              -- Name + Größe + mtime + EXIF-Zeit (T13)
  file_hash BLOB,                -- BLAKE2b des Inhalts (exakte Duplikate)
  taken_at REAL,                 -- Unix-Zeit (EXIF DateTimeOriginal + SubSec, Fallback mtime)
  taken_at_source TEXT,          -- 'exif' | 'mtime'
  camera TEXT,                   -- Make+Model (Zeitversatz zwischen Geräten, Sequenzen)
  exposure_bias REAL,            -- EXIF ExposureBiasValue (Belichtungsreihen)
  width INTEGER, height INTEGER, orientation INTEGER,
  rating INTEGER,                -- EXIF/XMP-Rating, NULL wenn keins
  status TEXT,                   -- 'pending' | 'done' | 'error'
  error TEXT,
  algo_version INTEGER
)
features(
  photo_id INTEGER PRIMARY KEY,
  phash BLOB,                    -- 64 Bit
  embedding BLOB,                -- float16[512], L2-normalisiert
  sharpness_global REAL, sharpness_peak REAL, motion_blur REAL,
  exposure_mean REAL, clip_low REAL, clip_high REAL, contrast REAL,
  aesthetic REAL,
  zeroshot TEXT,                 -- JSON: {label: score}
  face_count INTEGER,
  faces TEXT                     -- JSON: [{box, size_rel, conf, sharpness, eyes_open}, ...]
)
decisions(
  photo_id INTEGER PRIMARY KEY,
  manual TEXT,                   -- NULL | 'keep' | 'reject'  (Nutzer-Override, bleibt immer erhalten)
  locked INTEGER DEFAULT 0
)
journal(
  id INTEGER PRIMARY KEY, batch_id TEXT, created_at REAL,
  src TEXT, dst TEXT, state TEXT  -- 'planned' | 'moved' | 'reverted' | 'failed'
)
settings(key TEXT PRIMARY KEY, value TEXT)
  -- mode ('clean' | 'quota'), Zielquote, Gruppierungsstärke, Optionen,
  -- applied ('0' | '1') + count_before (Status "übernommen", T2)
```

Gruppen, Sequenzen und automatische Entscheidungen werden **nicht** persistiert, sondern bei jedem Laden/Reglerwechsel aus `features` neu berechnet (dauert < 300 ms). Persistiert werden nur Merkmale, manuelle Entscheidungen, Einstellungen und Journal.

## 5. Analyse-Pipeline und Parallelisierung

Zuerst wird die **einfache Variante** gebaut und gemessen (T11):

```
Scan (Hauptprozess, schnell)
  └─► Liste PhotoItems, Abgleich mit DB (neu / geändert / unverändert)
Analyse – pro Bild komplett in einem Worker (ProcessPool, Worker = max(1, Kerne − 1)):
  Datei lesen → Datei-Hash → JPEG mit draft verkleinert dekodieren → Orientierung anwenden
  → Vorschauen schreiben (320 + 1600 px, JPEG)
  → Schärfe, Belichtung, pHash
  → Gesichter (YuNet) auf 640-px-Version; Augen offen auf Gesichtsausschnitten
  → CLIP-Embedding (ONNX, 1 Thread pro Worker) → Ästhetik → Zero-Shot-Scores
Ergebnisse werden in Batches (z. B. alle 50 Bilder) in SQLite geschrieben → Fortsetzen nach Abbruch.
Zweiter Durchgang (nur Serienmitglieder mit Gesichtern, T6):
  Original in voller Auflösung dekodieren, Gesichtsschärfe auf nativen Ausschnitten.
```

Eine getrennte CLIP-Batch-Stufe (Hauptprozess, Batch 16–32, mehrere Threads) wird nur gebaut, wenn sie im Benchmark messbar schneller ist.

Details:
- **Dekodierung (T9):** Die `draft`-Skalierung (1/2, 1/4, 1/8) so wählen, dass die lange Kante ≥ ~1500 px bleibt (bei 24 MP meist 1/4).
- Jeder Worker lädt YuNet, Augenmodell und CLIP **einmal** beim Start (Pool-Initializer), nicht pro Bild.
- ONNX Runtime: `intra_op_num_threads = 1` pro Worker, `graph_optimization_level = ORT_ENABLE_ALL`. CLIP bevorzugt als INT8-quantisierte Variante; Qualität gegen FP32 prüfen (siehe 05).
- **Prozesspriorität** auf „Niedriger als normal“ setzen (`psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)`, auch in Workern), damit der Laptop bedienbar bleibt.
- Windows + multiprocessing: Start-Methode `spawn`; `if __name__ == "__main__":` und `multiprocessing.freeze_support()` im Launcher – **Pflicht für PyInstaller**.
- Abbruch: gemeinsames `Event`; laufende Bilder fertig machen, Rest verwerfen, Status bleibt `pending`.
- Fortschritt per SSE: `{stage, done, total, eta_seconds, current_file}`; ETA aus gleitendem Mittel der letzten 50 Bilder.

### Zeitbudget (Schätzung, 4-Kern-i5, Bild 24 MP / 15 MB JPEG, SSD)

| Schritt | pro Bild (1 Kern) | Bemerkung |
|---|---|---|
| Datei lesen + Hash | 25–45 ms | USB-HDD: 100–150 ms (dann I/O-gebunden) |
| JPEG-Dekodierung mit `draft` (1/4 im DCT) | 40–70 ms | ohne `draft` 250–400 ms – **entscheidender Trick** |
| Vorschauen (2 Größen, JPEG q=85) | 8–15 ms | |
| Schärfe + Belichtung + pHash | 10–15 ms | |
| YuNet (640 px) | 10–20 ms | |
| Augen offen (nur Bilder mit Gesichtern, je Gesicht) | 15–30 ms | |
| CLIP ViT-B/32 INT8 (1 Thread) | 40–80 ms | |
| Zero-Shot + Ästhetik | < 1 ms | Skalarprodukte auf dem Embedding |
| **Summe** | **≈ 150–270 ms CPU-Zeit** | parallelisiert auf ~3 Kerne → **≈ 3–6 min für 2.000 Bilder** |
| Zweiter Durchgang (T6) | 250–400 ms pro betroffenem Bild | nur Serienmitglieder mit Gesichtern |

## 6. Übernehmen und Rückgängig (Datensicherheit)

Ablauf „Übernehmen“:
1. Liste der zu verschiebenden Dateien berechnen (JPEG + `.xmp`-Sidecar jedes aussortierten Bildes).
2. Prüfungen: ausreichende Rechte; keine Datei gesperrt. (`_Aussortiert` liegt im Album, also auf demselben Laufwerk – Verschieben ist ein atomares Umbenennen, kein Kopieren.)
3. Neuer `batch_id`; alle Vorgänge als `planned` ins Journal schreiben, **commit**. Journal-Kopie nach `_Aussortiert\_FotoDuenner_Protokoll.json` schreiben (T4).
4. Pro Datei: Zielpfad `<Album>\_Aussortiert\<rel_path>`; **`os.rename`** (schlägt unter Windows fehl, wenn das Ziel existiert → atomar, überschreibt nie; T3). Bei `FileExistsError` Suffix ` (2)`, ` (3)` … und erneut versuchen. Nach Erfolg Journal-Eintrag `moved`, commit (in kleinen Transaktionen); Protokoll-Kopie am Ende des Batches aktualisieren.
5. Bei Fehler: Eintrag `failed` mit Meldung, weiter mit nächster Datei; am Ende Bericht.
6. Beim Programmstart: Journal-Einträge `planned` ohne Abschluss prüfen (Datei an Quelle oder Ziel?) und Status korrigieren (Absturzsicherheit).
7. Album-Status `applied = 1` + Ausgangsanzahl in `settings` speichern (T2).

„Rückgängig“ verschiebt alle `moved`-Einträge eines Batches in umgekehrter Reihenfolge zurück (wieder `os.rename`; ist der Quellpfad belegt, Suffix und Hinweis), Status `reverted`. Ist die Album-DB nicht mehr vorhanden, wird das Protokoll aus `_Aussortiert\_FotoDuenner_Protokoll.json` gelesen. Leere Unterordner in `_Aussortiert` werden danach entfernt (nur leere Ordner, `os.rmdir`!). Die Protokoll-Kopie ist eine Datei des Programms und darf nach vollständigem Rückgängigmachen entfernt werden.

Nach dem Übernehmen gelten verschobene Bilder als „nicht mehr im Album“; erneutes Öffnen zeigt sie nicht mehr (der Ordner `_Aussortiert` wird beim Scan ignoriert). Optional: Bereich „Bereits aussortiert“ mit Zurückholen-Knopf.

## 7. Windows-Fallstricke (unbedingt beachten)

1. **Nicht-ASCII-Pfade** (Umlaute, „Südtirol 2024“): `cv2.imread` scheitert → nur Pillow / `np.fromfile` + `cv2.imdecode` verwenden. ONNX-Modelle als Bytes laden (`InferenceSession(model_bytes)`), falls der Installationspfad Umlaute enthält (z. B. Benutzername „Jürgen“). YuNet ebenfalls aus Bytes. Falls MediaPipe: `model_asset_buffer` statt `model_asset_path`.
2. **Lange Pfade** (> 260 Zeichen): `LongPathsEnabled` ist auf typischen Rechnern aus → bei Dateioperationen intern `\\?\`-Präfix verwenden (in `paths.py` kapseln) und testen (Annahme A6).
3. **OneDrive-„Nur online“-Dateien**: nicht erwartet (D7), aber einfache Prüfung über Dateiattribute `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS (0x400000)` / `FILE_ATTRIBUTE_OFFLINE`; vor der Analyse warnen, Anzahl anzeigen, Option „Überspringen“.
4. **Defender/Virenscanner** verlangsamen erstes Lesen vieler Dateien – nicht beeinflussbar, in Benchmark berücksichtigen.
5. **Gesperrte Dateien** (Bild in anderem Programm geöffnet): Verschieben schlägt fehl → `failed`, Hinweis, Rest geht weiter.
6. **EXIF-Orientierung**: immer anwenden (nach `draft` prüfen, ob `ImageOps.exif_transpose` korrekt arbeitet), sonst stehen Hochformatbilder quer und Gesichtserkennung versagt.
7. **Zeitzonen/Uhrzeiten**: Kamera und Handy haben oft unterschiedliche Uhrzeiten. v1: EXIF-Zeit unverändert nutzen; Gruppierung stützt sich primär auf Bildinhalt, Zeit nur als weiches Kriterium (siehe 03), daher robust.
8. **SmartScreen**: Unsignierte `.exe` erzeugt beim ersten Start „Der Computer wurde durch Windows geschützt“ → in der Anleitung erklären („Weitere Informationen → Trotzdem ausführen“). Optional Code-Signing später.
9. **Einzelinstanz und Port**: freien Port dynamisch wählen; Lock-Datei mit Port in `%LOCALAPPDATA%\FotoDuenner\`. Zweiter Start erkennt die laufende Instanz und holt deren Fenster nach vorn (Annahme A3).
10. **Fenster schließen = Programm beenden**: Server-Thread und Worker werden sauber beendet; läuft eine Analyse oder ein Übernehmen, fragt das Programm vorher nach (Annahme A3). Kein Heartbeat, kein Konsolenfenster (D4).
11. **WebView2 fehlt** (alte Windows-10-Installation): verständliche Meldung mit Link zum Microsoft-Installer statt Absturz.

## 8. Paketierung und Auslieferung

- `scripts/build_windows.ps1`: `uv sync` → `pyinstaller fotoduenner.spec` → Ordner `FotoDuenner\` mit `FotoDuenner.exe`, `models\`, `web\` → ZIP `FotoDuenner-<version>-win64.zip`. Build-Ausgabe außerhalb von Google Drive (Annahme A8).
- PyInstaller-Spec: `--onedir` (schnellerer Start als onefile, keine Entpackung nach %TEMP%, weniger Virenscanner-Fehlalarme), `windowed` (kein Konsolenfenster), `collect_data_files` für `onnxruntime`, `webview` (und ggf. `mediapipe`); `models\` und `web\` als Daten.
- Auslieferung über **öffentliche GitHub-Releases** im Account `lukasbommler` (D1, D8). Modell-Dateien sind nicht im Git, sondern nur im ZIP.
- Lizenzen: `LICENSE` (MIT) und `THIRD_PARTY_LICENSES.txt` (alle mitgelieferten Pakete und Modelle, beim Build erzeugt) im ZIP (D14).
- Erst-Start-Test auf einem **sauberen** Windows (ohne Python), z. B. Windows-Sandbox.
- Entwicklermodus parallel: `uv run python -m fotoduenner` (gleiches Verhalten). Umgebung über `UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\uv-envs\fotoduenner` außerhalb von Google Drive (D11).
- Versionsnummer im UI anzeigen; `algo_version` in `config.py` – Erhöhung erzwingt Neuberechnung der Merkmale.
- Benutzeranleitung `ANLEITUNG.txt` (Deutsch, 1 Seite) im ZIP: Entpacken, Starten, SmartScreen, erst mit Kopie testen, Rückgängig, Deinstallation (Ordner löschen + `%LOCALAPPDATA%\FotoDuenner`).
