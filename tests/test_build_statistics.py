#!/usr/bin/env python3
"""The convert statistics of a finished build (DECISIONS round 25 Q8): how long the conversion and the quantization
ran and the size before and after, from the build's build_info.json, in the build record the CLI's `build status` and
the GUI's Build History show."""

import json

from orchestrator.Core.build_history import RECORD_FIELDS, record_rows, statistics_text


def test_the_statistics_of_a_finished_build(tmp_path):
    (tmp_path / "build_info.json").write_text(json.dumps({
        "model": "m", "quantization": "Q4_K_M",
        "conversion": {"seconds": 41, "input_bytes": 2 * 1024 ** 3, "output_bytes": 1024 ** 3},
        "quantize_seconds": 12}))
    text = statistics_text(tmp_path)
    assert text.startswith("convert 41 s, ") and text.endswith(", quantize 12 s") and " to " in text
    rows = dict(record_rows({"output_dir": str(tmp_path)}))
    assert rows["statistics"] == text and "statistics" in RECORD_FIELDS


def test_no_statistics_without_a_readable_build_info(tmp_path):
    assert statistics_text(None) == "" and statistics_text(tmp_path) == ""
    (tmp_path / "build_info.json").write_text("{not json")
    assert statistics_text(tmp_path) == ""
    (tmp_path / "build_info.json").write_text(json.dumps({"conversion": {"seconds": "x", "input_bytes": True}}))
    assert statistics_text(tmp_path) == ""


def test_the_cli_and_the_gui_label_the_statistics():
    from orchestrator import cli
    from orchestrator.utils.localization import ui_text
    assert cli._BUILD_FIELD_LABELS["statistics"] == "Statistics"
    assert ui_text("bld.field.statistics", "de") == "Statistik"
