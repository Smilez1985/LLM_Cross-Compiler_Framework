"""Build standing approvals, recipe builds and the shared disk reserve (MCP design 2.8, 6.5, 6.10; T9 round 3).

- A target or recipe pack an AI installed under a standing approval is built under a build standing approval only
  after the person confirmed one build of it in the framework's own window; after that it is (both directions). An
  elicitation answer, a standing approval's own use or an "allow" never counts as that confirmation.
- A digest-pinned base image outside the Docker Official Images counts like an official one once the person confirmed
  a build of the target with exactly that image; a tag never does.
- Calls under a standing approval leave the larger disk reserve (20 GiB or 10 %), checked inside the cross-process
  reservation ledger under its lock, for start_build, download_hf_file and add_pack_from_index.
- start_recipe_build builds an installed recipe exactly as pinned, through start_build's two-phase path.
- lcf://packs/index mirrors list_packs; run_benchmark runs the very file it hashed; the gh/git runner of
  submit_contribution never reads stdin.
"""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from orchestrator.Core.community_manager import PROVENANCE_NAME, read_provenance, submit_contribution  # noqa: E402
from orchestrator.Core.confirmed_builds import ConfirmedBuilds, person_confirmed, pinned  # noqa: E402
from orchestrator.Core.disk_plan import DiskNeed, ReservationLedger  # noqa: E402
from orchestrator.Core.job_service import JobService  # noqa: E402
from orchestrator.Core.target_manager import TargetManager  # noqa: E402
from orchestrator.tools import tools_community  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.framework_tools import register_framework_tools  # noqa: E402
from orchestrator.tools.grants import GrantOffer  # noqa: E402
from orchestrator.tools.policy import PolicyStore, ToolPolicy  # noqa: E402
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, current_question  # noqa: E402
from test_build_tools import GB, MODEL, Cfg, Orchestrator, Service  # noqa: E402
from test_hf_tools import env  # noqa: E402,F401  (the Hugging Face tools' environment)
from test_recipes import COMMIT, recipe_data  # noqa: E402
from test_zoo import World, pack_bytes  # noqa: E402

BUILD = {"model": MODEL, "target": "demo-board", "format": "gguf", "board": "demo", "quantization": "Q4_K_M"}
L4T = "nvcr.io/nvidia/l4t-base@sha256:" + "c" * 64


# --- the environment: the community zoo and the build service in one framework ---------------------------------------

@pytest.fixture
def zoo(tmp_path, monkeypatch):
    disk = SimpleNamespace(total=10 ** 12, used=0, free=10 ** 12)   # the pack disk check never reads this machine
    monkeypatch.setattr(tools_community.shutil, "disk_usage", lambda p: disk)
    world = World(tmp_path)
    world.values["offline_mode"] = False
    cfg = Cfg(tmp_path)
    cfg.targets_dir = str(world.root / "targets")
    cfg.values.update(world.values, targets_dir=str(world.root / "targets"))
    framework = SimpleNamespace(config=cfg, resolve_path=lambda p: Path(p), orchestrator=Orchestrator(),
                                community_manager=world.cm, job_service=JobService())
    framework.run_coroutine = lambda coro, timeout=None: asyncio.run(coro)
    framework.target_manager = TargetManager(framework)
    framework.build_service = Service(framework)
    registry = register_framework_tools(ToolRegistry(PolicyStore(tmp_path / "policy"), AuditLog(tmp_path / "a.db")),
                                        framework)
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
    yield SimpleNamespace(world=world, cm=world.cm, framework=framework, service=framework.build_service,
                          registry=registry, orch=framework.orchestrator, jobs=framework.job_service, tmp=tmp_path,
                          disk=disk, state=Path(cfg.user_state_dir))


def wait_job(env, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = env.jobs.get(job_id)
        if job.finished:
            return job
        time.sleep(0.02)
    raise AssertionError("the job did not end")


def ask(env, name, arguments, answer=True, approved=None, **ctx):
    shown = []
    result = env.registry.call(name, arguments, ToolContext(
        "ditto", confirm=lambda s, i: shown.append(current_question()) or answer, **ctx), approved=approved)
    return result, shown


def pack_grant(env, kind, pack):
    spec = env.registry.get("add_pack_from_index")
    inp = spec.input_model(kind=kind, pack_id=pack)
    ctx = ToolContext("ditto")
    ctx.facts = env.registry.look_up(spec, inp, ctx)
    offer = env.registry.grant_offer(spec, inp, ctx)
    assert offer is not None
    return env.registry.create_grant(offer, "this", "revoke", "gui", "gui")


def build_grant(env, tool="start_build", target="demo-board", model=MODEL):
    """A build standing approval the person chose in the framework's window ("this model", up to 20 GB)."""
    spec = env.registry.get(tool)
    scope = {"target": target, "source": "hf", "max_gb": 20, "model": model}
    offer = GrantOffer(tool=tool, caller="ditto", rule_version=spec.grant.version, scopes={"this": scope},
                       summaries={"this": "builds"}, durations=["revoke"], warning="builds without asking")
    return env.registry.create_grant(offer, "this", "revoke", "gui", "gui")


def install_under_grant(env, trust="verified", **pack):
    env.world.add(env.world.target_pack(**pack), trust=trust)
    env.world.publish()
    granted = pack_grant(env, "target", "demo-board")
    out, shown = ask(env, "add_pack_from_index", {"kind": "target", "pack_id": "demo-board"})
    assert shown == [] and wait_job(env, out.job_id).state == "done"
    prov = read_provenance(env.cm.targets_dir / "demo-board")
    assert prov.installed_via == f"grant:{granted.id}"
    return prov


def channels(env, tool="start_build"):
    return [e.get("confirm_channel") for e in env.registry.audit.entries(500)
            if e.get("tool") == tool and e.get("outcome") == "started"]


# --- the confirmed build, both directions ---------------------------------------------------------------------------

def test_add_pack_then_start_build_is_asked_until_one_build_was_confirmed_then_covered(zoo):
    """End to end: a verified target pack installed under a pack standing approval, then builds of it under a build
    standing approval. The first build is asked (never "install third-party build code" plus "run its image build"
    without a question); the person's confirmation is recorded; the next build runs under the standing approval."""
    prov = install_under_grant(zoo)
    granted = build_grant(zoo)
    first, shown = ask(zoo, "start_build", BUILD)
    assert len(shown) == 1 and first.request_id, "the first build of a pack installed under a grant is asked"
    store = ConfirmedBuilds(zoo.state)
    assert store.confirmed("target", "demo-board", installed_via=prov.installed_via, pack_sha256=prov.pack_sha256)
    second, shown = ask(zoo, "start_build", BUILD)
    assert shown == [] and second.request_id, "after the confirmed build the build standing approval covers it"
    assert channels(zoo).count(f"grant:{granted.id}") == 1
    assert len(zoo.orch.requests) == 2


def test_an_elicitation_answer_is_not_a_confirmed_build(zoo):
    """An accepted MCP form is the client's statement (an automated client may answer by itself): it never unlocks
    the build standing approval."""
    install_under_grant(zoo)
    build_grant(zoo)
    ask(zoo, "start_build", BUILD, approved=True, confirm_channel="elicitation", answer="accepted")
    assert len(zoo.orch.requests) == 1
    assert ConfirmedBuilds(zoo.state).get("target", "demo-board") is None
    _, shown = ask(zoo, "start_build", BUILD)
    assert len(shown) == 1, "still asked: only the framework's own window counts"


def test_a_standing_approvals_own_use_and_an_allow_are_not_confirmations():
    assert not person_confirmed("grant:g_0123456789abcdef")
    assert not person_confirmed("elicitation") and not person_confirmed("") and not person_confirmed("none")
    assert person_confirmed("framework-gui") and person_confirmed("framework-cli")
    assert person_confirmed("approval:ap_1") and person_confirmed("framework")


def test_a_reinstall_under_another_standing_approval_needs_a_new_confirmation(tmp_path):
    store = ConfirmedBuilds(tmp_path)
    assert store.record("target", "Demo", installed_via="grant:g_" + "1" * 16, pack_sha256="a" * 64,
                        channel="framework-gui")
    assert store.confirmed("target", "demo", installed_via="grant:g_" + "1" * 16, pack_sha256="a" * 64)
    assert not store.confirmed("target", "demo", installed_via="grant:g_" + "2" * 16, pack_sha256="a" * 64)
    assert not store.confirmed("target", "demo", installed_via="grant:g_" + "1" * 16, pack_sha256="b" * 64)
    assert not store.record("target", "other", channel="elicitation"), "nothing is written for an elicitation"
    assert oct(os.stat(store.path).st_mode & 0o777) == "0o600"


def test_a_pinned_vendor_image_counts_after_a_confirmed_build_and_a_tag_never(zoo):
    zoo.world.add(zoo.world.target_pack(dockerfile=f"FROM {L4T}\nRUN echo ok\n"), trust="verified")
    zoo.world.publish()
    zoo.world.install()          # installed by the person: no standing approval involved
    build_grant(zoo)
    _, shown = ask(zoo, "start_build", BUILD)
    assert len(shown) == 1, "a vendor image the person never confirmed is asked"
    _, shown = ask(zoo, "start_build", BUILD)
    assert shown == [], "the same pinned image, confirmed in a build of this target"
    assert pinned(L4T) and not pinned("nvcr.io/nvidia/l4t-base:r36") and not pinned("debian:bookworm-slim")


def test_only_digest_pinned_images_are_remembered(tmp_path):
    """A tag can later name another image: it is never remembered as confirmed (packs refuse tags anyway)."""
    store = ConfirmedBuilds(tmp_path)
    store.record("target", "t", base_images=[L4T, "nvcr.io/nvidia/l4t-base:r36"], channel="framework-gui")
    assert store.image_confirmed("t", L4T)
    assert not store.image_confirmed("t", "nvcr.io/nvidia/l4t-base:r36")
    assert store.get("target", "t")["base_images"] == [L4T]


# --- the larger reserve, under the ledger's lock --------------------------------------------------------------------

def prepared(env, tool, arguments):
    """The facts of a call as its question would show them (the MCP server hands them in like this)."""
    spec = env.registry.get(tool)
    ctx = ToolContext("ditto")
    ctx.facts = env.registry.look_up(spec, spec.input_model(**arguments), ctx)
    return ctx


def test_a_build_under_a_standing_approval_keeps_the_larger_reserve_against_a_reservation_made_meanwhile(zoo):
    """Prepared while the disk kept 20 GiB / 10 % free; another process reserves space before it starts. Under the
    ledger's lock the standing reserve no longer holds: nothing starts (the normal reserve would have)."""
    zoo.world.add(zoo.world.target_pack(), trust="verified")
    zoo.world.publish()
    zoo.world.install()
    build_grant(zoo)
    ctx = prepared(zoo, "start_build", BUILD)
    assert ctx.facts.bound["grant"] is not None
    ledger = zoo.service.disk_ledger()
    ledger.reserve([DiskNeed("cache", Path(zoo.framework.config.values["cache_dir"]), 480 * GB, "another build")],
                   "other", "another build", disk_usage=zoo.service.disk_usage)
    with pytest.raises(ToolError) as e:
        zoo.registry.call("start_build", BUILD, ctx)
    assert e.value.code == "insufficient_resources" and "standing approval" in e.value.message
    assert zoo.orch.requests == [] and len(ledger.entries()) == 1
    # outside the standing approval (the person asked) the normal reserve applies and the build starts
    _, shown = ask(zoo, "start_build", BUILD)
    assert len(shown) == 1 and len(zoo.orch.requests) == 1


def test_a_pack_import_takes_a_ledger_reservation_and_keeps_the_larger_reserve_under_a_standing_approval(zoo):
    from test_community_tools import knowledge_pack
    zoo.world.add(knowledge_pack(zoo.world, "rk-docs"), kind="knowledge", pack_id="rk-docs", version="1.0.0")
    zoo.world.add(knowledge_pack(zoo.world, "more-docs"), kind="knowledge", pack_id="more-docs", version="1.0.0")
    zoo.world.publish()
    pack_grant(zoo, "knowledge", "rk-docs")
    ctx = prepared(zoo, "add_pack_from_index", {"kind": "knowledge", "pack_id": "more-docs"})
    assert ctx.facts.bound["grant"] is not None
    ledger = ReservationLedger(zoo.state)
    ledger.reserve([DiskNeed("packs", zoo.cm._install_root("knowledge"), 950 * GB, "a download")], "other",
                   "a download", disk_usage=lambda p: zoo.disk)
    with pytest.raises(ToolError) as e:
        zoo.registry.call("add_pack_from_index", {"kind": "knowledge", "pack_id": "more-docs"}, ctx)
    assert e.value.code == "insufficient_resources" and not (zoo.cm.knowledge_dir / "more-docs").exists()
    assert len(ledger.entries()) == 1


def test_a_pack_import_counts_other_processes_reservations_before_the_question(zoo):
    zoo.world.add(zoo.world.target_pack())
    zoo.world.publish()
    ReservationLedger(zoo.state).reserve([DiskNeed("packs", zoo.cm.targets_dir, 980 * GB, "a build")], "other",
                                         "a build", disk_usage=lambda p: zoo.disk)
    shown = []
    with pytest.raises(ToolError) as e:
        zoo.registry.call("add_pack_from_index", {"kind": "target", "pack_id": "demo-board"},
                          ToolContext("ditto", confirm=lambda s, i: shown.append(1) or True))
    assert e.value.code == "insufficient_resources" and "reserved by other calls" in e.value.message
    assert shown == [], "refused before the question and before the download"


def test_a_download_under_a_standing_approval_keeps_the_larger_reserve_under_the_ledger_lock(env, tmp_path):
    import test_hf_tools as hft
    env.registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in env.registry.list()]))
    spec = env.registry.get("download_hf_file")
    arguments = {"model": hft.REPO, "filename": "tiny-q4_k_m.gguf"}
    ctx = ToolContext("ditto")
    ctx.facts = env.registry.look_up(spec, spec.input_model(**arguments), ctx)
    offer = env.registry.grant_offer(spec, spec.input_model(**arguments), ctx)
    env.registry.create_grant(offer, "this", "revoke", "gui", "gui")
    disk = SimpleNamespace(total=10 ** 12, used=0, free=10 ** 12)
    ledger = ReservationLedger(tmp_path / "state")
    ledger.reserve([DiskNeed("models", tmp_path / "models", 950 * GB, "a build")], "other", "a build",
                   disk_usage=lambda p: disk)
    with pytest.raises(ToolError) as e:
        env.registry.call("download_hf_file", arguments, ctx)
    assert e.value.code == "insufficient_resources" and "standing approval" in e.value.message
    assert len(ledger.entries()) == 1 and env.jobs.list() == [], "nothing was downloaded"


# --- start_recipe_build ---------------------------------------------------------------------------------------------

def install_recipe(env, recipe_id="demo-recipe", **changes):
    data = recipe_data()
    data.update(id=recipe_id, target={"id": "demo-board", "pack_sha256": "a" * 64}, format="gguf", board="demo",
                quantization="Q4_K_M", **changes)
    root = env.tmp / "recipes-src" / recipe_id
    root.mkdir(parents=True)
    (root / "recipe.yml").write_text(json.dumps(data))
    zipped = env.tmp / f"{recipe_id}.zip"
    zipped.write_bytes(pack_bytes(root, kind="recipe", pack_id=recipe_id, version="1.0.0"))
    env.cm.finish_install(env.cm.prepare_install_file(zipped))
    return env.cm.recipes_dir / recipe_id


@pytest.fixture
def recipes(zoo):
    zoo.world.add(zoo.world.target_pack(), trust="verified")
    zoo.world.publish()
    zoo.world.install()
    return zoo


def test_a_recipe_is_built_exactly_as_pinned_through_start_builds_question(recipes):
    install_recipe(recipes)
    out, shown = ask(recipes, "start_recipe_build", {"recipe_id": "demo-recipe"})
    first = shown[0].splitlines()[0]
    assert first.startswith("Builds the community recipe demo-recipe: Downloads about 15.0 GB")
    assert "Recipe (from the recipe pack, not checked): demo-recipe by alice" in shown[0]
    assert "target's files differ" in shown[0]
    request = recipes.orch.requests[0]
    assert out.request_id == request.request_id and request.model_revision == COMMIT
    assert request.targets == ["demo-board"] and request.quantization_options == ["Q4_K_M"]
    assert request.max_context_length == 4096 and request.disk_reservation, "the same disk reservation as start_build"
    assert recipes.service.lookups[-1] == ("Qwen/Qwen3.5-2B", COMMIT), "the recipe's commit, never the branch"


def test_an_unknown_recipe_and_a_dataset_the_recipe_does_not_name_are_refused_before_the_question(recipes):
    install_recipe(recipes)
    for arguments, code in (({"recipe_id": "nope"}, "unknown_id"),
                            ({"recipe_id": "demo-recipe", "dataset_path": "datasets:x.txt"}, "invalid_value")):
        shown = []
        with pytest.raises(ToolError) as e:
            recipes.registry.call("start_recipe_build", arguments,
                                  ToolContext("ditto", confirm=lambda s, i: shown.append(1) or True))
        assert e.value.code == code and shown == []
    assert recipes.orch.requests == []


def test_a_recipe_that_names_a_dataset_needs_exactly_that_file(recipes, monkeypatch):
    from orchestrator.schemas.recipes import Recipe
    install_recipe(recipes)
    real = recipes.service.installed_recipe
    named = {"name": "calib.txt", "sha256": "d" * 64}

    def with_dataset(recipe_id):
        recipe, prov = real(recipe_id)
        data = recipe.model_dump(mode="json")
        data["options"]["dataset"] = named
        return Recipe.model_validate(data), prov
    monkeypatch.setattr(recipes.service, "installed_recipe", with_dataset)
    with pytest.raises(ToolError) as e:
        ask(recipes, "start_recipe_build", {"recipe_id": "demo-recipe"})
    assert e.value.code == "missing_argument" and "calib.txt" in e.value.message
    other = recipes.tmp / "calib.txt"
    other.write_text("not the pinned file")
    with pytest.raises(Exception) as e:
        recipes.service.check_recipe_dataset(with_dataset("demo-recipe")[0], str(other))
    assert "sha256 differs" in str(e.value)


def test_a_recipe_changed_after_the_question_is_not_built(recipes):
    folder = install_recipe(recipes)

    def change(spec, inp):
        data = json.loads((folder / "recipe.yml").read_text())
        data["quantization"] = "Q8_0"
        (folder / "recipe.yml").write_text(json.dumps(data))
        return True
    with pytest.raises(ToolError) as e:
        recipes.registry.call("start_recipe_build", {"recipe_id": "demo-recipe"}, ToolContext("ditto", confirm=change))
    assert "changed after the question" in e.value.message and recipes.orch.requests == []
    assert recipes.service.disk_ledger().entries() == [], "the reservation is released"


def test_a_recipe_installed_under_a_standing_approval_is_covered_only_after_a_confirmed_build(recipes):
    folder = install_recipe(recipes)
    prov = json.loads((folder / PROVENANCE_NAME).read_text())
    prov["installed_via"] = "grant:g_" + "e" * 16
    (folder / PROVENANCE_NAME).write_text(json.dumps(prov))
    granted = build_grant(recipes, tool="start_recipe_build", model="Qwen/Qwen3.5-2B")
    _, shown = ask(recipes, "start_recipe_build", {"recipe_id": "demo-recipe"})
    assert len(shown) == 1
    _, shown = ask(recipes, "start_recipe_build", {"recipe_id": "demo-recipe"})
    assert shown == [] and f"grant:{granted.id}" in channels(recipes, "start_recipe_build")


def test_the_packs_index_resource_mirrors_list_packs(zoo):
    zoo.world.add(zoo.world.target_pack())
    zoo.world.publish()
    zoo.cm.load_index(refresh=True)
    text, external = zoo.registry.read_resource("lcf://packs/index", {}, ToolContext("mcp"))
    assert "demo-board" in text and external
    zoo.world.session.calls.clear()
    zoo.registry.read_resource("lcf://packs/index", {}, ToolContext("mcp"))
    assert zoo.world.session.calls == [], "never refreshed"


# --- run_benchmark: the file it hashed is the file it runs ------------------------------------------------------------

def bench_runner(tmp_path, popen):
    from orchestrator.runtime import bench
    runner = bench.BenchRunner(tmp_path / "state", popen=popen)
    build = SimpleNamespace(build_id="b" * 64, backend="cpu")
    runner.bench_build = lambda sandbox=False: (build, Path("/bin/true"))
    return runner


class Proc:
    pid, returncode = -1, 0

    def poll(self):
        return 0


RESULT = b'[{"n_prompt": 512, "n_gen": 0, "avg_ts": 100.0}, {"n_prompt": 0, "n_gen": 128, "avg_ts": 10.0}]'


@pytest.mark.skipif(os.name == "nt", reason="descriptor paths are POSIX")
def test_the_benchmark_reads_the_open_file_it_hashed_even_if_the_path_is_swapped(tmp_path, monkeypatch):
    from orchestrator.runtime import bench, server_policy
    monkeypatch.setattr(server_policy, "check_command", lambda argv: None)
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF trusted catalog model")
    digest = bench.sha256_file(model)
    seen = {}

    def popen(argv, stdout=None, stderr=None, **kw):
        swapped = tmp_path / "evil.gguf"
        swapped.write_bytes(b"GGUF something else")
        os.replace(swapped, model)          # the path now names another file
        path = argv[argv.index("-m") + 1]
        assert kw.get("pass_fds"), "the checked descriptor is handed to llama-bench"
        seen["read"] = Path(path).read_bytes()
        stdout.write(RESULT)
        return Proc()
    run = bench_runner(tmp_path, popen).run(model, kind="tier_speed", label="tier", trusted=True, sha256=digest)
    assert seen["read"] == b"GGUF trusted catalog model" and run.gen_ts == 10.0


def test_a_model_whose_hash_differs_never_starts(tmp_path, monkeypatch):
    from orchestrator.runtime import bench, server_policy
    monkeypatch.setattr(server_policy, "check_command", lambda argv: None)
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF swapped before the run")
    started = []
    with pytest.raises(bench.BenchError) as e:
        bench_runner(tmp_path, lambda *a, **k: started.append(a)).run(model, kind="tier_speed", label="tier",
                                                                       trusted=True, sha256="0" * 64)
    assert "changed after it was checked" in str(e.value) and started == []


@pytest.mark.skipif(os.name == "nt", reason="descriptor paths are POSIX")
def test_the_sandbox_mounts_exactly_the_open_file(tmp_path):
    from orchestrator.runtime import bench
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF checked")
    fd = bench.open_pinned(model, bench.sha256_file(model), "m")
    try:
        (tmp_path / "x.gguf").write_bytes(b"GGUF other")
        os.replace(tmp_path / "x.gguf", model)
        folder = tmp_path / "pinned"
        folder.mkdir()
        copy = bench.BenchRunner._pinned_copy(model, fd, folder)
        assert copy.read_bytes() == b"GGUF checked"
    finally:
        os.close(fd)


# --- submit_contribution: gh and git never read stdin ------------------------------------------------------------------

def test_the_gh_and_git_runner_never_reads_stdin(tmp_path):
    pack = tmp_path / "contribution-target-x-1.0.0.zip"
    pack.write_bytes(b"zip")
    seen = []

    def runner(cmd, **kwargs):
        seen.append(kwargs.get("stdin"))
        if cmd[1:3] == ["repo", "fork"]:
            (Path(kwargs["cwd"]) / "zoo").mkdir()
        out = "pat" if cmd[1:3] == ["api", "user"] else "https://github.com/x/zoo/pull/1"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    submit_contribution(pack, "x/zoo", "target", runner=runner, which=lambda name: f"/usr/bin/{name}",
                        opener=lambda url: pytest.fail("the browser was opened"))
    assert len(seen) >= 8 and all(s is subprocess.DEVNULL for s in seen), seen

