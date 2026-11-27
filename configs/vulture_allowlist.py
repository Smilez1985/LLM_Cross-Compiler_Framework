"""
The exception list of the CI's dead-code step (scripts/dead_code.py; DESIGN, No dead code and no second system: the
second recon, decided by the maintainer on 2026-11-27).

vulture runs over `orchestrator` and `scripts` (the tests are no caller: a function only tests call counts as unwired).
Every function, method, class and property it reports as unused must stand here with its reason, else the step fails;
an entry that is no longer reported fails it too, so the list never keeps a name that is wired or gone. Functions
registered by a decorator (Click commands, pydantic validators, atexit, property setters) are recognised by the step
itself and are not listed.

Reasons:
- PROTOCOL: a method a library or a protocol calls by its name (ACP, Qt, Click, pydantic, MCP, html.parser, paramiko,
  urllib3), never this code.
- BY_NAME: a window's button handler, connected by its name in the window's button table (getattr).
- PREPARATION: a preparation kept for its roadmap row (decision A: the delegations, the enterprise mode's groundwork).
- TEST_API: a reading or test API only tests call (decision E).
- KEPT: kept by the maintainer's decision (2026-11-26: runner_text_problem, _validate_port_availability; 2026-11-27:
  configure_logger, log_exceptions and LoggingManager.export_logs, the logging toolkit).
- OPEN: found and reported to the maintainer, waiting for the decision per case (wire, fold into the better one, or
  remove with its replacement named).
"""

PROTOCOL = "protocol: called by a library or a protocol by its name"
BY_NAME = "by name: connected through the window's button table"
PREPARATION = "preparation (decision A): wired with its roadmap row"
TEST_API = "test API (decision E)"
KEPT = "kept by the maintainer's decision"
OPEN = "OPEN (reported 2026-11-27): waits for the maintainer's decision"

ALLOWED = {
    # PROTOCOL
    "orchestrator/acp_agent.py::on_connect": PROTOCOL,
    "orchestrator/acp_agent.py::set_config_option": PROTOCOL,
    "orchestrator/acp_agent.py::is_closing": PROTOCOL,
    "orchestrator/acp_agent.py::can_write_eof": PROTOCOL,
    "orchestrator/acp_agent.py::_drain_helper": PROTOCOL,
    "orchestrator/cli.py::shell_complete": PROTOCOL,
    "orchestrator/Core/acp_client.py::create_elicitation": PROTOCOL,
    "orchestrator/Core/acp_client.py::complete_elicitation": PROTOCOL,
    "orchestrator/Core/acp_client.py::read_text_file": PROTOCOL,
    "orchestrator/Core/acp_client.py::write_text_file": PROTOCOL,
    "orchestrator/Core/acp_client.py::create_terminal": PROTOCOL,
    "orchestrator/Core/acp_client.py::terminal_output": PROTOCOL,
    "orchestrator/Core/acp_client.py::release_terminal": PROTOCOL,
    "orchestrator/Core/acp_client.py::wait_for_terminal_exit": PROTOCOL,
    "orchestrator/Core/acp_client.py::kill_terminal": PROTOCOL,
    "orchestrator/Core/acp_client.py::ext_method": PROTOCOL,
    "orchestrator/Core/acp_client.py::ext_notification": PROTOCOL,
    "orchestrator/evals/ditto_eval.py::model_post_init": PROTOCOL,
    "orchestrator/gui/explain.py::eventFilter": PROTOCOL,
    "orchestrator/gui/knowledge_sources_window.py::dragEnterEvent": PROTOCOL,
    "orchestrator/gui/knowledge_sources_window.py::dropEvent": PROTOCOL,
    "orchestrator/gui/menus/tools_mcp.py::eventFilter": PROTOCOL,
    "orchestrator/gui/settings_pages.py::tabText": PROTOCOL,
    "orchestrator/gui/wizards.py::validatePage": PROTOCOL,
    "orchestrator/llm/types.py::model_post_init": PROTOCOL,
    "orchestrator/mcp_server.py::list_prompts": PROTOCOL,
    "orchestrator/rag/html_text.py::handle_startendtag": PROTOCOL,
    "orchestrator/rag/html_text.py::handle_comment": PROTOCOL,
    "orchestrator/rag/html_text.py::unknown_decl": PROTOCOL,
    "orchestrator/rag/html_text.py::handle_pi": PROTOCOL,
    "orchestrator/security/http.py::connect_unix_socket": PROTOCOL,
    "orchestrator/utils/ssh_trust.py::missing_host_key": PROTOCOL,
    # BY_NAME
    "orchestrator/gui/boards_window.py::_on_pair_code": BY_NAME,
    "orchestrator/gui/boards_window.py::_on_add": BY_NAME,
    "orchestrator/gui/boards_window.py::_on_edit": BY_NAME,
    "orchestrator/gui/boards_window.py::_on_remove_trust": BY_NAME,
    "orchestrator/gui/boards_window.py::_on_ssh": BY_NAME,
    "orchestrator/gui/boards_window.py::_on_settings": BY_NAME,
    "orchestrator/gui/boards_window.py::_on_plan": BY_NAME,
    "orchestrator/gui/boards_window.py::_on_prune": BY_NAME,
    "orchestrator/gui/boards_window.py::_on_prune_board": BY_NAME,
    "orchestrator/gui/boards_window.py::_on_clock": BY_NAME,
    "orchestrator/gui/boards_window.py::_on_packages": BY_NAME,
    "orchestrator/gui/boards_window.py::_on_verify": BY_NAME,
    "orchestrator/gui/boards_window.py::_on_check": BY_NAME,
    "orchestrator/gui/bundle_window.py::_on_file": BY_NAME,
    "orchestrator/gui/bundle_window.py::_on_plan": BY_NAME,
    "orchestrator/gui/bundle_window.py::_on_prepare_root": BY_NAME,
    "orchestrator/gui/bundle_window.py::_on_unprepare_root": BY_NAME,
    "orchestrator/gui/bundle_window.py::_on_gateway_bench": BY_NAME,
    "orchestrator/gui/bundle_window.py::_on_rollback": BY_NAME,
    "orchestrator/gui/bundle_window.py::_on_root_rollback": BY_NAME,
    "orchestrator/gui/bundle_window.py::_on_uninstall": BY_NAME,
    "orchestrator/gui/bundle_window.py::_on_delete": BY_NAME,
    "orchestrator/gui/bundle_window.py::_on_block_new": BY_NAME,
    "orchestrator/gui/bundle_window.py::_on_block_propose": BY_NAME,
    "orchestrator/gui/harness_window.py::_on_run": BY_NAME,
    "orchestrator/gui/harness_window.py::_on_attach": BY_NAME,
    "orchestrator/gui/harness_window.py::_on_stop": BY_NAME,
    "orchestrator/gui/harness_window.py::_on_take_in": BY_NAME,
    # PREPARATION
    "orchestrator/llm/auth/resolver.py::register_refresher": PREPARATION,
    "orchestrator/llm/auth/resolver.py::register_type": PREPARATION,
    "orchestrator/gui/chat_panel/composer.py::set_hint": PREPARATION,
    "orchestrator/Core/chat_feed.py::delegated_answer": PREPARATION,
    "orchestrator/Core/config_manager.py::ConfigFormat": PREPARATION,
    "orchestrator/Core/config_manager.py::ConfigScope": PREPARATION,
    "orchestrator/Core/config_manager.py::is_valid": PREPARATION,
    "orchestrator/Core/config_manager.py::get_validation_errors": PREPARATION,
    "orchestrator/Core/config_manager.py::add_change_listener": PREPARATION,
    "orchestrator/Core/config_manager.py::get_all": PREPARATION,
    "orchestrator/tools/audit.py::delegation_uses": PREPARATION,
    # TEST_API
    "orchestrator/Core/model_manager.py::_detect_model_format": TEST_API,
    "orchestrator/remote/gate.py::is_restricted_line": TEST_API,
    "orchestrator/remote/gate.py::restricted_line": TEST_API,
    "orchestrator/utils/next_steps/__init__.py::dialog_kwargs": TEST_API,
    "orchestrator/tools/policy.py::damaged": TEST_API,
    "orchestrator/runtime/profile.py::soc_model_known": TEST_API,
    "orchestrator/runtime/hardware.py::settings_view": TEST_API,
    "orchestrator/runtime/device/package.py::tarballs": TEST_API,
    "orchestrator/runtime/device/board_clock.py::working_rtc": TEST_API,
    "orchestrator/remote/pair_pake.py::key_lines": TEST_API,
    "orchestrator/Core/chat_feed.py::person_message": TEST_API,
    "orchestrator/Core/chat_feed.py::max_id": TEST_API,
    "orchestrator/Core/community_pack.py::sshsig_verify": TEST_API,
    "orchestrator/Core/memory_estimate.py::shape_parameters": TEST_API,
    "orchestrator/Core/trash.py::bytes_by_device": TEST_API,
    "orchestrator/gui/deployment_window.py::_on_client": TEST_API,
    "orchestrator/gui/empty_state.py::is_empty": TEST_API,
    "orchestrator/gui/localization.py::qt_translator": TEST_API,
    "orchestrator/gui/sign_in.py::current_method": TEST_API,
    "orchestrator/llm/auth/store.py::all_metadata": TEST_API,
    "orchestrator/mcp_server.py::open_count": TEST_API,
    "orchestrator/runtime/bench.py::run_record": TEST_API,
    "orchestrator/runtime/build/recipe.py::arm_arch": TEST_API,
    "orchestrator/runtime/hardware.py::parse_nvidia_smi": TEST_API,
    "orchestrator/runtime/llama_server.py::cleanup_stale": TEST_API,
    "orchestrator/runtime/profile.py::os_family": TEST_API,
    "orchestrator/runtime/profile.py::arm_core_name": TEST_API,
    "orchestrator/security/personal_data.py::scrub_findings": TEST_API,
    "orchestrator/tools/approvals.py::waiting_ids": TEST_API,
    "orchestrator/tools/confirmation.py::argument_lines": TEST_API,
    # KEPT
    "orchestrator/utils/logging.py::log_exceptions": KEPT,
    "orchestrator/utils/logging.py::export_logs": KEPT,
    "orchestrator/utils/logging.py::configure_logger": KEPT,
    "orchestrator/utils/sandbox.py::runner_text_problem": KEPT,
    "orchestrator/utils/validation.py::_validate_port_availability": KEPT,
    # OPEN
    "orchestrator/remote/transport.py::host_key": OPEN,
}
