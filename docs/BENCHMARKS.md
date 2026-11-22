# Benchmarks

Erzeugt mit `scripts/benchmark.py` (05). Neueste Messung jeweils unten.

### 2026-11-22 14:19 – M1 (Einlesen + Vorschauen), v0.1.0

- CPU: AMD Ryzen 7 PRO 4750U with Radeon Graphics (8 Kerne / 16 Threads), RAM 34 GB, Python 3.12.16
- 300 synthetische 24-MP-JPEGs, Ø 12.8 MB, SSD; Dateien lagen im Datei-Cache des Betriebssystems (Lesezeiten optimistisch, USB-HDD deutlich langsamer)

| Worker | ms/Bild (Wand) | Hochrechnung 2.000 | Spitzen-RAM | read ms | hash ms | decode ms | thumbs ms |
|---|---|---|---|---|---|---|---|
| 1 | 239 | 8.0 min | 107 MB | 19 | 9 | 183 | 24 |
| 2 | 114 | 3.8 min | 161 MB | 7 | 9 | 182 | 23 |
| 3 (A7) | 81 | 2.7 min | 217 MB | 7 | 9 | 193 | 25 |
| 7 | 39 | 1.3 min | 445 MB | 7 | 10 | 202 | 27 |

Stufen-Zeiten = CPU-Zeit je Bild innerhalb eines Workers (Mittelwert).

**Bewertung M1:** Abnahme erfüllt – 3 Worker (≈ 4-Kern-Laptop, A7): 2.000 Bilder ≈ 2,7 min (Ziel ≤ 3 min). Die Dekodierung (≈ 120 ms isoliert, 180–200 ms unter Last inkl. Öffnen, Metadaten, Drehen) ist deutlich teurer als in 02 §5 geschätzt (40–70 ms): Bei 13-MB-Dateien dominiert die Entropie-Dekodierung, die `draft` nicht einspart (volle Dekodierung 156 ms vs. `draft` 1/4 121 ms). Typische Handy-Fotos (3–5 MB) sind entsprechend schneller. Für M3 (Gesichter, CLIP) bleibt damit weniger Puffer bis zum Ziel von 6 min – dort messen und ggf. `DECODE_MIN_LONG_EDGE` senken.

### 2026-11-22 20:15 – M3 (alle Merkmale + 2. Durchgang), v0.1.0

- CPU: AMD Ryzen 7 PRO 4750U with Radeon Graphics (8 Kerne / 16 Threads), RAM 34 GB, Python 3.12.16
- 300 synthetische 24-MP-JPEGs, Ø 12.6 MB, SSD; Dateien lagen im Datei-Cache des Betriebssystems (Lesezeiten optimistisch, USB-HDD deutlich langsamer)
- 40 % mit Gesicht; zweiter Durchgang (T6) für 83 Bilder (Worst Case: jedes Gesichtsbild in einer Serie)

| Worker | CLIP | ms/Bild (Wand) | Hochrechnung 2.000 | Spitzen-RAM | read ms | hash ms | decode ms | thumbs ms | quality ms | faces ms | clip ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 (A7) | worker | 165 | 5.5 min | 2169 MB | 6 | 8 | 142 | 19 | 21 | 55 | 138 |
| 3 (A7) | stage | 136 | 4.5 min | 1252 MB | 7 | 9 | 178 | 23 | 25 | 63 | 65 |

Stufen-Zeiten = CPU-Zeit je Bild innerhalb eines Workers (Mittelwert); „faces“ nur erster Durchgang; „clip“ bei „stage“ = Anteil am Batch im Hauptprozess (16 Bilder, mehrere Threads). Wand-Zeit und Hochrechnung enthalten den zweiten Durchgang.

**Einordnung M3:**
- Abnahme „2.000 Bilder ≤ 10 min (Ziel 6) inkl. zweitem Durchgang“: **4,5 min** mit 3 Workern und getrennter CLIP-Stufe – auch im Worst Case, in dem jedes Gesichtsbild den zweiten Durchgang bekommt (synthetische Hintergründe sehen für CLIP alle gleich aus). Echte Alben: weniger zweite Durchgänge, dafür mehr Gesichter je Bild.
- **T11 – getrennte CLIP-Stufe:** CLIP allein im Batch (16 Bilder, 3 Threads) 1,46× schneller als 3 Worker × 1 Thread (`scripts/bench_clip_variants.py`, FP16: 21,8 vs. 14,9 Bilder/s). Gesamt-Pipeline: 136 statt 165 ms/Bild (−18 %) und **Spitzen-RAM 1,25 statt 2,17 GB** (ein CLIP-Modell statt drei). → getrennte Stufe gebaut (DECISIONS A26). Mit dem INT8-Modell war der Batch nicht schneller (0,97×) – der Unterschied kommt vom FP16/FP32-Rechenweg.
- **CLIP-Varianten gegen FP32** (154 Fotos des Testsets, `scripts/eval_features.py clip`): Xenova „quantized“ (INT8, Plan) mittlere Kosinus-Ähnlichkeit 0,924 (min. 0,73), Xenova „uint8“ 0,975, Xenova „int8“ 0,973, q4 0,970, **FP16 1,0000** (Paarabstände unverändert, INT8 verschiebt Paare nahe der Serien-Schwelle um Ø 0,047). Laufzeit 1 Thread: INT8 90 ms, FP16 109 ms, FP32 107 ms. → FP16 (A25).
- Qualitätsstufe nach Optimierung (Graustufen vor dem Verkleinern, Kachel-Statistik über Blockmittel, `cv2.meanStdDev`): 21–25 ms unter Last (vorher 41 ms).

