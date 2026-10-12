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
import re
import time
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

import click
import yaml
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markup import escape
from rich.prompt import Confirm

from orchestrator.Core.framework import FrameworkManager
from orchestrator.Core.orchestrator import LLMOrchestrator, BuildRequest, WorkflowType, PriorityLevel, OrchestrationStatus
from orchestrator.Core.builder import BuildEngine, ModelFormat, OptimizationLevel, repo_override_names
from orchestrator.Core.module_generator import ModuleGenerator
from orchestrator.Core.deployment_manager import DeploymentManager
from orchestrator.llm.sdk_env import guard_sdk_environment
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger, setup_logging

# ============================================================================
# GLOBAL CONFIGURATION
# ============================================================================

console = Console()
from orchestrator import __version__  # noqa: E402

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
            self.framework_manager = FrameworkManager(self.config_path, log_level=self.log_level)
            
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
    """Convert string to ModelFormat enum"""
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
    from orchestrator.tools.mcp_tokens import ENV_VAR
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
    from pydantic import ValidationError
    from orchestrator.schemas.targets import TargetCapabilities
    ctx.require_framework()
    tm = ctx.framework_manager.get_component("target_manager")
    if not tm:
        console.print("[red]Target Manager not available[/red]")
        sys.exit(1)
    tid = tm.resolve(target_name)
    if tid is None:
        console.print(f"[red]❌ Target '{escape(target_name)}' not found (or its target.yml cannot be read; "
                      f"see the log).[/red]")
        sys.exit(1)
    data = tm.get_target(tid) or {}
    if "capabilities" not in data:
        console.print(f"[yellow]Target '{escape(tid)}' loads but declares no capabilities: boards, formats and "
                      "quantizations are not checked before a build. Add a 'capabilities:' block "
                      "(see targets/Rockchip/target.yml).[/yellow]")
        return
    try:
        caps = TargetCapabilities.model_validate(data["capabilities"])
    except ValidationError as e:
        console.print(f"[red]❌ Target '{escape(tid)}': its capabilities block is invalid:[/red]")
        for err in e.errors():
            where = ".".join(str(part) for part in err.get("loc", ())) or "capabilities"
            console.print(f"[red]  capabilities.{escape(where)}: {escape(str(err.get('msg', '')))}[/red]")
        sys.exit(1)
    console.print(f"[green]✅ Target '{escape(tid)}' is valid ({len(caps.boards)} boards, formats: "
                  f"{escape(', '.join(caps.formats))})[/green]")


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
        generator = ModuleGenerator(targets_dir)

        output_path = generator.generate_module(module_data, profile=profile)
        
        console.print(f"\n[bold green]✅ Module successfully created![/bold green]")
        console.print(f"Location: {output_path}")
        console.print("\nNext steps:")
        console.print(f"1. Edit {output_path}/target.yml to add supported boards")
        # The build engine runs only this dispatcher (config_module.sh and the other *_module.sh are legacy).
        console.print(f"2. Customize {output_path}/modules/build.sh (the build steps; the Dockerfile installs "
                      f"the tools they use)")
        
    except Exception as e:
        console.print(f"[bold red]Error creating module: {e}[/bold red]")
        sys.exit(1)


def _ai_override(ctx: FrameworkContext, provider: Optional[str], model: Optional[str]):
    """Ditto with provider/model chosen for this command only (the saved AI settings stay unchanged)."""
    fm = ctx.framework_manager
    ditto = fm.get_component("ditto_manager")
    if ditto is None:
        console.print("[red]The AI assistant (Ditto) is not available.[/red]")
        sys.exit(1)
    if provider:
        if ditto.client.catalog.get(provider) is None:
            removed = ditto.client.catalog.removed_message(provider)
            console.print(f"[red]{escape(removed)}[/red]" if removed else
                          f"[red]Unknown AI provider '{escape(provider)}'. Choose one of: "
                          f"{escape(', '.join(ditto.client.catalog.ids()))}.[/red]")
            sys.exit(2)
        fm.config.set("ai_provider", provider)
    if model:
        fm.config.set("ai_model", model)
    return ditto


PAGED_QUESTION_LINES = 30  # a longer question (up to 16,000 characters) is shown through the pager first


def _ditto_confirm(spec, inp) -> bool:
    """Asks before a tool call that needs confirmation; without a terminal the answer is no. A long question (a
    module definition) is shown whole, through the pager when it does not fit the screen."""
    from orchestrator.tools.confirmation import MAX_ELICITATION, MAX_FRAMEWORK, CallTooLong, describe_call
    from orchestrator.tools.registry import current_question
    try:
        text = current_question() or describe_call(spec, inp.model_dump(mode="json"), actor="Ditto",
                                                   limit=MAX_FRAMEWORK)
    except CallTooLong as e:
        click.echo(str(e), err=True)
        return False
    if not sys.stdin.isatty():
        click.echo(text, err=True)
        click.echo("No terminal to confirm in: denied. Allow the tool with 'llm-cli tools permissions'.", err=True)
        return False
    long = len(text) > MAX_ELICITATION or len(text.splitlines()) > PAGED_QUESTION_LINES
    if long and sys.stdout.isatty():
        click.echo_via_pager(text + "\n")
    else:
        click.echo(text, err=True)
    if getattr(spec, "offers_request", False):  # a remote framework's question (Ditto's MCP-client mode)
        from orchestrator.llm.remote_tools import RemoteAnswer
        answer = click.prompt(f"Allow this? y = once, r = and ask the framework at {spec.server} to stop asking "
                              "(you confirm that there), n = no", type=click.Choice(["y", "r", "n"]), default="n",
                              err=True)
        return RemoteAnswer(answer != "n", stop_asking=answer == "r")
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
    ctx.require_framework()
    ditto = _ai_override(ctx, provider, model)
    try:
        info = ditto.describe("json")
    except DittoError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    console.print(Panel(f"[bold cyan]🤖 Ditto AI Hardware Agent[/bold cyan]\nAnalyzing: {escape(str(probe_file))}\n"
                        f"Provider: {escape(info['label'])} / {escape(info['model'])}", title="AI Setup"))
    try:
        with console.status("🤖 Analyzing the hardware probe...", spinner="dots"):
            definition = ditto.generate_module(Path(probe_file))
    except DittoError as e:
        console.print(f"[bold red]AI generation failed:[/bold red] {escape(str(e))}")
        sys.exit(1)

    # The base image is pinned to the digest its tag points at now, before the question (DECISIONS round 3): the
    # module is written with exactly the digest shown.
    if "@sha256:" not in definition.base_os:
        from orchestrator.utils.image_registry import ImageLookupError, inspect_image
        try:
            image = inspect_image(definition.base_os,
                                  offline=bool(ctx.framework_manager.config.get("offline_mode", False)))
        except ImageLookupError as e:
            console.print(f"[red]The base image {escape(definition.base_os)} cannot be pinned: {escape(str(e))}[/red]")
            console.print(escape(e.next))
            sys.exit(1)
        definition = definition.model_copy(update={"base_os": image.pinned})
    # The definition is shown in full before anything is written; the module generator checks it again.
    console.print("\n[bold green]AI analysis finished. Review the proposal:[/bold green]")
    click.echo(json.dumps(definition.model_dump(exclude={"cannot"}), indent=2, ensure_ascii=False))
    click.echo(proposal_facts(definition))
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
    except ValueError as e:
        console.print(f"[bold red]The proposal was rejected:[/bold red] {escape(str(e))}")
        sys.exit(1)
    from orchestrator.Core.module_generator import generator_input
    from orchestrator.tools.tools_targets import record_module_saved
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
@click.option('--output-dir', '-o', help='Custom output directory')
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
    ctx.require_framework()
    if parallel:
        Console(stderr=True).print("[yellow]--parallel is deprecated and has no effect.[/yellow]")
    
    try:
        # Target id (or folder name), resolved to the registered id below
        target_arch_str = target
        target_format_enum = model_format_from_string(format)
        
        opt_mapping = {
            'fast': OptimizationLevel.FAST,
            'balanced': OptimizationLevel.BALANCED,
            'size': OptimizationLevel.SIZE,
            'speed': OptimizationLevel.SPEED,
            'aggressive': OptimizationLevel.AGGRESSIVE
        }
        optimization_level = opt_mapping[optimization]
        
        priority_mapping = {
            'low': PriorityLevel.LOW,
            'normal': PriorityLevel.NORMAL,
            'high': PriorityLevel.HIGH,
            'urgent': PriorityLevel.URGENT,
            'critical': PriorityLevel.CRITICAL
        }
        priority_level = priority_mapping[priority]
        
        # Validate against the target's declared capabilities and fill its defaults.
        tm = getattr(ctx.framework_manager, "target_manager", None)
        if tm:
            # The registered id (also for its folder name or another letter case), so `-t rockchip` is checked
            # like `-t Rockchip`; an unknown target would otherwise "complete" with nothing built.
            match = tm.resolve(target)
            if match is None:
                known = [str(t.get("id")) for t in tm.list_targets() if t.get("id")]
                console.print(f"[red]Unknown target '{escape(target)}' (targets: {escape(', '.join(known) or 'none')}).[/red]")
                sys.exit(2)
            target = target_arch_str = match
        caps = tm.get_capabilities(target) if tm else None
        if caps:
            # The values the build really runs with (canonical spellings, the scripts' default board).
            _, board, quantization = caps.normalize(format, board, quantization)
            problems = caps.check_request(format, board, quantization, dataset=bool(dataset), imatrix=imatrix)
            if problems:
                for problem in problems:
                    console.print(f"[red]{escape(problem)}[/red]")
                sys.exit(2)
        elif board:
            board = board.strip().lower()  # the module scripts lower-case it as well

        # A path given here means the current directory; the configured default the installation's.
        if output_dir:
            output_dir = str(Path(output_dir).expanduser().resolve())
        else:
            base = ctx.framework_manager.resolve_path(ctx.framework_manager.config.get("output_dir", "output"))
            output_dir = str(base / f"build_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        
        build_request = BuildRequest(
            request_id="",
            workflow_type=WorkflowType.SIMPLE_CONVERSION,
            priority=priority_level,
            models=[model],
            targets=[target_arch_str], # List of strings
            target_formats=[target_format_enum],
            optimization_level=optimization_level,
            quantization_options=[quantization] if quantization else [],
            parallel_builds=False,
            output_base_dir=output_dir,
            description=f"CLI build: {model} -> {target} ({format})",
            use_gpu=gpu,
            # Pass IMatrix Flags to Orchestrator
            use_imatrix=imatrix,
            dataset_path=str(Path(dataset).resolve()) if dataset else None,
            target_board=board
        )
        
        # The orchestrator runs on the framework's background loop (as for the GUI and the build service), so the
        # build service can cancel the build from here on Ctrl+C.
        fm = ctx.framework_manager
        request_id = fm.run_coroutine(ctx.orchestrator.submit_build_request(build_request), timeout=30)

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
                workflow_status = fm.run_coroutine(ctx.orchestrator.get_workflow_status(request_id), timeout=30)
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
    """Asks on the terminal whether an automatic fix may be applied (manual steps are only shown)."""
    automatic = (not getattr(proposal, "is_ai_generated", False)
                 and getattr(getattr(proposal, "strategy", None), "value", "") in ("config_adjustment", "docker_prune", "retry_build"))
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


@build.command('status')
@click.argument('request_id', required=False)
@click.option('--all', '-a', is_flag=True, help=f'Show every kept build (default: the {STATUS_ROWS} newest)')
@pass_context
def build_status(ctx: FrameworkContext, request_id: Optional[str], all: bool):
    """Status of builds, including those the GUI, the MCP server or another CLI call started (the shared build state
    in the per-user folder, and the build records in the cache folder). REQUEST_ID: a request id (req_...) or a
    build id."""
    from orchestrator.Core.build_records import read_records, records_dir
    config, resolve = _settings(ctx)
    folder = records_dir(resolve(config.get("cache_dir", "cache")))
    records = read_records(folder)
    requests = _shared_requests(config)
    by_request = {r.get("request_id"): r for r in requests}
    with_builds = {r.get("request_id") for r in records}
    # Requests without a build record yet (queued, preparing, or ended before a build ran)
    waiting = [r for r in requests if r.get("request_id") not in with_builds]
    if request_id:
        matches = [r for r in records if request_id in (r.get("request_id"), r.get("build_id"))]
        request = by_request.get(request_id)
        if not matches and request is None:
            console.print(f"[red]Build '{escape(request_id)}' not found in {escape(str(folder))}.[/red]")
            sys.exit(1)
        if request is not None:
            _print_request_summary(request)
        for record in reversed(matches):  # attempts in order
            _print_build_record(record)
        for rid in dict.fromkeys([r.get("request_id") for r in matches if r.get("request_id")]
                                 + ([request_id] if request is not None else [])):
            _print_client_proposals(_client_proposals(config, rid))
        return
    if not records and not waiting:
        console.print(f"[yellow]No builds found in {escape(str(folder))}.[/yellow]")
        return
    rows = sorted([("b", r) for r in records] + [("r", r) for r in waiting],
                  key=lambda pair: str(pair[1].get("started") or ""), reverse=True)
    shown = rows if all else rows[:STATUS_ROWS]
    table = Table(title="Builds")
    for column in ("Request", "Build", "Status", "Progress", "Target", "Format", "Quantization", "Model",
                   "Started", "Where"):
        table.add_column(column)
    for kind, r in shown:
        if kind == "r":
            summary = r.get("summary") if isinstance(r.get("summary"), dict) else {}
            table.add_row(escape(str(r.get("request_id"))), "-", escape(str(r.get("status"))),
                          f"{r.get('progress') or 0}%", escape(str(summary.get("target") or "-")),
                          escape(str(summary.get("format") or "-")), escape(str(summary.get("quantization") or "-")),
                          escape(str(summary.get("model") or "-")), escape(str(r.get("started") or "-")),
                          escape(str(r.get("program") or "-")))
            continue
        where = (by_request.get(r.get("request_id")) or {}).get("program") or "-"
        table.add_row(escape(str(r.get("request_id") or "-")), escape(str(r.get("build_id"))),
                      escape(str(r.get("status"))), f"{r.get('progress') or 0}%", escape(str(r.get("target") or "-")),
                      escape(str(r.get("format") or "-")), escape(str(r.get("quantization") or "-")),
                      escape(str(r.get("model") or "-")), escape(str(r.get("started") or "-")), escape(str(where)))
    console.print(table)
    if len(rows) > len(shown):
        console.print(f"{len(rows) - len(shown)} older builds: llm-cli build status --all")
    proposed = sorted({p.request_id for p in _client_proposals(config)})
    if proposed:
        console.print(f"[magenta]AI clients proposed fixes for {len(proposed)} failed build(s): "
                      f"llm-cli build status {escape(proposed[-1])}[/magenta]")


def _shared_requests(config) -> list:
    """The build request summaries every framework process writes into the per-user folder (request_store)."""
    from orchestrator.Core.request_store import for_config
    try:
        store = for_config(config)
        return store.read() if store is not None else []
    except OSError:
        return []


def _print_request_summary(r: dict) -> None:
    summary = r.get("summary") if isinstance(r.get("summary"), dict) else {}
    rows = [("Request", r.get("request_id")), ("Status", r.get("status")), ("Stage", r.get("stage")),
            ("Progress", f"{r.get('progress') or 0}%"), ("Model", summary.get("model")),
            ("Target", summary.get("target")), ("Board", summary.get("board")), ("Format", summary.get("format")),
            ("Quantization", summary.get("quantization")), ("Optimization", summary.get("optimization")),
            ("Started by", r.get("owner") or "the person"), ("Where", r.get("program")),
            ("Started", r.get("started")), ("Ended", r.get("ended"))]
    for label, value in rows:
        if value not in (None, ""):
            console.print(f"[bold]{label}:[/bold] {escape(str(value))}")
    for error in r.get("errors") or []:
        console.print(f"[bold]Error:[/bold] {escape(str(error))}")
    if r.get("status") == "interrupted":
        console.print("[yellow]The process that ran this request has ended before it finished.[/yellow]")
    elif r.get("awaiting_decision"):
        console.print(f"[yellow]Waits for a decision on a self-healing proposal in the {escape(str(r.get('program')))}"
                      " that started it.[/yellow]")
    console.print("")


def _client_proposals(config, request_id: Optional[str] = None) -> list:
    """The fix proposals AI clients made (propose_healing_fix), kept in the per-user folder."""
    from orchestrator.Core.client_proposals import ClientProposalStore
    state_dir = getattr(config, "user_state_dir", None)
    return ClientProposalStore(Path(state_dir)).list(request_id) if state_dir else []


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
    rows = [("Build", r.get("build_id")), ("Request", r.get("request_id")), ("Status", r.get("status")),
            ("Stage", r.get("stage")), ("Progress", f"{r.get('progress') or 0}%"), ("Model", r.get("model")),
            ("Target", r.get("target")), ("Board", r.get("board")), ("Format", r.get("format")),
            ("Quantization", r.get("quantization")), ("Output", r.get("output_dir")),
            ("Started", r.get("started")), ("Ended", r.get("ended")), ("Process", r.get("pid")),
            ("Error", r.get("error"))]
    for label, value in rows:
        if value not in (None, ""):
            console.print(f"[bold]{label}:[/bold] {escape(str(value))}")
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

    sm = ctx.framework_manager.secrets_manager
    if not sm.set_secret(key, password):
        console.print(f"[red]Failed to store secret '{escape(key)}'.[/red]")
        sys.exit(1)
    elif getattr(sm, "storage_mode", None) == "ephemeral":
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
        
    keys = ctx.framework_manager.secrets_manager.list_secrets()
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
    from orchestrator.tools.policy import TIER_LABELS, CallerPolicy, Decision, ToolTier
    _require_terminal("llm-cli tools permissions")
    section = current.callers.get(caller) if caller else None
    base_tiers = section.tiers if section is not None else current.tiers
    # A caller's new section starts from the shared settings, so creating it never loosens a per-tool setting.
    overrides = dict((section if section is not None else current).overrides)
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
    if caller:
        callers = dict(current.callers)
        callers[caller] = CallerPolicy(tiers=tiers, overrides=overrides, reviewed=reviewed)
        return current.model_copy(update={"configured": True, "callers": callers})
    return current.model_copy(update={"configured": True, "tiers": tiers, "overrides": overrides,
                                      "reviewed": reviewed})


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
    from orchestrator.tools.policy import CallerPolicy
    known = {spec.name for spec in registry.list()}
    unknown = sorted((set(add) | set(remove)) - known)
    if unknown:
        console.print(f"[red]Unknown tools: {escape(', '.join(unknown))} ('llm-cli tools list' shows them).[/red]")
        sys.exit(2)

    def change(current):
        section = current.callers.get(caller) if caller else current
        if caller and section is None:  # a caller's first own section starts from the shared permissions
            section = CallerPolicy(tiers=dict(current.tiers), overrides=dict(current.overrides),
                                   reviewed=current.reviewed, framework_only=list(current.framework_only))
        tools = sorted((set(section.framework_only) | set(add)) - set(remove))
        if caller:
            return current.model_copy(update={"callers": {**current.callers,
                                                          caller: section.model_copy(update={"framework_only": tools})}})
        return current.model_copy(update={"framework_only": tools})
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
    from orchestrator.tools.policy import ToolPolicy
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
        if caller:
            _save_policy(registry, change=lambda current: current.model_copy(update={
                "callers": {name: section for name, section in current.callers.items() if name != caller},
                "grants": {name: grants for name, grants in current.grants.items() if name != caller}}))
            console.print(f"[yellow]{CALLER_LABELS[caller]} use the shared tool permissions again.[/yellow]")
            return
        _save_policy(registry, change=lambda current: ToolPolicy())
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
    """The audit view for people: who (the caller in words, the client as it named itself), the tool by its title
    (and the name the AI wrote, when that was a spelling variant), how the person was asked and what they answered,
    the reason code; with `details` also the repairs, duration, result, links and session."""
    from orchestrator.tools.registry import ToolError, caller_label

    def title(name: str) -> str:
        try:
            return registry.get(name).title
        except ToolError:
            return name

    for entry in entries:
        who = caller_label(str(entry.get("actor") or ""))
        if entry.get("client"):
            who += f"\n{entry['client']} (self-reported)"
        tool = str(entry.get("tool") or "")
        shown = title(tool) + (f" ({tool})" if title(tool) != tool else "")
        if entry.get("name_requested") and entry["name_requested"] != tool:
            shown += f"\nthe AI wrote {entry['name_requested']}"
        asked = entry.get("confirm_channel") or ""
        if entry.get("answer"):
            asked = f"{asked}: {entry['answer']}" if asked else str(entry["answer"])
        row = [str(entry.get("ts", "")), who, shown, str(entry.get("decision", "")),
               {None: "-", 0: "no", 1: "yes"}.get(entry.get("approved"), "?"), str(entry.get("outcome", "")),
               str(entry.get("reason_code") or ""), asked or "-"]
        if details:
            repairs = entry.get("repairs") if isinstance(entry.get("repairs"), list) else []
            result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
            row += [", ".join(f"{r.get('kind')}:{r.get('field') or '-'}" for r in repairs if isinstance(r, dict)),
                    str(entry.get("duration_ms") or ""),
                    (f"{str(result.get('sha256', ''))[:12]} {result.get('bytes', '')} B" if result else ""),
                    ", ".join(str(link) for link in entry.get("links") or []), str(entry.get("session") or "")]
        yield row


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
            rows = _audit_summary(registry.audit.entries(100000))
        except (sqlite3.Error, OSError) as e:
            console.print(f"[red]The audit log {escape(str(registry.audit.path))} cannot be read: "
                          f"{escape(str(e))}[/red]")
            sys.exit(1)
        table = Table(title="Tool calls per caller")
        for column in ("Who", "Calls", "Refused", "Cannot reported", "confirmed", "contradicted", "premature",
                       "unchecked"):
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
    columns = ["Time", "Who", "Tool", "Decision", "Confirmed", "Outcome", "Reason", "Asked / answer"]
    if details:
        columns += ["Repairs", "ms", "Result", "Links", "Session"]
    for column in columns:
        table.add_column(column)
    for row in _audit_rows(registry, entries, details):
        table.add_row(*(escape(cell) for cell in row))
    console.print(table)


CANNOT_CHECKS = ("confirmed", "contradicted", "premature", "unchecked")
REFUSED_OUTCOMES = ("denied", "not_confirmed", "rate_limited", "repeat_refused", "invalid_input", "unknown_tool")


def _audit_summary(entries) -> list:
    """Rows per caller: calls (end entries), refusals, report_cannot_do reports and how they were checked (design
    6.11 D14: the honest "cannot" in use)."""
    per: dict = {}
    for entry in entries:
        actor = str(entry.get("actor") or "?")
        row = per.setdefault(actor, {"calls": 0, "refused": 0, "cannot": 0, **{c: 0 for c in CANNOT_CHECKS}})
        outcome = str(entry.get("outcome") or "")
        if outcome == "cannot_reported":
            row["cannot"] += 1
            check = str(entry.get("detail") or "").split(":")[-1].split("\n")[0].strip()
            if check in CANNOT_CHECKS:
                row[check] += 1
        elif outcome in REFUSED_OUTCOMES:
            row["refused"] += 1
        elif outcome in ("ok", "error", "cancelled"):
            row["calls"] += 1
    return [[actor, r["calls"], r["refused"], r["cannot"], *(r[c] for c in CANNOT_CHECKS)]
            for actor, r in sorted(per.items())]


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


@cli.group()
def ditto():
    """Ditto, the AI assistant: sign-in (login, logout, auth), questions, evaluation and the local model"""
    pass


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
                   console=console, transient=True)
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


@ditto.group('remote')
def ditto_remote():
    """Ditto's MCP-client mode: use the tools of a framework running elsewhere (for example the headless Docker
    image) through its MCP server, with a named token linked to Ditto's permissions there"""
    pass


def _remote_settings_text(config) -> List[str]:
    from orchestrator.llm.remote_tools import (COMMAND_SETTING, NAME_SETTING, SOURCE_SETTING, TRANSPORT_SETTING,
                                               URL_SETTING)
    return [f"Tool source: {config.get(SOURCE_SETTING, 'local') or 'local'}",
            f"Transport: {config.get(TRANSPORT_SETTING, 'stdio') or 'stdio'}",
            f"Command (stdio): {' '.join(config.get(COMMAND_SETTING, []) or []) or '-'}",
            f"Address (http): {config.get(URL_SETTING, '') or '-'}",
            f"Name: {config.get(NAME_SETTING, '') or '-'}"]


@ditto_remote.command('set')
@click.option('--transport', type=click.Choice(['stdio', 'http']), required=True,
              help='stdio: Ditto starts the server command (with LCF_MCP_TOKEN); http: Ditto connects to the address '
                   'with the token as bearer header')
@click.option('--command', 'command', default=None,
              help='stdio: the command that starts the server, e.g. "docker exec -i lcf llm-cli mcp serve"')
@click.option('--url', default=None, help='http: the server address (https, or http on a loopback address)')
@click.option('--name', default='', help='Your name for that framework, shown in Ditto\'s dialogs')
@click.option('--with-token', is_flag=True, help='Read the named token from stdin instead of asking for it')
@pass_context
def ditto_remote_set(ctx: FrameworkContext, transport: str, command: Optional[str], url: Optional[str], name: str,
                     with_token: bool):
    """Switch Ditto to the tools of another framework's MCP server. Create the token there first:
    llm-cli mcp token add NAME --link ditto (its permissions are then Ditto's own permissions on that framework)."""
    import shlex
    from orchestrator.llm.remote_tools import (COMMAND_SETTING, NAME_SETTING, SOURCE_SETTING, TOKEN_SECRET,
                                               TRANSPORT_SETTING, URL_SETTING, RemoteSetupError, check_url)
    if not with_token:
        _require_terminal("llm-cli ditto remote set")
    argv: List[str] = []
    try:
        if transport == 'stdio':
            argv = shlex.split(command or "")
            if not argv:
                _usage("--command is required for --transport stdio.")
        else:
            check_url(url or "")
    except RemoteSetupError as e:
        _usage(str(e))
    except ValueError as e:
        _usage(f"--command cannot be read: {e}")
    ctx.require_framework()
    sm = ctx.framework_manager.secrets_manager
    if sm is None:
        console.print("[red]The credential vault is not available; the token cannot be stored.[/red]")
        sys.exit(1)
    token = sys.stdin.read().strip() if with_token else click.prompt("Named MCP token", hide_input=True)
    if not token:
        _usage("No token given.")
    if not sm.set_secret(TOKEN_SECRET, token):
        console.print("[red]The token could not be stored.[/red]")
        sys.exit(1)
    config = ctx.framework_manager.config
    for key, value in ((SOURCE_SETTING, "mcp"), (TRANSPORT_SETTING, transport), (COMMAND_SETTING, argv),
                       (URL_SETTING, url or ""), (NAME_SETTING, name)):
        config.set(key, value)
    if not config.save_user_config():
        console.print("[red]The settings could not be saved.[/red]")
        sys.exit(1)
    console.print("[green]Ditto now uses the tools of that framework (MCP-client mode); the token is in the "
                  "credential vault.[/green]")
    console.print("Check the connection with: llm-cli ditto remote test")


@ditto_remote.command('off')
@pass_context
def ditto_remote_off(ctx: FrameworkContext):
    """Back to Ditto's own tools in this framework (the stored token is kept; remove it with
    llm-cli secrets delete ditto_mcp_token)."""
    from orchestrator.llm.remote_tools import SOURCE_SETTING
    config, _ = _settings(ctx)
    config.set(SOURCE_SETTING, "local")
    if not config.save_user_config():
        console.print("[red]The settings could not be saved.[/red]")
        sys.exit(1)
    console.print("[green]Ditto uses its own tools again.[/green]")


@ditto_remote.command('show')
@pass_context
def ditto_remote_show(ctx: FrameworkContext):
    """Ditto's tool source and the remote server settings (never the token)"""
    config, _ = _settings(ctx)
    for line in _remote_settings_text(config):
        console.print(escape(line))


@ditto_remote.command('test')
@pass_context
def ditto_remote_test(ctx: FrameworkContext):
    """Connect to the remote framework with the stored token and list the tools and task prompts it offers Ditto"""
    from orchestrator.llm.remote_tools import RemoteConnection, RemoteSetupError, TOKEN_NEXT, remote_server, \
        stored_token
    ctx.require_framework()
    try:
        server = remote_server(ctx.framework_manager.config)
    except RemoteSetupError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    if server is None:
        console.print("Ditto uses its own tools (no remote server set): llm-cli ditto remote set ...")
        return
    token = stored_token(ctx.framework_manager.secrets_manager)
    if not token:
        console.print(f"[red]No named MCP token is stored. {escape(TOKEN_NEXT)}[/red]")
        sys.exit(1)
    try:
        with RemoteConnection(server, token, _ditto_confirm) as remote:
            tools, prompts = remote.tools(), remote.prompts()
    except RemoteSetupError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    console.print(f"Framework at {escape(server.label())}: {len(tools)} tools Ditto may use there, "
                  f"{len(prompts)} task prompts.")
    for spec in tools:
        console.print(f"  {escape(spec.name)}")


@ditto.group('runtime')
def ditto_runtime():
    """Local model server (llama.cpp) for offline use"""
    pass


def _installed_json(inst) -> Optional[dict]:
    if inst is None:
        return None
    data = inst.model_dump(mode="json", include={"origin", "build_id", "commit", "backend", "variant", "asset_id",
                                                 "role", "placement", "built_on", "built_at", "verified_at",
                                                 "duration_s", "devices", "notes"})
    data["label"] = inst.label
    data["path"] = str(inst.path)
    return data


def _installed_line(inst) -> str:
    """origin label, build, backend and commit of the build in use (escaped for rich)."""
    if inst.build_id:
        role = ", CPU fallback" if inst.role == "fallback" else ""
        text = (f"{inst.label}: build {inst.build_id[:16]} ({inst.backend}{role}), llama.cpp {inst.build} commit "
                f"{(inst.commit or '')[:8]}")
    else:
        text = f"{inst.label}: {inst.asset_id} ({inst.backend})"
    return escape(text)


@ditto_runtime.command('status')
@click.option('--json', 'as_json', is_flag=True, help='Machine-readable output')
@pass_context
def runtime_status(ctx: FrameworkContext, as_json: bool):
    """Hardware, the build in use (self-built, imported or prebuilt), the recommended model, and the server"""
    rt = _local_runtime(ctx)
    with console.status("Checking the hardware and the builds ..."):
        status = rt.status(check_rebuild=True)
        hardware = rt.hardware()
        backend, backend_reason = rt.recommend_backend()
        prebuilt = rt.select_backend()
        model, why = rt.choose_tier()
    preferred = rt.preferred_tier()
    inst = status.installed
    if as_json:
        click.echo(json.dumps({
            "build": status.build, "hardware": hardware.model_dump(mode="json"),
            "installed": _installed_json(inst), "problems": status.problems, "rebuild": status.rebuild,
            "recommended_backend": backend, "backend_reason": backend_reason,
            "prebuilt_option": prebuilt.asset.id if prebuilt.asset else None, "prebuilt_reason": prebuilt.reason,
            "running": status.running, "healthy": status.healthy, "base_url": status.base_url, "pid": status.pid,
            "model": status.model, "tier": status.tier, "ctx": status.ctx,
            "recommended_tier": model.tier, "tier_reason": why, "preferred_tier": preferred,
            "log_file": str(status.log_file)}, indent=2))
        return
    table = Table(title=f"Local runtime (llama.cpp {escape(status.build)})", show_header=False)
    table.add_column("", style="cyan")
    table.add_column("")
    table.add_row("Hardware", escape(hardware.summary()))
    if inst is not None:
        style = "yellow" if inst.origin == "prebuilt" else "green"
        table.add_row("In use", f"[{style}]{_installed_line(inst)}[/{style}]")
        if inst.build_id:
            built = [f"{inst.placement or '?'} build"]
            if inst.duration_s is not None:
                built.append(f"{inst.duration_s:.0f} s")
            if inst.built_on and inst.origin == "imported":
                built.append(f"on {inst.built_on}")
            if inst.built_at:
                built.append(inst.built_at)
            table.add_row("Built", escape(", ".join(built)))
            devices = ", ".join(inst.devices) or "CPU only"
            table.add_row("Verified", escape(f"{inst.verified_at or '?'}; devices: {devices}"))
        for note in inst.notes:
            table.add_row("Note", f"[yellow]{escape(note)}[/yellow]")
    else:
        table.add_row("In use", "[yellow]nothing[/yellow] (llm-cli ditto runtime install builds llama-server)")
    for problem in status.problems:
        if inst is None or problem not in inst.notes:
            table.add_row("Problem", f"[red]{escape(problem)}[/red]")
    if status.rebuild:
        table.add_row("Rebuild", f"[yellow]{escape(status.rebuild)}[/yellow]")
    table.add_row("Self-built plan", escape(f"{backend}: {backend_reason}" if backend == "cpu" or not backend_reason
                                            else f"CPU build, then {backend}: {backend_reason}"))
    table.add_row("Prebuilt opt-in", escape(f"{prebuilt.asset.id if prebuilt.asset else '-'} (--prebuilt, not "
                                            f"self-built): {prebuilt.reason}"))
    if status.running:
        health = "[green]ready[/green]" if status.healthy else "[yellow]starting / not answering[/yellow]"
        table.add_row("Server", f"{health} {escape(str(status.base_url))} (pid {status.pid}, "
                                f"model {escape(str(status.model))}, ctx {status.ctx})")
    else:
        table.add_row("Server", "stopped")
    table.add_row("Recommended model", escape(f"{model.file} - {why}"))
    if preferred and preferred != model.tier:
        table.add_row("Chosen model", escape(f"{preferred} (llm-cli ditto runtime start --model auto resets it)"))
    table.add_row("Log", escape(str(status.log_file)))
    console.print(table)


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
    if result.removed:
        console.print(f"[dim]Removed {len(result.removed)} old build(s).[/dim]")
    if rt.endpoint() is not None:
        console.print("The running server keeps running; the next start uses the new build "
                      "(llm-cli ditto runtime start).")
    if result.requested_missing:
        sys.exit(1)


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
@click.option('--keep', type=click.IntRange(0, 50), default=2, show_default=True,
              help='Other builds to keep besides the active build, the CPU fallback and the running one')
@pass_context
def runtime_prune(ctx: FrameworkContext, keep: int):
    """Remove old self-built builds (never the active build, the CPU fallback or the running one)"""
    from orchestrator.runtime.llama_server import LocalRuntimeError
    try:
        removed = _local_runtime(ctx).prune(keep_last=keep)
    except LocalRuntimeError as e:
        _runtime_failure(e)
    console.print(f"Removed {len(removed)} build(s)." if removed else "Nothing to remove.")


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
    console.print(f"[green]Running:[/green] {escape(ep.base_url)} model {escape(ep.model)} (pid {ep.pid}, "
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
    from orchestrator.runtime.catalog import load_catalog
    source = load_catalog().llama_cpp.source
    if source is None:
        console.print("[red]configs/llama_runtime.yml has no llama_cpp.source (the pinned commit).[/red]")
        sys.exit(1)
    return source


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
    rt = _local_runtime(ctx)
    recommended, why = rt.choose_tier()
    preferred = rt.preferred_tier()
    table = Table(title="Ditto models")
    for column in ("Tier", "File", "Size", "License", "Status", "Note"):
        table.add_column(column)
    labels = {"ready": "[green]downloaded[/green]", "unverified": "[yellow]not verified yet[/yellow]", "missing": "-"}
    for m in rt.catalog.models:
        notes = []
        if m.tier == recommended.tier:
            notes.append("recommended")
        if m.tier == preferred:
            notes.append("chosen")
        if m.on_request and not notes:
            notes.append("on request")
        if not m.pinned:
            notes.append("[red]not pinned[/red]")
        table.add_row(escape(m.tier), escape(m.file), _gib(m.size), escape(m.license), labels[rt.model_state(m)],
                      ", ".join(notes))
    console.print(table)
    console.print(f"Recommended for this machine: {escape(why)}")


@ditto_models.command('download')
@click.argument('tier', required=False)
@click.option('--yes', '-y', is_flag=True, help='Do not ask before downloading')
@pass_context
def models_download(ctx: FrameworkContext, tier: Optional[str], yes: bool):
    """Download a model at its pinned revision and check size and sha256 (default: the recommended tier)"""
    from orchestrator.runtime.llama_server import LocalRuntimeError
    rt = _local_runtime(ctx)
    if tier is None:
        model, why = rt.choose_tier()
        console.print(f"Recommended: {escape(why)}")
    else:
        try:
            model = rt.catalog.model(tier)
        except KeyError as e:
            console.print(f"[red]{escape(str(e.args[0]))}[/red]")
            sys.exit(2)
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

    def tool_used(use):
        if use.ok:
            err.print(f"[dim]Used tool: {escape(use.title)}[/dim]")
        else:
            err.print(f"[yellow]Tool not run: {escape(use.title)} ({escape(use.detail)})[/yellow]")

    try:
        reply = ditto.chat(question, confirm=_ditto_confirm, use_tools=not no_tools, on_tool=tool_used)
    except DittoError as e:
        err.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    click.echo(reply.text)


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
    from orchestrator.evals.ditto_eval import DEFAULT_CASES, RETRIEVAL_CASES, DittoEval, load_cases, run_setup
    ctx.require_framework()
    if tiers and (provider or model):
        console.print("[red]--tier runs the managed local model; it cannot be combined with --provider/--model.[/red]")
        sys.exit(2)
    if cases_file:
        files = [cases_file]
    else:  # retrieval cases need the embedding model, not an AI model: only when asked for
        files = [] if set(kinds) == {"retrieval"} else [DEFAULT_CASES]
        if "retrieval" in kinds or (case_ids and not kinds):
            files.append(RETRIEVAL_CASES)
    try:
        cases = [c for path in files for c in load_cases(path)]
    except (OSError, ValueError) as e:
        console.print(f"[red]Cannot read the cases: {escape(str(e))}[/red]")
        sys.exit(2)
    cases = [c for c in cases if (not kinds or c.kind in kinds) and (not case_ids or c.id in case_ids)]
    if not cases:
        console.print("[red]No case matches the filters.[/red]")
        sys.exit(2)
    ai_cases = [c for c in cases if c.kind != "retrieval"]
    retrieval_cases = [c for c in cases if c.kind == "retrieval"]
    fm = ctx.framework_manager
    runs = []

    def progress(result):
        mark = "[green]pass[/green]" if result.pass_rate == 1 else (
            "[red]fail[/red]" if result.pass_rate == 0 else f"[yellow]{result.pass_rate:.0%}[/yellow]")
        note = f"  [dim]{escape(result.first_problem()[:160])}[/dim]" if result.pass_rate < 1 else ""
        console.print(f"  {mark} {escape(result.case.id)} ({result.seconds:.1f}s){note}")

    if ai_cases:
        ditto = _ai_override(ctx, "local" if tiers else provider, model)
        if effort:
            for key in ("ai_effort", "ai_effort_chat", "ai_effort_tools", "ai_effort_json", "ai_effort_healing"):
                fm.config.set(key, effort)  # this run only; the saved settings stay unchanged
        targets_dir = fm.resolve_path(fm.config.get("targets_dir", "targets"))

        def run_once(label: str):
            from orchestrator.Core.ditto_manager import DittoError
            ditto.reload()
            try:
                setup = ditto.describe("chat")
            except DittoError as e:
                console.print(f"[red]{escape(str(e))}[/red]")
                sys.exit(1)
            console.print(f"[bold]{escape(label)}[/bold]: {escape(setup['label'])} / {escape(setup['model'])}, "
                          f"{len(ai_cases)} cases x {repeat}")
            report = DittoEval(ditto, targets_dir).run(ai_cases, repeat=repeat, on_result=progress)
            models = {}
            for task in ("chat", "json", "healing"):  # each task may use its own model
                try:
                    models[task] = ditto.describe(task).get("model", "")
                except DittoError:
                    models[task] = ""
            report.setup = run_setup(label, setup.get("provider", ""), setup.get("model", ""), cases_file, ai_cases,
                                     repeat, task_models=models, effort=effort or "settings")
            runs.append(report)

        if tiers:
            from orchestrator.runtime.llama_server import LocalRuntimeError
            rt = _local_runtime(ctx)
            before = rt.endpoint()  # a server you started yourself keeps running
            for tier in tiers:
                try:
                    with console.status(f"Starting llama-server with {tier} ..."):
                        rt.start(tier=tier, remember=False)
                    run_once(f"tier {tier}")
                except LocalRuntimeError as e:
                    _runtime_failure(e)
                finally:
                    running = rt.endpoint()
                    if before is None or running is None or running.pid != before.pid:
                        rt.stop()  # the eval's own server (or what is left of a failed start)
            if before is not None and getattr(rt.endpoint(), "pid", None) != before.pid:
                console.print(f"[yellow]Your llama-server ({escape(before.model)}) was replaced for the evaluation; "
                              "start it again with: llm-cli ditto runtime start[/yellow]")
        else:
            run_once("configured model" if not (provider or model) else f"{provider or ''} {model or ''}".strip())
    elif tiers or provider or model or effort:
        console.print("[dim]--tier, --provider, --model and --effort apply to AI cases; retrieval needs none.[/dim]")
    if retrieval_cases:
        runs += _eval_retrieval(fm, retrieval_cases, cases_file or RETRIEVAL_CASES, rag_variants or ("fused",),
                                repeat, progress)

    all_kinds = sorted({c.kind for c in cases})
    table = Table(title="Ditto evaluation")
    for column in ("Run", "Model", "Passed", *all_kinds, "Avg s/case"):
        table.add_column(column)
    for report in runs:
        kinds_rate = report.by_kind()
        avg = sum(r.seconds for r in report.results) / len(report.results)
        table.add_row(escape(report.setup["label"]), escape(report.setup["model"]), f"{report.pass_rate:.0%}",
                      *[f"{kinds_rate[k]:.0%}" if k in kinds_rate else "-" for k in all_kinds], f"{avg:.1f}")
    console.print(table)
    metrics = [(report, report.retrieval()) for report in runs if report.retrieval() is not None]
    if metrics:
        def share(value):
            return "-" if value is None else f"{value:.0%}"

        table = Table(title="Retrieval")
        for column in ("Run", "hit@1", "hit@3", "hit@k", "MRR", "Unrelated: no excerpt", "p50 ms"):
            table.add_column(column)
        for report, m in metrics:
            table.add_row(escape(report.setup["label"]), share(m["hit@1"]), share(m["hit@3"]), share(m["hit@k"]),
                          "-" if m["mrr"] is None else f"{m['mrr']:.3f}", share(m["empty_when_irrelevant"]),
                          f"{m['latency_p50_ms']:.0f}")
        console.print(table)
    if json_out:
        json_out.write_text(json.dumps({"runs": [r.to_dict() for r in runs]}, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        console.print(f"Report written to {escape(str(json_out))}")
    if min_pass is not None and any(r.pass_rate < min_pass for r in runs):
        sys.exit(1)


def _eval_retrieval(fm, cases, source: Path, variants, repeat: int, progress) -> list:
    """Retrieval cases: the corpus is indexed once into a temporary knowledge base, one report per variant."""
    import time
    from orchestrator.evals.ditto_eval import (RetrievalEval, RetrievalEvalError, check_retrieval_cases,
                                               corpus_digest, corpus_dir, load_corpus, run_setup,
                                               temporary_knowledge_base)
    try:
        folder = corpus_dir(source)
        docs = load_corpus(folder)
        check_retrieval_cases(cases, docs)
    except (OSError, ValueError) as e:
        console.print(f"[red]Cannot read the retrieval corpus: {escape(str(e))}[/red]")
        sys.exit(2)
    reports = []
    with temporary_knowledge_base(fm) as knowledge:
        ev = RetrievalEval(knowledge, docs)
        start = time.monotonic()
        try:
            with console.status(f"Indexing {len(docs)} corpus documents into a temporary knowledge base "
                                "(the embedding model is downloaded on first use) ..."):
                ev.ingest()
        except RetrievalEvalError as e:
            console.print(f"[red]{escape(str(e))}[/red]")
            sys.exit(1)
        index_seconds = round(time.monotonic() - start, 1)
        embedding = str(knowledge.get_status().get("embedding_model", ""))
        reranker = knowledge.reranker() if "rerank" in variants else None
        if "rerank" in variants:
            if reranker is None:
                console.print(f"[red]The rerank variant needs a reranker: {escape(knowledge.reranker_reason)}. Set "
                              "rag_reranker to a model id to grade one anyway.[/red]")
                sys.exit(2)
            with console.status(f"Loading the reranker {reranker.model_id} (downloaded on first use) ..."):
                ready = knowledge.prepare_models()
            if not ready:
                console.print(f"[red]{escape(knowledge.last_error)}[/red]")
                sys.exit(1)
        for variant in dict.fromkeys(variants):
            label, reranked = f"retrieval {variant}", variant == "rerank"
            model = f"{embedding} + {reranker.model_id}" if reranked else embedding
            console.print(f"[bold]{escape(label)}[/bold]: {escape(model)}, {len(cases)} cases x {repeat}")
            report = ev.run(cases, variant=variant, repeat=repeat, on_result=progress)
            try:
                import psutil
                rss_mb = round(psutil.Process().memory_info().rss / 2 ** 20)  # models loaded
            except Exception:
                rss_mb = None
            used = {"model": reranker.model_id, "min_score": knowledge.rerank_floor(reranker)} if reranked else None
            report.setup = run_setup(
                label, "knowledge base", model, source, cases, repeat, variant=variant, reranker=used,
                model_reason=knowledge.model_reason, corpus_sha256=corpus_digest(folder), corpus_documents=len(docs),
                index_seconds=index_seconds, rss_mb=rss_mb)
            reports.append(report)
    return reports


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


def _usage(message: str):
    _say(message, err=True, fg="red")
    sys.exit(EXIT_USAGE)


def _auth_fail(error: BaseException, code: Optional[int] = None, service=None):
    """The error, its "Next:" lines, and the exit code of its kind (3 rejected, 4 network, 5 storage, 6 policy)."""
    from orchestrator.llm.auth.signin import exit_code
    if (getattr(error, "code", None) or "").startswith("storage."):
        _storage_fail(error, service=service)
    _say(str(error), err=True, fg="red")
    for action in getattr(error, "actions", ()) or ():
        target = action.command or action.url
        _say(f"Next: {action.label}" + (f": {target}" if target else ""), err=True)
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


def _read_key(spec, preset, *, with_token: bool, from_env: bool) -> Tuple[str, str]:
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
        if not spec.env:
            _usage(f"{preset.label} ({spec.label}) reads no environment variable; use --with-token or the prompt.")
        name = next((v for v in spec.env if (os.environ.get(v) or "").strip()), None)
        if name is None:
            _usage(f"{' and '.join(spec.env)} {'is' if len(spec.env) == 1 else 'are'} not set.")
        if malformed_key(os.environ[name].strip()):
            _usage(f"{name} has a space, a line break or another character that is never part of a key.")
        return os.environ[name].strip(), f"env:{name}"
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
    from orchestrator.llm.auth.signin import exit_code, plain_http_to_remote, valid_profile_name
    from orchestrator.llm.auth.spec import KEY_TYPES
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
    key, source = _read_key(spec, preset, with_token=with_token, from_env=from_env)
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
# DOCTOR (deterministic health checks)
# ============================================================================

@cli.command('doctor')
@click.option('--json', 'as_json', is_flag=True, help='Machine-readable output')
@click.option('--fix', is_flag=True, help='Apply automatic fixes up to --tier, then check again')
@click.option('--tier', default=1, show_default=True, type=click.IntRange(1, 2),
              help='1: harmless fixes (makes your personal folder private); 2: also removes caches (dangling '
                   'framework images, Docker build cache)')
@pass_context
def doctor(ctx: FrameworkContext, as_json: bool, fix: bool, tier: int):
    """Check the installation and explain how to fix problems (exit code 0 ok, 1 warnings, 2 errors)"""
    from orchestrator.Core.doctor import Doctor, Severity
    ctx.require_framework()
    doc = Doctor(ctx.framework_manager)
    report = doc.fix(tier) if fix else doc.run()
    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2))
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
        console.print(f"[yellow]Still present after the fix: {escape(code)}[/yellow]")
    sys.exit(report.exit_code)


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
    app_root = Path(__file__).resolve().parents[1]
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


def _audit_person(registry, event: str, arguments: dict) -> None:
    """A person's change to who may call the tools, in the audit log (actor cli). Names only, never a token."""
    if registry.audit is None:
        return
    try:
        registry.audit.record("cli", event, "framework", "person", "ok", arguments=arguments, approved=True)
    except Exception as e:
        Console(stderr=True).print(f"[yellow]Done, but the audit log could not record it: {escape(str(e))}[/yellow]")


def _policy_summary(policy) -> str:
    from orchestrator.tools.policy import TIER_LABELS
    tiers = ", ".join(f"{tier.value}: {policy.tiers[tier].value}" for tier in TIER_LABELS)
    return tiers + (f" (+{len(policy.overrides)} per tool)" if policy.overrides else "")


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
              help="Use Ditto's permissions by reference (Ditto on another computer); changes apply at once")
@pass_context
def mcp_token_add(ctx: FrameworkContext, name: str, policy_from: Optional[str], link: Optional[str]):
    """Create a token; it can only read until you allow more (tools permissions --token NAME)"""
    from orchestrator.tools.mcp_tokens import ACTOR_PREFIX, TokenStoreError
    _at_a_terminal("Creating an MCP token", shows_token=True)
    registry = _tool_registry(ctx)
    tokens = _token_store(registry)
    policy = None
    if policy_from and link:
        console.print("[red]Use either --policy-from or --link.[/red]")
        sys.exit(2)
    if policy_from:
        # "mcp:<name>" (a token's audit actor) is always a token, also one named like a caller ("mcp", "ditto").
        named_token = policy_from.startswith(ACTOR_PREFIX)
        source = policy_from[len(ACTOR_PREFIX):] if named_token else policy_from
        if not named_token and source in ("mcp", "ditto"):
            if registry.policy_store.problem:
                console.print(f"[red]{escape(registry.policy_store.problem)}[/red]")
                sys.exit(1)
            policy = registry.policy(source)
        elif tokens.get(source) is not None:
            policy = registry.policy(ACTOR_PREFIX + source)
        else:
            console.print(f"[red]Unknown caller '{escape(policy_from)}': use mcp, ditto or a token name.[/red]")
            sys.exit(2)
        # A copy (later changes of the source do not apply): the source's decisions and reviewed tools; copied
        # from ditto or mcp it counts as set by you, so the token is no longer treated as a new, read-only one.
        policy = policy.model_copy(deep=True, update={"configured": True} if not named_token else {})
    try:
        token = tokens.add(name, policy=policy, link=link)
    except TokenStoreError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    _audit_person(registry, "mcp_token.add", {"token": name, "policy_from": policy_from, "link": link})
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
    from orchestrator.tools.mcp_tokens import TokenStoreError
    _at_a_terminal("Revoking an MCP token")
    registry = _tool_registry(ctx)
    tokens = _token_store(registry)
    if tokens.get(name) is None:
        console.print(f"[red]There is no token named '{escape(name)}'.[/red]")
        sys.exit(1)
    if not click.confirm(f"Revoke the token '{name}'? Its client loses access at once.", default=False):
        return
    try:
        tokens.revoke(name)
    except TokenStoreError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    _audit_person(registry, "mcp_token.revoke", {"token": name})
    from orchestrator.tools.mcp_tokens import ACTOR_PREFIX
    if registry.policy().grants.get(ACTOR_PREFIX + name):
        try:  # a new token of the same name would not inherit them anyway (they name the token's creation time)
            registry.policy_store.update(lambda p: p.model_copy(update={"grants": {
                c: g for c, g in p.grants.items() if c != ACTOR_PREFIX + name}}), actor="cli")
        except Exception as e:
            console.print(f"[yellow]Its standing approvals could not be removed ({escape(str(e))}); they no longer "
                          "apply.[/yellow]")
    console.print(f"[green]Token '{escape(name)}', its permissions and its standing approvals are deleted.[/green]")


def _token_permissions(registry, name: str, reset: bool) -> None:
    """`tools permissions --token NAME`: the same questions, saved as that token's own permissions."""
    from orchestrator.tools.mcp_tokens import ACTOR_PREFIX, TokenStoreError, read_only_policy
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
    policy = read_only_policy() if reset else _ask_tool_policy(registry, before,
                                                               shown_for=ACTOR_PREFIX + name)
    try:
        tokens.set_policy(name, policy)
    except TokenStoreError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    _audit_person(registry, "mcp_token.permissions", {"token": name, "before": _policy_summary(before),
                                                      "after": _policy_summary(policy), "was_linked": record.link})
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
    """List finished builds (from their manifests) in the output directory."""
    from orchestrator.Core.deployment_manager import list_builds
    output_dir = _output_dir(ctx)
    builds = list_builds(output_dir) if output_dir.exists() else []
    if not builds:
        console.print(f"[yellow]No finished builds in {escape(str(output_dir))}.[/yellow]")
        return

    table = Table(title=f"Builds in {escape(str(output_dir))}")
    table.add_column("Folder", style="bold yellow")
    table.add_column("Model")
    table.add_column("Format", style="cyan")
    table.add_column("Quantization")
    table.add_column("Board")
    table.add_column("Size (MB)", justify="right")
    for info in builds:
        folder = Path(info["path"])
        try:
            shown = str(folder.relative_to(output_dir))
        except ValueError:
            shown = str(folder)
        size_mb = sum(f.stat().st_size for f in folder.glob('**/*') if f.is_file()) / (1024 * 1024)
        table.add_row(escape(shown), escape(str(info.get("model") or info.get("model_source") or "?")),
                      escape(str(info.get("format") or "?")), escape(str(info.get("quantization") or "-")),
                      escape(str(info.get("board") or "-")), f"{size_mb:.2f}")
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
    # A folder from 'deploy list-artifacts' (relative to the output directory) or any existing path.
    given = Path(artifact_name).expanduser()
    artifact_path = given.resolve() if given.is_absolute() or given.exists() else output_dir / artifact_name
    
    docker_config = {"use_docker": docker}
    if docker:
        # TODO: Get real image name from config/profile logic
        docker_config["image_name"] = "ghcr.io/llm-framework/inference-node:latest"
    
    with console.status(f"Generiere Paket für {artifact_name}...", spinner="dots"):
        pkg_path = dep_mgr.create_deployment_package(
            artifact_path=artifact_path,
            profile_name=profile,
            docker_config=docker_config,
            output_dir=output_dir
        )
        
    if pkg_path:
        console.print(f"[bold green]Package created successfully![/bold green]")
        console.print(f"Path: {pkg_path}")
    else:
        console.print("[bold red]Package generation failed. Check logs.[/bold red]")
        sys.exit(1)

@deploy.command('run')
@click.argument('package_path', type=click.Path(exists=True))
@click.option('--ip', required=True, help="Target IP")
@click.option('--user', required=True, help="Target Username")
@click.option('--port', default=22, show_default=True, type=int)
@click.option('--password', default=None, help="Only for the first connection: used once to install the "
              "framework's SSH key, never stored (asked interactively when omitted)")
@click.option('--trust-fingerprint', default=None,
              help="Trust a new device non-interactively if its host key has this SHA256 fingerprint")
@pass_context
def run_deploy(ctx: FrameworkContext, package_path, ip, user, port, password, trust_fingerprint):
    """
    Upload and execute a deployment package on the target.
    """
    from orchestrator.utils import ssh_trust
    ctx.require_framework()
    dep_mgr = ctx.deployment_manager
    if not dep_mgr:
        console.print("[red]Deployment Manager not loaded.[/red]")
        sys.exit(1)

    # Trust on first use, never silently.
    try:
        status, key = dep_mgr.host_key_status(ip, port)
    except Exception as e:
        console.print(f"[red]Could not reach {escape(ip)}:{port}: {escape(str(e))}[/red]")
        sys.exit(1)
    if status == "changed":
        console.print(f"[bold red]{escape(str(ssh_trust.HostKeyChanged(ip, port)))}[/bold red]")
        sys.exit(1)
    if status == "unknown":
        fp = ssh_trust.fingerprint(key)
        console.print(f"New device {escape(ip)}:{port} - {key.get_name()} {fp}")
        if trust_fingerprint:
            if trust_fingerprint != fp:
                console.print("[red]The fingerprint does not match --trust-fingerprint.[/red]")
                sys.exit(1)
        elif not (sys.stdin.isatty() and click.confirm(
                "Compare with 'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub' on the device. Trust it?", default=False)):
            console.print("[red]Device not trusted; nothing was deployed.[/red]")
            sys.exit(1)
        dep_mgr.trust_host(ip, key, port)
    if password is None and sys.stdin.isatty():
        password = click.prompt("Password (Enter if the framework key is already installed)",
                                default="", hide_input=True, show_default=False) or None
        
    with console.status(f"Deploying to {user}@{ip}...", spinner="earth"):
        success = dep_mgr.deploy_artifact(
            artifact_path=Path(package_path),
            target_ip=ip,
            user=user,
            password=password,
            port=port,
        )
        
    if success:
        console.print("[bold green]✅ Deployment Execution Successful![/bold green]")
    else:
        console.print("[bold red]❌ Deployment Failed.[/bold red]")
        sys.exit(1)

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


def _write_profile_file(profile, output_file: Path, as_json: bool) -> None:
    from orchestrator.Core.vault_key import write_private
    output_file.parent.mkdir(parents=True, exist_ok=True)
    write_private(output_file, profile.to_json() + "\n" if as_json else profile.to_text())


def _trust_device(state_dir: Path, host: str, port: int, trust_fingerprint: Optional[str]):
    """The device's host key, trusted on first use but never silently (as for deploy run). Exits when refused."""
    from orchestrator.utils import ssh_trust
    try:
        key = ssh_trust.fetch_host_key(host, port)
        status = ssh_trust.check_host_key(state_dir, host, port, key)
    except Exception as e:  # unreachable, no SSH server, a broken known_hosts file
        console.print(f"[red]Could not reach {escape(host)}:{port}: {escape(str(e))}[/red]")
        sys.exit(1)
    if status == "changed":
        console.print(f"[bold red]{escape(str(ssh_trust.HostKeyChanged(host, port)))}[/bold red]")
        sys.exit(1)
    if status == "unknown":
        fp = ssh_trust.fingerprint(key)
        console.print(f"New device {escape(host)}:{port} - {escape(key.get_name())} {fp}")
        if trust_fingerprint:
            if trust_fingerprint != fp:
                console.print("[red]The fingerprint does not match --trust-fingerprint.[/red]")
                sys.exit(1)
        elif not (sys.stdin.isatty() and click.confirm(
                "Compare with 'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub' on the device. Trust it?",
                default=False)):
            console.print("[red]Device not trusted; nothing was probed.[/red]")
            sys.exit(1)
        ssh_trust.trust_host_key(state_dir, host, port, key)
    return key


def _scan_device(store, state_dir: Path, output_file: Path, as_json: bool, host: str, user: Optional[str], port: int,
                 password: Optional[str], trust_fingerprint: Optional[str], label: Optional[str]) -> None:
    from orchestrator.runtime.profile import device_id_for_host_key
    from orchestrator.runtime.remote_probe import RemoteProbeError, probe_over_ssh, store_address, valid_host
    from orchestrator.utils import ssh_trust
    if not user:
        raise click.UsageError("--device needs --user")
    if not valid_host(host):
        raise click.UsageError("--device takes a host name or an IP address")
    if not 1 <= port <= 65535:
        raise click.UsageError("--port must be between 1 and 65535")
    if label is not None and not (1 <= len(" ".join(label.split())) <= 64 and label.isprintable()):
        raise click.UsageError("--label takes 1 to 64 printable characters")
    if ssh_trust.paramiko is None:
        console.print("[red]Probing a device needs the 'paramiko' package.[/red]")
        sys.exit(1)
    key = _trust_device(state_dir, host, port, trust_fingerprint)
    if password is None and sys.stdin.isatty():
        password = click.prompt("Password (Enter if the framework key is already installed)",
                                default="", hide_input=True, show_default=False) or None
    try:
        client = ssh_trust.connect(host, user, state_dir, port=port, password=password)
    except Exception as e:  # authentication, network, a host key that changed meanwhile
        console.print(f"[red]SSH login to {escape(user)}@{escape(host)} failed: {escape(str(e))}[/red]")
        sys.exit(1)
    try:
        if password:
            ssh_trust.install_public_key(client, ssh_trust.client_key(state_dir)[1])
        with console.status(f"Running the hardware probe on {escape(host)}...", spinner="dots"):
            result = probe_over_ssh(client)
    except (RemoteProbeError, ssh_trust.HostKeyError) as e:
        console.print(f"[red]The probe on {escape(host)} failed: {escape(str(e))}[/red]")
        sys.exit(1)
    finally:
        client.close()
    device_id = device_id_for_host_key(ssh_trust.fingerprint(key))
    try:
        _write_profile_file(result.profile, output_file, as_json)
        stored = store.save_device(device_id, result.profile, label=label, address=store_address(host, port))
    except (OSError, ValueError) as e:
        console.print(f"[red]Could not save the profile: {escape(str(e))}[/red]")
        sys.exit(1)
    from orchestrator.runtime.device.settings import remember_ssh_user
    remember_ssh_user(store, device_id, user)  # the deploy user for `device runtime deploy` (never root)
    tool = "Python probe" if result.tool == "python" else "shell probe"
    console.print(f"[bold green]Probe of {escape(host)} successful ({tool}). Config generated: "
                  f"{escape(str(output_file))}[/bold green]")
    for note in result.notes:
        console.print(f"[yellow]{escape(note)}[/yellow]")
    for line in _profile_summary(result.profile):
        console.print(line)
    console.print(f"Stored as device {device_id}: {escape(str(stored))}")


@cli.command('scan')
@click.option('--output', default="target_hardware_config.txt", show_default=True,
              help="File the profile is written to (relative to the current folder)")
@click.option('--json', 'as_json', is_flag=True, help="Write the JSON view instead of KEY=VALUE lines")
@click.option('--script', 'use_script', is_flag=True,
              help="Use the fallback script (hardware_probe.sh, on Windows hardware_probe.ps1) instead of the "
                   "Python probe")
@click.option('--device', default=None, help="Probe this device over SSH instead of this computer (host or IP)")
@click.option('--user', default=None, help="SSH user on the device (with --device)")
@click.option('--port', default=22, show_default=True, type=int, help="SSH port of the device (with --device)")
@click.option('--password', default=None, help="Only for the first connection: used once to install the "
              "framework's SSH key, never stored (asked interactively when omitted)")
@click.option('--trust-fingerprint', default=None,
              help="Trust a new device non-interactively if its host key has this SHA256 fingerprint")
@click.option('--label', default=None, help="Your name for the device in the profile store (with --device or "
              "--import)")
@click.option('--import', 'import_file', default=None, type=click.Path(exists=True, dir_okay=False),
              help="Import a board's probe file (target_hardware_config.txt copied from the board) into the probe "
                   "store and print its probe id, which AI clients and Ditto use")
@pass_context
def run_scan(ctx: FrameworkContext, output, as_json, use_script, device, user, port, password, trust_fingerprint,
             label, import_file):
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
        _scan_device(store, state_dir, output_file, as_json, device, user, port, password, trust_fingerprint, label)
        return

    try:
        with console.status("Running the hardware probe...", spinner="dots"):
            # The script writes the --output file itself; the Python probe's result is written below.
            profile, how = probes.run_local(use_script=use_script, output=output_file)
    except ProbeError as e:
        console.print(f"[red]Probe execution failed: {escape(str(e))}[/red]")
        if e.detail:  # the underlying error for the person (it may name a path, so it is not part of the message)
            console.print(f"[dim]{escape(e.detail)}[/dim]")
        if e.next:
            console.print(escape(e.next))
        sys.exit(1)
    if how != "fallback script" or as_json:
        try:
            _write_profile_file(profile, output_file, as_json)
        except OSError as e:
            console.print(f"[red]The profile could not be written to {escape(str(output_file))}: "
                          f"{escape(str(e))}[/red]")
            sys.exit(1)
    for note in probes.notes:
        console.print(f"[yellow]{escape(note)}[/yellow]")
    console.print(f"[bold green]Probe successful ({how}). Config generated: {escape(str(output_file))}[/bold green]")
    for line in _profile_summary(profile):
        console.print(line)
    if profile.needs_reprobe:
        console.print("[yellow]This profile lacks facts a build needs; run the probe again before a build.[/yellow]")
    console.print(f"Stored as this computer's profile (probe id local): {escape(str(store.local_path()))}")


def _scan_import(ctx: FrameworkContext, probes, import_file, label: Optional[str]) -> None:
    """Copies a board's probe file, validated and without its host name, into the probe store and prints its probe
    id (MCP design T5: AI clients and Ditto name a board's probe only by that id, never by a path)."""
    from orchestrator.runtime.probe_service import ProbeError, match_target
    if label is not None and not (1 <= len(" ".join(label.split())) <= 64 and label.isprintable()):
        raise click.UsageError("--label takes 1 to 64 printable characters")
    try:
        probe_id = probes.import_file(import_file, label)
        profile = probes.get(probe_id)
    except ProbeError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        if e.next:
            console.print(escape(e.next))
        sys.exit(1)
    info = probes.info(probe_id, profile)
    console.print(f"[bold green]Imported as probe {probe_id} ({escape(info.label)}).[/bold green]")
    for line in _profile_summary(profile):
        console.print(line)
    if profile.needs_reprobe:
        console.print("[yellow]This probe was converted from an older probe file and lacks facts; probe the board "
                      "again with the current script when you can.[/yellow]")
    try:  # the targets only, without booting the framework: the match is a hint
        from types import SimpleNamespace
        from orchestrator.Core.framework import resolve_install_path
        from orchestrator.Core.target_manager import TargetManager
        config, _ = _settings(ctx)
        target_manager = TargetManager(SimpleNamespace(config=config, resolve_path=resolve_install_path))
    except Exception:  # noqa: BLE001
        target_manager = None
    match = match_target(profile, target_manager)
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
    registry = _tool_registry(ctx)
    try:
        if getattr(registry, "audit", None) is None:
            raise RuntimeError("no audit log")
        registry.audit.record("user", tool, "external", "ask", "ok", arguments=arguments, approved=True,
                              detail=detail, extra={"answer": "accepted", "confirm_channel": "framework-cli"})
    except Exception as e:
        console.print(f"[red]The consent could not be written to the audit log ({escape(str(e))}); nothing was "
                      "changed.[/red]")
        sys.exit(1)


def _device_plan(profile):
    from orchestrator.runtime.build.recipe import PlanError
    from orchestrator.runtime.build.toolchain import host_platform
    from orchestrator.runtime.catalog import load_catalog
    from orchestrator.runtime.device.package import plan_device_runtime
    try:
        return plan_device_runtime(profile, catalog=load_catalog(), host=host_platform())
    except PlanError as e:
        console.print(f"[red]No device runtime can be planned for this device: {escape(str(e))}[/red]")
        sys.exit(1)


def _connect_device(state_dir: Path, device_id: str, host: str, port: int, user: str, password: Optional[str]):
    """An SSH client for the stored device: its host key must be the trusted one the device was probed with (the
    device ID is derived from it), so a deployment never reaches another machine at the same address."""
    from orchestrator.runtime.profile import device_id_for_host_key
    from orchestrator.utils import ssh_trust
    if ssh_trust.paramiko is None:
        console.print("[red]Reaching a device needs the 'paramiko' package.[/red]")
        sys.exit(1)
    try:
        key = ssh_trust.fetch_host_key(host, port)
        status = ssh_trust.check_host_key(state_dir, host, port, key)
    except Exception as e:  # unreachable, no SSH server, a broken known_hosts file
        console.print(f"[red]Could not reach {escape(host)}:{port}: {escape(str(e))}[/red]")
        sys.exit(1)
    if status == "changed":
        console.print(f"[bold red]{escape(str(ssh_trust.HostKeyChanged(host, port)))}[/bold red]")
        sys.exit(1)
    if status != "known":
        console.print(f"[red]{escape(host)}:{port} is not trusted yet: probe it first with llm-cli scan --device "
                      f"{escape(host)} --user USER.[/red]")
        sys.exit(1)
    if device_id_for_host_key(ssh_trust.fingerprint(key)) != device_id:
        console.print(f"[red]{escape(host)}:{port} answers with the host key of another device than {device_id}; "
                      "probe it again before deploying to it.[/red]")
        sys.exit(1)
    try:
        client = ssh_trust.connect(host, user, state_dir, port=port, password=password)
    except Exception as e:  # authentication, network, a host key that changed meanwhile
        console.print(f"[red]SSH login to {escape(user)}@{escape(host)} failed: {escape(str(e))}. Pair the device "
                      "once with llm-cli scan --device (it asks for the password one time) or pass --password.[/red]")
        sys.exit(1)
    if password:
        try:
            ssh_trust.install_public_key(client, ssh_trust.client_key(state_dir)[1])
        except ssh_trust.HostKeyError as e:
            client.close()
            console.print(f"[red]{escape(str(e))}[/red]")
            sys.exit(1)
    return client


def _bench_choice(ctx: FrameworkContext, shell, home: str, bench_model: Optional[str], allow_untrusted: bool,
                  device_id: str, user: str):
    """(model path on the device or None, allowed): a catalog model (by sha256) runs; any other model only after
    the user's consent, written to the audit log first (decision D23)."""
    from orchestrator.runtime.catalog import load_catalog
    from orchestrator.runtime.device.remote import model_sha256
    from orchestrator.runtime.device.settings import SettingsError, check_model_path
    if bench_model is None:
        return None, False
    try:
        path = check_model_path(bench_model if bench_model.startswith("/") else f"{home}/{bench_model}")
    except SettingsError as e:
        raise click.UsageError(f"--bench-model: {e}")
    digest = model_sha256(shell, path)
    if digest is None:
        console.print(f"[yellow]No readable model at {escape(path)} on the device; the short benchmark is skipped."
                      "[/yellow]")
        return None, False
    catalog = load_catalog()
    pinned = {m.sha256 for m in catalog.models if m.sha256}
    if catalog.smoke_model is not None:
        pinned.add(catalog.smoke_model.sha256)
    if digest in pinned:
        return path, True
    question = (f"{path} is not a catalog model, so it is untrusted. llama-bench loads it natively on the device as "
                f"{user} (no sandbox). Run the short benchmark with it? Your answer is written to the audit log.")
    if not (allow_untrusted or (_interactive() and click.confirm(question, default=False))):
        return path, False
    _record_consent(ctx, "device_runtime_untrusted_model", {"device": device_id, "model_sha256": digest},
                    "an untrusted model may run natively on the device for the short llama-bench run")
    return path, True


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
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.settings import DeploySettings, SettingsError
    from orchestrator.runtime.profile import ProfileError, ProfileStore
    state_dir = _state_dir(ctx)
    store = ProfileStore(state_dir)
    rows = []
    for device_id in store.list_devices():
        info = store.device_info(device_id)
        try:
            profile = store.load_device(device_id)
        except ProfileError:
            profile = None
        try:
            settings: Dict[str, Any] = DeploySettings.from_dict(store.load_deploy_settings(device_id)).as_dict()
        except (SettingsError, ValueError):
            settings = {"problem": "damaged; set them again"}
        record = DeviceRuntimeStore(state_dir, device_id).record()
        system = None
        if profile is not None:
            system = {k: profile.get(k) for k in ("OS", "OS_ID", "OS_VERSION_ID", "ARCH", "LIBC", "LIBC_VERSION",
                                                  "INIT_SYSTEM", "SOC_MODEL")}
        rows.append({"device_id": device_id, "label": info.get("label"), "address": info.get("address"),
                     "system": system, "deploy": settings,
                     "deployed": {k: record.get(k) for k in ("build_id", "deployed_at", "install")} if record
                     else None})
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
@pass_context
def device_settings(ctx: FrameworkContext, device_name: str, install: Optional[str], ssh_user: Optional[str],
                    port: Optional[int], model: Optional[str], confirm_sudo: bool):
    """Show or change how the runtime is installed on a device. Decided here, before a deployment: a deployment
    never asks."""
    from dataclasses import replace
    from orchestrator.runtime.device.settings import SettingsError
    _state, store, device_id, profile = _device_context(ctx, device_name)
    current = _deploy_settings(store, device_id)
    changes = {k: v for k, v in (("install", install), ("ssh_user", ssh_user), ("port", port), ("model", model))
               if v is not None}
    if changes:
        try:
            new = current.changed(**changes)
        except SettingsError as e:
            raise click.UsageError(str(e))
        if new.install == "systemd" and new.sudo_confirmed_at is None:
            if profile.get("INIT_SYSTEM") != "systemd":
                console.print(f"[red]Device {device_id} does not run systemd (its probe reports "
                              f"{escape(profile.get('INIT_SYSTEM') or 'no init system')}); the start script is the "
                              "way to run the runtime there. The settings were not changed.[/red]")
                sys.exit(1)
            question = ("The systemd unit is written to /etc/systemd/system and enabled with sudo on the device "
                        "(sudo -n: it must work without a password, the deployment never asks). Allow deployments to "
                        "this device to run these sudo commands? Your answer is written to the audit log.")
            if not (confirm_sudo or (_interactive() and click.confirm(question, default=False))):
                console.print("[red]The systemd unit needs your confirmation that deployments may use sudo on the "
                              "device; the settings were not changed.[/red]")
                sys.exit(1)
            _record_consent(ctx, "device_runtime_systemd", {"device": device_id},
                            "deployments may run sudo -n on the device to install the systemd unit")
            new = replace(new, sudo_confirmed_at=_utc_stamp())
        try:
            store.save_deploy_settings(device_id, new.as_dict())
        except (OSError, ValueError) as e:
            console.print(f"[red]Could not save the deploy settings: {escape(str(e))}[/red]")
            sys.exit(1)
        current = new
        console.print(f"[green]Deploy settings of device {device_id} saved.[/green]")
    console.print(f"Install: {current.install}" + (" (start script in the user's home)" if current.install == "user"
                                                    else f" (systemd unit; sudo confirmed {current.sudo_confirmed_at})"))
    console.print(f"SSH user: {escape(current.ssh_user or 'not set (--user)')}")
    console.print(f"Port: {current.port}")
    console.print(f"Model for the systemd unit: {escape(current.model or 'not set')}")
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
    state_dir, _store, device_id, profile = _device_context(ctx, device_name)
    plan = _device_plan(profile)
    if as_json:
        click.echo(plan.to_json(), nl=False)
        return
    target, builder, options = plan.target, plan.builder, plan.options()
    console.print(f"Device {device_id}: {target['os']}/{target['arch']}, {target.get('libc')} "
                  f"{target.get('libc_version')}")
    console.print(f"Builder: container {builder.get('base')} ({builder.get('libc')} {builder.get('libc_version')}, "
                  f"GCC {builder.get('gcc')}), without network")
    console.print(f"CPU: {options.get('GGML_CPU_ARM_ARCH') or ('all variants' if options.get('GGML_CPU_ALL_VARIANTS') == 'ON' else 'x86 features of the probe')}"
                  f", OpenMP {options.get('GGML_OPENMP')}, C++ runtime linked statically: "
                  f"{'yes' if plan.verify.get('static_libstdcxx') else 'no'}")
    console.print(f"Programs: {', '.join(plan.targets)}"
                  + (f"; with {', '.join(plan.verify['runtime_libs'])}" if plan.verify.get("runtime_libs") else ""))
    console.print(f"Build ID: {plan.build_id}")
    for note in plan.notes:
        console.print(f"[yellow]{escape(note)}[/yellow]")
    built = BuildStore(state_dir).find(plan)
    console.print("Built: yes, " + str(built.path) if built else "Built: no (llm-cli device runtime build "
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
    from orchestrator.runtime.build.executor import BuildExecutor, DockerEngine
    from orchestrator.runtime.build.source import SourceError, cached_source, fetch_source
    from orchestrator.runtime.build.store import BuildFailed, BuildStore, StoreError
    from orchestrator.runtime.build.toolchain import host_platform
    from orchestrator.runtime.build.verify import Verifier
    from orchestrator.runtime.device.package import DeviceRuntimeStore, PackageError, package_runtime
    state_dir, _store, device_id, profile = _device_context(ctx, device_name)
    plan = _device_plan(profile)
    source = _pinned_llama_source()
    builds = BuildStore(state_dir)
    try:
        src = cached_source(source, state_dir)
        if src is None:
            if not yes and not (_interactive() and click.confirm(
                    f"The pinned llama.cpp source ({source.tag}, commit {source.commit[:12]}, about 215 MB) is not "
                    "cached yet. Fetch it now?", default=True)):
                console.print("[red]The build needs the pinned source (or llm-cli ditto runtime import-source on a "
                              "machine without internet).[/red]")
                sys.exit(1)
            with console.status("Fetching the pinned llama.cpp source ..."):
                src = fetch_source(source, state_dir, url=mirror, log=lambda msg: console.print(escape(msg)))
    except SourceError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    build = builds.find(plan)
    if build is None:
        console.print(f"Building llama.cpp {source.tag} for device {device_id} ({plan.target['arch']}, "
                      f"{plan.target.get('libc')} {plan.target.get('libc_version')}) in the builder container "
                      f"{plan.builder.get('base')} ...")
        executor = BuildExecutor(engine=DockerEngine(), log=lambda msg: console.print(escape(msg)))
        try:
            build = builds.build(plan, source=src, executor=executor, verifier=Verifier(), jobs=jobs,
                                 perf_key=profile.perf_key(), host=host_platform())
        except BuildFailed as e:
            console.print(f"[red]{escape(str(e))}[/red]")
            if e.log:
                console.print(f"Logs: {escape(str(e.log))}")
            sys.exit(1)
        except StoreError as e:
            console.print(f"[red]{escape(str(e))}[/red]")
            sys.exit(1)
    else:
        console.print(f"Build {build.build_id[:16]} for this device is already stored; packaging it.")
    devices = DeviceRuntimeStore(state_dir, device_id)
    try:
        packaged = package_runtime(builds, build, licence=src / "LICENSE", out_dir=devices.root)
    except PackageError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    deployed = devices.record().get("build_id")
    devices.prune(keep=tuple(b for b in (build.build_id, deployed) if b))
    console.print(f"[bold green]Device runtime ready:[/bold green] {escape(str(packaged.path))}")
    console.print(f"sha256 {packaged.sha256}; format checked here (ELF, glibc symbol versions, no shared C++ runtime); "
                  "the programs run their checks on the device during the deployment.")
    console.print(f"Next: llm-cli device runtime deploy {device_id}")


def _ensure_package(state_dir: Path, device_id: str, builds, build):
    from orchestrator.runtime.build.source import cached_source
    from orchestrator.runtime.device.package import DeviceRuntimeStore, PackageError, package_runtime
    devices = DeviceRuntimeStore(state_dir, device_id)
    packaged = devices.tarball(build)
    if packaged is not None:
        return devices, packaged
    src = cached_source(_pinned_llama_source(), state_dir)
    if src is None:
        console.print("[red]The tarball must be packaged again, which needs the pinned source (its licence text): "
                      f"run llm-cli device runtime build {device_id}.[/red]")
        sys.exit(1)
    try:
        return devices, package_runtime(builds, build, licence=src / "LICENSE", out_dir=devices.root)
    except PackageError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)


@device_runtime.command('deploy')
@click.argument('device_name', metavar='DEVICE')
@click.option('--user', 'ssh_user', default=None, help='SSH user for this deployment (default: the deploy settings)')
@click.option('--password', default=None, help="Only for a device that is not paired yet: used once to install the "
              "framework's SSH key, never stored")
@click.option('--bench-model', default=None, metavar='PATH',
              help='A model file on the device for the short llama-bench check (relative: in the home folder)')
@click.option('--allow-untrusted-model', is_flag=True,
              help='Run the short benchmark also with a model that is not a catalog model (written to the audit log)')
@pass_context
def device_runtime_deploy(ctx: FrameworkContext, device_name: str, ssh_user: Optional[str], password: Optional[str],
                          bench_model: Optional[str], allow_untrusted_model: bool):
    """Install the built runtime on the device over SSH as the deploy settings say, then check it there"""
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.device.devices import split_address
    from orchestrator.runtime.device.remote import (RemoteError, RemoteShell, deploy_runtime, read_facts,
                                                    start_script_path, verify_on_device)
    from orchestrator.runtime.device.settings import SettingsError
    from orchestrator.runtime.server_policy import DEVICE_DEFAULT_PORT
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
    plan = _device_plan(profile)
    builds = BuildStore(state_dir)
    build = builds.find(plan)
    if build is None:
        console.print(f"[red]The runtime for this device is not built yet (or the device's profile changed): "
                      f"llm-cli device runtime build {device_id}[/red]")
        sys.exit(1)
    devices, packaged = _ensure_package(state_dir, device_id, builds, build)
    try:
        host, port = split_address(store.device_info(device_id).get("address"))
    except ValueError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    source = _pinned_llama_source()
    client = _connect_device(state_dir, device_id, host, port, settings.ssh_user, password)
    try:
        shell = RemoteShell(client)
        facts = read_facts(shell)
        if facts.uid == 0:
            console.print("[red]The SSH user on the device is root: the runtime never runs as root. Deploy as an "
                          "unprivileged user (llm-cli device settings DEVICE --user NAME).[/red]")
            sys.exit(1)
        # Asked before anything changes on the device (decision D23: an untrusted model only after consent).
        model_path, allowed = _bench_choice(ctx, shell, facts.home, bench_model, allow_untrusted_model, device_id,
                                            settings.ssh_user)

        def check_new_build(home: str, folder: str):
            # Design 3.8 and P6: the new build is checked in its own folder and becomes current only if it passes.
            return verify_on_device(shell, home=home, folder=folder, plan_verify=build.plan.get("verify") or {},
                                    pin_build=source.build_number, pin_commit=source.commit, port=settings.port,
                                    bench_model=model_path, bench_allowed=allowed)

        result = deploy_runtime(shell, tarball=packaged.path, tarball_sha256=packaged.sha256,
                                build_id=build.build_id, arch=str(plan.target["arch"]), settings=settings,
                                check=check_new_build, say=lambda msg: console.print(escape(msg)))
    except RemoteError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    finally:
        client.close()
    report = result.report
    if not result.activated or report is None or not report.ok:
        console.print(f"[red]Build {build.build_id[:16]} did not pass its checks on the device; it was not "
                      "activated.[/red]")
        if report is not None:
            _print_report(report)
        for note in result.notes:
            console.print(f"[yellow]{escape(note)}[/yellow]")
        sys.exit(1)
    now = _utc_stamp()
    devices.save_record({"build_id": build.build_id, "deployed_at": now, "install": settings.install,
                         "ssh_user": settings.ssh_user, "previous": result.previous, "perf_key": profile.perf_key(),
                         "verification": report.as_dict(), "verified_at": now,
                         "unit_started": result.unit_started})
    devices.prune(keep=(build.build_id,))
    console.print(f"[bold green]Deployed[/bold green] build {build.build_id[:16]} to {escape(result.root)}"
                  + (f" (previous {result.previous} kept)" if result.previous else ""))
    _print_report(report)
    for note in result.notes:
        console.print(f"[yellow]{escape(note)}[/yellow]")
    script = start_script_path(result.home)
    if settings.install == "systemd":
        console.print(f"systemd unit: {'started' if result.unit_started else 'enabled'}; the server listens on "
                      f"127.0.0.1:{settings.port} with the key in ~/.config/llm-framework/llama-server.key")
    else:
        # The start script listens on its default port unless --port is given.
        port_option = f" --port {settings.port}" if settings.port != DEVICE_DEFAULT_PORT else ""
        console.print(f"Start it on the device: sh {escape(script)} start MODEL.gguf{port_option} "
                      f"(127.0.0.1:{settings.port}, key in ~/.config/llm-framework/llama-server.key; stop, status, "
                      "serve for the foreground)")


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
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.device.devices import split_address
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.remote import RemoteError, RemoteShell, read_facts, verify_on_device
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
    stored = BuildStore(state_dir).get(record["build_id"])
    plan_verify = (stored.plan.get("verify") if stored else None) or _device_plan(profile).verify
    try:
        host, port = split_address(store.device_info(device_id).get("address"))
    except ValueError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    source = _pinned_llama_source()
    client = _connect_device(state_dir, device_id, host, port, user, None)
    try:
        shell = RemoteShell(client)
        facts = read_facts(shell)
        if facts.uid == 0:
            console.print("[red]The SSH user is root; the checks run as the unprivileged user only.[/red]")
            sys.exit(1)
        model_path, allowed = _bench_choice(ctx, shell, facts.home, bench_model, allow_untrusted_model, device_id,
                                            user)
        report = verify_on_device(shell, home=facts.home, plan_verify=plan_verify, pin_build=source.build_number,
                                  pin_commit=source.commit, port=settings.port, bench_model=model_path,
                                  bench_allowed=allowed)
    except RemoteError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        sys.exit(1)
    finally:
        client.close()
    record.update({"verification": report.as_dict(), "verified_at": _utc_stamp()})
    record.pop("format", None)
    devices.save_record(record)
    console.print(f"Device {device_id}, build {record['build_id'][:16]}:")
    _print_report(report)
    if not report.ok:
        sys.exit(1)


@device_runtime.command('status')
@click.argument('device_name', metavar='DEVICE')
@click.option('--json', 'as_json', is_flag=True, help='Machine-readable output')
@pass_context
def device_runtime_status(ctx: FrameworkContext, device_name: str, as_json: bool):
    """What is deployed on the device and whether it still matches the device's profile and the pin"""
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    state_dir, store, device_id, profile = _device_context(ctx, device_name)
    plan = _device_plan(profile)
    record = DeviceRuntimeStore(state_dir, device_id).record()
    built = BuildStore(state_dir).find(plan)
    status = {"device_id": device_id, "planned_build_id": plan.build_id, "built": built is not None,
              "deployed": record or None,
              "current": bool(record) and record.get("build_id") == plan.build_id}
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
    config, _ = _settings(ctx)
    click.echo(json.dumps(_effective_settings(config), indent=2, default=str, ensure_ascii=False))


# Settings that are not part of the schema but look like credentials are never printed.
SECRET_NAME = re.compile(r"(?i)(^|[_.-])(token|password|passwd|secret|api_?key|access_key|private_key|credentials?)"
                         r"($|[_.-])")


def _redacted(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):  # below a key that names a credential, even a value no pattern recognizes
        return {k: "REDACTED" if v and SECRET_NAME.search(str(k)) else _redacted(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redacted(v) for v in value]
    return value


def _effective_settings(config) -> Dict[str, Any]:
    """Every setting with its value and source (default, a file, the environment, or changed in this run)."""
    result = {}
    values = getattr(config, "config_values", {})
    for key in sorted(values):
        entry = values[key]
        src = entry.source
        source = f"file {src.source_path}" if src.source_type == "file" and src.source_path else src.source_type
        unknown = getattr(entry, "schema", None) is None
        if getattr(entry, "is_secret", False) or (unknown and SECRET_NAME.search(key) and entry.value):
            value = "REDACTED"
        else:
            value = _redacted(entry.value)
        result[key] = {"value": value, "source": source}
    return result


@config.command('repos')
@click.option('--add', nargs=2, help="Add repo override: <name> <url>")
@click.option('--remove', help="Remove repo override: <name>")
@pass_context
def config_repos(ctx: FrameworkContext, add, remove):
    """Manage SSOT Source Repositories"""
    ctx.require_framework()
    cm = ctx.framework_manager.config
    current_repos = dict(cm.get("source_repositories", {}) or {})
    
    def save():
        if not (cm.set("source_repositories", current_repos) and cm.save_user_config()):
            console.print(f"[red]Not saved: {escape(str(Path(cm.config_dir) / 'config.yml'))} could not be updated "
                          f"(see the log); the repository overrides are unchanged.[/red]")
            sys.exit(1)

    if add:
        name, url = add
        variables = repo_override_names(name)
        if not variables:  # the build engine could not pass it on under any name
            console.print(f"[red]Invalid name {escape(repr(name))}: it needs ASCII letters or digits (it becomes "
                          f"<NAME>_REPO_OVERRIDE, e.g. llama.cpp -> LLAMA_CPP_REPO_OVERRIDE).[/red]")
            sys.exit(2)
        # The shape the build engine passes on as <NAME>_REPO_OVERRIDE (plain URLs of earlier versions still work).
        current_repos[name.lower()] = {"url": url}
        save()
        console.print(f"[green]Added repo override: {escape(name.lower())} -> {escape(redact_secrets(url))} (passed to "
                      f"builds as {escape(variables[0])})[/green]")
    elif remove:
        if remove.lower() in current_repos:
            del current_repos[remove.lower()]
            save()
            console.print(f"[green]Removed repo override: {escape(remove)}[/green]")
        else:
            console.print(f"[yellow]Repo {escape(remove)} not found.[/yellow]")
    else:
        # List
        if not current_repos:
            console.print("[yellow]No repository overrides defined.[/yellow]")
        else:
            table = Table(title="Source Repositories (SSOT)")
            table.add_column("Key", style="cyan")
            table.add_column("URL", style="green")
            for k, v in current_repos.items():
                url = v.get('url') if isinstance(v, dict) else v
                table.add_row(escape(str(k)), escape(redact_secrets(str(url or "-"))))
            console.print(table)

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
    from orchestrator.Core.community_pack import parse_public_keys
    keys = []
    for f in files:
        try:
            text = Path(f).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            _zoo_fail(click.UsageError(f"{f} is not a text file with public keys (see 'llm-cli zoo keys')."), 2)
        keys += [k for k in parse_public_keys(text) if isinstance(k, kind)]
    if files and not keys:
        _zoo_fail(ValueError("The key files contain no usable public key (see 'llm-cli zoo keys')."), 2)
    return keys


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
    cm = _zoo(ctx)
    items = [i for i in cm.list_items(cm.cached_index())
             if i.id.casefold() == pack_id.casefold() and (not kind or i.kind == kind)]
    if not items:
        _zoo_fail(ValueError(f"'{pack_id}' is neither in the cached index nor installed (llm-cli zoo list --refresh)."))
    for i in items:
        lines = [f"{i.kind} {i.id} {i.version} by {i.author} ({i.license or 'licence unknown'})",
                 f"Trust: {i.trust.value}" + (f" - revoked: {i.revoked}" if i.revoked else ""),
                 f"Installed: {i.installed_version or 'no'}" + (f" in {i.path}" if i.path else "")]
        if i.entry:
            lines += [f"Download: {i.entry.url} ({i.entry.size} bytes, sha256 {i.entry.sha256})"]
        if i.description:
            lines.append(i.description)
        if i.kind == "recipe" and i.path and (i.path / "recipe.yml").is_file():
            lines += ["", (i.path / "recipe.yml").read_text(encoding="utf-8")]
        console.print(Panel(escape("\n".join(lines)), title=escape(i.title)))


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
@click.option('--to', 'recipients', multiple=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help='Encrypt for private sharing to the age recipients (age1...) in this file (repeatable)')
@click.option('--passphrase', 'use_passphrase', is_flag=True, help='Encrypt for private sharing with a passphrase')
@click.option('--out', type=click.Path(file_okay=False, path_type=Path), help='Folder for the pack')
@click.option('--pr', 'open_pr', is_flag=True,
              help='Hand the pack to a pull request on the community repository (gh if installed, else the browser)')
@click.option('--yes', is_flag=True, help='With --pr: submit without asking after the summary')
@pass_context
def zoo_export(ctx: FrameworkContext, kind: str, source: Optional[str], author: str, license_: Optional[str],
               model_license: str, pack_id: Optional[str], recipients: Tuple[Path, ...], use_passphrase: bool,
               out: Optional[Path], open_pr: bool, yes: bool):
    """Export a target (SOURCE: target id), a recipe (SOURCE: a build folder or its build_info.json) or your
    knowledge base as a scrubbed pack signed with your key. Shows what the privacy scrub changed."""
    from pyrage.x25519 import Recipient
    from orchestrator.Core.community_manager import submit_contribution
    from orchestrator.Core.community_pack import PackError
    if kind in ("target", "recipe") and not source:
        _zoo_fail(click.UsageError(f"Name the {'target id' if kind == 'target' else 'build folder'} to export."), 2)
    if recipients and use_passphrase:
        _zoo_fail(click.UsageError("Use --to or --passphrase, not both: age encrypts to keys or to one passphrase."), 2)
    if kind == "recipe" and Path(source).is_dir():
        source = str(Path(source) / "build_info.json")
    cm = _zoo(ctx)
    passphrase = click.prompt("Passphrase", hide_input=True, confirmation_prompt=True, err=True) \
        if use_passphrase else None
    try:
        result = cm.export_pack(kind, source, author=author, license=license_, pack_id=pack_id,
                                model_license=model_license, recipients=_public_keys(recipients, Recipient),
                                passphrase=passphrase, out_dir=out)
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


if __name__ == "__main__":
    cli()
