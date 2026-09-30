# Adapted from FL2744/L1001 (MIT). See LICENSE.
from __future__ import annotations
import re, unicodedata, json
from dataclasses import dataclass
from html import escape
from typing import Any


ENTITY_GLOSSARY_LIMIT = 500

TRANSLATION_STYLE_OPTIONS = {
    "1": (
        "Literal",
        "Translate as literally as the target language permits. Preserve source "
        "syntax, repetition, metaphors, and lexical choices where intelligible; "
        "do not smooth away strangeness or ambiguity.",
    ),
    "2": (
        "Faithful and balanced",
        "Balance close fidelity with idiomatic target-language prose. Preserve "
        "meaning, tone, nuance, structure, and ambiguity without needless stiffness.",
    ),
    "3": (
        "Domesticated",
        "Favor natural target-culture idiom, conventions, units, and familiar "
        "expressions when this preserves the intended effect. Do not alter facts, "
        "names, historical context, or substantive meaning.",
    ),
    "4": (
        "Readable",
        "Prioritize clear, flowing, contemporary prose for a general adult reader. "
        "Simplify awkward sentence structure when needed without losing content.",
    ),
    "5": (
        "Accessible",
        "Use plain language, shorter sentences where helpful, and transparent "
        "wording suitable for a broad audience. Preserve every idea and necessary "
        "technical or cultural distinction.",
    ),
    "6": (
        "Literary",
        "Prioritize voice, rhythm, imagery, rhetorical effect, and stylistic texture "
        "while remaining accurate and complete.",
    ),
    "7": (
        "Academic",
        "Use precise, formal, discipline-appropriate prose and preserve technical "
        "terminology, qualifications, citations, and argumentative structure.",
    ),
}

ENTITY_MARKER_RE = re.compile(
    r"\[\[ENTITY:([A-Za-z0-9_-]+)\]\](.*?)\[\[/ENTITY\]\]",
    flags=re.DOTALL,
)

TRANSLATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "translated_html": {"type": "string"},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "marker_id": {"type": "string"},
                    "canonical_name": {"type": "string"},
                    "display_text": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "person",
                            "place",
                            "organization",
                            "work",
                            "event",
                            "object",
                            "concept",
                            "other",
                        ],
                    },
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "note": {"type": "string"},
                },
                "required": [
                    "marker_id",
                    "canonical_name",
                    "display_text",
                    "category",
                    "aliases",
                    "note",
                ],
                "additionalProperties": False,
            },
        },
        "continuity_notes": {"type": "string"},
    },
    "required": ["translated_html", "entities", "continuity_notes"],
    "additionalProperties": False,
}

SYSTEM_INSTRUCTIONS = r"""
You are a meticulous professional translator working on a long-form text that is
being processed in consecutive chunks.

TRANSLATION REQUIREMENTS
1. Translate every part of the SOURCE CHUNK. Do not summarize, omit, censor,
   expand, explain, or add material to the translation.
2. Produce an accurate, idiomatic, modern, and accessible translation.
3. Prefer vocabulary understandable to an educated layperson when that does not
   erase necessary technical, historical, literary, legal, religious, or cultural
   distinctions.
4. Preserve the author's meaning, tone, voice, imagery, ambiguity, paragraphing,
   headings, dialogue, lists, emphasis, tables, quotations, and links as closely
   as the target language allows.
5. Preserve deliberate repetition and uncertainty. Do not silently "improve" the
   author's argument or resolve ambiguities that exist in the source.
6. Use the supplied previous context only to maintain continuity. Never translate
   or repeat the previous context in the current output.
7. Use spellings from the ENTITY SPELLING GLOSSARY when the same entity reappears.
8. Write the translation and all entity notes in the requested target language.

HTML REQUIREMENTS
1. Return translated_html as a semantic HTML fragment, not as Markdown and not as
   a complete HTML document.
2. Do not include <!DOCTYPE>, <html>, <head>, <body>, <main>, <script>, or <style>
   tags. The calling program will create the complete document.
3. Use suitable semantic elements such as <p>, <h1> through <h6>, <blockquote>,
   <ul>, <ol>, <li>, <em>, <strong>, <br>, <hr>, <pre>, <code>, <table>,
   <thead>, <tbody>, <tr>, <th>, <td>, and <a>.
4. Produce well-formed HTML. Close every element that requires a closing tag.
5. Escape literal ampersands and angle brackets when they are text rather than
   markup. Preserve meaningful hyperlinks from the source when present.
6. Do not add CSS classes, inline styles, JavaScript, tracking elements, or
   external assets.

PROPER NOUN AND ENTITY MARKUP
1. Within this chunk, mark the first occurrence of every named entity or proper
   noun. This includes named people, places, organizations, institutions, works,
   events, objects, languages, religions, historical periods, named doctrines,
   and other specifically named things.
2. Mark only the visible name, not surrounding punctuation, articles, titles,
   descriptive text, or HTML tags, using exactly this syntax:
      [[ENTITY:E1]]Visible name[[/ENTITY]]
3. Keep each marker entirely inside the text content of a single HTML element.
   Never place an opening or closing HTML tag inside an ENTITY marker.
4. Use a unique marker ID for each marked entity in the chunk: E1, E2, E3, etc.
5. Mark only the first occurrence of an entity within the current chunk. The
   calling program will determine whether it is the first occurrence in the
   complete work.
6. Do not add bold or endnote-reference HTML for entity markers yourself. The
   calling program will add it.
7. Add exactly one entity record for every marker in translated_html.
8. canonical_name should identify the entity consistently. display_text should
   match the visible marked text. aliases should list useful alternative names,
   shortened forms, or transliterations found in or strongly implied by the text.
9. The note should briefly explain who or what the entity is and why it matters in
   this work or passage. Use one to three concise sentences. Do not invent dates,
   titles, relationships, historical claims, or biographical details. When the
   available context is insufficient, explicitly give a limited contextual note,
   such as "A person mentioned in this passage; the supplied text gives no further
   identification."

OUTPUT
Return only data conforming to the supplied JSON schema. translated_html must
contain the complete translated HTML fragment, including the entity markers.
continuity_notes should be a brief note about names, pronouns, terminology,
unresolved references, or stylistic details that may help with the next chunk;
use an empty string if none are needed.
""".strip()

@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    token_count: int

@dataclass
class WorkMetadata:
    title: str
    author: str
    source_language: str
    source_details: str
    target_language: str
    target_details: str

    def as_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "author": self.author,
            "source_language": self.source_language,
            "source_details": self.source_details,
            "target_language": self.target_language,
            "target_details": self.target_details,
        }

def normalize_entity(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().strip()
    value = re.sub(r"^[\s\"'“”‘’`*_#]+|[\s\"'“”‘’`*_#]+$", "", value)
    value = re.sub(r"[^\w\s\-]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value

class EntityRegistry:
    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.entries: list[dict[str, Any]] = entries or []
        self.alias_to_numbers: dict[str, set[int]] = {}
        self._reindex()

    def _reindex(self) -> None:
        self.alias_to_numbers.clear()
        for entry in self.entries:
            number = int(entry["number"])
            values = [
                entry.get("canonical_name", ""),
                entry.get("display_text", ""),
                *entry.get("aliases", []),
            ]
            for value in values:
                normalized = normalize_entity(str(value))
                if normalized:
                    self.alias_to_numbers.setdefault(normalized, set()).add(number)

    def _entry_for_number(self, number: int) -> dict[str, Any]:
        return self.entries[number - 1]

    @staticmethod
    def _categories_compatible(first: str, second: str) -> bool:
        first = first or "other"
        second = second or "other"
        if first == second or "other" in {first, second}:
            return True
        # Countries, cities, universities, governments, and institutions may be
        # classified inconsistently as places or organizations across chunks.
        return {first, second} == {"place", "organization"}

    @staticmethod
    def _canonical_names_related(first: str, second: str) -> bool:
        first = normalize_entity(first)
        second = normalize_entity(second)
        if not first or not second:
            return True
        if first == second or first in second or second in first:
            return True

        first_tokens = set(first.split())
        second_tokens = set(second.split())
        shared = first_tokens & second_tokens
        if len(shared) < 2:
            return False
        return len(shared) / min(len(first_tokens), len(second_tokens)) >= 0.75

    def _find_existing_number(
        self,
        *,
        canonical: str,
        category: str,
        aliases: list[str],
    ) -> int | None:
        canonical_norm = normalize_entity(canonical)

        # Prefer an exact canonical-name match. This is the strongest signal and
        # avoids merging two people who happen to share a short first name.
        for entry in self.entries:
            if (
                normalize_entity(str(entry.get("canonical_name", "")))
                == canonical_norm
                and self._categories_compatible(
                    category, str(entry.get("category", "other"))
                )
            ):
                return int(entry["number"])

        candidate_numbers: set[int] = set()
        for alias in aliases:
            normalized = normalize_entity(alias)
            if normalized:
                candidate_numbers.update(self.alias_to_numbers.get(normalized, set()))

        for number in sorted(candidate_numbers):
            entry = self._entry_for_number(number)
            if not self._categories_compatible(
                category, str(entry.get("category", "other"))
            ):
                continue
            if self._canonical_names_related(
                canonical, str(entry.get("canonical_name", ""))
            ):
                return number
        return None

    def resolve_or_add(
        self, record: dict[str, Any], actual_surface: str
    ) -> tuple[dict[str, Any], bool]:
        canonical = (
            str(record.get("canonical_name", "")).strip() or actual_surface.strip()
        )
        display = actual_surface.strip() or str(record.get("display_text", "")).strip()
        category = str(record.get("category", "other"))
        candidates = [
            str(record.get("display_text", "")),
            actual_surface,
            *[str(item) for item in record.get("aliases", [])],
        ]

        existing_number = self._find_existing_number(
            canonical=canonical,
            category=category,
            aliases=[canonical, *candidates],
        )

        if existing_number is not None:
            entry = self._entry_for_number(existing_number)
            merged_aliases = list(entry.get("aliases", []))
            for candidate in [canonical, *candidates]:
                candidate = candidate.strip()
                if (
                    candidate
                    and candidate not in merged_aliases
                    and candidate
                    not in {entry.get("canonical_name", ""), entry.get("display_text", "")}
                ):
                    merged_aliases.append(candidate)
            entry["aliases"] = merged_aliases
            self._reindex()
            return entry, False

        note = re.sub(r"\s+", " ", str(record.get("note", "")).strip())
        if not note:
            note = (
                "The supplied passage identifies this named entity but gives no "
                "further contextual information."
            )

        aliases: list[str] = []
        for candidate in candidates:
            candidate = candidate.strip()
            if candidate and candidate not in aliases and candidate not in {canonical, display}:
                aliases.append(candidate)

        entry = {
            "number": len(self.entries) + 1,
            "canonical_name": canonical,
            "display_text": display,
            "category": category,
            "aliases": aliases,
            "note": note,
        }
        self.entries.append(entry)
        self._reindex()
        return entry, True

    def glossary_text(self, limit: int = ENTITY_GLOSSARY_LIMIT) -> str:
        if not self.entries:
            return "(No entities have appeared in earlier chunks.)"

        selected = self.entries[-limit:]
        lines: list[str] = []
        for entry in selected:
            aliases = [
                str(alias)
                for alias in entry.get("aliases", [])
                if str(alias).strip()
            ][:4]
            alias_text = f"; aliases: {', '.join(aliases)}" if aliases else ""
            lines.append(
                f"- {entry['canonical_name']} | preferred visible form: "
                f"{entry['display_text']} | {entry['category']}{alias_text}"
            )
        return "\n".join(lines)

    def endnotes_html(self, heading: str = "Endnotes") -> str:
        if not self.entries:
            return ""

        lines = [
            '<section class="endnotes" id="endnotes" aria-labelledby="endnotes-heading">',
            f'  <h2 id="endnotes-heading">{escape(heading)}</h2>',
            '  <ol>',
        ]
        for entry in self.entries:
            number = int(entry["number"])
            canonical = escape(
                str(entry["canonical_name"]).replace("\n", " ").strip()
            )
            category = escape(
                str(entry.get("category", "other")).replace("_", " ")
            )
            note = escape(str(entry["note"]).replace("\n", " ").strip())
            lines.extend(
                [
                    f'    <li id="endnote-{number}">',
                    f'      <p><strong>{canonical}</strong> '
                    f'<span class="entity-category">({category})</span>. {note} '
                    f'<a class="endnote-backlink" href="#endnote-ref-{number}" '
                    f'aria-label="Return to reference {number}">↩</a></p>',
                    '    </li>',
                ]
            )
        lines.extend(['  </ol>', '</section>'])
        return "\n".join(lines)

def process_entity_markers(
    translated_html: str,
    entity_records: list[dict[str, Any]],
    registry: EntityRegistry,
) -> tuple[str, list[str]]:
    records_by_id = {
        str(record.get("marker_id", "")).strip(): record
        for record in entity_records
        if str(record.get("marker_id", "")).strip()
    }
    warnings: list[str] = []

    def replace_marker(match: re.Match[str]) -> str:
        marker_id = match.group(1)
        surface = match.group(2).strip()
        record = records_by_id.get(marker_id)
        if record is None:
            warnings.append(f"Marker {marker_id} had no entity record; markup was removed.")
            return surface

        entry, is_new = registry.resolve_or_add(record, surface)
        if not is_new:
            return surface
        number = int(entry["number"])
        return (
            f'<strong class="first-entity">{surface}</strong>'
            f'<sup class="endnote-reference" id="endnote-ref-{number}">'
            f'<a href="#endnote-{number}" aria-label="Endnote {number}">{number}</a>'
            f'</sup>'
        )

    processed = ENTITY_MARKER_RE.sub(replace_marker, translated_html)

    # Remove malformed leftover marker wrappers rather than leaking internal syntax
    # into the final document.
    if "[[ENTITY:" in processed or "[[/ENTITY]]" in processed:
        warnings.append("Malformed entity markup was found and cleaned.")
        processed = re.sub(r"\[\[ENTITY:[^\]]+\]\]", "", processed)
        processed = processed.replace("[[/ENTITY]]", "")

    return processed.strip(), warnings

def metadata_prompt(metadata: WorkMetadata) -> str:
    return "\n".join(
        [
            f"Title: {metadata.title or '(not supplied)'}",
            f"Author: {metadata.author or '(not supplied)'}",
            f"Source language: {metadata.source_language}",
            f"Source details: {metadata.source_details or '(none supplied)'}",
            f"Target language: {metadata.target_language}",
            f"Target details: {metadata.target_details or '(none supplied)'}",
        ]
    )

def build_chunk_prompt(
    *,
    chunk: TextChunk,
    chunk_count: int,
    metadata: WorkMetadata,
    registry: EntityRegistry,
    previous_source_tail: str,
    previous_target_tail: str,
    previous_continuity_notes: str,
) -> str:
    return f"""
WORK METADATA
{metadata_prompt(metadata)}

CHUNK POSITION
Chunk {chunk.index + 1} of {chunk_count}

ENTITY SPELLING GLOSSARY
Use these spellings when the same entities reappear. Even for an entity in this
list, mark its first occurrence in the current chunk with an ENTITY marker; the
calling program will remove duplicate global notes.
{registry.glossary_text()}

PREVIOUS CONTINUITY NOTES — CONTEXT ONLY; DO NOT TRANSLATE OR REPEAT
{previous_continuity_notes or '(none)'}

PREVIOUS SOURCE TAIL — CONTEXT ONLY; DO NOT TRANSLATE OR REPEAT
{previous_source_tail or '(none; this is the first chunk)'}

PREVIOUS TARGET TAIL — CONTEXT ONLY; DO NOT REPEAT
{previous_target_tail or '(none; this is the first chunk)'}

SOURCE CHUNK TO TRANSLATE
--- BEGIN SOURCE CHUNK ---
{chunk.text}
--- END SOURCE CHUNK ---
""".strip()

def validate_translation_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("The API response was not a JSON object.")
    translated = payload.get("translated_html")
    entities = payload.get("entities")
    continuity = payload.get("continuity_notes")
    if not isinstance(translated, str) or not translated.strip():
        raise ValueError("The API returned an empty translation.")
    if not isinstance(entities, list):
        raise ValueError("The API response did not contain an entity list.")
    if not isinstance(continuity, str):
        raise ValueError("The API response did not contain continuity notes.")
    return payload

def parse_json_output(output_text: str) -> dict[str, Any]:
    """Parse JSON returned directly or in a Markdown code fence."""
    cleaned = output_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = cleaned.removesuffix("```").rstrip()
    return validate_translation_payload(json.loads(cleaned))

def clean_heading_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def infer_html_lang(language: str) -> str:
    "Return a reasonable BCP 47 language tag for common language names."
    normalized = normalize_entity(language).replace("-", " ")
    mapping = {
        "arabic": "ar",
        "chinese": "zh",
        "mandarin": "zh",
        "cantonese": "yue",
        "english": "en",
        "french": "fr",
        "german": "de",
        "greek": "el",
        "hebrew": "he",
        "hindi": "hi",
        "italian": "it",
        "japanese": "ja",
        "korean": "ko",
        "latin": "la",
        "persian": "fa",
        "farsi": "fa",
        "portuguese": "pt",
        "russian": "ru",
        "spanish": "es",
        "turkish": "tr",
        "ukrainian": "uk",
        "urdu": "ur",
    }
    for name, tag in mapping.items():
        if re.search(rf"\b{re.escape(name)}\b", normalized):
            return tag
    return "und"

def html_direction(lang: str) -> str:
    return "rtl" if lang in {"ar", "fa", "he", "ur"} else "ltr"

def localized_endnotes_heading(lang: str) -> str:
    headings = {
        "ar": "الهوامش",
        "de": "Endnoten",
        "el": "Σημειώσεις",
        "en": "Endnotes",
        "es": "Notas finales",
        "fa": "یادداشت‌ها",
        "fr": "Notes",
        "he": "הערות סיום",
        "hi": "अंत टिप्पणियाँ",
        "it": "Note finali",
        "ja": "後注",
        "ko": "미주",
        "pt": "Notas finais",
        "ru": "Концевые сноски",
        "tr": "Sonnotlar",
        "uk": "Кінцеві примітки",
        "ur": "اختتامی حواشی",
        "zh": "尾注",
        "yue": "尾註",
    }
    return headings.get(lang, "Endnotes")
