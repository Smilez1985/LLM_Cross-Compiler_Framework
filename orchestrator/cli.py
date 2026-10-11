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
import subprocess
import asyncio
import os
import platform
import re
import time
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
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
from orchestrator.Core.builder import BuildEngine, ModelFormat, OptimizationLevel
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

DEFAULT_CONFIG = {
    "targets_dir": "targets",
    "models_dir": "models", 
    "output_dir": "output",
    "configs_dir": "configs",
    "cache_dir": "cache",
    "log_level": "INFO",
    "max_concurrent_builds": 2
}


# ============================================================================
# CLICK CONTEXT UND MANAGER INITIALISIERUNG
# ============================================================================

class FrameworkContext:
    """Shared context for CLI commands, hält Instanzen der Core-Manager"""
    
    def __init__(self):
        self.config: Dict[str, Any] = DEFAULT_CONFIG.copy()
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

def load_config_file(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration from file"""
    config = DEFAULT_CONFIG.copy()
    if config_path and Path(config_path).exists():
        try:
            with open(Path(config_path), 'r') as f:
                if Path(config_path).suffix in ['.yml', '.yaml']:
                    file_config = yaml.safe_load(f)
                else:
                    file_config = json.load(f)
            if file_config:
                config.update(file_config)
        except Exception as e:
            # stderr: stdout may carry a protocol (llm-cli mcp serve)
            Console(stderr=True).print(f"[yellow]Warning: Failed to load config file: {escape(str(e))}[/yellow]")
    return config


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
    # Load configuration
    ctx.config.update(load_config_file(config))
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

@module.command('create')
@click.option('--name', prompt='Module Name', help='Name of the new target')
@click.option('--arch', prompt='Architecture', type=click.Choice(['aarch64', 'x86_64', 'armv7l', 'riscv64']), help='CPU Architecture')
@click.option('--sdk', prompt='SDK/Backend', default='None', help='Special SDK (e.g. CUDA, RKNN)')
@pass_context
def module_create(ctx: FrameworkContext, name: str, arch: str, sdk: str):
    """Create a new hardware target module interactively"""
    
    console.print(f"\n[bold cyan]Creating new target module: {name}[/bold cyan]")
    
    # Interaktive Abfrage weiterer Details (CLI Wizard Style)
    base_os = click.prompt("Base Docker Image", default="debian:bookworm-slim")
    description = click.prompt("Description", default=f"Target for {name}")
    
    console.print("Enter required system packages (comma separated):")
    packages_input = click.prompt("Packages", default="build-essential, cmake, git")
    packages = [p.strip() for p in packages_input.split(',')]
    
    cpu_flags = click.prompt("Default CPU Flags", default="")
    
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
        
        output_path = generator.generate_module(module_data)
        
        console.print(f"\n[bold green]✅ Module successfully created![/bold green]")
        console.print(f"Location: {output_path}")
        console.print("\nNext steps:")
        console.print(f"1. Edit {output_path}/target.yml to add supported boards")
        console.print(f"2. Customize {output_path}/modules/config_module.sh")
        
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


def _ditto_confirm(spec, inp) -> bool:
    """Asks before a tool call that needs confirmation; without a terminal the answer is no."""
    from orchestrator.tools.confirmation import CallTooLong, describe_call
    from orchestrator.tools.registry import current_question
    try:
        text = current_question() or describe_call(spec, inp.model_dump(mode="json"), actor="Ditto")
    except CallTooLong as e:
        click.echo(str(e), err=True)
        return False
    click.echo(text, err=True)
    if not sys.stdin.isatty():
        click.echo("No terminal to confirm in: denied. Allow the tool with 'llm-cli tools permissions'.", err=True)
        return False
    return click.confirm("Allow this?", default=False, err=True)


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
    from orchestrator.Core.ditto_manager import DittoError
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

    # The definition is shown in full before anything is written; the module generator checks it again.
    console.print("\n[bold green]AI analysis finished. Review the proposal:[/bold green]")
    click.echo(json.dumps(definition.model_dump(), indent=2, ensure_ascii=False))
    if not Confirm.ask("Generate module with these settings?"):
        return
    fm = ctx.framework_manager
    targets_dir = fm.resolve_path(fm.config.get("targets_dir", "targets"))
    module_name = definition.module_name.replace(" ", "_")
    try:
        path = ditto.save_module(definition, targets_dir, module_name=module_name)
    except ValueError as e:
        console.print(f"[bold red]The proposal was rejected:[/bold red] {escape(str(e))}")
        sys.exit(1)
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
    """Status of builds, including those the GUI, the MCP server or another CLI call started (read from the build
    records in the cache folder). REQUEST_ID: a request id (req_...) or a build id."""
    from orchestrator.Core.build_records import read_records, records_dir
    config, resolve = _settings(ctx)
    folder = records_dir(resolve(config.get("cache_dir", "cache")))
    records = read_records(folder)
    if request_id:
        matches = [r for r in records if request_id in (r.get("request_id"), r.get("build_id"))]
        if not matches:
            console.print(f"[red]Build '{escape(request_id)}' not found in {escape(str(folder))}.[/red]")
            sys.exit(1)
        for record in reversed(matches):  # attempts in order
            _print_build_record(record)
        return
    if not records:
        console.print(f"[yellow]No builds found in {escape(str(folder))}.[/yellow]")
        return
    shown = records if all else records[:STATUS_ROWS]
    table = Table(title="Builds")
    for column in ("Request", "Build", "Status", "Progress", "Target", "Format", "Quantization", "Model",
                   "Started"):
        table.add_column(column)
    for r in shown:
        table.add_row(escape(str(r.get("request_id") or "-")), escape(str(r.get("build_id"))),
                      escape(str(r.get("status"))), f"{r.get('progress') or 0}%", escape(str(r.get("target") or "-")),
                      escape(str(r.get("format") or "-")), escape(str(r.get("quantization") or "-")),
                      escape(str(r.get("model") or "-")), escape(str(r.get("started") or "-")))
    console.print(table)
    if len(records) > len(shown):
        console.print(f"{len(records) - len(shown)} older builds: llm-cli build status --all")


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


def _ask_tool_policy(registry, current, caller: Optional[str] = None):
    """Interactive questions for the tool permissions (one per tier), for one caller or the shared ones. Every
    registered tool counts as reviewed afterwards: the questions apply to the tools listed above them."""
    from orchestrator.tools.policy import TIER_LABELS, CallerPolicy, Decision, ToolTier
    section = current.callers.get(caller) if caller else None
    base_tiers = section.tiers if section is not None else current.tiers
    # A caller's new section starts from the shared settings, so creating it never loosens a per-tool setting.
    overrides = dict((section if section is not None else current).overrides)
    rows = registry.permissions(caller or "")
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


@tools.command('permissions')
@click.option('--caller', type=CALLER_CHOICE, default=None,
              help="Set the permissions of one caller (ditto or mcp) instead of the shared ones")
@click.option('--reset', is_flag=True, help='Back to the recommended defaults (asks again at the next setup); '
                                            'with --caller: that caller uses the shared permissions again')
@pass_context
def tools_permissions(ctx: FrameworkContext, caller: Optional[str], reset: bool):
    """Set which tool calls are allowed, need confirmation, or are denied"""
    from orchestrator.tools.policy import ToolPolicy
    registry = _tool_registry(ctx)
    if reset:
        if caller:
            current = registry.policy()
            callers = {name: section for name, section in current.callers.items() if name != caller}
            registry.policy_store.save(current.model_copy(update={"callers": callers}))
            console.print(f"[yellow]{CALLER_LABELS[caller]} use the shared tool permissions again.[/yellow]")
            return
        registry.policy_store.save(ToolPolicy())
        console.print("[yellow]Tool permissions reset to the recommended defaults.[/yellow]")
        return
    console.print(f"Tool permissions for {CALLER_LABELS[caller]}.")
    policy = registry.policy_store.save(_ask_tool_policy(registry, registry.policy(), caller))
    console.print(f"[green]Saved to {registry.policy_store.path}[/green]")
    tiers = policy.callers[caller].tiers if caller else policy.tiers
    for tier, decision in tiers.items():
        console.print(f"  {tier.value}: {decision.value}")


@tools.command('audit')
@click.option('--limit', default=30, show_default=True, help='Number of entries')
@click.option('--verify', is_flag=True,
              help='Check the chain: changed or removed entries, and removal of the newest entries (anchor)')
@click.option('--archive', is_flag=True,
              help='Move the log and its anchor aside (kept with a timestamp) and start a new log')
@pass_context
def tools_audit(ctx: FrameworkContext, limit: int, verify: bool, archive: bool):
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
    for column in ("Time", "Actor", "Tool", "Decision", "Confirmed", "Outcome"):
        table.add_column(column)
    confirmed = {None: "-", 0: "no", 1: "yes"}
    for entry in entries:
        table.add_row(escape(entry["ts"]), escape(entry["actor"]), escape(entry["tool"]), escape(entry["decision"]),
                      confirmed.get(entry["approved"], "?"), escape(entry["outcome"]))
    console.print(table)


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


@ditto.group('runtime')
def ditto_runtime():
    """Local model server (llama.cpp) for offline use"""
    pass


@ditto_runtime.command('status')
@click.option('--json', 'as_json', is_flag=True, help='Machine-readable output')
@pass_context
def runtime_status(ctx: FrameworkContext, as_json: bool):
    """Hardware, recommended build and model, and whether the server is running"""
    rt = _local_runtime(ctx)
    with console.status("Checking the hardware ..."):
        status = rt.status()
        hardware = rt.hardware()
        choice = rt.select_backend()
        model, why = rt.choose_tier()
    preferred = rt.preferred_tier()
    if as_json:
        click.echo(json.dumps({
            "build": status.build, "hardware": hardware.model_dump(mode="json"),
            "recommended_build": choice.asset.id if choice.asset else None, "build_reason": choice.reason,
            "installed": status.installed.asset_id if status.installed else None,
            "running": status.running, "healthy": status.healthy, "base_url": status.base_url, "pid": status.pid,
            "model": status.model, "tier": status.tier, "ctx": status.ctx,
            "recommended_tier": model.tier, "tier_reason": why, "preferred_tier": preferred,
            "log_file": str(status.log_file)}, indent=2))
        return
    table = Table(title=f"Local runtime (llama.cpp {escape(status.build)})", show_header=False)
    table.add_column("", style="cyan")
    table.add_column("")
    table.add_row("Hardware", escape(hardware.summary()))
    table.add_row("Recommended build", escape(f"{choice.asset.id if choice.asset else '-'}: {choice.reason}"))
    inst = status.installed
    table.add_row("Installed", escape(f"{inst.asset_id} ({inst.path})") if inst else
                  "[yellow]no[/yellow] (llm-cli ditto runtime install)")
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
              show_default=True, help='Build to install (ROCm only when chosen here)')
@click.option('--force', is_flag=True, help='Download and unpack again even if it is installed')
@click.option('--yes', '-y', is_flag=True, help='Do not ask before downloading')
@pass_context
def runtime_install(ctx: FrameworkContext, backend: str, force: bool, yes: bool):
    """Download the pinned llama.cpp build (size and sha256 verified before unpacking)"""
    from orchestrator.runtime.llama_server import LocalRuntimeError
    rt = _local_runtime(ctx)
    choice = rt.select_backend(backend)
    if choice.asset is None:
        console.print(f"[red]{escape(choice.reason)}[/red]")
        sys.exit(1)
    console.print(f"llama.cpp {escape(rt.build)} - {escape(choice.asset.id)}: {escape(choice.reason)}")
    if not yes and not click.confirm(f"Download {_gib(choice.asset.download_size)} into {rt.root}?", default=True):
        return
    bar, update = _download_progress()
    try:
        with bar:
            inst = rt.install(prefer=backend, force=force, progress=update)
    except LocalRuntimeError as e:
        _runtime_failure(e)
    console.print(f"[green]Installed {escape(inst.asset_id)} in {escape(str(inst.path))}[/green]")


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
    policy = registry.policy_store.save(_ask_tool_policy(registry, registry.policy()))
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
              help='stdio: started by the client (default). http: local Streamable HTTP with a bearer token.')
@click.option('--host', default='127.0.0.1', show_default=True, help='HTTP only; loopback addresses only')
@click.option('--port', default=8765, show_default=True, type=int, help='HTTP only')
@pass_context
def mcp_serve(ctx: FrameworkContext, transport: str, host: str, port: int):
    """Run the MCP server (stdout carries the protocol; logs go to stderr)"""
    import contextlib
    from orchestrator import mcp_server
    mcp_server.no_programs_from_the_working_directory()
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
    try:
        mcp_server.serve(ctx.framework_manager, transport=transport, host=host, port=port)
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
# SCAN COMMAND (NEU V2.0)
# ============================================================================

@cli.command('scan')
@click.option('--output', default="target_hardware_config.txt", show_default=True,
              help="File the probe writes (relative to the current folder)")
@pass_context
def run_scan(ctx: FrameworkContext, output):
    """Run the hardware probe on this computer (exit code 1 if it fails or writes no file)."""
    script_dir = Path(__file__).resolve().parents[1] / "scripts"
    output_file = Path(output).expanduser().resolve()

    is_windows = platform.system() == "Windows"
    script = script_dir / ("hardware_probe.ps1" if is_windows else "hardware_probe.sh")

    if not script.exists():
        console.print(f"[red]Probe script not found at {escape(str(script))}[/red]")
        sys.exit(1)

    if is_windows:
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script), "-OutputFile", str(output_file)]
    else:
        cmd = ["bash", str(script), "--output", str(output_file)]
    try:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.unlink(missing_ok=True)  # a file of an earlier run must not count as this run's result
        with console.status("Running Hardware Probe...", spinner="dots"):
            subprocess.run(cmd, check=True, cwd=os.getcwd())
    except (subprocess.CalledProcessError, OSError) as e:
        console.print(f"[red]Probe execution failed: {escape(str(e))}[/red]")
        sys.exit(1)
    if not output_file.exists():
        console.print(f"[red]Probe script ran, but {escape(str(output_file))} was not created.[/red]")
        sys.exit(1)
    console.print(f"[bold green]✅ Probe successful. Config generated: {escape(str(output_file))}[/bold green]")


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
    
    if add:
        name, url = add
        # The shape the build engine passes on as <NAME>_REPO_OVERRIDE (plain URLs of earlier versions still work).
        current_repos[name.lower()] = {"url": url}
        cm.set("source_repositories", current_repos)
        cm.save_user_config()
        console.print(f"[green]Added repo override: {escape(name.lower())} -> {escape(redact_secrets(url))} (passed to "
                      f"builds as {escape(name.split('.')[-1].upper())}_REPO_OVERRIDE)[/green]")
    elif remove:
        if remove.lower() in current_repos:
            del current_repos[remove.lower()]
            cm.set("source_repositories", current_repos)
            cm.save_user_config()
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
    keys = [k for f in files for k in parse_public_keys(Path(f).read_text(encoding="utf-8")) if isinstance(k, kind)]
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
              help='Encrypted packs: accept only these senders (public key files, repeatable)')
@click.option('--passphrase', 'use_passphrase', is_flag=True, help='Encrypted packs: ask for the passphrase')
@click.option('--yes', is_flag=True, help="Do not ask for a pack whose trust comes from the signed index, also a "
              "file the index lists (a file without an index entry is 'local' and is always confirmed)")
@pass_context
def zoo_install(ctx: FrameworkContext, pack_id: Optional[str], kind: str, file_path: Optional[Path],
                senders: Tuple[Path, ...], use_passphrase: bool, yes: bool):
    """Install a community pack: download pinned by the signed index, checks in quarantine, your confirmation"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from orchestrator.Core.community_manager import TrustLevel
    from orchestrator.Core.community_pack import PackError
    if bool(pack_id) == bool(file_path):
        _zoo_fail(click.UsageError("Give either an id from the index or --file."), 2)
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
