# PreDB MP3 Sortierer – Anleitung (Version 8)

Python-Skript: `predb_sort_mp3.py`  
Voraussetzung: **Python 3.9 oder neuer**, keine zusätzlichen Python-Pakete nötig.  
Datenquelle: **PreDB.net-API** (`api.predb.net`). **Kein SRRDB-Zugriff.**

## 1. Installation

Falls du das ZIP-Paket verwendest, entpacke es und wechsle in den Ordner mit dem Skript:

```bash
unzip PreDB_MP3_Sortierer_v8_mit_README.zip
```

Unter Arch Linux kannst du Python bei Bedarf installieren:

```bash
sudo pacman -S python
```

Hilfe des Skripts anzeigen:

```bash
python3 predb_sort_mp3.py --help
```

## 2. Schnellstart

**Vorschau, ohne Dateien zu verändern oder eine API-Anfrage zu starten:**

```bash
python3 predb_sort_mp3.py "/pfad/zu/MP3"
```

**Tatsächlich sortieren, Ordnernamen korrigieren, M3U/SFV erzeugen und fehlende NFOs abrufen:**

```bash
python3 predb_sort_mp3.py "/pfad/zu/MP3" --apply
```

**Mit besonders langsamen API-Anfragen:**

```bash
python3 predb_sort_mp3.py "/pfad/zu/MP3" --apply --delay 15
```

Der Verzeichnispfad ist optional: Ohne Pfad verarbeitet das Skript das aktuelle Arbeitsverzeichnis (`.`).

## 3. Alle Kommandozeilenoptionen

| Option | Standard | Beschreibung |
|---|---|---|
| `directory` | `.` | Optionaler Positionsparameter: Hauptverzeichnis mit losen MP3s und bereits vorhandenen Release-Ordnern. Pfade mit Leerzeichen in Anführungszeichen setzen. |
| `--apply` | Aus | **Führt Änderungen aus.** Ohne diese Option gibt es nur eine Vorschau (keine Dateiveränderungen, kein HTTP). |
| `--offline` | Aus | **Keine Netzwerkabfragen**. Verarbeitet bei `--apply` nur Releases, für die gültige, noch nicht abgelaufene PreDB-Metadaten im Cache vorhanden sind. |
| `--no-nfo` | Aus | **Keine NFO-Dateien herunterladen.** PreDB-Metadaten für korrekte Release-Ordnernamen werden bei einem Online-Durchlauf weiterhin abgefragt. |
| `--delay SEKUNDEN` | `3` | Mindestabstand zwischen HTTP-Anfragen in Sekunden; **mindestens 2**. Dezimalwerte sind möglich, z. B. `--delay 5.5`. |
| `--cache-days TAGE` | `30` | Gültigkeitsdauer **erfolgreicher** PreDB-Metadaten im Cache; **grösser als 0**. Dezimalwerte möglich. Nicht gefundene Releases werden höchstens **1 Tag** gecacht (oder weniger, wenn `--cache-days` kleiner als 1 ist). |
| `--limit ANZAHL` | Unbegrenzt | Verarbeitet nur die ersten `ANZAHL` erkannten Releases, alphabetisch nach internem Sortierschlüssel; **mindestens 1**. |
| `-h`, `--help` | – | Zeigt die integrierte Hilfe und beendet das Programm. |

**Wichtig:** `--limit 10` bedeutet nicht «die nächsten zehn noch nicht bearbeiteten Releases». Bei jedem Start werden erneut die ersten zehn erkannten Releases ausgewählt.

## 4. Praktische Befehle

### Nur 5 Releases als Vorschau anzeigen

```bash
python3 predb_sort_mp3.py "/pfad/zu/MP3" --limit 5
```

### Nur die ersten 10 Releases wirklich bearbeiten

```bash
python3 predb_sort_mp3.py "/pfad/zu/MP3" --apply --limit 10
```

### Sortieren, ohne eine NFO herunterzuladen

```bash
python3 predb_sort_mp3.py "/pfad/zu/MP3" --apply --no-nfo
```

### Offline anhand gespeicherter API-Metadaten sortieren

```bash
python3 predb_sort_mp3.py "/pfad/zu/MP3" --apply --offline
```

Ohne gültigen PreDB-Cache wird das entsprechende Release **nicht** umbenannt oder neu einsortiert, weil die korrekte Gross-/Kleinschreibung nicht bestätigt werden kann. Vorhandene Ordner werden dabei nicht erfunden oder auf Verdacht umbenannt.

### Ältere Cache-Einträge wieder online abfragen

```bash
python3 predb_sort_mp3.py "/pfad/zu/MP3" --apply --cache-days 7
```

Dadurch gelten positive Cache-Einträge nur noch 7 Tage als frisch. Bereits gültige Cache-Einträge werden nicht bei jedem Start neu abgefragt.

### Konservativer Durchlauf ohne NFO-Downloads

```bash
python3 predb_sort_mp3.py "/pfad/zu/MP3" --apply --no-nfo --delay 15 --limit 20
```

### Im aktuellen Verzeichnis arbeiten

```bash
cd "/pfad/zu/MP3"
python3 /pfad/zum/predb_sort_mp3.py --apply
```

## 5. Regeln für Datei- und Ordnernamen

- **Release-Ordner:** Nur bei einem exakten (Gross-/Kleinschreibung ignorierenden) PreDB-Treffer wird die **Originalschreibweise von PreDB.net** verwendet.
- **MP3-Tracks:** Ihre bestehenden Dateinamen werden **nicht umbenannt**; vorhandene Kleinbuchstaben bleiben klein.
- **M3U/SFV:** Heissen `00-<release-in-kleinbuchstaben>.m3u` bzw. `.sfv`.
- **NFO:** Vorhandene NFO-Namen werden auf Kleinbuchstaben korrigiert. Ein schon vorhandenes Präfix `00-` bleibt; heruntergeladene NFOs heissen `<release-in-kleinbuchstaben>.nfo` **ohne neu erzwungenes `00-`**.
- **Inhalt vorhandener Dateien:** Wird nicht geändert.
- **Bei Namenskollisionen:** Keine vorhandene Datei wird überschrieben; der Konflikt wird gemeldet.

### Beispiel

Ordnername, exakt nach PreDB.net:

```text
2_Many_Djs-Live_At_Glazen_Huis_(Ghent)-STREAM-12-21-2011-CiN_INT/
```

Dateinamen im Ordner (der Release-Teil bleibt klein):

```text
01-2_many_djs-live_at_glazen_huis_(ghent)-stream-12-21-2011-cin_int.mp3
00-2_many_djs-live_at_glazen_huis_(ghent)-stream-12-21-2011-cin_int.m3u
00-2_many_djs-live_at_glazen_huis_(ghent)-stream-12-21-2011-cin_int.sfv
2_many_djs-live_at_glazen_huis_(ghent)-stream-12-21-2011-cin_int.nfo
```

Die gezeigte NFO kann bei einer bereits vorhandenen `00-...nfo` auch das Präfix `00-` behalten.

## 6. Was wird geprüft und erstellt?

Das Skript sucht **direkt im angegebenen Hauptverzeichnis** nach MP3-Dateien im Muster `NN-<release>.mp3` (eine bis drei Ziffern) sowie nach passenden Begleitdateien. Es erkennt auch bereits vorhandene Release-Unterordner, wenn sich darin passende MP3s befinden. Es durchsucht **nicht rekursiv beliebige verschachtelte Verzeichnisse**.

Bei `--apply`:

1. Es prüft den Release-Namen anhand einer passenden PreDB-Antwort oder eines gültigen Cache-Eintrags.
2. Es legt den Release-Ordner an bzw. korrigiert seine Schreibweise.
3. Es verschiebt passende lose Dateien und führt passende, bereits vorhandene Release-Ordner zusammen – ohne Überschreiben.
4. **Vor** dem Erzeugen neuer Begleitdateien prüft es vorhandene originale M3U-/SFV-Listen, Tracknummern und gegebenenfalls CRC32-Prüfsummen.
5. Fehlende `.m3u`- und `.sfv`-Dateien werden **lokal aus den vorhandenen MP3s** erstellt. Sie sind nicht garantiert bytegleich mit den ursprünglichen Scene-Dateien.
6. Wenn keine passende NFO existiert und `--no-nfo` nicht gesetzt ist, versucht es den Download über PreDB.net.

**Grenze der Vollständigkeitsprüfung:** Ohne echte Original-M3U/SFV-Dateiliste lässt sich nicht sicher feststellen, ob ein Release vollständig ist. Das Skript erkennt Nummernlücken und kann Original-SFV-CRC32-Werte prüfen, sofern diese Dateien vorliegen. Die bei PreDB angegebene Gesamtdateizahl ist **nicht** gleich der MP3-Trackzahl.

## 7. Cache, API-Limits und Fehlermeldungen

- API-Metadaten liegen im versteckten Unterordner `.predb_cache` des MP3-Hauptverzeichnisses.
- Die Vorschau (`ohne --apply`) verwendet höchstens den vorhandenen Cache und sendet keine HTTP-Anfragen.
- `--offline --apply` nutzt ausschliesslich gültige Cache-Daten und lädt **keine NFOs** herunter.
- Das Skript hat **keine feste Höchstzahl** von HTTP-Anfragen pro Durchlauf, wartet aber mindestens `--delay` Sekunden zwischen Anfragen.
- Bei **HTTP 403 oder 429** stoppt es alle weiteren Online-Abfragen **für diesen Durchlauf**. Ein erneuter Start hebt eine mögliche Sperre des Anbieters nicht auf.
- Ein einzelnes Release kann mehrere API-Anfragen auslösen (Release-Daten, Suche, NFO-Metadaten und ggf. NFO-Datei).
- Anfragen erfolgen nur an die im Skript zugelassenen PreDB-API-Adressen; kein Website-Crawling.

Typische Ausgaben:

| Ausgabe | Bedeutung |
|---|---|
| `[RELEASE] ... (PreDB-verifiziert)` | Passenden PreDB-Eintrag gefunden. |
| `[ORDNER] Originalschreibweise hergestellt` | Ordner auf exakte PreDB-Schreibweise gebracht. |
| `[VERSCHOBEN]` | Datei ohne Überschreiben verschoben. |
| `[ERSTELLT]` | Fehlende lokale M3U oder SFV angelegt. |
| `[NFO] Heruntergeladen` | NFO über PreDB.net bezogen. |
| `[NFO] Bereits vorhanden` | Kein Download erforderlich. |
| `[UNVOLLSTAENDIG]` | Tracknummer, referenzierte Datei oder CRC32 ist auffällig. |
| `[UEBERSPRUNGEN]` | Kein bestätigter PreDB-Name; daher keine Umbenennung auf Verdacht. |
| `[BLEIBT] Ziel bereits vorhanden` | Überschreibschutz hat die Änderung verhindert. |
| `[API] HTTP 403/429` | Anbieter verweigert weitere Abfragen, Online-Zugriff stoppt für den Lauf. |

## 8. Sicherheits- und Nutzungshinweise

- **Niemals Dateien überschreiben:** Das Skript legt neue Dateien exklusiv an und überspringt belegte Ziele. Es kann aber Dateien und Ordner **verschieben oder umbenennen**; daher zuerst einen Probelauf bzw. ein Backup verwenden.
- **Keine Symlinks verfolgen:** Symlinks werden bei der Erkennung übersprungen.
- Bei Dateisystemen mit eingeschränkten Umbenennungsfunktionen (z. B. bestimmten SMB-/FUSE-Mounts) versucht das Skript einen alternativen sicheren Weg. Bei Fehlern Originalpfade und eventuelle `.predb-recase-*`-Verzeichnisse prüfen.
- Wenn PreDB.net eine IP-Sperre gesetzt hat, die Abfragen stoppen und die Bedingungen des Dienstes beachten; nicht durch Proxy-Rotation umgehen.

## 9. Alle Optionen auf einen Blick

```text
python3 predb_sort_mp3.py [-h] [--apply] [--offline] [--no-nfo]
                          [--delay SEKUNDEN] [--cache-days TAGE]
                          [--limit ANZAHL] [directory]
```

**Empfohlener Start:**

```bash
python3 predb_sort_mp3.py "/pfad/zu/MP3"           # Vorschau
python3 predb_sort_mp3.py "/pfad/zu/MP3" --apply   # Ausführen
```
