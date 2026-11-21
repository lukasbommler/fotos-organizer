# 01 – Anforderungen

## 1. Ausgangslage und Ziel

Der Nutzer hat pro Urlaub 500–2.000 Fotos (10–20 MB pro Bild, also bis ca. 30–40 GB pro Album) auf einem Windows-Laptop. Manuelles Ausdünnen ist zu aufwändig. Ziel ist, die Bildanzahl spürbar zu reduzieren, **ohne wichtige Bilder zu verlieren** – zunächst nur durch Entfernen von Doppelten und technisch schlechten Bildern, bei Bedarf bis zu einer wählbaren Quote (siehe F2).

Was wichtig ist (Priorität absteigend):
1. **Bilder mit Personen** – die wichtigste Kategorie, großzügig behalten.
2. **Besondere Bilder** – Motive, die nur einmal oder selten fotografiert wurden; ästhetisch gelungene Bilder.
3. Von Serien/gleichen Motiven die **besten Vertreter**.

Was weg darf:
- Exakte Duplikate (gleiche Datei mehrfach).
- Sehr ähnliche Bilder (Serienbilder, mehrfach fotografiertes gleiches Motiv) über die besten Vertreter hinaus.
- Technisch schlechte Bilder (unscharf, verwackelt, stark über-/unterbelichtet), sofern es kein unersetzliches Bild ist.

Was nie ausgedünnt wird: Belichtungsreihen und Panorama-Sequenzen (werden als Ganzes behalten).

## 2. Nutzer und Umgebung

- **Nutzer:** der Entwickler selbst und Freunde (DECISIONS D1). Bedienung muss ohne Kommandozeile gehen; Freunde bekommen ein ZIP mit `.exe` über öffentliche GitHub-Releases.
- **Plattform:** nur Windows 10/11, 64 Bit (D3).
- **Hardware:** Office-Laptop, **keine dedizierte Grafikkarte**. Annahme: Intel Core i5/i7 oder AMD Ryzen 5 mit 4–8 Kernen, 8–16 GB RAM, interne SSD; Fotos ggf. auf externer USB-Festplatte.
- **Speicherort:** lokale Ordner pro Urlaub (z. B. auf dem Desktop), eventuell mit Unterordnern (pro Tag oder pro Kamera/Handy). Cloud-Ordner werden nicht erwartet (D7).

## 3. Funktionsumfang (Muss)

### F1 – Ordner wählen und analysieren
- Ordnerauswahl über nativen Windows-Dialog (über pywebview).
- Option „Unterordner einbeziehen“ (Standard: an). Ordner `_Aussortiert` wird immer ignoriert.
- Unterstütztes Format in v1: **nur JPG/JPEG** (D2). Alle anderen Dateien (RAW, HEIC, PNG, Videos …) werden ignoriert und nie bewegt. HEIC/PNG eventuell in einer späteren Version.
- Sidecar-Dateien `.xmp` mit gleichem Namen gehören zum Bild und werden immer mitverschoben.
- Fortschrittsanzeige mit Stufe, Anzahl, geschätzter Restzeit; Abbrechen möglich; Fortsetzen nach Abbruch ohne Neuberechnung bereits analysierter Bilder.

### F2 – Automatische Beurteilung
- Für jedes Bild: Aufnahmezeit, Personen/Gesichter (Anzahl, Größe, Schärfe, **Augen offen**), Schärfe, Belichtung, Ästhetik-Schätzung, Bildinhalt-Vektor (Embedding), Datei-Hash, einfache Inhalts-Labels (z. B. Screenshot, Schild, Person von hinten).
- Erkennung exakter Duplikate, Belichtungsreihen und Panorama-Sequenzen.
- Gruppierung ähnlicher Bilder: **Serien** (fast identisch) und **Motivgruppen** (gleiches Motiv, auch zeitlich etwas auseinander).
- Pro Gruppe Auswahl der besten und möglichst **unterschiedlichen** Bilder; Personenbilder bevorzugt.
- Aussortieren technisch schlechter Bilder mit Begründung.
- **Zwei Modi** (D5, D12):
  - **„Nur Doppelte & Schlechte“** (Standard beim ersten Lauf): kein Zielwert; nur exakte Duplikate, überzählige Serienbilder und technisch schlechte Bilder werden aussortiert. Motivgruppen bleiben unangetastet.
  - **„Zielquote“**: Regler 30–90 % behalten (Standard 50 %), zusätzlich werden Motivgruppen ausgedünnt.

### F3 – Prüf-Übersicht
- Dunkles, modernes Design; alles auf einen Blick: Gruppen als kompakte, aufklappbare **Bündel** (gestapelte Mini-Vorschauen) (D9).
- Drei Bereiche: **Gruppen** (ausgedünnte Serien/Motive), **Qualität** (wegen schlechter Qualität aussortiert oder „Bitte prüfen“), **Einzelbilder** (behalten, zur Kontrolle).
- Jede Entscheidung per Klick umkehrbar; Großansicht mit Zoom auf 100 % zur Schärfeprüfung; Vergleichsansicht.
- Zu jedem aussortierten Bild eine **verständliche Begründung** („fast identisch mit Bild 3, aber unschärfer“).
- Moduswechsel, Zielquote und Gruppierungsstärke wirken **sofort** (ohne neue Analyse); manuelle Entscheidungen bleiben dabei erhalten und ändern nie die Entscheidung anderer Bilder (T1).

### F4 – Übernehmen und Rückgängig
- „Übernehmen“ verschiebt alle aussortierten Bilder (inkl. `.xmp`-Sidecars) nach `<Album>\_Aussortiert\`, relative Unterordnerstruktur bleibt erhalten.
- Vor dem Verschieben: Zusammenfassung (Anzahl, Speicherplatz) und Bestätigung.
- „Rückgängig“ stellt den letzten (bzw. jeden protokollierten) Übernahme-Vorgang vollständig wieder her – auch dann, wenn der Programm-Cache gelöscht wurde (Protokoll-Kopie in `_Aussortiert`, T4).
- Kein Löschen. Endgültiges Löschen macht der Nutzer selbst im Explorer.

### F5 – Sitzungen
- Analyse-Ergebnisse und manuelle Entscheidungen werden pro Album gespeichert. Erneutes Öffnen desselben Ordners zeigt den letzten Stand.
- Neue/geänderte Dateien im Ordner werden beim erneuten Öffnen erkannt und nachanalysiert.
- Ein bereits übernommenes Album wird als „übernommen“ (mit Ausgangsanzahl) angezeigt und nicht automatisch erneut ausgedünnt (T2).

### F6 – Sterne-Schutz
- Bereits vergebene Bewertungen (EXIF/XMP Rating ≥ 1, eingebettet oder im `.xmp`-Sidecar) gelten als **geschützt** und werden nie automatisch aussortiert. FotoDünner liest Ratings nur, schreibt sie nie.

## 4. Funktionsumfang (Kann, spätere Versionen)
- Formate HEIC/PNG; RAW und RAW+JPG-Paare.
- Bekannte Personen markieren (Gesichter-Clustering, „Familie immer behalten“).
- Personen-Körperdetektor (Personen ohne sichtbares Gesicht); in v1 nur über das Zero-Shot-Label „Person von hinten“.
- Option „In Papierkorb statt `_Aussortiert`“ (über `send2trash`).
- Export einer „Best-of“-Auswahl (z. B. 10 %) in separaten Ordner.
- Vergleichsansicht mit synchronem Zoom, Taste F (Zoom auf Gesicht), Tastenkombinationen `G` + Taste (T14).
- In-App-Modus „Bewertung erfassen“ für die Kalibrierung (D10, wird nach der Auswahl-Logik gebaut).

## 5. Nicht-Ziele
- Keine Bildbearbeitung, kein Fotoverwaltungsprogramm, keine Cloud, keine Benutzerkonten.
- Keine Videoanalyse.
- Keine Mac/Linux-Paketierung (Code soll aber plattformneutral bleiben, wo es nichts kostet).
- Kein Hell-Modus.

## 6. Qualitätsanforderungen

| Kriterium | Anforderung |
|---|---|
| Sicherheit der Daten | 0 gelöschte oder veränderte Originale, unter allen Umständen (Absturz, Abbruch, volle Platte). |
| Erstanalyse 2.000 Bilder à 15 MB, SSD, 4-Kern-Laptop | ≤ 10 min (Ziel 5–7 min) |
| Erneutes Öffnen analysierter Ordner | ≤ 5 s bis Übersicht sichtbar |
| Regler/Modus ändern → Übersicht aktualisiert | ≤ 300 ms |
| Scrollen mit 2.000 Bildern | flüssig, Animationen bremsen nicht (nur `transform`/`opacity`) |
| RAM-Verbrauch | ≤ 2 GB während der Analyse |
| Personenbilder | Personenbild wird nie als einziges seiner Gruppe aussortiert; Gesichtserkennung findet ≥ 95 % der deutlich sichtbaren Gesichter (> 3 % Bildbreite). |
| Laptop bleibt bedienbar | Analyse nutzt max. (Kerne − 1) Prozesse, niedrige Prozesspriorität. |
| Installation | Entpacken + Doppelklick; kein Admin-Recht, kein Python nötig. |

## 7. Geklärte Fragen

| Frage | Antwort |
|---|---|
| Welche Formate liegen vor? | Nur JPG/JPEG in v1 (D2). |
| Gibt es RAW+JPG doppelt? | Nicht relevant in v1 (D2). |
| Liegen die Fotos in OneDrive? | Nein, lokale Ordner; einfache Warnung bei „nur online“-Dateien bleibt (D7). |
| Sollen bereits vergebene Sterne schützen? | Ja, Rating ≥ 1 = geschützt (F6). |
| Standard-Reduktion? | „Nur Doppelte & Schlechte“; Zielquote-Modus mit Standard 50 % (D5). |
