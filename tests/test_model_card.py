"""The model card of an RKNN vision model (orchestrator/runtime/bundle/model_card.py): the shared vectors
(tests/data/model_card_vectors.json, checked here and by the Rust tests of rust/llmcf-detect/src/card.rs), the strict
JSON reading, the cap and the pair of card and model."""

import copy
import json
from pathlib import Path

import pytest

from orchestrator.runtime.bundle import model_card as mc

VECTORS = json.loads((Path(__file__).parent / "data" / "model_card_vectors.json").read_text(encoding="utf-8"))


def _patched(case):
    doc = copy.deepcopy(VECTORS["bases"][case["base"]])
    for patch in case["patches"]:
        at = doc
        for step in patch["path"][:-1]:
            at = at[step]
        last = patch["path"][-1]
        if "value" in patch:
            if isinstance(at, list) and last == len(at):
                at.append(patch["value"])
            else:
                at[last] = patch["value"]
        else:
            del at[last]
    return doc


@pytest.mark.parametrize("case", VECTORS["accept"], ids=lambda c: c["why"])
def test_the_shared_card_vectors_are_taken(case):
    card = mc.parse(json.dumps(_patched(case)).encode())
    assert card.task == "detect" and card.kind in dict(mc.KINDS)


@pytest.mark.parametrize("case", VECTORS["reject"], ids=lambda c: c["why"])
def test_the_shared_card_vectors_are_refused(case):
    with pytest.raises(mc.CardError):
        mc.parse(json.dumps(_patched(case)).encode())


@pytest.mark.parametrize("case", VECTORS["raw_reject"], ids=lambda c: c["why"])
def test_text_that_is_no_strict_card_is_refused(case):
    with pytest.raises(mc.CardError):
        mc.parse(case["text"].encode())


def test_a_card_is_capped_and_the_kinds_of_later_steps_say_so():
    with pytest.raises(mc.CardError, match="1024 KiB"):
        mc.parse(b" " * (mc.MAX_CARD_BYTES + 1))
    doc = copy.deepcopy(VECTORS["bases"]["yolo"])
    doc["task"], doc["postprocess"] = "pose", {"kind": "yolo-pose"}
    with pytest.raises(mc.CardError, match="later step"):
        mc.parse(json.dumps(doc))


def test_a_card_and_its_model_are_a_pair(tmp_path):
    model = tmp_path / "yolo-rk3566-i8.rknn"
    model.write_bytes(b"RKNN\0model")
    assert mc.card_path_for(model).name == "yolo-rk3566-i8.card.json"
    from orchestrator.utils.helpers import sha256_file
    doc = copy.deepcopy(VECTORS["bases"]["yolo"])
    doc["rknn"]["sha256"] = sha256_file(model)
    path = mc.card_path_for(model)
    path.write_text(json.dumps(doc), encoding="utf-8")
    card = mc.check_file(path, sha256_file(model))
    assert "yolo-dfl, 3 labels, input 640 x 640" in card.summary()
    with pytest.raises(mc.CardError, match="another model"):
        mc.check_file(path, "0" * 64)


def test_a_builds_cards_are_checked_when_its_artefact_is_taken_in(tmp_path):
    from orchestrator.Core.builder import BuildEngine
    from orchestrator.utils.helpers import sha256_file
    good, bad, bare = (tmp_path / f"{n}.rknn" for n in ("good", "bad", "bare"))
    for p in (good, bad, bare):
        p.write_bytes(b"RKNN" + p.name.encode())
    doc = copy.deepcopy(VECTORS["bases"]["yolo"])
    doc["rknn"]["sha256"] = sha256_file(good)
    (tmp_path / "good.card.json").write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "bad.card.json").write_text(json.dumps(doc), encoding="utf-8")   # names the other model

    class Progress:
        warnings = []

        def add_warning(self, text):
            self.warnings.append(text)
    arts = [{"file": p.name, "sha256": sha256_file(p)} for p in (good, bad, bare)] + [{"file": "x.gguf", "sha256": ""}]
    progress = Progress()
    cards = BuildEngine._model_cards(tmp_path, arts, progress)
    assert [(c["file"], c["result"]) for c in cards] == [("good.rknn", "passed"), ("bad.rknn", "refused"),
                                                        ("bare.rknn", "missing")]
    assert "yolo-dfl" in cards[0]["detail"] and len(progress.warnings) == 2
