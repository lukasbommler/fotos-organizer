# DECISIONS – Entscheidungen und Abweichungen vom Plan

Einträge hier **gehen den Dokumenten 01–05 vor**, wo sie ihnen widersprechen.

## Nutzer-Entscheidungen (2026-11-20)

| # | Thema | Entscheidung |
|---|---|---|
| D1 | Nutzer | Entwickler selbst + Freunde. Verteilung als ZIP mit `.exe` (öffentliche GitHub-Releases). |
| D2 | Formate | **Nur JPG/JPEG** in v1. Kein RAW (kein `rawpy`, keine RAW+JPG-Paare). HEIC/PNG höchstens später. Sidecar `.xmp` mit gleichem Namen wird weiterhin mitverschoben. |
| D3 | Plattform | Nur Windows 10/11. |
| D4 | Fenster | **Eigenes Fenster (pywebview / Edge WebView2) ab v1**, kein Browser-Tab. Ordnerdialog über pywebview. Entfällt: Heartbeat/„Browser geschlossen“-Logik, Konsolenfenster-Hinweis. Lokaler Server + Token bleiben. |
| D5 | Reduktions-Modus | Zwei Modi: **„Nur Doppelte & Schlechte“ (Standard beim ersten Lauf)** – kein Zielwert, nur Serien-Duplikate und technisch schlechte Bilder raus. Reicht das nicht: Umschalten auf **„Zielquote“** mit Regler (30–90 %). |
| D6 | Bildbewertung | Augen-offen-Erkennung in v1. Zusätzlich alle sinnvollen Hilfsmittel (siehe T5–T8). MediaPipe vs. reines ONNX-Landmarkenmodell wird in einem M0-Spike entschieden (Paketkonflikt `opencv-contrib-python` ↔ `opencv-python-headless`). |
| D7 | Speicherort Alben | Lokale Ordner (z. B. Desktop). Kein Cloud-Ordner erwartet; „nur online“-Warnung bleibt als einfache Prüfung. |
| D8 | Repo | Öffentlich, GitHub-Account **`lukasbommler`**. Commits mit der GitHub-noreply-Adresse dieses Accounts. Keine echten Fotos ins Repo (nur synthetische/CC0-Testbilder). |
| D9 | Design | Dunkel, futuristisch-elegant („KI-Tool“), dezente Animationen, trotzdem einfach. **Alles auf einen Blick:** Gruppen als kompakte **Bündel** (gestapelte Mini-Vorschauen, aufklappbar). Kein Hell-Modus. Animationen dürfen das Scrollen mit 2.000 Bildern nicht bremsen (nur `transform`/`opacity`). |
| D10 | Kalibrierung | Später (nach Auswahl-Logik): In-App-Modus „Bewertung erfassen“, 300–500 zusammenhängende Bilder, plus Prüfung von ~30 Gruppen. **Nicht** über Sterne im Explorer (würde Originale ändern und kollidiert mit dem Sterne-Schutz). |
| D11 | Projektort | Projekt liegt in Google Drive. Python-Umgebung **außerhalb** von Drive: `UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\uv-envs\fotoduenner`. |

## Korrekturen aus dem Plan-Review

| # | Thema | Entscheidung |
|---|---|---|
| T1 | Quote vs. manuelle Entscheidungen (03 §8) | Budget nur über nicht-manuelle Bilder: `K = round(p · N_auto)`. Manuelle Klicks ändern so nie andere Bilder. |
| T2 | Erneutes Öffnen nach „Übernehmen“ | Album speichert Status „übernommen“ + Ausgangsanzahl; es wird nicht automatisch erneut um p % ausgedünnt. |
| T3 | Verschieben | `os.rename` (schlägt unter Windows fehl, wenn Ziel existiert → atomar, nie überschreiben) statt prüfen + `os.replace`. |
| T4 | Journal-Kopie | Zusätzlich `_Aussortiert\_FotoDuenner_Protokoll.json`, damit Rückgängig auch ohne `%LOCALAPPDATA%`-Cache möglich ist. |
| T5 | Unschärfe ohne Kalibrierung | Solange `BLUR_ABS = None`: unterste 5 % nur als **„Bitte prüfen“**, nie automatisch aussortiert. |
| T6 | Schärfe in Serien | Für Serienmitglieder mit Gesichtern: zweiter Durchgang mit voller Auflösung, Gesichtsschärfe auf nativen Ausschnitten. |
| T7 | Zusatz-Signale | Exakte Duplikate (Datei-Hash); Belichtungsreihen (EXIF `ExposureBiasValue`) und Panorama-Sequenzen erkennen → ganze Sequenz behalten; CLIP-Zero-Shot mit vorab berechneten Text-Embeddings („Person von hinten“, „Screenshot“, „unscharfes Foto“, „Speisekarte/Schild“). |
| T8 | Gruppierungs-Embedding | CLIP vs. DINOv2-small schon in M3 vergleichen (CLIP gruppiert eher thematisch). |
| T9 | Dekodierung | Skalierung passend zu JPEG-`draft` (1/2, 1/4, 1/8) wählen; lange Kante ≥ ~1500 px genügt. |
| T10 | Vorschauen | JPEG statt WebP (deutlich schnellere Kodierung). |
| T11 | Pipeline | Zuerst einfache Variante benchmarken: gesamte Analyse pro Worker, ONNX mit 1 Thread. Getrennte CLIP-Stufe nur, wenn messbar schneller. |
| T12 | Sicherheit | Cookie `SameSite=Strict`, eigener Header bei POST. `psutil` als Abhängigkeit ergänzen. |
| T13 | Cache-Schlüssel | Merkmal-Cache über Datei-Fingerabdruck (Name + Größe + mtime + EXIF-Zeit), nicht nur Album-Pfad. |
| T14 | Umfang v1 | Später: synchroner Zoom in Vergleichsansicht, Taste F, Tastenkombis `G`+Taste. |

## Nutzer-Entscheidungen (2026-11-20, Fortsetzung)

| # | Thema | Entscheidung |
|---|---|---|
| D12 | Modus „Nur Doppelte & Schlechte“ (`mode = "clean"`) | Aussortiert werden nur: (a) **exakte Duplikate** (gleicher Datei-Hash, das erste nach Pfad bleibt), (b) innerhalb jeder **Serie** (Ebene 1) alle Bilder außer Rang 0 – ein weiteres Mitglied bleibt, wenn es noch etwas Eigenes bringt: max. Kosinus zu allen behaltenen < `CLEAN_DISTINCT_COS` (0,93) **oder** andere Personenkonstellation (`face_count_main`), (c) **technisch schlechte** Bilder nach 03 §4 (mit T5: Unschärfe ohne Kalibrierung nur „Bitte prüfen“). Motivgruppen (Ebene 2) bleiben unangetastet, Belichtungsreihen/Panoramen nie ausgedünnt, alle Schutzregeln gelten. |
| D13 | CLIP-Zero-Shot-Labels (T7) – Wirkung | # ASSUMPTION (Nutzer hat keine Präferenz): „Person von hinten“ → zählt als Personenbild (Schutz + Prioritäts-Bonus, `q_face` neutral 0,7). „Screenshot“, „Speisekarte/Schild/Dokument“ → Bildwert × `ZS_LOW_VALUE_FACTOR` (0,8), **nie allein Grund zum Aussortieren**, Hinweis in der Begründung. „Unscharfes Foto“ → nur zusätzliches „Bitte prüfen“, nie Aussortieren. Jedes Label einzeln in `config.py` abschaltbar; in M3 an Testbildern prüfen, unzuverlässige Labels abschalten. |
| D14 | Lizenz | Projekt unter **MIT-Lizenz** (`LICENSE` im Repo). Lizenzen aller Modelle in `models/MODELS.md`; Lizenztexte aller mitgelieferten Pakete und Modelle als `THIRD_PARTY_LICENSES.txt` im Release-ZIP (beim Build erzeugt). |
| D15 | Dokumente | Die Dokumente 01–05 und README wurden am 2026-11-20 an D1–D14 und T1–T14 angepasst. Diese Datei bleibt das Entscheidungsprotokoll und geht weiterhin vor. `docs/CLAUDE.md` (veraltete Kopie) entfernt – maßgeblich ist `CLAUDE.md` im Projektwurzelverzeichnis. |
| D16 | Design-Richtung (präzisiert D9, 2026-11-21) | **Kein generischer „KI-Tool“-Look**, sondern ein futuristisches, professionelles **Präzisionsinstrument am Leuchttisch**. Verboten: Lila/Blau-Verläufe, Glow-Blobs, Verlaufs-Text, Glas-Effekte (Glassmorphism), Funkel-/Sparkle-Symbole ✨, Pillen-Knöpfe, Chat-Optik, das Wort „KI“ als Verkaufsargument. Stattdessen: Graphit-Töne, **eine** Signalfarbe (Dunkelkammer-Amber) nur für Fokus/Aktionen, harte Kanten (Radius ≤ 2 px), Haarlinien-Raster, Schnittmarken-Ecken, Messwerte als feine Instrumenten-Skalen, Metadaten in Monospace mit Tabellenziffern, Bildnummern wie auf dem Filmstreifen („024“). Entscheidungen als **Fettstift-Markierungen wie auf einem Kontaktbogen**: behalten = eingekreist, aussortiert = durchgestrichen. Bedienung bleibt selbsterklärend: klare deutsche Beschriftungen, große Klickflächen, keine versteckten Gesten. Schriften: offline mitgeliefert, Lizenz OFL (z. B. IBM Plex Sans + IBM Plex Mono), Auswahl in M2. |

## Annahmen (vom Nutzer nicht beanstandet)

| # | Thema | Annahme |
|---|---|---|
| A1 | „Quote neu anwenden“ | Entfällt. Durch T1 verschieben manuelle Klicks das Budget nicht mehr, der Knopf hätte keine Funktion. |
| A2 | Belichtungsreihen / Panoramen (T7) | Erkennung: gleiche Kamera, aufeinanderfolgende Aufnahmen mit Abstand ≤ `SEQ_MAX_GAP` (3 s), mindestens 3 Bilder. **Belichtungsreihe:** `ExposureBiasValue` unterscheidet sich innerhalb der Folge. **Panorama:** gleichmäßige seitliche Verschiebung zwischen Nachbarbildern (Phasenkorrelation auf 256-px-Graustufen, Versatz 15–70 % der Bildbreite, gleiche Richtung). Erkannte Sequenzen bleiben vollständig erhalten. |
| A3 | Fenster-Verhalten (D4) | Fenster schließen = Programm beenden (bei laufender Analyse/Übernahme mit Rückfrage). Zweiter Start holt das vorhandene Fenster nach vorn. Fehlt WebView2 (alte Windows-10-Installation): Meldung mit Link zum Microsoft-Installer. |
| A4 | Modus speichern | Modus (`clean`/`quota`) und Zielquote werden pro Album in `settings` gespeichert. |
| A5 | Bündel-Design (D9) | Mockup der Bündel-Ansicht wird in M2 erstellt und vor M5 mit dem Nutzer abgestimmt. |
| A6 | Lange Pfade | `LongPathsEnabled` ist auf typischen Rechnern 0 (auch auf dem Entwicklungsrechner). Daher wird für Dateioperationen das `\?\`-Präfix in `paths.py` verwendet, nicht die Registry-Einstellung vorausgesetzt. |
| A7 | Benchmark | Entwicklungsrechner hat 16 Threads. `scripts/benchmark.py` begrenzt Worker standardmäßig auf 3 (≈ 4-Kern-Laptop) und meldet zusätzlich die ungebremste Zahl. |
| A8 | Entwicklungs-Artefakte | Wegen D11 (Projekt in Google Drive): `build/`, `dist/` und heruntergeladene Modelle liegen außerhalb von Drive (`%LOCALAPPDATA%\FotoDuenner-dev\`) oder sind per `.gitignore` ausgeschlossen. Modelle > 100 MB kommen nie ins Git; sie werden per `scripts/fetch_models.py` geladen und nur im Release-ZIP mitgeliefert. |
