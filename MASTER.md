Ich arbeite an einem Python-Skript namens `begleitschreiben.py`, das für Steuerunterlagen eine Markdown-Übersicht erzeugt und diese mit Paperless-ngx abgleicht.

Ziel:
Das Skript soll einen lokalen Steuerordner rekursiv analysieren, jede Datei auflisten, mit Paperless abgleichen und zusätzlich anzeigen, welche Paperless-Dokumente mit einem bestimmten Steuer-Tag lokal fehlen.

Aktueller Aufruf:

    ./begleitschreiben.py Steuer2023 ../2023

Argumente:
1. `tag`
   - Beispiel: `Steuer2023`
   - Wird als Paperless-Tag verwendet.
2. `base_path`
   - Beispiel: `../2023`
   - Wird rekursiv nach Dateien durchsucht.

Konfiguration:
Im selben Verzeichnis wie das Skript liegt eine `.env`.

Unterstützte Syntax:

    PAPERLESS_URL="http://paperless:8000"
    PAPERLESS_TOKEN="..."

oder:

    export PAPERLESS_URL="http://paperless:8000"
    export PAPERLESS_TOKEN="..."

Paperless-Version:
Aktuell Paperless-ngx 2.20.x.

Wichtige Randbedingung:
Keine Vollscans über alle Paperless-Dokumente durchführen.
Das Skript soll effizient bleiben und keine tausenden Metadata-Requests erzeugen.

Lokale Dateisuche:
- rekursiv ab `base_path`
- Ausgabe des vollständigen relativen Pfads ab `base_path`
- alphabetisch sortieren
- maximal 99 Dateien
- Nummerierung zweistellig:
  `01.`, `02.`, ... `99.`

Folgende Pfade komplett ignorieren:
- Pfad enthält `$`
- Pfad enthält `=`
- irgendein Datei- oder Verzeichnisname beginnt mit `.`

Beispiele für ignorierte Pfade:

    .git/config
    foo/.cache/datei.pdf
    $RECYCLE.BIN/datei.pdf
    foo=bar/datei.pdf

Hash:
Für jede lokale Datei wird MD5 berechnet.

Paperless-Lookup:
Primärer Lookup immer über die MD5-Prüfsumme:

    GET /api/documents/?checksum__iexact=<md5>

Nicht `checksum=...` verwenden.

Die Prüfung gegen `archive_checksum` wurde bewusst verworfen, weil dafür kein effizienter direkter Lookup zur Verfügung stand.

Fallback über Paperless-ID:
Wenn über MD5 kein Paperless-Dokument gefunden wird, soll der Dateiname auf eine Paperless-ID geprüft werden.

Konvention:
Wenn der Dateiname ohne Erweiterung auf

    #<Ziffern>

endet, wird die Ziffernfolge als Paperless-Dokument-ID interpretiert.

Beispiele:

    Steuerbescheinigung #4711.pdf
    Dokument#123.pdf

werden erkannt.

Nicht erkannt:

    Dokument #123 alt.pdf
    Dokument #ABC.pdf

Die ID wird dann direkt geprüft über:

    GET /api/documents/<id>/

Die führenden Nullen von Paperless sind unproblematisch:

    #0000160

wird als ID 160 interpretiert.

Lookup-Reihenfolge:

    MD5
      ↓
    Treffer?
      ├─ ja → Dokument verwenden
      └─ nein → #ID aus Dateiname suchen
                   ↓
                 vorhanden?
                   ├─ ja → Dokument per ID laden
                   └─ nein → Dokument fehlt

Statussymbole:
- `✅`
  - Dokument in Paperless gefunden
  - übergebener Tag ist gesetzt

- `🏷️`
  - Dokument in Paperless gefunden
  - übergebener Tag fehlt

- `❓`
  - Dokument weder über MD5 noch über #ID gefunden

Tag-Auflösung:
Der Tagname wird einmalig über `/api/tags/` auf die Paperless-ID aufgelöst.

Markdown-Ausgabe pro Datei:

    ## 01. `2023 Altersvorsorge/2024-02-01 Riesterförderung.pdf`
           Größe: 2.37 MiB | Inhalt: "ba30a3df608d3d480a9b2561b9d996fb" | Paperless ID: 4711 ✅
           [  ] in Steuererklärung berücksichtigt   [  ] in Steuererklärung irrelevant

Formatierungsregeln:
- keine Leerzeile zwischen Pfad und Metadaten
- Metadatenzeile mit genau 7 Leerzeichen einrücken
- Checkbox-Zeile ebenfalls mit genau 7 Leerzeichen einrücken
- beide Checkboxen in derselben Zeile
- zwischen den beiden Checkbox-Texten genau drei Leerzeichen
- Hash in normalen Anführungszeichen ausgeben, nicht in Backticks
- Paperless-ID ausgeben
- falls nicht gefunden:
  
      Paperless ID: fehlt

Dateigröße:
Human-readable mit:
- B
- KiB
- MiB
- GiB
- TiB

Paperless-Abgleich fehlender lokaler Dokumente:
Nach der lokalen Dateiliste sollen alle Paperless-Dokumente ausgegeben werden, die:

- den übergebenen Tag besitzen
- aber keiner Datei im `base_path` oder irgendeinem rekursiven Unterverzeichnis zugeordnet werden konnten

Dafür:
- während der lokalen MD5/#ID-Lookups alle gefundenen Paperless-IDs merken
- anschließend einmal serverseitig alle Dokumente mit dem Tag laden:

    GET /api/documents/?tags__id=<tag_id>

- die IDs mit den lokal gefundenen Paperless-IDs vergleichen
- nur die Differenz ausgeben

Kein Vollscan über den gesamten Paperless-Bestand.

Abschnitt:

    ## In Paperless mit Tag "Steuer2023", aber nicht im Zielverzeichnis oder dessen Unterverzeichnissen

Beispiel:

    - `Jahressteuerbescheinigung 2023` (Paperless ID: 1843)
    - `Spendenbescheinigung 2023` (Paperless ID: 2017)

Wenn keine fehlen:

    Keine. Alle entsprechend getaggten Paperless-Dokumente wurden im Zielverzeichnis oder einem seiner Unterverzeichnisse gefunden.

Danach:

    ## Zusammenfassung

mit mindestens:

    **Anzahl Dateien:** 37
    **Gesamtes Dateivolumen:** 82.31 MiB
    **Paperless-Dokumente mit Tag „Steuer2023“, die lokal fehlen:** 2

stdout/stderr:
- komplette Markdown-Ausgabe nach stdout
- Fehler nach stderr
- dadurch soll funktionieren:

    ./begleitschreiben.py Steuer2023 ../2023 > Steuer2023.md

Shebang:

    #!/usr/bin/env python3

Keine externen Python-Abhängigkeiten verwenden.
Aktuell werden nur Standardbibliotheken genutzt:
- argparse
- hashlib
- json
- re
- sys
- pathlib
- urllib

Paperless Storage Path:
Ich habe für Steuerdokumente einen Speicherpfad in Paperless konfiguriert, der die Paperless-ID im Dateinamen trägt.

Beispiel:

    {{ created_year }} {{ title }} #{{ doc_pk }}

`created_year` entspricht dem Dokument-/Ausstellungsjahr, nicht dem Importjahr.
`doc_pk` wird von Paperless z.B. als `#0000160` ausgegeben.

Wenn wir an diesem Projekt weiterarbeiten:
- bestehende Funktionalität nicht stillschweigend entfernen
- Effizienz des Paperless-Zugriffs beibehalten
- keine Iteration über tausende Paperless-Dokumente einführen
- bei Änderungen möglichst immer wieder das vollständige Skript mit Shebang ausgeben
- stdout-kompatible Markdown-Ausgabe erhalten
