# schulcloud-downloader (`sc-dl`)

Lädt die eigenen Materialien aus der Schul-Cloud (Schul-Cloud Brandenburg / dBildungscloud) gesammelt herunter, sortiert nach Kurs bzw. Raum, Thema und Board. Filter nach Kurs, Thema, Datum und Dateityp sind möglich.

Das Tool liest nur. Es ändert, löscht oder lädt nichts in der Schul-Cloud hoch.

## Direkt im Browser (ohne Installation)

**https://hjorven.github.io/schulcloud-downloader/**

Anmelden, alle Fundstellen ansehen, auswählen und herunterladen. In Chrome und Edge speichert die Seite direkt in einen Ordner deiner Wahl, in anderen Browsern als ZIP-Datei.

Die Seite hat keinen eigenen Server. Anmeldung und Downloads laufen direkt zwischen deinem Browser und der Schul-Cloud, das Passwort wird nicht gespeichert. Falls die Schul-Cloud Anfragen von dieser Seite blockiert, zeigt die Seite einen Hinweis; dann die lokale Version unten nutzen (`sc-dl web`), die genauso aussieht.

## Was geladen wird

| Bereich | Zielordner |
|---|---|
| Dateien in Themen | `<Kurs>/Themen/01 <Thema>/` |
| In Themen verlinkte Kursdateien | zusätzlich im Ordner des Themas |
| Dateien an Aufgaben | `<Kurs>/Aufgaben/<Aufgabe>/` |
| Datei-Elemente und Datei-Ordner in Boards | `<Kurs>/Board - <Titel>/01 <Spalte>/01 <Karte>/` |
| Alter Bereich "Kursdateien" | `<Kurs>/Kursdateien/<Ordner>/` |
| Boards in den neuen Räumen | `Raum <Name>/Board - <Titel>/...` |

Bereits geladene Dateien merkt sich das Tool in `.sc-dl-index.json` im Zielordner. Ein zweiter Lauf lädt nur neue oder geänderte Dateien.

## Installation

Voraussetzung: Python 3.10 oder neuer (`python --version` bzw. `python3 --version`).

**macOS / Linux**

```bash
git clone https://github.com/hjorven/schulcloud-downloader.git
cd schulcloud-downloader
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/hjorven/schulcloud-downloader.git
cd schulcloud-downloader
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
```

Danach steht der Befehl `sc-dl` zur Verfügung, solange die `.venv` aktiv ist.

## Am einfachsten: Webseite oder geführter Modus

**Webseite** (öffnet sich im Browser, läuft nur auf deinem Rechner):

```bash
sc-dl web
```

Dort E-Mail und Passwort eingeben, das Tool sucht alle Kurse und Räume durch und zeigt alles als aufklappbare Liste mit Häkchen. Suchfeld und Dateityp-Knöpfe helfen beim Eingrenzen, dann Zielordner eintragen und "Herunterladen" klicken. Beenden mit `Strg+C` im Terminal.

Die Seite ist nur unter `http://127.0.0.1` erreichbar, nicht aus dem Netz. Zugangsdaten gehen nur an die Schul-Cloud.

**Geführter Modus im Terminal** (fragt alles nacheinander ab, Auswahl per Nummern wie `1,3-5`):

```bash
sc-dl start
```

`sc-dl` ohne weitere Angabe startet ebenfalls den geführten Modus.

## Anmelden (für die Befehle unten)

```bash
sc-dl login --url https://brandenburg.cloud --user deine.mail@schule.de
```

Das Passwort wird verdeckt abgefragt und nicht gespeichert. Gespeichert wird nur das Zugangs-Token (gültig für einige Stunden) in `~/.config/sc-dl/config.json` bzw. unter Windows in `%APPDATA%\sc-dl\config.json`, lesbar nur für dich.

Falls der Passwort-Login nicht geht, kannst du das Token aus dem Browser nehmen: In der Schul-Cloud angemeldet `F12` drücken, Reiter "Application" bzw. "Speicher", dann "Cookies", den Wert von `jwt` kopieren und so einfügen:

```bash
sc-dl login --url https://brandenburg.cloud --token "HIER_DEN_JWT_WERT_EINFUEGEN"
```

Abmelden (Token löschen):

```bash
sc-dl logout
```

## Erster Test

```bash
sc-dl probe
```

Prüft, welche Schnittstellen bei deiner Instanz antworten, und zeigt nur `OK`/`FEHLT` plus Anzahlen, keine Inhalte.

## Benutzung

Übersicht, ohne etwas zu laden:

```bash
sc-dl list
sc-dl list --kurs "Mathe" --dateien
sc-dl list --csv uebersicht.csv
```

Alles herunterladen:

```bash
sc-dl download --out ~/Schulcloud
```

Beispiele mit Filtern:

```bash
# Nur Kurse mit "Mathe" im Namen, nur PDFs
sc-dl download --out ~/Schulcloud --kurs "Mathe" --typ pdf

# Nur Themen bzw. Board-Abschnitte mit "Funktion", geändert seit 1. September 2026
sc-dl download --out ~/Schulcloud --thema "Funktion" --seit 2026-09-01

# Zeitraum, mehrere Dateitypen, nur Kurse (keine Räume)
sc-dl download --out ~/Schulcloud --seit 01.08.2026 --bis 31.10.2026 --typ pdf --typ docx --nur kurse

# Probelauf: zeigt die Zielpfade, lädt nichts
sc-dl download --out ~/Schulcloud --kurs "Physik" --dry-run
```

| Option | Bedeutung |
|---|---|
| `--kurs TEXT` | Kurs- oder Raumname enthält TEXT (mehrfach möglich, Groß/klein egal) |
| `--thema TEXT` | Thema, Aufgabe, Board, Spalte, Karte oder Ordner enthält TEXT |
| `--typ ENDUNG` | Dateiendung, z.B. `pdf`, `docx`, `png` |
| `--seit DATUM`, `--bis DATUM` | Änderungsdatum, Format `2026-09-01` oder `01.09.2026` |
| `--nur kurse` / `--nur raeume` | Nur einen der beiden Bereiche |
| `--ohne-kursdateien` | Alten Bereich "Kursdateien" auslassen |
| `--parallel N` | Gleichzeitige Downloads (Standard 3) |
| `--dry-run` | Nichts laden, nur anzeigen |

## Hinweise

- Es werden nur Dateien geladen, die dein Konto ohnehin sehen darf.
- Das Tool macht kurze Pausen zwischen Anfragen und lädt standardmäßig höchstens 3 Dateien gleichzeitig, um die Schul-Cloud nicht zu belasten.
- Läuft das Token während eines langen Downloads ab, einfach `sc-dl login` und den gleichen Befehl erneut starten. Fertige Dateien werden übersprungen.

## Entwicklung

Die Browser-Version liegt in `web/` (reines HTML/JavaScript, keine Abhängigkeiten) und wird bei jedem Push auf `main` über `.github/workflows/pages.yml` veröffentlicht. Einmalig nötig: im Repo unter Settings, Pages, Source "GitHub Actions" auswählen.

```bash
python -m pip install ".[test]"
python -m pytest -q
```

Die verwendeten Endpunkte stammen aus [hpi-schul-cloud/schulcloud-server](https://github.com/hpi-schul-cloud/schulcloud-server):

| Zweck | Endpunkt |
|---|---|
| Login | `POST /api/v3/authentication/local` |
| Kurse | `GET /api/v3/courses` |
| Kursinhalt | `GET /api/v3/course-rooms/{id}/board` |
| Thema | `GET /api/v3/lessons/{id}` |
| Räume | `GET /api/v3/rooms`, `GET /api/v3/rooms/{id}/boards` |
| Boards | `GET /api/v3/boards/{id}`, `GET /api/v3/cards?ids=...` |
| Dateien (neu) | `GET /api/v3/file/list/school/{schoolId}/{parentType}/{parentId}` |
| Kursdateien (alt) | `GET /api/v1/fileStorage?owner={kursId}`, `GET /api/v1/fileStorage/signedUrl?file={id}&download=true` |
