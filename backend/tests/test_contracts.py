"""Contract test: frontend/lib/contracts.ts must match backend/contracts.py.

Fails if either side changes alone. Run from backend/:  pytest tests/test_contracts.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import contracts as py

TS_PATH = Path(__file__).resolve().parents[2] / "frontend" / "lib" / "contracts.ts"

# TS interface name -> Pydantic model
INTERFACES = {
    "KeyPoint": py.KeyPoint,
    "Section": py.Section,
    "ProductionPlan": py.ProductionPlan,
    "DialogueTurn": py.DialogueTurn,
    "CoverageReport": py.CoverageReport,
    "CompleteData": py.CompleteData,
    "ErrorData": py.ErrorData,
    "StreamEvent": py.StreamEvent,
}

LITERAL_ARRAYS = {
    "STATUS_VALUES": py.STATUS_VALUES,
    "ROLE_VALUES": py.ROLE_VALUES,
    "HOST_VALUES": py.HOST_VALUES,
    "PRIORITY_VALUES": py.PRIORITY_VALUES,
    "KIND_VALUES": py.KIND_VALUES,
    "IN_PROGRESS_STATUSES": py.IN_PROGRESS_STATUSES,
    "STREAM_EVENT_FIELDS": py.STREAM_EVENT_FIELDS,
}


# ---------------------------------------------------------------- TS parsing


def ts_const_array(src: str, name: str) -> tuple[str, ...]:
    m = re.search(rf"export const {name}\s*=\s*\[(.*?)\]\s*as const", src, re.S)
    if not m:
        raise AssertionError(f"{name} not found in contracts.ts")
    return tuple(re.findall(r'"([^"]*)"', m.group(1)))


def ts_interface_fields(src: str, name: str) -> tuple[str, ...]:
    m = re.search(rf"export interface {name}\s*\{{(.*?)\n\}}", src, re.S)
    if not m:
        raise AssertionError(f"interface {name} not found in contracts.ts")
    return tuple(re.findall(r"^\s*(\w+)\??\s*:", m.group(1), re.M))


def ts_record(src: str, name: str) -> dict[str, int]:
    m = re.search(rf"export const {name}[^=]*=\s*\{{(.*?)\}};", src, re.S)
    if not m:
        raise AssertionError(f"{name} not found in contracts.ts")
    return {k: int(v) for k, v in re.findall(r"(\w+)\s*:\s*(\d+)", m.group(1))}


def collect_mismatches(src: str) -> list[str]:
    problems: list[str] = []
    for name, expected in LITERAL_ARRAYS.items():
        got = ts_const_array(src, name)
        if got != tuple(expected):
            problems.append(f"{name}: ts={got} py={tuple(expected)}")
    for name, model in INTERFACES.items():
        got = ts_interface_fields(src, name)
        expected = tuple(model.model_fields.keys())
        if got != expected:
            problems.append(f"interface {name}: ts={got} py={expected}")
    progress = ts_record(src, "STATUS_PROGRESS")
    if progress != py.STATUS_PROGRESS:
        problems.append(f"STATUS_PROGRESS: ts={progress} py={py.STATUS_PROGRESS}")
    return problems


# --------------------------------------------------------------------- tests


@pytest.fixture(scope="module")
def ts_src() -> str:
    return TS_PATH.read_text(encoding="utf-8")


def test_status_strings_match(ts_src):
    assert ts_const_array(ts_src, "STATUS_VALUES") == py.STATUS_VALUES


def test_status_order_is_specified():
    assert py.STATUS_VALUES == (
        "extracting_facts",
        "generating_script",
        "revising_outline",
        "synthesizing_audio",
        "complete",
        "error",
    )


def test_stream_event_fields_match(ts_src):
    assert ts_interface_fields(ts_src, "StreamEvent") == tuple(py.StreamEvent.model_fields)
    assert ts_const_array(ts_src, "STREAM_EVENT_FIELDS") == tuple(py.StreamEvent.model_fields)


@pytest.mark.parametrize("name", ["ROLE_VALUES", "HOST_VALUES", "PRIORITY_VALUES", "KIND_VALUES"])
def test_literal_sets_match(ts_src, name):
    assert ts_const_array(ts_src, name) == LITERAL_ARRAYS[name]


@pytest.mark.parametrize("name", list(INTERFACES))
def test_interface_fields_match(ts_src, name):
    assert ts_interface_fields(ts_src, name) == tuple(INTERFACES[name].model_fields)


def test_progress_map_matches(ts_src):
    assert ts_record(ts_src, "STATUS_PROGRESS") == py.STATUS_PROGRESS
    assert set(py.STATUS_PROGRESS) == set(py.STATUS_VALUES)


def test_whole_contract_has_no_mismatch(ts_src):
    assert collect_mismatches(ts_src) == []


# Prove the test is sensitive: a one-sided change on either side must fail.


def test_detects_ts_side_change(ts_src):
    mutated = ts_src.replace('"revising_outline",', '"revising_plan",', 1)
    assert collect_mismatches(mutated), "a TS-only status rename went undetected"
    mutated = ts_src.replace("  delivery_instruction: string;", "  delivery: string;", 1)
    assert collect_mismatches(mutated), "a TS-only field rename went undetected"


def test_detects_python_side_change(ts_src, monkeypatch):
    monkeypatch.setitem(LITERAL_ARRAYS, "HOST_VALUES", ("A", "B", "C"))
    assert collect_mismatches(ts_src), "a Python-only literal change went undetected"
