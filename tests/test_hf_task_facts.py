"""The pipeline task and library of a model (orchestrator/Core/hf_hub.py): HubClient.info carries them into ModelFacts
from the one lookup it makes (model_info's pipeline_tag and library_name, else the model card's), with the offline
refusal and the token rules unchanged; folder_facts reads a local model folder without the Hub (config.json, the
README's front matter, the files with their sizes, the classes and the input size), never following a link and never
naming its path. The Hub is a stand-in: no network."""

import json
from types import SimpleNamespace

import pytest

from orchestrator.Core import hf_hub
from orchestrator.runtime.bundle import propose

COMMIT = "b" * 40


class Api:
    def __init__(self, calls, info):
        self.calls, self.info = calls, info

    def model_info(self, repo, **kwargs):
        self.calls.append((repo, kwargs.get("token")))
        return self.info

    def auth_check(self, repo, **kwargs):
        return True


def _client(info, offline=False, token=None):
    calls, tokens = [], []
    config = SimpleNamespace(get=lambda key, default=None: offline if key == "offline_mode" else default)
    framework = SimpleNamespace(config=config, secrets_manager=SimpleNamespace(get_secret=lambda key: token))

    def factory(t):
        tokens.append(t)
        return Api(calls, info)
    return hf_hub.HubClient(framework, api_factory=factory), calls, tokens


def _info(**kw):
    base = dict(sha=COMMIT, gated=False, card_data={"license": "mit"}, tags=[], safetensors=None,
                siblings=[SimpleNamespace(rfilename="model.onnx", size=5_000_000, lfs=None),
                          SimpleNamespace(rfilename="config.json", size=100, lfs=None)])
    base.update(kw)
    return SimpleNamespace(**base)


def test_info_carries_the_pipeline_task_and_library_from_the_one_lookup():
    client, calls, tokens = _client(_info(pipeline_tag="object-detection", library_name="transformers"))
    facts = client.info("example/det")
    assert (facts.pipeline_tag, facts.library_name, facts.source) == ("object-detection", "transformers", "hub")
    assert calls == [("example/det", False)] and tokens == [False]   # one lookup, without a token for a public model


def test_the_card_names_them_where_model_info_does_not_and_odd_words_are_dropped():
    client, _calls, _tokens = _client(_info(card_data={"license": "mit", "pipeline_tag": "Text-Generation",
                                                       "library_name": "gguf"}))
    facts = client.info("example/chat")
    assert (facts.pipeline_tag, facts.library_name) == ("text-generation", "gguf")
    client, _calls, _tokens = _client(_info(pipeline_tag="rm -rf /", library_name=["x"]))
    facts = client.info("example/odd")
    assert (facts.pipeline_tag, facts.library_name) == ("", "")


def test_offline_mode_refuses_before_any_request():
    client, calls, tokens = _client(_info(pipeline_tag="text-generation"), offline=True)
    with pytest.raises(hf_hub.HubError) as e:
        client.info("example/chat")
    assert e.value.blocker == "offline_mode" and calls == [] and tokens == []


def test_a_draft_from_hub_facts_names_the_model_and_its_commit():
    client, _calls, _tokens = _client(_info(pipeline_tag="object-detection"))
    draft = propose.propose(propose.summary_from_facts(client.info("example/det-tiny")))
    assert draft.kind == "detect" and draft.block_id == "detect-det-tiny"
    assert draft.facts["commit"] == COMMIT and draft.facts["licence"] == "mit"
    assert next(f for f in draft.spec["files"] if f["role"] == "model")["formats"] == ["onnx"]


def test_folder_facts_read_the_task_classes_and_size_without_the_hub(tmp_path):
    folder = tmp_path / "my-detector"
    (folder / "sub").mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({"id2label": {str(i): f"c{i}" for i in range(80)},
                                                    "image_size": 640}), encoding="utf-8")
    (folder / "README.md").write_text("---\nlicense: apache-2.0\npipeline_tag: object-detection\n"
                                      "library_name: transformers\n---\n# A detector\n", encoding="utf-8")
    (folder / "sub" / "model.onnx").write_bytes(b"x" * 1000)
    (folder / ".hidden").write_text("x", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    try:
        (folder / "link.txt").symlink_to(outside)
    except OSError:
        pass
    facts = hf_hub.folder_facts(folder)
    assert (facts.pipeline_tag, facts.library_name, facts.license) == ("object-detection", "transformers",
                                                                       "apache-2.0")
    assert (facts.classes, facts.image_size, facts.source, facts.folder_name) == (80, 640, "folder", "my-detector")
    assert sorted(f.name for f in facts.files) == ["README.md", "config.json", "sub/model.onnx"]
    draft = propose.propose(propose.summary_from_facts(facts))
    assert draft.block_id == "detect-my-detector" and draft.facts["source"] == "folder"
    text = "\n".join(draft.lines()) + draft.guide_text() + json.dumps(draft.labels())
    assert str(tmp_path) not in text and "80 classes" in text and "640 px" in text


def test_folder_facts_take_the_task_from_config_json_and_the_size_from_the_preprocessor(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"pipeline_tag": "image-classification"}), encoding="utf-8")
    (tmp_path / "preprocessor_config.json").write_text(json.dumps({"size": {"height": 224, "width": 224}}),
                                                       encoding="utf-8")
    facts = hf_hub.folder_facts(tmp_path)
    assert facts.pipeline_tag == "image-classification" and facts.image_size == 224 and facts.classes is None


def test_folder_facts_without_any_task_and_broken_files(tmp_path):
    (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "README.md").write_text("---\n: : [\n---\n", encoding="utf-8")
    facts = hf_hub.folder_facts(tmp_path)
    assert facts.pipeline_tag == "" and facts.license == "unknown"
    assert propose.propose(propose.summary_from_facts(facts)).rule == "unknown"
    with pytest.raises(ValueError):
        hf_hub.folder_facts(tmp_path / "missing")
