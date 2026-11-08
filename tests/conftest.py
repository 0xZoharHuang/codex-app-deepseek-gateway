"""Keep the test process's asyncio policy separate from the App runtime."""
import asyncio

import pytest


@pytest.fixture(scope="session")
def event_loop_policy():
    previous = asyncio.get_event_loop_policy()
    policy = asyncio.DefaultEventLoopPolicy()
    # On Python 3.13, looking up an unset main-thread loop can create a
    # fallback loop that the scoped pytest-asyncio runners do not own.
    policy.set_event_loop(None)
    asyncio.set_event_loop_policy(policy)
    try:
        yield policy
    finally:
        asyncio.set_event_loop_policy(previous)
