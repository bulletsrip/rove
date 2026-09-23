from __future__ import annotations

MAX_LONG_ITERATIONS = 20
MAX_LONG_ACTIONS = 1000
MAX_LONG_ACTIONS_PER_PASS = 3
MAX_LONG_NO_NEW_ITERATIONS = 2


def collection_instruction(
    goal: str,
    iteration: int = 1,
    collected_items: list[str] | None = None,
    target_count: int | None = None,
) -> str:
    """Tell the browser agent how to advance a collection without revisiting items."""
    collected_items = collected_items or []
    preview = "; ".join(collected_items[-12:])
    if len(preview) > 1800:
        preview = preview[-1800:]
    prefix = "Start" if iteration == 1 else "Continue"
    target_note = f" Stop once {target_count} unique items have been collected." if target_count else ""
    return (
        f"{prefix} the collection task: {goal}\n\n"
        f"This is collection pass {iteration}. Prefer the page's list, table, catalog, pagination, "
        "Next, or Load more controls. Extract items from the listing when the requested fields are "
        "already visible; do not open an individual item detail page unless those fields are unavailable. "
        "Do not revisit an item or alternate between the same pages. Move forward to the next unseen "
        f"batch.{target_note} If there are no more items, choose DONE. If the page cannot advance, choose BLOCKED.\n"
        f"Recently collected items: {preview or '(none yet)'}"
    )


def continuation_instruction(
    goal: str,
    iteration: int,
    collected_items: list[str],
    target_count: int | None = None,
) -> str:
    """Give Jev a concrete next-batch instruction after a collection cycle."""
    return collection_instruction(goal, iteration, collected_items, target_count)


def batch_extraction_goal(goal: str) -> str:
    """Turn the overall collection goal into a verifiable current-page batch goal."""
    return (
        "Extract every distinct requested item visible in the current catalog/listing page for this "
        f"collection task: {goal}\n"
        "Only report evidence from the current page batch. Do not require pagination to be complete "
        "and do not return needs_review merely because other pages have not been visited yet."
    )


def merge_items(existing: list[str], result: dict) -> tuple[list[str], int]:
    """Append new extracted items while preserving order and removing duplicates."""
    merged = list(existing)
    seen = {item.strip().casefold() for item in merged if isinstance(item, str) and item.strip()}
    added = 0
    for item in result.get("items", []) if isinstance(result, dict) else []:
        if not isinstance(item, str) or not item.strip():
            continue
        normalized = item.strip().casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        merged.append(item.strip())
        added += 1
    return merged, added
