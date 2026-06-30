from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from recodex.models import ParsedTranscript, TranscriptEvent


class SessionImporter(Protocol):
    """Source-specific importer for AI coding session transcripts."""

    name: str
    default_roots: tuple[Path, ...]
    supported_extensions: tuple[str, ...]

    def discover(self, roots: Iterable[Path]) -> list[Path]:
        """Return importable files under the provided roots."""
        ...

    def parse_file(self, path: Path) -> ParsedTranscript:
        """Parse one file into recodex's normalized transcript model."""
        ...


def retag_parsed_transcript(parsed: ParsedTranscript, source: str) -> ParsedTranscript:
    """Return a parsed transcript with source-specific provenance normalized."""
    session_metadata = dict(parsed.session.metadata)
    session_metadata["source_tool"] = source
    events: list[TranscriptEvent] = []
    for event in parsed.events:
        event_metadata = dict(event.metadata)
        event_metadata["source_tool"] = source
        events.append(replace(event, metadata=event_metadata))
    return ParsedTranscript(
        session=replace(
            parsed.session,
            tool=source,
            source=source,
            metadata=session_metadata,
        ),
        events=tuple(events),
    )
