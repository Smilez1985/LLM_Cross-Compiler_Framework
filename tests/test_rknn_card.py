"""The Rockchip target module writes the model card of a vision model at conversion
(targets/Rockchip/scripts/rknn_converter.py, card_from_facts): from the model's own facts (the Ultralytics metadata,
preprocessor_config.json, config.json's id2label, the ONNX outputs) to a card the host's checker
(orchestrator/runtime/bundle/model_card.py) takes, or no card and what is missing. The toolkit is not needed: the
rules are pure."""

import importlib.util
import sys
from pathlib import Path

import pytest

from orchestrator.runtime.bundle import model_card as mc

SCRIPT = Path(__file__).resolve().parents[1] / "targets" / "Rockchip" / "scripts" / "rknn_converter.py"


@pytest.fixture(scope="module")
def conv():
    spec = importlib.util.spec_from_file_location("rknn_converter_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True   # no __pycache__ in the target module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


SETTINGS = {"file": "yolo-rk3566-i8.rknn", "sha256": "c" * 64, "toolkit": "2.3.2", "platform": "rk3566",
            "quantisation": "w8a8", "name": "yolo-rk3566-i8", "licence": "AGPL-3.0"}
ULTRA = {"task": "detect", "names": "{0: 'person', 1: 'bicycle', 2: 'car'}", "stride": "32", "imgsz": "[640, 640]"}


def test_rockchips_dfl_export_gets_a_yolo_dfl_card(conv):
    outputs = []
    for g in (80, 40, 20):
        outputs += [("box", [1, 64, g, g]), ("score", [1, 3, g, g]), ("sum", [1, 1, g, g])]
    card, missing = conv.card_from_facts(ULTRA, [1, 3, 640, 640], outputs, {}, SETTINGS)
    assert missing == []
    checked = mc.parse(card)
    assert checked.kind == "yolo-dfl" and card["postprocess"] == {"kind": "yolo-dfl", "strides": [8, 16, 32],
                                                                    "reg_max": 16}
    assert card["labels"] == ["person", "bicycle", "car"] and card["input"]["std"] == [255, 255, 255]
    assert card["rknn"]["toolkit"] == "2.3.2" and card["model"]["licence"] == "AGPL-3.0"


def test_the_other_detection_exports_the_rules_can_tell(conv):
    heads = [("o", [1, 24, g, g]) for g in (80, 40, 20)]   # YOLOv5: 3 x (5 + 3)
    card, _ = conv.card_from_facts(ULTRA, [1, 3, 640, 640], heads, {}, SETTINGS)
    assert mc.parse(card).kind == "yolo-anchor" and len(card["postprocess"]["anchors"]) == 3
    yolox = [("o", [1, 8, g, g]) for g in (80, 40, 20)]
    card, _ = conv.card_from_facts(ULTRA, [1, 3, 640, 640], yolox, {}, SETTINGS)
    assert mc.parse(card).kind == "yolo-anchorfree-obj"
    yolo26 = [("boxes", [1, 4, 8400]), ("scores", [1, 3, 8400])]
    card, _ = conv.card_from_facts(ULTRA, [1, 3, 640, 640], yolo26, {}, SETTINGS)
    assert mc.parse(card).kind == "yolo-nms-free"
    hf = {"config.json": {"id2label": {"1": "dog", "0": "cat"}},
          "preprocessor_config.json": {"image_mean": [0.5, 0.5, 0.5], "image_std": [0.5, 0.5, 0.5]}}
    card, _ = conv.card_from_facts({}, [1, 3, 320, 320], [("o", [1, 7, g, g]) for g in (40, 20, 10)], hf, SETTINGS)
    assert card["labels"] == ["cat", "dog"] and card["input"]["mean"] == [127.5, 127.5, 127.5]
    assert mc.parse(card).kind == "yolo-anchorfree-obj"


def test_without_the_facts_no_card_is_written_and_the_log_says_what_is_missing(conv):
    stock = [("output0", [1, 7, 8400])]   # the stock Ultralytics export: one combined output
    card, missing = conv.card_from_facts(ULTRA, [1, 3, 640, 640], stock, {}, SETTINGS)
    assert card is None and "single combined output" in missing[0]
    card, missing = conv.card_from_facts({}, [1, 3, -1, -1], [], {}, SETTINGS)
    assert card is None and any("class names" in m for m in missing) and any("static input" in m for m in missing)
    assert conv.card_path("/out/net-rk3566-i8.rknn") == "/out/net-rk3566-i8.card.json"
