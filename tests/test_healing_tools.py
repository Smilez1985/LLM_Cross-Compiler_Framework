"""The self-healing "Docker cleanup" for a full Docker disk (docker_prune; round 6 7.7, next-fixes items 100 and 104).

It deletes nothing itself and is never decided by an AI: it is a manual step that points to the one deletion path
for the framework's Docker images, which names every image with name, id, size and what it is (Tools > Framework Build
Images... in the GUI, also a button in the healing dialog; 'llm-cli doctor --fix --tier 2' at a terminal).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_docker_prune_never_delegated_and_lists_images():
    from orchestrator.Core.self_healing_manager import AUTOMATIC_STRATEGIES, DOCKER_DISK_STEP
    from orchestrator.tools import facts_catalog
    from orchestrator.tools.framework_tools import DESTRUCTIVE_STRATEGIES, NO_TOOL_ACTIONS
    assert "docker_prune" not in AUTOMATIC_STRATEGIES and not DESTRUCTIVE_STRATEGIES, "approving it runs nothing"
    for words in ("Tools > Framework Build Images...", "llm-cli doctor --fix --tier 2", "name, id, size and what",
                  "tick and confirm", "nothing is ticked by default", "build cache is not touched"):
        assert words in DOCKER_DISK_STEP, words
    assert facts_catalog.TOOL_FACTS["resolve_healing"].deletes is False
    assert "Tools > Framework Build Images..." in dict(NO_TOOL_ACTIONS)["empty_trash"]
    # One deletion path for Docker images, reached only from the person's windows and terminal: the GUI's image list
    # and the person's list Mojo Miracle opens (docker_objects.offer_image_list), which only the terminal (the CLI)
    # and the GUI's Mojo window call.
    def callers(name):
        return sorted(p.relative_to(REPO).as_posix() for p in (REPO / "orchestrator").rglob("*.py")
                      if f"{name}(" in p.read_text(encoding="utf-8").replace(f"def {name}(", ""))
    assert callers("delete_framework_images") == ["orchestrator/Core/mojo/docker_objects.py",
                                                  "orchestrator/gui/deletion_dialog.py"]
    assert callers("offer_image_list") == ["orchestrator/cli.py", "orchestrator/gui/mojo_window.py"]
    objects = (REPO / "orchestrator" / "Core" / "mojo" / "docker_objects.py").read_text(encoding="utf-8")
    assert objects.count("delete_framework_images(") == 2, "its definition and the one call in offer_image_list"
    healer = (REPO / "orchestrator" / "Core" / "self_healing_manager.py").read_text(encoding="utf-8")
    assert "images.remove" not in healer and ".prune(" not in healer
