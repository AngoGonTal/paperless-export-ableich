#!/usr/bin/env python3

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def human_readable_size(size_bytes: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(size_bytes)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"

            return f"{size:.2f} {unit}"

        size /= 1024

    return f"{size_bytes} B"


def md5_file(file_path: Path) -> str:
    md5 = hashlib.md5()

    with file_path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            md5.update(chunk)

    return md5.hexdigest()


def extract_paperless_id(file_path: Path) -> int | None:
    """
    Erkennt eine Paperless-ID am Ende des Dateinamens
    unmittelbar vor der Dateiendung.

    Beispiele:

        Rechnung #4711.pdf
            -> 4711

        Dokument#123.pdf
            -> 123

        Dokument #123 alt.pdf
            -> kein Treffer

        Dokument #ABC.pdf
            -> kein Treffer
    """

    match = re.search(
        r"#(\d+)$",
        file_path.stem,
    )

    if not match:
        return None

    return int(match.group(1))


def load_env(env_path: Path) -> dict[str, str]:
    """
    Liest eine .env-Datei ein.

    Unterstützt sowohl:

        PAPERLESS_URL=http://paperless
        PAPERLESS_TOKEN=abc123

    als auch:

        export PAPERLESS_URL="http://paperless"
        export PAPERLESS_TOKEN="abc123"
    """

    if not env_path.is_file():
        raise RuntimeError(
            f".env-Datei nicht gefunden: {env_path}"
        )

    values = {}

    with env_path.open("r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()

            if not line or line.startswith("#"):
                continue

            if line.startswith("export "):
                line = line[len("export "):].strip()

            if "=" not in line:
                continue

            key, value = line.split("=", 1)

            key = key.strip()
            value = value.strip()

            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in ("'", '"')
            ):
                value = value[1:-1]

            values[key] = value

    return values


class PaperlessClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def get_json(
        self,
        path_or_url: str,
        params: dict | None = None,
        allow_not_found: bool = False,
    ) -> dict | None:

        if path_or_url.startswith(("http://", "https://")):
            url = path_or_url
        else:
            url = f"{self.base_url}/{path_or_url.lstrip('/')}"

        if params:
            separator = "&" if "?" in url else "?"
            url += separator + urlencode(params)

        request = Request(
            url,
            headers={
                "Authorization": f"Token {self.token}",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)

        except HTTPError as exc:
            if exc.code == 404 and allow_not_found:
                return None

            try:
                body = exc.read().decode(
                    "utf-8",
                    errors="replace",
                )
            except Exception:
                body = ""

            message = (
                f"Paperless antwortete mit HTTP {exc.code} "
                f"für {url}"
            )

            if body:
                message += f": {body}"

            raise RuntimeError(message) from exc

        except URLError as exc:
            raise RuntimeError(
                f"Paperless ist nicht erreichbar: {exc.reason}"
            ) from exc

    def find_tag_id(self, tag_name: str) -> int:
        """
        Sucht einen Paperless-Tag anhand seines exakten Namens.
        """

        url = "/api/tags/"
        params = {
            "page_size": 100,
        }

        while url:
            data = self.get_json(
                url,
                params=params,
            )

            if data is None:
                break

            params = None

            for tag in data.get("results", []):
                if tag.get("name") == tag_name:
                    return tag["id"]

            url = data.get("next")

        raise RuntimeError(
            f'Der Tag "{tag_name}" wurde in Paperless nicht gefunden.'
        )

    def lookup_document_by_checksum(
        self,
        checksum: str,
        required_tag_id: int,
    ) -> tuple[str, int | None]:
        """
        Sucht ein Dokument anhand seiner MD5-Prüfsumme.

        Rückgabe:

            ("✅", id)
                Dokument gefunden und Tag vorhanden

            ("🏷️", id)
                Dokument gefunden, Tag fehlt

            ("❓", None)
                Dokument nicht gefunden
        """

        checksum = checksum.strip().lower()

        data = self.get_json(
            "/api/documents/",
            params={
                "checksum__iexact": checksum,
                "page_size": 10,
            },
        )

        if data is None:
            return "❓", None

        documents = data.get("results", [])

        if not documents:
            return "❓", None

        for document in documents:
            tags = document.get("tags", [])

            if required_tag_id in tags:
                return "✅", document.get("id")

        return "🏷️", documents[0].get("id")

    def lookup_document_by_id(
        self,
        document_id: int,
        required_tag_id: int,
    ) -> tuple[str, int | None]:
        """
        Sucht ein Dokument direkt anhand seiner Paperless-ID.

        Dieser Lookup wird nur als Fallback verwendet, wenn
        über die MD5-Prüfsumme kein Dokument gefunden wurde.

        Rückgabe:

            ("✅", id)
                ID existiert und Tag vorhanden

            ("🏷️", id)
                ID existiert, Tag fehlt

            ("❓", None)
                ID existiert nicht
        """

        document = self.get_json(
            f"/api/documents/{document_id}/",
            allow_not_found=True,
        )

        if document is None:
            return "❓", None

        tags = document.get("tags", [])

        if required_tag_id in tags:
            return "✅", document.get("id", document_id)

        return "🏷️", document.get("id", document_id)

    def lookup_document(
        self,
        checksum: str,
        file_path: Path,
        required_tag_id: int,
    ) -> tuple[str, int | None]:
        """
        Sucht zuerst per MD5.

        Nur wenn die MD5-Suche keinen Treffer liefert, wird
        versucht, eine Paperless-ID aus dem Dateinamen zu
        extrahieren und direkt darüber zu suchen.
        """

        status, document_id = self.lookup_document_by_checksum(
            checksum,
            required_tag_id,
        )

        if document_id is not None:
            return status, document_id

        filename_document_id = extract_paperless_id(
            file_path
        )

        if filename_document_id is None:
            return "❓", None

        return self.lookup_document_by_id(
            filename_document_id,
            required_tag_id,
        )

    def get_documents_with_tag(
        self,
        tag_id: int,
    ) -> list[dict]:
        """
        Liefert alle Paperless-Dokumente mit dem angegebenen Tag.
        """

        documents = []

        url = "/api/documents/"
        params = {
            "tags__id": tag_id,
            "page_size": 100,
        }

        while url:
            data = self.get_json(
                url,
                params=params,
            )

            if data is None:
                break

            params = None

            documents.extend(
                data.get("results", [])
            )

            url = data.get("next")

        return documents


def should_exclude(relative_path: Path) -> bool:
    """
    Entscheidet, ob ein Pfad aus der Ausgabe ausgeschlossen wird.
    """

    path_string = str(relative_path)

    # Pfade mit Dollarzeichen oder Gleichheitszeichen ausblenden
    if "$" in path_string or "=" in path_string:
        return True

    # Dateien oder Verzeichnisse ausblenden,
    # deren Name mit "." beginnt
    if any(
        part.startswith(".")
        for part in relative_path.parts
    ):
        return True

    return False


def collect_files(base_path: Path) -> list[Path]:
    """
    Sammelt rekursiv alle relevanten Dateien.
    """

    files = []

    for path in base_path.rglob("*"):
        if not path.is_file():
            continue

        relative_path = path.relative_to(base_path)

        if should_exclude(relative_path):
            continue

        files.append(path)

    files.sort(
        key=lambda p: str(
            p.relative_to(base_path)
        ).lower()
    )

    return files


def generate_markdown(
    base_path: Path,
    files: list[Path],
    paperless: PaperlessClient,
    tag_id: int,
    tag_name: str,
) -> None:

    if len(files) > 99:
        raise ValueError(
            f"Es wurden {len(files)} Dateien gefunden. "
            "Es können maximal 99 Dateien nummeriert werden."
        )

    total_size = 0
    indent = " " * 7

    local_paperless_document_ids: set[int] = set()

    print("# Dateiliste")
    print()

    for number, file_path in enumerate(
        files,
        start=1,
    ):
        relative_path = file_path.relative_to(
            base_path
        )

        file_size = file_path.stat().st_size
        total_size += file_size

        file_hash = md5_file(
            file_path
        )

        paperless_status, paperless_id = paperless.lookup_document(
            file_hash,
            file_path,
            tag_id,
        )

        if paperless_id is not None:
            local_paperless_document_ids.add(
                paperless_id
            )

        paperless_id_text = (
            str(paperless_id)
            if paperless_id is not None
            else "fehlt"
        )

        print(
            f"## {number:02d}. `{relative_path}`"
        )

        print(
            f"{indent}"
            f"Größe: {human_readable_size(file_size)} | "
            f'Inhalt: "{file_hash}" | '
            f"Paperless ID: {paperless_id_text} "
            f"{paperless_status}"
        )

        print(
            f"{indent}"
            "[  ] in Steuererklärung berücksichtigt"
            "   "
            "[  ] in Steuererklärung irrelevant"
        )

        print()

    #
    # Alle Paperless-Dokumente mit dem gewünschten Tag laden.
    #
    tagged_documents = paperless.get_documents_with_tag(
        tag_id
    )

    #
    # Dokumente bestimmen, für die im Zielverzeichnis oder
    # irgendeinem rekursiven Unterverzeichnis kein passendes
    # lokales Dokument gefunden wurde.
    #
    missing_documents = [
        document
        for document in tagged_documents
        if document.get("id")
        not in local_paperless_document_ids
    ]

    missing_documents.sort(
        key=lambda document: (
            document.get("title") or ""
        ).lower()
    )

    print("---")
    print()
    print(
        f'## In Paperless mit Tag "{tag_name}", '
        "aber nicht im Zielverzeichnis "
        "oder dessen Unterverzeichnissen"
    )
    print()

    if missing_documents:
        for document in missing_documents:
            document_id = document.get("id")

            title = (
                document.get("title")
                or "(ohne Titel)"
            )

            original_filename = (
                document.get("original_file_name")
                or document.get("original_filename")
            )

            if original_filename:
                print(
                    f"- `{title}` "
                    f"— `{original_filename}` "
                    f"(Paperless ID: {document_id})"
                )
            else:
                print(
                    f"- `{title}` "
                    f"(Paperless ID: {document_id})"
                )
    else:
        print(
            "Keine. Alle entsprechend getaggten "
            "Paperless-Dokumente wurden im Zielverzeichnis "
            "oder einem seiner Unterverzeichnisse gefunden."
        )

    print()
    print("---")
    print()
    print("## Zusammenfassung")
    print()

    print(
        f"**Anzahl Dateien:** {len(files)}  "
    )

    print(
        f"**Gesamtes Dateivolumen:** "
        f"{human_readable_size(total_size)}  "
    )

    print(
        f"**Paperless-Dokumente mit Tag "
        f"„{tag_name}“, die lokal fehlen:** "
        f"{len(missing_documents)}"
    )


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Listet Dateien rekursiv als Markdown auf stdout auf "
            "und gleicht sie per MD5 bzw. Paperless-ID "
            "mit Paperless ab."
        )
    )

    parser.add_argument(
        "tag",
        help=(
            "Paperless-Tag, der bei gefundenen Dokumenten "
            "vorhanden sein soll, z.B. Steuer2023"
        ),
    )

    parser.add_argument(
        "base_path",
        type=Path,
        help=(
            "Basispfad, der rekursiv durchsucht werden soll"
        ),
    )

    args = parser.parse_args()

    base_path = (
        args.base_path
        .expanduser()
        .resolve()
    )

    if not base_path.exists():
        print(
            f"Fehler: Der Basispfad existiert nicht: "
            f"{base_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not base_path.is_dir():
        print(
            f"Fehler: Der Basispfad ist kein Verzeichnis: "
            f"{base_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        #
        # .env liegt im selben Verzeichnis wie das Skript.
        #
        script_dir = Path(__file__).resolve().parent
        env_path = script_dir / ".env"

        env = load_env(
            env_path
        )

        paperless_url = env.get(
            "PAPERLESS_URL"
        )

        paperless_token = env.get(
            "PAPERLESS_TOKEN"
        )

        if not paperless_url:
            raise RuntimeError(
                "PAPERLESS_URL fehlt in der .env-Datei."
            )

        if not paperless_token:
            raise RuntimeError(
                "PAPERLESS_TOKEN fehlt in der .env-Datei."
            )

        paperless = PaperlessClient(
            paperless_url,
            paperless_token,
        )

        #
        # Tag einmalig anhand seines Namens auflösen.
        #
        tag_id = paperless.find_tag_id(
            args.tag
        )

        #
        # Zielverzeichnis rekursiv durchsuchen.
        #
        files = collect_files(
            base_path
        )

        #
        # Markdown-Ausgabe erzeugen.
        #
        generate_markdown(
            base_path,
            files,
            paperless,
            tag_id,
            args.tag,
        )

    except (
        ValueError,
        RuntimeError,
        OSError,
    ) as exc:
        print(
            f"Fehler: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
