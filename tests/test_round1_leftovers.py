"""Leftovers of MCP phase T round 1 (T1, T2, T6), each pinned by a test that fails without it.

- download_hf_file reserves its disk space in the cross-process reservation ledger builds use, so a download and a
  build (or two downloads in two processes) never take the same free space.
- start_build has the standing-approval rule of design 6.10 (shipped or verified target, safetensors or GGUF, ungated
  with an ungated base model, permissive licence, official base image, size class), with the larger reserve.
- report_cannot_do counts only the calls of the current session.
- check_build_request's Hugging Face look-ups count toward the 30 per hour Hugging Face rate.
(Staging through the T6 fetcher and the disk watchdog during conversion are tested in test_build_tools.py.)
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from orchestrator.Core import disk_plan  # noqa: E402
from orchestrator.tools.grants import BUILD_RULE  # noqa: E402
from orchestrator.tools.registry import ToolContext, ToolError  # noqa: E402
from orchestrator.tools.tools_hf import SEARCH_RATE  # noqa: E402
from test_build_tools import BUILD, GB, MODEL, ask, env, facts  # noqa: E402,F401 - env is the fixture
from test_hf_tools import REPO  # noqa: E402
from test_hf_tools import env as hf_env  # noqa: E402,F401 - fixture
from test_hf_tools import call as hf_call  # noqa: E402
from test_hf_tools import wait_job  # noqa: E402


# --- download_hf_file and the shared ledger -------------------------------------------------------------------------

class TestDownloadLedger:

    def ledger(self, hf_env):
        return disk_plan.ReservationLedger(Path(hf_env.framework.config.user_state_dir))

    def test_space_another_process_reserved_is_counted_before_the_question(self, hf_env, monkeypatch):
        from orchestrator.Core import hf_hub
        usage = SimpleNamespace(total=100 * GB, used=0, free=4 * GB)  # reserve 2 GiB: 1.8 GB left for the file
        monkeypatch.setattr(hf_hub.shutil, "disk_usage", lambda p: usage)
        spec = hf_env.registry.get("download_hf_file")
        inp = spec.input_model(model=REPO, filename="tiny-q4_k_m.gguf")
        assert hf_env.registry.look_up(spec, inp, ToolContext("mcp"))  # fits
        # a build of another framework process reserved 1.9 GB on the same disk (when it had more room)
        models = Path(hf_env.settings["models_dir"])
        roomy = SimpleNamespace(total=100 * GB, used=0, free=50 * GB)
        self.ledger(hf_env).reserve([disk_plan.DiskNeed("cache", models, int(1.9 * GB), "a build")], "gui", "build",
                                    disk_usage=lambda p: roomy)
        with pytest.raises(ToolError) as exc:
            hf_env.registry.look_up(spec, inp, ToolContext("mcp"))
        assert exc.value.code == "insufficient_resources" and "reserved by other" in exc.value.message

    def test_an_accepted_download_holds_a_ledger_reservation_until_it_ends(self, hf_env):
        out = hf_call(hf_env, "download_hf_file", {"model": REPO, "filename": "tiny-q4_k_m.gguf"})
        job = wait_job(hf_env, out.job_id)
        assert job.state == "done"
        assert self.ledger(hf_env).entries() == [], "released when the job ended"

    def test_the_reservation_is_taken_in_the_ledger_builds_use(self, hf_env, monkeypatch):
        seen = []
        original = disk_plan.ReservationLedger.reserve

        def spy(self, needs, owner, label, **kw):
            rid, checks = original(self, needs, owner, label, **kw)
            seen.append((label, [n.label for n in needs], self.path))
            return rid, checks
        monkeypatch.setattr(disk_plan.ReservationLedger, "reserve", spy)
        out = hf_call(hf_env, "download_hf_file", {"model": REPO, "filename": "tiny-q4_k_m.gguf"})
        wait_job(hf_env, out.job_id)
        (label, needs, path), = seen
        assert label == f"{REPO}/tiny-q4_k_m.gguf" and needs == ["models"]
        assert path == Path(hf_env.framework.config.user_state_dir) / disk_plan.LEDGER_FILE


# --- start_build's standing-approval rule ---------------------------------------------------------------------------

def grant_of(env, arguments=None):
    spec = env.registry.get("start_build")
    ctx = ToolContext("ditto")
    ctx.facts = env.registry.look_up(spec, spec.input_model(**(arguments or BUILD)), ctx)
    return spec, spec.grant.facts(None, ctx)


class TestBuildGrant:

    def test_start_build_offers_the_design_rule(self, env):
        spec, granted = grant_of(env)
        assert spec.grant is not None and spec.grant.rule is BUILD_RULE
        assert granted["target_trust"] == "shipped" and granted["image_base_official"] is True
        assert granted["weights_format"] == "safetensors" and granted["gated"] is False
        assert granted["base_gated"] is False and granted["license"] == "apache-2.0"
        assert BUILD_RULE.scope(granted, "this") == {"target": "Rockchip", "source": "hf", "max_gb": 20,
                                                     "model": MODEL}

    @pytest.mark.parametrize("change", [{"weights": "pickle"}, {"gated": "auto"}, {"license": "llama3.1"},
                                        {"base_gated": True}, {"base_gated": None}])
    def test_outside_the_rule_the_person_is_asked(self, env, change):
        kw = dict(change)
        licence = kw.pop("license", None)
        base_gated = kw.pop("base_gated", False)
        env.service.hub = facts(**kw)
        env.service.hub.base_gated = base_gated
        if licence:
            env.service.hub.license = licence
        _, granted = grant_of(env)
        assert granted is None or BUILD_RULE.scope(granted, "this") is None, change

    def test_the_larger_reserve_of_a_standing_approval(self, env):
        env.service.free = 90 * GB  # enough for the build with 2 % reserve, not with the 10 % of a standing approval
        _, granted = grant_of(env)
        assert granted is None, "a standing approval must leave 20 GiB or 10 % free, or the person is asked"
        env.service.free = 600 * GB
        assert grant_of(env)[1] is not None

    def test_an_ai_generated_target_is_never_shipped(self, env, monkeypatch):
        from orchestrator.Core import community_manager
        monkeypatch.setattr(community_manager, "ai_generated_target", lambda folder: True)
        _, granted = grant_of(env)
        assert granted["target_trust"] == "other" and BUILD_RULE.scope(granted, "this") is None


# --- report_cannot_do: this session only ----------------------------------------------------------------------------

class TestCannotSession:

    def test_a_call_without_a_session_never_counts_earlier_sessions(self, booted_framework):
        from orchestrator.tools.policy import ToolPolicy
        registry = booted_framework.tool_registry
        registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
        registry.audit.record("ditto", "start_build", "local_write", "ask", "error", detail="failed earlier",
                              extra={"session": "old", "reason_code": "failed"})
        out = registry.call("report_cannot_do", {"task": "t", "reason": "failed", "missing": "m"},
                            ToolContext("ditto"))
        assert out.checked != "confirmed", "a failure of another session is no fact about this one"
        out = registry.call("report_cannot_do", {"task": "t", "reason": "failed", "missing": "m"},
                            ToolContext("ditto", session="old"))
        assert out.checked == "confirmed"


# --- check_build_request and the Hugging Face rate --------------------------------------------------------------

class TestCheckBuildHfRate:

    def test_hub_lookups_of_dry_runs_share_the_30_per_hour_rate(self, env):
        for _ in range(SEARCH_RATE.count):
            env.registry.call("check_build_request", BUILD, ToolContext("ditto", session="r"))
        lookups = len(env.service.lookups)
        with pytest.raises(ToolError) as exc:
            env.registry.call("check_build_request", BUILD, ToolContext("ditto", session="r2"))
        assert exc.value.code == "limit_reached" and "include_download_size" in str(exc.value)
        assert len(env.service.lookups) == lookups, "no Hugging Face request over the limit"
        out = env.registry.call("check_build_request", {**BUILD, "include_download_size": False},
                                ToolContext("ditto", session="r3"))
        assert out.decision in ("allow", "ask", "deny"), "a dry run without the look-up still works"
