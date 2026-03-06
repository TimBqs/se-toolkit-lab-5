"""ETL pipeline: fetch data from the autochecker API and load it into the database.

The autochecker dashboard API provides two endpoints:
- GET /api/items — lab/task catalog
- GET /api/logs  — anonymized check results (supports ?since= and ?limit= params)

Both require HTTP Basic Auth (email + password from settings).
"""

from datetime import datetime

import httpx
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.settings import settings


# ---------------------------------------------------------------------------
# Extract — fetch data from the autochecker API
# ---------------------------------------------------------------------------


async def fetch_items() -> list[dict]:
    """Fetch the lab/task catalog from the autochecker API.

    TODO: Implement this function.
    - Use httpx.AsyncClient to GET {settings.autochecker_api_url}/api/items
    - Pass HTTP Basic Auth using settings.autochecker_email and
      settings.autochecker_password
    - The response is a JSON array of objects with keys:
      lab (str), task (str | null), title (str), type ("lab" | "task")
    - Return the parsed list of dicts
    - Raise an exception if the response status is not 200
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.autochecker_api_url}/api/items",
            auth=(settings.autochecker_email, settings.autochecker_password),
        )
        response.raise_for_status()
        return response.json()


async def fetch_logs(since: datetime | None = None) -> list[dict]:
    """Fetch check results from the autochecker API.

    TODO: Implement this function.
    - Use httpx.AsyncClient to GET {settings.autochecker_api_url}/api/logs
    - Pass HTTP Basic Auth using settings.autochecker_email and
      settings.autochecker_password
    - Query parameters:
      - limit=500 (fetch in batches)
      - since={iso timestamp} if provided (for incremental sync)
    - The response JSON has shape:
      {"logs": [...], "count": int, "has_more": bool}
    - Handle pagination: keep fetching while has_more is True
      - Use the submitted_at of the last log as the new "since" value
    - Return the combined list of all log dicts from all pages
    """
    all_logs: list[dict] = []
    current_since = since

    async with httpx.AsyncClient() as client:
        while True:
            params: dict[str, str | int] = {"limit": 500}
            if current_since is not None:
                params["since"] = current_since.isoformat()

            response = await client.get(
                f"{settings.autochecker_api_url}/api/logs",
                auth=(settings.autochecker_email, settings.autochecker_password),
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            logs = data.get("logs", [])
            all_logs.extend(logs)

            if not data.get("has_more", False):
                break

            # Use the last log's submitted_at as the new since value
            current_since = datetime.fromisoformat(logs[-1]["submitted_at"])

    return all_logs


# ---------------------------------------------------------------------------
# Load — insert fetched data into the local database
# ---------------------------------------------------------------------------


async def load_items(items: list[dict], session: AsyncSession) -> int:
    """Load items (labs and tasks) into the database.

    TODO: Implement this function.
    - Import ItemRecord from app.models.item
    - Process labs first (items where type="lab"):
      - For each lab, check if an item with type="lab" and matching title
        already exists (SELECT)
      - If not, INSERT a new ItemRecord(type="lab", title=lab_title)
      - Build a dict mapping the lab's short ID (the "lab" field, e.g.
        "lab-01") to the lab's database record, so you can look up
        parent IDs when processing tasks
    - Then process tasks (items where type="task"):
      - Find the parent lab item using the task's "lab" field (e.g.
        "lab-01") as the key into the dict you built above
      - Check if a task with this title and parent_id already exists
      - If not, INSERT a new ItemRecord(type="task", title=task_title,
        parent_id=lab_item.id)
    - Commit after all inserts
    - Return the number of newly created items
    """
    from app.models.item import ItemRecord

    new_count = 0

    # Map: lab_short_id (e.g., "lab-01") -> ItemRecord
    lab_map: dict[str, ItemRecord] = {}

    # Process labs first
    for item in items:
        if item.get("type") != "lab":
            continue

        title = item.get("title", "")
        lab_short_id = item.get("lab", "")

        # Check if lab already exists
        existing = (
            await session.exec(
                select(ItemRecord).where(
                    ItemRecord.type == "lab",
                    ItemRecord.title == title,
                )
            )
        ).scalars()
        lab_item = existing.first()

        if lab_item is None:
            # Create new lab record
            lab_item = ItemRecord(type="lab", title=title)
            session.add(lab_item)
            new_count += 1

        # Map short ID to record for task lookup
        lab_map[lab_short_id] = lab_item

    # Process tasks
    for item in items:
        if item.get("type") != "task":
            continue

        title = item.get("title", "")
        lab_short_id = item.get("lab", "")

        # Find parent lab
        parent_lab = lab_map.get(lab_short_id)
        if parent_lab is None:
            continue  # Skip if parent lab not found

        # Check if task already exists
        existing = (
            await session.exec(
                select(ItemRecord).where(
                    ItemRecord.type == "task",
                    ItemRecord.title == title,
                    ItemRecord.parent_id == parent_lab.id,
                )
            )
        ).scalars()
        task_item = existing.first()

        if task_item is None:
            # Create new task record
            task_item = ItemRecord(type="task", title=title, parent_id=parent_lab.id)
            session.add(task_item)
            new_count += 1

    await session.commit()
    return new_count


async def load_logs(
    logs: list[dict], items_catalog: list[dict], session: AsyncSession
) -> int:
    """Load interaction logs into the database.

    Args:
        logs: Raw log dicts from the API (each has lab, task, student_id, etc.)
        items_catalog: Raw item dicts from fetch_items() — needed to map
            short IDs (e.g. "lab-01", "setup") to item titles stored in the DB.
        session: Database session.

    TODO: Implement this function.
    - Import Learner from app.models.learner
    - Import InteractionLog from app.models.interaction
    - Import ItemRecord from app.models.item
    - Build a lookup from (lab_short_id, task_short_id) to item title
      using items_catalog. For labs, the key is (lab, None). For tasks,
      the key is (lab, task). The value is the item's title.
    - For each log dict:
      1. Find or create a Learner by external_id (log["student_id"])
         - If creating, set student_group from log["group"]
      2. Find the matching item in the database:
         - Use the lookup to get the title for (log["lab"], log["task"])
         - Query the DB for an ItemRecord with that title
         - Skip this log if no matching item is found
      3. Check if an InteractionLog with this external_id already exists
         (for idempotent upsert — skip if it does)
      4. Create InteractionLog with:
         - external_id = log["id"]
         - learner_id = learner.id
         - item_id = item.id
         - kind = "attempt"
         - score = log["score"]
         - checks_passed = log["passed"]
         - checks_total = log["total"]
         - created_at = parsed log["submitted_at"]
    - Commit after all inserts
    - Return the number of newly created interactions
    """
    from app.models.interaction import InteractionLog
    from app.models.item import ItemRecord
    from app.models.learner import Learner

    new_count = 0

    # Build lookup: (lab_short_id, task_short_id | None) -> title
    item_title_lookup: dict[tuple[str, str | None], str] = {}
    for item in items_catalog:
        lab_short_id = item.get("lab", "")
        task_short_id = item.get("task")  # Can be None for labs
        title = item.get("title", "")
        item_title_lookup[(lab_short_id, task_short_id)] = title

    for log in logs:
        # 1. Find or create Learner
        student_id = log.get("student_id", "")
        group = log.get("group", "")

        learner = (
            await session.exec(
                select(Learner).where(Learner.external_id == student_id)
            )
        ).scalars()
        learner_item = learner.first()

        if learner_item is None:
            learner_item = Learner(external_id=student_id, student_group=group)
            session.add(learner_item)
            await session.flush()  # Get the ID

        # 2. Find the matching item
        lab_short_id = log.get("lab", "")
        task_short_id = log.get("task")  # Can be None for lab-level logs
        item_title = item_title_lookup.get((lab_short_id, task_short_id))

        if item_title is None:
            continue  # Skip if no matching item found

        item_record = (
            await session.exec(
                select(ItemRecord).where(ItemRecord.title == item_title)
            )
        ).scalars()
        item = item_record.first()

        if item is None:
            continue  # Skip if no matching item in DB

        # 3. Check for existing InteractionLog (idempotent upsert)
        log_external_id = log.get("id")
        existing_interaction = (
            await session.exec(
                select(InteractionLog).where(InteractionLog.external_id == log_external_id)
            )
        ).scalars()
        if existing_interaction.first() is not None:
            continue  # Skip if already exists

        # 4. Create new InteractionLog
        submitted_at_str = log.get("submitted_at", "")
        submitted_at = datetime.fromisoformat(submitted_at_str) if submitted_at_str else None

        interaction = InteractionLog(
            external_id=log_external_id,
            learner_id=learner_item.id,
            item_id=item.id,
            kind="attempt",
            score=log.get("score"),
            checks_passed=log.get("passed"),
            checks_total=log.get("total"),
            created_at=submitted_at,
        )
        session.add(interaction)
        new_count += 1

    await session.commit()
    return new_count


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def sync(session: AsyncSession) -> dict:
    """Run the full ETL pipeline.

    TODO: Implement this function.
    - Step 1: Fetch items from the API (keep the raw list) and load them
      into the database
    - Step 2: Determine the last synced timestamp
      - Query the most recent created_at from InteractionLog
      - If no records exist, since=None (fetch everything)
    - Step 3: Fetch logs since that timestamp and load them
      - Pass the raw items list to load_logs so it can map short IDs
        to titles
    - Return a dict: {"new_records": <number of new interactions>,
                      "total_records": <total interactions in DB>}
    """
    from app.models.interaction import InteractionLog

    # Step 1: Fetch and load items
    raw_items = await fetch_items()
    await load_items(raw_items, session)

    # Step 2: Determine the last synced timestamp
    latest_interaction = (
        await session.exec(
            select(InteractionLog).order_by(InteractionLog.created_at.desc()).limit(1)
        )
    ).scalars()
    latest_record = latest_interaction.first()
    since = latest_record.created_at if latest_record else None

    # Step 3: Fetch and load logs since that timestamp
    raw_logs = await fetch_logs(since=since)
    new_records = await load_logs(raw_logs, raw_items, session)

    # Get total count
    total_result = (
        await session.exec(select(InteractionLog))
    ).scalars()
    total_records = len(total_result.all())

    return {"new_records": new_records, "total_records": total_records}
