from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path
from typing import Any


FIELD_ORDER = [
    "Date",
    "Title",
    "TitleOG",
    "Identify",
    "Artist",
    "ArtistOG",
    "CoverArtist",
    "Version",
    "Discnumber",
    "Track",
    "Comment",
    "Special",
    "xxHash",
]

IDENTIFY_KEYWORDS = (
    "ver",
    "version",
    "karaoke",
    "reload",
    "reloaded",
    "remix",
    "remaster",
    "acoustic",
    "instrumental",
    "live",
    "anniversary",
    "birthday",
    "edit",
)


LINE_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*:\s*(.*?)\s*$")


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_optional_text(value: Any) -> str | None:
    text = _to_text(value)
    if not text or text == "None":
        return None
    return text


def _decode_raw_value(raw_value: str) -> str:
    value = raw_value.strip()
    if value in {"", "None"}:
        return ""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]

    return value


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False

    for idx, ch in enumerate(value):
        if escaped:
            escaped = False
            continue

        if ch == "\\":
            escaped = True
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            continue

        if ch == "#" and not in_single and not in_double:
            return value[:idx].rstrip()

    return value.rstrip()


def _encode_text_value(text: str | None) -> str:
    if text is None:
        return "None"

    value = text.strip()
    if value == "" or value == "None":
        return "None"

    # Keep style close to existing files: plain values when safe, quoted otherwise.
    if any(ch in value for ch in ['"', "\\", "\n", "\r"]):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    return value


def _contains_non_latin_script(text: str) -> bool:
    for ch in text:
        if not ch.isalpha():
            continue
        if "LATIN" not in unicodedata.name(ch, ""):
            return True
    return False


def _looks_latin_text(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    return not _contains_non_latin_script(text)


def _split_last_parenthetical(text: str) -> tuple[str, str] | None:
    text = text.rstrip()
    if not text.endswith(")"):
        return None

    depth = 0
    for i in range(len(text) - 1, -1, -1):
        ch = text[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                left = text[:i].rstrip()
                inner = text[i + 1 : -1].strip()
                return left, inner

    return None


def _looks_identify(group_text: str) -> bool:
    lowered = group_text.strip().lower()
    return any(keyword in lowered for keyword in IDENTIFY_KEYWORDS)


def _extract_title_fields(raw_title: str) -> tuple[str, str | None, str | None]:
    title = raw_title.strip()
    identify_parts: list[str] = []

    # Peel off trailing identify tags like "(Neuro ver.)" or "(Reloaded)".
    while True:
        split_result = _split_last_parenthetical(title)
        if split_result is None:
            break

        left, group = split_result
        if not _looks_identify(group):
            break

        identify_parts.insert(0, group)
        title = left

    split_result = _split_last_parenthetical(title)
    title_og: str | None = None
    if split_result is not None:
        left, group = split_result
        if left and _contains_non_latin_script(left) and _looks_latin_text(group):
            title_og = left
            title = group

    identify = " | ".join(identify_parts) if identify_parts else None
    return title.strip(), title_og, identify


def _extract_artist_fields(raw_artist: str) -> tuple[str, str | None]:
    artist = raw_artist.strip()
    split_result = _split_last_parenthetical(artist)
    if split_result is None:
        return artist, None

    left, group = split_result
    if left and _contains_non_latin_script(left) and _looks_latin_text(group):
        return group.strip(), left.strip()

    return artist, None


def _parse_hjson_object(raw: str, path: Path) -> tuple[list[str], dict[str, str]]:
    lines = raw.splitlines()
    if not lines:
        raise ValueError(f"Empty file: {path}")

    keys_in_order: list[str] = []
    values: dict[str, str] = {}

    for line in lines:
        stripped = line.strip()
        if stripped in {"", "{", "}"}:
            continue

        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        match = LINE_RE.match(line)
        if match is None:
            raise ValueError(f"Unsupported line format in {path}: {line}")

        key, value = match.group(1), _strip_inline_comment(match.group(2))
        if key not in values:
            keys_in_order.append(key)
        values[key] = value

    return keys_in_order, values


def _build_migrated_values(keys_in_order: list[str], values: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    raw_title = values.get("Title", "")
    raw_artist = values.get("Artist", "")

    title_text = _decode_raw_value(raw_title)
    artist_text = _decode_raw_value(raw_artist)

    has_new_title_fields = ("TitleOG" in values) or ("Identify" in values)
    has_new_artist_fields = "ArtistOG" in values

    if has_new_title_fields:
        new_title = raw_title
        new_title_og = values.get("TitleOG", "None")
        new_identify = values.get("Identify", "None")
    else:
        title_en, title_og, identify = _extract_title_fields(title_text)
        new_title = raw_title if title_en == title_text else _encode_text_value(title_en)
        new_title_og = _encode_text_value(title_og)
        new_identify = _encode_text_value(identify)

    if has_new_artist_fields:
        new_artist = raw_artist
        new_artist_og = values.get("ArtistOG", "None")
    else:
        artist_en, artist_og = _extract_artist_fields(artist_text)
        new_artist = raw_artist if artist_en == artist_text else _encode_text_value(artist_en)
        new_artist_og = _encode_text_value(artist_og)

    migrated = dict(values)
    migrated["Title"] = new_title
    migrated["TitleOG"] = new_title_og
    migrated["Identify"] = new_identify
    migrated["Artist"] = new_artist
    migrated["ArtistOG"] = new_artist_og

    final_order: list[str] = [key for key in FIELD_ORDER if key in migrated]
    for key in keys_in_order:
        if key not in final_order:
            final_order.append(key)

    return final_order, migrated


def _serialize_hjson(order: list[str], values: dict[str, str]) -> str:
    out_lines = ["{"]
    for key in order:
        out_lines.append(f"  {key}: {values[key]}")
    out_lines.append("}")
    out_lines.append("")
    return "\n".join(out_lines)


def migrate_file(path: Path, dry_run: bool = False) -> bool:
    raw = path.read_text(encoding="utf-8")
    key_order, parsed_values = _parse_hjson_object(raw, path)
    final_order, migrated_values = _build_migrated_values(key_order, parsed_values)
    new_text = _serialize_hjson(final_order, migrated_values)

    changed = raw != new_text
    if changed and not dry_run:
        path.write_text(new_text, encoding="utf-8")

    return changed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Batch-migrate metadata: add TitleOG/Identify/ArtistOG and normalize field order."
        )
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Root directory to scan recursively for .hjson files (default: current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report how many files would change, without writing",
    )
    args = parser.parse_args()

    root = Path(args.root)
    files = sorted(p for p in root.rglob("*.hjson") if p.is_file())

    changed_count = 0
    for file_path in files:
        if migrate_file(file_path, dry_run=args.dry_run):
            changed_count += 1

    mode = "would be changed" if args.dry_run else "changed"
    print(f"Processed {len(files)} files; {changed_count} {mode}.")


if __name__ == "__main__":
    main()
