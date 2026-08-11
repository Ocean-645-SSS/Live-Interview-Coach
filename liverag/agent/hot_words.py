"""STT session hot-word management.

``HOT_WORDS.md`` accepts the legacy ``word|level`` format and the extended
``word|level|domains|aliases|misrecognitions`` format.  Only canonical words
and their levels are serialized into Volcengine's ``corpus.context`` payload;
the remaining columns support deterministic session selection and later text
normalization.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from liverag.interview.schemas import InterviewPlan


_HOT_WORDS_MD = Path(__file__).parents[2] / "docs" / "HOT_WORDS.md"
_MAX_DEFAULT_HOT_WORDS = 100
_DEFAULT_MIN_SESSION_HOT_WORDS = 30
_DEFAULT_MAX_SESSION_HOT_WORDS = 80
_FIXED_SESSION_CORE_WORDS = (
    "Agent",
    "LLM",
    "RAG",
    "MCP",
    "Prompt",
    "Transformer",
    "Python",
    "Java",
    "MySQL",
    "Redis",
    "Docker",
    "Git",
    "FastAPI",
    "Spring Boot",
    "Kafka",
    "PostgreSQL",
    "SQLAlchemy",
    "LangChain",
    "LangGraph",
    "Embedding",
)


@dataclass(frozen=True, slots=True)
class HotWordEntry:
    """A canonical ASR hot word and optional selection metadata."""

    word: str
    level: int
    domains: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    misrecognitions: tuple[str, ...] = ()


def load_hot_word_entries(path: Path | None = None) -> list[HotWordEntry]:
    """Load compatible hot-word entries from the configured Markdown file."""

    path = path or _HOT_WORDS_MD
    if not path.exists():
        return []
    return _parse_hot_word_entries_md(path.read_text(encoding="utf-8"))


def load_hot_words(path: Path | None = None) -> str:
    """Load the legacy default hot-word payload without changing its limit."""

    return serialize_hot_words(load_hot_word_entries(path)[:_MAX_DEFAULT_HOT_WORDS])


def build_session_hot_words(
    plan: InterviewPlan,
    path: Path | None = None,
    *,
    min_words: int = _DEFAULT_MIN_SESSION_HOT_WORDS,
    max_words: int = _DEFAULT_MAX_SESSION_HOT_WORDS,
) -> str:
    """Select canonical hot words relevant to one frozen interview plan."""

    entries = load_hot_word_entries(path)
    selected = select_session_hot_words(
        plan,
        entries,
        min_words=min_words,
        max_words=max_words,
    )
    return serialize_hot_words(selected)


def select_session_hot_words(
    plan: InterviewPlan,
    entries: Sequence[HotWordEntry],
    *,
    min_words: int = _DEFAULT_MIN_SESSION_HOT_WORDS,
    max_words: int = _DEFAULT_MAX_SESSION_HOT_WORDS,
    fixed_core_words: Sequence[str] = _FIXED_SESSION_CORE_WORDS,
) -> list[HotWordEntry]:
    """Return a deterministic, de-duplicated session hot-word list.

    Plan matches are preferred.  Fixed core words are appended afterwards and
    count toward ``max_words``.  When a concise plan has too few direct
    matches, the remaining slots required by ``min_words`` fall back to the
    highest-weighted global entries.
    """

    unique_entries = _unique_entries(entries)
    plan_text = plan.model_dump_json().casefold()
    core_keys = {_word_key(word) for word in fixed_core_words}
    core_entries = [entry for entry in unique_entries if _word_key(entry.word) in core_keys]
    core_entries = core_entries[:max_words]
    core_entry_keys = {_word_key(entry.word) for entry in core_entries}
    dynamic_capacity = max(0, max_words - len(core_entries))

    ranked = sorted(
        (
            (_entry_relevance_score(plan_text, entry), index, entry)
            for index, entry in enumerate(unique_entries)
            if _word_key(entry.word) not in core_entry_keys
        ),
        key=lambda item: (-item[0], -item[2].level, item[1]),
    )

    selected = [entry for score, _, entry in ranked if score > 0][:dynamic_capacity]
    selected_keys = {_word_key(entry.word) for entry in selected}
    required_dynamic_count = max(0, min_words - len(core_entries))
    if len(selected) < required_dynamic_count:
        for _, _, entry in ranked:
            key = _word_key(entry.word)
            if key in selected_keys:
                continue
            selected.append(entry)
            selected_keys.add(key)
            if len(selected) >= min(required_dynamic_count, dynamic_capacity):
                break

    return selected + core_entries


def serialize_hot_words(entries: Sequence[HotWordEntry]) -> str:
    """Serialize canonical words only into Volcengine's context JSON."""

    if not entries:
        return ""
    return json.dumps(
        {"hotwords": [{"word": entry.word, "level": entry.level} for entry in entries]},
        ensure_ascii=False,
    )


def _parse_hot_word_entries_md(content: str) -> list[HotWordEntry]:
    """Parse compatible ``word|level[|domains|aliases|misrecognitions]`` lines."""

    entries: list[HotWordEntry] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        columns = [column.strip() for column in line.split("|")]
        if not 2 <= len(columns) <= 5:
            continue
        word = columns[0]
        try:
            level = int(columns[1])
        except ValueError:
            continue
        if not word or not 1 <= level <= 10:
            continue
        metadata = [_split_metadata(column) for column in columns[2:]]
        metadata.extend([()] * (3 - len(metadata)))
        entries.append(
            HotWordEntry(
                word=word,
                level=level,
                domains=metadata[0],
                aliases=metadata[1],
                misrecognitions=metadata[2],
            )
        )
    return entries


def _parse_hot_words_md(content: str) -> list[tuple[str, int]]:
    """Return legacy ``(word, level)`` values for existing internal callers."""

    return [(entry.word, entry.level) for entry in _parse_hot_word_entries_md(content)]


def _split_metadata(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split("、") if item.strip())


def _unique_entries(entries: Sequence[HotWordEntry]) -> list[HotWordEntry]:
    seen: set[str] = set()
    unique: list[HotWordEntry] = []
    for entry in entries:
        key = _word_key(entry.word)
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


def _entry_relevance_score(plan_text: str, entry: HotWordEntry) -> int:
    score = 0
    if _contains_term(plan_text, entry.word):
        score += 12
    score += sum(8 for alias in entry.aliases if _contains_term(plan_text, alias))
    score += sum(4 for domain in entry.domains if _contains_term(plan_text, domain))
    return score


def _contains_term(text: str, term: str) -> bool:
    normalized = term.strip().casefold()
    if not normalized:
        return False
    if normalized.isascii() and normalized.replace(" ", "").isalnum() and len(normalized) <= 4:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text))
    return normalized in text


def _word_key(word: str) -> str:
    return word.strip().casefold()


def build_corpus_context(hot_words_json: str) -> dict[str, dict[str, str]]:
    """Build the optional ``corpus`` field for a Full Client Request."""

    if not hot_words_json.strip():
        return {}
    return {"corpus": {"context": hot_words_json}}


def inject_hot_words_into_initial_request(
    initial: bytes | bytearray,
    hot_words_json: str,
) -> bytes:
    """Inject hot words into a Volcengine Full Client Request packet."""

    if not hot_words_json:
        return bytes(initial)

    import gzip

    header_size = initial[0] & 0x0F
    offset = header_size * 4
    sequence = int.from_bytes(initial[offset : offset + 4], "big", signed=True)
    offset += 4
    payload_size = int.from_bytes(initial[offset : offset + 4], "big", signed=False)
    offset += 4
    compressed = initial[offset : offset + payload_size]
    json_bytes = gzip.decompress(bytes(compressed))
    payload = json.loads(json_bytes.decode("utf-8"))
    payload["corpus"] = {"context": hot_words_json}
    new_json = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    new_compressed = gzip.compress(new_json)
    result = bytearray(initial[: header_size * 4])
    result.extend(sequence.to_bytes(4, "big", signed=True))
    result.extend(len(new_compressed).to_bytes(4, "big"))
    result.extend(new_compressed)
    return bytes(result)


__all__ = [
    "HotWordEntry",
    "build_corpus_context",
    "build_session_hot_words",
    "inject_hot_words_into_initial_request",
    "load_hot_word_entries",
    "load_hot_words",
    "select_session_hot_words",
    "serialize_hot_words",
]
