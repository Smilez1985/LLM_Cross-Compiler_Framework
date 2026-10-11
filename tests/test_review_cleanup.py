"""Regression tests of the cleanup track (numbers: the items of the task).

8 FrameworkManager.shutdown ends the background event loop
"""

import asyncio

import pytest

from test_review_capabilities_paths import make_cli


@pytest.fixture
def cli(booted_framework, tmp_path, monkeypatch):
    return make_cli(booted_framework, tmp_path, monkeypatch)


# ---------------------------------------------------------------------------------------------------------------
# 8. shutdown() ends the background event loop
# ---------------------------------------------------------------------------------------------------------------

def test_shutdown_ends_the_background_event_loop(make_framework):
    fm = make_framework()

    async def answer():
        return 42

    assert fm.run_coroutine(answer(), timeout=10) == 42
    loop, thread = fm._loop, fm._loop_thread
    pending = asyncio.run_coroutine_threadsafe(asyncio.sleep(3600), loop)  # e.g. a request still being followed
    fm.shutdown()
    assert not thread.is_alive() and loop.is_closed()
    assert pending.cancelled()
    fm.shutdown()  # a second call (the fixture's) changes nothing
