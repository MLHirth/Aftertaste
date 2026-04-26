from __future__ import annotations

import time

from core.spotify_client import SpotifyClient


def queue_depth(queue_payload: dict[str, object]) -> int:
    queue = queue_payload.get("queue")
    if isinstance(queue, list):
        return len(queue)
    return 0


def top_up_queue(
    client: SpotifyClient,
    track_uris: list[str],
    target_depth: int = 3,
) -> dict[str, int]:
    payload = client.get_queue()
    depth = queue_depth(payload)
    if depth >= target_depth:
        return {"added": 0, "depth_before": depth, "depth_after": depth}

    added = 0
    missing = target_depth - depth
    for uri in track_uris:
        if added >= missing:
            break
        client.add_to_queue(uri)
        added += 1
        time.sleep(0.3)

    return {
        "added": added,
        "depth_before": depth,
        "depth_after": depth + added,
    }
