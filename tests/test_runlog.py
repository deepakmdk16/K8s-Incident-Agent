"""Tests for the shared structured logger (offline, deterministic)."""

from __future__ import annotations

import json
from io import StringIO

from common.runlog import get_logger, new_run_id


def test_lines_are_json_with_run_id() -> None:
    stream = StringIO()
    run_id = new_run_id()
    log = get_logger(run_id, name="test-json", stream=stream)
    log.info("hello %s", "world")
    line = json.loads(stream.getvalue().strip())
    assert line["run_id"] == run_id
    assert line["msg"] == "hello world"
    assert line["level"] == "INFO"


def test_repeat_get_logger_does_not_stack_handlers() -> None:
    stream = StringIO()
    run_id = new_run_id()
    first = get_logger(run_id, name="test-dup", stream=stream)
    second = get_logger(run_id, name="test-dup", stream=StringIO())
    assert first is second
    assert len(second.handlers) == 1
    second.info("once")
    assert len(stream.getvalue().splitlines()) == 1


def test_exception_lines_carry_the_traceback() -> None:
    stream = StringIO()
    run_id = new_run_id()
    log = get_logger(run_id, name="test-exc", stream=stream)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("tool call failed")
    line = json.loads(stream.getvalue().strip())
    assert line["msg"] == "tool call failed"
    assert "RuntimeError: boom" in line["exc"]


def test_new_run_ids_are_unique() -> None:
    assert new_run_id() != new_run_id()
