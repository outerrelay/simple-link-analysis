"""Recording merges so they can be reviewed and undone."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from sla.app.database import session_scope
from sla.app.models import LOCAL_USER, MergeRecord
from sla.app.staging import json_safe


@dataclass(frozen=True)
class RecordedMerge:
    id: str
    survivor_id: str
    absorbed_id: str
    entity_type: str
    summary: str
    undone: bool
    merged_at: str


def record(
    *,
    merge_id: str,
    survivor_id: str,
    absorbed_id: str,
    entity_type: str,
    summary: str,
    snapshot: dict[str, Any],
    resolutions: dict[str, Any],
    user_id: str = LOCAL_USER,
) -> RecordedMerge:
    with session_scope() as session:
        row = MergeRecord(
            id=merge_id,
            survivor_id=survivor_id,
            absorbed_id=absorbed_id,
            entity_type=entity_type,
            summary=summary,
            snapshot=json_safe(snapshot),
            resolutions=json_safe(resolutions),
            user_id=user_id,
        )
        session.add(row)
        session.flush()
        return _to_record(row)


def get(merge_id: str) -> tuple[RecordedMerge, dict[str, Any]] | None:
    """Return the merge and its snapshot, or None."""
    with session_scope() as session:
        row = session.get(MergeRecord, merge_id)
        return (_to_record(row), row.snapshot) if row else None


def recent(limit: int = 25, user_id: str = LOCAL_USER) -> list[RecordedMerge]:
    with session_scope() as session:
        rows = session.scalars(
            select(MergeRecord)
            .where(MergeRecord.user_id == user_id)
            .order_by(MergeRecord.merged_at.desc())
            .limit(limit)
        ).all()
        return [_to_record(row) for row in rows]


def mark_undone(merge_id: str) -> bool:
    with session_scope() as session:
        row = session.get(MergeRecord, merge_id)
        if row is None or row.undone:
            return False
        row.undone = True
        row.undone_at = datetime.now(tz=UTC)
        return True


def _to_record(row: MergeRecord) -> RecordedMerge:
    return RecordedMerge(
        id=row.id,
        survivor_id=row.survivor_id,
        absorbed_id=row.absorbed_id,
        entity_type=row.entity_type,
        summary=row.summary,
        undone=row.undone,
        merged_at=row.merged_at.isoformat() if row.merged_at else "",
    )


def set_summary(merge_id: str, summary: str) -> None:
    """Fill in the summary once the merge has actually happened."""
    with session_scope() as session:
        row = session.get(MergeRecord, merge_id)
        if row is not None:
            row.summary = summary


def discard(merge_id: str) -> None:
    """Remove a record written for a merge that then failed."""
    with session_scope() as session:
        row = session.get(MergeRecord, merge_id)
        if row is not None:
            session.delete(row)
