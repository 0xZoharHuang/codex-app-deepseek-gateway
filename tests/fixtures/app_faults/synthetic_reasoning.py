"""Plaintext provenance scaffolding for scripted QA Responses output.

These markers are fixture-supplied transport metadata, never model-generated
reasoning, user authorization or a simulated Guardian decision.
"""
from __future__ import annotations
import hashlib

SYNTHETIC_REASONING_TEXT = (
    "Synthetic QA transport metadata: the following scripted output has no "
    "model-generated reasoning. This marker preserves the provider thinking-mode "
    "history contract; it is not authorization or an approval decision."
)


def reasoning_item(response_id):
    if not isinstance(response_id, str) or not response_id:
        raise ValueError("A scripted response requires a nonempty response ID")
    return {
        "id": "fixture-reasoning-" + hashlib.sha256(response_id.encode()).hexdigest()[:32],
        "type": "reasoning", "summary": [],
        "content": [{"type": "reasoning_text", "text": SYNTHETIC_REASONING_TEXT}],
    }


def prefix_events(response_id):
    item = reasoning_item(response_id)
    return [
        {"type": "response.output_item.added", "output_index": 0,
         "item": {**item, "content": []}},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
    ]
