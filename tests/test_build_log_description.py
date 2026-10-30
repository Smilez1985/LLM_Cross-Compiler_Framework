"""read_build_log and the build-log resource say that an AI reads the log cleaned (chain followups-3): colour codes and
redraws removed, repeated lines collapsed, failure lines kept, and that the person sees the raw log
(utils/logtext.clean_lines, BuildService.log_tail)."""

import pytest

pytest.importorskip("mcp")


def _texts():
    from orchestrator.tools import framework_resources
    from orchestrator.tools.tools_builds import build_tool_specs
    import inspect
    return inspect.getsource(build_tool_specs), inspect.getsource(framework_resources)


def test_the_tool_and_the_resource_say_the_log_is_cleaned_and_the_person_sees_it_raw():
    tool, resource = (" ".join(t.split()) for t in _texts())
    start = tool.index('ToolSpec("read_build_log"')
    tool = tool[start:start + 900]
    for text in (tool, resource[resource.index("BUILD_LOG_URI, \"build-log\""):][:700]):
        assert "colour codes" in text and "collapsed" in text and "failure lines kept" in text.replace(
            "every failure line kept", "failure lines kept"), text
        assert "raw log" in text
