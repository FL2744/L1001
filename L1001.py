#!/usr/bin/env python3
"""
Translate a long work with the OpenAI Responses API.

Features
--------
- Interactive prompts for the source file and language information.
- Paragraph-aware, token-based chunking for works of roughly 20,000-100,000 words.
- Interactive translation styles and token chunk sizes.
- A startup choice between OpenAI and Virginia Tech ARC resources.
- API keys are requested securely at runtime and are not written to disk.
- OpenAI uses the Responses API; ARC uses its documented OpenAI-compatible
  Chat Completions endpoint.
- The first global occurrence of each proper noun is bolded and footnoted.
- Contextual endnotes are assembled at the end of the HTML document.
- Automatic retries, atomic checkpoints, and resume support.
- Input support for .txt, .md, and .docx files.

HOW TO RUN (macOS / Linux Terminal)
-----------------------------------
First-time setup, in your project directory:

    python3 -m venv .venv
    source .venv/bin/activate
    python3 -m pip install --upgrade pip
    python3 -m pip install -r requirements_long_work_translator_html.txt

Then run:

    python3 L1001.py

On later runs, the packages normally do not need to be installed again. From
your project directory:

    source .venv/bin/activate
    python3 L1001.py

At startup, choose OpenAI or Virginia Tech ARC. The program asks for that
provider's API key using hidden input. The key is kept only in memory for the
current run.

To leave the virtual environment afterward, run:

    deactivate

Do not use the Python script as a requirements file. In particular, do NOT run
"pip install -r L1001.py". The -r option must point to
requirements_long_work_translator_html.txt.
"""

from __future__ import annotations

import hashlib
import getpass
import json
import os
import random
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

try:
    import tiktoken
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "Missing dependency 'tiktoken'. Install requirements with:\n"
        "  python3 -m pip install -U openai python-dotenv tiktoken python-docx charset-normalizer"
    ) from exc

try:
    from dotenv import load_dotenv
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "Missing dependency 'python-dotenv'. Install requirements with:\n"
        "  python3 -m pip install -U openai python-dotenv tiktoken python-docx charset-normalizer"
    ) from exc

try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        OpenAI,
        RateLimitError,
    )
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "Missing or outdated dependency 'openai'. Install or upgrade with:\n"
        "  python3 -m pip install -U openai"
    ) from exc


# ----------------------------- Configuration ----------------------------- #

MODEL = "gpt-5.4-nano"
API_BASE_URL = ""
API_KEY_OVERRIDE = ""
REASONING_EFFORT = "medium"
MAX_SOURCE_TOKENS_PER_CHUNK = 5_500
CONTEXT_TAIL_TOKENS = 700
MAX_OUTPUT_TOKENS = 30_000
MAX_API_ATTEMPTS = 6
API_TIMEOUT_SECONDS = 1_800.0
ENTITY_GLOSSARY_LIMIT = 500
STATE_VERSION = 4
CHUNKING_VERSION = 1

ANNOTATIONS_ENABLED = False
ANNOTATION_SCOPE = "Named people, places, organizations, works, events, objects, and concepts"
ANNOTATION_NOTE_GUIDANCE = "Use one to three concise sentences per annotation."

TRANSLATION_STYLE = "Readable and balanced"
TRANSLATION_STYLE_INSTRUCTION = """
Use fluent, natural target-language prose while preserving the source's meaning,
tone, detail, ambiguity, and degree of formality. Balance fidelity with readability.
""".strip()

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


# ------------------------------- Data types ------------------------------- #


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


# ------------------------------ File helpers ------------------------------ #


def clean_dragged_path(value: str) -> str:
    """Clean common quoting/escaping produced by drag-and-drop in terminals."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.replace("\\ ", " ")


def prompt_existing_file() -> Path:
    while True:
        raw = input("Source file (.txt, .md, or .docx): ").strip()
        path = Path(clean_dragged_path(raw)).expanduser()
        if not path.exists():
            print(f"File not found: {path}")
            continue
        if not path.is_file():
            print(f"Not a file: {path}")
            continue
        if path.suffix.lower() not in {".txt", ".md", ".markdown", ".docx"}:
            print("Supported input types are .txt, .md, .markdown, and .docx.")
            continue
        return path.resolve()


def prompt_required(label: str) -> str:
    while True:
        value = input(label).strip()
        if value:
            return value
        print("Please enter a value.")


def prompt_yes_no(label: str, default: bool = True) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        value = input(label + suffix).strip().casefold()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please enter y or n.")


def prompt_translation_style() -> tuple[str, str]:
    print("\nTranslation style:")
    for number, (name, _) in TRANSLATION_STYLE_OPTIONS.items():
        default_marker = " (default)" if number == "2" else ""
        print(f"  {number}. {name}{default_marker}")
    print("  8. Custom instructions")

    while True:
        choice = input("Choose a translation style [2]: ").strip() or "2"
        if choice in TRANSLATION_STYLE_OPTIONS:
            return TRANSLATION_STYLE_OPTIONS[choice]
        if choice == "8":
            custom = prompt_required("Describe the translation style you want: ")
            return "Custom", custom
        print("Please enter a number from 1 to 8.")


def prompt_chunk_size(default: int) -> int:
    print(
        "\nChunk size is measured in source tokens. Smaller chunks use more API "
        "calls; larger chunks provide more context but require a larger model context window."
    )
    while True:
        raw = input(f"Maximum source tokens per chunk [{default:,}]: ").strip()
        if not raw:
            return default
        try:
            value = int(raw.replace(",", "").replace("_", ""))
        except ValueError:
            print("Please enter a whole number, such as 5500.")
            continue
        if not 500 <= value <= 100_000:
            print("Please choose a value from 500 to 100,000 tokens.")
            continue
        return value


def prompt_api_settings() -> tuple[str, str, str]:
    """Prompt for one supported provider and a private, runtime-only API key."""
    print("\nLLM provider:")
    print("  1. OpenAI API (default)")
    print("  2. Virginia Tech ARC LLM API")

    while True:
        choice = input("Choose provider [1]: ").strip() or "1"
        if choice in {"1", "2"}:
            break
        print("Please enter 1 or 2.")

    if choice == "1":
        model = input(f"Model [{MODEL}]: ").strip() or MODEL
        print(
            "Enter an OpenAI Platform API key. The key is hidden while typed "
            "and is not saved by this script."
        )
        while True:
            api_key = getpass.getpass("OpenAI API key (input hidden): ").strip()
            if api_key:
                break
            print("An OpenAI API key is required.")
        return model, "", api_key

    base_url = "https://llm-api.arc.vt.edu/api/v1"
    arc_models = {
        "1": "gpt-oss-120b",
        "2": "DeepSeek-V4-Flash",
        "3": "GLM-5.2",
        "4": "Kimi-K3",
    }
    print("\nVirginia Tech ARC model:")
    for number, name in arc_models.items():
        default_marker = " (default)" if number == "1" else ""
        print(f"  {number}. {name}{default_marker}")
    print("  5. Enter another ARC model ID")
    while True:
        model_choice = input("Choose ARC model [1]: ").strip() or "1"
        if model_choice in arc_models:
            model = arc_models[model_choice]
            break
        if model_choice == "5":
            model = prompt_required("ARC model name/ID: ")
            break
        print("Please enter a number from 1 to 5.")

    print(
        "Create a personal key at https://llm.arc.vt.edu under "
        "User profile > Settings > Account > API keys."
    )
    print("The key is hidden while typed and is not saved by this script.")
    while True:
        api_key = getpass.getpass("ARC personal API key (input hidden): ").strip()
        if api_key:
            break
        print("An ARC personal API key is required.")
    return model, base_url, api_key


def prompt_annotation_settings() -> tuple[bool, str, str]:
    """Configure optional named-entity annotations and endnotes."""
    if not prompt_yes_no("Add named-entity annotations and endnotes?", default=False):
        return False, "Annotations disabled", ""

    print("\nWhat should be annotated?")
    print("  1. All named entities and proper nouns (default)")
    print("  2. People, places, and organizations only")
    print("  3. People only")
    print("  4. Custom scope")
    scopes = {
        "1": "All named entities and proper nouns",
        "2": "Named people, places, organizations, and institutions",
        "3": "Named people",
    }
    while True:
        choice = input("Choose annotation scope [1]: ").strip() or "1"
        if choice in scopes:
            scope = scopes[choice]
            break
        if choice == "4":
            scope = prompt_required("Describe what should be annotated: ")
            break
        print("Please enter a number from 1 to 4.")

    print("\nAnnotation detail:")
    print("  1. Brief — one concise sentence (default)")
    print("  2. Standard — one to three sentences")
    print("  3. Detailed — up to one short paragraph")
    print("  4. Custom guidance")
    guidance_options = {
        "1": "Use one concise sentence per annotation.",
        "2": "Use one to three concise sentences per annotation.",
        "3": "Use up to one short paragraph explaining identity, context, and relevance without speculation.",
    }
    while True:
        choice = input("Choose annotation detail [1]: ").strip() or "1"
        if choice in guidance_options:
            guidance = guidance_options[choice]
            break
        if choice == "4":
            guidance = prompt_required("Enter annotation guidance: ")
            break
        print("Please enter a number from 1 to 4.")

    return True, scope, guidance


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")

    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            from charset_normalizer import from_bytes
        except ImportError as exc:
            raise RuntimeError(
                "The file is not UTF-8. Install charset-normalizer or convert the "
                "file to UTF-8 first."
            ) from exc

        best = from_bytes(raw).best()
        if best is None:
            raise RuntimeError("Could not determine the text file's encoding.")
        return str(best)


def markdown_from_docx(path: Path) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError(
            "Reading .docx files requires python-docx. Install it with:\n"
            "  python3 -m pip install -U python-docx"
        ) from exc

    document = Document(path)
    output: list[str] = []

    for paragraph in document.paragraphs:
        if not paragraph.text.strip():
            output.append("")
            continue

        parts: list[str] = []
        for run in paragraph.runs:
            text = run.text
            if not text:
                continue
            if run.bold and run.italic:
                text = f"***{text}***"
            elif run.bold:
                text = f"**{text}**"
            elif run.italic:
                text = f"*{text}*"
            parts.append(text)

        rendered = "".join(parts).strip() or paragraph.text.strip()
        style_name = (paragraph.style.name or "").casefold()
        heading_match = re.match(r"heading\s+(\d+)", style_name)
        if heading_match:
            level = min(max(int(heading_match.group(1)), 1), 6)
            rendered = f"{'#' * level} {rendered}"
        elif "list bullet" in style_name:
            rendered = f"- {rendered}"
        elif "list number" in style_name:
            rendered = f"1. {rendered}"

        output.append(rendered)

    return "\n\n".join(part for part in output if part is not None).strip()


def read_source(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        text = markdown_from_docx(path)
    else:
        text = read_text_file(path)

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise RuntimeError("The source file contains no readable text.")
    return text


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


# ------------------------------ Tokenization ------------------------------ #


def get_encoding() -> Any:
    # o200k_base is suitable for recent multilingual GPT models. Using the
    # underlying encoding avoids failures when a newly released model name has
    # not yet been added to an older local tiktoken model map.
    return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str, encoding: Any) -> int:
    return len(encoding.encode(text, disallowed_special=()))


def tail_by_tokens(text: str, max_tokens: int, encoding: Any) -> str:
    if not text:
        return ""
    tokens = encoding.encode(text, disallowed_special=())
    if len(tokens) <= max_tokens:
        return text
    return encoding.decode(tokens[-max_tokens:])


def find_natural_break(candidate: str) -> int | None:
    """Find a late sentence/line boundary in a decoded token prefix."""
    if len(candidate) < 80:
        return None

    start = int(len(candidate) * 0.60)
    region = candidate[start:]
    patterns = [
        r"\n+",
        r"[.!?。！？؟]\s+",
        r"[;:；：]\s+",
        r"[,，]\s+",
    ]
    best: int | None = None
    for pattern in patterns:
        matches = list(re.finditer(pattern, region))
        if matches:
            position = start + matches[-1].end()
            if best is None or position > best:
                best = position
    return best


def split_oversized_block(block: str, max_tokens: int, encoding: Any) -> list[str]:
    pieces: list[str] = []
    remaining = block

    while count_tokens(remaining, encoding) > max_tokens:
        tokens = encoding.encode(remaining, disallowed_special=())
        candidate = encoding.decode(tokens[:max_tokens])
        boundary = find_natural_break(candidate)
        if boundary is None or boundary < 1:
            boundary = len(candidate)

        piece = remaining[:boundary].rstrip()
        if not piece:
            # Defensive fallback for an unexpected token/character boundary.
            piece = candidate
            boundary = len(candidate)

        pieces.append(piece)
        remaining = remaining[boundary:].lstrip()

    if remaining.strip():
        pieces.append(remaining.strip())
    return pieces


def paragraph_blocks(text: str) -> list[str]:
    return [
        block.strip()
        for block in re.split(r"\n[ \t]*\n+", text)
        if block.strip()
    ]


def build_chunks(text: str, max_tokens: int, encoding: Any) -> list[TextChunk]:
    blocks: list[str] = []
    for block in paragraph_blocks(text):
        if count_tokens(block, encoding) <= max_tokens:
            blocks.append(block)
        else:
            blocks.extend(split_oversized_block(block, max_tokens, encoding))

    chunks: list[TextChunk] = []
    current: list[str] = []
    current_tokens = 0

    for block in blocks:
        block_tokens = count_tokens(block, encoding)
        separator_tokens = 1 if current else 0
        if current and current_tokens + separator_tokens + block_tokens > max_tokens:
            chunk_text = "\n\n".join(current).strip()
            chunks.append(
                TextChunk(
                    index=len(chunks),
                    text=chunk_text,
                    token_count=count_tokens(chunk_text, encoding),
                )
            )
            current = [block]
            current_tokens = block_tokens
        else:
            current.append(block)
            current_tokens += separator_tokens + block_tokens

    if current:
        chunk_text = "\n\n".join(current).strip()
        chunks.append(
            TextChunk(
                index=len(chunks),
                text=chunk_text,
                token_count=count_tokens(chunk_text, encoding),
            )
        )

    if not chunks:
        raise RuntimeError("No translation chunks could be created.")
    return chunks


# --------------------------- Entity/endnote logic ------------------------- #


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


# ------------------------------- API logic -------------------------------- #


def load_api_client() -> OpenAI:
    env_path = Path.home() / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = API_KEY_OVERRIDE or os.getenv("OPENAI_API_KEY")
    if not api_key and not API_BASE_URL:
        raise RuntimeError(
            f"OPENAI_API_KEY was not found. Add it to {env_path} like this:\n"
            "OPENAI_API_KEY=your_key_here\n"
            "or choose the option to enter a key interactively."
        )
    client_options: dict[str, Any] = dict(
        api_key=api_key or "not-required",
        timeout=API_TIMEOUT_SECONDS,
        max_retries=0,
    )
    if API_BASE_URL:
        client_options["base_url"] = API_BASE_URL
    return OpenAI(**client_options)


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


def retryable_status(error: APIStatusError) -> bool:
    status = getattr(error, "status_code", None)
    return status in {408, 409, 429} or (isinstance(status, int) and status >= 500)


def parse_json_output(output_text: str) -> dict[str, Any]:
    """Parse JSON returned directly or in a Markdown code fence."""
    cleaned = output_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = cleaned.removesuffix("```").rstrip()
    return validate_translation_payload(json.loads(cleaned))


def translate_chunk(
    client: OpenAI,
    prompt: str,
    chunk_number: int,
    chunk_count: int,
) -> dict[str, Any]:
    last_error: Exception | None = None

    for attempt in range(1, MAX_API_ATTEMPTS + 1):
        try:
            request_options: dict[str, Any] = dict(
                model=MODEL,
                instructions=(
                    SYSTEM_INSTRUCTIONS
                    + "\n\nTRANSLATION STYLE SELECTED BY THE USER\n"
                    + TRANSLATION_STYLE_INSTRUCTION
                    + (
                        f"\n\nANNOTATION CONFIGURATION\nAnnotations are enabled. Annotate only: {ANNOTATION_SCOPE}. {ANNOTATION_NOTE_GUIDANCE}"
                        if ANNOTATIONS_ENABLED
                        else "\n\nANNOTATION CONFIGURATION\nAnnotations are disabled. Do not add ENTITY markers or explanatory annotations. Return entities as an empty list."
                    )
                ),
                input=prompt,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "translation_chunk",
                        "description": (
                            "A translated HTML fragment, entity annotations, and "
                            "brief continuity notes."
                        ),
                        "strict": True,
                        "schema": TRANSLATION_SCHEMA,
                    }
                },
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
            )
            if API_BASE_URL:
                # ARC implements OpenAI Chat Completions, not Responses.
                instructions = request_options["instructions"]
                response = client.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": prompt + "\n\nReturn only the required JSON object."},
                    ],
                    max_tokens=min(MAX_OUTPUT_TOKENS, 8_000),
                )
                output_text = response.choices[0].message.content or ""
            else:
                request_options["reasoning"] = {"effort": REASONING_EFFORT}
                response = client.responses.create(**request_options)
                if getattr(response, "status", None) == "incomplete":
                    details = getattr(response, "incomplete_details", None)
                    raise RuntimeError(f"The response was incomplete: {details}")
                output_text = getattr(response, "output_text", "")

            if not output_text:
                raise RuntimeError("The API returned no text output.")
            return parse_json_output(output_text)

        except RateLimitError as exc:
            last_error = exc
        except (APIConnectionError, APITimeoutError) as exc:
            last_error = exc
        except APIStatusError as exc:
            if not retryable_status(exc):
                raise
            last_error = exc
        except (json.JSONDecodeError, ValueError, RuntimeError) as exc:
            last_error = exc

        if attempt == MAX_API_ATTEMPTS:
            break

        delay = min(90.0, (2 ** (attempt - 1)) + random.uniform(0.5, 2.5))
        print(
            f"  Attempt {attempt} failed for chunk {chunk_number}/{chunk_count}: "
            f"{last_error}"
        )
        print(f"  Retrying in {delay:.1f} seconds...")
        time.sleep(delay)

    raise RuntimeError(
        f"Chunk {chunk_number}/{chunk_count} failed after {MAX_API_ATTEMPTS} "
        f"attempts: {last_error}"
    )


# --------------------------- Checkpoint and output ------------------------ #


def safe_filename_fragment(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return value[:50] or "translated"


def default_output_path(source_path: Path, target_language: str) -> Path:
    suffix = safe_filename_fragment(target_language)
    return source_path.with_name(f"{source_path.stem}_{suffix}_translation.html")


def prompt_output_path(default_path: Path) -> Path:
    raw = input(f"Output HTML file [{default_path}]: ").strip()
    if not raw:
        path = default_path
    else:
        path = Path(clean_dragged_path(raw)).expanduser()
        if path.suffix.lower() not in {".html", ".htm"}:
            path = path.with_suffix(".html")
    return path.resolve()


def checkpoint_compatible(
    state: dict[str, Any],
    *,
    source_hash: str,
    source_path: Path,
    output_path: Path,
    metadata: WorkMetadata,
    chunk_count: int,
) -> bool:
    return all(
        [
            state.get("state_version") == STATE_VERSION,
            state.get("chunking_version") == CHUNKING_VERSION,
            state.get("output_format") == "html",
            state.get("source_sha256") == source_hash,
            state.get("source_path") == str(source_path),
            state.get("output_path") == str(output_path),
            state.get("model") == MODEL,
            state.get("api_base_url") == (API_BASE_URL or "OpenAI"),
            state.get("reasoning_effort") == REASONING_EFFORT,
            state.get("translation_style") == TRANSLATION_STYLE,
            state.get("translation_style_instruction")
            == TRANSLATION_STYLE_INSTRUCTION,
            state.get("annotations_enabled") == ANNOTATIONS_ENABLED,
            state.get("annotation_scope") == ANNOTATION_SCOPE,
            state.get("annotation_note_guidance") == ANNOTATION_NOTE_GUIDANCE,
            state.get("metadata") == metadata.as_dict(),
            state.get("chunk_count") == chunk_count,
            state.get("max_source_tokens_per_chunk")
            == MAX_SOURCE_TOKENS_PER_CHUNK,
        ]
    )


def initial_state(
    *,
    source_hash: str,
    source_path: Path,
    output_path: Path,
    metadata: WorkMetadata,
    chunk_count: int,
) -> dict[str, Any]:
    return {
        "state_version": STATE_VERSION,
        "chunking_version": CHUNKING_VERSION,
        "output_format": "html",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_sha256": source_hash,
        "source_path": str(source_path),
        "output_path": str(output_path),
        "model": MODEL,
        "api_base_url": API_BASE_URL or "OpenAI",
        "reasoning_effort": REASONING_EFFORT,
        "translation_style": TRANSLATION_STYLE,
        "translation_style_instruction": TRANSLATION_STYLE_INSTRUCTION,
        "annotations_enabled": ANNOTATIONS_ENABLED,
        "annotation_scope": ANNOTATION_SCOPE,
        "annotation_note_guidance": ANNOTATION_NOTE_GUIDANCE,
        "metadata": metadata.as_dict(),
        "chunk_count": chunk_count,
        "max_source_tokens_per_chunk": MAX_SOURCE_TOKENS_PER_CHUNK,
        "next_chunk": 0,
        "entities": [],
        "continuity_notes": "",
        "warnings": [],
    }


def prepare_workspace(
    *,
    source_hash: str,
    source_path: Path,
    output_path: Path,
    metadata: WorkMetadata,
    chunk_count: int,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    workspace = output_path.parent / f"{output_path.stem}_translation_work"
    chunks_dir = workspace / "chunks"
    state_path = workspace / "state.json"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read checkpoint {state_path}: {exc}") from exc

        if checkpoint_compatible(
            state,
            source_hash=source_hash,
            source_path=source_path,
            output_path=output_path,
            metadata=metadata,
            chunk_count=chunk_count,
        ):
            next_chunk = int(state.get("next_chunk", 0))
            if next_chunk == 0:
                return workspace, chunks_dir, state_path, state
            if prompt_yes_no(
                f"A checkpoint has {next_chunk}/{chunk_count} chunks complete. Resume?",
                default=True,
            ):
                return workspace, chunks_dir, state_path, state

        if not prompt_yes_no(
            "An incompatible or declined checkpoint exists. Replace it and start over?",
            default=False,
        ):
            raise SystemExit("Translation cancelled; the existing checkpoint was preserved.")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archived = workspace.with_name(workspace.name + f"_archived_{timestamp}")
        workspace.rename(archived)
        print(f"Old checkpoint archived at: {archived}")
        chunks_dir.mkdir(parents=True, exist_ok=True)

    state = initial_state(
        source_hash=source_hash,
        source_path=source_path,
        output_path=output_path,
        metadata=metadata,
        chunk_count=chunk_count,
    )
    atomic_write_json(state_path, state)
    return workspace, chunks_dir, state_path, state


def chunk_file_path(chunks_dir: Path, index: int) -> Path:
    return chunks_dir / f"chunk_{index + 1:05d}.html"


def read_last_completed_target(chunks_dir: Path, next_chunk: int) -> str:
    if next_chunk <= 0:
        return ""
    path = chunk_file_path(chunks_dir, next_chunk - 1)
    if not path.exists():
        raise RuntimeError(
            f"Checkpoint says chunk {next_chunk} is complete, but {path} is missing."
        )
    return path.read_text(encoding="utf-8")


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


def strip_document_wrappers(fragment: str) -> str:
    "Remove accidental full-document wrappers from an API-produced fragment."
    fragment = re.sub(r"<!DOCTYPE[^>]*>", "", fragment, flags=re.I)
    fragment = re.sub(
        r"</?(?:html|head|body|main)(?:\s[^>]*)?>",
        "",
        fragment,
        flags=re.I,
    )
    fragment = re.sub(
        r"<script\b[^>]*>.*?</script>",
        "",
        fragment,
        flags=re.I | re.S,
    )
    fragment = re.sub(
        r"<style\b[^>]*>.*?</style>",
        "",
        fragment,
        flags=re.I | re.S,
    )
    return fragment.strip()


def build_document(
    *,
    chunks_dir: Path,
    chunk_count: int,
    metadata: WorkMetadata,
    registry: EntityRegistry,
) -> str:
    translated_chunks: list[str] = []
    for index in range(chunk_count):
        path = chunk_file_path(chunks_dir, index)
        if not path.exists():
            raise RuntimeError(f"Cannot assemble output; missing translated chunk: {path}")
        fragment = strip_document_wrappers(path.read_text(encoding="utf-8"))
        translated_chunks.append(
            f'<section class="translation-chunk" data-chunk="{index + 1}">\n'
            f'{fragment}\n'
            f'</section>'
        )

    title = escape(clean_heading_text(metadata.title or "Translated Work"))
    author_html = ""
    if metadata.author:
        author_html = (
            f'\n      <p class="author">'
            f'{escape(clean_heading_text(metadata.author))}</p>'
        )

    lang = infer_html_lang(metadata.target_language)
    direction = html_direction(lang)
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    source_language = escape(metadata.source_language)
    target_language = escape(metadata.target_language)
    model = escape(MODEL)
    endnotes = registry.endnotes_html(localized_endnotes_heading(lang))
    endnotes_html = f"\n\n    {endnotes}" if endnotes else ""
    body = "\n\n".join(translated_chunks)

    return f'''<!DOCTYPE html>
<html lang="{lang}" dir="{direction}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="generator" content="Long-Work Translator using {model}">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light dark;
      font-family: Georgia, "Times New Roman", serif;
      line-height: 1.65;
    }}
    body {{
      margin: 0;
      background: Canvas;
      color: CanvasText;
    }}
    main {{
      max-width: 48rem;
      margin: 0 auto;
      padding: 3rem 1.5rem 5rem;
    }}
    .document-header {{
      margin-bottom: 3rem;
      padding-bottom: 1.5rem;
      border-bottom: 1px solid color-mix(in srgb, CanvasText 25%, transparent);
    }}
    h1, h2, h3, h4, h5, h6 {{
      line-height: 1.2;
    }}
    .document-header h1 {{
      margin: 0;
    }}
    .author {{
      margin: 0.75rem 0 0;
      font-style: italic;
    }}
    p, blockquote, ul, ol, table, pre {{
      margin-block: 1em;
    }}
    blockquote {{
      margin-inline: 1.5rem;
      padding-inline-start: 1rem;
      border-inline-start: 0.2rem solid currentColor;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 0.45rem 0.6rem;
      border: 1px solid color-mix(in srgb, CanvasText 30%, transparent);
      vertical-align: top;
    }}
    pre {{
      overflow-x: auto;
      padding: 1rem;
      border: 1px solid color-mix(in srgb, CanvasText 25%, transparent);
    }}
    a {{
      color: LinkText;
    }}
    .translation-chunk {{
      display: contents;
    }}
    .endnote-reference {{
      margin-inline-start: 0.12em;
      font-size: 0.72em;
      line-height: 0;
    }}
    .endnote-reference a,
    .endnote-backlink {{
      text-decoration: none;
    }}
    .endnotes {{
      margin-top: 4rem;
      padding-top: 1.5rem;
      border-top: 1px solid color-mix(in srgb, CanvasText 25%, transparent);
      font-size: 0.94em;
    }}
    .endnotes li {{
      padding-inline-start: 0.35rem;
    }}
    .entity-category {{
      font-style: italic;
    }}
    @media print {{
      :root {{ color-scheme: light; }}
      main {{ max-width: none; padding: 0; }}
      a {{ color: inherit; }}
      .translation-chunk {{ display: contents; }}
    }}
  </style>
</head>
<body>
  <!--
    Translated from {source_language} into {target_language} with {model}.
    Generated {generated}.
    Entity endnotes are AI-generated and should be fact-checked for publication.
  -->
  <main>
    <header class="document-header">
      <h1>{title}</h1>{author_html}
    </header>

    <article class="translated-work">
{body}
    </article>{endnotes_html}
  </main>
</body>
</html>
'''


# --------------------------------- Main ---------------------------------- #


def main() -> int:
    global MODEL
    global API_BASE_URL
    global API_KEY_OVERRIDE
    global MAX_SOURCE_TOKENS_PER_CHUNK
    global TRANSLATION_STYLE
    global TRANSLATION_STYLE_INSTRUCTION
    global ANNOTATIONS_ENABLED
    global ANNOTATION_SCOPE
    global ANNOTATION_NOTE_GUIDANCE

    print("Long-Work Translator")
    print(f"Default model: {MODEL}")

    TRANSLATION_STYLE, TRANSLATION_STYLE_INSTRUCTION = prompt_translation_style()
    MAX_SOURCE_TOKENS_PER_CHUNK = prompt_chunk_size(
        MAX_SOURCE_TOKENS_PER_CHUNK
    )
    ANNOTATIONS_ENABLED, ANNOTATION_SCOPE, ANNOTATION_NOTE_GUIDANCE = (
        prompt_annotation_settings()
    )
    MODEL, API_BASE_URL, API_KEY_OVERRIDE = prompt_api_settings()

    print("\nTranslation configuration:")
    print(f"  Style: {TRANSLATION_STYLE}")
    print(f"  Chunk size: {MAX_SOURCE_TOKENS_PER_CHUNK:,} source tokens")
    print(f"  Annotations: {'on' if ANNOTATIONS_ENABLED else 'off'}")
    if ANNOTATIONS_ENABLED:
        print(f"  Annotation scope: {ANNOTATION_SCOPE}")
        print(f"  Annotation detail: {ANNOTATION_NOTE_GUIDANCE}")
    print(f"  Model: {MODEL}")
    print(f"  Endpoint: {API_BASE_URL or 'OpenAI'}")
    print("  API key: entered interactively (not saved)")
    print()

    source_path = prompt_existing_file()
    title_default = source_path.stem.replace("_", " ").strip()
    title = input(f"Work title [{title_default}]: ").strip() or title_default
    author = input("Author (optional): ").strip()
    source_language = prompt_required("Source language: ")
    source_details = input(
        "Source dialect, period, genre, or register (optional): "
    ).strip()
    target_language = prompt_required("Target language: ")
    target_details = input(
        "Target regional variety or additional style guidance (optional): "
    ).strip()

    metadata = WorkMetadata(
        title=title,
        author=author,
        source_language=source_language,
        source_details=source_details,
        target_language=target_language,
        target_details=target_details,
    )

    output_path = prompt_output_path(default_output_path(source_path, target_language))

    try:
        source_text = read_source(source_path)
        encoding = get_encoding()
        chunks = build_chunks(
            source_text,
            max_tokens=MAX_SOURCE_TOKENS_PER_CHUNK,
            encoding=encoding,
        )
        source_hash = sha256_text(source_text)
    except Exception as exc:
        print(f"\nCould not prepare the source: {exc}", file=sys.stderr)
        return 1

    source_words = len(re.findall(r"\S+", source_text))
    source_tokens = count_tokens(source_text, encoding)
    print("\nSource prepared:")
    print(f"  Approximate whitespace-delimited words: {source_words:,}")
    print(f"  Tokens: {source_tokens:,}")
    print(f"  Translation chunks: {len(chunks):,}")
    print(f"  Output: {output_path}")
    if ANNOTATIONS_ENABLED:
        print("  Named entities will be annotated with endnotes.")
    else:
        print("  Annotations and endnotes are off.")

    if not prompt_yes_no("Begin translation?", default=True):
        print("Translation cancelled.")
        return 0

    try:
        client = load_api_client()
        workspace, chunks_dir, state_path, state = prepare_workspace(
            source_hash=source_hash,
            source_path=source_path,
            output_path=output_path,
            metadata=metadata,
            chunk_count=len(chunks),
        )
        registry = EntityRegistry(state.get("entities", []))
        next_chunk = int(state.get("next_chunk", 0))
        previous_target = read_last_completed_target(chunks_dir, next_chunk)
        previous_continuity_notes = str(state.get("continuity_notes", ""))

        if next_chunk >= len(chunks):
            print("All chunks are already translated; rebuilding the final document.")

        for index in range(next_chunk, len(chunks)):
            chunk = chunks[index]
            previous_source = chunks[index - 1].text if index > 0 else ""
            previous_source_tail = tail_by_tokens(
                previous_source, CONTEXT_TAIL_TOKENS, encoding
            )
            previous_target_tail = tail_by_tokens(
                previous_target, CONTEXT_TAIL_TOKENS, encoding
            )

            print(
                f"\nTranslating chunk {index + 1}/{len(chunks)} "
                f"({chunk.token_count:,} source tokens)..."
            )
            prompt = build_chunk_prompt(
                chunk=chunk,
                chunk_count=len(chunks),
                metadata=metadata,
                registry=registry,
                previous_source_tail=previous_source_tail,
                previous_target_tail=previous_target_tail,
                previous_continuity_notes=previous_continuity_notes,
            )
            payload = translate_chunk(
                client,
                prompt,
                chunk_number=index + 1,
                chunk_count=len(chunks),
            )

            if ANNOTATIONS_ENABLED:
                processed, warnings = process_entity_markers(
                    payload["translated_html"], payload["entities"], registry
                )
            else:
                processed = ENTITY_MARKER_RE.sub(r"\2", payload["translated_html"]).strip()
                warnings = []
            if not processed:
                raise RuntimeError(f"Chunk {index + 1} produced no translated text.")

            chunk_path = chunk_file_path(chunks_dir, index)
            atomic_write_text(chunk_path, processed + "\n")
            previous_target = processed
            previous_continuity_notes = payload["continuity_notes"].strip()

            state["next_chunk"] = index + 1
            state["entities"] = registry.entries
            state["continuity_notes"] = previous_continuity_notes
            state["updated_at"] = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            for warning in warnings:
                state.setdefault("warnings", []).append(
                    f"Chunk {index + 1}: {warning}"
                )
                print(f"  Warning: {warning}")
            atomic_write_json(state_path, state)
            if ANNOTATIONS_ENABLED:
                print(
                    f"  Saved chunk {index + 1}; "
                    f"{len(registry.entries)} unique named entities recorded."
                )
            else:
                print(f"  Saved chunk {index + 1}.")

        final_html = build_document(
            chunks_dir=chunks_dir,
            chunk_count=len(chunks),
            metadata=metadata,
            registry=registry,
        )
        atomic_write_text(output_path, final_html)

        state["completed"] = True
        state["completed_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        state["updated_at"] = state["completed_at"]
        state["entities"] = registry.entries
        atomic_write_json(state_path, state)

        print("\nTranslation complete.")
        print(f"HTML file: {output_path}")
        print(f"Checkpoint/work files: {workspace}")
        if ANNOTATIONS_ENABLED:
            print(f"Endnotes: {len(registry.entries):,}")
        if state.get("warnings"):
            print(
                f"Warnings recorded: {len(state['warnings'])}. "
                f"Review {state_path} for details."
            )
        if ANNOTATIONS_ENABLED:
            print(
                "Before publication, review the translation and fact-check the "
                "AI-generated entity notes."
            )
        else:
            print("Before publication, review the translation.")
        return 0

    except KeyboardInterrupt:
        print(
            "\nTranslation interrupted. Completed chunks were checkpointed; "
            "run the script again to resume.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        print(f"\nTranslation stopped: {exc}", file=sys.stderr)
        print(
            "Completed chunks remain checkpointed. Run the script again to resume.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
