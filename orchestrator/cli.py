#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Command Line Interface (llm-cli)

Professional CLI for automation, CI/CD integration, and power users.
Supports all framework operations including Source Management, Module Generation,
AI Assistance, Deployment, Self-Healing, and Smart Calibration (IMatrix).
The version shown by --help, --version and the banner is orchestrator.__version__.
"""

import sys
import json
import asyncio
import os
import time
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict, Any, List, Tuple
from datetime import datetime

import click
import yaml

from orchestrator.Core.build_formats import ModelFormat
from orchestrator.llm.sdk_env import guard_sdk_environment
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.lazy_imports import lazy_attributes, lazy_value
from orchestrator.utils.logging import get_logger, setup_logging

if TYPE_CHECKING:  # annotations only: imported where first used (see _LAZY)
    from orchestrator.Core.builder import BuildEngine
    from orchestrator.Core.deployment_manager import DeploymentManager
    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.Core.orchestrator import LLMOrchestrator

# ============================================================================
# GLOBAL CONFIGURATION
# ============================================================================

from orchestrator import __version__  # noqa: E402

# Lazy loading: the framework, the build engine and the other managers are imported where a command first uses
# them, not here, so `llm-cli --help` and the commands that need no framework start fast. The names stay
# attributes of this module (cli.FrameworkManager, ...; a test may replace one).
_LAZY = {
    "FrameworkManager": "orchestrator.Core.framework",
    "LLMOrchestrator": "orchestrator.Core.orchestrator", "BuildRequest": "orchestrator.Core.orchestrator",
    "WorkflowType": "orchestrator.Core.orchestrator", "PriorityLevel": "orchestrator.Core.orchestrator",
    "OrchestrationStatus": "orchestrator.Core.orchestrator",
    "BuildEngine": "orchestrator.Core.builder", "OptimizationLevel": "orchestrator.Core.builder",
    "repo_override_names": "orchestrator.Core.builder",
    "ModuleGenerator": "orchestrator.Core.module_generator",
    "DeploymentManager": "orchestrator.Core.deployment_manager",
}

__getattr__ = lazy_attributes(__name__, _LAZY)


def _lazy(name: str) -> Any:
    """`name` of _LAZY: the value set on this module (imported earlier, or a test's stand-in), else imported now."""
    return lazy_value(globals(), name, __getattr__)


# rich is imported when a command first prints, not for --help.
def Console(*args, **kwargs):  # noqa: N802 - rich's Console
    from rich.console import Console as RichConsole
    return RichConsole(*args, **kwargs)


def escape(markup: str, *args, **kwargs) -> str:
    """rich's markup escape."""
    from rich.markup import escape as rich_escape
    return rich_escape(markup, *args, **kwargs)


def Table(*args, **kwargs):  # noqa: N802 - rich's Table
    from rich.table import Table as RichTable
    return RichTable(*args, **kwargs)


def Panel(*args, **kwargs):  # noqa: N802 - rich's Panel
    from rich.panel import Panel as RichPanel
    return RichPanel(*args, **kwargs)


class _SharedConsole:
    """The console the commands print to (rich's Console), made at its first use."""

    def __init__(self):
        self._console = None

    def real(self):
        """rich's Console itself."""
        if self._console is None:
            self._console = Console()
        return self._console

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real(), name)


console = _SharedConsole()


def _rich_console():
    """The Console behind `console`, for rich APIs that need the object itself (a live display enters it with
    `with console:`, which a stand-in cannot forward)."""
    return console.real() if isinstance(console, _SharedConsole) else console

# ============================================================================
# CLICK CONTEXT UND MANAGER INITIALISIERUNG
# ============================================================================

class FrameworkContext:
    """Shared context for CLI commands, hält Instanzen der Core-Manager"""
    
    def __init__(self):
        # The settings themselves are read by ConfigManager (see _settings and FrameworkManager), never copied here.
        self.config_path: Optional[str] = None
        self.log_level: Optional[str] = None
        self.init_error: Optional[Exception] = None
        self.framework_manager: Optional[FrameworkManager] = None
        self.orchestrator: Optional[LLMOrchestrator] = None
        self.build_engine: Optional[BuildEngine] = None
        self.deployment_manager: Optional[DeploymentManager] = None
        self.verbose: bool = False
        self.quiet: bool = False
        self.http_client: Any = None  # reaches the providers for sign-in checks (tests: a mock transport)
        self.mcp_token: Optional[str] = None  # LCF_MCP_TOKEN, taken from the environment for `mcp serve` only
        self.settings: Any = None  # the settings read without booting the framework (see _settings)
        self._initialized: bool = False
        
    def initialize(self):
        """Initialisiert FrameworkManager und Orchestrator."""
        if self._initialized:
            return
            
        try:
            # Framework Manager initialisieren
            self.framework_manager = _lazy("FrameworkManager")(self.config_path, log_level=self.log_level)
            
            if not self.framework_manager.initialize():
                # The cause itself (e.g. the credential storage error with its choices), not just "failed".
                raise (getattr(self.framework_manager, "init_error", None)
                       or RuntimeError("Framework Manager initialization failed"))
            
            # Orchestrator initialisieren  
            self.orchestrator = self.framework_manager.orchestrator # Hole vom Kernel
            
            # Deployment Manager (NEU: v2.0 Integration)
            self.deployment_manager = self.framework_manager.get_component("deployment_manager")
            
            # Synchrone Initialisierung für CLI
            if self.orchestrator and not getattr(self.orchestrator, 'build_engine', None):
                 loop = asyncio.new_event_loop()
                 asyncio.set_event_loop(loop)
                 loop.run_until_complete(self.orchestrator.initialize())
                 loop.close()
            
            # Build Engine ist über Orchestrator verfügbar
            if self.orchestrator:
                self.build_engine = self.orchestrator.build_engine
            
            self._initialized = True
            
        except Exception as e:
            self.init_error = e
            Console(stderr=True).print(f"[red]Failed to initialize framework: {escape(str(e))}[/red]")

    def require_framework(self) -> "FrameworkContext":
        """Boots the framework on first use; commands that do not need it work without a keyring or Docker.
        Exit code 5 when the credential vault cannot be used (the message names the choices), 1 otherwise."""
        self.initialize()
        if self.init_error or not self.framework_manager:
            storage = (getattr(self.init_error, "code", None) or "").startswith("storage.")
            sys.exit(5 if storage else 1)
        return self


pass_context = click.make_pass_decorator(FrameworkContext, ensure=True)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def _settings(ctx: "FrameworkContext"):
    """The framework's settings (config.yml, a -c file, LLM_FRAMEWORK_* variables) and its path rule (relative to
    the installation), as Ditto, builds and the GUI use them. A booted framework's own settings when there is
    one; otherwise they are read without booting it, so the command needs neither a keyring nor Docker."""
    from orchestrator.Core.framework import resolve_install_path
    fm = ctx.framework_manager
    if fm is not None:
        return fm.config, fm.resolve_path
    if ctx.settings is None:
        from orchestrator.Core.config_manager import ConfigManager
        ctx.settings = ConfigManager(ctx.config_path)
    return ctx.settings, resolve_install_path


def _allow_sdk_env_headers(config_path: Optional[str]) -> Any:
    """The setting ai_allow_sdk_env_headers (read only when OPENAI/ANTHROPIC_CUSTOM_HEADERS is set). Settings that
    cannot be read count as no: the variables are then dropped and the command still runs."""
    try:
        from orchestrator.Core.config_manager import ConfigManager
        return ConfigManager(config_path).get("ai_allow_sdk_env_headers", False)
    except Exception as e:
        get_logger(__name__).warning(f"Could not read ai_allow_sdk_env_headers ({e.__class__.__name__}).")
        return False


def model_format_from_string(format_str: str) -> ModelFormat:
    """Convert string to ModelFormat enum (by name or value, any letter case; GGUF for an unknown one). Kept for its
    callers: `build start` lets BuildService.start_build normalize the format now."""
    try:
        return ModelFormat[format_str.upper()]
    except KeyError:
        try:
            return ModelFormat(format_str.lower())
        except ValueError:
            return ModelFormat.GGUF


# ============================================================================
# MAIN CLI GROUP
# ============================================================================

CLI_HELP = f"""
    🚀 LLM Cross-Compiler Framework CLI v{__version__}

    Professional command-line interface for cross-compiling Large Language Models
    for edge hardware. Supports automation, CI/CD integration, Deployment,
    Self-Healing and Smart Calibration (IMatrix).

    Sign Ditto in to an AI provider with `llm-cli ditto login <provider>`;
    `llm-cli ditto auth status` shows the sign-in of every provider.
    """


@click.group(help=CLI_HELP)
@click.version_option(version=__version__)
@click.option('--config', '-c', type=click.Path(exists=True), help='Configuration file path')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
@click.option('--quiet', '-q', is_flag=True, help='Quiet output')
@click.option('--log-level', default='INFO', type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR']))
@click.option('--debug-http', is_flag=True,
              help='Log HTTP and provider SDK debug output to stderr and keep ANTHROPIC_LOG/OPENAI_LOG=debug '
                   '(credentials stay redacted)')
@pass_context
def cli(ctx: FrameworkContext, config: Optional[str], verbose: bool, quiet: bool, log_level: str,
        debug_http: bool):
    """The help text is CLI_HELP (its version comes from orchestrator.__version__)."""
    # The stdio client's MCP token: taken out of the environment before anything else runs, for every command, so
    # no command and no child process ever inherits it; only `mcp serve` gets it, through the context.
    from orchestrator.tools.mcp_env import ENV_VAR
    ctx.mcp_token = os.environ.pop(ENV_VAR, None)
    from orchestrator.Core.request_store import set_program
    set_program("cli")  # shown with this process's builds in the shared build state
    # ConfigManager reads this file (and reports it on stderr if it cannot be read) wherever settings are needed.
    ctx.config_path = config
    ctx.log_level = "DEBUG" if verbose else ("ERROR" if quiet else log_level)
    ctx.verbose = verbose
    ctx.quiet = quiet

    # Applies to framework loggers too; they are created before FrameworkManager configures logging.
    setup_logging(level=ctx.log_level, debug_http=debug_http)

    # Every command, `mcp serve` included, passes here before anything starts a thread.
    for warning in guard_sdk_environment(lambda: _allow_sdk_env_headers(config), debug_http=debug_http):
        # One line each, whatever the terminal width (MCP clients log stderr line by line).
        Console(stderr=True, soft_wrap=True).print(f"[yellow]Warning: {escape(warning)}[/yellow]")

    if not quiet:
        Console(stderr=True).print(Panel(
            f"[bold cyan]LLM Cross-Compiler Framework CLI v{__version__}[/bold cyan]\n"
            f"Professional cross-compilation for edge AI",
            title="🚀 Framework CLI"
        ))


# ============================================================================
# TARGET MANAGEMENT COMMANDS
# ============================================================================

@cli.group()
def targets():
    """Manage hardware targets and architectures"""
    pass


@targets.command('list')
@click.option('--format', '-f', default='table', type=click.Choice(['table', 'json', 'yaml']))
@pass_context
def list_targets(ctx: FrameworkContext, format: str):
    """List the hardware targets: id (the value for --target), name, boards and formats from their capabilities"""
    from orchestrator.Core.build_service import BuildService, BuildServiceError
    ctx.require_framework()
    fm = ctx.framework_manager
    try:
        targets = (getattr(fm, "build_service", None) or BuildService(fm)).list_targets().targets
    except BuildServiceError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    if format != 'table':
        data = [t.model_dump(mode="json") for t in targets]
        click.echo(json.dumps(data, indent=2) if format == 'json' else yaml.safe_dump(data, sort_keys=False))
        return
    if not targets:
        console.print("[yellow]No targets found.[/yellow]")
        return
    table = Table(title="Available Targets")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Architecture", style="green")
    table.add_column("Boards", style="magenta")
    table.add_column("Formats", style="yellow")
    for t in targets:
        table.add_row(escape(t.id), escape(t.name or ""), escape(t.architecture or "-"),
                      escape(", ".join(t.boards) or "-"),
                      escape(", ".join(f.id for f in t.formats) or "- (no capabilities declared)"))
    console.print(table)


@targets.command('validate')
@click.argument('target_name')
@pass_context
def validate_target(ctx: FrameworkContext, target_name: str):
    """Check that a target loads and that its capabilities block passes the schema (exit code 1 if not)"""
    ctx.require_framework()
    tm = ctx.framework_manager.get_component("target_manager")
    if not tm:
        console.print("[red]Target Manager not available[/red]")
        sys.exit(1)
    check = tm.validate(target_name)  # the one check (the GUI's Check target calls it too)
    if not check.found:
        console.print(f"[red]❌ Target '{escape(target_name)}' not found (or its target.yml cannot be read; "
                      f"see the log).[/red]")
        sys.exit(1)
    tid = check.target_id
    if check.no_capabilities:
        console.print(f"[yellow]Target '{escape(tid)}' loads but declares no capabilities: boards, formats and "
                      "quantizations are not checked before a build. Add a 'capabilities:' block "
                      "(see targets/Rockchip/target.yml).[/yellow]")
        return
    if check.errors:
        console.print(f"[red]❌ Target '{escape(tid)}': its capabilities block is invalid:[/red]")
        for where, message in check.errors:
            console.print(f"[red]  {escape(where)}: {escape(message)}[/red]")
        sys.exit(1)
    console.print(f"[green]✅ Target '{escape(tid)}' is valid ({check.boards} boards, formats: "
                  f"{escape(', '.join(check.formats))})[/green]")


# ============================================================================
# MODULE MANAGEMENT COMMANDS (WIZARD VIA CLI)
# ============================================================================

@cli.group()
def module():
    """Manage hardware target modules"""
    pass

def _target_profile(probe_file, strict: bool):
    """The hardware profile in a probe file of the target (schema 2, or schema 1 converted), for the runtime: block of
    a new module. strict: a file that is no probe profile is an error; otherwise it gives None (a free-form probe
    that only the AI reads)."""
    from orchestrator.runtime.profile import ProfileError, read_profile
    try:
        return read_profile(Path(probe_file))
    except (ProfileError, OSError, ValueError) as e:
        if strict:
            console.print(f"[red]{escape(str(probe_file))} is not a hardware probe profile: {escape(str(e))}[/red]")
            sys.exit(1)
        console.print("[yellow]The probe file is not a hardware probe profile (llm-cli scan writes one), so the "
                      "module's runtime: block plans the device runtime when the device is probed.[/yellow]")
        return None


@module.command('create')
@click.option('--name', prompt='Module Name', help='Name of the new target')
@click.option('--arch', prompt='Architecture', type=click.Choice(['aarch64', 'x86_64', 'armv7l', 'riscv64']), help='CPU Architecture')
@click.option('--sdk', prompt='SDK/Backend', default='None', help='Special SDK (e.g. CUDA, RKNN)')
@click.option('--probe', 'probe_file', type=click.Path(exists=True, dir_okay=False), default=None,
              help="Hardware probe of the target (llm-cli scan --device writes one): the module's runtime: block is "
                   "the build recipe's plan for its device runtime")
@pass_context
def module_create(ctx: FrameworkContext, name: str, arch: str, sdk: str, probe_file: Optional[str]):
    """Create a new hardware target module interactively"""

    console.print(f"\n[bold cyan]Creating new target module: {name}[/bold cyan]")
    profile = _target_profile(probe_file, strict=True) if probe_file else None

    # Interaktive Abfrage weiterer Details (CLI Wizard Style)
    base_os = click.prompt("Base Docker Image", default="debian:bookworm-slim")
    description = click.prompt("Description", default=f"Target for {name}")
    
    console.print("Enter required system packages (comma separated):")
    packages_input = click.prompt("Packages", default="build-essential, cmake, git")
    packages = [p.strip() for p in packages_input.split(',')]
    
    # Advisory only: kept in the runtime: block of target.yml, never applied (llama.cpp's options come from the
    # build recipe and the device's probe).
    cpu_flags = click.prompt("CPU flags to note in the module (advisory, not applied)", default="")

    module_data = {
        "module_name": name,
        "architecture": arch,
        "sdk": sdk,
        "description": description,
        "base_os": base_os,
        "packages": packages,
        "cpu_flags": cpu_flags,
        "supported_boards": [], 
        "setup_commands": "",
        "cmake_flags": "",
        "detection_commands": "lscpu"
    }
    
    try:
        # The configured targets folder (relative paths: the installation), as builds and the GUI use it.
        config, resolve = _settings(ctx)
        targets_dir = resolve(config.get("targets_dir", "targets"))
        generator = _lazy("ModuleGenerator")(targets_dir)

        output_path = generator.generate_module(module_data, profile=profile)
        
        console.print(f"\n[bold green]✅ Module successfully created![/bold green]")
        console.print(f"Location: {output_path}")
        console.print("\nNext steps:")
        console.print(f"1. Edit {output_path}/target.yml to add supported boards")
        # The build is the four stage modules, which modules/build.sh (the stage runner) runs in order (DECISIONS U9).
        console.print(f"2. Customize {output_path}/modules/convert_module.sh and target_module.sh (the convert and "
                      f"target stages of the build, which build.sh runs after source and config; the Dockerfile "
                      f"installs the tools they use)")
        
    except Exception as e:
        console.print(f"[bold red]Error creating module: {e}[/bold red]")
        sys.exit(1)


def _ai_override(ctx: FrameworkContext, provider: Optional[str], model: Optional[str]):
    """Ditto with provider/model chosen for this command only. The values are set in this short-lived process with
    the source "cli", which save_user_config never writes, so the saved AI settings stay unchanged also when a tool
    saves the settings during the command (the GUI uses Core/ai_override.ditto_with instead, and DittoManager.for_run
    goes through it too)."""
    from orchestrator.Core.ai_override import CLI_SOURCE, override_values, provider_problem
    fm = ctx.framework_manager
    ditto = fm.get_component("ditto_manager")
    if ditto is None:
        console.print("[red]The AI assistant (Ditto) is not available.[/red]")
        sys.exit(1)
    problem = provider_problem(ditto.client.catalog, provider) if provider else None
    if problem:
        console.print(f"[red]{escape(problem)}[/red]")
        sys.exit(2)
    for key, value in override_values(provider, model, ditto=ditto).items():
        fm.config.set(key, value, source_type=CLI_SOURCE)
    return ditto


def _run_ditto(ctx: FrameworkContext, provider: Optional[str], model: Optional[str]):
    """Ditto with provider/model chosen for this run only, through DittoManager.for_run (the module wizard's "for this
    analysis only" takes the same path): the settings themselves are not changed."""
    from orchestrator.Core.ditto_manager import DittoError
    ditto = ctx.framework_manager.get_component("ditto_manager")
    if ditto is None:
        console.print("[red]The AI assistant (Ditto) is not available.[/red]")
        sys.exit(1)
    try:
        return ditto.for_run(provider, model)
    except DittoError as e:  # an unknown or removed provider
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(2)


PAGED_QUESTION_LINES = 30  # a longer question (up to 16,000 characters) is shown through the pager first


def _printable(text: Any) -> str:
    """Model output (an answer, an error that quotes a provider or a tool, a tool line) for the terminal: line breaks
    and tabs stay; control characters (C0 including carriage return, DEL, C1), format characters (bidi controls and
    isolates, zero-width characters) are removed, so text a build log or a web page steered cannot rewrite the screen,
    the window title or the clipboard, nor reorder what the person reads. The zero-width joiner stays, as in
    utils/untrusted (the one rule for it: needed inside emoji sequences, harmless on its own). Line and paragraph
    separators become line breaks. (A value the person confirms is shown escaped instead: tools.confirmation.visible.)"""
    import unicodedata
    from orchestrator.utils.untrusted import _KEEP_FORMAT
    out = []
    for ch in str(text):
        category = unicodedata.category(ch)
        if ch in "\n\t":
            out.append(ch)
        elif category in ("Zl", "Zp"):
            out.append("\n")
        elif category not in ("Cc", "Cf") or ch in _KEEP_FORMAT:
            out.append(ch)
    return "".join(out)


def _ditto_confirm(spec, inp) -> bool:
    """Asks before a tool call that needs confirmation; without a terminal the answer is no. A long question (a
    module definition) is shown whole, through the pager when it does not fit the screen. Ctrl+C or the end of input at
    a prompt is a no as well (the registry records it like any other), never a failed tool call."""
    from orchestrator.tools.confirmation import MAX_ELICITATION, MAX_FRAMEWORK, CallTooLong, describe_call
    from orchestrator.tools.registry import current_question
    try:
        text = current_question() or describe_call(spec, inp.model_dump(mode="json"), actor="Ditto",
                                                   limit=MAX_FRAMEWORK)
    except CallTooLong as e:
        click.echo(str(e), err=True)
        return False
    if sys.stdin is None or not sys.stdin.isatty():
        # Nobody may sit here and another program (an AI app running this command) may read it: the call and its
        # arguments only, never the facts the framework's question shows the person alone (full paths, logins).
        try:
            click.echo(describe_call(spec, inp.model_dump(mode="json"), actor="Ditto", limit=MAX_FRAMEWORK), err=True)
        except CallTooLong as e:
            click.echo(str(e), err=True)
        click.echo("No terminal to confirm in: denied. Allow the tool with 'llm-cli tools permissions'.", err=True)
        return False
    long = len(text) > MAX_ELICITATION or len(text.splitlines()) > PAGED_QUESTION_LINES
    if long and sys.stdout.isatty():
        click.echo_via_pager(text + "\n")
    else:
        click.echo(text, err=True)
    try:
        return _ditto_answer()
    except click.Abort:   # Ctrl+C or the end of input at one of the prompts
        click.echo("\nDenied.", err=True)
        return False


def _ditto_answer():
    """The person's answer at the terminal to the question _ditto_confirm showed: yes, no, or a standing approval
    (GrantChoice) when the tool offers one. click.Abort when the prompt is cancelled."""
    from orchestrator.tools.registry import current_grant_offer, current_registry
    offer = current_grant_offer()
    if offer is None:
        return click.confirm("Allow this?", default=False, err=True)
    answer = click.prompt("Allow this? y = once, a = and do not ask again, n = no",
                          type=click.Choice(["y", "a", "n"]), default="n", err=True)
    if answer != "a":
        return answer == "y"
    from orchestrator.tools.grants import DURATION_LABELS, GrantChoice, gui_running
    registry = current_registry()
    if registry is not None and gui_running(registry.policy_store.path.parent):
        click.echo("A GUI session is running: use its approval window for 'do not ask again'.", err=True)
        return click.confirm("Allow this once?", default=False, err=True)
    width = offer.default_width()
    if len(offer.scopes) > 1:
        width = click.prompt("For " + " or ".join(f"{w} ({offer.summaries[w]})" for w in offer.scopes),
                             type=click.Choice(list(offer.scopes)), default=width, err=True)
    duration = click.prompt("For how long: " + "; ".join(f"{d} = {DURATION_LABELS[d]}" for d in offer.durations),
                            type=click.Choice(offer.durations), default=offer.default_duration(), err=True)
    click.echo(offer.warning, err=True)
    if not click.confirm("Allow this and do not ask again within this scope?", default=False, err=True):
        return click.confirm("Allow this once?", default=False, err=True)
    return GrantChoice(width, duration)


_ditto_confirm.confirm_channel = "framework-cli"  # recorded in the audit log


@module.command('generate-ai')
@click.argument('probe_file', type=click.Path(exists=True))
@click.option('--provider', help='AI provider for this run (e.g. anthropic, openai, ollama); default: AI settings')
@click.option('--model', help='Model for this run; default: AI settings')
@pass_context
def generate_ai(ctx: FrameworkContext, probe_file, provider: Optional[str], model: Optional[str]):
    """
    🤖 AI-Powered: Generate module from hardware probe (target_hardware_config.txt).

    Ditto uses the provider's stored sign-in (llm-cli ditto login <provider>).
    """
    from orchestrator.Core.ditto_manager import DittoError, proposal_facts
    _ditto_notice_once()   # Ditto at work outside the ditto group: the notice comes first here too
    ctx.require_framework()
    ditto = _run_ditto(ctx, provider, model)
    try:
        info = ditto.describe("json")
    except DittoError as e:
        console.print(f"[red]{escape(_printable(e))}[/red]")
        sys.exit(1)
    console.print(Panel(f"[bold cyan]🤖 Ditto AI Hardware Agent[/bold cyan]\nAnalyzing: {escape(str(probe_file))}\n"
                        f"Provider: {escape(info['label'])} / {escape(info['model'])}", title="AI Setup"))
    try:
        with console.status("🤖 Analyzing the hardware probe...", spinner="dots"):
            definition = ditto.generate_module(Path(probe_file))
    except (DittoError, OSError) as e:   # OSError: the probe cannot be read (a folder, no permission)
        console.print(f"[bold red]AI generation failed:[/bold red] {escape(_printable(e))}")
        sys.exit(1)

    # The base image is pinned to the digest its tag points at now, before the question (DECISIONS round 3): the
    # module is written with exactly the digest shown (the module wizard's AI import pins it the same way).
    from orchestrator.Core.ditto_manager import pin_base_image
    from orchestrator.utils.image_registry import ImageLookupError
    try:
        definition = pin_base_image(definition, offline=bool(ctx.framework_manager.config.get("offline_mode", False)))
    except ImageLookupError as e:   # the base image is the AI's value, and the message may quote it
        console.print(f"[red]The base image {escape(_printable(definition.base_os))} cannot be pinned: "
                      f"{escape(_printable(e))}[/red]")
        console.print(escape(e.next))
        sys.exit(1)
    # The definition is shown in full before anything is written; the module generator checks it again. Hidden and
    # control characters of the AI's values are shown escaped (tools.confirmation.visible), never sent as they are.
    from orchestrator.tools.confirmation import visible
    console.print("\n[bold green]AI analysis finished. Review the proposal:[/bold green]")
    proposal = json.dumps(definition.model_dump(exclude={"cannot"}), indent=2, ensure_ascii=False)
    click.echo("\n".join(visible(line) for line in proposal.split("\n")))
    click.echo("\n".join(visible(line) for line in proposal_facts(definition).split("\n")))
    from rich.prompt import Confirm
    if not Confirm.ask("Generate module with these settings?"):
        return
    console.print("[dim]cpu_flags and cmake_flags are kept in the module as advisory text; llama.cpp is built by the "
                  "framework's recipe from the device's probe.[/dim]")
    fm = ctx.framework_manager
    targets_dir = fm.resolve_path(fm.config.get("targets_dir", "targets"))
    module_name = definition.module_name.replace(" ", "_")
    profile = _target_profile(probe_file, strict=False)
    try:
        path = ditto.save_module(definition, targets_dir, module_name=module_name, profile=profile)
    except (ValueError, OSError) as e:   # OSError: the targets folder cannot be written
        console.print(f"[bold red]The proposal was rejected:[/bold red] {escape(_printable(e))}")
        sys.exit(1)
    from orchestrator.Core.module_generator import generator_input
    from orchestrator.Core.module_generator import record_module_saved
    record_module_saved(fm, "cli", generator_input(definition.model_dump(), module_name), path, "framework-cli")
    console.print(f"[bold green]✅ Module '{escape(module_name)}' created in {escape(str(path))}[/bold green]")


# ============================================================================
# BUILD COMMANDS
# ============================================================================

@cli.group()
def build():
    """Build and cross-compile models"""
    pass


@build.command('start')
@click.option('--model', '-m', required=True, help='Model name (HuggingFace) or local path')
@click.option('--target', '-t', required=True, help='Target id or folder name in targets/ (llm-cli targets list)')
@click.option('--format', '-f', default='gguf', type=click.Choice([f.value for f in ModelFormat if f is not ModelFormat.HUGGINGFACE]),
              help='Target format (gguf: llama.cpp/CPU, rkllm: Rockchip NPU LLM, rknn: Rockchip NPU ONNX)')
@click.option('--board', '-b', help='Board within the target family, e.g. rk3588, rk3576, rk3566')
@click.option('--quantization', '-q', help='Quantization, e.g. Q4_K_M, IQ4_XS, F16 (gguf) or w4a16, w8a8 (rkllm)')
@click.option('--output', '--output-dir', '-o', 'output_dir',
              help="Folder the finished model (golden artifact) is saved to; default: the output folder ('llm-cli "
                   "config output-folder'). At a terminal without it you are asked; '-' keeps it in the framework's "
                   "build folder")
@click.option('--optimization', default='balanced',
              type=click.Choice(['fast', 'balanced', 'size', 'speed', 'aggressive']),
              help='Optimization level, passed to the target\'s build scripts as OPTIMIZATION_LEVEL '
                   '(the Rockchip target ignores it)')
@click.option('--priority', default='normal',
              type=click.Choice(['low', 'normal', 'high', 'urgent', 'critical']),
              help='Build priority')
@click.option('--parallel', is_flag=True, default=False, hidden=True,
              help='Deprecated: has no effect (one call runs one build)')
@click.option('--follow', '-F', is_flag=True, help='Follow build output in real-time')
@click.option('--gpu/--no-gpu', default=False, help='Enable GPU passthrough')
# --- NEU v2.4.0: IMatrix Flags ---
@click.option('--imatrix/--no-imatrix', default=False, help='Enable Smart Calibration (IMatrix) for quantization')
@click.option('--dataset', type=click.Path(exists=True), help='Custom calibration dataset path (txt)')
@pass_context
def start_build(ctx: FrameworkContext, model: str, target: str, format: str, board: Optional[str],
                quantization: Optional[str], output_dir: Optional[str], 
                optimization: str, priority: str, parallel: bool, follow: bool, gpu: bool,
                imatrix: bool, dataset: Optional[str]):
    """Start a new build job. Ctrl+C cancels it (its container is stopped) and exits with code 130."""
    from orchestrator.Core.build_service import BuildServiceError
    from orchestrator.Core.orchestrator import OrchestrationStatus
    ctx.require_framework()
    if parallel:
        Console(stderr=True).print("[yellow]--parallel is deprecated and has no effect.[/yellow]")

    try:
        fm = ctx.framework_manager
        # The one build path (BuildService.start_build), as for the Builder tab and AI callers.
        service = _build_service(ctx)
        tm = getattr(fm, "target_manager", None)
        if tm:
            # The registered id (also for its folder name or another letter case), so `-t rockchip` is checked
            # like `-t Rockchip`; an unknown target would otherwise "complete" with nothing built.
            match = tm.resolve(target)
            if match is None:
                known = [str(t.get("id")) for t in tm.list_targets() if t.get("id")]
                console.print(f"[red]Unknown target '{escape(target)}' (targets: {escape(', '.join(known) or 'none')}).[/red]")
                sys.exit(2)
            target = match
        # Checked by the service against the target's declared capabilities (a target without them is built
        # unchecked), with its defaults filled: the values the build really runs with (canonical spellings, the
        # scripts' default board).
        try:
            request = service.validate(service.request_in(
                model=model, target=target, format=format, board=board, quantization=quantization,
                use_imatrix=imatrix, dataset_path=str(Path(dataset).resolve()) if dataset else None, use_gpu=gpu),
                local_paths=True)
        except BuildServiceError as e:
            for problem in getattr(e, "problems", None) or [str(e)]:
                console.print(f"[red]{escape(problem)}[/red]")
            sys.exit(2)
        board, quantization = request.board, request.quantization

        # Where the golden artifact goes (eighth round, answer 3): --output, else at a terminal the person's answer to
        # a prompt with the output folder as its default, else the output folder; '-' keeps it in the framework's
        # build folder ('llm-cli build save' saves it later). A relative path means the current directory.
        output_dir, chosen = _build_destination(ctx, output_dir)

        # The orchestrator runs on the framework's background loop (as for the GUI and the build service), so the
        # build service can cancel the build from here on Ctrl+C.
        request_id = service.start_build(request, local_paths=True, optimization=optimization, priority=priority,
                                         output_dir=output_dir,
                                         description=f"CLI build: {model} -> {target} ({format})").request_id

        # The CLI stays until the build has ended; from here on Ctrl+C cancels it.
        last = None
        workflow_status = None
        try:
            if not ctx.quiet:
                console.print(Panel(
                    f"[bold]Model:[/bold] {escape(model)}\n"
                    f"[bold]Target:[/bold] {escape(target)}\n"
                    f"[bold]Format:[/bold] {format}\n"
                    f"[bold]Quantization:[/bold] {escape(quantization or 'None')}\n"
                    f"[bold]Output:[/bold] {escape(output_dir)}\n"
                    f"[bold]GPU:[/bold] {'✅ Enabled' if gpu else '❌ Disabled'}\n"
                    f"[bold]Smart Calibration (IMatrix):[/bold] {'✅ Enabled' if imatrix else '❌ Disabled'}",
                    title=f"🚀 Starting Build: {request_id}"
                ))
            if follow:
                console.print("[blue]Following build progress (Ctrl+C cancels the build)...[/blue]")
            while True:
                workflow_status = service.request_state(request_id)
                if workflow_status:
                    line = (workflow_status.status, workflow_status.current_stage, workflow_status.completed_builds)
                    if follow and line != last:
                        status_color = {OrchestrationStatus.COMPLETED: "green", OrchestrationStatus.ERROR: "red",
                                        OrchestrationStatus.HEALING: "magenta"}.get(workflow_status.status, "yellow")
                        # Stages, proposals and errors can contain model or log text: never parse it as markup.
                        console.print(f"[{status_color}]Status: {workflow_status.status.value} - {escape(workflow_status.current_stage)} "
                                      f"({workflow_status.completed_builds}/{workflow_status.total_builds} builds)[/{status_color}]")
                    last = line
                    if (workflow_status.status == OrchestrationStatus.AWAITING_APPROVAL
                            and workflow_status.healing_proposal is not None):
                        _resolve_healing_interactively(ctx.orchestrator, request_id, workflow_status.healing_proposal)
                    if workflow_status.status in [OrchestrationStatus.COMPLETED, OrchestrationStatus.ERROR, OrchestrationStatus.CANCELLED]:
                        break
                _pause(BUILD_POLL_SECONDS)
        except KeyboardInterrupt:
            _cancel_interrupted_build(ctx, request_id)

        if workflow_status:
            for artifact in workflow_status.artifacts:
                console.print(f"[green]Artifacts: {escape(str(artifact))}[/green]")
                if chosen:  # outside the output folder: the listings keep showing it
                    _remember_saved(ctx, artifact, output_dir)
            for warning in workflow_status.warnings:
                console.print(f"[yellow]{escape(str(warning))}[/yellow]")
            for error in workflow_status.errors:
                console.print(f"[red]{escape(str(error))}[/red]")
            if workflow_status.status != OrchestrationStatus.COMPLETED or workflow_status.failed_builds:
                sys.exit(1)

    except Exception as e:
        console.print(f"[red]Build failed: {escape(str(e))}[/red]")
        sys.exit(1)


BUILD_POLL_SECONDS = 2
KEEP_IN_BUILD_FOLDER = "-"


def _build_service(ctx: FrameworkContext):
    """The one build path (BuildService) on the orchestrator this command's context holds: the framework's own
    service, or one bound to the context's orchestrator when that is another object."""
    from orchestrator.Core.build_service import BuildService
    fm = ctx.framework_manager
    shared = getattr(fm, "build_service", None)
    if shared is not None and ctx.orchestrator is getattr(fm, "orchestrator", None):
        return shared
    return BuildService(fm, orchestrator=ctx.orchestrator)


def _build_destination(ctx: FrameworkContext, given: Optional[str]) -> Tuple[str, bool]:
    """(folder the build publishes to, whether the person chose it): --output, else at a terminal the answer to a
    prompt with the output folder as its default ('-': the framework's build folder), else the output folder. A folder
    the person chose is checked (no system folder, not the program folder) and made when it does not exist."""
    from orchestrator.Core.artifact_output import DestinationRefused, build_folder, check_destination, output_folder
    fm = ctx.framework_manager
    default = output_folder(fm)
    if given is None and _interactive():
        given = click.prompt(f"Save the finished model to (Enter: the output folder, '{KEEP_IN_BUILD_FOLDER}': keep "
                             "it in the framework's build folder)", default=str(default), show_default=True)
    if given is not None and given.strip() == KEEP_IN_BUILD_FOLDER:
        return str(build_folder(fm)), False
    if given is None or Path(given).expanduser() == default:
        return str(default), False
    try:
        return str(check_destination(fm, given, create=True)), True
    except DestinationRefused as e:
        console.print(f"[red]{escape(str(e))}[/red] {escape(e.next)}")
        sys.exit(2)


def _remember_saved(ctx: FrameworkContext, artifact: Any, root: str) -> None:
    from orchestrator.Core.artifact_output import remember
    try:
        remember(ctx.framework_manager, artifact, root)
    except (OSError, TimeoutError) as e:
        console.print(f"[yellow]Saved, but not listed by the framework ({escape(str(e))}).[/yellow]")
# How long Ctrl+C waits for a cancelled build to stop (its container stops within about 10 s; a large model file
# being copied is noticed between two chunks).
CANCEL_WAIT_SECONDS = 120


def _pause(seconds: float) -> None:
    """The pause between two status polls (tests replace it)."""
    time.sleep(seconds)


def _cancel_interrupted_build(ctx: FrameworkContext, request_id: str) -> None:
    """Ctrl+C in `build start`: cancels the build through the build service (its container is stopped), waits
    until the cancel has finished, says so and exits with 130."""
    from orchestrator.Core.build_service import BuildService, BuildServiceError
    fm = ctx.framework_manager
    service = getattr(fm, "build_service", None) or BuildService(fm)
    console.print(f"\n[yellow]Interrupted: cancelling build {escape(request_id)} and stopping its container "
                  "...[/yellow]")
    try:
        service.cancel(request_id)
        outcome = service.wait_stopped(request_id, CANCEL_WAIT_SECONDS)
        status = service.get_status(request_id)
    except BuildServiceError as e:  # it ended meanwhile
        console.print(f"[yellow]{escape(str(e))}[/yellow]")
        sys.exit(130)
    except KeyboardInterrupt:
        console.print("[yellow]Not waiting any longer; the build is still being cancelled.[/yellow]")
        sys.exit(130)
    for warning in status.warnings:
        console.print(f"[yellow]{escape(warning)}[/yellow]")
    if outcome == "cancelled":
        console.print(f"[yellow]Build {escape(request_id)} cancelled: its container was stopped and nothing was "
                      "written to the output folder.[/yellow]")
    elif outcome == "finished":
        console.print(f"[yellow]Build {escape(request_id)} could no longer be cancelled and has ended on its "
                      "own.[/yellow]")
    else:
        console.print(f"[yellow]Build {escape(request_id)} is cancelled but has not stopped within "
                      f"{CANCEL_WAIT_SECONDS} s; check it with: llm-cli build status {escape(request_id)}[/yellow]")
    sys.exit(130)


def _resolve_healing_interactively(orchestrator, request_id, proposal):
    """Asks on the terminal whether an automatic fix may be applied (manual steps are only shown; no automatic fix
    deletes anything)."""
    from orchestrator.Core.self_healing_manager import AUTOMATIC_STRATEGIES
    automatic = (not getattr(proposal, "is_ai_generated", False)
                 and getattr(getattr(proposal, "strategy", None), "value", "") in AUTOMATIC_STRATEGIES)
    origin = "AI suggestion" if getattr(proposal, "is_ai_generated", False) else "Known issue"
    console.print(f"[bold magenta]Self-Healing ({origin}): {escape(str(proposal.summary))}[/bold magenta]")
    console.print(f"Cause: {escape(str(proposal.root_cause))}")
    label = "The framework will" if automatic else "Manual step (not run by the framework)"
    console.print(f"{label}: [italic]{escape(str(proposal.fix_command))}[/italic]")
    approved = False
    if automatic and sys.stdin.isatty():
        try:
            approved = click.confirm("Apply this fix and retry the build?", default=False)
        except click.Abort:
            orchestrator.resolve_healing(request_id, False)
            raise KeyboardInterrupt from None
    elif automatic:
        console.print("[yellow]No terminal to ask; the proposal is dismissed.[/yellow]")
    orchestrator.resolve_healing(request_id, approved)


STATUS_ROWS = 20  # builds `build status` lists without --all


@build.command('save')
@click.argument('artifact')
@click.option('--to', 'destination', help="Folder to save it to (default: the output folder; at a terminal you are "
                                          "asked)")
@pass_context
def build_save(ctx: FrameworkContext, artifact: str, destination: Optional[str]):
    """Save a finished build that waits in the framework's build folder (the GUI's Save...): ARTIFACT is its folder
    as 'llm-cli deploy list-artifacts' shows it, its full path, or its id. On the same disk it is moved; onto
    another disk every file is copied and checked by sha256 before the build folder's copy is removed."""
    from orchestrator.Core.artifact_output import (BUILDS, DestinationRefused, build_folder, list_artifacts,
                                                   output_folder, save_artifact)
    from orchestrator.Core.build_service import artifact_id
    from orchestrator.Core.knowledge_snapshots import size_text
    ctx.require_framework()
    fm = ctx.framework_manager
    waiting = [a for a in list_artifacts(fm) if a.where == BUILDS]
    given = Path(artifact).expanduser()
    match = next((a for a in waiting if artifact in (a.label, a.label.split(":", 1)[1], artifact_id(a.key))
                  or (given.is_absolute() and os.path.realpath(given) == os.path.realpath(a.path))), None)
    if match is None:
        console.print(f"[red]No finished build '{escape(artifact)}' waits in the framework's build folder "
                      f"({escape(str(build_folder(fm)))}).[/red] 'llm-cli deploy list-artifacts' lists them.")
        sys.exit(2)
    if destination is None:
        destination = str(output_folder(fm))
        if _interactive():
            destination = click.prompt("Save it to", default=destination, show_default=True)
    try:
        result = save_artifact(fm, match.path, destination, create=True)
    except DestinationRefused as e:
        console.print(f"[red]{escape(str(e))}[/red] {escape(e.next)}")
        sys.exit(1)
    how = "moved on the same disk, nothing copied" if result.renamed else \
        "every copy checked against its original by sha256; not everything could be copied, so the build folder's " \
        "copy stays" if result.kept else \
        "every copy checked against its original by sha256, then the build folder's copy removed"
    console.print(f"[green]Saved: {escape(str(result.folder))} ({result.files} files, {size_text(result.bytes)}; "
                  f"{how}).[/green]")
    if result.zip is not None:
        console.print(f"Golden ZIP: {escape(str(result.zip))}")
    for note in result.notes:
        console.print(f"[yellow]{escape(note)}[/yellow]")


@build.command('status')
@click.argument('request_id', required=False)
@click.option('--all', '-a', is_flag=True, help=f'Show every kept build (default: the {STATUS_ROWS} newest)')
@pass_context
def build_status(ctx: FrameworkContext, request_id: Optional[str], all: bool):
    """Status of builds, including those the GUI, the MCP server or another CLI call started (the shared build state
    in the per-user folder, and the build records in the cache folder). REQUEST_ID: a request id (req_...) or a
    build id."""
    from orchestrator.Core.build_history import client_proposals, read_history
    config, resolve = _settings(ctx)
    history = read_history(config, resolve)  # the one query (the GUI's Build History reads it too)
    folder = history.folder
    if request_id:
        details = history.details(request_id)
        if details is None:
            console.print(f"[red]Build '{escape(request_id)}' not found in {escape(str(folder))}.[/red]")
            sys.exit(1)
        if details.request is not None:
            _print_request_summary(details.request)
        for record in details.attempts:  # attempts in order
            _print_build_record(record)
        for rid in details.proposal_requests:
            _print_client_proposals(client_proposals(config, rid))
        return
    rows = history.rows()
    if not rows:
        console.print(f"[yellow]No builds found in {escape(str(folder))}.[/yellow]")
        return
    shown = rows if all else rows[:STATUS_ROWS]
    table = Table(title="Builds")
    columns = ("request", "build", "status", "progress", "target", "format", "quantization", "model", "started",
               "where")
    for column in ("Request", "Build", "Status", "Progress", "Target", "Format", "Quantization", "Model",
                   "Started", "Where"):
        table.add_column(column)
    for row in shown:
        values = row.values()
        table.add_row(*(values[c] if c == "progress" else escape(values[c]) for c in columns))
    console.print(table)
    if len(rows) > len(shown):
        console.print(f"{len(rows) - len(shown)} older builds: llm-cli build status --all")
    proposed = sorted({p.request_id for p in client_proposals(config)})
    if proposed:
        console.print(f"[magenta]AI clients proposed fixes for {len(proposed)} failed build(s): "
                      f"llm-cli build status {escape(proposed[-1])}[/magenta]")


# The CLI's labels of the fields build_history lists (the GUI labels them in the person's language).
_BUILD_FIELD_LABELS = {"request": "Request", "build": "Build", "status": "Status", "stage": "Stage",
                       "progress": "Progress", "model": "Model", "target": "Target", "board": "Board",
                       "format": "Format", "quantization": "Quantization", "optimization": "Optimization",
                       "owner": "Started by", "where": "Where", "started": "Started", "ended": "Ended",
                       "output": "Output", "process": "Process", "error": "Error"}


def _print_request_summary(r: dict) -> None:
    from orchestrator.Core.build_history import request_rows
    for name, value in request_rows(r):
        if name == "owner":
            value = value or "the person"
        if value not in (None, ""):
            console.print(f"[bold]{_BUILD_FIELD_LABELS[name]}:[/bold] {escape(str(value))}")
    for error in r.get("errors") or []:
        console.print(f"[bold]Error:[/bold] {escape(str(error))}")
    if r.get("status") == "interrupted":
        console.print("[yellow]The process that ran this request has ended before it finished.[/yellow]")
    elif r.get("awaiting_decision"):
        console.print(f"[yellow]Waits for a decision on a self-healing proposal in the {escape(str(r.get('program')))}"
                      " that started it.[/yellow]")
    console.print("")


def _print_client_proposals(proposals: list) -> None:
    """Who proposed each fix, marked as AI-generated; the texts are the AI's and are printed as plain text."""
    from orchestrator.utils.untrusted import sanitize_untrusted

    def text(value) -> str:  # invisible characters out, markup escaped
        return escape(sanitize_untrusted(str(value), 2000))

    for p in proposals:
        console.print(f"[bold magenta]Fix proposal {escape(p.proposal_id)} (AI-generated), proposed by "
                      f"{text(p.who())}[/bold magenta]")
        console.print(f"  Summary: {text(p.summary)}")
        if p.root_cause:
            console.print(f"  Cause: {text(p.root_cause)}")
        console.print(f"  Manual step ({escape(p.where)}, not run by the framework): [italic]{text(p.manual_step)}"
                      f"[/italic]")
        console.print(f"  Confidence: {p.confidence:.0%}" + (f", {escape(p.created)}" if p.created else ""))
        console.print("")


def _print_build_record(r: dict) -> None:
    from orchestrator.Core.build_history import record_rows
    for name, value in record_rows(r):
        if value not in (None, ""):
            console.print(f"[bold]{_BUILD_FIELD_LABELS[name]}:[/bold] {escape(str(value))}")
    if r.get("status") == "interrupted":
        console.print("[yellow]The process that ran this build has ended before the build finished.[/yellow]")
    console.print("")

# ============================================================================
# SECRETS MANAGEMENT COMMANDS (NEU V2.0)
# ============================================================================

@cli.group()
def secrets():
    """Store tokens in the encrypted credential vault (AI provider keys: llm-cli ditto login)"""
    pass

@secrets.command('set')
@click.argument('key')
@click.option('--with-token', is_flag=True,
              help='Read the value from stdin, e.g. printf %s "$TOKEN" | llm-cli secrets set hf_token --with-token')
@click.option('--password', default=None,
              help='Deprecated: a value on the command line ends up in the shell history and the process list; '
                   'use the prompt or --with-token')
@pass_context
def set_secret(ctx: FrameworkContext, key, with_token: bool, password: Optional[str]):
    """Set a secret in the secure store. The value is asked twice without echo, or read from stdin (--with-token).
    AI provider keys: llm-cli ditto login <provider>."""
    if with_token and password is not None:
        _usage("Use either --with-token or --password, not both.")
    if password is not None:
        _say("--password is deprecated: the value is now in your shell history. Use the prompt or --with-token.",
             err=True, fg="yellow")
    ctx.require_framework()
    if not ctx.framework_manager.secrets_manager:
        console.print("[red]SecretsManager not available![/red]")
        sys.exit(1)
    if with_token:
        password = sys.stdin.read().strip()
        if not password:
            _usage("No value on stdin.")
    elif password is None:
        password = click.prompt("Value", hide_input=True, confirmation_prompt=True)

    from orchestrator.Core.secret_entries import SecretNotStored, store_secret
    try:
        stored = store_secret(ctx.framework_manager.secrets_manager, key, password)
    except SecretNotStored:
        console.print(f"[red]Failed to store secret '{escape(key)}'.[/red]")
        sys.exit(1)
    if stored.memory_only:
        # Never let a CI user believe it was saved.
        console.print(f"[yellow]Secret '{escape(key)}' is kept in memory for this run only "
                      "(secret_storage: ephemeral); nothing was saved.[/yellow]")
    else:
        console.print(f"[green]Secret '{escape(key)}' stored in the encrypted credential vault.[/green]")

@secrets.command('list')
@pass_context
def list_secrets(ctx: FrameworkContext):
    """List available secret keys (names only)."""
    ctx.require_framework()
    if not ctx.framework_manager.secrets_manager:
        console.print("[red]SecretsManager not available![/red]")
        sys.exit(1)
        
    from orchestrator.Core.secret_entries import stored_names
    keys = stored_names(ctx.framework_manager.secrets_manager)
    if not keys:
        console.print("[yellow]No secrets found.[/yellow]")
        return
        
    table = Table(title="Secure Storage Registry")
    table.add_column("Key Name", style="cyan")
    table.add_column("Status", style="green")
    for k in keys:
        table.add_row(k, "Encrypted")
    console.print(table)

# ============================================================================
# DEPLOYMENT COMMANDS (NEU V2.0)
# ============================================================================

# ============================================================================
# TOOLS & PERMISSIONS (shared by Ditto and the MCP server)
# ============================================================================

@cli.group()
def tools():
    """Framework tools, their permissions and the audit log"""
    pass


def _tool_registry(ctx: FrameworkContext):
    ctx.require_framework()
    registry = getattr(ctx.framework_manager, "tool_registry", None)
    if registry is None:
        console.print("[red]Tool registry not available.[/red]")
        sys.exit(1)
    return registry


CALLER_CHOICE = click.Choice(["ditto", "mcp"])
CALLER_LABELS = {None: "every AI caller without permissions of its own", "ditto": "Ditto",
                 "mcp": "local MCP clients (stdio)"}


@tools.command('list')
@click.option('--caller', type=CALLER_CHOICE, default=None,
              help="Whose permissions: ditto or mcp (default: the shared ones)")
@pass_context
def tools_list(ctx: FrameworkContext, caller: Optional[str]):
    """List the tools and what your permissions allow"""
    registry = _tool_registry(ctx)
    rows = registry.permissions(caller or "")
    policy = registry.policy()
    if registry.policy_store.problem:
        Console(stderr=True).print(f"[red]{escape(registry.policy_store.problem)}[/red]")
    table = Table(title=f"Framework tools for {CALLER_LABELS[caller]}"
                  + ("" if policy.configured else " (permissions not set up yet)"))
    table.add_column("Tool", style="cyan", no_wrap=True)
    table.add_column("Tier")
    table.add_column("Permission")
    table.add_column("Description")
    descriptions = {spec.name: spec.description for spec in registry.list()}
    for row in rows:
        facts = [f"default: {row['default']}", f"at most: {row['max']}"]
        if row["new"]:
            facts.append("new")
        facts.append("standing approval possible" if row["standing_approval"] else "no standing approval")
        table.add_row(row["tool"], row["tier"], row["decision"],
                      f"{descriptions[row['tool']]}\n({'; '.join(facts)})")
    console.print(table)
    if any(row["new"] for row in rows):
        console.print("New tools use their own default, never a looser tier setting, until you review them with "
                      "'llm-cli tools permissions'.")
    own = sorted(policy.callers)
    if own and caller is None:
        console.print(f"Callers with their own permissions: {', '.join(own)} (see --caller).")


def _ask_tool_policy(registry, current, caller: Optional[str] = None, shown_for: str = ""):
    """Interactive questions for the tool permissions (one per tier), for one caller or the shared ones. Every
    registered tool counts as reviewed afterwards: the questions apply to the tools listed above them. `shown_for`:
    whose tool facts to list (a named token's, whose own policy `current` is), else `caller`'s. Needs a person at a
    terminal: piped answers (an AI client with a shell) are refused."""
    from orchestrator.tools.policy import TIER_LABELS, Decision, ToolTier, caller_section, with_section
    _require_terminal("llm-cli tools permissions")
    # A caller's new section starts from the shared settings, so creating it never loosens a per-tool setting.
    section = caller_section(current, caller) if caller else current
    base_tiers = section.tiers
    overrides = dict(section.overrides)
    rows = registry.permissions(shown_for or caller or "")
    for row in rows:  # what each tool brings, shown before the person decides
        facts = [f"default {row['default']}", f"at most {row['max']}"] + (["new"] if row["new"] else []) + [
            "standing approval possible" if row["standing_approval"] else "no standing approval"]
        console.print(escape(f"  {row['tool']} ({row['tier']}): {', '.join(facts)}"))
    new = [row["tool"] for row in rows if row["new"]]
    if new:
        console.print(f"New tools since the last review (they use their own default until you save): "
                      f"{escape(', '.join(new))}")
    tiers = {}
    for tier, label in TIER_LABELS.items():
        choices = ["ask", "deny"] if tier == ToolTier.EXTERNAL else ["allow", "ask", "deny"]
        default = base_tiers.get(tier, Decision.ASK).value
        default = default if default in choices else "ask"
        answer = click.prompt(f"{label}", type=click.Choice(choices), default=default)
        tiers[tier] = Decision(answer)
    reviewed = sorted(spec.name for spec in registry.list())
    changed = with_section(current, caller, tiers=tiers, overrides=overrides, reviewed=reviewed)
    return changed.model_copy(update={"configured": True})


def _save_policy(registry, policy=None, change=None):
    """Saves the tool permissions a person set here (audited with actor cli). A change saved elsewhere meanwhile, or
    an audit log that cannot record it, is reported and nothing is saved."""
    from orchestrator.tools.policy import PolicyStoreError
    try:
        if change is not None:
            return registry.policy_store.update(change, actor="cli")
        return registry.policy_store.save(policy, actor="cli")
    except PolicyStoreError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)


def _print_grants(registry) -> None:
    """Each caller's standing approvals: scope in plain words, state, created (and how), uses and last use (from the
    audit log), expiry."""
    from orchestrator.tools.approvals import caller_label, tool_title
    rows = registry.grant_rows()
    if not rows:
        console.print("No standing approvals.")
        return
    console.print("[bold]Standing approvals (\"do not ask again\")[/bold]")
    for row in rows:
        grant = row["grant"]
        console.print(f"  {escape(grant.id)}  {escape(tool_title(registry, grant.tool))} for "
                      f"{escape(caller_label(row['caller']))}: {escape(grant.summary)}")
        console.print(f"      {row['state']}; created {escape(grant.created)} via {grant.created_via}; used "
                      f"{row['uses']} times" + (f", last {escape(row['last_use'])}" if row["last_use"] else "")
                      + (f"; until {escape(grant.expires)}" if grant.expires else "; until revoked"))
    console.print("Revoke one with 'llm-cli tools permissions --revoke-grant ID'; --reset clears them all.")


def _set_framework_only(registry, caller: Optional[str], add: Tuple[str, ...], remove: Tuple[str, ...]) -> None:
    from orchestrator.tools.policy import caller_section, with_section
    known = {spec.name for spec in registry.list()}
    unknown = sorted((set(add) | set(remove)) - known)
    if unknown:
        console.print(f"[red]Unknown tools: {escape(', '.join(unknown))} ('llm-cli tools list' shows them).[/red]")
        sys.exit(2)

    def change(current):
        # a caller's first own section starts from the shared permissions
        section = caller_section(current, caller) if caller else current
        tools = sorted((set(section.framework_only) | set(add)) - set(remove))
        return with_section(current, caller, framework_only=tools)
    policy = _save_policy(registry, change=change)
    tools = policy.framework_only_for(caller)
    console.print(f"[green]Asked in the framework only, for {escape(CALLER_LABELS[caller])}: "
                  f"{escape(', '.join(tools) or 'none')}[/green]")


@tools.command('permissions')
@click.option('--caller', type=CALLER_CHOICE, default=None,
              help="Set the permissions of one caller (ditto or mcp) instead of the shared ones")
@click.option('--token', 'token_name', help='Set the permissions of this named MCP token (with --reset: read only)')
@click.option('--reset', is_flag=True, help='Back to the recommended defaults (asks again at the next setup); '
                                            'with --caller: that caller uses the shared permissions again')
@click.option('--confirm-in-framework', 'framework_only', multiple=True, metavar='TOOL',
              help="Always put this tool's questions to you in the framework (approval window or 'tools approvals'), "
                   "never in the AI client's own dialog (repeatable)")
@click.option('--confirm-anywhere', 'anywhere', multiple=True, metavar='TOOL',
              help='Undo --confirm-in-framework for this tool (repeatable)')
@click.option('--grants', 'show_grants', is_flag=True, help='List the standing approvals ("do not ask again") and exit')
@click.option('--revoke-grant', 'revoke_grant', metavar='ID', help='Revoke one standing approval (needs a terminal)')
@pass_context
def tools_permissions(ctx: FrameworkContext, caller: Optional[str], token_name: Optional[str], reset: bool,
                      framework_only: Tuple[str, ...] = (), anywhere: Tuple[str, ...] = (), show_grants: bool = False,
                      revoke_grant: Optional[str] = None):
    """Set which tool calls are allowed, need confirmation, or are denied (needs a terminal)"""
    from orchestrator.tools.policy import reset_policy
    registry = _tool_registry(ctx)
    if show_grants:
        _print_grants(registry)
        return
    if revoke_grant:
        _require_terminal("llm-cli tools permissions --revoke-grant")
        from orchestrator.tools.policy import PolicyStoreError
        try:
            revoked = registry.revoke_grant(revoke_grant, "cli")
        except PolicyStoreError as e:
            console.print(f"[red]{escape(str(e))}[/red]")
            sys.exit(1)
        if not revoked:
            console.print(f"[red]There is no standing approval '{escape(revoke_grant)}' ('llm-cli tools permissions "
                          "--grants').[/red]")
            sys.exit(1)
        console.print(f"[green]Standing approval {escape(revoke_grant)} revoked: those calls ask again.[/green]")
        return
    if token_name is not None:
        if caller:
            console.print("[red]Use either --caller or --token.[/red]")
            sys.exit(2)
        _token_permissions(registry, token_name, reset)
        return
    if framework_only or anywhere:
        _require_terminal("llm-cli tools permissions --confirm-in-framework")
        _set_framework_only(registry, caller, framework_only, anywhere)
        return
    if reset:
        _require_terminal("llm-cli tools permissions --reset")
        _save_policy(registry, change=lambda current: reset_policy(current, caller))
        if caller:
            console.print(f"[yellow]{CALLER_LABELS[caller]} use the shared tool permissions again.[/yellow]")
            return
        console.print("[yellow]Tool permissions reset to the recommended defaults.[/yellow]")
        return
    console.print(f"Tool permissions for {CALLER_LABELS[caller]}.")
    current = registry.policy()
    if registry.policy_store.problem:  # e.g. not signed: the questions start from "deny"
        console.print(f"[yellow]{escape(registry.policy_store.problem)}[/yellow]")
    if current.grants:
        _print_grants(registry)
    policy = _save_policy(registry, _ask_tool_policy(registry, current, caller))
    console.print(f"[green]Saved to {registry.policy_store.path}[/green]")
    tiers = policy.callers[caller].tiers if caller else policy.tiers
    for tier, decision in tiers.items():
        console.print(f"  {tier.value}: {decision.value}")


def _audit_rows(registry, entries, details: bool = False):
    """The audit view for people (orchestrator/tools/audit_view.py, shared with the GUI's Audit Log window)."""
    from orchestrator.tools.audit_view import audit_rows
    return audit_rows(registry, entries, details)


@tools.command('audit')
@click.option('--limit', default=30, show_default=True, help='Number of entries')
@click.option('--details', is_flag=True, help='Also show repairs, duration, result hash, links and session')
@click.option('--verify', is_flag=True,
              help='Check the chain: changed or removed entries, and removal of the newest entries (anchor)')
@click.option('--archive', is_flag=True,
              help='Move the log and its anchor aside (kept with a timestamp) and start a new log')
@click.option('--summary', is_flag=True,
              help='Counts per caller: calls, refusals, and the reports that a task cannot be done by how the '
                   'framework checked them (confirmed, contradicted, premature, unchecked)')
@pass_context
def tools_audit(ctx: FrameworkContext, limit: int, details: bool, verify: bool, archive: bool, summary: bool):
    """Show the audit log of tool calls"""
    import sqlite3

    from orchestrator.tools.audit_view import DETAIL_COLUMNS, ENTRY_COLUMNS, SUMMARY_COLUMNS, SUMMARY_ENTRIES
    registry = _tool_registry(ctx)
    if registry.audit is None:
        console.print("[red]No audit log.[/red]")
        sys.exit(1)
    if archive:
        try:
            target = registry.audit.archive()
        except (OSError, sqlite3.Error) as e:
            console.print(f"[red]The audit log {escape(str(registry.audit.path))} cannot be moved: "
                          f"{escape(str(e))}[/red] Close the GUI and AI clients using the framework and try again.")
            sys.exit(1)
        if target is None:
            console.print("No audit log to archive.")
        else:
            console.print(f"[green]Audit log moved to {escape(str(target))}; the next tool call starts a new "
                          "log.[/green]")
        return
    if summary:
        try:
            rows = _audit_summary(registry.audit.entries(SUMMARY_ENTRIES))
        except (sqlite3.Error, OSError) as e:
            console.print(f"[red]The audit log {escape(str(registry.audit.path))} cannot be read: "
                          f"{escape(str(e))}[/red]")
            sys.exit(1)
        table = Table(title="Tool calls per caller")
        for column in SUMMARY_COLUMNS:
            table.add_column(column)
        for row in rows:
            table.add_row(*(escape(str(cell)) for cell in row))
        console.print(table)
        return
    try:
        if verify:
            result = registry.audit.check()
            color = "green" if result.intact else "yellow" if result.problem == "anchor_missing" else "red"
            console.print(f"[{color}]{escape(result.message)}[/{color}]")
            sys.exit(0 if result.intact else 1)
        entries = registry.audit.entries(limit)
    except (sqlite3.Error, OSError) as e:
        console.print(f"[red]The audit log {escape(str(registry.audit.path))} cannot be read: {escape(str(e))}[/red]")
        sys.exit(1)
    table = Table(title="Tool calls")
    for column in ENTRY_COLUMNS + (DETAIL_COLUMNS if details else ()):
        table.add_column(column)
    for row in _audit_rows(registry, entries, details):
        table.add_row(*(escape(cell) for cell in row))
    console.print(table)


def _audit_summary(entries) -> list:
    """Rows per caller: calls, refusals, report_cannot_do reports and how they were checked
    (orchestrator/tools/audit_view.py, shared with the GUI's Audit Log window)."""
    from orchestrator.tools.audit_view import audit_summary
    return audit_summary(entries)


# Pending approvals: questions of AI clients that could not ask you themselves (design 6.9).

def _approval_store(registry):
    service = getattr(registry, "approvals", None)
    if service is None:
        console.print("[red]Pending approvals are not available.[/red]")
        sys.exit(1)
    return service.store


def _show_approval(record) -> None:
    left = record.seconds_left()
    console.print(f"[bold]{escape(record.id)}[/bold]  {escape(record.title)} ({escape(record.tool)}), "
                  f"{left // 60} min {left % 60:02d} s left")
    console.print(f"Caller: {escape(record.caller_class)}")
    console.print(f"Client: {escape(record.client or 'unnamed client')} (self-reported)")
    click.echo("")
    click.echo(record.question)  # escaped by the framework (visible()), shown as plain text
    click.echo("")


@tools.group('approvals', invoke_without_command=True)
@click.pass_context
def tools_approvals(click_ctx):
    """Calls of AI clients waiting for your approval (needs a terminal)"""
    if click_ctx.invoked_subcommand is None:
        click_ctx.invoke(tools_approvals_list)


@tools_approvals.command('list')
@pass_context
def tools_approvals_list(ctx: FrameworkContext):
    """The calls waiting for your approval"""
    _require_terminal("llm-cli tools approvals")
    store = _approval_store(_tool_registry(ctx))
    pending = store.pending()
    if store.problem:
        console.print(f"[red]{escape(store.problem)}[/red]")
    if not pending:
        console.print("No call is waiting for your approval.")
        return
    console.print("[bold]Calls waiting for your approval[/bold]")
    for record in pending:  # one line each: ids stay whole in a narrow terminal
        left = record.seconds_left()
        what = "asks to stop asking" if record.kind == "grant_request" else \
            ("call, 'do not ask again' possible" if record.grant is not None else "call")
        console.print(f"  {escape(record.id)}  {escape(record.title)} ({what})  caller: {escape(record.caller_class)}  "
                      f"client: {escape(record.client or 'unnamed')} (self-reported)  {left // 60} min left")
    console.print("Show one with 'llm-cli tools approvals show ID', then approve or decline it.")


def _pending_record(store, approval_id: str):
    record = store.get(approval_id)
    if record is None:
        console.print(f"[red]There is no approval '{escape(approval_id)}' ('llm-cli tools approvals' lists "
                      "them).[/red]")
        sys.exit(1)
    return record


@tools_approvals.command('show')
@click.argument('approval_id')
@pass_context
def tools_approvals_show(ctx: FrameworkContext, approval_id: str):
    """Show the full question of a waiting call"""
    _require_terminal("llm-cli tools approvals show")
    record = _pending_record(_approval_store(_tool_registry(ctx)), approval_id)
    _show_approval(record)
    if record.state != "pending":
        console.print(f"State: {escape(record.state)}")


def _grant_choice(registry, record, width: Optional[str], duration: Optional[str]):
    """The person's "do not ask again" for this record: refused while a GUI session runs (its window is the stronger
    channel), only within what the framework offered, shown with its warning."""
    from orchestrator.tools.grants import DURATION_LABELS, gui_running
    offer = record.grant
    if offer is None:
        console.print("[red]'Do not ask again' is not offered for this call.[/red]")
        sys.exit(1)
    if gui_running(registry.policy_store.path.parent):
        console.print("[red]A GUI session of the framework is running: use its approval window (Tools > Pending "
                      "Approvals...) for 'do not ask again'.[/red]")
        sys.exit(1)
    width = width or offer.default_width()
    duration = duration or offer.default_duration()
    if width not in offer.scopes or duration not in offer.durations:
        console.print(f"[red]Offered here: width {escape(', '.join(offer.scopes))}; duration "
                      f"{escape(', '.join(offer.durations))}.[/red]")
        sys.exit(2)
    click.echo(f"Do not ask again for: {offer.summaries[width]}")
    click.echo(f"For how long: {DURATION_LABELS[duration]}")
    click.echo("")
    click.echo(offer.warning)
    click.echo("")
    return width, duration


def _decide_approval(ctx: FrameworkContext, approval_id: str, approve: bool, always: bool = False,
                     width: Optional[str] = None, duration: Optional[str] = None) -> None:
    from orchestrator.tools.approvals import ApprovalStoreError, apply_decision
    registry = _tool_registry(ctx)
    store = _approval_store(registry)
    record = _pending_record(store, approval_id)
    _show_approval(record)
    choice = None
    if approve and (always or record.kind == "grant_request"):
        choice = _grant_choice(registry, record, width, duration)
    if record.kind == "grant_request":
        question = "Stop asking for such calls within this scope?" if approve else "Decline this request?"
    elif choice is not None:
        question = "Approve this call and do not ask again within this scope?"
    else:
        question = "Approve this call? The framework then runs it for the AI client." if approve else \
            "Decline this call?"
    if not click.confirm(question, default=False):
        console.print("Nothing decided.")
        sys.exit(1)

    def audit(decided):
        apply_decision(registry, decided, "cli")
    try:
        store.decide(approval_id, approve, "cli", expected_question=record.question_sha256, record_decision=audit,
                     choice=choice)
    except (ApprovalStoreError, ValueError) as e:  # ValueError: "do not ask again" cannot be saved for it
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    except Exception as e:  # the audit log cannot record it: nothing was decided
        console.print(f"[red]The audit log could not record your decision ({escape(str(e))}); nothing was "
                      "decided.[/red]")
        sys.exit(1)
    if choice is not None:
        console.print("[green]Standing approval saved; revoke it with 'llm-cli tools permissions --revoke-grant ID' "
                      "(the IDs: 'llm-cli tools permissions --grants').[/green]")
    if record.kind == "grant_request":
        return
    console.print(f"[green]Approved: the framework runs '{escape(record.title)}' now.[/green]" if approve else
                  f"[yellow]Declined: '{escape(record.title)}' does not run.[/yellow]")


@tools_approvals.command('approve')
@click.argument('approval_id')
@click.option('--always', is_flag=True, help="Approve and do not ask again for calls within the offered scope "
                                             "(refused while a GUI session runs: use its approval window)")
@click.option('--width', type=click.Choice(['this', 'any']), help="With --always: this model or repository only "
                                                                  "(default), or any")
@click.option('--duration', type=click.Choice(['revoke', '30d', '24h']),
              help="With --always: until you revoke it (default; it lapses after 90 days without use), 30 days or "
                   "24 hours (all local stdio clients together: 30 days at most)")
@pass_context
def tools_approvals_approve(ctx: FrameworkContext, approval_id: str, always: bool, width: Optional[str],
                            duration: Optional[str]):
    """Approve a waiting call; the framework runs it for the AI client"""
    _require_terminal("llm-cli tools approvals approve")
    _decide_approval(ctx, approval_id, True, always=always, width=width, duration=duration)


@tools_approvals.command('decline')
@click.argument('approval_id')
@pass_context
def tools_approvals_decline(ctx: FrameworkContext, approval_id: str):
    """Decline a waiting call"""
    _require_terminal("llm-cli tools approvals decline")
    _decide_approval(ctx, approval_id, False)


@tools.group('folders')
def tools_folders():
    """Folders the AI may use for local model and dataset files"""
    pass


def _local_files(ctx: FrameworkContext):
    from orchestrator.tools.local_files import LocalFiles
    registry = _tool_registry(ctx)
    return registry, LocalFiles.for_framework(ctx.framework_manager)


def _require_terminal(command: str) -> None:
    """Changes to what an AI may do are a person's decision: no piped answers (an AI client with a shell)."""
    if not _interactive():
        console.print(f"[red]'{escape(command)}' needs a terminal: run it yourself, not through a pipe or an AI "
                      "client.[/red]")
        sys.exit(2)


@tools_folders.command('list')
@pass_context
def tools_folders_list(ctx: FrameworkContext):
    """The folders AI clients and Ditto may use, with their labels"""
    _, files = _local_files(ctx)
    table = Table(title="Folders the AI may use (local files are named <label>:<relative path>)")
    for column in ("Label", "Kind", "Folder", "Status"):
        table.add_column(column)
    for folder in files.folders():
        status = ("built-in" if folder.builtin else "added") if not folder.problem else \
            f"[red]not usable: {escape(folder.problem)}[/red]"
        table.add_row(escape(folder.label), folder.kind, escape(str(folder.path)), status)
    console.print(table)
    if files.store is not None and files.store.problem:
        console.print(f"[red]{escape(files.store.problem)}[/red]")


@tools_folders.command('add')
@click.argument('path', type=click.Path(file_okay=False))
@click.option('--label', required=True, help="Short name AI clients use for it (lowercase, e.g. 'mymodels')")
@click.option('--kind', type=click.Choice(['model', 'dataset', 'any']), default='any', show_default=True,
              help='What the folder holds (shown to AI clients)')
@pass_context
def tools_folders_add(ctx: FrameworkContext, path: str, label: str, kind: str):
    """Let AI clients and Ditto use the model or dataset files in PATH (asks first; needs a terminal). Adding a
    label again confirms its folder again, for example after a link was changed."""
    from orchestrator.tools.local_files import FOLDER_WARNING, LocalFileError, record_folder_change
    _require_terminal("llm-cli tools folders add")
    registry, files = _local_files(ctx)
    try:
        real = files.store.check_new(Path(path), files.data_folders())
    except LocalFileError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    console.print(f"Folder: {escape(str(real))}\nLabel: {escape(label)} (files are named {escape(label)}:<path>)")
    console.print(f"[yellow]{escape(FOLDER_WARNING)}[/yellow]")
    if not click.confirm("Let AI clients and Ditto use the files in this folder?", default=False):
        console.print("Nothing changed.")
        sys.exit(1)
    try:
        entry = files.store.add(Path(path), label, kind, files.data_folders(), expected_real=real)
    except LocalFileError as e:
        console.print(f"[red]{escape(str(e))}[/red] The folder was not added.")
        sys.exit(1)
    try:
        record_folder_change(registry.audit, "cli", "added", entry)
    except Exception as e:  # what widens an AI's reach is recorded, or it does not happen
        files.store.remove(label)
        console.print(f"[red]The audit log could not record the change ({escape(str(e))}); the folder was not "
                      "added.[/red]")
        sys.exit(1)
    console.print(f"[green]AI clients and Ditto may now use files in {escape(str(real))} as "
                  f"{escape(label)}:<relative path>.[/green]")


@tools_folders.command('remove')
@click.argument('label')
@pass_context
def tools_folders_remove(ctx: FrameworkContext, label: str):
    """Stop AI clients and Ditto from using the folder LABEL (needs a terminal)"""
    from orchestrator.tools.local_files import LocalFileError, record_folder_change
    _require_terminal("llm-cli tools folders remove")
    registry, files = _local_files(ctx)
    try:
        removed = files.store.remove(label)
    except LocalFileError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    if not removed:
        console.print(f"[red]No added folder is labelled '{escape(label)}'.[/red] The models and datasets folders "
                      "are settings (models_dir, datasets_dir).")
        sys.exit(1)
    try:
        record_folder_change(registry.audit, "cli", "removed", {"label": label})
    except Exception as e:
        console.print(f"[yellow]The folder was removed, but the audit log could not record it: {escape(str(e))}"
                      "[/yellow]")
    console.print(f"[green]'{escape(label)}' removed.[/green]")


@tools.command('crawl-notice')
@pass_context
def tools_crawl_notice(ctx: FrameworkContext):
    """Read the one-time Deep Ingest notice (needs a terminal); AI clients may crawl only after a person read it"""
    from orchestrator.utils.crawl_notice import CrawlNotice, acknowledge_and_audit, notice_text
    from orchestrator.utils.localization import current_language
    _require_terminal("llm-cli tools crawl-notice")
    ctx.require_framework()
    notice = CrawlNotice(ctx.framework_manager.config.user_state_dir)
    title, text = notice_text(current_language())
    console.print(f"[bold]{escape(title)}[/bold]\n\n{escape(text)}\n")
    if not notice.needed():
        console.print(f"Already read (acknowledged in the {notice.acknowledged_via() or 'GUI or CLI'}).")
        return
    if not click.confirm("I have read the notice", default=False):
        console.print("Not acknowledged: AI clients cannot crawl until a person has read it.")
        sys.exit(1)
    registry = getattr(ctx.framework_manager, "tool_registry", None)
    problem = acknowledge_and_audit(notice, "cli", getattr(registry, "audit", None))
    if problem:
        console.print(f"[yellow]Acknowledged, but the audit log could not record it: {escape(problem)}[/yellow]")
    console.print("[green]Acknowledged. AI clients may now ask to crawl documentation sites (crawl_docs_site); "
                  "you confirm every crawl.[/green]")


@cli.group()
def ditto():
    """Ditto, the AI assistant (experimental): sign-in (login, logout, auth), questions, evaluation and the local
    model"""
    _ditto_notice_once()


def _notice_terminal() -> bool:
    """A person reads stderr: only then is the notice on Ditto printed and recorded."""
    try:
        return sys.stderr is not None and sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def _ditto_notice_once() -> None:
    """Ditto is optional and experimental (eighth round, Q-2): before the first command at a terminal that uses Ditto
    (the `llm-cli ditto` group, `llm-cli module generate-ai`) the notice goes to stderr and is recorded per person
    (utils/ditto_notice.py). Without a terminal nothing is printed or recorded, so the next command at a terminal
    prints it. It never stops the command."""
    if not _notice_terminal():
        return
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.utils.ditto_notice import DittoNotice, notice_text
    from orchestrator.utils.localization import current_language
    notice = DittoNotice(ConfigManager.user_state_path())
    if not notice.needed():
        return
    title, text = notice_text(current_language())
    err = Console(stderr=True)
    err.print(f"[bold yellow]{escape(title)}[/bold yellow]\n{escape(text)}\n")
    try:
        notice.acknowledge("cli")
    except OSError as e:
        err.print(f"[yellow]The notice could not be remembered ({escape(str(e))}); it is shown again next time."
                  "[/yellow]")


def _local_runtime(ctx: FrameworkContext):
    """The managed llama-server with the configured models folder (models_dir, as Ditto uses it); needs neither
    the keyring nor Docker, so the framework is not booted for it."""
    from orchestrator.runtime.llama_server import LocalLLMRuntime
    # The effective models_dir (LLM_FRAMEWORK_MODELS_DIR, config.yml, or the per-user folder when the installation
    # is read-only), the same folder the GUI and the MCP server use.
    config, resolve = _settings(ctx)
    return LocalLLMRuntime(data_dir=config.user_state_dir,
                           models_dir=resolve(config.get("models_dir", "models") or "models") / "ditto")


def _download_progress():
    from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
    bar = Progress(TextColumn("{task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn(),
                   console=_rich_console(), transient=True)
    tasks = {}

    def update(name: str, done: int, total: Optional[int]):
        if name not in tasks:
            tasks[name] = bar.add_task(escape(name), total=total)
        bar.update(tasks[name], completed=done)
    return bar, update


def _gib(size: Optional[int]) -> str:
    return f"{size / 1024 ** 3:.1f} GiB" if size else "?"


def _runtime_failure(error) -> None:
    """Prints a runtime error (and the server log excerpt, escaped) and exits with 1."""
    console.print(f"[red]{escape(str(error))}[/red]")
    if getattr(error, "log_tail", ""):
        console.print(Panel(escape(error.log_tail), title="llama-server log (last lines)"))
    sys.exit(1)


@ditto.group('runtime')
def ditto_runtime():
    """Local model server (llama.cpp) for offline use"""
    pass


def _installed_line(inst) -> str:
    """origin label, build, backend and commit of the build in use (escaped for rich)."""
    from orchestrator.runtime.runtime_overview import installed_summary
    return escape(installed_summary(inst))


@ditto_runtime.command('status')
@click.option('--json', 'as_json', is_flag=True, help='Machine-readable output')
@pass_context
def runtime_status(ctx: FrameworkContext, as_json: bool):
    """Hardware, the build in use (self-built, imported or prebuilt), the recommended model, and the server"""
    from orchestrator.runtime.runtime_overview import overview
    rt = _local_runtime(ctx)
    with console.status("Checking the hardware and the builds ..."):
        view = overview(rt)   # the same call as the GUI's Ditto > Local Runtime... window
    if as_json:
        click.echo(json.dumps(view.as_json(), indent=2))
        return
    status, hardware, prebuilt, model, why = view.status, view.hardware, view.prebuilt, view.model, view.tier_reason
    backend, backend_reason, preferred, inst = view.backend, view.backend_reason, view.preferred, status.installed
    table = Table(title=f"Local runtime (llama.cpp {escape(status.build)})", show_header=False)
    table.add_column("", style="cyan")
    table.add_column("")
    table.add_row("Hardware", escape(hardware.summary()))
    _runtime_mode_rows(table, status)
    if inst is not None:
        style = "yellow" if inst.origin == "prebuilt" else "green"
        table.add_row("In use" if status.mode != "docker" else "Bare metal",
                      f"[{style}]{_installed_line(inst)}[/{style}]")
        facts = view.build_facts()
        if facts is not None:
            built = [f"{facts['placement'] or '?'} build"]
            if facts["duration_s"] is not None:
                built.append(f"{facts['duration_s']:.0f} s")
            if facts["built_on"]:
                built.append(f"on {facts['built_on']}")
            if facts["built_at"]:
                built.append(facts["built_at"])
            table.add_row("Built", escape(", ".join(built)))
            devices = ", ".join(inst.devices) or "CPU only"
            table.add_row("Verified", escape(f"{inst.verified_at or '?'}; devices: {devices}"))
        for note in inst.notes:
            table.add_row("Note", f"[yellow]{escape(note)}[/yellow]")
    else:
        table.add_row("In use", "[yellow]nothing[/yellow] (llm-cli ditto runtime install builds llama-server)")
    for problem in view.extra_problems():
        table.add_row("Problem", f"[red]{escape(problem)}[/red]")
    if status.rebuild:
        table.add_row("Rebuild", f"[yellow]{escape(status.rebuild)}[/yellow]")
    table.add_row("Self-built plan", escape(f"CPU build, then {backend}: {backend_reason}" if view.gpu_after_cpu
                                            else f"{backend}: {backend_reason}"))
    table.add_row("Prebuilt opt-in", escape(f"{prebuilt.asset.id if prebuilt.asset else '-'} (--prebuilt, not "
                                            f"self-built): {prebuilt.reason}"))
    if status.running:
        health = "[green]ready[/green]" if status.healthy else "[yellow]starting / not answering[/yellow]"
        where = f"container {status.container}" if status.container else f"pid {status.pid}"
        table.add_row("Server", f"{health} {escape(str(status.base_url))} ({escape(where)}, "
                                f"model {escape(str(status.model))}, ctx {status.ctx})")
    else:
        table.add_row("Server", "stopped")
    table.add_row("Recommended model", escape(f"{model.file} - {why}"))
    if view.chosen_differs:
        table.add_row("Chosen model", escape(f"{preferred} (llm-cli ditto runtime start --model auto resets it)"))
    table.add_row("Log", escape(str(status.log_file)))
    console.print(table)


def _runtime_mode_rows(table: Table, status) -> None:
    """The runtime mode and, in Docker mode, Ditto's verified image (its name, ID, build, base and how it was made)."""
    if status.mode != "docker":
        table.add_row("Mode", "bare metal (llm-cli ditto runtime ensure --mode docker --force switches)")
        return
    image = status.docker or {}
    table.add_row("Mode", "Docker (llm-cli ditto runtime ensure --mode bare-metal --force switches)")
    table.add_row("In use", "[green]" + escape(
        f"image {image.get('tag') or '?'} ({str(image.get('image_id') or '')[7:19]}), build "
        f"{str(image.get('build_id') or '')[:16]} ({image.get('backend') or '?'}, {image.get('made_by') or '?'}, on "
        f"{image.get('base') or '?'}), verified in a container at {image.get('verified_at') or '?'}") + "[/green]")


@ditto_runtime.command('install')
@click.option('--backend', type=click.Choice(['auto', 'cpu', 'vulkan', 'cuda', 'rocm', 'metal']), default='auto',
              show_default=True, help='GPU backend to build after the CPU build (auto: the build recipe chooses; '
              'cpu: only the CPU build)')
@click.option('--placement', type=click.Choice(['auto', 'native', 'container']), default='auto', show_default=True,
              help="Where the build runs: with this machine's toolchain, or in the framework's builder container "
              "(Docker, no network during the build)")
@click.option('--jobs', type=click.IntRange(1, 256), default=None,
              help='Parallel compile jobs (default: from the CPUs and the free memory)')
@click.option('--prebuilt', is_flag=True,
              help="Download the official prebuilt llama.cpp archive instead (not self-built; size and sha256 "
              "verified)")
@click.option('--force', is_flag=True, help='With --prebuilt: download and unpack again even if it is installed')
@click.option('--yes', '-y', is_flag=True, help='Do not ask before building or downloading')
@pass_context
def runtime_install(ctx: FrameworkContext, backend: str, placement: str, jobs: Optional[int], prebuilt: bool,
                    force: bool, yes: bool):
    """Build llama-server on this machine from the pinned llama.cpp commit (CPU build first, then the GPU build)"""
    from orchestrator.runtime.llama_server import PREBUILT_LABEL, LocalRuntimeError
    rt = _local_runtime(ctx)
    if prebuilt:
        choice = rt.select_backend(backend)
        if choice.asset is None:
            console.print(f"[red]{escape(choice.reason)}[/red]")
            sys.exit(1)
        console.print(f"llama.cpp {escape(rt.build)} - {escape(choice.asset.id)} ([yellow]{PREBUILT_LABEL}[/yellow]): "
                      f"{escape(choice.reason)}")
        if rt.prebuilt_installed(choice.asset) and not force:
            question = (f"This official archive is already installed in {rt.root}. Use it for Ditto's server "
                        f"({PREBUILT_LABEL})?")
        else:
            question = f"Download the official archive ({_gib(choice.asset.download_size)}) into {rt.root}?"
        if not yes and not click.confirm(question, default=True):
            return
        bar, update = _download_progress()
        try:
            with bar:
                inst = rt.install_prebuilt(prefer=backend, force=force, progress=update)
        except LocalRuntimeError as e:
            _runtime_failure(e)
        console.print(f"[green]Installed {escape(str(inst.asset_id))} in {escape(str(inst.path))}[/green] "
                      f"([yellow]{PREBUILT_LABEL}[/yellow])")
        return
    if force:
        console.print("[dim]--force applies to --prebuilt: a self-built build is reused by its build ID and built "
                      "again when its files no longer match its manifest.[/dim]")
    try:
        with console.status("Probing this machine and planning the build ..."):
            plan = rt.plan_install(backend=backend, placement=placement)
    except LocalRuntimeError as e:
        _runtime_failure(e)
    for line in plan.describe():
        console.print(escape(line))
    for note in plan.notes:
        console.print(f"[dim]{escape(note)}[/dim]")
    if plan.needs_source and not yes and not click.confirm(
            "Build now? The CPU build takes a few minutes, a GPU build longer.", default=True):
        return

    def fetch_ok() -> bool:
        pin = rt.catalog.llama_cpp.source
        return yes or click.confirm(f"The pinned llama.cpp source ({pin.tag}, commit {pin.commit[:12]}, about 215 MB) "
                                    "is not cached yet. Fetch it now?", default=True)

    try:
        result = rt.install(plan, jobs=jobs, log=lambda msg: console.print(escape(msg)), fetch_source=fetch_ok)
    except LocalRuntimeError as e:
        _runtime_failure(e)
    active, fallback = result.active, result.fallback
    console.print(f"[green]Self-built llama-server ready:[/green] {escape(active.backend)} build "
                  f"{escape(active.build_id[:16])} (verified on this machine)" +
                  (f", CPU fallback {escape(fallback.build_id[:16])}" if fallback.build_id != active.build_id else ""))
    for problem in result.problems:
        console.print(f"[yellow]{escape(problem)}[/yellow]")
    if result.older:
        console.print(f"[dim]{len(result.older)} older build(s) kept; the framework deletes none by itself. "
                      "'llm-cli ditto runtime prune' lists them for you to delete.[/dim]")
    if rt.endpoint() is not None:
        console.print("The running server keeps running; the next start uses the new build "
                      "(llm-cli ditto runtime start).")
    if rt.mode() == "docker":
        console.print("Ditto's server runs in Docker mode; this bare-metal build is used after a switch: llm-cli ditto "
                      "runtime ensure --mode bare-metal --force")
    if result.requested_missing:
        sys.exit(1)


@ditto_runtime.command('ensure')
@click.option('--mode', type=click.Choice(['auto', 'bare-metal', 'docker']), default='auto', show_default=True,
              help="auto: keep what is installed, and when nothing is, install bare metal (no Docker overhead), or "
              "Docker when bare metal is not possible. bare-metal or docker: the mode of a new installation (with "
              "--force: switch to it)")
@click.option('--force', is_flag=True, help="Switch to --mode although Ditto is installed in the other mode: the "
              "models are reused, only the runtime is made again (repackaged where possible) after a check that it "
              "fits next to the old mode; once the new mode works, every object of the old mode is listed and deleted "
              "for good after your one yes. Needs a terminal")
@click.option('--placement', type=click.Choice(['auto', 'native', 'container']), default='auto', show_default=True,
              help="Where a build runs (as for install): with this machine's toolchain or in the builder container")
@click.option('--backend', type=click.Choice(['auto', 'cpu', 'vulkan', 'cuda', 'rocm', 'metal']), default='auto',
              show_default=True, help='GPU backend (as for install)')
@click.option('--jobs', type=click.IntRange(1, 256), default=None, help='Parallel compile jobs of a build')
@click.option('--no-model', is_flag=True, help="Do not offer the download of the recommended model")
@click.option('--yes', '-y', is_flag=True, help="Do not ask before fetching the source or downloading a model under a "
              "permissive licence (its size and licence are still shown first; the build starts without a question "
              "anyway); removes nothing")
@pass_context
def runtime_ensure(ctx: FrameworkContext, mode: str, force: bool, placement: str, backend: str, jobs: Optional[int],
                   no_model: bool, yes: bool):
    """Install Ditto's local runtime when it is not there, bare metal or in Docker (running it again does nothing)"""
    from orchestrator.runtime.llama_server import LocalRuntimeError
    from orchestrator.runtime.runtime_mode import RuntimeModeError, SpaceShortage, container_build_requested, ensure
    if force and mode == "auto":
        _usage("--force needs a mode: --mode bare-metal or --mode docker.")
    if force:
        _require_terminal("llm-cli ditto runtime ensure --force")  # switching the mode is your decision
    rt = _local_runtime(ctx)

    def fetch_ok() -> bool:
        pin = rt.catalog.llama_cpp.source
        text = f"the pinned llama.cpp source ({pin.tag}, commit {pin.commit[:12]}, about 215 MB)"
        if yes:
            console.print(f"Fetching {escape(text)} from GitHub (checked by its commit).")
            return True
        if not _interactive():
            console.print(f"[yellow]{escape(text[0].upper() + text[1:])} is not cached yet; run this in a terminal "
                          "or with -y to fetch it.[/yellow]")
            return False
        return click.confirm(f"{text[0].upper() + text[1:]} is not cached yet. Fetch it now?", default=True)

    def run(wanted: str, forced: bool):
        return ensure(rt, mode=wanted, force=forced, placement=placement, backend=backend, jobs=jobs,
                      log=lambda msg: console.print(escape(msg)), fetch_source=fetch_ok,
                      container_build=container_build_requested())

    try:
        result = run(mode, force)
    except SpaceShortage as e:
        result = _runtime_space_shortage(ctx, rt, e, run)
    except (RuntimeModeError, LocalRuntimeError) as e:
        _runtime_failure(e)
    if result.leftovers is not None:
        _runtime_leftovers(ctx, rt, result.leftovers)
    if not no_model:
        _offer_ditto_model(rt, yes)


def _runtime_objects_table(title: str, plan) -> None:
    """Every object of a list: its name, full path (or Docker's ID), file name, size and what it is."""
    from orchestrator.Core.knowledge_snapshots import size_text
    table = Table(title=title)
    for column in ("Name", "Full path or ID", "File name", "Size", "What it is"):
        table.add_column(column, overflow="fold")
    for item in plan.items:
        table.add_row(escape(item.speaking_name), escape(item.full_path or item.file_name), escape(item.file_name),
                      size_text(item.size_bytes), escape(item.what))
    console.print(table)
    if any(i.kind == "docker_image" for i in plan.items):
        console.print("Image sizes include layers other images may share, so less space can be freed.")


def _runtime_removed(removal) -> None:
    from orchestrator.Core.knowledge_snapshots import size_text
    for name in removal.deleted:
        console.print(f"[green]Deleted: {escape(name)}[/green]")
    for name, reason in removal.failed:
        console.print(f"[yellow]Kept: {escape(name)} ({escape(reason)})[/yellow]")
    console.print(f"{len(removal.deleted)} deleted for good, about {size_text(removal.freed_bytes)}.")


def _runtime_space_shortage(ctx: FrameworkContext, rt, shortage, run):
    """W2: the new mode does not fit next to the old one, so nothing was built. The sizes, then your choice: free space
    and switch again, or remove the old mode first (its full list, one yes, default no; Ditto is unavailable until the
    new mode works, the models stay). Removing it first is offered only while the new mode may fit then (estimated
    again without the old mode's build), and only at a terminal: without one nothing is deleted or built. When the new
    mode then fails, you are told so and offered the old mode again. Returns the result of the switch; exits when
    nothing was switched."""
    from orchestrator.Core.knowledge_snapshots import size_text
    from orchestrator.runtime.llama_server import LocalRuntimeError
    from orchestrator.runtime.runtime_mode import (LeftoversChanged, RuntimeModeError, SpaceShortage, mode_label,
                                                   remove_leftovers)
    new, old, plan = shortage.estimate.mode, shortage.old_mode, shortage.old_plan
    after = shortage.after_removal
    again = f"llm-cli ditto runtime ensure --mode {new} --force"
    console.print(f"[yellow]{escape(mode_label(new))} does not fit next to {escape(mode_label(old))}, so nothing was "
                  "built.[/yellow]")
    for line in shortage.estimate.lines():
        console.print(escape(line))
    if plan is None:
        console.print(f"Nothing of {escape(mode_label(old))} is left to remove. Free space on the disks above and run "
                      f"again: {escape(again)}")
        sys.exit(1)
    if shortage.removal_helps is False:
        # deleting the old mode first would only leave Ditto without a local runtime: not offered
        console.print(f"[yellow]Even with {escape(mode_label(old))} deleted first, {escape(mode_label(new))} would not "
                      "fit:[/yellow]")
        for line in after.lines():
            console.print(escape(line))
        console.print(f"So deleting {escape(mode_label(old))} first is not offered; nothing was deleted and nothing was "
                      f"built. Free space on the disks above and run again: {escape(again)}")
        sys.exit(1)
    if after is not None:
        console.print(f"With {escape(mode_label(old))} deleted first:")
        for line in after.lines():
            console.print(escape(line))
    console.print(f"You can:\n  1. free space on the disks above yourself and run again: {escape(again)}\n"
                  f"  2. remove {escape(mode_label(old))} first: every object below is deleted for good. Ditto is "
                  f"unavailable until {escape(mode_label(new))} works; the models stay.")
    _runtime_objects_table(f"Every object of {mode_label(old)}: {len(plan.items)}, {size_text(plan.total_bytes)}",
                           plan)
    if not _interactive():
        console.print("[yellow]Nothing was deleted and nothing was built: only you decide this, at a terminal. Run "
                      f"there: {escape(again)}[/yellow]")
        sys.exit(1)
    if not click.confirm(f"Delete all {len(plan.items)} objects of {mode_label(old)} for good first? Ditto is "
                         f"unavailable until {mode_label(new)} works; the models stay", default=False):
        console.print(f"Nothing was deleted and nothing was built. Free space and run again: {escape(again)}")
        sys.exit(1)
    try:
        removal = remove_leftovers(rt, plan, actor="cli", audit=_device_audit(ctx, _state_dir(ctx)),
                                   before_switch=True, removes=old)
    except (LeftoversChanged, RuntimeModeError, PermissionError) as e:
        console.print(f"[yellow]{escape(str(e))}[/yellow]")
        sys.exit(1)
    if removal.stopped:
        console.print("Ditto's server was stopped first.")
    _runtime_removed(removal)
    try:
        return run(new, True)
    except SpaceShortage as e:
        console.print(f"[red]Even without {escape(mode_label(old))}, {escape(mode_label(new))} does not fit:[/red]")
        for line in e.estimate.lines():
            console.print(escape(line))
        failure = f"free space and run again: {again}"
    except (RuntimeModeError, LocalRuntimeError) as e:
        failure = str(e)
    console.print(f"[red]{escape(mode_label(new))} could not be made: {escape(failure)}[/red]")
    console.print(f"Ditto has no local runtime now: {escape(mode_label(old))} was deleted before. The models are still "
                  "there.")
    if click.confirm(f"Make {mode_label(old)} again now?", default=False):
        try:
            run(old, True)
        except (RuntimeModeError, LocalRuntimeError) as e:
            _runtime_failure(e)
        console.print(f"Ditto runs {escape(mode_label(old))} again.")
    else:
        console.print(f"Later: llm-cli ditto runtime ensure --mode {escape(old)}")
    sys.exit(1)


def _runtime_leftovers(ctx: FrameworkContext, rt, plan) -> None:
    """W2: the new mode works, so the old mode goes completely: the full list (name, full path or ID, file name, size,
    what each is), then exactly that list is deleted for good after your one yes at a terminal (default no; no choice
    per item, no trash). Without a terminal nothing is removed."""
    from orchestrator.Core.knowledge_snapshots import size_text
    from orchestrator.runtime.runtime_mode import LeftoversChanged, RuntimeModeError, remove_leftovers
    _runtime_objects_table(f"Every object of the old mode: {len(plan.items)}, {size_text(plan.total_bytes)}", plan)
    console.print("The new mode works. Everything on this list is deleted for good (not into the trash), so the old "
                  "mode is gone completely; the models stay.")
    if not _interactive():
        console.print("[yellow]Nothing was removed: run 'llm-cli ditto runtime ensure --mode "
                      f"{escape(rt.mode())} --force' yourself in a terminal to confirm this list.[/yellow]")
        return
    if not click.confirm(f"Delete exactly these {len(plan.items)} objects for good?", default=False):
        console.print("Nothing was removed; they stay until you remove them.")
        return
    try:
        removal = remove_leftovers(rt, plan, actor="cli", audit=_device_audit(ctx, _state_dir(ctx)))
    except (LeftoversChanged, RuntimeModeError, PermissionError) as e:
        console.print(f"[yellow]{escape(str(e))}[/yellow]")
        return
    _runtime_removed(removal)


def _offer_ditto_model(rt, yes: bool) -> None:
    """The model of the chosen or recommended tier: nothing when it is there; otherwise its source, size and licence
    first, then the download after your yes (with -y only under a permissive licence; any other licence is yours to
    accept with 'llm-cli ditto models download')."""
    from orchestrator.runtime.llama_server import LocalRuntimeError
    from orchestrator.runtime.runtime_mode import model_offer
    offer = model_offer(rt)   # the same offer as the GUI's after its install
    model, why = offer.model, offer.why
    if not offer.needed:
        console.print(f"Model: {escape(model.file)} is there; nothing to download (the models folder is shared by "
                      "both modes).")
        return
    console.print(f"Model for Ditto: {escape(model.repo)} / {escape(model.file)}, {_gib(model.size)}, licence "
                  f"{escape(model.license)} ({escape(why)}).")
    if not model.pinned:
        console.print("[red]It has no pinned size and sha256, so it is not downloaded.[/red]")
        return
    if yes and offer.permissive:
        console.print(f"Downloading it into {escape(str(rt.models_dir))} (checked by size and sha256).")
    elif not _interactive():
        console.print(f"Not downloaded without a terminal. Download it yourself: llm-cli ditto models download "
                      f"{escape(model.tier)}")
        return
    elif not click.confirm(f"Download {_gib(model.size)} into {rt.models_dir}? The licence ({model.license}) is yours "
                           "to accept.", default=True):
        return
    bar, update = _download_progress()
    try:
        with bar:
            path = rt.download_model(model.tier, progress=update)
    except LocalRuntimeError as e:
        _runtime_failure(e)
    console.print(f"[green]Verified and saved: {escape(str(path))}[/green]")


@ditto_runtime.command('import')
@click.argument('file', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option('--yes', '-y', is_flag=True, help='Do not ask before running the imported programs for the check')
@pass_context
def runtime_import(ctx: FrameworkContext, file: Path, yes: bool):
    """Import a llama-server build made on another machine of the same kind (checked against the pin, then verified)"""
    from orchestrator.runtime.llama_server import LocalRuntimeError
    rt = _local_runtime(ctx)

    def confirm(info: dict) -> bool:
        built_on = info.get("built_on") or {}
        console.print(f"Build {escape(str(info.get('build_id', ''))[:16])} of llama.cpp {escape(str(info.get('tag')))} "
                      f"(commit {escape(str(info.get('commit', ''))[:12])}), built on "
                      f"{escape(str(built_on.get('os')))}/{escape(str(built_on.get('arch')))} at "
                      f"{escape(str(info.get('built_at')))}; programs: {escape(', '.join(info.get('files') or []))}")
        return yes or click.confirm("Its files match its manifest and the pin. Run its programs here to verify it? "
                                    "Import only builds you made yourself.", default=False)

    try:
        with console.status("Checking the build ..."):
            inst = rt.import_build(file, confirm)
    except LocalRuntimeError as e:
        _runtime_failure(e)
    console.print(f"[green]Imported and verified:[/green] {_installed_line(inst)}")


@ditto_runtime.command('export')
@click.argument('file', type=click.Path(dir_okay=False, path_type=Path))
@pass_context
def runtime_export(ctx: FrameworkContext, file: Path):
    """Write the self-built build in use to FILE (.tar.gz) for another machine of the same kind"""
    from orchestrator.runtime.llama_server import LocalRuntimeError
    if not file.name.endswith((".tar.gz", ".tgz")):
        console.print("[red]FILE must end with .tar.gz[/red]")
        sys.exit(2)
    try:
        path = _local_runtime(ctx).export_build(file)
    except LocalRuntimeError as e:
        _runtime_failure(e)
    console.print(f"[green]Wrote {escape(str(path))}[/green]. On the other machine: llm-cli ditto runtime import "
                  f"{escape(path.name)}")


@ditto_runtime.command('prune')
@pass_context
def runtime_prune(ctx: FrameworkContext):
    """List old runtime builds and delete the ones you pick, after you confirm them in a terminal (nothing preselected;
    never the active build, the CPU fallback or the running one)"""
    from orchestrator.Core.deletion import PlanChanged, size_text
    rt = _local_runtime(ctx)
    plan = rt.cleanup_plan()
    if plan is None:
        console.print("No old runtime builds: the active build, the CPU fallback and the running one are never "
                      "listed.")
        return
    accepted = confirm_deletion(plan, title=f"Old runtime builds ({len(plan.items)}, {size_text(plan.total_bytes)})",
                                multiple=True, again="llm-cli ditto runtime prune")
    if accepted is None:
        return
    trash, audit = _person_trash(ctx, Path(rt.data_dir))
    try:
        result = rt.delete_old_builds(accepted, trash=trash, audit=audit)
    except (PlanChanged, PermissionError, RuntimeError) as e:
        console.print(f"[yellow]{escape(str(e))}[/yellow]")
        return
    _print_deletion_result(result, accepted)


@ditto_runtime.command('start')
@click.option('--model', 'tier', default=None, help="Model tier (llm-cli ditto models list); remembered for later "
              "starts. 'auto': the recommendation for this machine")
@click.option('--ctx', 'ctx_len', type=click.IntRange(256, 1048576), default=None,
              help='Context length in tokens (default: from free memory, at most 32768)')
@click.option('--sleep-idle', type=click.IntRange(0, None), default=None,
              help='Unload the model after this many idle seconds (0 = never)')
@pass_context
def runtime_start(ctx: FrameworkContext, tier: Optional[str], ctx_len: Optional[int], sleep_idle: Optional[int]):
    """Start llama-server on 127.0.0.1 (free port, per-start API key in a private file)"""
    from orchestrator.runtime.llama_server import LocalRuntimeError
    rt = _local_runtime(ctx)
    try:
        with console.status("Starting llama-server ..."):
            ep = rt.start(tier=tier, ctx=ctx_len, sleep_idle_seconds=sleep_idle)
    except LocalRuntimeError as e:
        _runtime_failure(e)
    status = rt.status()
    where = f"container {status.container}" if status.container else f"pid {ep.pid}"
    console.print(f"[green]Running:[/green] {escape(ep.base_url)} model {escape(ep.model)} ({escape(where)}, "
                  f"ctx {status.ctx})")
    console.print("Ditto uses it as the 'local' AI provider; the API key changes with every start and is readable "
                  "only by you.")


@ditto_runtime.command('stop')
@pass_context
def runtime_stop(ctx: FrameworkContext):
    """Stop the llama-server started by the framework (and leftovers of a crash)"""
    from orchestrator.runtime.llama_server import LocalRuntimeError
    try:
        stopped = _local_runtime(ctx).stop()
    except LocalRuntimeError as e:
        _runtime_failure(e)
    console.print(f"Stopped ({stopped} process{'es' if stopped > 1 else ''})." if stopped else "Not running.")


def _pinned_llama_source():
    """The llama.cpp pin of configs/llama_runtime.yml (exits with 1 when the catalog has none)."""
    from orchestrator.runtime.build.source import SourceError, catalog_pin
    try:
        return catalog_pin()
    except SourceError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)


@ditto_runtime.command('export-source')
@click.argument('file', type=click.Path(dir_okay=False, path_type=Path))
@click.option('--mirror', default=None, metavar='URL',
              help='Fetch from this mirror instead of the pinned repository (the commit is verified the same way)')
@click.option('--yes', '-y', is_flag=True, help='Fetch without asking when the source is not cached yet')
@pass_context
def runtime_export_source(ctx: FrameworkContext, file: Path, mirror: Optional[str], yes: bool):
    """Write the pinned llama.cpp source as a git bundle (FILE, plus FILE.json) for an air-gapped machine"""
    from orchestrator.runtime.build.source import SourceError, cached_source, export_source
    source = _pinned_llama_source()
    state_dir = _settings(ctx)[0].user_state_dir
    try:
        if cached_source(source, state_dir) is None and not yes and not click.confirm(
                f"The pinned llama.cpp source ({source.tag}, commit {source.commit[:12]}, about 215 MB) is not "
                "cached yet. Fetch it now?", default=True):
            return
        with console.status("Preparing the llama.cpp source ..."):
            manifest = export_source(source, state_dir, file, url=mirror,
                                     log=lambda msg: console.print(escape(msg)))
    except SourceError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    console.print(f"[green]Wrote {escape(str(file))}[/green] ({_gib(int(manifest['bundle_size']))}, llama.cpp "
                  f"{escape(source.tag)}, commit {escape(source.commit[:12])}) and {escape(file.name)}.json.")
    console.print("On the air-gapped machine: llm-cli ditto runtime import-source " + escape(file.name))


@ditto_runtime.command('import-source')
@click.argument('file', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@pass_context
def runtime_import_source(ctx: FrameworkContext, file: Path):
    """Import the pinned llama.cpp source from a bundle of export-source (checked by its commit)"""
    from orchestrator.runtime.build.source import SourceError, import_source
    source = _pinned_llama_source()
    try:
        with console.status("Importing and verifying the llama.cpp source ..."):
            path = import_source(source, _settings(ctx)[0].user_state_dir, file,
                                 log=lambda msg: console.print(escape(msg)))
    except SourceError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    console.print(f"[green]Verified llama.cpp {escape(source.tag)} (commit {escape(source.commit[:12])})[/green] "
                  f"in {escape(str(path))}")


@ditto.group('models')
def ditto_models():
    """Models for Ditto's local mode"""
    pass


@ditto_models.command('list')
@pass_context
def models_list(ctx: FrameworkContext):
    """Pinned models, their size and whether they are downloaded"""
    from orchestrator.runtime.ditto_models import models_overview
    view = models_overview(_local_runtime(ctx))  # the GUI's Ditto > Local Models... shows the same
    table = Table(title="Ditto models")
    for column in ("Tier", "File", "Size", "License", "Status", "Note"):
        table.add_column(column)
    labels = {"ready": "[green]downloaded[/green]", "unverified": "[yellow]not verified yet[/yellow]", "missing": "-"}
    for row in view.rows:
        m = row.model
        notes = ["[red]not pinned[/red]" if note == "not pinned" else note for note in row.notes()]
        table.add_row(escape(m.tier), escape(m.file), _gib(m.size), escape(m.license), labels[row.state],
                      ", ".join(notes))
    console.print(table)
    console.print(f"Recommended for this machine: {escape(view.why)}")


@ditto_models.command('download')
@click.argument('tier', required=False)
@click.option('--yes', '-y', is_flag=True, help='Do not ask before downloading')
@pass_context
def models_download(ctx: FrameworkContext, tier: Optional[str], yes: bool):
    """Download a model at its pinned revision and check size and sha256 (default: the one `runtime start` loads)"""
    from orchestrator.runtime.ditto_models import model_to_download
    from orchestrator.runtime.llama_server import LocalRuntimeError
    rt = _local_runtime(ctx)
    try:
        model, why = model_to_download(rt, tier)  # the GUI's Ditto > Local Models... asks for the same model
    except KeyError as e:
        console.print(f"[red]{escape(str(e.args[0]))}[/red]")
        sys.exit(2)
    if why is not None:
        console.print(f"Recommended: {escape(why)}")
    elif tier is None:   # the tier chosen earlier: the model a plain `runtime start` loads
        console.print(f"Chosen earlier with 'runtime start --model': {escape(model.tier)}")
    if rt.model_ready(model):
        console.print(f"[green]{escape(model.file)} is already downloaded.[/green]")
        return
    console.print(f"{escape(model.repo)} / {escape(model.file)} ({escape(model.license)})")
    if not yes and not click.confirm(f"Download {_gib(model.size)} into {rt.models_dir}?", default=True):
        return
    bar, update = _download_progress()
    try:
        with bar:
            path = rt.download_model(model.tier, progress=update)
    except LocalRuntimeError as e:
        _runtime_failure(e)
    console.print(f"[green]Verified and saved: {escape(str(path))}[/green]")


@ditto.command('setup')
@pass_context
def ditto_setup(ctx: FrameworkContext):
    """First-time setup: tool permissions (asked once, used by Ditto and the MCP server)"""
    registry = _tool_registry(ctx)
    console.print(Panel(
        "Ditto and connected AI clients (MCP) use the framework through tools.\n"
        "Decide once what they may do on their own, what needs your confirmation, and what is never allowed.\n"
        "Tools that reach other devices or delete data always ask (you can deny them, never allow them unasked).",
        title="Ditto setup - tool permissions"))
    policy = _save_policy(registry, _ask_tool_policy(registry, registry.policy()))
    console.print(f"[green]Permissions saved ({registry.policy_store.path}).[/green]")
    console.print("Change them later with: llm-cli tools permissions")
    return policy


@ditto.command('ask')
@click.argument('question')
@click.option('--no-tools', is_flag=True, help='Answer without using the framework tools')
@click.option('--provider', help='AI provider for this question (e.g. anthropic, openai, ollama); default: AI settings')
@click.option('--model', help='Model for this question; default: AI settings')
@pass_context
def ditto_ask(ctx: FrameworkContext, question: str, no_tools: bool, provider: Optional[str], model: Optional[str]):
    """Ask Ditto one question. Tools run within your tool permissions; "ask" ones need your confirmation
    (denied without a terminal). The answer goes to stdout, everything else to stderr."""
    from orchestrator.Core.ditto_manager import DittoError
    ctx.require_framework()
    ditto = _ai_override(ctx, provider, model)
    err = Console(stderr=True)

    from orchestrator.Core.ditto_manager import tool_note

    def tool_used(use):   # a made-up tool's name and a handler's error are outside text
        line = escape(_printable(tool_note(use)))
        err.print(f"[dim]{line}[/dim]" if use.ok else f"[yellow]{line}[/yellow]")

    try:
        reply = ditto.chat(question, confirm=_ditto_confirm, use_tools=not no_tools, on_tool=tool_used)
    except DittoError as e:   # may quote the provider's or a tool's text
        err.print(f"[red]{escape(_printable(e))}[/red]")
        sys.exit(1)
    click.echo(_printable(reply.text))


@ditto.command('eval')
@click.option('--cases', 'cases_file', type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help='Cases file (default: evals/ditto/cases.yml of the installation, for --kind retrieval '
                   'evals/retrieval/cases.yml)')
@click.option('--provider', help='AI provider for the run; default: AI settings')
@click.option('--model', help='Model for the run; default: AI settings')
@click.option('--tier', 'tiers', multiple=True,
              help='Run against the managed local model of this tier (repeatable, e.g. --tier small --tier small-q2)')
@click.option('--kind', 'kinds', multiple=True,
              type=click.Choice(["healing", "optimizer", "module", "chat", "retrieval"]),
              help='Only cases of this kind (repeatable). retrieval grades knowledge-base searches on a fixture '
                   'corpus with the local embedding model (no AI model) and runs only when chosen')
@click.option('--rag-variant', 'rag_variants', multiple=True, type=click.Choice(["fused", "rerank"]),
              help='Retrieval only: search variant to grade (repeatable): fused (hybrid search) or rerank (plus '
                   'the cross-encoder of rag_reranker and its relevance floor); default: fused')
@click.option('--case', 'case_ids', multiple=True, help='Only this case id (repeatable)')
@click.option('--repeat', type=click.IntRange(1, 20), default=1, show_default=True, help='Runs per case')
@click.option('--json', 'json_out', type=click.Path(dir_okay=False, path_type=Path), default=None,
              help='Write the full report as JSON to this file')
@click.option('--min-pass', type=click.FloatRange(0, 1), default=None,
              help='Exit code 1 if a run passes fewer cases than this share (e.g. 0.8)')
@click.option('--effort', type=click.Choice(["low", "medium", "high"]), default=None,
              help='Reasoning effort for the run (local models: low = no thinking); default: AI settings')
@pass_context
def ditto_eval(ctx: FrameworkContext, cases_file: Optional[Path], provider: Optional[str], model: Optional[str],
               tiers: Tuple[str, ...], kinds: Tuple[str, ...], rag_variants: Tuple[str, ...],
               case_ids: Tuple[str, ...], repeat: int, json_out: Optional[Path], min_pass: Optional[float],
               effort: Optional[str]):
    """Grade Ditto on fixed cases with the chosen provider/model, e.g. to check whether a 2-bit local model is
    good enough, or the knowledge-base search (--kind retrieval, works offline once the embedding model is
    downloaded). Nothing is built or changed. Cloud providers bill every request (about 20 per run and repeat)."""
    from orchestrator.Core.ai_override import CLI_SOURCE, override_values
    from orchestrator.Core.ditto_manager import DittoError
    from orchestrator.evals.eval_run import (EvalEvents, EvalRunError, check_choice, retrieval_table, run_evaluation,
                                             select_cases, summary_table, write_report)
    from orchestrator.runtime.llama_server import LocalRuntimeError
    ctx.require_framework()
    # The same run as the GUI's Ditto > Evaluation... (evals/eval_run.py); this command prints it.
    try:
        check_choice(tiers, provider, model)
        cases = select_cases(cases_file, kinds, case_ids)
    except EvalRunError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(e.exit_code)
    fm = ctx.framework_manager

    class Printed(EvalEvents):
        def busy(self, step, **values):
            text = {"start_tier": f"Starting llama-server with {values.get('tier')} ...",
                    "index": f"Indexing {values.get('documents')} corpus documents into a temporary knowledge base "
                             "(the embedding model is downloaded on first use) ...",
                    "reranker": f"Loading the reranker {values.get('model')} (downloaded on first use) ..."}[step]
            return console.status(text)

        def run_started(self, label, name, run_model, count, times):
            what = f"{escape(name)} / {escape(run_model)}" if name is not None else escape(run_model)
            console.print(f"[bold]{escape(label)}[/bold]: {what}, {count} cases x {times}")

        def result(self, result):
            mark = "[green]pass[/green]" if result.pass_rate == 1 else (
                "[red]fail[/red]" if result.pass_rate == 0 else f"[yellow]{result.pass_rate:.0%}[/yellow]")
            note = f"  [dim]{escape(result.first_problem()[:160])}[/dim]" if result.pass_rate < 1 else ""
            console.print(f"  {mark} {escape(result.case.id)} ({result.seconds:.1f}s){note}")

        def server_replaced(self, served):
            console.print(f"[yellow]Your llama-server ({escape(served)}) was replaced for the evaluation; "
                          "start it again with: llm-cli ditto runtime start[/yellow]")

        def unused_options(self):
            console.print("[dim]--tier, --provider, --model and --effort apply to AI cases; retrieval needs none.[/dim]")

    def make_ditto():
        ditto = _ai_override(ctx, "local" if tiers else provider, model)
        for key, value in override_values(effort=effort).items():
            fm.config.set(key, value, source_type=CLI_SOURCE)  # this run only: never saved
        return ditto

    try:
        runs = run_evaluation(fm, cases, make_ditto=make_ditto, make_runtime=lambda: _local_runtime(ctx), tiers=tiers,
                              provider=provider, model=model, effort=effort, cases_file=cases_file,
                              rag_variants=rag_variants, repeat=repeat, events=Printed())
    except EvalRunError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(e.exit_code)
    except LocalRuntimeError as e:
        _runtime_failure(e)
    except DittoError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)

    columns, rows = summary_table(runs, sorted({c.kind for c in cases}))
    table = Table(title="Ditto evaluation")
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(escape(row[0]), escape(row[1]), *row[2:])
    console.print(table)
    retrieval = retrieval_table(runs)
    if retrieval is not None:
        columns, rows = retrieval
        table = Table(title="Retrieval")
        for column in columns:
            table.add_column(column)
        for row in rows:
            table.add_row(escape(row[0]), *row[1:])
        console.print(table)
    if json_out:
        write_report(json_out, runs)
        console.print(f"Report written to {escape(str(json_out))}")
    if min_pass is not None and any(r.pass_rate < min_pass for r in runs):
        sys.exit(1)


# ============================================================================
# SIGN-IN (ditto login / logout / auth)
# ============================================================================

EXIT_USAGE, EXIT_CANCELLED = 2, 1
# Storage problems a different protection solves (a damaged vault or one in a git work tree needs another fix).
STORAGE_CHOICE_CODES = ("storage.keyring_unavailable", "storage.passphrase_needed", "storage.helper_failed",
                        "storage.unavailable")
FIELD_OPTIONS = (("workspace_id", "--workspace-id"), ("organization", "--organization"), ("project", "--project"))


def _interactive() -> bool:
    """A person at a terminal: only then is anything asked."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _say(text: str, *, err: bool = False, fg: Optional[str] = None) -> None:
    """Plain lines (URLs and commands are never wrapped); colors only on a terminal."""
    click.secho(text, err=err, fg=fg)


def _say_error(error: BaseException, *, fg: Optional[str] = "red") -> None:
    """An error for the command line (wave 6, design A.7): its message, then one "Next:" line per next step it carries
    (orchestrator/utils/next_steps rendered for the CLI: they name the command, as the CLI always did) and per
    AuthAction. A ToolError prints its message and its steps, or its own next step when it has no steps. Domain chains
    switch their handlers to it when their messages stop naming commands."""
    from orchestrator.utils.next_steps import render, steps_of
    steps = steps_of(error)
    if hasattr(error, "fill_steps"):   # a ToolError: its `next` is written for an AI
        _say(error.message if steps or not error.next else f"{error.message} Next: {error.next}", err=True, fg=fg)
    else:
        _say(str(error), err=True, fg=fg)
    for line in render(steps, "cli", "en", channel="cli").splitlines():
        _say(line, err=True)
    for action in getattr(error, "actions", ()) or ():
        target = action.command or action.url
        _say(f"Next: {action.label}" + (f": {target}" if target else ""), err=True)


def _usage(message: str):
    _say(message, err=True, fg="red")
    sys.exit(EXIT_USAGE)


def _auth_fail(error: BaseException, code: Optional[int] = None, service=None):
    """The error, its "Next:" lines, and the exit code of its kind (3 rejected, 4 network, 5 storage, 6 policy)."""
    from orchestrator.llm.auth.signin import exit_code
    if (getattr(error, "code", None) or "").startswith("storage."):
        _storage_fail(error, service=service)
    _say_error(error)
    sys.exit(code if code is not None else exit_code(error))


def _storage_fail(error: BaseException, provider: Optional[str] = None, service=None):
    """A vault that cannot be used (exit code 5), with the protections to choose from; nothing is chosen here."""
    own = service.storage_message() if service is not None else None  # the vault's full text, not a summary
    _say(own or str(error), err=True, fg="red")
    if getattr(error, "code", None) in STORAGE_CHOICE_CODES:
        _say("Choose how to protect your credentials (nothing is chosen for you):", err=True)
        for line in ("llm-cli ditto auth storage --use passphrase    a passphrase asked once per start",
                     'llm-cli ditto auth storage --use helper --helper "pass show llmcc/vault-key"',
                     f"llm-cli ditto login {provider or '<provider>'} --insecure-storage    a key file next to the "
                     "vault, NOT encrypted at rest",
                     "LLM_SECRET_STORAGE=ephemeral    memory only, for CI (keys from environment variables)"):
            _say(f"  {line}", err=True)
    sys.exit(5)


def _auth_service(ctx: FrameworkContext):
    """Settings, credential vault and provider layer for the sign-in commands. The framework is not booted: a
    missing keyring is reported with the choices (exit code 5) instead of stopping everything."""
    from orchestrator.llm.auth.signin import SignIn
    fm = ctx.framework_manager
    if fm is not None and getattr(fm, "secrets_manager", None) is not None:
        config, secrets = fm.config, fm.secrets_manager
    else:
        from types import SimpleNamespace
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.secrets_manager import SecretsManager
        config = ConfigManager(ctx.config_path)
        secrets = SecretsManager(SimpleNamespace(config=config))
        secrets.initialize()  # a failure stays in secrets.storage_error and is reported where a secret is needed
    try:
        return SignIn(config, secrets, http_client=ctx.http_client)
    except (ValueError, OSError) as e:  # a provider catalog in the config folder that cannot be read
        _say(f"The provider catalog cannot be read: {e}", err=True, fg="red")
        sys.exit(1)


def _auth_catalog(ctx: FrameworkContext):
    """The provider catalog alone (no vault, no keyring)."""
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.llm import load_catalog
    try:
        return load_catalog(user_dir=ConfigManager(ctx.config_path).config_dir)
    except (ValueError, OSError) as e:
        _say(f"The provider catalog cannot be read: {e}", err=True, fg="red")
        sys.exit(1)


def _auth_preset(catalog, name: str):
    from orchestrator.utils.helpers import redact_secrets
    preset = catalog.get(name)
    if preset is not None:
        return preset
    removed = catalog.removed_message(name)
    if removed:
        _say(removed, err=True, fg="red")
        sys.exit(6)
    _usage(f"Unknown AI provider '{redact_secrets(str(name))}'. Choose one of: {', '.join(catalog.ids())}.")


def _method_tags(info) -> str:
    tags = []
    if info.recommended:
        tags.append("recommended")
    if info.spec.opt_in:
        tags.append("advanced, only when chosen")
    if not info.built:
        tags.append("not available in this version")
    return f" ({', '.join(tags)})" if tags else ""


def _named_method(service, preset, method_id: str):
    """--method: a method of the provider, else a usage error (the value redacted like a provider name)."""
    from orchestrator.utils.helpers import redact_secrets
    spec = service.method(preset, method_id)
    if spec is None:
        _usage(f"{preset.label} has no sign-in method '{redact_secrets(str(method_id))}'. Methods: "
               f"{', '.join(preset.auth)}.")
    return spec


def _login_method(service, preset, method_id: Optional[str]):
    """--method, the only method, or (at a terminal) the user's pick. Opt-in methods count only when named."""
    if method_id:
        return _named_method(service, preset, method_id)
    if len(preset.methods) == 1:
        return preset.methods[0]
    candidates = [m for m in preset.methods if not m.opt_in]
    if not _interactive():
        if len(candidates) == 1:
            return candidates[0]
        _usage(f"{preset.label} offers several sign-in methods; choose one with --method: "
               f"{', '.join(m.id for m in candidates)}.")
    infos = service.methods(preset)
    _say(f"Sign-in methods of {preset.label}:", err=True)
    for number, info in enumerate(infos, 1):
        _say(f"  {number}) {info.spec.label} [{info.spec.id}]{_method_tags(info)}", err=True)
    default = next((n for n, info in enumerate(infos, 1) if info.recommended), 1)
    choice = click.prompt("Method", type=click.IntRange(1, len(infos)), default=default, err=True)
    return infos[choice - 1].spec


def _ensure_storage(service, preset, insecure: bool, yes: bool) -> None:
    """The vault must be usable before a key is asked for. --insecure-storage is the user's explicit choice of the
    key file when no keyring works; it never replaces a protection that works."""
    from orchestrator.Core.vault_key import StorageError
    problem = service.storage_problem(preset.id)
    if problem is None:
        mode = getattr(service.secrets, "storage_mode", None)
        if insecure and mode != "insecure":
            _say(f"The credential vault is protected with '{mode}', so --insecure-storage is ignored (to switch: "
                 "llm-cli ditto auth storage --use insecure).", err=True, fg="yellow")
        return
    if not insecure or problem.code not in STORAGE_CHOICE_CODES:
        _storage_fail(problem, preset.id, service)
    configured = str(service.config.get("secret_storage", "keyring") or "keyring").strip().lower()
    if configured != "keyring":
        # A helper or passphrase the user chose is never downgraded by a flag (a script may always pass it).
        _say(service.storage_message() or str(problem), err=True, fg="red")
        _say(f"--insecure-storage only replaces a keyring that cannot be used; the vault is protected with "
             f"'{configured}' (secret_storage). Fix that, or switch on purpose: llm-cli ditto auth storage --use "
             "insecure", err=True)
        sys.exit(5)
    warning = "The vault key will be kept in a file next to the vault, NOT encrypted at rest."
    if not yes and _interactive() and not click.confirm(f"{warning} Continue?", default=False, err=True):
        _say("Cancelled.", err=True)
        sys.exit(EXIT_CANCELLED)
    try:
        service.choose_storage("insecure")
    except StorageError as e:
        _storage_fail(e, preset.id)
    _say(f"{warning} (secret_storage: insecure)", err=True, fg="yellow")


def _read_key(spec, preset, *, with_token: bool, from_env: bool, service) -> Tuple[str, str]:
    """The key and where it came from: stdin, the provider's environment variable or a hidden prompt. Never argv."""
    from orchestrator.llm.auth.store import malformed_key
    optional = spec.type == "bearer_optional"
    if with_token:
        key = sys.stdin.read().strip()
        if not key:  # an unset variable in a script: never taken as "no key", which would delete a stored one
            _usage("No key on stdin. Example: printf %s \"$KEY\" | llm-cli ditto login "
                   f"{preset.id} --with-token" + (f". To use it without a key: llm-cli ditto logout {preset.id}"
                                                  if optional else ""))
        if malformed_key(key):
            _usage("The key on stdin has a space, a line break or another character that is never part of a key; "
                   "pipe only the key.")
        return key, "stdin"
    if from_env:
        from orchestrator.llm.auth.signin import EnvKeyError
        try:
            key, name = service.env_key(preset, spec)
        except EnvKeyError as e:
            if e.code == "no_variable":
                _usage(f"{preset.label} ({spec.label}) reads no environment variable; use --with-token or the "
                       "prompt.")
            if e.code == "not_set":
                _usage(f"{' and '.join(spec.env)} {'is' if len(spec.env) == 1 else 'are'} not set.")
            _usage(f"{e.name} has a space, a line break or another character that is never part of a key.")
        return key, f"env:{name}"
    if not _interactive():
        _usage("No terminal to ask for the key: pipe it to --with-token (printf %s \"$KEY\" | llm-cli ditto login "
               f"{preset.id} --with-token) or use --from-env.")
    if spec.where:
        _say(f"Where to find it: {spec.where}", err=True)
    elif spec.key_page:
        _say(f"Create a key at {spec.key_page}", err=True)
    if optional:
        key = click.prompt("API token (Enter for none)", hide_input=True, default="", show_default=False, err=True)
    else:
        key = click.prompt("API key", hide_input=True, err=True)
    if malformed_key(key.strip()):
        _usage("The key has a space or another character that is never part of a key; paste only the key.")
    return key.strip(), "prompt"


def _check_or_fail(service, preset, spec, base_url: Optional[str], **typed):
    """The free check (`base_url`: the address given on the command line, None for the configured one)."""
    with Console(stderr=True).status(f"Checking with {preset.label} ..."):
        result = service.check(preset, spec, base_url, **typed)
    if not result.ok:
        _auth_fail(result.error or RuntimeError("The credential could not be checked."))
    return result


def _summary(head: str, result, *parts: Optional[str]) -> str:
    """"<head>: part, account, N models." with what the check found ("(not checked)" without one)."""
    found = [p for p in parts if p]
    if result is not None:
        found += [p for p in (result.account_label, f"{result.models} models" if result.models is not None else None)
                  if p]
    return head + (f": {', '.join(found)}" if found else "") + (" (not checked)" if result is None else "") + "."


def _primary_hint(service, preset) -> None:
    if not service.client.is_primary(preset):
        _say(f"Ditto's provider is still the one in the AI settings; to use {preset.label}, choose it there or set "
             f"ai_provider: {preset.id}.", err=True)


@ditto.command('login')
@click.argument('provider')
@click.option('--method', 'method_id', metavar='ID',
              help='Sign-in method (see llm-cli ditto auth methods PROVIDER); needed without a terminal when the '
                   'provider offers several')
@click.option('--with-token', is_flag=True,
              help='Read the key from stdin, e.g. printf %s "$KEY" | llm-cli ditto login openai --with-token')
@click.option('--from-env', is_flag=True,
              help="Copy the key from the provider's environment variable (e.g. ANTHROPIC_API_KEY) into the vault")
@click.option('--base-url', metavar='URL',
              help='Server address for providers whose address you set (Ollama, custom); remembered per provider')
@click.option('--workspace-id', metavar='ID', help='Claude API: workspace ID (wrkspc_...) for keys valid in every '
                                                   'workspace of the organization')
@click.option('--organization', metavar='ID', help='OpenAI: organization ID (org-...)')
@click.option('--project', metavar='ID', help='OpenAI: project ID (proj_...)')
@click.option('--profile', metavar='NAME', help='--method ant_profile: the `ant auth login` profile (default: the '
                                                'active one)')
@click.option('--insecure-storage', is_flag=True,
              help='Without a usable keyring: keep the vault key in a file next to the vault (NOT encrypted at rest)')
@click.option('--no-check', is_flag=True, help='Store without the free check (for example offline)')
@click.option('--yes', '-y', is_flag=True,
              help='Do not ask before sending a key over plain http:// or using --insecure-storage')
@pass_context
def ditto_login(ctx: FrameworkContext, provider: str, method_id: Optional[str], with_token: bool, from_env: bool,
                base_url: Optional[str], workspace_id: Optional[str], organization: Optional[str],
                project: Optional[str], profile: Optional[str], insecure_storage: bool, no_check: bool, yes: bool):
    """Sign Ditto in to PROVIDER (anthropic, openai, google, openrouter, ollama, custom, ...).

    A key is never taken from the command line: it comes from a hidden prompt, from stdin (--with-token) or from
    the provider's environment variable (--from-env). It is checked for free before it is stored in the
    encrypted vault, and the method is remembered (ai_auth_<provider>). Exit codes: 0 signed in, 2 usage,
    3 rejected, 4 network, 5 credential storage unavailable, 6 not allowed or not available."""
    from orchestrator.llm.auth.errors import auth_error
    from orchestrator.llm.auth.signin import exit_code, valid_profile_name
    from orchestrator.llm.auth.spec import KEY_TYPES
    from orchestrator.llm.settings_change import plain_http_to_remote  # the AI-settings rule every channel shares
    from orchestrator.llm.auth.store import display_origin, last4
    from orchestrator.llm.errors import LLMError, NotConfigured, rejected_key_error

    if with_token and from_env:
        _usage("Use either --with-token or --from-env, not both.")
    service = _auth_service(ctx)
    preset = _auth_preset(service.catalog, provider)
    spec = _login_method(service, preset, method_id)
    if not service.built(preset, spec):
        _auth_fail(auth_error("auth.not_built", provider=preset.id, provider_label=preset.label, method=spec.id))
    fields = {fid: value for fid, value in (("workspace_id", workspace_id), ("organization", organization),
                                            ("project", project)) if value is not None}
    for fid, option in FIELD_OPTIONS:
        if fid in fields and spec.field(fid) is None:
            _usage(f"{option} does not apply to {preset.label} ({spec.label}).")
    try:
        service.store.check_fields(preset, spec, fields)  # a malformed option value is a usage error
    except LLMError as e:
        _usage(str(e))
    keyed = spec.type in KEY_TYPES and not preset.runtime_endpoint
    if (with_token or from_env) and not keyed:
        _usage(f"--with-token and --from-env take a key; {preset.label} ({spec.label}) needs none.")
    if profile is not None and spec.type != "cli_session":
        _usage("--profile belongs to --method ant_profile.")
    try:
        url = service.endpoint(preset, base_url)
    except ValueError as e:
        _usage(str(e))
    except NotConfigured as e:  # the managed local server is not running
        _auth_fail(e, 4)
    if not url and not preset.runtime_endpoint:
        _usage(f"{preset.label} has no server address yet: add --base-url URL.")

    if preset.runtime_endpoint:
        _say(f"{preset.label} needs no sign-in: the framework passes the server's per-start key.")
        if not no_check:
            result = _check_or_fail(service, preset, spec, base_url)
            _say(_summary(f"It answers at {display_origin(url)}", result), fg="green")
        return

    if spec.type == "cli_session":
        profile = (profile or "").strip() or None
        if profile is not None and not valid_profile_name(profile):
            _usage("A profile name has letters, digits, '.', '_' or '-' (at most 64).")
        if spec.note:
            _say(spec.note, err=True, fg="yellow")
        result = None
        if not no_check:
            with Console(stderr=True).status("Checking the profile through the Anthropic SDK ..."):
                result = service.check(preset, spec, profile=profile)
            if not result.ok:
                _say(str(result.error), err=True, fg="red")
                login = " ".join(spec.cli.login) if spec.cli and spec.cli.login else "ant auth login"
                if spec.cli and spec.cli.install and shutil.which("ant") is None:
                    _say(f"Next: install the ant CLI: {spec.cli.install}", err=True)
                _say(f"Next: sign in with the ant CLI: {login}" + (f" --profile {profile}" if profile else ""),
                     err=True)
                sys.exit(exit_code(result.error) if result.error else 3)
        service.use_method(preset, spec, profile=profile)
        which = f"profile '{profile}'" if profile else "active profile"
        _say(_summary(f"Ditto uses the ant CLI {which} for {preset.label}", result), fg="green")
        _say(f"Back to the API key: llm-cli ditto logout {preset.id} --method {spec.id}", err=True)
        _primary_hint(service, preset)
        return

    if spec.type == "none":
        result = None if no_check else _check_or_fail(service, preset, spec, base_url)
        service.use_method(preset, spec, base_url, remember_url=bool(base_url))
        _say(_summary(f"{preset.label} at {display_origin(url)}", result, "no sign-in needed"), fg="green")
        _primary_hint(service, preset)
        return

    # A key method (api_key, bearer_optional).
    if spec.type == "api_key":
        _ensure_storage(service, preset, insecure_storage, yes)
    key, source = _read_key(spec, preset, with_token=with_token, from_env=from_env, service=service)
    if key and spec.rejected(key):
        _auth_fail(rejected_key_error(preset))  # before any request
    if key and spec.type == "bearer_optional":
        _ensure_storage(service, preset, insecure_storage, yes)
    if key and plain_http_to_remote(url) and not yes:
        warning = f"{display_origin(url)} uses plain http://, so the key travels unencrypted to another computer."
        if not _interactive():
            _usage(f"{warning} Add --yes to send it anyway.")
        if not click.confirm(f"{warning} Continue?", default=False, err=True):
            _say("Cancelled.", err=True)
            sys.exit(EXIT_CANCELLED)
    result = None if no_check else _check_or_fail(service, preset, spec, base_url, key=key, fields=fields)
    try:
        if key:
            saved = service.save_key(preset, spec, key, base_url, fields=fields, source=source,
                                     remember_url=bool(base_url), checked=result)
            warnings = saved.warnings
        else:  # an optional key left empty: no key for this address
            service.sign_out(preset, spec, base_url)
            service.use_method(preset, spec, base_url, remember_url=bool(base_url))
            warnings = []
    except ValueError as e:
        _usage(str(e))
    except LLMError as e:
        _auth_fail(e, service=service)
    if key:
        ending = last4(key)
        _say(_summary(f"Signed in to {preset.label}", result, spec.label + (f" ...{ending}" if ending else "")),
             fg="green")
    else:
        _say(_summary(f"{preset.label} at {display_origin(url)}", result, "used without a key"), fg="green")
    if result is not None and result.ok and result.error is not None:
        _say(str(result.error), err=True, fg="yellow")
    for warning in warnings:
        _say(warning, err=True, fg="yellow")
    if key and getattr(service.secrets, "storage_mode", None) == "ephemeral":
        _say("The key is kept in memory for this run only (secret_storage: ephemeral); nothing was saved.",
             err=True, fg="yellow")
    elif source.startswith("env:"):
        _say(f"The key from {source[4:]} is now stored in the vault; the variable is no longer needed.", err=True)
    _primary_hint(service, preset)


@ditto.command('logout')
@click.argument('provider')
@click.option('--method', 'method_id', metavar='ID', help='Sign-in method to sign out of (default: the one in use)')
@click.option('--base-url', metavar='URL', help='Server address whose key is deleted (providers whose address you '
                                                'set; default: the configured one)')
@pass_context
def ditto_logout(ctx: FrameworkContext, provider: str, method_id: Optional[str], base_url: Optional[str]):
    """Sign Ditto out of PROVIDER. The local copy is deleted first; the command says what it cannot revoke and
    where you revoke the key itself."""
    from orchestrator.llm.auth.store import display_origin
    from orchestrator.llm.errors import LLMError
    service = _auth_service(ctx)
    preset = _auth_preset(service.catalog, provider)
    try:
        url = service.endpoint(preset, base_url) if base_url else None
    except ValueError as e:
        _usage(str(e))
    if method_id:
        spec = _named_method(service, preset, method_id)
    else:
        current = service.status(preset, url)
        spec = service.chosen(preset) or (preset.method(current.method) if current.method else None) \
            or service.recommended(preset) or preset.methods[0]
    try:
        result = service.sign_out(preset, spec, url)
    except ValueError as e:
        _usage(str(e))
    except LLMError as e:
        _auth_fail(e, service=service)
    where = f" at {display_origin(url)}" if url else ""
    if spec.type in ("api_key", "bearer_optional") and result.note is None:
        _say(f"Signed out of {preset.label}: the stored key{where} was deleted from this computer." if result.deleted
             else f"No key was stored for {preset.label}{where}; nothing was deleted.",
             fg="green" if result.deleted else None)
        if result.revoke_url:
            _say(f"The key itself keeps working until you revoke it at {result.revoke_url}")
        if result.env_variable:
            _say(f"{result.env_variable} is still set, so Ditto keeps using the key from it; unset it to stop.",
                 err=True, fg="yellow")
    elif spec.type == "cli_session":
        _say(f"Ditto no longer uses the ant CLI profile for {preset.label}." if result.deleted
             else f"Ditto was not using the ant CLI profile for {preset.label}.", fg="green" if result.deleted else None)
        if result.note:
            _say(result.note, err=True)
    elif spec.type == "none":
        _say(f"{preset.label}: the remembered server address and method were forgotten." if result.deleted
             else f"{preset.label} needs no sign-in; nothing was stored.")
    else:
        _say(result.note or f"Signed out of {preset.label}.")
        if result.revoke_url:
            _say(f"Revoke it at {result.revoke_url}")


@ditto.group('auth')
def ditto_auth():
    """Sign-in state, sign-in methods, credential storage and the vault key"""
    pass


@ditto_auth.command('status')
@click.option('--json', 'as_json', is_flag=True, help='Machine-readable output (never a key: the last four '
                                                      'characters at most)')
@click.option('--check', is_flag=True, help='Also run the free check for every provider with a credential (network)')
@pass_context
def auth_status(ctx: FrameworkContext, as_json: bool, check: bool):
    """Per provider: sign-in method, where the credential comes from, account and the last four characters of the
    key. Exit code 5 when the credential vault cannot be used; with --check the code of the first failed check."""
    from orchestrator.llm.auth.signin import STORAGE, exit_code
    service = _auth_service(ctx)
    statuses = service.status_all(check=check)
    storage = service.storage()
    failed = next((s.check.error for s in sorted(statuses, key=lambda s: not s.primary)
                   if s.check is not None and not s.check.ok), None)
    if as_json:
        click.echo(json.dumps({"storage": storage, "providers": [s.to_dict() for s in statuses]}, indent=2,
                              ensure_ascii=False))
    else:
        table = Table(title="Sign-in")
        for column in ("Provider", "Method", "Source", "Account / key", "Status"):
            table.add_column(column)
        if check:
            table.add_column("Check")
        for st in statuses:
            key = " · ".join(p for p in (st.account, f"...{st.last4}" if st.last4 else None,
                                         st.fields.get("workspace_id")) if p)
            state = "credential vault unavailable (see below)" if st.state == STORAGE else st.message
            row = [escape(("* " if st.primary else "") + st.label), escape(st.method_label or "-"),
                   escape(st.source or "-"), escape(key or "-"), escape(state)]
            if check:
                row.append(escape(st.check.message) if st.check is not None else "-")
            table.add_row(*row)
        console.print(table)
        _say("* Ditto's provider (ai_provider). Sign in: llm-cli ditto login <provider>")
        if storage["usable"]:
            _say(f"Credential vault: {storage['mode']}" + (f", {storage['file']}" if storage["file"] and
                                                            storage["mode"] != "ephemeral" else ""))
    if not storage["usable"]:
        if as_json:
            sys.exit(5)
        _storage_fail(service.storage_problem(), service=service)
    if failed is not None:
        sys.exit(exit_code(failed))


@ditto_auth.command('methods')
@click.argument('provider', required=False)
@click.option('--json', 'as_json', is_flag=True, help='Machine-readable output')
@pass_context
def auth_methods(ctx: FrameworkContext, provider: Optional[str], as_json: bool):
    """Sign-in methods per provider in the order they are preferred, with the key page, environment variables,
    extra options and policy notes. Needs neither the vault nor the network."""
    from orchestrator.llm.auth.signin import method_infos
    from orchestrator.llm.auth.spec import TYPE_LABELS
    catalog = _auth_catalog(ctx)
    presets = [_auth_preset(catalog, provider)] if provider else list(catalog.providers.values())
    options = dict(FIELD_OPTIONS)
    if as_json:
        click.echo(json.dumps({"providers": [{
            "provider": p.id, "label": p.label, "methods": [{
                "id": i.spec.id, "type": i.spec.type, "label": i.spec.label, "built": i.built,
                "recommended": i.recommended, "opt_in": i.spec.opt_in, "key_page": i.spec.key_page,
                "env": list(i.spec.env), "fields": [f.id for f in i.spec.fields], "note": i.spec.note,
                "where": i.spec.where} for i in method_infos(p)]} for p in presets]}, indent=2, ensure_ascii=False))
        return
    for preset in presets:
        _say(f"{preset.label} [{preset.id}]")
        for number, info in enumerate(method_infos(preset), 1):
            spec = info.spec
            _say(f"  {number}. {spec.label} [{spec.id}] - {TYPE_LABELS.get(spec.type, spec.type)}{_method_tags(info)}")
            if spec.key_page:
                _say(f"     key page: {spec.key_page}")
            if spec.where:
                _say(f"     where: {spec.where}")
            if spec.env:
                _say(f"     environment: {', '.join(spec.env)} (llm-cli ditto login {preset.id} --from-env)")
            if spec.fields:
                _say("     options: " + ", ".join(f"{options.get(f.id, f.id)} ({f.label})" for f in spec.fields))
            if spec.note:
                _say(f"     note: {spec.note}")


@ditto_auth.command('storage')
@click.option('--use', 'mode', type=click.Choice(['keyring', 'helper', 'passphrase', 'insecure', 'ephemeral']),
              help='Protect the vault this way from now on (every stored credential is re-encrypted)')
@click.option('--helper', 'helper', metavar='COMMAND',
              help='With --use helper: the command that prints the vault key, e.g. "pass show llmcc/vault-key" '
                   '(run without a shell)')
@click.option('--yes', '-y', is_flag=True, help='Do not ask before choosing the unencrypted key file')
@pass_context
def auth_storage(ctx: FrameworkContext, mode: Optional[str], helper: Optional[str], yes: bool):
    """Show how the credential vault is protected, or choose another protection with --use. Nothing is chosen
    automatically; exit code 5 when the vault cannot be used."""
    from orchestrator.Core.vault_key import StorageError
    if mode == "ephemeral":
        _usage("ephemeral protects no vault and is chosen per run: LLM_SECRET_STORAGE=ephemeral llm-cli ...")
    if helper is not None and mode != "helper":
        _usage("--helper belongs to --use helper.")
    service = _auth_service(ctx)
    if mode is None:
        info = service.storage()
        if not info["usable"]:
            _storage_fail(service.storage_problem(), service=service)
        _say(f"The credential vault is protected with '{info['mode']}'."
             + (f" File: {info['file']}" if info["file"] and info["mode"] != "ephemeral" else ""))
        _say("Other choices: " + "; ".join(f"{name}: {text}" for name, text in info["choices"].items()
                                           if name != info["mode"]))
        return
    if mode == "insecure" and not yes and _interactive() and not click.confirm(
            "The vault key will be kept in a file next to the vault, NOT encrypted at rest. Continue?",
            default=False, err=True):
        _say("Cancelled.", err=True)
        sys.exit(EXIT_CANCELLED)
    try:
        service.choose_storage(mode, helper)
    except StorageError as e:
        _storage_fail(e)
    _say(f"The credential vault is now protected with '{mode}'.", fg="green")


@ditto_auth.command('rotate-key')
@pass_context
def auth_rotate_key(ctx: FrameworkContext):
    """Create a new vault key and re-encrypt every stored credential with it; the old key is removed."""
    from orchestrator.Core.vault_key import StorageError
    from orchestrator.llm.errors import LLMError
    service = _auth_service(ctx)
    try:
        service.rotate_key()
    except (LLMError, StorageError) as e:
        _storage_fail(e, service=service)
    if getattr(service.secrets, "storage_mode", None) == "ephemeral":
        _say("The vault is kept in memory for this run only (secret_storage: ephemeral); the new key is not saved.",
             err=True, fg="yellow")
        return
    _say("Vault key rotated: every stored credential is re-encrypted with the new key, and the old key was removed.",
         fg="green")


# ============================================================================
# DOCTOR (the 3.0.0 name of Mojo, kept as an alias with its options, output and exit codes)
# ============================================================================

DOCTOR_NOTE = ("Note: the health check is Mojo now: 'llmcf mojo' checks (Mojo Oracle) and 'llmcf mojo --miracle' "
               "repairs (Mojo Miracle); 'llm-cli doctor' keeps working.")


@cli.command('doctor')
@click.option('--json', 'as_json', is_flag=True, help='Machine-readable output')
@click.option('--fix', is_flag=True, help='Apply automatic fixes up to --tier, then check again')
@click.option('--tier', default=1, show_default=True, type=click.IntRange(1, 2),
              help='1: harmless fixes (makes your personal folder private); 2: also lists the framework\'s build '
                   'images (name, id, size, what each is; nothing ticked) and deletes exactly those you pick and '
                   'confirm in a terminal; the Docker build cache is never cleared')
@pass_context
def doctor(ctx: FrameworkContext, as_json: bool, fix: bool, tier: int):
    """Check the installation and explain how to fix problems (exit code 0 ok, 1 warnings, 2 errors)"""
    from orchestrator.Core.doctor import Doctor, Severity
    click.echo(DOCTOR_NOTE, err=True)
    ctx.require_framework()
    doc = Doctor(ctx.framework_manager)
    report, why_not = _doctor_fix(doc) if fix else (doc.run(), {})
    if as_json:
        data = report.to_dict()
        if fix and tier >= 2:  # listed only: a deletion needs the person's yes at a terminal
            data["framework_images"] = _doctor_image_listing(ctx.framework_manager)
        click.echo(json.dumps(data, indent=2))
        sys.exit(report.exit_code)
    icons = {Severity.OK: "[green]✔[/green]", Severity.INFO: "[blue]ℹ[/blue]",
             Severity.WARN: "[yellow]![/yellow]", Severity.ERROR: "[red]✘[/red]"}
    for finding in report.findings:
        console.print(f"{icons[finding.severity]} {escape(finding.subsystem)}: {escape(finding.message)}")
        if finding.severity != Severity.OK and finding.fix_hint:
            tier_note = f" (automatic fix: --fix --tier {finding.fix_tier})" if finding.fix else ""
            console.print(f"    → {escape(finding.fix_hint)}{tier_note}")
    for code in report.fixed:
        console.print(f"[green]Fixed: {escape(code)}[/green]")
    for code in report.still_present:
        why = f" ({why_not[code]})" if why_not.get(code) else ""
        console.print(f"[yellow]Still present after the fix: {escape(code)}{escape(why)}[/yellow]")
    if fix and tier >= 2:
        _doctor_free_space(ctx.framework_manager)
    sys.exit(report.exit_code)


def _doctor_fix(doc):
    """`--fix` (tier 1 and the first part of tier 2): the harmless repairs of Mojo Miracle (they only narrow access or
    switch to an intact build; audited, applied only while nothing changed, checked again), reported as the doctor
    did: `fixed` and `still_present`, plus why each one still present was not repaired (for the person's output)."""
    terminal = _interactive()
    asked = {"decision": "person", "answered_by": "person", "confirm_channel": "framework-cli"} if terminal else {}
    result = doc.miracle(actor="cli", at_terminal=False, **asked)
    report = result.after
    report.fixed, report.still_present, why_not = _miracle_fixed(result)
    return report, why_not


def _miracle_fixed(result) -> Tuple[List[str], List[str], Dict[str, str]]:
    """The repairs Mojo Miracle tried, as `doctor --fix` reports them: the codes it repaired, the codes still present
    after it, and why each of those was not repaired."""
    tried = [e for e in result.entries if e.verdict == "miracle"]
    still = [e for e in tried if e.outcome not in ("repaired", "already_fixed")]
    return ([e.finding.code for e in tried if e.outcome == "repaired"], [e.finding.code for e in still],
            {e.finding.code: e.reason for e in still})


def _doctor_image_listing(framework) -> dict:
    """Tier 2 for `--json`: the list only (nothing is deleted without the person's yes at a terminal)."""
    from orchestrator.Core.mojo.docker_objects import DockerUnavailable, docker_storage, framework_image_plan
    try:
        plan = framework_image_plan(framework)
    except DockerUnavailable as e:
        return {"available": False, "problem": str(e), "images": [], "deleted": False}
    items = plan.items if plan is not None else []
    storage = docker_storage(framework) or {}
    return {"available": True, "deleted": False, "docker_root": storage.get("root") or None,
            "note": "Nothing is deleted with --json: run 'llm-cli doctor --fix --tier 2' in a terminal (or use Tools "
                    "> Framework Build Images... in the GUI) and tick the images to delete.",
            "images": [{"name": i.speaking_name, "id": i.file_name, "size_bytes": i.size_bytes, "what": i.what}
                       for i in items]}


def _doctor_free_space(framework, heading: str = "Tier 2: the framework's build images",
                       again: str = "llm-cli doctor --fix --tier 2") -> Tuple[int, str]:
    """Tier 2 (and Mojo Miracle at a terminal): lists every build image of the framework (name, id, size, what each
    is), nothing ticked, and deletes exactly the images the person picks and then confirms at a terminal (default no).
    Docker's build cache is never cleared: it cannot be limited to the framework. The same list and deletion as Tools >
    Framework Build Images... in the GUI: the one deletion path for the framework's Docker images. Returns how many
    images were deleted and, when none was, why ("" when the person chose none). The listing and the deletion are
    docker_objects.offer_image_list's, which the GUI's Mojo window opens with its deletion dialog."""
    from orchestrator.Core.knowledge_snapshots import size_text
    from orchestrator.Core.mojo.docker_objects import offer_image_list
    cache_note = ("Docker's build cache is not touched: it cannot be limited to the framework ('docker system df' "
                  "shows its size; 'docker builder prune' would clear it for every project on this computer).")
    console.print(f"\n[bold]{escape(heading)}[/bold]")

    def where(root: str) -> None:
        if root:
            console.print(f"Docker keeps its images in {escape(root)}.")

    def ask(plan, root: str):
        where(root)
        console.print("Sizes include layers other images may share, so less space can be freed. " + escape(cache_note))
        return confirm_deletion(plan, title=f"{len(plan.items)} framework build images, "
                                            f"{size_text(plan.total_bytes)}", multiple=True, again=again)

    outcome = offer_image_list(framework, ask, actor="cli")
    if not outcome.listed:
        console.print(f"[yellow]{escape(outcome.problem)}[/yellow] {escape(cache_note)}")
        return 0, outcome.why
    if outcome.plan is None:
        where(outcome.root)
        console.print(f"No framework images to delete. {escape(cache_note)}")
        return 0, outcome.why
    if outcome.problem:
        console.print(f"[yellow]{escape(outcome.problem)}[/yellow]")
        return 0, outcome.why
    result = outcome.result
    if result is None:
        return 0, ""
    for name in result.deleted:
        console.print(f"[green]Deleted: {escape(name)}[/green]")
    for name, reason in result.failed:
        console.print(f"[yellow]Kept: {escape(name)} ({escape(reason)})[/yellow]")
    console.print(f"{len(result.deleted)} images deleted, about {size_text(result.freed_bytes)}.")
    return outcome.count, outcome.why


# ============================================================================
# MOJO (the health check: Mojo Oracle looks, Mojo Miracle repairs; round 6 design 7, round 7 items 11', M', T)
# ============================================================================

MOJO_ICONS = {"ok": "[green]✔[/green]", "info": "[blue]ℹ[/blue]", "warn": "[yellow]![/yellow]",
              "error": "[red]✘[/red]"}


@cli.command('mojo')
@click.option('--oracle', 'mode', flag_value='oracle', default=True,
              help='Only look and report what was found (the default); at a terminal it then offers Mojo Miracle')
@click.option('--miracle', 'mode', flag_value='miracle',
              help='Repair what can be repaired, then report what was repaired, what was not and why')
@click.option('--json', 'as_json', is_flag=True,
              help='Machine-readable output; never asks and never deletes (with --miracle only the harmless repairs '
                   'run)')
@click.option('--board', 'board', default=None, metavar='NAME',
              help="Check this one trusted board now: its label, device ID or address (one connection to its SSH "
                   "port, no login). Without it Mojo contacts no board and reports only what was recorded")
@pass_context
def mojo(ctx: FrameworkContext, mode: str, as_json: bool, board: Optional[str]):
    """Check the installation (Mojo Oracle) and repair what can be repaired (Mojo Miracle); exit code 0 ok, 1
    warnings, 2 errors"""
    from orchestrator.Core.mojo import Mojo, offers
    if board is not None and mode != "oracle":
        _usage("--board goes with --oracle: Mojo Oracle checks the board, Mojo Miracle repairs nothing on a board.")
    ctx.require_framework()
    framework = ctx.framework_manager
    contact = (_mojo_board(ctx, board),) if board is not None else ()
    engine = Mojo(framework, contact=contact)
    report = engine.run()
    offered = offers(framework, report)
    if as_json:
        if mode == "miracle":
            # Without a person at a terminal: only the harmless repairs (round 6 Q11); nothing is deleted.
            result = engine.miracle(actor="cli", at_terminal=False, before=report)
            data = {"mode": mode, **_mojo_dict(result.after, offers(framework, result.after)),
                    **_mojo_miracle_dict(result)}
        else:
            data = {"mode": mode, **_mojo_dict(report, offered)}
        click.echo(json.dumps(data, indent=2))
        sys.exit(data["exit_code"])
    terminal = _interactive()
    _mojo_print_oracle(report, offered, terminal)
    _mojo_offer_boards(framework, report, terminal)
    if mode == "oracle":
        from orchestrator.Core.mojo.views import miracle_acts
        acts = miracle_acts(offered)   # the GUI's Mojo window offers Mojo Miracle under the same rule
        if not acts:
            sys.exit(report.exit_code)
        if not terminal:
            console.print("Mojo Miracle can act on " + ("1 of them" if len(acts) == 1 else f"{len(acts)} of them")
                          + ": run 'llmcf mojo --miracle' (in a terminal it also shows you the list to choose what is "
                            "deleted).")
            sys.exit(report.exit_code)
        if not click.confirm("Run Mojo Miracle now?", default=False):
            console.print("Mojo Miracle did not run; nothing was changed.")
            sys.exit(report.exit_code)
    console.print("\n[bold]Mojo Miracle: repairing what can be repaired[/bold]")
    asked = {"decision": "person", "answered_by": "person", "confirm_channel": "framework-cli"} if terminal else {}
    # The plans printed above bind the repairs: a problem that changed since then is not repaired (plan_changed).
    result = engine.miracle(actor="cli", at_terminal=terminal, before=report, offered=offered,
                            free_space=(lambda: _mojo_free_space(framework)) if terminal else None, **asked)
    _mojo_print_miracle(result)
    sys.exit(result.exit_code)


def _mojo_board(ctx: FrameworkContext, name: str) -> str:
    """The device ID of the trusted board `--board` names (its label, device ID or address); exits with the trusted
    boards when it names none or several (Core/mojo/boards.find_board, as the GUI finds it)."""
    from orchestrator.Core.mojo.boards import find_board
    found = find_board(ctx.framework_manager, name)
    if found.device_id:
        return found.device_id
    console.print(f"[red]{escape(found.problem)}.[/red]" + (" Give one of these (label, device ID or address):"
                                                             if found.boards else " No board is trusted yet."))
    for d in found.boards:
        console.print(f"  {escape(d.label)}  {d.device_id}  {escape(d.address)}")
    sys.exit(2)


def _mojo_board_command(name: str, device_id: str) -> str:
    """The command that checks one board: its name in double quotes when it has spaces, its device ID when the name
    cannot be quoted plainly."""
    if all(ch.isalnum() or ch in "._:-[]" for ch in name):
        arg = name
    elif not any(ch in name for ch in '"`$%!\\'):
        arg = f'"{name}"'
    else:
        arg = device_id
    return f"llmcf mojo --oracle --board {arg}"


def _mojo_offer_boards(framework, report, terminal: bool) -> None:
    """Boards a recorded fact points to and that Mojo did not contact (W1): at a terminal "Check <board> now? [y/N]"
    for each (yes: one connection to its SSH port, no login, and the result); without a terminal the command that
    checks it. The person sees the board's label; the command names it as `--board` takes it best (board_name). The
    boards and the check are Core/mojo/boards' (the GUI's Mojo window offers the same boards)."""
    from orchestrator.Core.mojo.boards import boards_to_offer, check_board
    for device_id, label, name in boards_to_offer(framework, report):
        command = _mojo_board_command(name, device_id)
        if not terminal:
            console.print(f"Mojo did not contact the board {escape(label)}. To check it: {escape(command)}")
            continue
        if not click.confirm(f"Check {label} now? (one connection to its SSH port, no login)", default=False):
            console.print(f"Mojo did not contact {escape(label)}. Later: {escape(command)}")
            continue
        finding = check_board(framework, device_id)
        if finding is None:
            continue
        console.print(f"{MOJO_ICONS[finding.severity.value]} {escape(finding.subsystem)}: {escape(finding.message)}")
        if finding.severity.value != "ok" and finding.fix_hint:
            console.print(f"    → {escape(finding.fix_hint)}")


def _mojo_miracle_line(offer, terminal: bool) -> str:
    """The person's line about what Mojo Miracle can do for one finding (Core/mojo/views.miracle_line; the GUI's Mojo
    window shows the same kinds of line)."""
    from orchestrator.Core.mojo.views import miracle_line
    return miracle_line(offer, terminal)


def _mojo_print_oracle(report, offered, terminal: bool) -> None:
    """What Mojo Oracle found: every finding; for each problem where (the full path), why it matters, the hint and
    what Mojo Miracle can do; then one sentence that counts every problem by what can be done about it."""
    from orchestrator.Core.mojo.views import oracle_summary, person_findings, plan_steps
    console.print("[bold]Mojo Oracle: checking the installation (read-only, nothing is changed)[/bold]")
    for finding, offer in person_findings(report, offered):
        console.print(f"{MOJO_ICONS[finding.severity.value]} {escape(finding.subsystem)}: {escape(finding.message)}")
        if offer is None:  # no problem: an information's hint is shown as the doctor showed it
            if finding.severity.value == "info" and finding.fix_hint:
                console.print(f"    → {escape(finding.fix_hint)}")
            continue
        if finding.where:
            console.print(f"    Where: {escape(finding.where)}")
        if finding.why:
            console.print(f"    Why it matters: {escape(finding.why)}")
        if finding.fix_hint:
            console.print(f"    → {escape(finding.fix_hint)}")
        console.print(f"    {escape(_mojo_miracle_line(offer, terminal))}")
        for step in plan_steps(offer):
            where = f" ({step.full_path})" if step.full_path else ""
            console.print(f"      Plan: {escape(step.target_label)}{escape(where)}: {escape(step.before)} → "
                          f"{escape(step.after)}" + (", can be undone" if step.undoable else ""))
    console.print(escape(oracle_summary(offered, person=True)))


def _mojo_print_miracle(result) -> None:
    """What Mojo Miracle did: every problem, repaired or not, and why not; then the count (Core/mojo/views: the GUI's
    Mojo window shows the same)."""
    from orchestrator.Core.mojo.views import entry_next, entry_text, miracle_summary, outcome_kind
    words = {"repaired": "[green]✔ Repaired[/green]", "already_fixed": "[green]✔ Already gone[/green]",
             "still_present": "[yellow]! Still there[/yellow]", "declined": "[yellow]- Not deleted[/yellow]",
             "not_repaired": "[red]✘ Not repaired[/red]"}
    for entry in result.entries:
        finding = entry.finding
        console.print(f"{words[outcome_kind(entry)]}: {escape(finding.subsystem)} ({escape(finding.code)}): "
                      f"{escape(entry_text(entry))}")
        step, hint = entry_next(entry)
        if step == "again":
            console.print("    → Run 'llmcf mojo' again: it shows the new plan.")
        elif step == "hint":
            console.print(f"    → {escape(hint)}")
    for finding in result.new_problems:
        console.print(f"{MOJO_ICONS[finding.severity.value]} New after Mojo Miracle: {escape(finding.subsystem)} "
                      f"({escape(finding.code)}): {escape(finding.message)}")
        if finding.fix_hint:
            console.print(f"    → {escape(finding.fix_hint)}")
    console.print(miracle_summary(result))


def _mojo_free_space(framework) -> Tuple[int, str]:
    """The person's list of the framework's build images (WP7's deletion path), from Mojo Miracle at a terminal: how
    many images were deleted, and why none was ("" when the person chose none)."""
    return _doctor_free_space(framework, heading="Free disk space: the framework's build images",
                              again="llmcf mojo --miracle")


def _mojo_dict(report, offered) -> dict:
    """Mojo Oracle for --json: the report (the doctor's keys) with what Mojo Miracle can do for each problem."""
    from orchestrator.Core.mojo.views import oracle_summary
    by_finding = {id(o.finding): o for o in offered}
    data = report.to_dict()
    for row, finding in zip(data["findings"], report.findings):
        if finding.board_check:   # a recorded fact points to this board; Mojo did not contact it (W1)
            row["check_board"] = {"board": finding.board_name, "device_id": finding.board_check,
                                  "command": _mojo_board_command(finding.board_name, finding.board_check)}
        offer = by_finding.get(id(finding))
        if offer is not None:
            row["miracle"] = {"can": offer.verdict, "summary": offer.summary,
                              "plan": [{"target": s.target_label, "path": s.full_path, "before": s.before,
                                        "after": s.after, "undoable": s.undoable}
                                       for s in (offer.plan.steps if offer.plan is not None else ())]}
    data["summary"] = oracle_summary(offered, person=True)
    return data


def _mojo_miracle_dict(result) -> dict:
    """Mojo Miracle for --json: what was repaired, and what was not and why. `fixed` and `still_present` mean what they
    mean for `doctor --fix --json`: the repairs Mojo Miracle made, and those it tried that are still there."""
    fixed, still_present, _ = _miracle_fixed(result)
    return {"exit_code": result.exit_code, "fixed": fixed, "still_present": still_present,
            "repaired": [{"code": e.finding.code, "summary": e.summary} for e in result.repaired],
            "already_gone": [{"code": e.finding.code, "reason": e.reason} for e in result.already_gone],
            "not_repaired": [{"code": e.finding.code, "outcome": e.outcome, "can": e.verdict, "reason": e.reason}
                             for e in result.not_repaired],
            "new_problems": [f.code for f in result.new_problems]}


# ============================================================================
# DELETING (the person's question with the full list; round 5 f)
# ============================================================================

def _parse_numbers(text: str, count: int) -> Optional[List[int]]:
    """'1,3' or '2-4' (1-based) as 0-based positions; None for anything else or a number outside 1..count."""
    picked: List[int] = []
    for part in (p.strip() for p in str(text or "").split(",")):
        if not part:
            continue
        first, dash, last = part.partition("-")
        if not first.strip().isdigit() or (dash and not last.strip().isdigit()):
            return None
        low, high = int(first), int(last) if dash else int(first)
        if low < 1 or high > count or low > high:
            return None
        picked.extend(range(low - 1, high))
    return sorted(set(picked))


def confirm_deletion(plan, *, title: str, multiple: bool = False, again: str = "",
                     verb: str = "Delete") -> Optional[Any]:
    """The person's question before any deletion at the command line (design 3.10, round 5 f): the full list (name,
    full path or Docker id, file name, size, what it is), then, with `multiple`, the numbers to delete (nothing is
    ticked by default), the mode when the plan offers the trash, and "Delete exactly N items (size)? [y/N]". Only at a
    terminal: without one nothing is asked and nothing deleted. Returns the accepted plan (orchestrator.Core.deletion)
    of exactly the chosen items, or None."""
    from orchestrator.Core.deletion import accept, choose, size_text
    table = Table(title=title)
    for column in ("#", "Item", "Full path or id", "File", "Size", "What it is"):
        table.add_column(column, overflow="fold")
    for number, item in enumerate(plan.items, 1):
        table.add_row(str(number), escape(item.speaking_name), escape(item.full_path or item.file_name),
                      escape(item.file_name), size_text(item.size_bytes), escape(item.what))
    console.print(table)
    if not _interactive():
        console.print("[yellow]Nothing was deleted: deleting needs your answer at a terminal"
                      + (f" ('{escape(again)}')" if again else "") + ".[/yellow]")
        return None
    chosen = plan
    if multiple:
        while True:
            answer = click.prompt("Numbers to delete (e.g. 1,3 or 2-4), empty for none", default="",
                                  show_default=False)
            picked = _parse_numbers(answer, len(plan.items))
            if picked is not None:
                break
            console.print(f"[yellow]Give numbers from 1 to {len(plan.items)}.[/yellow]")
        chosen = choose(plan, picked)
        if chosen is None:
            console.print("Nothing was deleted.")
            return None
    mode = chosen.mode_offered[0]
    if len(chosen.mode_offered) > 1:
        console.print("trash: moved to the trash, you can restore them, and their space stays used until you empty "
                      f"the trash; permanent: deleted for good, frees {size_text(chosen.total_bytes)} now.")
        mode = click.prompt("Move to the trash or delete for good?", type=click.Choice(list(chosen.mode_offered)),
                            default=chosen.mode_offered[0])
    what = "Move exactly {n} items ({size}) to the trash?" if mode == "trash" else \
        verb + " exactly {n} items ({size}) for good?"
    if not click.confirm(what.format(n=len(chosen.items), size=size_text(chosen.total_bytes)), default=False):
        console.print("Nothing was deleted.")
        return None
    return accept(chosen, "cli", mode)


def _cli_trash(ctx: FrameworkContext):
    from orchestrator.Core.trash import Trash
    return Trash.for_framework(ctx.framework_manager)


def _cli_audit(ctx: FrameworkContext):
    audit = getattr(getattr(ctx.framework_manager, "tool_registry", None), "audit", None)
    return audit if callable(getattr(audit, "record", None)) else None


def _person_trash(ctx: FrameworkContext, state_dir: Path):
    """(trash, audit log) for the person's deletion at a terminal, also when the framework is not booted (the runtime
    commands need neither the secrets store nor Docker): the trash index is signed with the audit key, as the
    framework's own trash."""
    from orchestrator.Core.trash import DEFAULT_RETENTION_DAYS, Trash
    fm = ctx.framework_manager if getattr(ctx, "_initialized", False) else None
    if fm is not None:
        return Trash.for_framework(fm), _cli_audit(ctx)
    audit = _device_audit(ctx, state_dir)
    try:
        from orchestrator.Core.vault_key import audit_key
        key = audit_key(None, state_dir)
    except Exception:  # noqa: BLE001 - without a keyring the trash index is unkeyed, like the permission file
        key = None
    config, _ = _settings(ctx)
    try:
        days = int(config.get("trash_retention_days", DEFAULT_RETENTION_DAYS) or DEFAULT_RETENTION_DAYS)
    except (TypeError, ValueError):
        days = DEFAULT_RETENTION_DAYS
    refuse = None if key else getattr(audit, "kept_with_key", None)
    return Trash(state_dir, key=key, refuse_unkeyed=refuse, audit=audit, retention_days=days), audit


def _print_deletion_result(result, accepted) -> None:
    """What a deletion did (deletion.DeletionResult): every item moved or deleted, every item kept and why."""
    from orchestrator.Core.deletion import size_text
    for name in result.done:
        console.print(f"[green]{'Moved to the trash' if accepted.mode == 'trash' else 'Deleted'}: "
                      f"{escape(name)}[/green]")
    for name, reason in result.failed:
        console.print(f"[yellow]Kept: {escape(name)} ({escape(reason)})[/yellow]")
    if accepted.mode == "trash" and result.done:
        console.print("They can be restored with 'llm-cli trash restore ID' ('llm-cli trash' lists them); their space "
                      "stays used until you empty the trash.")
    elif result.done:
        console.print(f"{size_text(result.freed_bytes)} freed.")


# ============================================================================
# KNOWLEDGE BASE (what the person sees of it on the command line)
# ============================================================================

@cli.group()
def knowledge():
    """The knowledge base on this computer: its sources and their licences, snapshots, the framework's documents"""
    pass


@knowledge.group('snapshots', invoke_without_command=True)
@click.pass_context
def knowledge_snapshots(click_ctx):
    """The knowledge base's snapshots (same as 'snapshots list')"""
    if click_ctx.invoked_subcommand is None:
        click_ctx.invoke(knowledge_snapshots_list)


@knowledge_snapshots.command('list')
@pass_context
def knowledge_snapshots_list(ctx: FrameworkContext):
    """Every snapshot with its full path, file name, size and what it is; nothing is deleted"""
    from orchestrator.Core.knowledge_snapshots import KEEPING_NOTE, list_snapshots, size_text, snapshot_folder
    ctx.require_framework()
    folder = snapshot_folder(ctx.framework_manager.config.user_state_dir)
    snapshots = list_snapshots(folder)
    if not snapshots:
        console.print(f"No knowledge snapshots in {escape(str(folder))}.")
        return
    total = sum(s.size_bytes for s in snapshots)
    table = Table(title=f"Knowledge snapshots ({len(snapshots)}, {size_text(total)}), oldest first")
    for column in ("Snapshot", "Taken", "Size", "What it is", "Full path"):
        table.add_column(column, overflow="fold")
    for s in snapshots:
        table.add_row(escape(s.name), s.taken, size_text(s.size_bytes), escape(s.what), escape(str(s.path)))
    console.print(table)
    _print_snapshot_disk(ctx, total)
    console.print(escape(KEEPING_NOTE))


def _print_snapshot_disk(ctx: FrameworkContext, total: int) -> None:
    """What the snapshots use against the free space of their disk, and whether the next one fits (eighth round,
    answer 2: a snapshot that does not fit stops the change that needs it; nothing is deleted by itself)."""
    from orchestrator.Core.knowledge_snapshots import snapshot_disk
    disk = snapshot_disk(ctx.framework_manager.config.user_state_dir, total)  # the GUI's line says the same
    if disk is None:
        return
    console.print(escape(disk.line()))
    warning = disk.warning()
    if warning:
        console.print(f"[yellow]{escape(warning)}[/yellow]")


def _delete_snapshots_cli(ctx: FrameworkContext, names: Optional[Tuple[str, ...]]) -> None:
    """Lists the snapshots (`names`, or every one for the multiple choice) and deletes exactly what the person
    confirms at a terminal: into the trash by default, or for good."""
    from orchestrator.Core.deletion import PlanChanged, size_text
    from orchestrator.Core.knowledge_snapshots import delete_snapshots, snapshot_folder, snapshot_plan
    ctx.require_framework()
    folder = snapshot_folder(ctx.framework_manager.config.user_state_dir)
    try:
        plan = snapshot_plan(folder, names or None)
    except ValueError as e:
        console.print(f"[red]{escape(str(e))}[/red] 'llm-cli knowledge snapshots' lists them.")
        sys.exit(2)
    if plan is None:
        console.print(f"No knowledge snapshots in {escape(str(folder))}.")
        return
    again = "llm-cli knowledge snapshots " + ("delete " + " ".join(names) if names else "prune")
    accepted = confirm_deletion(plan, title=f"Knowledge snapshots ({len(plan.items)}, {size_text(plan.total_bytes)}), "
                                            "oldest first", multiple=not names, again=again)
    if accepted is None:
        return
    try:
        result = delete_snapshots(folder, accepted, trash=_cli_trash(ctx), audit=_cli_audit(ctx))
    except (PlanChanged, PermissionError, RuntimeError) as e:
        console.print(f"[yellow]{escape(str(e))}[/yellow]")
        return
    for name in result.done:
        console.print(f"[green]{'Moved to the trash' if accepted.mode == 'trash' else 'Deleted'}: "
                      f"{escape(name)}[/green]")
    for name, reason in result.failed:
        console.print(f"[yellow]Kept: {escape(name)} ({escape(reason)})[/yellow]")
    if accepted.mode == "trash" and result.done:
        console.print("They can be restored with 'llm-cli trash restore ID' ('llm-cli trash' lists them); their space "
                      "stays used until you empty the trash.")


@knowledge_snapshots.command('delete')
@click.argument('names', nargs=-1, required=True)
@pass_context
def knowledge_snapshots_delete(ctx: FrameworkContext, names: Tuple[str, ...]):
    """Delete exactly the snapshots NAMES, after you confirm the list in a terminal (trash first)"""
    _delete_snapshots_cli(ctx, names)


@knowledge_snapshots.command('prune')
@pass_context
def knowledge_snapshots_prune(ctx: FrameworkContext):
    """List every snapshot and delete the ones you pick, after you confirm them in a terminal (nothing preselected)"""
    _delete_snapshots_cli(ctx, None)


@knowledge_snapshots.command('export')
@click.argument('names', nargs=-1, required=True)
@click.option('--to', 'destination', required=True, type=click.Path(path_type=Path),
              help='An existing folder: on this computer, a USB stick or a network share')
@pass_context
def knowledge_snapshots_export(ctx: FrameworkContext, names: Tuple[str, ...], destination: Path):
    """Copy the snapshots NAMES to a folder you choose; each copy is checked by sha256. Nothing is deleted."""
    from orchestrator.Core.knowledge_snapshots import export_snapshots, size_text, snapshot_folder
    ctx.require_framework()
    folder = snapshot_folder(ctx.framework_manager.config.user_state_dir)
    try:
        results = export_snapshots(folder, names, destination)
    except ValueError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(2)
    for r in results:
        if r.ok:
            note = f" ({r.problem})" if r.problem else ""
            console.print(f"[green]Copied and checked: {escape(r.name)} -> {escape(r.copy)} "
                          f"({size_text(r.size_bytes)}, sha256 {r.sha256[:16]}...){escape(note)}[/green]")
        else:
            console.print(f"[yellow]Not exported: {escape(r.name)}: {escape(r.problem)}[/yellow]")
    done = sum(1 for r in results if r.ok)
    console.print(f"{done} of {len(results)} snapshots exported. The snapshots themselves were not changed; delete "
                  "them with 'llm-cli knowledge snapshots delete NAME' if you want the space back.")
    if done < len(results):
        sys.exit(1)


@knowledge.group('storage', invoke_without_command=True)
@click.pass_context
def knowledge_storage(click_ctx):
    """Where the local knowledge base is stored (Qdrant Edge), and the old storage of earlier versions"""
    if click_ctx.invoked_subcommand is None:
        click_ctx.invoke(knowledge_storage_show)


def _print_storage_summary(info: Dict[str, Any], state_dir: Path, used: Optional[str] = None,
                           server: bool = False) -> None:
    """The storage the knowledge base really uses (`used`: qdrant-edge, old-local or server, as an open knowledge base
    named it; else a Qdrant server when `server` (setting qdrant_url), else as the records say, nothing opened), the
    conversion and the old folder (migration.storage_status: Tools > Knowledge Snapshots... shows the same status)."""
    from orchestrator.rag import migration
    for style, line in migration.storage_lines(migration.storage_status(state_dir, info, used, server=server)):
        console.print(f"[{style}]{escape(line)}[/{style}]" if style else escape(line))


@knowledge_storage.command('show')
@pass_context
def knowledge_storage_show(ctx: FrameworkContext):
    """The storage, the conversion of the old storage (when, how many excerpts, what was checked, or why it failed) and
    the old folder; reads the records only, nothing is opened or changed"""
    from orchestrator.rag import migration
    ctx.require_framework()
    state_dir = Path(ctx.framework_manager.config.user_state_dir)
    _print_storage_summary(migration.summary(state_dir), state_dir,
                           server=migration.server_configured(ctx.framework_manager.config))


@knowledge_storage.command('convert')
@pass_context
def knowledge_storage_convert(ctx: FrameworkContext):
    """Convert the old storage to Qdrant Edge now (also after a failed attempt); vectors and texts are copied and
    checked, nothing is embedded again, and the old folder is kept"""
    from orchestrator.rag import migration
    ctx.require_framework()
    state_dir = Path(ctx.framework_manager.config.user_state_dir)
    server = migration.server_configured(ctx.framework_manager.config)
    refusal = migration.convert_refusal(state_dir, server)   # the GUI's [Convert now] is offered on the same condition
    if refusal == "nothing":
        console.print("There is no old storage to convert.")
        _print_storage_summary(migration.summary(state_dir), state_dir, server=server)
        return
    if refusal == "server":   # the knowledge base would open the server: nothing would be converted
        console.print(f"[red]{escape(migration.SERVER_IN_USE)}; nothing was converted.[/red]")
        sys.exit(1)
    if refusal == "engine_missing":
        console.print(f"[red]{escape(migration.ENGINE_MISSING)}; nothing can be converted.[/red]")
        sys.exit(1)
    ensure = getattr(ctx.framework_manager, "ensure_rag_manager", None)
    rag = ensure() if callable(ensure) else None
    if rag is None:
        console.print("[red]The knowledge base is off (setting enable_rag_knowledge). Switch it on in the GUI's AI "
                      "settings first.[/red]")
        sys.exit(1)
    with console.status("Converting the knowledge base to Qdrant Edge (copy and check)..."):
        result = rag.convert_storage()
    _print_storage_summary(result, state_dir, used=result.get("used") or None)
    if result.get("error"):
        console.print(f"[red]{escape(str(result['error']))}[/red]")
    if result.get("used") != "qdrant-edge":
        sys.exit(1)


@knowledge_storage.command('delete-old')
@pass_context
def knowledge_storage_delete_old(ctx: FrameworkContext):
    """Delete the old storage folder after its conversion, once you confirm it in a terminal (trash first)"""
    from orchestrator.Core.deletion import PlanChanged
    from orchestrator.rag import migration
    ctx.require_framework()
    state_dir = Path(ctx.framework_manager.config.user_state_dir)
    plan = migration.old_store_plan(state_dir)
    if plan is None:
        console.print("There is no old storage folder to delete (it is deleted already, or the knowledge base was not "
                      "converted to Qdrant Edge yet, so the old folder is still the knowledge base).")
        return
    accepted = confirm_deletion(plan, title="The old knowledge base storage", again="llm-cli knowledge storage "
                                                                                    "delete-old")
    if accepted is None:
        return
    rag = getattr(ctx.framework_manager, "rag_manager", None)
    if callable(getattr(rag, "close", None)):
        rag.close()  # this process's own hold on the knowledge base (its lock) is released first
    try:
        result = migration.delete_old_store(state_dir, accepted, trash=_cli_trash(ctx), audit=_cli_audit(ctx))
    except (PlanChanged, PermissionError, RuntimeError) as e:
        console.print(f"[yellow]{escape(str(e))}[/yellow]")
        return
    if not result.done:
        console.print(f"[yellow]Kept: the old knowledge base storage ({escape(result.problem)})[/yellow]")
        return
    if accepted.mode == "trash":
        console.print("[green]Moved to the trash: the old knowledge base storage[/green] (restore it with 'llm-cli "
                      "trash restore ID'; its space stays used until you empty the trash).")
    else:
        console.print("[green]Deleted: the old knowledge base storage[/green]")


@knowledge.command('index-docs')
@click.option('--with-code', is_flag=True,
              help="Also index the framework's code and configuration (many more files, takes minutes)")
@pass_context
def knowledge_index_docs(ctx: FrameworkContext, with_code: bool):
    """Add the framework's own documentation (README, CONTRIBUTING, docs) to the knowledge base, so Ditto can search
    it; a snapshot is taken first"""
    from orchestrator.Core.rag_manager import framework_documents
    ctx.require_framework()
    ensure = getattr(ctx.framework_manager, "ensure_rag_manager", None)
    rag = ensure() if callable(ensure) else None
    if rag is None:
        console.print("[red]The knowledge base is off (setting enable_rag_knowledge). Switch it on in the GUI's AI "
                      "settings first.[/red]")
        sys.exit(1)
    docs, code = framework_documents(rag.app_root, with_code)
    from orchestrator.Core.knowledge_snapshots import snapshot_space
    service = getattr(ctx.framework_manager, "build_service", None)
    checks = snapshot_space(rag, getattr(service, "disk_usage", None))
    short = [c for c in checks if not c.fits]
    if short:  # no snapshot, no index: never "continue anyway"
        console.print("[red]Not enough disk space for the snapshot taken first, so nothing was indexed: "
                      f"{escape('; '.join(c.sentence() for c in short))}. Free space first (llm-cli knowledge "
                      "snapshots lists the snapshots with their sizes).[/red]")
        sys.exit(1)
    for check in checks:
        if check.verdict == "tight":
            console.print(f"[yellow]Disk space: {escape(check.sentence())}.[/yellow]")
    console.print(f"Indexing {len(docs)} documentation files"
                  + (f" and {len(code)} code and configuration files" if with_code else "")
                  + " into the knowledge base. A snapshot is taken first, so it can be undone.")
    err = Console(stderr=True)
    with err.status("Indexing...") as status:
        result = rag.ingest_codebase(include_code=with_code,
                                     progress=lambda n, total, name: status.update(f"File {n} of {total}"))
    if not result.get("success"):
        console.print(f"[red]{escape(str(result.get('message')))}[/red]")
        sys.exit(1)
    console.print(f"[green]{escape(str(result.get('message')))}[/green]")
    if result.get("snapshot"):
        console.print(f"Snapshot taken first: {escape(str(result['snapshot']))} (llm-cli knowledge snapshots).")
    console.print("Ditto finds these texts in its knowledge base now (search_knowledge with kind docs for AI "
                  "clients).")


@knowledge.command('sources')
@click.option('--kind', type=click.Choice(["web", "pack", "docs", "other", "any"]), default="any", show_default=True,
              help='Only sources of this kind')
@click.option('--topic', help='Only sources that belong to this topic or target (e.g. llama.cpp or Rockchip)')
@pass_context
def knowledge_sources(ctx: FrameworkContext, kind: str, topic: Optional[str]):
    """What the knowledge base holds: every source with the licence and copyright notice it shows, what a
    knowledge pack would carry of it (full text, or only its address and short facts) and why, and how much hidden
    text was left out of it before it was stored"""
    from orchestrator.Core import knowledge_sources as ks   # Tools > Knowledge Sources... lists the same
    from orchestrator.rag.errors import KnowledgeBaseError
    ctx.require_framework()
    try:
        rag = ks.knowledge_base(ctx.framework_manager)
    except ks.KnowledgeOff as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    try:
        entries = ks.sources(rag, kind, topic)
    except KnowledgeBaseError as e:
        console.print(f"[red]The knowledge base cannot be read: {escape(str(e))}[/red]")
        sys.exit(1)
    if not entries:
        console.print(escape(ks.empty_sentence(kind, topic)))
        return
    table = Table(title="Knowledge sources, their licence and what a knowledge pack carries of them")
    for column in ("Source", "Kind", "Topic or target", "Hidden text left out", "Licence", "Copyright notice",
                   "In a pack", "Why"):
        table.add_column(column, overflow="fold")
    for e in entries:   # addresses, titles and notices are site text: escaped, never markup
        table.add_row(escape(e["source"]), e["kind"], escape(ks.topic_of(e)), ks.removed_text(e),
                      escape(e.get("licence") or "-"), escape(e.get("copyright") or "-"), ks.carried(e),
                      escape(e.get("share_reason") or ""))
    console.print(table)
    console.print(escape(ks.totals_sentence(entries)))


@knowledge.command('loose')
@pass_context
def knowledge_loose(ctx: FrameworkContext):
    """The loose knowledge files: knowledge always belongs to a topic or a target, so a loose file is never imported
    until you sort it into a topic or target folder ('llm-cli knowledge sort')"""
    ctx.require_framework()
    cm = _zoo(ctx)
    found = cm.loose_knowledge()   # the Community Hub's Sort loose knowledge... lists the same
    if not found:
        console.print("No loose knowledge file: every knowledge file belongs to a topic or a target.")
        return
    table = Table(title=f"Loose knowledge files in {escape(str(cm.knowledge_dir))} (not imported)")
    for column in ("File", "Size"):
        table.add_column(column, overflow="fold")
    for item in found:
        table.add_row(escape(item.label), f"{item.size} bytes")
    console.print(table)
    topics = cm.knowledge_topics()
    if topics:
        console.print("Topics and targets in use: " + escape(", ".join(topics)))
    console.print("Sort each with 'llm-cli knowledge sort FILE --topic NAME' (or --target NAME): it is checked like a "
                  "knowledge pack, moved into that folder and imported.")


@knowledge.command('sort')
@click.argument('name')
@click.option('--topic', help='The topic its pages belong to (a new or an existing topic folder)')
@click.option('--target', help='The target its pages belong to (a target on this computer)')
@pass_context
def knowledge_sort(ctx: FrameworkContext, name: str, topic: Optional[str], target: Optional[str]):
    """Sort a loose knowledge file (NAME as 'llm-cli knowledge loose' lists it) into a topic or target folder: it is
    checked like a knowledge pack (quarantine, the knowledge validator, the licence files), shown, and after your Yes
    moved there and imported into the knowledge base (a snapshot first)"""
    from orchestrator.Core.community_pack import PackError
    ctx.require_framework()
    if bool(topic) == bool(target):
        _zoo_fail(click.UsageError("Knowledge always belongs to a topic or a target: give --topic or --target (not "
                                   "both)."), 2)
    cm = _zoo(ctx)
    try:
        prepared = cm.prepare_sort(name, topic=topic, target=target)
    except (PackError, OSError) as e:
        _say_error(e)   # the message, then the next step it carries
        sys.exit(1)
    click.echo(prepared.summary(), err=True)
    if not sys.stdin.isatty():   # a loose file nobody reviewed: always confirmed, like a pack of trust local
        cm.discard(prepared)
        _zoo_fail(ValueError("No terminal to confirm in: not sorted (a loose file is always confirmed)."))
    if not click.confirm("Sort and import this file?", default=False, err=True):
        cm.discard(prepared)
        sys.exit(1)
    try:
        dest = cm.finish_install(prepared)
    except (PackError, OSError) as e:
        _zoo_fail(e)
    console.print(f"[green]Sorted {escape(prepared.loose_label)} into {escape(str(dest))}[/green]")
    _say_knowledge_import(cm)


# ============================================================================
# TRASH (only the person restores or empties it)
# ============================================================================

@cli.group(invoke_without_command=True)
@click.pass_context
def trash(click_ctx):
    """What deletions moved to the trash (same as 'trash list')"""
    if click_ctx.invoked_subcommand is None:
        click_ctx.invoke(trash_list)


@trash.command('list')
@pass_context
def trash_list(ctx: FrameworkContext):
    """Every entry with what it is, where it came from, its size and who asked to delete it"""
    from orchestrator.Core.deletion import size_text
    from orchestrator.Core.trash import Trash, TrashError, describe
    ctx.require_framework()
    try:
        entries = _cli_trash(ctx).entries()
    except TrashError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    if not entries:
        console.print("The trash is empty.")
        return
    total = sum(e.size for e in entries if Trash.present(e))
    table = Table(title=f"Trash ({len(entries)} entries, {size_text(total)}; the space stays used until you empty it)")
    for column in ("Id", "Item", "What it is", "Size", "Moved there", "Asked by", "Reminder", "From (full path)"):
        table.add_column(column, overflow="fold")
    for entry in entries:
        row = describe(entry)
        gone = "" if Trash.present(entry) else " (gone from the trash folder)"
        table.add_row(row["id"], escape(row["name"]) + gone, row["what"], row["size"], row["deleted"],
                      escape(row["by"]), row["remind"], escape(row["from"]))
    console.print(table)
    console.print("Nothing leaves the trash by itself. 'llm-cli trash restore ID' puts an entry back; "
                  "'llm-cli trash empty' deletes for good what you confirm.")


@trash.command('restore')
@click.argument('ids', nargs=-1, required=True)
@pass_context
def trash_restore(ctx: FrameworkContext, ids: Tuple[str, ...]):
    """Put entries back where they were (next to it with a new name when that place is taken)"""
    from orchestrator.Core.trash import TrashError
    ctx.require_framework()
    store = _cli_trash(ctx)
    failed = False
    for entry_id in ids:
        try:
            dest = store.restore(entry_id, "cli")
        except TrashError as e:
            console.print(f"[yellow]{escape(entry_id)}: {escape(str(e))}[/yellow]")
            failed = True
            continue
        console.print(f"[green]Restored {escape(entry_id)} to {escape(str(dest))}[/green]")
    if failed:
        sys.exit(1)


@trash.command('empty')
@click.argument('ids', nargs=-1)
@click.option('--all', 'everything', is_flag=True, help='List every entry (you still pick which ones)')
@click.option('--due', is_flag=True, help='List the entries past their reminder date (you still pick which ones)')
@pass_context
def trash_empty(ctx: FrameworkContext, ids: Tuple[str, ...], everything: bool, due: bool):
    """Delete trash entries for good, after you confirm the list in a terminal (no AI and no tool does this)"""
    from orchestrator.Core.deletion import PlanChanged, size_text
    from orchestrator.Core.trash import TrashError
    ctx.require_framework()
    if bool(ids) + everything + due != 1:
        console.print("[red]Name the entries (IDS), or use --all or --due.[/red]")
        sys.exit(2)
    store = _cli_trash(ctx)
    try:
        plan = store.plan(ids if ids else None, due_only=due)
    except TrashError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    listed = {Path(i.full_path).parent.name for i in plan.items} if plan is not None else set()
    missing = [i for i in ids if i not in listed]
    if missing:
        console.print(f"[red]Not in the trash: {escape(', '.join(missing))}[/red] ('llm-cli trash' lists it).")
        sys.exit(2)
    if plan is None:
        console.print("Nothing to delete.")
        return
    accepted = confirm_deletion(plan, title=f"Trash entries ({len(plan.items)}, {size_text(plan.total_bytes)}) to "
                                            "delete for good", multiple=not ids, again="llm-cli trash empty")
    if accepted is None:
        return
    try:
        result = store.empty(accepted)
    except (PlanChanged, PermissionError, RuntimeError, TrashError) as e:
        console.print(f"[yellow]{escape(str(e))}[/yellow]")
        return
    for name in result.deleted:
        console.print(f"[green]Deleted for good: {escape(name)}[/green]")
    for name, reason in result.failed:
        console.print(f"[yellow]Kept: {escape(name)} ({escape(reason)})[/yellow]")
    console.print(f"{len(result.deleted)} entries deleted, {size_text(result.freed_bytes)} freed.")


# ============================================================================
# MODELS (the models folder: Hugging Face downloads of earlier versions)
# ============================================================================

@cli.group()
def models():
    """The models folder: take over what earlier versions downloaded from Hugging Face"""
    pass


@models.command('take-over')
@pass_context
def models_take_over(ctx: FrameworkContext):
    """Copy what earlier versions saved in the Hugging Face cache inside the models folder into the models folder,
    each file checked, after your yes at a terminal; then offer to remove the old copies (the full list, "No" by
    default). The same as Take over old downloads... in the GUI's Hugging Face browser."""
    ctx.require_framework()
    manager = ctx.framework_manager.model_manager
    lang = _config_language(ctx)
    from orchestrator.utils.localization import ui_text
    from orchestrator.utils.sizes import format_size
    files = manager.old_downloads()
    new = [f for f in files if f.state == "new"]
    if not new:
        plan = manager.old_copies_plan()
        if plan is None:
            console.print(escape(ui_text("hf.takeover.none", lang)))
            return
        _remove_old_hf_copies(ctx, manager, plan, lang)
        return
    # the full list (the GUI's question shows the first ones; a terminal shows every file) and the yes question
    lines = [f"{f.repo} / {f.name} ({format_size(f.size, lang)})" for f in new]
    total = format_size(sum(f.size for f in new), lang)
    click.echo(ui_text("hf.takeover.ask", lang, n=len(new), size=total, files="\n".join(lines)))
    if not _interactive():
        console.print("[yellow]Nothing was copied: the take-over needs your answer at a terminal "
                      "('llm-cli models take-over').[/yellow]")
        return
    if not click.confirm(f"Copy exactly {len(new)} files ({total})?", default=False):
        console.print("Nothing was copied.")
        return
    import threading
    from orchestrator.Core.hf_hub import person_text
    stop = threading.Event()

    def progress(done: int, count: int, name: str) -> None:
        if name:
            console.print(escape(ui_text("hf.takeover.running", lang, name=name, done=done, total=count)) +
                          (" (Ctrl+C)" if done == 0 else ""))
    try:
        with _ctrl_c_cancels(stop):   # Ctrl+C stops between two chunks; the file being copied is not kept
            result = manager.take_over(new, stop=stop, progress=progress)
    except Exception as e:  # noqa: BLE001 - as the window: a disk that cannot hold the copies (in your language), a
        # file in use by another llm-cli or the GUI, ...: one plain line, never a traceback (a second Ctrl+C ends it)
        console.print(f"[red]{escape(ui_text('hf.takeover.failed', lang, reason=person_text(e, lang)))}[/red]")
        sys.exit(1)
    console.print(escape(ui_text("hf.takeover.result", lang, copied=len(result.copied), total=len(new),
                                 size=format_size(result.bytes_copied, lang))))
    if result.stopped:
        console.print(f"[yellow]{escape(ui_text('hf.takeover.stopped', lang))}[/yellow]")
    for file, code in result.skipped:   # every file that stays, with its reason
        console.print(escape(ui_text("hf.takeover.kept", lang, name=f"{file.repo} / {file.name}",
                                     reason=ui_text(f"hf.takeover.why.{code}", lang))))
    plan = manager.old_copies_plan()
    if plan is not None:
        _remove_old_hf_copies(ctx, manager, plan, lang)


def _remove_old_hf_copies(ctx: FrameworkContext, manager, plan, lang: Optional[str]) -> None:
    """The old copies of the taken-over downloads: the full list, the mode and the yes question ("No" by default,
    confirm_deletion), then exactly the accepted ones go (hf_takeover.delete_old_copies, audited first)."""
    from orchestrator.Core.deletion import PlanChanged
    from orchestrator.utils.localization import ui_text
    accepted = confirm_deletion(plan, title=ui_text("hf.takeover.remove_title", lang),
                                again="llm-cli models take-over")
    if accepted is None:
        return
    trash = None
    if accepted.mode == "trash":
        try:
            trash = _cli_trash(ctx)
        except Exception:  # noqa: BLE001 - no per-user folder: delete_old_copies refuses the trash mode then
            trash = None
    try:
        result = manager.delete_old_copies(accepted, trash=trash, audit=_cli_audit(ctx))
    except PlanChanged:
        console.print(f"[yellow]{escape(ui_text('deletion.changed', lang))}[/yellow]")
        return
    except (PermissionError, RuntimeError) as e:
        console.print(f"[yellow]{escape(ui_text('deletion.failed', lang, reason=str(e)))}[/yellow]")
        return
    key = "deletion.done.trash" if accepted.mode == "trash" else "deletion.done.permanent"
    for name in result.done:
        console.print(f"[green]{escape(ui_text(key, lang, name=name))}[/green]")
    for name, reason in result.failed:
        console.print(f"[yellow]{escape(ui_text('deletion.kept', lang, name=name, reason=reason))}[/yellow]")


# ============================================================================
# MCP SERVER (AI clients such as Claude Code / Claude Desktop / VS Code)
# ============================================================================

@cli.group()
def mcp():
    """Model Context Protocol server for AI clients"""
    pass


@mcp.command('serve')
@click.option('--transport', type=click.Choice(['stdio', 'http']), default='stdio', show_default=True,
              help='stdio: started by the client (default). http: local Streamable HTTP with a named token per client.')
@click.option('--host', default='127.0.0.1', show_default=True, help='HTTP only; loopback addresses only')
@click.option('--port', default=8765, show_default=True, type=int, help='HTTP only')
@click.option('--container', is_flag=True,
              help='HTTP inside a container only: bind the container\'s interfaces (publish the port to the host\'s '
                   'loopback: -p 127.0.0.1:PORT:PORT); a named token and the origin check still apply')
@pass_context
def mcp_serve(ctx: FrameworkContext, transport: str, host: str, port: int, container: bool):
    """Run the MCP server (stdout carries the protocol; logs go to stderr)"""
    import contextlib
    from orchestrator import mcp_server
    from orchestrator.tools.mcp_tokens import ENV_VAR
    mcp_server.no_programs_from_the_working_directory()
    from orchestrator.Core.request_store import set_program
    set_program("mcp")
    # Taken from the environment by the cli() group before anything ran (the token must not be inherited).
    token = getattr(ctx, "mcp_token", None)
    if token is None:
        token = os.environ.pop(ENV_VAR, None)
    if token is not None and transport != 'stdio':
        Console(stderr=True).print(f"[yellow]{ENV_VAR} is ignored over HTTP: each client sends its own "
                                   "token.[/yellow]")
    # Nothing may reach stdout before the transport owns it.
    with contextlib.redirect_stdout(sys.stderr):
        ctx.require_framework()
        registry = getattr(ctx.framework_manager, "tool_registry", None)
        if registry is None:
            Console(stderr=True).print("[red]The tool registry is not available (see the log above).[/red]")
            sys.exit(1)
        policy = registry.policy()
        if registry.policy_store.problem:
            Console(stderr=True).print(f"[red]{escape(registry.policy_store.problem)}[/red]")
        elif not policy.configured:
            Console(stderr=True).print("[yellow]Tool permissions are not set up yet: read-only tools work, "
                                       "everything else asks (or is denied without elicitation). "
                                       "Run 'llm-cli ditto setup'.[/yellow]")
    stdio_token = {"stdio_token": token} if token is not None and transport == 'stdio' else {}
    try:
        mcp_server.serve(ctx.framework_manager, transport=transport, host=host, port=port, **stdio_token,
                         **({"container": True} if container else {}))
    except ValueError as e:
        Console(stderr=True).print(f"[red]{escape(str(e))}[/red]")
        sys.exit(2)


@mcp.command('config')
@click.option('--write', 'write_client', type=click.Choice(['claude-desktop']),
              help='Add the server to this client\'s configuration (other entries are kept, a backup is written)')
@pass_context
def mcp_config(ctx: FrameworkContext, write_client: Optional[str]):
    """Show how to connect Claude Code, Claude Desktop or VS Code to this framework"""
    from orchestrator import mcp_server
    app_root = mcp_server.installation_root()
    try:
        if write_client == 'claude-desktop':
            path = mcp_server.write_claude_desktop_config(app_root)
        else:
            snippets = mcp_server.client_snippets(app_root)
    except (ValueError, OSError) as e:  # OSError: unwritable folder, read-only dotfile target, ...
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    if write_client == 'claude-desktop':
        console.print(f"[green]Added to {escape(str(path))}. Restart Claude Desktop.[/green]")
        return
    # Plain text without panels or wrapping, so the snippets can be copied exactly.
    sections = [("Claude Code (run once in a terminal)", snippets["claude-code"]),
                (f"Claude Desktop ({mcp_server.claude_desktop_config_path()}), "
                 "or: llm-cli mcp config --write claude-desktop", snippets["claude-desktop"]),
                ("VS Code (.vscode/mcp.json)", snippets["vscode"])]
    for title, text in sections:
        click.echo(f"# {title}")
        click.echo(text)
        click.echo("")
    click.echo("Sign in to these clients with your own Claude account or key; the framework never sees it.")


# Named MCP tokens: created, changed and revoked only by a person at a terminal, never by a tool.

def _stdout_is_terminal() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _at_a_terminal(action: str, *, shows_token: bool = False) -> None:
    """Piped input (an agent's shell) is refused, and a token is printed only to a terminal, never into a pipe."""
    if not (_interactive() and (not shows_token or _stdout_is_terminal())):
        where = "stdin and stdout" if shows_token else "stdin"
        Console(stderr=True).print(f"[red]{action} needs a person at a terminal ({where}); it is refused "
                                   "here.[/red]")
        sys.exit(1)


def _token_store(registry):
    from orchestrator.tools.mcp_tokens import TokenStoreError
    if registry.tokens is None:
        Console(stderr=True).print("[red]The MCP token store is not available.[/red]")
        sys.exit(1)
    try:
        registry.tokens.list()  # a damaged or unreadable store is reported before anything else
    except TokenStoreError as e:
        Console(stderr=True).print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    return registry.tokens


def _audit_problem(problem: Optional[str]) -> None:
    if problem:
        Console(stderr=True).print(f"[yellow]Done, but the audit log could not record it: {escape(problem)}[/yellow]")


def _audit_person(registry, event: str, arguments: dict) -> None:
    """A person's change to who may call the tools, in the audit log (actor cli). Names only, never a token."""
    from orchestrator.tools.mcp_tokens import person_change
    _audit_problem(person_change(registry, "cli", event, arguments))


def _policy_summary(policy) -> str:
    from orchestrator.tools.mcp_tokens import policy_summary
    return policy_summary(policy)


def _show_new_token(name: str, token: str, what: str) -> None:
    click.echo(f"{what} for '{name}' (shown only now; the framework keeps only its hash):\n")
    click.echo(token)
    click.echo("\nOver HTTP the client sends 'Authorization: Bearer <token>'. Over stdio, put it into the client's "
               "server entry as the environment variable LCF_MCP_TOKEN.")


@mcp.group('token')
def mcp_token():
    """Named tokens for MCP clients (HTTP, or stdio with LCF_MCP_TOKEN), each with its own permissions"""
    pass


@mcp_token.command('add')
@click.argument('name')
@click.option('--policy-from', 'policy_from',
              help="Start with a copy of another caller's permissions: mcp (local stdio clients), ditto, or a "
                   "token name (mcp:NAME is always a token). Default: read only")
@click.option('--link', type=click.Choice(['ditto']),
              help="Use Ditto's permissions by reference; a change to Ditto's applies at once")
@pass_context
def mcp_token_add(ctx: FrameworkContext, name: str, policy_from: Optional[str], link: Optional[str]):
    """Create a token; it can only read until you allow more (tools permissions --token NAME)"""
    from orchestrator.tools.mcp_tokens import TokenStoreError, UnknownCaller, add_token
    _at_a_terminal("Creating an MCP token", shows_token=True)
    registry = _tool_registry(ctx)
    tokens = _token_store(registry)
    if policy_from and link:
        console.print("[red]Use either --policy-from or --link.[/red]")
        sys.exit(2)
    # --policy-from: "mcp:<name>" (a token's audit actor) is always a token, also one named like a caller ("mcp",
    # "ditto"); a copy, so later changes of the source do not apply (mcp_tokens.copied_policy).
    try:
        token, problem = add_token(registry, name, "cli", policy_from=policy_from, link=link)
    except UnknownCaller as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(2)
    except TokenStoreError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    _audit_problem(problem)
    _show_new_token(name, token, "Token")
    if link:
        click.echo(f"It uses {link}'s permissions; changes to them apply to it at once.")
    else:
        click.echo(f"Permissions: {_policy_summary(tokens.get(name).policy)}. Change them with "
                   f"'llm-cli tools permissions --token {name}'.")


@mcp_token.command('list')
@pass_context
def mcp_token_list(ctx: FrameworkContext):
    """List the tokens (names and permissions; tokens themselves are never shown again)"""
    tokens = _token_store(_tool_registry(ctx))
    records = tokens.list()
    if not records:
        console.print("No MCP tokens. Create one with 'llm-cli mcp token add NAME'.")
        return
    table = Table(title="MCP tokens")
    for column in ("Name", "Audit actor", "Permissions", "Created", "Rotated"):
        table.add_column(column)
    for record in records:
        permissions = f"linked to {record.link}" if record.link else _policy_summary(record.policy)
        table.add_row(record.name, f"mcp:{record.name}", permissions, record.created, record.rotated or "-")
    console.print(table)


@mcp_token.command('rotate')
@click.argument('name')
@pass_context
def mcp_token_rotate(ctx: FrameworkContext, name: str):
    """Replace a token; the old one stops working at once, the permissions stay"""
    from orchestrator.tools.mcp_tokens import TokenStoreError
    _at_a_terminal("Rotating an MCP token", shows_token=True)
    registry = _tool_registry(ctx)
    try:
        token = _token_store(registry).rotate(name)
    except TokenStoreError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    _audit_person(registry, "mcp_token.rotate", {"token": name})
    _show_new_token(name, token, "New token")


@mcp_token.command('revoke')
@click.argument('name')
@pass_context
def mcp_token_revoke(ctx: FrameworkContext, name: str):
    """Delete a token and its permissions; its client loses access at once"""
    from orchestrator.tools.mcp_tokens import TokenStoreError, revoke_token
    _at_a_terminal("Revoking an MCP token")
    registry = _tool_registry(ctx)
    tokens = _token_store(registry)
    if tokens.get(name) is None:
        console.print(f"[red]There is no token named '{escape(name)}'.[/red]")
        sys.exit(1)
    if not click.confirm(f"Revoke the token '{name}'? Its client loses access at once.", default=False):
        return
    try:  # the token, its permissions and its standing approvals, as the GUI does (mcp_tokens.revoke_token)
        result = revoke_token(registry, name, "cli")
    except TokenStoreError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    _audit_problem(result.audit_problem)
    if result.grants_problem:  # a new token of the same name would not inherit them (they name its creation time)
        console.print(f"[yellow]Its standing approvals could not be removed ({escape(result.grants_problem)}); they "
                      "no longer apply.[/yellow]")
    console.print(f"[green]Token '{escape(name)}', its permissions and its standing approvals are deleted.[/green]")


def _token_permissions(registry, name: str, reset: bool) -> None:
    """`tools permissions --token NAME`: the same questions, saved as that token's own permissions."""
    from orchestrator.tools.mcp_tokens import ACTOR_PREFIX, TokenStoreError, read_only_policy, set_token_policy
    _at_a_terminal("Changing a token's permissions")
    tokens = _token_store(registry)
    record = tokens.get(name)
    if record is None:
        console.print(f"[red]There is no token named '{escape(name)}' ('llm-cli mcp token list').[/red]")
        sys.exit(1)
    before = registry.policy(ACTOR_PREFIX + name)
    if record.link and not click.confirm(f"The token '{name}' uses {record.link}'s permissions. Give it its own "
                                         "instead?", default=False):
        return
    # --reset: a new token again (read only, not configured), as the GUI's "Back to read only" (reset_token_policy)
    policy = read_only_policy() if reset else _ask_tool_policy(registry, before,
                                                               shown_for=ACTOR_PREFIX + name)
    try:
        problem = set_token_policy(registry, name, policy, "cli")
    except TokenStoreError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    _audit_problem(problem)
    console.print(f"[green]Token '{escape(name)}': {escape(_policy_summary(policy))}[/green]")


@cli.group()
def deploy():
    """Manage deployments and artifacts"""
    pass

def _output_dir(ctx: FrameworkContext) -> Path:
    """The configured output folder (relative paths: the installation), as builds and the GUI use it."""
    ctx.require_framework()
    fm = ctx.framework_manager
    return fm.resolve_path(fm.config.get("output_dir", "output"))


@deploy.command('list-artifacts')
@pass_context
def list_artifacts(ctx: FrameworkContext):
    """List finished builds (from their manifests): in the output folder, in the framework's build folder (not
    saved yet; 'llm-cli build save' saves one) and in folders you saved them to."""
    from orchestrator.Core.artifact_output import WHERE_TEXT, list_artifacts as listed_artifacts
    output_dir = _output_dir(ctx)
    builds = listed_artifacts(ctx.framework_manager)
    if not builds:
        console.print(f"[yellow]No finished builds in {escape(str(output_dir))} or the framework's build folder."
                      "[/yellow]")
        return

    table = Table(title=f"Finished builds (output folder: {escape(str(output_dir))})")
    table.add_column("Folder", style="bold yellow")
    table.add_column("Where")
    table.add_column("Model")
    table.add_column("Format", style="cyan")
    table.add_column("Quantization")
    table.add_column("Board")
    table.add_column("Size (MB)", justify="right")
    table.add_column("Full path", overflow="fold")
    for item in builds:
        info, folder = item.info, item.path
        size_mb = sum(f.stat().st_size for f in folder.glob('**/*') if f.is_file()) / (1024 * 1024)
        table.add_row(escape(item.label.split(":", 1)[1]), WHERE_TEXT.get(item.where, item.where),
                      escape(str(info.get("model") or info.get("model_source") or "?")),
                      escape(str(info.get("format") or "?")), escape(str(info.get("quantization") or "-")),
                      escape(str(info.get("board") or "-")), f"{size_mb:.2f}", escape(str(folder)))
    console.print(table)

@deploy.command('package')
@click.argument('artifact_name')
@click.option('--profile', required=True, help="Target Hardware Profile (from targets/profiles)")
@click.option('--docker/--no-docker', default=False, help="Include Docker images (Air-Gap)")
@pass_context
def create_package(ctx: FrameworkContext, artifact_name, profile, docker):
    """
    Create a deployment ZIP package.
    Includes artifact, generated deploy.sh, checksums, and optional Docker images.
    """
    ctx.require_framework()
    dep_mgr = ctx.deployment_manager
    if not dep_mgr:
        console.print("[red]Deployment Manager not loaded.[/red]")
        sys.exit(1)
        
    output_dir = _output_dir(ctx)
    # A folder from 'deploy list-artifacts' (relative to the output folder or the framework's build folder) or any
    # existing path; the same core step as the GUI's "Create package only..." (make_package).
    from orchestrator.Core.deployment_manager import DeployError, make_package
    try:
        with console.status(f"Generiere Paket für {artifact_name}...", spinner="dots"):
            pkg_path = make_package(dep_mgr, ctx.framework_manager, artifact_name, profile, docker, output_dir)
    except DeployError as e:
        console.print(f"[bold red]Package generation failed: {escape(str(e))}[/bold red]")
        if e.next:
            console.print(escape(e.next))
        sys.exit(1)
    console.print(f"[bold green]Package created successfully![/bold green]")
    console.print(f"Path: {pkg_path}")


# `--password` without a value: asked with hidden input. A value on the command line still works for scripts, but
# every user sees it in the process list, and it stays in the shell history.
ASK_PASSWORD = "\0ask"


def device_password_options(what: str):
    """--password [VALUE] and --password-stdin of the three commands that log in to a device once with a password."""
    def add(command):
        command = click.option('--password-stdin', 'password_stdin', is_flag=True,
                               help="Read the password from standard input (one line, e.g. piped from a password "
                                    "manager)")(command)
        return click.option('--password', default=None, is_flag=False, flag_value=ASK_PASSWORD, metavar='[VALUE]',
                            help=f"{what}: used once to install the framework's SSH key, never stored. Without a "
                                 "value it is asked with hidden input; a value here is deprecated (visible in the "
                                 "process list and your shell history)")(command)
    return add


def _device_password(password: Optional[str], from_stdin: bool, ask_when_omitted: bool = False) -> Optional[str]:
    """The one-time device password, or None (the framework's key logs in). Asked with hidden input for --password
    without a value (and, with ask_when_omitted, on a terminal when nothing was given); one line of a piped stdin
    for --password-stdin; a value on the command line still works but warns."""
    if from_stdin:
        if password is not None:
            raise click.UsageError("Use either --password or --password-stdin, not both.")
        if sys.stdin.isatty():
            raise click.UsageError("--password-stdin reads the password from a pipe; on a terminal use --password "
                                   "without a value (it is asked with hidden input).")
        line = sys.stdin.readline().rstrip("\r\n")
        if not line:
            raise click.UsageError("No password on standard input.")
        return line
    if password == ASK_PASSWORD:
        return click.prompt("Password", hide_input=True)
    if password is None:
        if ask_when_omitted and sys.stdin.isatty():
            return click.prompt("Password (Enter if the framework key is already installed)",
                                default="", hide_input=True, show_default=False) or None
        return None
    _say("The password is visible in the process list and your shell history; use --password without a value or "
         "--password-stdin.", err=True, fg="yellow")
    return password


@deploy.command('run')
@click.argument('package_path', type=click.Path(exists=True))
@click.option('--host', '--ip', 'host', required=True,
              help="Address or host name of the device, e.g. 192.168.1.50 or myboard.local (--ip is the old name)")
@click.option('--user', required=True, help="Target Username")
@click.option('--port', default=22, show_default=True, type=int)
@device_password_options("Only for the first connection (asked when omitted on a terminal)")
@click.option('--trust-fingerprint', default=None,
              help="Trust a new device non-interactively if its host key has this SHA256 fingerprint")
@click.option('--label', default=None, help="Your name for the device (shown to you and to AI tools; default: the "
              "label it already has, or its address)")
@click.option('--wait', 'wait_seconds', default=300, show_default=True, type=click.IntRange(0, 86400),
              help="When the network drops during the deploy (a Wi-Fi board), wait this many seconds for the board "
                   "to answer again; the upload then continues where it stopped")
@pass_context
def run_deploy(ctx: FrameworkContext, package_path, host, user, port, password, password_stdin, trust_fingerprint,
               label, wait_seconds):
    """
    Upload and execute a deployment package on the target (its address or host name).
    """
    from orchestrator.runtime.device import reasons
    from orchestrator.remote.address import valid_host
    from orchestrator.utils import ssh_trust
    if not valid_host(host):
        raise click.UsageError("--host takes an address or a host name, e.g. 192.168.1.50, fe80::1 (no brackets) or "
                               "myboard.local")
    password = _device_password(password, password_stdin)  # read before any question: stdin may be the pipe
    ctx.require_framework()
    dep_mgr = ctx.deployment_manager
    if not dep_mgr:
        console.print("[red]Deployment Manager not loaded.[/red]")
        sys.exit(1)

    # Trust on first use, never silently (the one decision of remote/pairing.py first_use).
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.trust import KeyCheck
    try:
        status, key = dep_mgr.host_key_status(host, port)
    except Exception as e:
        _unreachable(ctx, host, port, e, step="deploy")
    store = _deploy_trust_store(ctx, dep_mgr) if status == "unknown" else None   # a store only to trust a key
    outcome = _first_use(ctx, store, KeyCheck.of(Endpoint(host, port), key, status), trust_fingerprint,
                         nothing="deployed")
    if outcome.verdict == "changed":
        _note_reach(ctx, host, port, "deploy", reasons.HOST_KEY_CHANGED)
        _print_reason(ctx, reasons.HOST_KEY_CHANGED, host, port)
        console.print(f"[bold red]{escape(str(ssh_trust.HostKeyChanged(host, port)))}[/bold red]")
        sys.exit(1)
    if password is None and not password_stdin:
        password = _device_password(None, False, ask_when_omitted=True)

    import threading
    stop = threading.Event()
    with _ctrl_c_cancels(stop), console.status(f"Deploying to {user}@{host}...", spinner="earth") as shown:
        result = dep_mgr.deploy_package(Path(package_path), host, user, port=port, password=password,
                                        wait_seconds=wait_seconds, cancelled=stop.is_set,
                                        on_event=lambda event: shown.update(_deploy_event_text(ctx, event, host)))

    if result.success:
        console.print("[bold green]✅ Deployment Execution Successful![/bold green]")
        if result.resumed:
            console.print(f"The network dropped {result.resumed} time(s); the deploy waited {result.waited_s:.0f} s "
                          "for the board and continued where it stopped.")
        for note in result.notes:  # e.g. an old container on the board that only you remove
            console.print(f"[yellow]{escape(note)}[/yellow]")
        # Trusted by you and logged in to with the framework's key: AI tools may now name it (deploy_to_device).
        _remember_device(ctx, host, port, user, key, paired=True, label=label)
    else:
        console.print("[bold red]❌ Deployment Failed.[/bold red]")
        if result.code:
            _print_reason(ctx, result.code, host, port)
        if result.reason:
            console.print(escape(result.reason))
        for line in result.output[-20:]:
            console.print(f"  [dim]{escape(line)}[/dim]")
        sys.exit(1)


def _deploy_trust_store(ctx: FrameworkContext, dep_mgr):
    """The known_hosts the deploy logs in with (the deployment manager's folder; else the command's)."""
    from orchestrator.remote.trust import TrustStore
    make = getattr(dep_mgr, "trust_store", None)
    return make() if callable(make) else TrustStore(_state_dir(ctx))


def _first_use(ctx: Optional[FrameworkContext], store, check, trust_fingerprint: Optional[str], *, nothing: str):
    """The CLI's prompt for trust on first use (remote/pairing.py first_use decides): an unknown key is shown with its
    fingerprint and trusted when it has --trust-fingerprint or the person says yes at a terminal ("No" is the default);
    the trust is written to the audit log. Exits when it is refused; returns the decision (known, trusted, changed)."""
    from orchestrator.remote import pairing
    if check.status == "unknown":
        console.print(f"New device {escape(check.endpoint.host)}:{check.endpoint.port} - {escape(check.key_type)} "
                      f"{check.fingerprint}")

    def ask(_check) -> bool:
        return _interactive() and click.confirm(
            "Compare with 'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub' on the device. Trust it?", default=False)

    audit = (lambda: _device_audit(ctx, store.state_dir)) if ctx is not None else None
    outcome = pairing.first_use(store, check, ask=ask, preset_fingerprint=trust_fingerprint, via="cli", audit=audit,
                                actor="cli")
    if outcome.verdict == "mismatch":
        console.print("[red]The fingerprint does not match --trust-fingerprint.[/red]")
        sys.exit(1)
    if outcome.verdict == "declined":
        console.print(f"[red]Device not trusted; nothing was {nothing}.[/red]")
        sys.exit(1)
    return outcome


class _ctrl_c_cancels:
    """While a deploy runs, Ctrl+C sets `stop` (the deploy's own cancel) instead of ending the CLI at once: the wait
    for the board ends, and the result says what happened on the board (deploy.sh started or not). A second Ctrl+C
    stops at once. Without a main thread to catch the signal, Ctrl+C keeps its usual meaning."""

    def __init__(self, stop):
        self.stop, self.before, self.installed = stop, None, False

    def _caught(self, _signum, _frame) -> None:
        if self.stop.is_set():
            raise KeyboardInterrupt
        self.stop.set()

    def __enter__(self):
        import signal
        try:
            self.before = signal.signal(signal.SIGINT, self._caught)
            self.installed = True
        except ValueError:  # not the main thread
            pass
        return self

    def __exit__(self, *exc) -> bool:
        import signal
        if self.installed:
            signal.signal(signal.SIGINT, self.before if self.before is not None else signal.default_int_handler)
        return False


def _kv_text(ctx: Optional[FrameworkContext], choice) -> str:
    """The KV-cache type of a board's server and why (DeploySettings.kv_choice), in the configured language."""
    from orchestrator.utils.localization import ui_text
    lang = _config_language(ctx)
    if not choice.automatic:
        return ui_text("device.kv.chosen", lang, kind=choice.kind)
    why = ui_text(f"device.kv.why_{choice.why}", lang, free=choice.free_gib)
    return ui_text("device.kv.auto", lang, kind=choice.kind, why=why)


def _config_language(ctx: Optional[FrameworkContext]) -> Optional[str]:
    """The interface language of the settings (en/de), read without booting the framework; else the default."""
    try:
        value = _settings(ctx)[0].get("language") if ctx is not None else None
    except Exception:  # noqa: BLE001 - a broken setting never stops a deploy's message
        value = None
    return value if value in ("en", "de") else None


def _print_reason(ctx: Optional[FrameworkContext], code: str, host: str, port: int) -> None:
    """Why the board cannot be reached or logged in to, and what to do about it (eighth round N6)."""
    from orchestrator.runtime.device import reasons
    lang = _config_language(ctx)
    console.print(f"[red]{escape(reasons.person_title(code, lang, address=host, port=port))}[/red] ({code})")
    console.print(escape(reasons.person_next(code, lang, address=host, port=port)))


def _note_reach(ctx: Optional[FrameworkContext], host: str, port: int, step: str, code: str,
                device_id: str = "", state_dir: Optional[Path] = None) -> None:
    """Records for Mojo what this contact with a trusted board found (W1: Mojo reads it instead of contacting the
    board): a reason code keeps the problem, "" (the board was reached and logged in to) ends it. `state_dir`: the
    state folder the command works in (else the context's). Never fails (trusted.record_reach)."""
    if not step:
        return
    from orchestrator.remote.address import Endpoint
    from orchestrator.runtime.device.trusted import record_reach
    try:
        folder = state_dir or (_state_dir(ctx) if ctx is not None else _default_state_dir())
    except Exception:  # noqa: BLE001 - bookkeeping never changes what the command does
        return
    record_reach(folder, step, code, device_id=device_id, endpoint=Endpoint(host, port))


def _default_state_dir() -> Path:
    from orchestrator.runtime.profile import default_state_dir
    return default_state_dir()


def _unreachable(ctx: Optional[FrameworkContext], host: str, port: int, error: BaseException,
                 doing: str = "reach", step: str = "", device_id: str = "", state_dir: Optional[Path] = None) -> None:
    """Exits after a failed connection or login: its reason code with the next step when it has one (recorded for
    Mojo with the board, in `state_dir`, when `step` names the contact)."""
    from orchestrator.remote import reasons
    code = reasons.reach_code(error)
    if code:
        _note_reach(ctx, host, port, step, code, device_id, state_dir)
        _print_reason(ctx, code, host, port)
        if code == reasons.LOCAL_PROBLEM:   # this computer's own words: which file, and why
            console.print(escape(str(error)))
    else:
        console.print(f"[red]Could not {doing} {escape(host)}:{port}: {escape(str(error))}[/red]")
    sys.exit(1)


def _deploy_event_text(ctx: FrameworkContext, event, host: str) -> str:
    """The console line of a running deploy: the upload's progress, or the wait for the board (N5), with the key that
    cancels a wait (remote/text.py event_text)."""
    from orchestrator.remote.text import event_text
    text = event_text(event, host, _config_language(ctx))
    return escape(text + " (Ctrl+C)" if event.kind == "waiting" else text)


def _device_audit(ctx: FrameworkContext, state_dir: Path):
    """The tool audit log for a person's device decisions, also when the framework is not booted (scan --device)."""
    fm = ctx.framework_manager if getattr(ctx, "_initialized", False) else None
    audit = getattr(getattr(fm, "tool_registry", None), "audit", None)
    if audit is not None:
        return audit
    try:
        from orchestrator.tools.audit import AuditLog
        from orchestrator.Core.vault_key import audit_key
        return AuditLog(state_dir / "audit.db", key=audit_key(None, state_dir))
    except Exception:  # noqa: BLE001 - the record is still written; the trust itself was the person's
        return None


def _remember_device(ctx: FrameworkContext, host: str, port: int, user: str, key, *, paired: bool,
                     label: Optional[str] = None):
    """Writes the trusted device's record (MCP design 2.7: only the GUI and the CLI write one, after the person
    trusted the device's host key and the framework logged in to it) and audits it."""
    from orchestrator.runtime.device.trusted import record_trusted_device
    state_dir = _state_dir(ctx)
    device = record_trusted_device(state_dir, host, port, user, key, via="cli", paired=paired, label=label,
                                   audit=_device_audit(ctx, state_dir), actor="cli")
    _say_trusted(device)
    return device


def _say_trusted(device) -> None:
    if device is not None:
        console.print(f"Trusted device {device.device_id} ({escape(device.label)}): AI tools may deploy to it with "
                      "your confirmation (llm-cli deploy devices list).")


@deploy.group('devices')
def deploy_devices():
    """Devices you trusted and paired: the only devices AI tools may deploy to (each deploy is still confirmed)"""
    pass


@deploy_devices.command('list')
@click.option('--json', 'as_json', is_flag=True, help='Machine-readable output')
@pass_context
def deploy_devices_list(ctx: FrameworkContext, as_json: bool):
    """List the trusted devices with their host key and whether the key is still the trusted one"""
    from orchestrator.runtime.device.person_steps import trusted_rows
    rows = trusted_rows(_state_dir(ctx))
    if as_json:
        click.echo(json.dumps(rows, indent=2, sort_keys=True))
        return
    if not rows:
        console.print("No trusted device yet: 'llm-cli deploy run', 'llm-cli scan --device' or the GUI's Deployment "
                      "window adds one after you confirmed its host key; 'llm-cli deploy devices add' adds a device "
                      "you trusted before.")
        return
    table = Table(title="Trusted devices")
    for column in ("Device", "Label", "Address", "User", "Host key", "Paired", "Last deploy"):
        table.add_column(column)
    for r in rows:
        state = {"known": "trusted", "changed": "[red]CHANGED in known_hosts[/red]",
                 "unknown": "[yellow]not in known_hosts[/yellow]"}[r["host_key"]]
        last_deploy = r["last_deploy"] or {}   # may hold only a recorded contact for Mojo: then no deploy is shown
        table.add_row(r["device_id"], escape(r["label"]), escape(r["address"]), escape(r["user"]),
                      f"{escape(r['key_type'])} {r['fingerprint']} ({state})", "yes" if r["paired"] else "no",
                      escape(f"{last_deploy.get('at')} ({'ok' if last_deploy.get('ok') else 'failed'})")
                      if last_deploy.get("at") else "-")
    console.print(table)


@deploy_devices.command('remove')
@click.argument('device_id')
@pass_context
def deploy_devices_remove(ctx: FrameworkContext, device_id: str):
    """Remove a device's record: AI tools can no longer name it (its known_hosts entry is kept)"""
    from orchestrator.runtime.device.person_steps import remove_trust
    state_dir = _state_dir(ctx)
    if not remove_trust(state_dir, device_id, audit=lambda: _device_audit(ctx, state_dir), actor="cli",
                        channel="framework-cli"):
        console.print(f"[red]No trusted device {escape(device_id)} (llm-cli deploy devices list).[/red]")
        sys.exit(1)
    console.print(f"Device {escape(device_id)} removed from the trusted devices.")


@deploy_devices.command('add')
@click.argument('host')
@click.option('--user', required=True, help="The account on the device that deploys run as")
@click.option('--port', default=22, show_default=True, type=click.IntRange(1, 65535))
@click.option('--label', default=None, help="Your name for the device (default: its profile label or address)")
@pass_context
def deploy_devices_add(ctx: FrameworkContext, host: str, user: str, port: int, label: Optional[str]):
    """Add a device you trusted before (its host key is already in the framework's known_hosts). Needs a terminal:
    you confirm the fingerprint shown; nothing is trusted here that was not trusted before."""
    from orchestrator.runtime.device import person_steps
    from orchestrator.runtime.device.trusted import TrustedDevices
    if not _interactive():
        console.print("[red]'deploy devices add' needs a terminal: you confirm the device's fingerprint yourself."
                      "[/red]")
        sys.exit(1)
    try:
        label = person_steps.check_board_entry(host, user, label)
    except person_steps.BoardStepError as e:
        raise click.UsageError("HOST must be an address or a host name (e.g. 192.168.1.50, fe80::1 without brackets "
                               "or myboard.local) and --user an account name" if e.kind == "host_user" else str(e))
    state_dir = _state_dir(ctx)
    try:
        key, status, fp = person_steps.read_host_key(state_dir, host, port)
    except person_steps.BoardStepError as e:
        if e.kind == "no_paramiko":
            console.print(f"[red]{escape(str(e))}[/red]")
            sys.exit(1)
        _unreachable(ctx, host, port, e.error)
    if status != "known":
        console.print(f"[red]{escape(str(person_steps.not_known_error(host, port, status)))}[/red]")
        sys.exit(1)
    console.print(f"{escape(host)}:{port} presents the trusted key {escape(key.get_name())} {fp}.")
    if not click.confirm(f"Allow AI tools to deploy to {user}@{host} (each deploy is still confirmed by you)?",
                         default=False):
        console.print("Nothing was added.")
        return
    device, paired = person_steps.add_trusted_board(state_dir, host, port, user, key, label=label, via="cli",
                                                    audit=_device_audit(ctx, state_dir), actor="cli")
    _say_trusted(device)
    if device is None:
        console.print("[red]The device record could not be written.[/red]")
        sys.exit(1)
    if not paired:
        console.print("[yellow]The framework's SSH key does not log in there yet: pair the device once with "
                      "'llm-cli deploy run' (it asks for the password one time).[/yellow]")
    elif TrustedDevices(state_dir).key_status(device) != "known":
        console.print("[yellow]The trusted key is not in the framework's known_hosts.[/yellow]")


@deploy_devices.command('edit')
@click.argument('device')
@click.option('--host', default=None,
              help="The board's new address or host name, e.g. 192.168.1.51 or myboard.local (default: it stays)")
@click.option('--port', default=None, type=click.IntRange(1, 65535),
              help="The board's new SSH port (default: it stays)")
@click.option('--key', 'key_text', default=None,
              help="The board's new SSH host key: paste its line, e.g. of /etc/ssh/ssh_host_ed25519_key.pub on the "
                   "board (in quotes; default: the key stays)")
@click.option('--read-key', is_flag=True,
              help="Read the board's new host key from the board at its (new) address instead of pasting it")
@pass_context
def deploy_devices_edit(ctx: FrameworkContext, device: str, host: Optional[str], port: Optional[int],
                        key_text: Optional[str], read_key: bool):
    """Change a trusted board's address (host name or IP address, port) or its SSH host key in the framework's
    known_hosts, e.g. after its address changed or it was reinstalled. Needs a terminal: you compare the old and the
    new fingerprint ("No" is the default); the change is written to the audit log."""
    from orchestrator.runtime.device import person_steps
    from orchestrator.runtime.device.trusted import TrustedDevices, find_boards
    if key_text is not None and read_key:
        raise click.UsageError("Give either --key or --read-key.")
    if host is None and port is None and key_text is None and not read_key:
        raise click.UsageError("Give the new --host, --port, --key or --read-key.")
    if not _interactive():
        console.print("[red]'deploy devices edit' needs a terminal: you compare the old and the new fingerprint "
                      "yourself.[/red]")
        sys.exit(1)
    state_dir = _state_dir(ctx)
    found = find_boards(TrustedDevices(state_dir), device, by_address=True)
    if len(found) != 1:
        console.print(f"[red]No trusted board {escape(device)} (llm-cli deploy devices list).[/red]" if not found else
                      f"[red]{escape(device)} names several trusted boards "
                      f"({', '.join(d.device_id for d in found)}); give its device ID.[/red]")
        sys.exit(1)
    try:
        change = person_steps.plan_board_change(state_dir, found[0].device_id, host=host, port=port,
                                                key_text=key_text, read_key=read_key)
    except person_steps.BoardStepError as e:
        if e.kind == "unreachable":
            _unreachable(ctx, (host or found[0].host), port or found[0].port, e.error, doing="read the key of")
        if e.kind == "address":
            raise click.UsageError("--host takes an address or a host name, e.g. 192.168.1.50, fe80::1 (no brackets) "
                                   "or myboard.local")
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    for line in person_steps.change_lines(change):
        console.print(escape(line))
    if not click.confirm(person_steps.CHANGE_QUESTION, default=False):
        console.print("Nothing was changed.")
        return
    try:
        moved, paired = person_steps.change_board(state_dir, change, via="cli",
                                                  audit=lambda: _device_audit(ctx, state_dir), actor="cli")
    except person_steps.BoardStepError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    plan = change.plan
    if moved is None:
        console.print("[red]The framework's known_hosts holds the change, but the board's record could not be "
                      "written: add the board again with 'llm-cli deploy devices add'.[/red]")
        sys.exit(1)
    console.print(f"Board {moved.device_id} ({escape(moved.label)}): {escape(plan.new.display)}, host key "
                  f"{escape(plan.new_key_type)} {plan.new_fingerprint}.")
    if plan.key_changed and not paired:
        console.print("[yellow]The framework's SSH key does not log in there yet: pair the board once with "
                      "'llm-cli deploy run' (it asks for the password one time).[/yellow]")


# ============================================================================
# SCAN COMMAND
# ============================================================================

def _profile_summary(profile) -> list:
    """Lines for the console: what the probe found (values from a device are untrusted: escaped)."""
    def value(key, default="unknown"):
        return escape(profile.get(key) or default)

    lines = [f"System: {value('OS')}/{value('ARCH')}, {value('OS_PRETTY_NAME', profile.get('OS_ID') or 'unknown')}, "
             f"C library {value('LIBC')} {value('LIBC_VERSION', '')}".rstrip(),
             f"CPU: {value('CPU_MODEL')} ({value('CPU_CORES_LOGICAL')} logical cores), RAM {value('RAM_TOTAL_MB')} MiB"]
    gpus = [escape(g.get("NAME") or g.get("VENDOR", "GPU")) for g in profile.gpus()]
    lines.append("GPU: " + ("; ".join(gpus) if gpus else "none found"))
    if profile.get("NPU_VENDOR"):
        lines.append(f"NPU: {value('NPU_VENDOR')} {value('NPU_MODEL', '')}".rstrip())
    if profile.warnings:
        lines.append("Notes: " + escape(", ".join(profile.warnings)))
    return lines


def _trust_device(state_dir: Path, host: str, port: int, trust_fingerprint: Optional[str],
                  ctx: Optional[FrameworkContext] = None):
    """The device's host key, trusted on first use but never silently (as for deploy run). Exits when refused."""
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.trust import TrustStore
    from orchestrator.utils import ssh_trust
    store = TrustStore(state_dir)
    try:
        check = store.check(Endpoint(host, port))
    except Exception as e:  # unreachable, no SSH server, a broken known_hosts file
        _unreachable(ctx, host, port, e, step="probe", state_dir=state_dir)
    if _first_use(ctx, store, check, trust_fingerprint, nothing="probed").verdict == "changed":
        _note_reach(ctx, host, port, "probe", "device_host_key_changed", state_dir=state_dir)
        console.print(f"[bold red]{escape(str(ssh_trust.HostKeyChanged(host, port)))}[/bold red]")
        sys.exit(1)
    return check.key


def _scan_device(store, state_dir: Path, output_file: Path, as_json: bool, host: str, user: Optional[str], port: int,
                 password: Optional[str], trust_fingerprint: Optional[str], label: Optional[str],
                 ctx: Optional[FrameworkContext] = None) -> None:
    from orchestrator.remote import transport
    from orchestrator.remote.address import valid_host, valid_user
    from orchestrator.runtime.scan_steps import USER_HINT, DeviceScanError, label_problem, scan_device
    if not user:
        raise click.UsageError("--device needs --user")
    if not valid_user(user, allow_root=True):
        raise click.UsageError(f"--user: {USER_HINT}")
    if not valid_host(host):
        raise click.UsageError("--device takes an address or a host name, e.g. 192.168.1.50, fe80::1 (no brackets) "
                               "or myboard.local")
    if not 1 <= port <= 65535:
        raise click.UsageError("--port must be between 1 and 65535")
    if label_problem(label):
        raise click.UsageError("--label takes 1 to 64 printable characters")
    if not transport.available()[0]:   # a real import: a broken installation counts as missing
        console.print("[red]Probing a device needs the 'paramiko' package.[/red]")
        sys.exit(1)
    key = _trust_device(state_dir, host, port, trust_fingerprint, ctx)
    if password is None:
        password = _device_password(None, False, ask_when_omitted=True)
    from orchestrator.remote.text import wait_lines
    # The probe's session waits for a board whose network dropped (N5): said as the device runtime commands and the
    # Builder tab's scan say it, with the key that ends it (Ctrl+C ends the command)
    lines = wait_lines(lambda text: console.print(escape(text)), host, _config_language(ctx), hint=" (Ctrl+C)")
    # The same core step as the GUI's Builder tab > Scan / Import... > Remote Scan via SSH... (scan_steps.scan_device):
    # login (recorded for Mojo), probe, profile file, profile store, deploy user and the trusted, paired record.
    try:
        with console.status(f"Running the hardware probe on {escape(host)}...", spinner="dots"):
            scan = scan_device(state_dir, host, port, user, key, password=password, label=label,
                               output_file=output_file, as_json=as_json, store=store, record=ctx is not None,
                               via="cli", actor="cli",   # the audit log is opened only for the record, as before
                               audit_factory=(lambda: _device_audit(ctx, state_dir)) if ctx is not None else None,
                               on_event=lines)
    except DeviceScanError as e:
        if e.kind == "login":   # recorded for Mojo by the core step already
            _unreachable(ctx, host, port, e.error, doing=f"log in to {user}@", state_dir=state_dir)
        elif e.kind == "probe" and e.reason and getattr(e.error, "kind", "") == "wait":
            # the board did not answer again after its network dropped: the reason in plain words, as `deploy run`
            _board_step_failed(ctx, e.error, host, port)
        elif e.kind == "probe":
            console.print(f"[red]The probe on {escape(host)} failed: {escape(str(e.error))}[/red]")
        else:
            console.print(f"[red]Could not save the profile: {escape(str(e.error))}[/red]")
        sys.exit(1)
    if scan.trusted is not None:
        console.print(f"Trusted device {scan.trusted.device_id} ({escape(scan.trusted.label)}): AI tools may deploy "
                      "to it with your confirmation (llm-cli deploy devices list).")
    tool = "Python probe" if scan.tool == "python" else "shell probe"
    console.print(f"[bold green]Probe of {escape(host)} successful ({tool}). Config generated: "
                  f"{escape(str(output_file))}[/bold green]")
    for note in scan.notes:
        console.print(f"[yellow]{escape(note)}[/yellow]")
    for line in _profile_summary(scan.profile):
        console.print(line)
    console.print(f"Stored as device {scan.device_id}: {escape(str(scan.stored))}")


@cli.command('scan')
@click.option('--output', default="target_hardware_config.txt", show_default=True,
              help="File the profile is written to (relative to the current folder)")
@click.option('--json', 'as_json', is_flag=True, help="Write the JSON view instead of KEY=VALUE lines")
@click.option('--script', 'use_script', is_flag=True,
              help="Use the fallback script (hardware_probe.sh, on Windows hardware_probe.ps1) instead of the "
                   "Python probe")
@click.option('--device', default=None, help="Probe this device over SSH instead of this computer (its address or "
              "host name, e.g. 192.168.1.50 or myboard.local)")
@click.option('--user', default=None, help="SSH user on the device (with --device)")
@click.option('--port', default=22, show_default=True, type=int, help="SSH port of the device (with --device)")
@device_password_options("With --device, only for the first connection (asked when omitted on a terminal)")
@click.option('--trust-fingerprint', default=None,
              help="Trust a new device non-interactively if its host key has this SHA256 fingerprint")
@click.option('--label', default=None, help="Your name for the device in the profile store (with --device or "
              "--import)")
@click.option('--import', 'import_file', default=None, type=click.Path(exists=True, dir_okay=False),
              help="Import a board's probe file (target_hardware_config.txt copied from the board) into the probe "
                   "store and print its probe id, which AI clients and Ditto use")
@pass_context
def run_scan(ctx: FrameworkContext, output, as_json, use_script, device, user, port, password, password_stdin,
             trust_fingerprint, label, import_file):
    """Run the hardware probe on this computer, or with --device on a board over SSH, and store the profile
    (exit code 1 if the probe fails or writes no file). With --import, store a board's probe file instead."""
    from orchestrator.runtime.probe_service import HardwareProbes, ProbeError
    from orchestrator.runtime.profile import ProfileStore, default_state_dir
    config, _ = _settings(ctx)
    state_dir = Path(getattr(config, "user_state_dir", None) or default_state_dir())
    store = ProfileStore(state_dir)
    probes = HardwareProbes(store=store)
    output_file = Path(output).expanduser().resolve()
    if import_file and device:
        raise click.UsageError("--import and --device exclude each other")
    if import_file:
        _scan_import(ctx, probes, import_file, label)
        return
    if device:
        password = _device_password(password, password_stdin)
        _scan_device(store, state_dir, output_file, as_json, device, user, port, password, trust_fingerprint, label,
                     ctx=ctx)
        return

    from orchestrator.runtime.scan_steps import ProfileWriteError, scan_local
    try:
        with console.status("Running the hardware probe...", spinner="dots"):
            # The same core step as the GUI's Builder tab > Scan / Import... > Scan This Computer (scan_local): the
            # script writes the --output file itself, the Python probe's result is written there by the step.
            scan = scan_local(probes, output_file, as_json=as_json, use_script=use_script)
    except ProbeError as e:
        console.print(f"[red]Probe execution failed: {escape(str(e))}[/red]")
        if e.detail:  # the underlying error for the person (it may name a path, so it is not part of the message)
            console.print(f"[dim]{escape(e.detail)}[/dim]")
        if e.next:
            console.print(escape(e.next))
        sys.exit(1)
    except ProfileWriteError as e:
        console.print(f"[red]The profile could not be written to {escape(str(output_file))}: "
                      f"{escape(str(e))}[/red]")
        sys.exit(1)
    for note in scan.notes:
        console.print(f"[yellow]{escape(note)}[/yellow]")
    console.print(f"[bold green]Probe successful ({scan.how}). Config generated: {escape(str(output_file))}"
                  "[/bold green]")
    for line in _profile_summary(scan.profile):
        console.print(line)
    if scan.needs_reprobe:
        console.print("[yellow]This profile lacks facts a build needs; run the probe again before a build.[/yellow]")
    console.print(f"Stored as this computer's profile (probe id local): {escape(str(scan.local_path))}")


def _scan_import(ctx: FrameworkContext, probes, import_file, label: Optional[str]) -> None:
    """Copies a board's probe file, validated and without its host name, into the probe store and prints its probe
    id (MCP design T5: AI clients and Ditto name a board's probe only by that id, never by a path)."""
    from orchestrator.runtime.probe_service import ProbeError
    from orchestrator.runtime.scan_steps import import_probe, label_problem
    if label_problem(label):
        raise click.UsageError("--label takes 1 to 64 printable characters")
    try:  # the targets only, without booting the framework: the match is a hint
        from types import SimpleNamespace
        from orchestrator.Core.framework import resolve_install_path
        from orchestrator.Core.target_manager import TargetManager
        config, _ = _settings(ctx)
        target_manager = TargetManager(SimpleNamespace(config=config, resolve_path=resolve_install_path))
    except Exception:  # noqa: BLE001
        target_manager = None
    try:  # the same core step as the GUI's Builder tab > Scan / Import... > Import from File (scan_steps.import_probe)
        imported = import_probe(probes, import_file, label, target_manager)
    except ProbeError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        if e.next:
            console.print(escape(e.next))
        sys.exit(1)
    probe_id, profile, info, match = imported.probe_id, imported.profile, imported.info, imported.match
    console.print(f"[bold green]Imported as probe {probe_id} ({escape(info.label)}).[/bold green]")
    for line in _profile_summary(profile):
        console.print(line)
    if profile.needs_reprobe:
        console.print("[yellow]This probe was converted from an older probe file and lacks facts; probe the board "
                      "again with the current script when you can.[/yellow]")
    if match is not None:
        console.print(f"Matching target: {escape(match.target)}" + (f", board {escape(match.board)}" if match.board
                                                                   else "") + f" ({escape(match.reason)}).")
    else:
        console.print("No target matches this probe yet: an AI client or Ditto can propose a module for it "
                      f"(MCP prompt add_board with probe_id {probe_id}).")


# ============================================================================
# DEVICES AND THEIR RUNTIME (self-built llama.cpp on a board)
# ============================================================================

def _utc_stamp() -> str:
    from datetime import timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _state_dir(ctx: FrameworkContext) -> Path:
    from orchestrator.runtime.profile import default_state_dir
    config, _ = _settings(ctx)
    return Path(getattr(config, "user_state_dir", None) or default_state_dir())


def _device_context(ctx: FrameworkContext, name: str):
    """(state dir, profile store, device ID, stored profile) of a stored device; exits with 1 when it is unknown."""
    from orchestrator.runtime.device.devices import DeviceNotFound, resolve_device
    from orchestrator.runtime.profile import ProfileError, ProfileStore
    state_dir = _state_dir(ctx)
    store = ProfileStore(state_dir)
    try:
        device_id = resolve_device(store, name)
        profile = store.load_device(device_id)
    except (DeviceNotFound, ProfileError) as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    if profile is None:
        console.print(f"[red]Device {device_id} has no stored profile: probe it again with llm-cli scan --device."
                      "[/red]")
        sys.exit(1)
    return state_dir, store, device_id, profile


def _deploy_settings(store, device_id: str):
    from orchestrator.runtime.device.settings import DeploySettings, SettingsError
    try:
        return DeploySettings.from_dict(store.load_deploy_settings(device_id))
    except (SettingsError, ValueError) as e:
        console.print(f"[red]The deploy settings of device {device_id} cannot be used: {escape(str(e))}[/red]")
        sys.exit(1)


def _record_consent(ctx: FrameworkContext, tool: str, arguments: Dict[str, Any], detail: str) -> None:
    """Writes a consent to the audit log; without its record there is no consent (exit 1, nothing changed)."""
    from orchestrator.runtime.device.person_steps import record_consent
    registry = _tool_registry(ctx)
    try:
        record_consent(getattr(registry, "audit", None), tool, arguments, detail, channel="framework-cli")
    except Exception as e:
        console.print(f"[red]The consent could not be written to the audit log ({escape(str(e))}); nothing was "
                      "changed.[/red]")
        sys.exit(1)


def _device_plan(profile):
    from orchestrator.runtime.build.recipe import PlanError
    from orchestrator.runtime.device.steps import plan_runtime
    try:
        return plan_runtime(profile)
    except PlanError as e:
        console.print(f"[red]No device runtime can be planned for this device: {escape(str(e))}[/red]")
        sys.exit(1)


def _connect_device(state_dir: Path, device_id: str, host: str, port: int, user: str, password: Optional[str],
                    ctx: Optional[FrameworkContext] = None, step: str = ""):
    """The board session with the stored device (remote/session.py RemoteSession): its host key must be the trusted
    one the device was probed with (the device ID is derived from it), so a deployment never reaches another machine
    at the same address; when the board's network drops, the session waits for it and says so on the console (N5).
    `step` names the contact for Mojo's record of the board (W1), kept in `state_dir`. A verify only checks: it never
    rewrites the board's trust record (its --ssh-user may name another account than the one AI tools deploy as)."""
    from orchestrator.remote.text import wait_lines
    from orchestrator.runtime.device import person_steps, reasons
    # the key that ends a wait, as `deploy run` names it (Ctrl+C ends the command; a wait never runs on to 300 s)
    lines = wait_lines(lambda text: console.print(escape(text)), host, _config_language(ctx), hint=" (Ctrl+C)")
    try:   # the shared step records the board's contacts for Mojo in `state_dir`
        client, key = person_steps.connect_stored(state_dir, device_id, host, port, user, password, step=step,
                                                  on_event=lines)
    except person_steps.BoardStepError as e:
        if e.kind in ("unreachable", "host_key_changed") and e.reason:
            _print_reason(ctx, e.reason, host, port)
            if e.kind == "host_key_changed":
                console.print(f"[bold red]{escape(str(e))}[/bold red]")
        elif e.kind == "login_failed":
            if e.reason == reasons.AUTH_FAILED:
                _print_reason(ctx, reasons.AUTH_FAILED, host, port)
            else:  # none of the four reasons: shown as it is, never relabelled as a refused login
                console.print(f"[red]{escape(str(e))}[/red]")
            console.print("[red]Pair the device once with llm-cli scan --device (it asks for the password one time) "
                          "or pass --password.[/red]")
        else:
            console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    if ctx is not None and step != "verify":  # trusted by the person when it was probed, logged in to with the
        _remember_device(ctx, host, port, user, key, paired=True)  # framework's key now (a verify only checks)
    return client


def _board_step_failed(ctx: Optional[FrameworkContext], error, host: str, port: int) -> None:
    """Exits after a step on the board failed (a RemoteError of `device runtime deploy|verify|prune --on-device`): a
    board that did not answer again after its network dropped (N5), or that refused the login again, says why in the
    plain words `deploy run` uses (_print_reason: what the reason code means and the next step), never only the raw
    code; any other failure as its sentence."""
    from orchestrator.runtime.device import reasons
    if error.reason in reasons.CODES:
        _print_reason(ctx, error.reason, host, port)
        if error.kind == "wait":   # the wait line said it before; the session's own sentence only repeats the code
            sys.exit(1)
    console.print(f"[red]{escape(str(error))}[/red]")
    sys.exit(1)


def _bench_choice(ctx: FrameworkContext, shell, home: str, bench_model: Optional[str], allow_untrusted: bool,
                  device_id: str, user: str):
    """(model path on the device or None, allowed, its sha256): a catalog model (by sha256) runs; any other model only
    after the user's consent, written to the audit log first (decision D23)."""
    from orchestrator.runtime.device import steps
    from orchestrator.runtime.device.person_steps import UNTRUSTED_CONSENT, UNTRUSTED_QUESTION
    from orchestrator.runtime.device.settings import SettingsError

    def may_run(path: str) -> bool:   # the GUI's Boards window asks these same words
        question = UNTRUSTED_QUESTION.format(path=path, user=user)
        return allow_untrusted or (_interactive() and click.confirm(question, default=False))

    tool, detail = UNTRUSTED_CONSENT
    try:
        choice = steps.choose_bench_model(
            shell, home, bench_model, may_run_untrusted=may_run,
            record_consent=lambda digest: _record_consent(ctx, tool, {"device": device_id, "model_sha256": digest},
                                                          detail))
    except SettingsError as e:
        raise click.UsageError(f"--bench-model: {e}")
    if choice.missing:
        console.print(f"[yellow]No readable model at {escape(choice.missing)} on the device; the short benchmark is "
                      "skipped.[/yellow]")
    return choice.path, choice.allowed, choice.digest


def _record_board_test(ctx: FrameworkContext, store, device_id: str, profile, report, digest: Optional[str],
                       runtime_build_id: str, source, kv_cache: Optional[str]) -> None:
    """Records a benchmark on the board with the builds of its model as "tested on" the board (N3) and says so."""
    from types import SimpleNamespace
    from orchestrator.runtime.device.service import record_board_test
    from orchestrator.utils.localization import ui_text
    smoke = report.check("smoke")
    if smoke is None or smoke.ok is None or not digest:
        return
    label = store.device_info(device_id).get("label") or device_id
    from orchestrator.Core.artifact_output import artifact_folders
    config, resolve = _settings(ctx)  # the folders of golden artifacts without booting the framework
    builds = record_board_test(artifact_folders(SimpleNamespace(config=config, resolve_path=resolve)),
                               SimpleNamespace(device_id=device_id, label=label), profile, report, digest,
                               runtime_build_id, source, kv_cache)
    lang = _config_language(ctx)
    board = profile.get("BOARD_MODEL") or label
    console.print(escape(ui_text("device.bench.recorded", lang, board=board, count=len(builds)) if builds else
                         ui_text("device.bench.not_recorded", lang)))


def _print_report(report) -> None:
    for check in report.checks:
        mark = {True: "[green]passed[/green]", False: "[red]failed[/red]", None: "[yellow]skipped[/yellow]"}[check.ok]
        console.print(f"  {check.name}: {mark} - {escape(check.detail)}")


@cli.group()
def device():
    """Devices probed over SSH and their self-built llama.cpp runtime"""
    pass


@device.command('list')
@click.option('--json', 'as_json', is_flag=True, help='Machine-readable output')
@pass_context
def device_list(ctx: FrameworkContext, as_json: bool):
    """List the stored devices with their system, deploy settings and deployed runtime"""
    from orchestrator.runtime.device.person_steps import device_rows
    rows = device_rows(_state_dir(ctx))
    if as_json:
        click.echo(json.dumps(rows, indent=2, sort_keys=True))
        return
    if not rows:
        console.print("No device is stored yet: probe one with llm-cli scan --device HOST --user USER.")
        return
    table = Table(title="Devices")
    for column in ("Device", "Label", "Address", "System", "Deploy", "Deployed runtime"):
        table.add_column(column)
    for row in rows:
        s = row["system"] or {}
        system = (f"{s.get('OS_ID') or s.get('OS') or '?'} {s.get('OS_VERSION_ID') or ''} {s.get('ARCH') or ''}, "
                  f"{s.get('LIBC') or '?'} {s.get('LIBC_VERSION') or ''}, init {s.get('INIT_SYSTEM') or '?'}"
                  if s else "profile missing")
        deploy = row["deploy"]
        deploy_text = deploy.get("problem") or f"{deploy.get('install', 'user')} as {deploy.get('ssh_user') or '?'}"
        deployed = row["deployed"]
        table.add_row(row["device_id"], escape(row["label"] or "-"), escape(row["address"] or "-"), escape(system),
                      escape(deploy_text), escape(f"{deployed['build_id'][:16]} ({deployed.get('install')})")
                      if deployed else "-")
    console.print(table)


@device.command('settings')
@click.argument('device_name', metavar='DEVICE')
@click.option('--install', type=click.Choice(['user', 'systemd']),
              help='user: start script in your home on the device (default); systemd: also a systemd unit, '
                   'written with sudo (only on devices with systemd, after your confirmation now)')
@click.option('--user', 'ssh_user', help='SSH user on the device; the server runs as this user (never root)')
@click.option('--port', type=int, help='Loopback port of the systemd unit\'s server (1024-65535, default 8080)')
@click.option('--model', help='Model file on the device that the systemd unit serves (relative: in the home folder)')
@click.option('--confirm-sudo', is_flag=True,
              help='With --install systemd: confirm without a question that deployments may run sudo on the device')
@click.option('--kv-cache', type=click.Choice(['auto', 'f16', 'q8_0', 'q4_0']),
              help='KV cache of the server: q8_0 about half the memory, q4_0 about a quarter (some quality loss), '
                   'f16 full precision; auto (the default) chooses q8_0 for a board with little RAM, else f16')
@pass_context
def device_settings(ctx: FrameworkContext, device_name: str, install: Optional[str], ssh_user: Optional[str],
                    port: Optional[int], model: Optional[str], confirm_sudo: bool, kv_cache: Optional[str]):
    """Show or change how the runtime is installed on a device. Decided here, before a deployment: a deployment
    never asks."""
    from orchestrator.runtime.device import person_steps
    from orchestrator.runtime.device.settings import SettingsError
    _state, store, device_id, profile = _device_context(ctx, device_name)
    changes = {k: v for k, v in (("install", install), ("ssh_user", ssh_user), ("port", port), ("model", model))
               if v is not None}
    if kv_cache is not None:
        changes["kv_cache"] = None if kv_cache == "auto" else kv_cache
    if not changes:
        current = _deploy_settings(store, device_id)   # damaged settings are named, never shown as the defaults
    else:
        try:   # damaged settings are set again from the defaults only after your yes at a terminal
            current, _replaced = person_steps.settings_to_change(
                store, device_id, may_replace=lambda reason, path, size: _interactive() and click.confirm(
                    person_steps.DAMAGED_QUESTION.format(device_id=device_id, reason=reason, path=path, size=size),
                    default=False))
        except person_steps.BoardStepError as e:
            console.print(f"[red]{escape(str(e))}[/red]")
            sys.exit(1)
    if changes:
        tool, detail = person_steps.SYSTEMD_CONSENT
        try:
            new = person_steps.change_settings(
                store, device_id, profile, current, changes,
                may_use_sudo=lambda: confirm_sudo or (_interactive() and click.confirm(person_steps.SUDO_QUESTION,
                                                                                         default=False)),
                record_sudo_consent=lambda: _record_consent(ctx, tool, {"device": device_id}, detail),
                stamp=_utc_stamp)
        except SettingsError as e:
            raise click.UsageError(str(e))
        except person_steps.BoardStepError as e:
            console.print(f"[red]{escape(str(e))}[/red]")
            sys.exit(1)
        current = new
        console.print(f"[green]Deploy settings of device {device_id} saved.[/green]")
    console.print(f"Install: {current.install}" + (" (start script in the user's home)" if current.install == "user"
                                                    else f" (systemd unit; sudo confirmed {current.sudo_confirmed_at})"))
    console.print(f"SSH user: {escape(current.ssh_user or 'not set (--user)')}")
    console.print(f"Port: {current.port}")
    console.print(f"Model for the systemd unit: {escape(current.model or 'not set')}")
    console.print(f"KV cache: {escape(_kv_text(ctx, current.kv_choice(profile)))}")
    for problem in current.problems(profile):
        console.print(f"[yellow]{escape(problem)}[/yellow]")


@device.group('runtime')
def device_runtime():
    """The self-built llama.cpp runtime of a device: plan, build, deploy, verify"""
    pass


@device_runtime.command('plan')
@click.argument('device_name', metavar='DEVICE')
@click.option('--json', 'as_json', is_flag=True, help='The whole plan as JSON')
@pass_context
def device_runtime_plan(ctx: FrameworkContext, device_name: str, as_json: bool):
    """Show the cross build planned from the device's own probe profile"""
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.device.steps import plan_view
    state_dir, _store, device_id, profile = _device_context(ctx, device_name)
    plan = _device_plan(profile)
    if as_json:
        click.echo(plan.to_json(), nl=False)
        return
    view = plan_view(plan, BuildStore(state_dir).find(plan))
    target, builder = view["target"], view["builder"]
    cpu = view["cpu_arm_arch"] or ("all variants" if view["cpu_all_variants"] else "x86 features of the probe")
    console.print(f"Device {device_id}: {target['os']}/{target['arch']}, {target.get('libc')} "
                  f"{target.get('libc_version')}")
    console.print(f"Builder: container {builder.get('base')} ({builder.get('libc')} {builder.get('libc_version')}, "
                  f"GCC {builder.get('gcc')}), without network")
    console.print(f"CPU: {cpu}, OpenMP {view['openmp']}, C++ runtime linked statically: "
                  f"{'yes' if view['static_libstdcxx'] else 'no'}")
    console.print(f"Programs: {', '.join(view['programs'])}"
                  + (f"; with {', '.join(view['runtime_libs'])}" if view["runtime_libs"] else ""))
    console.print(f"Build ID: {view['build_id']}")
    for note in view["notes"]:
        console.print(f"[yellow]{escape(note)}[/yellow]")
    console.print("Built: yes, " + view["built_path"] if view["built"] else "Built: no (llm-cli device runtime build "
                  f"{device_id})")


@device_runtime.command('build')
@click.argument('device_name', metavar='DEVICE')
@click.option('--jobs', '-j', type=click.IntRange(1, 256), default=None,
              help='Parallel compile jobs (default: CPUs, limited by the available memory)')
@click.option('--mirror', default=None, metavar='URL', help='Fetch the pinned source from this mirror')
@click.option('--yes', '-y', is_flag=True, help='Fetch the pinned source without asking when it is not cached')
@pass_context
def device_runtime_build(ctx: FrameworkContext, device_name: str, jobs: Optional[int], mirror: Optional[str],
                         yes: bool):
    """Cross-build llama.cpp for the device in the builder container (no network) and package the tarball"""
    from orchestrator.runtime.build.source import SourceError
    from orchestrator.runtime.build.store import BuildFailed, StoreError
    from orchestrator.runtime.device import steps
    from orchestrator.runtime.device.package import DeviceRuntimeStore, PackageError
    state_dir, _store, device_id, profile = _device_context(ctx, device_name)
    plan = _device_plan(profile)
    source = _pinned_llama_source()
    fetching = []   # the console status while the pinned source is fetched

    def may_fetch() -> bool:
        return yes or (_interactive() and click.confirm(
            f"The pinned llama.cpp source ({source.tag}, commit {source.commit[:12]}, about 215 MB) is not cached yet. "
            "Fetch it now?", default=True))

    def on_step(kind: str, build) -> None:
        if kind == "fetch":
            fetching.append(console.status("Fetching the pinned llama.cpp source ..."))
            fetching[-1].start()
        elif kind == "fetched" and fetching:
            fetching.pop().stop()
        elif kind == "build":
            console.print(f"Building llama.cpp {source.tag} for device {device_id} ({plan.target['arch']}, "
                          f"{plan.target.get('libc')} {plan.target.get('libc_version')}) in the builder container "
                          f"{plan.builder.get('base')} ...")
        elif kind == "stored":
            console.print(f"Build {build.build_id[:16]} for this device is already stored; packaging it.")

    try:
        built = steps.build_runtime(state_dir, device_id, profile, plan, source, jobs=jobs, mirror=mirror,
                                    may_fetch=may_fetch, on_step=on_step,
                                    log=lambda msg: console.print(escape(msg)))
    except steps.SourceDeclined:
        console.print("[red]The build needs the pinned source (or llm-cli ditto runtime import-source on a machine "
                      "without internet).[/red]")
        sys.exit(1)
    except BuildFailed as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        if e.log:
            console.print(f"Logs: {escape(str(e.log))}")
        sys.exit(1)
    except (SourceError, StoreError, PackageError) as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    finally:
        while fetching:
            fetching.pop().stop()
    build, packaged, devices = built.build, built.packaged, DeviceRuntimeStore(state_dir, device_id)
    console.print(f"[bold green]Device runtime ready:[/bold green] {escape(str(packaged.path))}")
    _say_older_packages(devices, build)
    console.print(f"sha256 {packaged.sha256}; format checked here (ELF, glibc symbol versions, no shared C++ runtime); "
                  "the programs run their checks on the device during the deployment.")
    console.print(f"Next: llm-cli device runtime deploy {device_id}")


def _say_older_packages(devices, build) -> None:
    """Says how many older runtime packages of the device are kept (the framework deletes none by itself)."""
    from orchestrator.runtime.device.steps import older_package_count
    older = older_package_count(devices, build)
    if older:
        console.print(f"[dim]{older} older runtime package(s) of this device kept; the framework deletes none "
                      f"by itself. 'llm-cli device runtime prune {devices.device_id}' lists them for you to delete."
                      "[/dim]")


def _ensure_package(state_dir: Path, device_id: str, builds, build):
    from orchestrator.runtime.device.package import PackageError
    from orchestrator.runtime.device.steps import PackageNeedsSource, ensure_package
    try:
        return ensure_package(state_dir, device_id, builds, build, _pinned_llama_source)
    except PackageNeedsSource:
        console.print("[red]The tarball must be packaged again, which needs the pinned source (its licence text): "
                      f"run llm-cli device runtime build {device_id}.[/red]")
        sys.exit(1)
    except PackageError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)


@device_runtime.command('prune')
@click.argument('device_name', metavar='DEVICE')
@click.option('--on-device', is_flag=True,
              help="List the older runtime builds on the device itself (over SSH) instead of the packages on this "
                   "computer; never the active build, never one a running program uses")
@pass_context
def device_runtime_prune(ctx: FrameworkContext, device_name: str, on_device: bool):
    """List the device's older runtime packages and delete the ones you pick, after you confirm them in a terminal
    (nothing preselected; never the package of the build deployed last)"""
    from orchestrator.Core.deletion import PlanChanged, size_text
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    state_dir, store, device_id, _profile = _device_context(ctx, device_name)
    if on_device:
        _prune_on_device(ctx, state_dir, store, device_id)
        return
    devices = DeviceRuntimeStore(state_dir, device_id)
    plan = devices.cleanup_plan()
    if plan is None:
        console.print(f"No older runtime packages of device {device_id}: the one of the build deployed last is never "
                      "listed.")
        return
    accepted = confirm_deletion(plan, title=f"Runtime packages of device {device_id} ({len(plan.items)} files, "
                                            f"{size_text(plan.total_bytes)})",
                                multiple=True, again=f"llm-cli device runtime prune {device_id}")
    if accepted is None:
        return
    trash, audit = _person_trash(ctx, state_dir)
    try:
        result = devices.delete_packages(accepted, state_dir, trash=trash, audit=audit)
    except (PlanChanged, PermissionError, RuntimeError) as e:
        console.print(f"[yellow]{escape(str(e))}[/yellow]")
        return
    _print_deletion_result(result, accepted)


def _prune_on_device(ctx: FrameworkContext, state_dir: Path, store, device_id: str) -> None:
    """The older runtime builds on the device (round 5 f): a deployment keeps every one of them, and this lists them
    with their full path on the device, size and what each is; exactly the ones you tick are removed after your yes
    at a terminal (nothing ticked, "No" by default). A board has no trash, so a removal is for good."""
    from orchestrator.Core.deletion import PlanChanged, size_text
    from orchestrator.runtime.device import person_steps
    from orchestrator.runtime.device.remote import RemoteError, RemoteShell
    settings = _deploy_settings(store, device_id)
    if settings.ssh_user is None:
        console.print(f"[red]No SSH user is set for device {device_id} (llm-cli device settings ... --user).[/red]")
        sys.exit(1)
    try:
        host, port = person_steps.board_address(store, device_id)
    except ValueError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    client = _connect_device(state_dir, device_id, host, port, settings.ssh_user, None)
    try:
        shell = RemoteShell(client)
        prune = person_steps.list_board_builds(shell, state_dir, device_id)
        if prune.busy:
            console.print(f"Not listed, because a running program uses them: {', '.join(prune.busy)}.")
        plan = prune.plan
        if plan is None:
            console.print(f"No older runtime builds on device {device_id} to remove: the active build is never "
                          "listed.")
            return
        accepted = confirm_deletion(plan, title=f"Runtime builds on device {device_id} ({len(plan.items)}, "
                                                f"{size_text(plan.total_bytes)}; a board has no trash)",
                                    multiple=True, verb="Remove",
                                    again=f"llm-cli device runtime prune {device_id} --on-device")
        if accepted is None:
            return
        try:
            result = person_steps.remove_board_builds(shell, accepted, prune, device_id, _device_audit(ctx, state_dir))
        except (PlanChanged, PermissionError, RuntimeError) as e:
            console.print(f"[yellow]{escape(str(e))}[/yellow]")
            return
    except RemoteError as e:
        _board_step_failed(ctx, e, host, port)
    finally:
        client.close()
    _print_deletion_result(result, accepted)


@device_runtime.command('deploy')
@click.argument('device_name', metavar='DEVICE')
@click.option('--user', 'ssh_user', default=None, help='SSH user for this deployment (default: the deploy settings)')
@device_password_options("Only for a device that is not paired yet")
@click.option('--bench-model', default=None, metavar='PATH',
              help='A model file on the device for the short llama-bench check (relative: in the home folder)')
@click.option('--allow-untrusted-model', is_flag=True,
              help='Run the short benchmark also with a model that is not a catalog model (written to the audit log)')
@pass_context
def device_runtime_deploy(ctx: FrameworkContext, device_name: str, ssh_user: Optional[str], password: Optional[str],
                          password_stdin: bool, bench_model: Optional[str], allow_untrusted_model: bool):
    """Install the built runtime on the device over SSH as the deploy settings say, then check it there"""
    password = _device_password(password, password_stdin)
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.device import steps
    from orchestrator.runtime.device.person_steps import board_address
    from orchestrator.runtime.device.remote import RemoteError, RemoteShell, read_facts
    from orchestrator.runtime.device.settings import SettingsError
    state_dir, store, device_id, profile = _device_context(ctx, device_name)
    settings = _deploy_settings(store, device_id)
    if ssh_user is not None:
        try:
            settings = settings.changed(ssh_user=ssh_user)
        except SettingsError as e:
            raise click.UsageError(str(e))
    if settings.ssh_user is None:
        console.print(f"[red]No SSH user is set for device {device_id}: llm-cli device settings {device_id} --user "
                      "NAME (or --user for this deployment).[/red]")
        sys.exit(1)
    problems = settings.problems(profile)
    if problems:
        for problem in problems:
            console.print(f"[red]{escape(problem)}[/red]")
        console.print("[red]Nothing was deployed; change the deploy settings first (llm-cli device settings).[/red]")
        sys.exit(1)
    kv = settings.kv_choice(profile)
    plan = _device_plan(profile)
    builds = BuildStore(state_dir)
    build = builds.find(plan)
    if build is None:
        console.print(f"[red]The runtime for this device is not built yet (or the device's profile changed): "
                      f"llm-cli device runtime build {device_id}[/red]")
        sys.exit(1)
    devices, packaged = _ensure_package(state_dir, device_id, builds, build)
    try:
        host, port = board_address(store, device_id)
    except ValueError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    source = _pinned_llama_source()
    client = _connect_device(state_dir, device_id, host, port, settings.ssh_user, password, ctx=ctx,
                             step="runtime_deploy")
    try:
        shell = RemoteShell(client)
        facts = read_facts(shell)
        if facts.uid == 0:
            console.print("[red]The SSH user on the device is root: the runtime never runs as root. Deploy as an "
                          "unprivileged user (llm-cli device settings DEVICE --user NAME).[/red]")
            sys.exit(1)
        # Asked before anything changes on the device (decision D23: an untrusted model only after consent).
        model_path, allowed, digest = _bench_choice(ctx, shell, facts.home, bench_model, allow_untrusted_model,
                                                    device_id, settings.ssh_user)
        # Design 3.8 and P6: the new build is checked in its own folder and becomes current only if it passes; only
        # then is its record saved.
        outcome = steps.install_runtime(shell, state_dir=state_dir, device_id=device_id, profile=profile,
                                        settings=settings, build=build, packaged=packaged, source=source,
                                        arch=str(plan.target["arch"]), kv_cache=kv.kind,
                                        bench=steps.BenchChoice(model_path, allowed, digest),
                                        say=lambda msg: console.print(escape(msg)))
    except RemoteError as e:
        _board_step_failed(ctx, e, host, port)
    finally:
        client.close()
    result, report = outcome.result, outcome.report
    if not outcome.ok:
        console.print(f"[red]Build {build.build_id[:16]} did not pass its checks on the device; it was not "
                      "activated.[/red]")
        if report is not None:
            _print_report(report)
        for note in result.notes:
            console.print(f"[yellow]{escape(note)}[/yellow]")
        sys.exit(1)
    console.print(f"[bold green]Deployed[/bold green] build {build.build_id[:16]} to {escape(result.root)}"
                  + (f" (previous {result.previous} kept)" if result.previous else ""))
    _print_report(report)
    _record_board_test(ctx, store, device_id, profile, report, digest if allowed else None, build.build_id, source,
                       kv.kind)
    for note in result.notes:
        console.print(f"[yellow]{escape(note)}[/yellow]")
    _say_older_packages(devices, build)
    console.print(f"KV cache: {escape(_kv_text(ctx, kv))}")
    if settings.install == "systemd":
        console.print(f"systemd unit: {'started' if result.unit_started else 'enabled'}; the server listens on "
                      f"127.0.0.1:{settings.port} with the key in ~/.config/llm-framework/llama-server.key")
    else:
        # The note above names the start command (the port when it is not the default, the KV cache).
        console.print(f"The server listens on 127.0.0.1:{settings.port}, key in "
                      "~/.config/llm-framework/llama-server.key; stop, status and serve (foreground) work the same "
                      "way.")


@device_runtime.command('verify')
@click.argument('device_name', metavar='DEVICE')
@click.option('--user', 'ssh_user', default=None, help='SSH user (default: the deploy settings)')
@click.option('--bench-model', default=None, metavar='PATH',
              help='A model file on the device for the short llama-bench check (relative: in the home folder)')
@click.option('--allow-untrusted-model', is_flag=True,
              help='Run the short benchmark also with a model that is not a catalog model (written to the audit log)')
@pass_context
def device_runtime_verify(ctx: FrameworkContext, device_name: str, ssh_user: Optional[str],
                          bench_model: Optional[str], allow_untrusted_model: bool):
    """Check the deployed runtime on the device again (as the unprivileged user, never with sudo)"""
    from orchestrator.runtime.device import steps
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.person_steps import board_address
    from orchestrator.runtime.device.remote import RemoteError, RemoteShell, read_facts
    from orchestrator.runtime.device.settings import SettingsError
    state_dir, store, device_id, profile = _device_context(ctx, device_name)
    settings = _deploy_settings(store, device_id)
    try:
        user = settings.changed(ssh_user=ssh_user).ssh_user if ssh_user else settings.ssh_user
    except SettingsError as e:
        raise click.UsageError(str(e))
    if user is None:
        console.print(f"[red]No SSH user is set for device {device_id} (llm-cli device settings ... --user).[/red]")
        sys.exit(1)
    devices = DeviceRuntimeStore(state_dir, device_id)
    record = devices.record()
    if not record:
        console.print(f"[red]Nothing was deployed to device {device_id} yet (llm-cli device runtime deploy).[/red]")
        sys.exit(1)
    plan_verify = steps.deployed_verify_plan(state_dir, record, lambda: _device_plan(profile))
    try:
        host, port = board_address(store, device_id)
    except ValueError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    source = _pinned_llama_source()
    client = _connect_device(state_dir, device_id, host, port, user, None, ctx=ctx, step="verify")
    try:
        shell = RemoteShell(client)
        facts = read_facts(shell)
        if facts.uid == 0:
            console.print("[red]The SSH user is root; the checks run as the unprivileged user only.[/red]")
            sys.exit(1)
        model_path, allowed, digest = _bench_choice(ctx, shell, facts.home, bench_model, allow_untrusted_model,
                                                    device_id, user)
        report = steps.check_runtime(shell, state_dir=state_dir, device_id=device_id, home=facts.home, record=record,
                                     plan_verify=plan_verify, source=source, port=settings.port,
                                     bench=steps.BenchChoice(model_path, allowed, digest))
    except RemoteError as e:
        _board_step_failed(ctx, e, host, port)
    finally:
        client.close()
    console.print(f"Device {device_id}, build {record['build_id'][:16]}:")
    _print_report(report)
    _record_board_test(ctx, store, device_id, profile, report, digest if allowed else None, record["build_id"],
                       source, settings.kv_choice(profile).kind)
    if not report.ok:
        sys.exit(1)


@device_runtime.command('status')
@click.argument('device_name', metavar='DEVICE')
@click.option('--json', 'as_json', is_flag=True, help='Machine-readable output')
@pass_context
def device_runtime_status(ctx: FrameworkContext, device_name: str, as_json: bool):
    """What is deployed on the device and whether it still matches the device's profile and the pin"""
    from orchestrator.runtime.device.steps import runtime_status
    state_dir, store, device_id, profile = _device_context(ctx, device_name)
    plan = _device_plan(profile)
    status = runtime_status(state_dir, device_id, plan)
    record, built = status["deployed"] or {}, status["built"]
    if as_json:
        click.echo(json.dumps(status, indent=2, sort_keys=True))
        return
    console.print(f"Device {device_id}: planned build {plan.build_id[:16]} "
                  f"({'built' if built else 'not built yet'})")
    if not record:
        console.print("Deployed: nothing yet")
        return
    report = record.get("verification") or {}
    console.print(f"Deployed: build {record['build_id'][:16]} on {escape(str(record.get('deployed_at')))} "
                  f"({escape(str(record.get('install')))}), checks on the device "
                  f"{'passed' if report.get('ok') else 'failed'} ({escape(str(record.get('verified_at')))})")
    if record.get("kv_cache"):
        console.print(f"KV cache at that deployment: {escape(str(record['kv_cache']))}")
    if not status["current"]:
        console.print("[yellow]The deployed build is not the one planned now (the profile, the pin or the recipe "
                      "changed): build and deploy again.[/yellow]")


# ============================================================================
# CONFIG COMMANDS (SOURCES MANAGEMENT)
# ============================================================================

@cli.group()
def config():
    """Configuration Management"""
    pass

@config.command('show')
@pass_context
def show_config(ctx: FrameworkContext):
    """Show the effective settings (config.yml, a -c file, LLM_FRAMEWORK_* variables, defaults) as JSON, each with
    its source; secrets are redacted"""
    from orchestrator.Core.settings_view import effective_settings
    config, _ = _settings(ctx)
    click.echo(json.dumps(effective_settings(config), indent=2, default=str, ensure_ascii=False))


@config.command('output-folder')
@click.argument('folder', required=False)
@pass_context
def config_output_folder(ctx: FrameworkContext, folder: Optional[str]):
    """Show or set the output folder finished models are saved to (default ~/llmcf/output, for example a folder on
    a big second disk). Builds in the old folder stay where they are and stay listed."""
    from orchestrator.Core.artifact_output import DestinationRefused, build_folder, output_folder, set_output_folder
    ctx.require_framework()
    fm = ctx.framework_manager
    if folder is None:
        console.print(f"Output folder: {escape(str(output_folder(fm)))}")
        console.print(f"The framework's build folder (builds that are not saved yet, and earlier builds): "
                      f"{escape(str(build_folder(fm)))}")
        return
    try:
        chosen = set_output_folder(fm, folder)
    except DestinationRefused as e:
        console.print(f"[red]{escape(str(e))}[/red] {escape(e.next)}")
        sys.exit(2)
    except OSError:
        console.print(f"[red]Not saved: {escape(str(Path(fm.config.config_dir) / 'config.yml'))} could not be "
                      "updated (see the log); the output folder is unchanged.[/red]")
        sys.exit(1)
    console.print(f"[green]Output folder: {escape(str(chosen))}[/green] Builds in the old folder stay where they are "
                  "and stay listed ('llm-cli deploy list-artifacts').")


@config.command('repos')
@click.option('--add', nargs=2, help="Add repo override: <name> <url>")
@click.option('--remove', help="Remove repo override: <name>")
@pass_context
def config_repos(ctx: FrameworkContext, add, remove):
    """Manage SSOT Source Repositories"""
    from orchestrator.Core.settings_view import (CREDENTIAL_IN_URL, RepoOverrideError, add_repo_override,
                                                 remove_repo_override, repo_overrides, shown_repo_url)
    ctx.require_framework()
    cm = ctx.framework_manager.config
    # The same functions as File > Settings... in the GUI.
    try:
        if add:
            name, url = add
            variable = add_repo_override(cm, name, url)
            console.print(f"[green]Added repo override: {escape(name.lower())} -> {escape(shown_repo_url(url))} "
                          f"(passed to builds as {escape(variable)})[/green]")
        elif remove:
            if remove_repo_override(cm, remove):
                console.print(f"[green]Removed repo override: {escape(remove)}[/green]")
            else:
                console.print(f"[yellow]Repo {escape(remove)} not found.[/yellow]")
        else:
            current_repos = repo_overrides(cm)
            if not current_repos:
                console.print("[yellow]No repository overrides defined.[/yellow]")
            else:
                table = Table(title="Source Repositories (SSOT)")
                table.add_column("Key", style="cyan")
                table.add_column("URL", style="green")
                for k, url in current_repos.items():
                    table.add_row(escape(k), escape(shown_repo_url(url)))
                console.print(table)
    except RepoOverrideError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(2 if e.code in ("invalid_name", CREDENTIAL_IN_URL) else 1)

# ============================================================================
# COMMUNITY ZOO (signed community packs: targets, recipes, knowledge)
# ============================================================================

ZOO_KINDS = ("target", "recipe", "knowledge")


@cli.group()
def zoo():
    """Community zoo: signed targets, recipes and knowledge packs (nothing is ever uploaded by the framework)"""
    pass


def _zoo(ctx: FrameworkContext):
    ctx.require_framework()
    cm = ctx.framework_manager.get_component("community_manager")
    if cm is None:
        console.print("[red]The community manager is not available.[/red]")
        sys.exit(1)
    return cm


def _zoo_fail(error: Exception, code: int = 1):
    console.print(f"[red]{escape(str(error))}[/red]")
    sys.exit(code)


def _public_keys(files, kind):
    from orchestrator.Core.community_pack import KeyFileError, read_public_key_files
    try:
        return read_public_key_files(files, kind)   # the Community Hub reads key files the same way
    except KeyFileError as e:
        _zoo_fail(click.UsageError(str(e)) if e.code == "not_text" else ValueError(str(e)), 2)


@zoo.command('list')
@click.option('--kind', type=click.Choice(ZOO_KINDS), help='Only this kind')
@click.option('--refresh', is_flag=True, help='Download the signed index again')
@click.option('--json', 'as_json', is_flag=True, help='Machine-readable output')
@pass_context
def zoo_list(ctx: FrameworkContext, kind: Optional[str], refresh: bool, as_json: bool):
    """List the community index and installed packs with their trust level"""
    items, note = _zoo(ctx).listing(refresh)
    items = [i for i in items if not kind or i.kind == kind]
    if as_json:
        click.echo(json.dumps([{"kind": i.kind, "id": i.id, "version": i.version, "trust": i.trust.value,
                                "installed_version": i.installed_version, "revoked": i.revoked,
                                "author": i.author, "title": i.title} for i in items], indent=2))
        return
    table = Table(title="Community zoo")
    for column in ("Kind", "ID", "Version", "Trust", "Installed", "Author", "Title"):
        table.add_column(column)
    for i in items:
        table.add_row(i.kind, escape(i.id), i.version, i.trust.value, i.installed_version or "-",
                      escape(i.author), escape(i.title))
    console.print(table)
    console.print(f"[dim]{escape(note)}[/dim]")


@zoo.command('show')
@click.argument('pack_id')
@click.option('--kind', type=click.Choice(ZOO_KINDS), help='Kind, if the id exists for several')
@pass_context
def zoo_show(ctx: FrameworkContext, pack_id: str, kind: Optional[str]):
    """Show one entry of the zoo (and an installed recipe)"""
    found = _zoo(ctx).details(pack_id, kind)   # the Community Hub's Details... shows the same
    if not found:
        _zoo_fail(ValueError(f"'{pack_id}' is neither in the cached index nor installed (llm-cli zoo list --refresh)."))
    for details in found:
        console.print(Panel(escape("\n".join(details.lines())), title=escape(details.item.title)))


@zoo.command('install')
@click.argument('pack_id', required=False)
@click.option('--kind', type=click.Choice(ZOO_KINDS), default='target', show_default=True)
@click.option('--file', 'file_path', type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help='Install a pack file (or an encrypted pack) instead of an index entry')
@click.option('--from', 'senders', multiple=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help='Encrypted packs: accept only these senders (public key files, repeatable); refused for a '
              'plain pack')
@click.option('--passphrase', 'use_passphrase', is_flag=True, help='Encrypted packs: ask for the passphrase')
@click.option('--yes', is_flag=True, help="Do not ask for a pack whose trust comes from the signed index, also a "
              "file the index lists (a file without an index entry is 'local' and is always confirmed)")
@pass_context
def zoo_install(ctx: FrameworkContext, pack_id: Optional[str], kind: str, file_path: Optional[Path],
                senders: Tuple[Path, ...], use_passphrase: bool, yes: bool):
    """Install a community pack: download pinned by the signed index, checks in quarantine, your confirmation"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from orchestrator.Core.community_manager import TrustLevel
    from orchestrator.Core.community_pack import PackError, is_encrypted_pack
    if bool(pack_id) == bool(file_path):
        _zoo_fail(click.UsageError("Give either an id from the index or --file."), 2)
    if senders and not (file_path and is_encrypted_pack(file_path)):
        _zoo_fail(click.UsageError("--from applies only to encrypted packs (.lcfpack); this pack is not encrypted. "
                                   "A plain pack shows its author signature in the summary."), 2)
    cm = _zoo(ctx)
    try:
        if file_path:
            # An encrypted pack opens with your own age identity, or with the passphrase.
            passphrase = click.prompt("Passphrase", hide_input=True, err=True) if use_passphrase else None
            prepared = cm.prepare_install_file(file_path, passphrase=passphrase,
                                               trusted_senders=_public_keys(senders, Ed25519PublicKey) or None)
        else:
            with console.status(f"Downloading and checking {kind} {pack_id}..."):
                prepared = cm.prepare_install(kind, pack_id)
    except (PackError, OSError) as e:
        _zoo_fail(e)
    click.echo(prepared.summary(), err=True)
    asked = not (yes and prepared.trust != TrustLevel.LOCAL)
    if asked and not sys.stdin.isatty():
        cm.discard(prepared)
        _zoo_fail(ValueError("No terminal to confirm in: not installed (a file without an index entry is "
                             "'local' and is always confirmed; --yes does not apply to it)."))
    if asked and not click.confirm("Install this pack?", default=False, err=True):
        cm.discard(prepared)
        sys.exit(1)
    try:
        dest = cm.finish_install(prepared)
    except (PackError, OSError) as e:
        _zoo_fail(e)
    if prepared.manifest.kind == "target" and ctx.framework_manager.target_manager:
        ctx.framework_manager.target_manager.initialize()
    console.print(f"[green]Installed {prepared.manifest.kind} {escape(prepared.manifest.id)} in "
                  f"{escape(str(dest))}[/green]")
    if prepared.imports_knowledge:   # a knowledge pack, or a target pack's knowledge snapshot
        _say_knowledge_import(cm)


def _say_knowledge_import(cm) -> None:
    """What the import into the knowledge base did after an install or a sort (N13: a snapshot first, and nothing
    imported without it; why a pack waits is said, never only logged)."""
    if getattr(cm, "last_sync_problem", ""):
        console.print(f"[yellow]Not imported into the knowledge base yet: {escape(cm.last_sync_problem)}[/yellow]")
    elif getattr(cm, "last_sync_snapshot", None):
        console.print(f"The knowledge base was saved first as the snapshot {escape(cm.last_sync_snapshot)} "
                      "('llm-cli knowledge snapshots' lists it).")


@zoo.command('verify')
@click.argument('pack_file', required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@pass_context
def zoo_verify(ctx: FrameworkContext, pack_file: Optional[Path]):
    """Check installed packs (revoked, changed), or a pack file: format, hashes, validators and privacy scrub
    (used by the community repository's CI; works without a keyring)"""
    from orchestrator.Core.community_manager import CommunityManager
    from orchestrator.Core.community_pack import PackError
    if pack_file:
        try:
            manifest, problems = CommunityManager(None).verify_file(pack_file)
        except (PackError, OSError) as e:
            _zoo_fail(e)
        for problem in problems:
            console.print(f"[red]✘ {escape(problem)}[/red]")
        if problems:
            sys.exit(1)
        console.print(f"[green]✔ {manifest.kind} {escape(manifest.id)} {manifest.version}: acceptable[/green]")
        return
    results = _zoo(ctx).verify_installed()
    for folder, prov, trust, problems in results:
        mark = "[red]✘[/red]" if problems else "[green]✔[/green]"
        detail = "; ".join(problems) or (trust.value if trust else "")
        console.print(f"{mark} {prov.kind} {escape(prov.id)} {prov.version}: {escape(detail)}")
    if not results:
        console.print("No community packs installed.")
    sys.exit(1 if any(problems for *_, problems in results) else 0)


@zoo.command('export')
@click.argument('kind', type=click.Choice(ZOO_KINDS))
@click.argument('source', required=False)
@click.option('--author', required=True, help='Name shown as the author (a pseudonym is fine)')
@click.option('--license', 'license_', help='Licence of the pack (SPDX id); default MIT, NOASSERTION for '
              'knowledge (the pages keep their owners\' terms)')
@click.option('--model-license', default='unknown', show_default=True, help='Recipes: licence id of the model')
@click.option('--id', 'pack_id', help='Knowledge: id of the pack')
@click.option('--topic', help='Knowledge (this or --target is required): the topic its pages belong to, e.g. llama.cpp')
@click.option('--target', 'target', help='Knowledge (this or --topic is required): the target its pages belong to')
@click.option('--to', 'recipients', multiple=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help='Encrypt for private sharing to the age recipients (age1...) in this file (repeatable)')
@click.option('--passphrase', 'use_passphrase', is_flag=True, help='Encrypt for private sharing with a passphrase')
@click.option('--out', type=click.Path(file_okay=False, path_type=Path), help='Folder for the pack')
@click.option('--pr', 'open_pr', is_flag=True,
              help='Hand the pack to a pull request on the community repository (gh if installed, else the browser)')
@click.option('--yes', is_flag=True, help='With --pr: submit without asking after the summary')
@pass_context
def zoo_export(ctx: FrameworkContext, kind: str, source: Optional[str], author: str, license_: Optional[str],
               model_license: str, pack_id: Optional[str], topic: Optional[str], target: Optional[str],
               recipients: Tuple[Path, ...], use_passphrase: bool, out: Optional[Path], open_pr: bool, yes: bool):
    """Export a target (SOURCE: target id), a recipe (SOURCE: a build folder or its build_info.json) or your
    knowledge base (on a --topic or a --target) as a scrubbed pack signed with your key. Shows what the privacy scrub
    changed."""
    from pyrage.x25519 import Recipient
    from orchestrator.Core.community_manager import submit_contribution
    from orchestrator.Core.community_pack import PackError
    if kind in ("target", "recipe") and not source:
        _zoo_fail(click.UsageError(f"Name the {'target id' if kind == 'target' else 'build folder'} to export."), 2)
    if recipients and use_passphrase:
        _zoo_fail(click.UsageError("Use --to or --passphrase, not both: age encrypts to keys or to one passphrase."), 2)
    if kind == "knowledge" and bool(topic) == bool(target):
        _zoo_fail(click.UsageError("Knowledge always belongs to a topic or a target: give --topic or --target (not "
                                   "both)."), 2)
    if kind != "knowledge" and (topic or target):
        _zoo_fail(click.UsageError("--topic and --target apply only to a knowledge pack."), 2)
    if kind == "recipe" and Path(source).is_dir():
        source = str(Path(source) / "build_info.json")
    cm = _zoo(ctx)
    passphrase = click.prompt("Passphrase", hide_input=True, confirmation_prompt=True, err=True) \
        if use_passphrase else None
    try:
        result = cm.export_pack(kind, source, author=author, license=license_, pack_id=pack_id,
                                model_license=model_license, recipients=_public_keys(recipients, Recipient),
                                passphrase=passphrase, out_dir=out, topic=topic, target=target)
    except (PackError, OSError, ValueError) as e:
        _zoo_fail(e)
    click.echo(result.summary())
    if not open_pr:
        console.print("[dim]Review the pack, then share the file or submit it with --pr.[/dim]")
        return
    if result.encrypted:
        _zoo_fail(ValueError("Encrypted packs are for private sharing; they are not submitted to the community."))
    # Publishing is a release of its own: only after the summary and the scrub changes were shown.
    if not yes and not (sys.stdin.isatty() and click.confirm(
            "Submit this pack to the public community repository?", default=False, err=True)):
        _zoo_fail(ValueError(f"Not submitted; the pack is ready at {result.path}."))
    try:
        console.print(escape(submit_contribution(result.path, ctx.framework_manager.config.get("community_repo", ""),
                                                 kind)))
    except PackError as e:
        _zoo_fail(e)


@zoo.command('keys')
@pass_context
def zoo_keys(ctx: FrameworkContext):
    """Show your public keys: give them to people who send you encrypted packs or check your signed packs"""
    from orchestrator.Core.community_pack import PackError, UserKeys
    ctx.require_framework()
    try:
        click.echo(UserKeys(ctx.framework_manager.secrets_manager).export_public(), nl=False)
    except PackError as e:
        _zoo_fail(e)


# --- the maintainer of the community repository (orchestrator/Core/community_maintainer.py; the person only) --------

@zoo.group('maintainer')
def zoo_maintainer():
    """For the maintainer of the community repository: signing key, review and publish, revoke (your own gh login)"""
    pass


def _maintainer(ctx: FrameworkContext):
    """The maintainer actions for community_repo, every step printed (the Community Hub's Maintainer tab does the
    same)."""
    from orchestrator.Core import community_maintainer as cmaint
    ctx.require_framework()

    def report(code: str, values: dict) -> None:
        console.print(f"[dim]{escape(cmaint.say(f'zoo.maint.step.{code}', **values))}[/dim]")
    try:
        return cmaint.for_framework(ctx.framework_manager, "cli", progress=report)
    except cmaint.MaintainerError as e:
        _zoo_fail(e)


def _maintainer_key(m, key_name: Optional[str]) -> str:
    from orchestrator.Core.community_maintainer import MaintainerError
    key = key_name or m.remembered_key()
    if not key:
        _zoo_fail(MaintainerError("no_key_name"))
    return key


@zoo_maintainer.command('key')
@click.argument('name')
@click.option('--save', 'save_to', type=click.Path(dir_okay=False, path_type=Path),
              help='Also save the public entry to this text file (an existing file is never replaced)')
@pass_context
def zoo_maintainer_key(ctx: FrameworkContext, name: str, save_to: Optional[Path]):
    """Once: create the index signing key in this computer's keyring and show its public entry for
    configs/community_keys.yml (a key of that name is never replaced)"""
    from orchestrator.Core import community_maintainer as cmaint
    if save_to is not None and save_to.exists():
        _zoo_fail(cmaint.MaintainerError("file_exists", path=str(save_to)))
    ctx.require_framework()
    audit = getattr(getattr(ctx.framework_manager, "tool_registry", None), "audit", None)
    try:
        entry = cmaint.create_signing_key(name, audit=audit, actor="cli")
    except cmaint.MaintainerError as e:
        _zoo_fail(e)
    console.print(escape(cmaint.say("zoo.maint.key.done", name=entry.name, key_id=entry.key_id)))
    click.echo(entry.text(), nl=False)
    if save_to is not None:
        try:
            console.print(escape(cmaint.say("zoo.maint.key.saved", path=str(entry.save(save_to)))))
        except (cmaint.MaintainerError, OSError) as e:
            _zoo_fail(e)
    try:
        cmaint.for_framework(ctx.framework_manager, "cli").remember_key(entry.name)
    except (cmaint.MaintainerError, OSError):
        pass


@zoo_maintainer.command('review')
@click.argument('item', required=False)
@click.option('--review-dir', type=click.Path(file_okay=False, path_type=Path),
              help='Write every file of the packs to this new or empty folder instead of printing them')
@click.option('--key', 'key_name', help='Name of the signing key (default: the one used last)')
@click.option('--trust', type=click.Choice(["community", "verified"]), default="community", show_default=True,
              help="verified only when someone other than the author reproduced the pack")
@click.option('--title', help='Title shown in the hub (a contribution with one pack; default: the pack id)')
@pass_context
def zoo_maintainer_review(ctx: FrameworkContext, item: Optional[str], review_dir: Optional[Path],
                          key_name: Optional[str], trust: str, title: Optional[str]):
    """Without ITEM: the open pull requests that add packs, and merged packs not published yet. With ITEM (a pull
    request number, or main:contributions/KIND/FILE.zip): fetch its packs, check them (zoo verify), show every file,
    then ask "Publish?" ("No" is the default). Yes merges it, moves the pack to packs/, adds its index entry, signs the
    index and merges that as a pull request after the required check; Ctrl+C cancels before the last merge."""
    from orchestrator.Core import community_maintainer as cmaint
    m = _maintainer(ctx)
    if not item:
        try:
            items = m.contributions()
        except cmaint.MaintainerError as e:
            _zoo_fail(e)
        if not items:
            console.print(escape(cmaint.say("zoo.maint.none")))
            return
        table = Table(title=f"Contributions to {m.repo}")
        for column in ("zoo.maint.col.item", "zoo.maint.col.title", "zoo.maint.col.author", "zoo.maint.col.packs",
                       "zoo.maint.col.check"):
            table.add_column(cmaint.say(column))
        for found in items:   # a title or author can hold a direction override: shown as escapes
            table.add_row(escape(found.label), escape(cmaint.one_line(found.title)),
                          escape(cmaint.one_line(found.author)),
                          escape("\n".join(cmaint.one_line(p) for p in found.packs)),
                          cmaint.say(f"zoo.maint.check.{found.check}"))
        console.print(table)
        console.print("[dim]Review one: llmcf zoo maintainer review NUMBER[/dim]")
        return
    try:
        review = m.review(m.find(item), review_dir)
    except cmaint.MaintainerError as e:
        _zoo_fail(e)
    try:
        for pack in review.packs:
            click.echo(f"\n=== {pack.line()}")
            if review_dir is not None:
                click.echo(f"Written to {review.folder / pack.file_name} and {review.folder / Path(pack.file_name).stem}:")
            for file in pack.files:
                if review_dir is not None:
                    click.echo(f"  {cmaint.shown_text(file.path)} ({file.size} bytes)")
                    continue
                click.echo(f"\n----- {cmaint.shown_text(file.path)} ({file.size} bytes) -----")
                click.echo(cmaint.shown_text(file.text) if file.text is not None
                           else cmaint.say("zoo.maint.binary", size=file.size))
        problems = review.problems()
        click.echo("")
        if not review.ok:
            _zoo_fail(cmaint.MaintainerError("rejected", problems="\n".join(f"- {p}" for p in problems)
                                             or "- no pack"))
        console.print(f"[green]{escape(cmaint.say('zoo.maint.reviewed.ok', item=review.item.label))}[/green]")
        key = _maintainer_key(m, key_name)
        question = cmaint.say("zoo.maint.publish.ask", packs="\n".join(p.line() for p in review.packs),
                              item=review.item.label, trust=trust, key=key)
        if not (_interactive() and click.confirm(question, default=False)):
            _zoo_fail(ValueError(cmaint.say("zoo.maint.publish.no")))
        try:
            m.publish(review, key_name=key, trust=trust, title=title or "")
        except cmaint.MaintainerError as e:
            _zoo_fail(e)
    finally:
        review.discard()


@zoo_maintainer.command('revoke')
@click.argument('kind', type=click.Choice(ZOO_KINDS))
@click.argument('pack_id')
@click.option('--reason', required=True, help='Why (every user sees it; at most 300 characters)')
@click.option('--version', 'version', help='Only this version (default: every version of the id)')
@click.option('--sha256', help='Only exactly this file (its sha256 from the index)')
@click.option('--key', 'key_name', help='Name of the signing key (default: the one used last)')
@pass_context
def zoo_maintainer_revoke(ctx: FrameworkContext, kind: str, pack_id: str, reason: str, version: Optional[str],
                          sha256: Optional[str], key_name: Optional[str]):
    """Revoke a published pack: added to the revoked list of the signed index, version raised, signed, merged as a
    pull request after the required check ("No" is the default of the question)"""
    from orchestrator.Core import community_maintainer as cmaint
    if version and sha256:
        _zoo_fail(click.UsageError("Use --version or --sha256, not both."), 2)
    try:
        what = cmaint.describe_revocation(cmaint.revocation(kind, pack_id, reason=reason, version=version,
                                                            sha256=sha256))
    except cmaint.MaintainerError as e:
        _zoo_fail(e)
    m = _maintainer(ctx)
    key = _maintainer_key(m, key_name)
    question = cmaint.say("zoo.maint.revoke.ask", what=what, reason=reason, key=key)
    if not (_interactive() and click.confirm(question, default=False)):
        _zoo_fail(ValueError(cmaint.say("zoo.maint.revoke.no")))
    try:
        m.revoke(kind, pack_id, reason=reason, version=version, sha256=sha256, key_name=key)
    except cmaint.MaintainerError as e:
        _zoo_fail(e)


if __name__ == "__main__":
    cli()
