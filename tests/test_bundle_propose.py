"""A building block proposed from a model (orchestrator/runtime/bundle/propose.py): the table from Hugging Face pipeline
tasks (and library names) to a block kind and a `reach` draft, each field with its reason, limits from conservative
defaults, a generic draft that needs the person for a task the rules do not know, the model-card hints of the
detection-type tasks, and every draft passing the block schema's and reach.py's own check. Pure: no network, nothing
written but into tmp_path. Every name is a test value."""

import pytest

from orchestrator.runtime.bundle import propose as p
from orchestrator.runtime.bundle.blocks import BlockError, check_block_folder, new_block, parse_block

EXPECTED = {
    "text-generation": ("llm", ["openai-models", "openai-chat", "openai-completions"]),
    "image-text-to-text": ("vision", ["openai-chat"]),
    "feature-extraction": ("embed", ["openai-embeddings"]),
    "sentence-similarity": ("embed", ["openai-embeddings"]),
    "automatic-speech-recognition": ("stt", ["openai-audio-transcription"]),
    "text-to-speech": ("tts", ["openai-audio-speech"]),
    "object-detection": ("detect", ["http-multipart", "http-sse"]),
    "image-classification": ("classify", ["http-multipart", "http-sse"]),
    "image-segmentation": ("segment", ["http-multipart", "http-sse"]),
    "keypoint-detection": ("keypoints", ["http-multipart", "http-sse"]),
    "video-classification": ("video-classify", ["http-multipart", "http-sse"]),
    "video-text-to-text": ("video-text", ["http-multipart", "http-sse"]),
}


def _routes(draft):
    return draft.spec["reach"]["routes"]


def test_the_table_is_exactly_the_designed_one():
    assert set(p.TASKS) == set(EXPECTED)
    for task, (kind, protocols) in EXPECTED.items():
        draft = p.propose_task(task)
        assert draft.kind == kind and [r["protocol"] for r in _routes(draft)] == protocols, task
        assert draft.rule == task and draft.spec["status"] == "template"


@pytest.mark.parametrize("task", sorted(EXPECTED) + ["zero-shot-classification", "", "Not A Task!", "translation"])
def test_every_draft_passes_the_block_schema_and_reach_and_has_a_reason_per_route(task, tmp_path):
    draft = p.propose_task(task)
    block = draft.check()                       # parse_block: the reach section through reach.parse_reach
    assert block.reach is not None and len(block.reach.routes) == len(_routes(draft))
    fields = {r.field for r in draft.reasons}
    assert all(r.origin in p.ORIGINS and r.text for r in draft.reasons)
    for i, route in enumerate(_routes(draft)):
        assert f"reach.routes[{i}]" in fields and f"reach.routes[{i}].limits" in fields
        # the limits are explicit and conservative, never the gateway's largest
        assert route["max_body_kb"] <= 51200 and route["timeout_s"] <= 600
    for name in ("kind", "id", "files.model", "files.program", "variables.port", "variables.memory_limit_mb",
                 "status", "memory"):
        assert name in fields, (task, name)
    assert block.variable("port").type == "port" and block.reach.port == "port"
    # the written block is one check_block_folder takes (block.yml and the guide with its '## Models' section)
    folder = new_block(tmp_path, draft.block_id, kind=draft.kind, title=draft.spec["title"], spec=draft.spec,
                       guide=draft.guide_text())
    assert check_block_folder(folder) == []
    assert parse_block((folder / "block.yml").read_text(encoding="utf-8"), folder).reach == block.reach


def test_vision_takes_the_chats_with_images_and_an_mmproj_when_the_model_has_one():
    summary = p.ModelSummary(repo="example/vlm-tiny", task="image-text-to-text", source="hub",
                             files=(("vlm-Q4_K_M.gguf", 900_000_000), ("mmproj-f16.gguf", 300_000_000)))
    draft = p.propose(summary)
    (route,) = _routes(draft)
    assert route["match"] == "images" and route["path"] == "/v1/chat/completions" and route["stream"] is True
    roles = {f["role"]: f for f in draft.spec["files"]}
    assert roles["mmproj"]["formats"] == ["mmproj-gguf"] and roles["model"]["formats"] == ["gguf"]
    # memory from the largest model file, not counting the projector: 900 MB * 1.2 + 128
    assert draft.check().variable("memory_limit_mb").default == 1208


def test_a_detection_task_takes_an_image_form_and_offers_the_camera_stream():
    draft = p.propose_task("object-detection")
    form, events = _routes(draft)
    assert form["path"] == "/api/detect" and form["fields"] == [
        {"name": "image", "file": True, "types": ["jpeg", "png"], "required": True}]
    assert events["method"] == "GET" and events["protocol"] == "http-sse" and events["path"] == "/api/detect/events"
    assert any("camera" in r.text for r in draft.reasons if r.field == "reach.routes[1]")


def test_video_text_takes_a_clip_and_a_prompt():
    form, _events = _routes(p.propose_task("video-text-to-text"))
    assert [f["name"] for f in form["fields"]] == ["video", "prompt"]
    assert form["fields"][0]["types"] == ["mp4", "webm"] and form["fields"][1]["required"] is True


def test_an_unknown_task_gives_a_generic_draft_that_needs_the_person():
    draft = p.propose_task("zero-shot-classification")
    (route,) = _routes(draft)
    assert route["protocol"] == "http-json" and draft.rule == "unknown" and draft.needs_person
    assert draft.kind == "zero-shot-classification"
    assert any(r.origin == "needs the person" for r in draft.reasons if r.field == "kind")
    for odd in ("", "gateway", "harness", "Not A Task!", "x" * 40):
        assert p.propose_task(odd).kind == "custom", odd


def test_the_library_names_the_task_only_where_the_model_has_none():
    by_library = p.propose(p.ModelSummary(repo="example/st", library="sentence-transformers", source="hub"))
    assert by_library.kind == "embed" and by_library.rule == "library:sentence-transformers"
    assert any(r.origin == "library" for r in by_library.reasons if r.field == "kind")
    by_task = p.propose(p.ModelSummary(repo="example/st", task="text-to-speech", library="sentence-transformers"))
    assert by_task.kind == "tts"
    assert p.propose(p.ModelSummary(library="ultralytics")).kind == "detect"


def test_the_model_files_set_the_formats_and_their_reasons():
    gguf = p.propose(p.ModelSummary(repo="example/chat", task="text-generation",
                                    files=(("a-Q4.gguf", 400_000_000), ("a-Q8.gguf", 800_000_000))))
    model = next(f for f in gguf.spec["files"] if f["role"] == "model")
    assert model["formats"] == ["gguf"] and "2 GGUF files" in model["note"]
    assert any("llm-llama-server" in r.text for r in gguf.reasons)
    whisper = p.propose(p.ModelSummary(task="automatic-speech-recognition", files=(("ggml-base.bin", 1),)))
    assert next(f for f in whisper.spec["files"] if f["role"] == "model")["formats"] == ["ggml-whisper"]
    st = p.propose(p.ModelSummary(task="feature-extraction", files=(("model.safetensors", 10),)))
    model = next(f for f in st.spec["files"] if f["role"] == "model")
    assert "formats" not in model and "target module" in model["note"]
    nothing = p.propose_task("text-to-speech")
    assert next(r for r in nothing.reasons if r.field == "files.model").origin == "needs the person"
    assert next(r for r in nothing.reasons if r.field == "variables.memory_limit_mb").origin == "default"


def test_the_model_card_hints_of_the_detection_type_tasks():
    for task in p.MODEL_CARD_TASKS:
        hints = p.propose_task(task).model_card
        assert [h.what.split(" (")[0] for h in hints] == ["input size", "classes", "pre-processing",
                                                          "post-processing"], task
        assert all(h.source == p.CARD_SOURCE for h in hints)
        assert "llmcf-model-card/1" in p.CARD_SOURCE
    detect = p.propose_task("object-detection")
    assert any("vision-rknn-detect" in r.text for r in detect.reasons if r.field == "files.program")
    for task in ("text-generation", "text-to-speech", "zero-shot-classification"):
        assert p.propose_task(task).model_card == []
    summary = p.ModelSummary(repo="example/det", task="object-detection", classes=80, image_size=640,
                             files=(("config.json", 1), ("preprocessor_config.json", 1), ("model.onnx", 5)))
    draft = p.propose(summary)
    by = {h.what.split(" (")[0]: h for h in draft.model_card}
    assert by["classes"].source == "the model's config.json (id2label)" and by["classes"].found == "80 classes"
    assert by["input size"].found == "640 px" and "preprocessor_config.json" in by["pre-processing"].source
    assert next(f for f in draft.spec["files"] if f["role"] == "config")["name"] == "config.json"
    guide = draft.guide_text()
    assert "## Models" in guide and "80 classes" in guide and "non-maximum suppression" in guide
    assert any(n.startswith("Needs: classes") for n in draft.spec["notes"])


def test_the_id_and_title_come_from_the_name_made_plain():
    assert p.block_id_for("detect", "Yolo V8 / Tiny!") == "detect-yolo-v8-tiny"
    assert p.block_id_for("llm", "") == "llm"
    draft = p.propose(p.ModelSummary(repo="Example/Qwen2.5-0.5B-Instruct", task="text-generation", commit="a" * 40))
    assert draft.block_id == "llm-qwen2.5-0.5b-instruct" and draft.spec["title"].startswith("Qwen2.5-0.5B-Instruct")
    assert "aaaaaaaaaaaa" in draft.spec["summary"]


def test_labels_and_lines_hold_no_path_and_the_edited_text_is_checked_again():
    draft = p.propose(p.ModelSummary(repo="example/det", task="object-detection", source="hub",
                                     files=(("model.onnx", 5),)))
    labels = draft.labels()
    assert set(labels) == {"facts", "task", "rule", "block", "reasons", "needs_person", "model_card"}
    assert labels["facts"]["model"] == "example/det"
    text = "\n".join(draft.lines())
    assert "Why:" in text and "What the engine needs to know:" in text and "You decide:" in text
    spec = p.spec_from_text(draft.yaml_text())
    assert spec == draft.spec
    broken = draft.yaml_text().replace("protocol: http-multipart", "protocol: ftp")
    with pytest.raises(BlockError):
        p.spec_from_text(broken)


def test_an_ai_note_is_kept_for_the_person_bounded_and_cleaned(tmp_path):
    notes = p.ProposalNotes(tmp_path)
    for i in range(12):
        notes.add("example/det", f"note {i}​ with more limits", "ditto")
    kept = notes.for_model("example/det")
    assert len(kept) == p.MAX_NOTES_PER_MODEL and kept[-1].note == "note 11 with more limits"
    assert notes.add("example/other", "x" * 5000, "mcp").note.startswith("x" * p.MAX_NOTE)
    assert notes.for_model("nothing") == []
