# 04 – Benutzeroberfläche

HTML-Oberfläche in einem **eigenen Programmfenster** (pywebview / Edge WebView2, D4), technisch geladen von `http://127.0.0.1:<port>`. Sprache Deutsch. Zielauflösung ab 1366 × 768 (typischer Office-Laptop), skaliert bis 4K. Bedienung vollständig mit Maus **und** Tastatur.

## 0. Gestaltung (D9, D16, **geändert durch D21**: „Reaktor“, grüne/rote Rahmen)

- **Nur dunkles Design „Reaktor“ (D21):** eisblaue Instrumenten-Optik auf fast schwarzem Grund, abgeschrägte Ecken, technische Eckmarken, Metadaten in Monospace mit Tabellenziffern. Keine Lila-Verläufe, keine Funkel-Symbole, keine Pillen-Knöpfe. Kein Hell-Modus.
- **Farbe:** eine Signalfarbe (Eisblau) für Fokus, aktive Elemente und Hauptaktion. **Grün = behalten, Rot = aussortieren, Gelb = „Bitte prüfen“**; zusätzlich ✓/✕/! als Zeichen, damit Farbe nicht das einzige Merkmal ist.
- **Nichts liegt über dem Bild:** Die Entscheidung zeigt der Rahmen.
- **Animierte Bühne** (Start, Vorprüfung, Analyse): nur echte Werte und echte Vorschauen; sparsam (höchstens 30 Bilder/s, Pause bei verdecktem Fenster).
- Dezente Animationen (Bündel aufklappen, Statuswechsel, Fortschritt). **Nur `transform` und `opacity` animieren**, damit das Scrollen mit 2.000 Bildern flüssig bleibt; `prefers-reduced-motion` respektieren.
- **Alles auf einen Blick:** Gruppen erscheinen als kompakte **Bündel** – gestapelte Mini-Vorschauen (wie ein kleiner Fotostapel), die sich per Klick aufklappen.
- Ein Mockup der Bündel-Ansicht wird in M2 erstellt und vor M5 abgestimmt (A5).

## 1. Bildschirme

### 1.1 Start
- Titel, Kurzerklärung (2 Sätze: „Analysiert, schlägt vor, nichts wird gelöscht.“).
- Großer Knopf **„Ordner wählen …“** (nativer Windows-Dialog über pywebview).
- Liste **„Zuletzt geöffnet“** mit Ordnername, Pfad, Anzahl Bilder, Stand (z. B. „analysiert, 1.214 → 1.020 Bilder, noch nicht übernommen“ oder „übernommen am 12.08., 1.214 → 618“).
- Häkchen „Unterordner einbeziehen“.
- Hinweisbox beim ersten Start: „Tipp: Testen Sie FotoDünner zuerst mit einer Kopie eines Urlaubsordners.“

### 1.2 Vorprüfung (nach Ordnerwahl, ~1–3 s)
- Gefunden: X JPEG-Bilder, W ignorierte Dateien (andere Formate, Videos usw.), Gesamtgröße.
- Warnungen: „nur online“-Dateien, Laufwerk extern (langsamer), sehr wenige Bilder (< 20).
- Geschätzte Dauer.
- Knopf **„Analyse starten“**.

### 1.3 Analyse läuft
- Fortschrittsbalken mit Stufe („Bilder einlesen und prüfen“, „Gesichter genauer prüfen“, „Gruppen bilden“), Zähler, Restzeit.
- Fertige Vorschauen fliegen in den Ring (D21; Aktualisierung max. 2×/s); daneben Restzeit, Laufzeit, Tempo, aktuelles Bild.
- Knopf **„Abbrechen“** (Fortsetzen später möglich).

### 1.4 Prüf-Übersicht (Hauptbildschirm)

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ FotoDünner · Kroatien 2025                          1.214 Bilder → 1.020 (−16 %)     │
│ Modus: (● Nur Doppelte & Schlechte) (○ Zielquote)    Gruppierung: ○○●○○             │
│ [ Gruppen (184) ] [ Qualität (47) ] [ Einzelbilder (231) ] [ Alle ]   Filter: ☐ nur 👤 │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  ╔═══╗   ╔═══╗   ╔═══╗   ╔═══╗   ╔═══╗   ╔═══╗   ╔═══╗                               │
│  ║▓▓▓║╗  ║▓▓▓║╗  ║▓▓▓║   ║▓▓▓║╗  ║▓▓▓║╗  ║▓▓▓║╗  ║▓▓▓║╗     ← Bündel (gestapelt)      │
│  ╚═══╝╝  ╚═══╝╝  ╚═══╝   ╚═══╝╝  ╚═══╝╝  ╚═══╝╝  ╚═══╝╝                              │
│  12 · 9  13 · 4  14 · 2  15 · 6  ...                ← Gruppe · Anzahl                │
│  3 ✓ 👤  1 ✓ ⛰   1 ✓     2 ✓ 👤                       ← behalten, Symbole             │
│                                                                                      │
│  ▼ Gruppe 12 aufgeklappt · 9 Bilder · 3 behalten · 14:02–14:09 · 👤                  │
│  ┌────┐┌────┐┌────┐ ┌────┐┌────┐ ┌────┐┌────┐┌────┐┌────┐                            │
│  │ ✓  ││ ✗  ││ ✗  │ │ ✓  ││ ✗  │ │ ✓  ││ ✗  ││ ✗  ││ ✗  │   (Serien durch Abstand)   │
│  └────┘└────┘└────┘ └────┘└────┘ └────┘└────┘└────┘└────┘                            │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ Aussortiert: 194 Bilder · 2,9 GB          [ Rückgängig ]   [ Übernehmen … ]          │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**Kopfzeile:** Albumname, Live-Zähler „N → K Bilder (−x %)“, **Modus-Umschalter** „Nur Doppelte & Schlechte“ / „Zielquote“. Nur im Modus Zielquote erscheint der Regler „Ziel behalten“ (30–90 %, 5er-Schritte, Standard 50 %). Gruppierungsstärke (5 Stufen, Beschriftung „fein … grob“) – im Modus „Nur Doppelte & Schlechte“ ausgegraut, da Motivgruppen dort nicht ausgedünnt werden. Bei bereits übernommenen Alben zusätzlich Knopf „Erneut ausdünnen“ (T2).

**Reiter:**
- **Gruppen** – nur Gruppen mit mindestens einem aussortierten Bild, als Bündel, sortiert nach Aufnahmezeit. Gruppen ohne Änderung unter „Alle“.
- **Qualität** – Raster aller qualitäts-aussortierten Bilder + „Bitte prüfen“-Bilder (gelb, oben).
- **Einzelbilder** – alle behaltenen Einzelbilder (nur zur Kontrolle, standardmäßig dezenter).
- **Alle** – zeitliche Gesamtansicht aller Bilder mit Status.
- Filter: „nur mit Personen“, „nur geänderte“, „nur manuell geändert“.

**Bündel (zugeklappt):** gestapelte Mini-Vorschauen (oberstes Bild = Rang 0), darunter Gruppennummer, Anzahl, Anzahl behalten, Symbole (👤 Personen, ⚠ enthält „Bitte prüfen“, ✋ manuell geändert). Sonderbündel „Belichtungsreihe“ / „Panorama-Serie“ / „Exakte Kopien“ mit eigenem Symbol. Klick klappt das Bündel auf (Animation über `transform`), ein zweiter Klick oder Esc klappt es zu. Höchstens ein Bündel ist offen; „Stapel bestätigen“ (B) markiert es als erledigt und öffnet das nächste (A35).

**Bündel (aufgeklappt):** Kopf mit Nummer, Anzahl, behalten, Zeitspanne, Symbole; Serien innerhalb der Gruppe durch kleinen Abstand sichtbar. Bei großen Gruppen erste Zeile + „weitere 14 anzeigen“.

**Kachel (≈ 160 px, mit Strg + Mausrad / Knopf in 3 Größen: 120/160/240 px):**
- Behalten: grüner Rahmen, ✓ neben der Bildnummer (D21).
- Aussortiert: roter Rahmen, ✕, leicht abgedunkelt (Deckkraft 60 %), Begründung als Tooltip und unter der Kachel in 1 Zeile (abgeschnitten).
- „Bitte prüfen“: Amber-Randmarke + Ausrufezeichen.
- Manuell geändert: kleines Hand-Symbol.
- Geschützt (Sterne): ★.
- Kleine Symbole unten links: 👤 + Anzahl Gesichter, Augen geschlossen 😑, Schärfe-Balken (3 Stufen).
- Beim Überfahren eines aussortierten Bildes wird das Bezugsbild der Begründung (z. B. „Bild 2“) hervorgehoben.

## 2. Interaktionen

| Aktion | Maus | Tastatur |
|---|---|---|
| Entscheidung umdrehen | Klick auf Kachel | Leertaste |
| Behalten / aussortieren erzwingen | Menü-Taste | K / X |
| XL-Ansicht neben dem Stapel (A35) | Rechtsklick; schließt mit Rechtsklick oder Klick auf ein Bild | Esc schließt |
| Stapel bestätigen (A35) | Knopf „Stapel bestätigen“ | B |
| „Bitte prüfen“ bestätigen (A34) | Rechtsklick-Menü „Geprüft – behalten“ | K |
| Bündel auf-/zuklappen | Klick auf Bündel | Enter / Esc |
| Großansicht | Doppelklick | Enter (auf Kachel) |
| Navigation | – | Pfeiltasten, Bild↑/Bild↓ = nächste Gruppe |
| Ganze Gruppe: alle behalten / Vorschlag zurücksetzen | Gruppen-Menü ▾ | – (Tastenkombi `G` + Taste später, T14) |
| Vergleich | Strg + Klick auf 2–4 Kacheln → „Vergleichen“ | C |
| Rückgängig letzte Klick-Aktion | – | Strg + Z |

**Großansicht (Overlay):**
- 1600-px-Vorschau, Pfeile links/rechts innerhalb der Gruppe, darunter Filmstreifen der Gruppe.
- Taste Z oder Klick: **100 %-Zoom** an der Klickposition (lädt Original über `GET /api/album/{id}/photo/{pid}/full`, Dekodierung des Originals 0,3–1 s – Ladeindikator).
- Seitenleiste: Begründung, Werte verständlich formuliert („Schärfe: sehr gut“, „Augen: offen“, „Belichtung: in Ordnung“), Aufnahmezeit, Dateiname, Größe; Knöpfe Behalten/Aussortieren.
- Debug-Ansicht (Taste D, für die Entwicklung): alle Rohwerte, Zero-Shot-Scores und Gesichtsboxen einblenden.
- Später (T14): Taste F zoomt auf das größte Gesicht.

**Vergleichsansicht:** 2–4 Bilder nebeneinander, jedes einzeln zoombar. Synchroner Zoom/Pan später (T14).

## 3. Übernehmen

Dialog „Übernehmen“:
- „194 Bilder (2,9 GB) werden in den Unterordner `_Aussortiert` verschoben. Nichts wird gelöscht. Sie können dies jederzeit rückgängig machen.“
- Liste der Anzahlen nach Grund (exakte Kopie / Serie / Motiv / Qualität / manuell).
- Knöpfe „Abbrechen“, „Verschieben“.
- Fortschritt; danach Ergebnis: „194 verschoben, 0 Fehler.“ + Knopf „Ordner im Explorer öffnen“ (`os.startfile`) + Hinweis „Wenn Sie zufrieden sind, können Sie den Ordner `_Aussortiert` später selbst löschen.“
- Fehlerliste (z. B. gesperrte Dateien) mit „Erneut versuchen“.
- Danach gilt das Album als „übernommen“ (T2).

**Rückgängig:** Knopf in der Fußzeile, solange ein Batch `moved` existiert; Dialog mit Datum, Anzahl; danach wieder Prüf-Übersicht mit allen Bildern. Funktioniert auch über die Protokoll-Kopie in `_Aussortiert`, falls der Programm-Cache fehlt (T4).

## 4. Leistung im Frontend (wichtig bei 2.000 Kacheln)

- Vorschauen als JPEG (~15–30 KB) über `GET /api/album/{id}/photo/{pid}/thumb?size=s`, HTTP-Cache-Header (`immutable`, Versionsparameter).
- `loading="lazy"` und `decoding="async"` auf `<img>`; Bündel/Gruppen-Blöcke außerhalb des sichtbaren Bereichs mit `content-visibility: auto` → flüssiges Scrollen ohne eigenes Virtual Scrolling. Falls trotzdem ruckelig (> 3.000 Kacheln im Reiter „Alle“): einfache Fenster-Virtualisierung pro Gruppe.
- Zugeklappte Bündel laden nur die 3 obersten Vorschauen.
- Statusänderung = lokales Update der Kachel + `POST /api/album/{id}/decision` (debounced); Server berechnet neu und schickt nur geänderte IDs zurück (`{changed: [{id, keep, reason}], stats}`).
- Regler/Modus: `input`-Event drosseln (150 ms), Antwort ebenfalls als Diff.

## 5. API (Überblick)

| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/api/pick-folder` | nativer Dialog (pywebview) → Pfad |
| POST | `/api/album/open` | `{path, recursive}` → Vorprüfung |
| POST | `/api/album/{id}/analyze` | Analyse starten/fortsetzen |
| POST | `/api/album/{id}/cancel` | abbrechen |
| GET | `/api/album/{id}/events` | SSE Fortschritt |
| GET | `/api/album/{id}/view` | alle Bilder + `review`: Gruppen, Entscheidungen, Begründungen, Statistik (A30; Reiter/Filter im Frontend) |
| POST | `/api/album/{id}/settings` | Modus, Zielquote, Gruppierungsstärke, Optionen → Diff |
| POST | `/api/album/{id}/decision` | `{photo_id | photo_ids, manual: keep|reject|null}` → Diff (mehrere Bilder für Gruppen-Aktionen) |
| POST | `/api/album/{id}/seen` | `{items: [[Bild, Art, gezeigter Vorschlag]]}` – angesehene Bilder (A34) |
| POST | `/api/album/{id}/group-done` | `{sig, done}` – Stapel bestätigt / wieder offen (A35) |
| POST | `/api/album/{id}/rethin` | „Erneut ausdünnen“ eines übernommenen Albums |
| GET | `/api/album/{id}/photo/{pid}/thumb?size=s|l&v=…` | Vorschau (Foto-IDs gelten je Album, A18) |
| GET | `/api/album/{id}/photo/{pid}/full` | Original aufrecht als JPEG (100 %-Zoom, A31) |
| GET | `/api/album/{id}/photo/{pid}/info` | Details für Seitenleiste |
| GET | `/api/recent` | „Zuletzt geöffnet“ |
| POST | `/api/instance/focus` | zweiter Programmstart holt das Fenster nach vorn (A3) |
| POST | `/api/album/{id}/apply/preview` | Zusammenfassung vor dem Verschieben |
| POST | `/api/album/{id}/apply` | `{photo_ids}` (genau die der Zusammenfassung) → Verschieben im Hintergrund |
| POST | `/api/album/{id}/apply/retry` | `{batch_id}` → fehlgeschlagene/unterbrochene Dateien erneut |
| GET | `/api/album/{id}/apply/status` | Fortschritt und Ergebnis des laufenden Vorgangs + Album-Status (Abfrage alle 150 ms) |
| POST | `/api/album/{id}/undo` | letzten Vorgang rückgängig (auch ohne Analyse, A32) |
| POST | `/api/album/{id}/open-folder` | `_Aussortiert` im Explorer (nur im Programmfenster) |
| POST | `/api/open-explorer` | Ordner öffnen |
| POST | `/api/shutdown` | Programm beenden |

Sicherheit: Server bindet an `127.0.0.1`; zufälliges Sitzungs-Token in der Start-URL des Fensters (`?t=…`), als Cookie mit `HttpOnly` und **`SameSite=Strict`** gesetzt, bei jeder API-Anfrage geprüft (verhindert, dass fremde Webseiten in einem Browser die lokale API ansprechen). Alle POST-Anfragen zusätzlich mit eigenem Header (z. B. `X-FotoDuenner: 1`) (T12). `Host`-Header auf `127.0.0.1:<port>` prüfen. Foto-Endpunkte liefern nur Dateien, die in der Album-DB stehen (kein beliebiger Pfadzugriff).
