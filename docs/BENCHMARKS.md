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
