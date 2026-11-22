# Modelle

Alle Modelle werden **nicht** im Git gespeichert. Entwicklung: `uv run python scripts/fetch_models.py [--dev]` lädt sie nach `%LOCALAPPDATA%\FotoDuenner-dev\models\` (außerhalb von Google Drive, DECISIONS A8) und prüft SHA256. Die App lädt zur Laufzeit nichts herunter; im Release-ZIP liegen die Laufzeit-Modelle unter `_internal\models\`. Alle Modelle werden **aus Bytes** geladen (Umlaut-Pfade).

Geprüft am 2026-11-21.

## Laufzeit-Modelle (im Paket)

| Datei | Zweck | Quelle (gepinnte Revision) | Lizenz | SHA256 |
|---|---|---|---|---|
| `face_detection_yunet_2023mar.onnx` | Gesichtsdetektion (03 §3.5) | https://huggingface.co/opencv/face_detection_yunet @ `3cc26e7f` (Spiegel von opencv_zoo) | MIT (Shiqi Yu) | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| `clip_vit_b32_vision_fp16.onnx` | Bild-Embedding (03 §3.6, A25) | https://huggingface.co/Xenova/clip-vit-base-patch32 @ `6b7c5237`, `onnx/vision_model_fp16.onnx` | MIT (OpenAI CLIP; Xenova-Konvertierung MIT) | `35c4e0fb0aeee527dcde1693520b214a34424a786babd530f35366bad5844efd` |
| `aesthetic_vit_b32_linear.npz` | Ästhetik-Kopf (03 §3.7) | abgeleitet aus `sa_0_4_vit_b_32_linear.pth`, https://github.com/LAION-AI/aesthetic-predictor @ `6d122ada`; Konvertierung ohne PyTorch in `fetch_models.py` | MIT (LAION) | Quelle `.pth`: `c7b14cead230694acc7b9447974d3cad78003c72da032e402a303b6c2429e85f` |
| `face_landmarks_detector.onnx` | 478 Gesichtspunkte (Augen offen, D17) | MediaPipe Face Mesh V2 aus `face_landmarker.task` (float16, v1), mit `scripts/convert_face_landmarker.py` (tf2onnx 1.17.0, opset 17) nach ONNX | Apache-2.0 (Model Card „MediaPipe Face Mesh V2“) | Entwicklungsstand: `c8cde4e8a31b8badf59fd4774c88fe435d7c598bba9a8cd1590c3d4e43ec96aa` ¹ |
| `face_blendshapes.onnx` | 52 Blendshapes inkl. `eyeBlinkLeft/Right` (D17) | MediaPipe Blendshape V2 aus `face_landmarker.task`, konvertiert wie oben | Apache-2.0 (Model Card „Blendshape V2“) | Entwicklungsstand: `0e5982d37de6263738059d94eb0c2a9b2f9e05e065e797e1ec48025a84bf392b` ¹ |

| `fotoduenner/assets/zeroshot_text.npz` (im Git, 32 KB) | Zero-Shot-Text-Embeddings (03 §3.8, D13): je Label ein gemittelter Prompt-Vektor + 9 neutrale Gegen-Prompts | erzeugt mit `scripts/make_text_embeddings.py` aus `clip_vit_b32_text_fp32.onnx` + `clip_tokenizer.json` (unten) | MIT (abgeleitet aus OpenAI CLIP) | wird bei jeder Prompt-Änderung neu erzeugt; Prompts stehen im Skript und in der Datei (`prompts`) |

¹ tf2onnx schreibt laufabhängige Metadaten; erneutes Konvertieren ergibt andere Bytes, aber identische Ausgaben (am Referenzbild auf 3 Nachkommastellen geprüft). Maßgeblich für ein Release ist die Datei, die gebaut wurde – ihr SHA256 wird in `THIRD_PARTY_LICENSES.txt` vermerkt.

## Nur Entwicklung (`--dev`, nie im Paket)

| Datei | Zweck | Quelle | Lizenz | SHA256 |
|---|---|---|---|---|
| `clip_vit_b32_vision_fp32.onnx` | INT8-vs-FP32-Vergleich (M3) | Xenova @ `6b7c5237`, `onnx/vision_model.onnx` | MIT | `fd6e1402a588279d1723c7534d4bcba5bc0b14b47dfab0e46f8c47b8270d7d40` |
| `clip_vit_b32_vision_int8.onnx` | ursprünglich geplante Laufzeit-Variante, in M3 verworfen (Kosinus 0,924 zu FP32, A25) | Xenova @ `6b7c5237`, `onnx/vision_model_quantized.onnx` | MIT | `583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299` |
| `clip_vit_b32_vision_uint8.onnx` | beste fertige 8-Bit-Variante im Vergleich (0,975, A25) | Xenova @ `6b7c5237`, `onnx/vision_model_uint8.onnx` | MIT | `b95448754a6ae56964ec80c570c80bb9863787ee85f51b664abed8c0e5f22a7a` |
| `clip_vit_b32_text_fp32.onnx` | Zero-Shot-Text-Embeddings (03 §3.8) | Xenova @ `6b7c5237`, `onnx/text_model.onnx` | MIT | `3f6571f5bad13a97c469c1622e1cfc4d9aef78b79fdbfcff804ca357bfada8cc` |
| `clip_tokenizer.json` | Tokenizer für den Text-Encoder | Xenova @ `6b7c5237`, `tokenizer.json` | MIT | `f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd` |
| `dinov2_small_int8.onnx` | Vergleich Gruppierungs-Embedding (T8, M3) | https://huggingface.co/Xenova/dinov2-small @ `c2bb04a5`, `onnx/model_quantized.onnx` (Konvertierung von `facebook/dinov2-small`) | Apache-2.0 (Model Card `facebook/dinov2-small`) | `3afdc8bc63b50558d6e5770f5b799bb82455c2311183a2de43803f343a29d917` |
| `dinov2_small_fp32.onnx` | dito, FP32 | dito, `onnx/model.onnx` | Apache-2.0 | `83141175ec78b4ff9a2bb58a4c7c264ba0054d1c2e122e5a8114b79a8d4179ea` |
| `face_landmarker.task` | Quelle für die beiden MediaPipe-ONNX-Dateien | https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task | Apache-2.0 | `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff` |

## Ein-/Ausgaben und Vorverarbeitung (verifiziert mit ONNX Runtime 1.32 / OpenCV 5.1)

**YuNet** – über `cv2.FaceDetectorYN.create("onnx", bytes, empty, (w, h), score, nms, top_k)`; Eingabe BGR uint8 beliebiger Größe (`setInputSize`), Ausgabe je Gesicht `[x, y, w, h, 5 × (x, y) Landmarken (rechtes Auge, linkes Auge, Nase, Mundwinkel r/l), score]`.

**CLIP ViT-B/32 (Bild)** – gilt für FP16, FP32 und INT8 gleich (FP16: Gewichte FP16, Ein-/Ausgaben float32, gerechnet in FP32). Eingabe `pixel_values` float32 `[N, 3, 224, 224]` (NCHW). Vorverarbeitung: kürzere Seite auf 224 (bikubisch), mittig zuschneiden, RGB / 255, mean `(0.48145466, 0.4578275, 0.40821073)`, std `(0.26862954, 0.26130258, 0.27577711)`. Ausgabe `image_embeds` float32 `[N, 512]` (projiziert, **nicht** normalisiert → L2-normalisieren).

**CLIP ViT-B/32 (Text, nur Entwicklung)** – Eingabe `input_ids` int64 `[N, L]` (kein `attention_mask`), Ausgabe `text_embeds` float32 `[N, 512]`. Tokenizer (`tokenizers`-Paket, nur im Skript) setzt `<|startoftext|>` 49406 / `<|endoftext|>` 49407 selbst; Prompts werden einzeln ohne Padding kodiert.

**DINOv2-small (nur Entwicklung, T8)** – Eingabe `pixel_values` float32 `[N, 3, 224, 224]`; Vorverarbeitung: kürzere Seite auf 256 (bikubisch), mittig 224 zuschneiden, RGB / 255, ImageNet-mean `(0.485, 0.456, 0.406)`, std `(0.229, 0.224, 0.225)`. Ausgabe `last_hidden_state` `[N, 257, 384]`; Embedding = CLS-Token (Index 0, nach der letzten LayerNorm), L2-normalisiert. CPU, 1 Thread: ≈ 140 ms je Bild (CLIP INT8: ≈ 90 ms).

**Ästhetik-Kopf** – `score = embedding_l2norm · weight + bias`, `weight` float32 `[512]`, `bias` float32 `[1]` (≈ 3,42). Ausgabe ≈ 1–10.

**Face Mesh V2 (ONNX)** – Eingabe `input_12` float32 `[1, 256, 256, 3]` (NHWC, RGB, Werte 0..1). Ausschnitt: um die Augenlinie gedrehtes Quadrat, Seitenlänge 1,5 × Gesichtsgröße (MediaPipe: `scale 1.5`, `square_long`, Rotation über Punkte 33 → 263). Ausgaben: `Identity` `[1, 1, 1, 1434]` = 478 × (x, y, z) in Pixeln des 256er-Ausschnitts; `Identity_1` `[1, 1, 1, 1]` Präsenz-Logit (Sigmoid); `Identity_2` unbenutzt.

**Blendshape V2 (ONNX)** – Eingabe `serving_default_input_points:0` float32 `[1, 146, 2]` = (x, y) in **Bildpixeln** der 146 Punkte aus MediaPipes `kLandmarksSubsetIdxs` (`face_blendshapes_graph.cc`, inkl. Iris-Punkte 468–477). Ausgabe `[52]` Blendshapes in MediaPipe-Reihenfolge; Index 9 = `eyeBlinkLeft`, 10 = `eyeBlinkRight`.
