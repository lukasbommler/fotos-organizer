# 03 – Analyse- und Auswahllogik

Dieses Dokument ist das fachliche Herzstück. Alle Zahlenwerte sind **Startwerte** und gehören als benannte Parameter in `config.py`. Sie werden in Meilenstein M7 (siehe 05) an echten Alben kalibriert.

## 1. Grundprinzip

```
Bild ─► Merkmale (einmalig, gecacht)
          │
          ├─► Qualitäts-Prüfung ──► "technisch schlecht" / "Bitte prüfen" (Bereich Qualität)
          │
          ├─► Exakte Duplikate, Sequenzen (Belichtungsreihen, Panoramen)
          │
          └─► Gruppierung ─► Serien (fast identisch) ⊂ Motivgruppen, Rest = Einzelbilder
                               │
                               └─► Rangfolge je Gruppe (Wert + Vielfalt)
                                        │
          Modus ("clean" | "quota") + Schutzregeln ─► behalten / aussortieren + Begründung
```

Kernidee: Ein Bild ist umso eher verzichtbar, je **ähnlicher** es zu bereits behaltenen Bildern ist und je **schlechter** es ist. Einzelne Motive, Personenbilder, Sequenzen und vom Nutzer bewertete Bilder sind geschützt.

## 2. Einlesen (`pipeline/decode.py`)

- **Nur JPEG** in v1 (D2). `Image.open(path)`; vor dem Laden `img.draft("RGB", (w // s, h // s))` mit dem größten Faktor s ∈ {2, 4, 8}, bei dem die lange Kante ≥ `DECODE_MIN_LONG_EDGE` (~1500 px) bleibt (T9). Danach EXIF-Orientierung anwenden (`ImageOps.exif_transpose` nach `draft` prüfen).
- Arbeitsgrößen aus diesem Bild ableiten (einmal dekodieren, dann verkleinern):
  - `img_1600` (bzw. lange Kante des dekodierten Bildes, falls kleiner) → Vorschau L
  - `img_1024` (Graustufen) → Schärfe/Belichtung
  - `img_640` → Gesichtsdetektion
  - `img_320` → Vorschau S
  - `img_256` (Graustufen) → Panorama-Versatz (Phasenkorrelation)
  - `img_224` → CLIP
- Fehler beim Dekodieren (defekte Datei) → `status='error'`, Bild erscheint in UI unter „Nicht lesbar“, wird **nie automatisch aussortiert**.

## 3. Merkmale je Bild

### 3.1 Aufnahmezeit und Metadaten (`scanner/exif.py`)
- `DateTimeOriginal` (+ `SubSecTimeOriginal`), Fallback `DateTimeDigitized`, `DateTime`, zuletzt Datei-`mtime` (`taken_at_source='mtime'`).
- Kamera = Make + Model.
- `ExposureBiasValue` → `exposure_bias` (für Belichtungsreihen, §5.0).
- Rating: EXIF `Rating` (0x4746) bzw. XMP `xmp:Rating` aus eingebettetem XMP oder `.xmp`-Sidecar. Rating ≥ 1 → **geschützt**. Rating −1 (abgelehnt, Lightroom) → Vorschlag aussortieren. Ratings werden nur gelesen, nie geschrieben.

### 3.2 Schärfe (`pipeline/quality.py`)
Auf `img_1024` in Graustufen (float32):
- `sharpness_global` = Varianz des Laplace-Operators (`cv2.Laplacian(gray, cv2.CV_32F, ksize=3).var()`).
- `sharpness_peak` = Bild in 8 × 8 Kacheln teilen, Laplace-Varianz je Kachel, **90. Perzentil**. Begründung: Bei gewollter Hintergrundunschärfe (Porträt, Makro) ist nur ein Teil scharf – der Spitzenwert zählt, nicht der Durchschnitt.
- Kacheln mit sehr geringer Textur (Himmel, Wasser; Standardabweichung der Helligkeit < 4) werden für das Perzentil ignoriert.
- `motion_blur` (optional v1.1): Kohärenz des Strukturtensors in den schärfsten Kacheln; hohe Kohärenz bei niedriger Schärfe → verwackelt.
- Für den Vergleich werden Schärfewerte **album-relativ** als Perzentil-Rang (0..1) genutzt; für die automatische Qualitäts-Aussortierung zusätzlich ein absoluter Schwellwert `BLUR_ABS` (Kalibrierung in M7). Solange `BLUR_ABS = None`, führt Unschärfe nur zu „Bitte prüfen“ (T5, §4).

### 3.3 Belichtung
Auf `img_1024`, Luminanz Y (0..255):
- `exposure_mean` = Mittelwert, `contrast` = Standardabweichung.
- `clip_low` = Anteil Pixel ≤ 4, `clip_high` = Anteil Pixel ≥ 251.
- `q_exposure` (0..1) = 1 − min(1, max(0, clip_high − 0,02) · 4 + max(0, clip_low − 0,08) · 2 + Strafe für Mittelwert < 35 oder > 225).
- Nachtaufnahmen, Gegenlicht und Sonnenuntergänge haben natürlicherweise viele dunkle Pixel → `clip_low` milde gewichten; „stark unterbelichtet“ erst bei `exposure_mean < 20` **und** `contrast < 15`.

### 3.4 Hashes (`pipeline/hashing.py`)
- **Datei-Hash:** SHA-256 über den gesamten Dateiinhalt → exakte Duplikate (§5.0; SHA-256 statt BLAKE2b, A13).
- **pHash:** Graustufen 32 × 32, 2D-DCT, linke obere 8 × 8 Koeffizienten (ohne DC), Bit = Koeffizient > Median → 64 Bit. Hamming-Distanz ≤ 8 ⇒ „nahezu identisch“.

### 3.5 Gesichter (`pipeline/faces.py`)
- **Detektor:** OpenCV **YuNet** (`face_detection_yunet_2023mar.onnx`, opencv_zoo, Lizenz prüfen, ≈ 0,3 MB) über `cv2.FaceDetectorYN.create(...)`, Eingabe `img_640` **und** `img_480` (zusammengeführt, A23), `score_threshold=0.6` (Plan 0,75: nur 91 % Trefferquote am Testset), `nms_threshold=0.3`, `top_k=50`. Modell aus Bytes laden (Umlaut-Pfade!).
- Pro Gesicht speichern: Box (relativ), `size_rel` = Boxbreite / Bildbreite, `conf`, 5 Landmarken.
- Gesichter mit `size_rel < 0,015` verwerfen (Rauschen, Menschenmenge in der Ferne).
- **Gesichtsschärfe:** Box um 20 % erweitern, aus `img_1600` ausschneiden, auf 160 × 160 skalieren, Laplace-Varianz → album-relativer Rang. **Zweiter Durchgang (T6):** Für Serienmitglieder mit Gesichtern wird das Original in voller Auflösung dekodiert und die Gesichtsschärfe auf dem nativen Ausschnitt (ohne Hochskalieren) neu bestimmt – nur so sind feine Unterschiede innerhalb einer Serie sichtbar.
- **Augen offen:** nur für Gesichter mit `size_rel ≥ 0,04` (kleiner ist unzuverlässig → `eyes_open = null`). Teil von v1 (D6).
  - **Umsetzung (M0-Spike, D17):** MediaPipe *Face Mesh V2* + *Blendshape V2*, nach ONNX konvertiert, ausgeführt mit ONNX Runtime (kein `mediapipe`-Paket). Ein-/Ausgaben siehe `models/MODELS.md`.
    1. Ausschnitt aus YuNet: Mittelpunkt der Box, Drehung so, dass die Augenlinie waagerecht ist, Quadrat mit Seitenlänge 1,5 × max(Boxbreite, Boxhöhe) → 256 × 256, RGB 0..1.
    2. Landmarken (478 Punkte) → zweiter Durchgang mit Ausschnitt aus den Landmarken (Drehung über Punkte 33 → 263, Hüllrechteck × 1,5) für einen stabileren Ausschnitt.
    3. 146 ausgewählte Punkte in Bildpixeln → Blendshape-Modell → `eyes_open = 1 − max(eyeBlinkLeft, eyeBlinkRight)`.
    4. Präsenzwert (Sigmoid) < `EYES_MIN_PRESENCE` (0,5) → `eyes_open = null`.
  - Sonnenbrille/abgewandt → Werte unsicher; bei Konfidenz niedrig `null`.
- **Abgeleitet:**
  - `prominence` = max(`size_rel`) der Gesichter.
  - `is_people_photo` = mindestens ein Gesicht mit `size_rel ≥ 0,035` und `conf ≥ 0,85` **oder** Zero-Shot-Label „Person von hinten“ (§3.8). (Passanten im Hintergrund machen ein Landschaftsbild nicht zum Personenbild.)
  - `face_count_main` = Anzahl Gesichter mit `size_rel ≥ 0,5 · prominence`.
- Bekannte Grenze v1: Personen sehr klein oder ohne Gesicht werden nur teilweise (über Zero-Shot) erkannt. Option später: Personen-Detektor (Körper). **Lizenz beachten:** Ultralytics-YOLO ist AGPL – vermeiden.

### 3.6 Bildinhalt-Embedding (`pipeline/embed.py`)
- Modell: **CLIP ViT-B/32**, nur Bild-Encoder, ONNX. Hugging Face `Xenova/clip-vit-base-patch32`, **`onnx/vision_model_fp16.onnx`** (A25: INT8 `vision_model_quantized.onnx` wich mit Kosinus 0,92 zu stark von FP32 ab; MIT-Lizenz des OpenAI-CLIP). Alternativ selbst exportieren mit `open_clip` (Dev-Skript mit PyTorch). Ausgabe-Tensor (`image_embeds`, 512-dim, projiziert) **verifizieren**.
- Vorverarbeitung (CLIP-Standard): kürzere Seite auf 224 (bikubisch), mittig 224 × 224 zuschneiden, RGB float32 / 255, Normierung mean = (0,48145466; 0,4578275; 0,40821073), std = (0,26862954; 0,26130258; 0,27577711), Layout NCHW.
- Embedding L2-normalisieren, als float16 speichern.
- Kontrolle: INT8- vs. FP32-Embeddings auf 200 Bildern vergleichen (mittlere Kosinus-Ähnlichkeit ≥ 0,98 → INT8 verwenden).
- **Gruppierungs-Embedding (T8):** Schon in **M3** CLIP gegen **DINOv2-small** (Apache-2.0) für die Gruppierung vergleichen – CLIP gruppiert eher thematisch („alle Strandbilder“), DINOv2 eher nach gleicher Szene. CLIP bleibt in jedem Fall für Ästhetik und Zero-Shot nötig; DINOv2 nur zusätzlich, wenn messbar besser und das Zeitbudget es erlaubt.

### 3.7 Ästhetik (`pipeline/aesthetic.py`)
- LAION **Aesthetic Predictor v1** für ViT-B/32 (lineares Modell 512 → 1, `sa_0_4_vit_b_32_linear.pth`, Repo `LAION-AI/aesthetic-predictor`, Lizenz prüfen). Gewichte einmalig nach NumPy (`.npz`) konvertieren → keine PyTorch-Laufzeitabhängigkeit. Eingabe: L2-normalisiertes CLIP-Embedding. Ausgabe ≈ 1–10.
- Ehrliche Einordnung: grobe Schätzung, gut um „langweilig/missglückt“ von „schön“ zu trennen, schlecht für feine Unterschiede innerhalb einer Serie. Daher **geringes Gewicht** innerhalb von Gruppen; stärker relevant beim Füllen der Quote über Gruppen hinweg.
- Album-relativ als Perzentil-Rang nutzen.

### 3.8 Zero-Shot-Labels (`pipeline/zeroshot.py`, T7, D13)
- Text-Embeddings werden **vorab** in der Entwicklung mit dem CLIP-Text-Encoder berechnet (`scripts/make_text_embeddings.py`) und als `fotoduenner/assets/zeroshot_text.npz` mitgeliefert – zur Laufzeit kein Text-Encoder, kein Tokenizer. Prompts nur englisch (A21).
- Je Label mehrere deutsch-/englischsprachige Prompts (Prompt-Ensemble, gemittelt), plus neutrale Gegen-Prompts („ein Urlaubsfoto“, „eine Landschaft“, „ein Foto von Menschen“).
- Score je Label = Softmax über (Label-Prompts + Gegen-Prompts) mit CLIP-Temperatur 100; Label gilt, wenn Score ≥ `ZS_THRESHOLD[label]`.

| Label | Wirkung |
|---|---|
| `person_back` („Person von hinten“, Person ohne sichtbares Gesicht) | zählt als Personenbild (`is_people_photo`), `q_face` = 0,7 (neutral) |
| `screenshot` | Bildwert × `ZS_LOW_VALUE_FACTOR`; nie allein Grund zum Aussortieren; Hinweis in der Begründung |
| `document_sign` (Speisekarte, Schild, Dokument, Fahrplan) | wie `screenshot` |
| `blurry` („unscharfes Foto“) | nur zusätzliches „Bitte prüfen“, nie Aussortieren |

- Jedes Label einzeln über `ZS_ENABLED` abschaltbar. In M3 an Testbildern prüfen; unzuverlässige Labels abschalten und in DECISIONS.md vermerken. **Ergebnis M3 (A24):** `person_back`, `screenshot`, `document_sign` zuverlässig mit neuen Schwellen; `blurry` abgeschaltet.

## 4. Qualitäts-Aussortierung (Bereich „Qualität“)

Ein Bild wird als **technisch schlecht** vorgeschlagen, wenn mindestens eines gilt:
- **Unscharf:** `sharpness_peak < BLUR_ABS` **und** Schärfe-Rang im Album < `BLUR_REL` (0,10). **Solange `BLUR_ABS = None` (unkalibriert):** die untersten `BLUR_REVIEW_REL` (5 %) werden nur als **„Bitte prüfen“** markiert und **nie automatisch aussortiert** (T5).
- **Personenbild unscharf:** wie oben, aber zusätzlich alle Hauptgesichter mit Gesichtsschärfe-Rang < 0,10.
- **Stark fehlbelichtet:** `clip_high > 0,35` oder (`exposure_mean < 20` und `contrast < 15`) oder `exposure_mean > 240`.
- **Fehlauslösung:** sehr geringer Kontrast und kaum Textur (`contrast < 8` und `sharpness_peak` im untersten Album-Perzentil `MISFIRE_SHARP_REL`, 3 %; nach Kalibrierung `< BLUR_ABS / 2`) – z. B. Foto vom Boden/aus der Hosentasche.
- Lightroom-Rating „abgelehnt“.

**Schutzregeln (gehen vor):**
- Rating ≥ 1 → nie automatisch aussortieren.
- Mitglieder einer Belichtungsreihe oder eines Panoramas (§5.0) → nie wegen Belichtung aussortieren (Über-/Unterbelichtung ist dort gewollt).
- Personenbild, das das **einzige** seiner Gruppe ist bzw. dessen Gruppe kein besseres Personenbild enthält → nicht aussortieren, sondern als **„Bitte prüfen“** markieren (bleibt behalten, gelbes Symbol in der UI).
- Bilder im Status `error` → nie verschieben.

Im Modus „Zielquote“ zählen Qualitäts-Aussortierungen zur Quote, werden aber **nicht** erzwungen, um eine Quote zu erreichen.

## 5. Gruppierung (`grouping/`)

### 5.0 Vorstufe – exakte Duplikate und Sequenzen
- **Exakte Duplikate:** gleicher `file_hash` → das erste Bild (nach `rel_path` sortiert) bleibt, alle weiteren werden aussortiert („Exakte Kopie von …“). Gilt in beiden Modi. Duplikate gehen danach nicht mehr in die Gruppierung ein.
- **Sequenzen (`grouping/sequences.py`, Annahme A2):** Aufeinanderfolgende Aufnahmen derselben Kamera mit Abstand ≤ `SEQ_MAX_GAP` (3 s), mindestens `SEQ_MIN_LEN` (3) Bilder:
  - **Belichtungsreihe:** `exposure_bias` unterscheidet sich innerhalb der Folge (mindestens 2 verschiedene Werte, Spanne ≥ 0,7 EV).
  - **Panorama:** Phasenkorrelation auf `img_256` zwischen Nachbarbildern ergibt einen seitlichen (oder senkrechten) Versatz von 15–70 % der Bildbreite, gleiche Richtung über die ganze Folge, Korrelations-Spitze ≥ `PANO_MIN_PEAK`.
  - Erkannte Sequenzen werden **vollständig behalten** und erscheinen in der UI als eigene, geschlossene Gruppe („Belichtungsreihe“ / „Panorama-Serie“). Sie gehen als **ein** Knoten (Mittelwert-Embedding) in die Motiv-Gruppierung ein.

### 5.1 Ebene 1 – Serie (fast identisch)
Verbinde Bilder i, j, wenn
- (pHash-Hamming ≤ 8 **und** |Δt| ≤ 120 s) **oder**
- (Kosinus-Ähnlichkeit ≥ 0,95 **und** |Δt| ≤ 300 s).

Zusammenhangskomponenten (Union-Find) = Serien. Effizient: Bilder nach Zeit sortieren, nur Paare im Zeitfenster vergleichen. Bilder ohne EXIF-Zeit: nur pHash-Kriterium ohne Zeitbedingung, mit Hamming ≤ 5.

### 5.2 Ebene 2 – Motiv (gleiches Motiv, ggf. zeitlich auseinander)
- Knoten = Serien und Sequenzen (Embedding = normalisierter Mittelwert, Zeit = Median).
- Distanz: `d = (1 − cos) + α · min(1, |Δt| / T_max) + β · [anderer Kalendertag]`
  Startwerte: α = 0,06, T_max = 3 h, β = 0,05.
- Hierarchisches Clustering, **Average Linkage** (`scipy.cluster.hierarchy.linkage(..., method="average")` auf kondensierter Distanzmatrix), Schnitt bei Schwelle `θ`.
- Regler „Gruppierungsstärke“ (5 Stufen) ↦ θ ∈ {0,08; 0,11; 0,14; 0,18; 0,22} (Startwerte, in M7 kalibrieren). Standard: Stufe 3.
- **Mega-Gruppen verhindern:** Gruppe mit > 30 Bildern oder Zeitspanne > 1 Tag wird mit θ · 0,7 erneut unterteilt (rekursiv, max. 3×). Hintergrund: Lightroom-Nutzer kritisieren, dass „ähnlich“ dort zu thematisch gruppiert (alle Strandbilder in einen Stapel).
- Linkage-Matrix einmal berechnen und cachen; Reglerwechsel = nur neuer Schnitt (`fcluster`) → sofort.

Ergebnis: Gruppen (≥ 2 Bilder) mit optionalen Serien-Untergruppen; Rest = **Einzelbilder**.

## 6. Bildwert (`selection/scoring.py`)

Alle Teilwerte 0..1 (Ränge album-relativ):

| Teilwert | Definition |
|---|---|
| `q_sharp` | Nicht-Personenbild: Rang(`sharpness_peak`). Personenbild: 0,6 · mittlerer Rang(Gesichtsschärfe der Hauptgesichter) + 0,4 · Rang(`sharpness_peak`); ohne erkanntes Gesicht (nur `person_back`) wie Nicht-Personenbild |
| `q_exp` | siehe 3.3 |
| `q_aes` | Rang(`aesthetic`) |
| `q_face` | Mittel von `eyes_open` der Hauptgesichter (`null` → 0,7); +0,1 wenn alle Hauptgesichter Augen offen > 0,6 (max 1) |

```
Personenbild:     value = 0,35·q_sharp + 0,10·q_exp + 0,15·q_aes + 0,40·q_face
Nicht-Personen:   value = 0,50·q_sharp + 0,20·q_exp + 0,30·q_aes
Zero-Shot screenshot / document_sign:  value ·= ZS_LOW_VALUE_FACTOR (0,8)
```

## 7. Rangfolge innerhalb einer Gruppe (MMR)

Ziel: nicht die 3 besten fast gleichen Bilder behalten, sondern die besten **unterschiedlichen**.

```
ranked = [argmax value]
while Kandidaten übrig:
    für jeden Kandidaten c:
        sim_c  = max cos(c, r) für r in ranked
        mmr_c  = λ · value_c − (1 − λ) · sim_c
    nächstes = argmax mmr_c ;  speichere marginal_c = value_c · (1 − sim_c)^γ
```
Startwerte: λ = 0,65, γ = 1,5. Zusätzlich: Unterscheidet sich ein Kandidat in `face_count_main` von allen bereits gewählten (z. B. andere Personenkonstellation), wird `sim_c` mit 0,8 multipliziert (fördert Vielfalt bei Personen).

Ergebnis je Bild: Position in der Gruppe (`rank`) und `marginal` (Zusatzwert, den das Bild über die besser platzierten hinaus bringt). Die Rangfolge wird für Serien (Ebene 1) und Motivgruppen (Ebene 2) getrennt berechnet.

## 8. Auswahl – zwei Modi (`selection/modes.py`, D5, D12)

N = Anzahl Bilder (ohne `error`). Der Modus wird pro Album gespeichert (A4); beim ersten Lauf ist `mode = "clean"`.

### 8.0 In beiden Modi zuerst: feste Entscheidungen
1. Manuell „behalten“/„aussortieren“ → fest.
2. Rating ≥ 1 → behalten.
3. Exakte Duplikate (§5.0) → aussortieren (außer Rating ≥ 1 oder manuell behalten).
4. Sequenzen (§5.0) → alle Mitglieder behalten.
5. Qualitäts-aussortiert (§4) → aussortieren (außer Schutzregeln); „Bitte prüfen“ → behalten mit gelber Markierung.

### 8.1 Modus „Nur Doppelte & Schlechte“ (`clean`, Standard)
Kein Zielwert. Zusätzlich zu 8.0 nur **Serien** (Ebene 1) ausdünnen:
- Rang 0 jeder Serie bleibt.
- Ein weiteres Serienmitglied bleibt, wenn es noch etwas Eigenes bringt: max. Kosinus zu allen bereits behaltenen Mitgliedern < `CLEAN_DISTINCT_COS` (0,93) **oder** andere Personenkonstellation (`face_count_main` unterscheidet sich von allen behaltenen). Geprüft in MMR-Reihenfolge.
- Alle anderen Serienmitglieder → aussortieren („Fast identisch mit Bild 2 dieser Serie, aber …“).
- Personen-Schutz: In einer Serie mit Personenbildern bleibt mindestens das beste Personenbild.
- **Motivgruppen und Einzelbilder bleiben unangetastet.**

Die UI zeigt das Ergebnis als „1.214 → 1.020 Bilder (−16 %)“. Reicht das dem Nutzer nicht, schaltet er auf „Zielquote“ um.

### 8.2 Modus „Zielquote“ (`quota`)
Eingabe: Zielquote `p` (Anteil behalten, Regler 30–90 %, Standard 50 %).

1. **Feste Entscheidungen:** wie 8.0. Bilder mit manueller Entscheidung heißen „manuell“, alle übrigen „automatisch“; `N_auto` = Anzahl automatischer Bilder.
2. **Pflicht-Behalten** (nur automatische Bilder):
   - Jedes Einzelbild (sofern nicht qualitäts-aussortiert).
   - Rang 0 jeder Gruppe.
   - Gruppen mit Personenbildern: zusätzlich Rang 1, wenn Gruppe ≥ 3 Bilder hat und Rang 1 ein Personenbild ist.
3. **Budget (T1):** `K = round(p · N_auto)` − Anzahl der bereits behaltenen **automatischen** Bilder. Manuelle Entscheidungen zählen nicht zum Budget – dadurch ändert ein manueller Klick nie die Entscheidung eines anderen Bildes.
4. **Auffüllen:** Alle übrigen automatischen Kandidaten nach Priorität sortieren:
   `priority = marginal · (1 + 0,5 · is_people_photo) · (0,85 + 0,3 · q_aes)` und die obersten `K` behalten, Rest aussortieren.
5. **Ziel nicht erreichbar:**
   - Pflicht-Behalten > Ziel: Hinweis in der UI („Mit den Schutzregeln sind höchstens 38 % Reduktion möglich.“). Option **„Auch Einzelbilder mit geringem Wert aussortieren“** (Standard: aus) – dann Einzelbilder ohne Personen mit `value` < 0,25 und Ästhetik-Rang < 0,2 als Kandidaten zulassen.
   - Weniger Kandidaten als Budget: einfach alle behalten.
6. Neuberechnung bei jeder Regleränderung, jedem Moduswechsel und jedem manuellen Klick (< 300 ms bei 2.000 Bildern). Die angezeigte Gesamtquote ergibt sich aus automatischen und manuellen Entscheidungen. Ein Knopf „Quote neu anwenden“ entfällt (A1).

### 8.3 Bereits übernommene Alben (T2)
Nach „Übernehmen“ speichert das Album `applied = 1` und die Ausgangsanzahl. Beim erneuten Öffnen wird **nicht** automatisch erneut ausgedünnt: alle verbliebenen Bilder gelten als behalten, bis der Nutzer bewusst „Erneut ausdünnen“ wählt. Die Quote bezieht sich dann auf den aktuellen Bestand.

## 9. Begründungen (`selection/reasons.py`)

Jedes aussortierte und jedes „Bitte prüfen“-Bild bekommt eine kurze deutsche Begründung, abgeleitet aus dem dominierenden Faktor:

| Situation | Text (Beispiel) |
|---|---|
| Exakte Kopie | „Exakte Kopie von IMG_0412.jpg.“ |
| Serie, schlechter als Rang 0 | „Fast identisch mit Bild 2 dieser Serie, aber unschärfer.“ |
| Serie, Augen | „Fast identisch mit Bild 2, aber Augen geschlossen.“ |
| Motivgruppe, Quote | „Ähnliches Motiv ist bereits 3× behalten.“ |
| Zero-Shot-Hinweis | „Ähnliches Motiv ist bereits 2× behalten (vermutlich Schild/Speisekarte).“ |
| Qualität unscharf | „Deutlich unscharf (schärfer als nur 4 % des Albums).“ |
| Qualität Belichtung | „Stark überbelichtet (38 % der Fläche weiß).“ |
| Fehlauslösung | „Vermutlich versehentlich ausgelöst (kaum Bildinhalt).“ |
| Bitte prüfen (unscharf, unkalibriert) | „Wirkt unscharf – bitte selbst prüfen.“ |
| Bitte prüfen (Person) | „Einziges Bild dieser Personen hier, aber unscharf – bitte selbst entscheiden.“ |

Bezugsbilder werden mit ihrer Position in der Gruppe angegeben und in der UI beim Überfahren hervorgehoben.

## 10. Parameterübersicht (Defaults für `config.py`)

```python
ALGO_VERSION = 1
DECODE_TARGET = 1600; DECODE_MIN_LONG_EDGE = 1500
FACE_SCORE_THRESHOLD = 0.6; FACE_DETECT_EDGES = (480, 640); FACE_MIN_SIZE_REL = 0.015  # A23
EYES_OPEN_THRESHOLD = 0.6  # A23
PEOPLE_MIN_SIZE_REL = 0.035; PEOPLE_MIN_CONF = 0.85; EYES_MIN_SIZE_REL = 0.04; EYES_MIN_PRESENCE = 0.5
PHASH_SERIES_MAX = 8; SERIES_PHASH_DT = 120; SERIES_COS_MIN = 0.95; SERIES_COS_DT = 300
SEQ_MAX_GAP = 3.0; SEQ_MIN_LEN = 3; BRACKET_MIN_EV_SPAN = 0.7
PANO_SHIFT_MIN = 0.15; PANO_SHIFT_MAX = 0.70; PANO_MIN_PEAK = 0.1
MOTIF_ALPHA = 0.06; MOTIF_TMAX = 3*3600; MOTIF_DAY_PENALTY = 0.05
MOTIF_THRESHOLDS = (0.08, 0.11, 0.14, 0.18, 0.22); MOTIF_DEFAULT_LEVEL = 2  # Index
GROUP_MAX_SIZE = 30; GROUP_MAX_SPAN = 24*3600
MMR_LAMBDA = 0.65; MMR_GAMMA = 1.5; FACECOUNT_DIVERSITY = 0.8
PEOPLE_PRIORITY_BOOST = 0.5
CLEAN_DISTINCT_COS = 0.93
BLUR_ABS = None   # in M7 kalibrieren; bis dahin nur "Bitte prüfen" (T5)
BLUR_REL = 0.10; BLUR_REVIEW_REL = 0.05; MISFIRE_SHARP_REL = 0.03
OVEREXP_CLIP_HIGH = 0.35; UNDEREXP_MEAN = 20; UNDEREXP_CONTRAST = 15
ZS_ENABLED = {"person_back": True, "screenshot": True, "document_sign": True, "blurry": False}  # A24
ZS_THRESHOLD = {"person_back": 0.70, "screenshot": 0.50, "document_sign": 0.65, "blurry": 0.6}  # A24
ZS_LOW_VALUE_FACTOR = 0.8
DEFAULT_MODE = "clean"; TARGET_KEEP_DEFAULT = 0.50; TARGET_KEEP_RANGE = (0.30, 0.90)
```
