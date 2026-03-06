"""Router for analytics endpoints.

Each endpoint performs SQL aggregation queries on the interaction data
populated by the ETL pipeline. All endpoints require a `lab` query
parameter to filter results by lab (e.g., "lab-01").
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, distinct
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models.item import ItemRecord
from app.models.learner import Learner
from app.models.interaction import InteractionLog

router = APIRouter()


def _parse_lab_number(lab: str) -> str:
    """Convert 'lab-04' to 'Lab 04' for title matching."""
    # e.g., "lab-04" -> "Lab 04"
    parts = lab.split("-")
    if len(parts) == 2:
        return f"Lab {parts[1].upper()}"
    return lab.upper()


@router.get("/scores")
async def get_scores(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Score distribution histogram for a given lab.

    TODO: Implement this endpoint.
    - Find the lab item by matching title (e.g. "lab-04" → title contains "Lab 04")
    - Find all tasks that belong to this lab (parent_id = lab.id)
    - Query interactions for these items that have a score
    - Group scores into buckets: "0-25", "26-50", "51-75", "76-100"
      using CASE WHEN expressions
    - Return a JSON array:
      [{"bucket": "0-25", "count": 12}, {"bucket": "26-50", "count": 8}, ...]
    - Always return all four buckets, even if count is 0
    """
    lab_title_prefix = _parse_lab_number(lab)

    # Find the lab item
    lab_item = (
        await session.exec(
            select(ItemRecord).where(
                ItemRecord.type == "lab",
                ItemRecord.title.like(f"%{lab_title_prefix}%"),
            )
        )
    ).first()

    if not lab_item:
        return []

    # Get all task item_ids for this lab
    tasks_result = (
        await session.exec(
            select(ItemRecord.id).where(ItemRecord.parent_id == lab_item.id)
        )
    ).all()
    task_ids = list(tasks_result)

    if not task_ids:
        return [
            {"bucket": "0-25", "count": 0},
            {"bucket": "26-50", "count": 0},
            {"bucket": "51-75", "count": 0},
            {"bucket": "76-100", "count": 0},
        ]

    # Query interactions with score for these tasks
    score_bucket = case(
        (InteractionLog.score <= 25, "0-25"),
        (InteractionLog.score <= 50, "26-50"),
        (InteractionLog.score <= 75, "51-75"),
        (InteractionLog.score <= 100, "76-100"),
        else_="76-100",
    ).label("bucket")

    result = await session.exec(
        select(score_bucket, func.count(InteractionLog.id))
        .where(
            InteractionLog.item_id.in_(task_ids),
            InteractionLog.score.isnot(None),
        )
        .group_by(score_bucket)
    )

    rows = result.all()
    bucket_counts = {row[0]: row[1] for row in rows}

    # Ensure all buckets are present
    all_buckets = ["0-25", "26-50", "51-75", "76-100"]
    return [
        {"bucket": bucket, "count": bucket_counts.get(bucket, 0)}
        for bucket in all_buckets
    ]


@router.get("/pass-rates")
async def get_pass_rates(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Per-task pass rates for a given lab.

    TODO: Implement this endpoint.
    - Find the lab item and its child task items
    - For each task, compute:
      - avg_score: average of interaction scores (round to 1 decimal)
      - attempts: total number of interactions
    - Return a JSON array:
      [{"task": "Repository Setup", "avg_score": 92.3, "attempts": 150}, ...]
    - Order by task title
    """
    lab_title_prefix = _parse_lab_number(lab)

    # Find the lab item
    lab_item = (
        await session.exec(
            select(ItemRecord).where(
                ItemRecord.type == "lab",
                ItemRecord.title.like(f"%{lab_title_prefix}%"),
            )
        )
    ).first()

    if not lab_item:
        return []

    # Get all tasks for this lab
    tasks = (
        await session.exec(
            select(ItemRecord).where(ItemRecord.parent_id == lab_item.id).order_by(ItemRecord.title)
        )
    ).all()

    result = []
    for task in tasks:
        # Get avg score and attempt count for this task
        stats = await session.exec(
            select(
                func.avg(InteractionLog.score).label("avg_score"),
                func.count(InteractionLog.id).label("attempts"),
            )
            .where(InteractionLog.item_id == task.id)
        )
        row = stats.first()
        avg_score = round(row[0], 1) if row[0] is not None else 0.0
        attempts = row[1] or 0

        result.append({
            "task": task.title,
            "avg_score": avg_score,
            "attempts": attempts,
        })

    return result


@router.get("/timeline")
async def get_timeline(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Submissions per day for a given lab.

    TODO: Implement this endpoint.
    - Find the lab item and its child task items
    - Group interactions by date (use func.date(created_at))
    - Count the number of submissions per day
    - Return a JSON array:
      [{"date": "2026-02-28", "submissions": 45}, ...]
    - Order by date ascending
    """
    lab_title_prefix = _parse_lab_number(lab)

    # Find the lab item
    lab_item = (
        await session.exec(
            select(ItemRecord).where(
                ItemRecord.type == "lab",
                ItemRecord.title.like(f"%{lab_title_prefix}%"),
            )
        )
    ).first()

    if not lab_item:
        return []

    # Get all task item_ids for this lab
    tasks_result = (
        await session.exec(
            select(ItemRecord.id).where(ItemRecord.parent_id == lab_item.id)
        )
    ).all()
    task_ids = list(tasks_result)

    if not task_ids:
        return []

    # Group interactions by date
    result = await session.exec(
        select(
            func.date(InteractionLog.created_at).label("date"),
            func.count(InteractionLog.id).label("submissions"),
        )
        .where(InteractionLog.item_id.in_(task_ids))
        .group_by(func.date(InteractionLog.created_at))
        .order_by(func.date(InteractionLog.created_at))
    )

    rows = result.all()
    return [{"date": str(row[0]), "submissions": row[1]} for row in rows]


@router.get("/groups")
async def get_groups(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Per-group performance for a given lab.

    TODO: Implement this endpoint.
    - Find the lab item and its child task items
    - Join interactions with learners to get student_group
    - For each group, compute:
      - avg_score: average score (round to 1 decimal)
      - students: count of distinct learners
    - Return a JSON array:
      [{"group": "B23-CS-01", "avg_score": 78.5, "students": 25}, ...]
    - Order by group name
    """
    lab_title_prefix = _parse_lab_number(lab)

    # Find the lab item
    lab_item = (
        await session.exec(
            select(ItemRecord).where(
                ItemRecord.type == "lab",
                ItemRecord.title.like(f"%{lab_title_prefix}%"),
            )
        )
    ).first()

    if not lab_item:
        return []

    # Get all task item_ids for this lab
    tasks_result = (
        await session.exec(
            select(ItemRecord.id).where(ItemRecord.parent_id == lab_item.id)
        )
    ).all()
    task_ids = list(tasks_result)

    if not task_ids:
        return []

    # Join interactions with learners and group by student_group
    result = await session.exec(
        select(
            Learner.student_group.label("group"),
            func.avg(InteractionLog.score).label("avg_score"),
            func.count(distinct(Learner.id)).label("students"),
        )
        .join(InteractionLog, Learner.id == InteractionLog.learner_id)
        .where(InteractionLog.item_id.in_(task_ids))
        .group_by(Learner.student_group)
        .order_by(Learner.student_group)
    )

    rows = result.all()
    return [
        {
            "group": row[0],
            "avg_score": round(row[1], 1) if row[1] is not None else 0.0,
            "students": row[2],
        }
        for row in rows
    ]
