#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Command Line Interface (v2.4.0)
DIREKTIVE: Goldstandard, vollständig, professionell geschrieben.

Professional CLI for automation, CI/CD integration, and power users.
Supports all framework operations including Source Management, Module Generation, 
AI Assistance, Deployment, Self-Healing, and Smart Calibration (IMatrix).

Updates v2.4.0:
- Added --imatrix and --dataset flags to build command.
- Integrated Smart Calibration workflow visualization.
- Added 'config repos' to manage SSOT source repositories.
"""

import sys
import json
import logging
import subprocess
import asyncio
import os
import platform
from pathlib import Path
from typing import Optional, Dict, List, Any
from datetime import datetime
from dataclasses import dataclass, asdict

import click
import yaml
import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from orchestrator.Core.framework import FrameworkManager, FrameworkConfig
from orchestrator.Core.orchestrator import LLMOrchestrator, BuildRequest, WorkflowType, PriorityLevel, OrchestrationStatus
from orchestrator.Core.builder import BuildEngine, ModelFormat, OptimizationLevel
from orchestrator.Core.module_generator import ModuleGenerator
from orchestrator.Core.deployment_manager import DeploymentManager
from orchestrator.utils.logging import get_logger
from orchestrator.utils.validation import ValidationError

# Import DittoCoder optionally
try:
    from orchestrator.Core.ditto_manager import DittoCoder
except ImportError:
    DittoCoder = None

# ============================================================================
# DATENKLASSEN
# ============================================================================

@dataclass
class TargetConfig:
    """Target configuration for CLI display"""
    name: str
    architecture: str
    status: str = "available"
    version: str = "1.0.0"
    maintainer: str = "Framework Team"
    description: str = ""
    supported_boards: List[str] = None
    docker_image: str = ""
    
    def __post_init__(self):
        if self.supported_boards is None:
            self.supported_boards = []


# ============================================================================
# GLOBAL CONFIGURATION
# ============================================================================

console = Console()
__version__ = "2.4.0"

DEFAULT_CONFIG = {
    "targets_dir": "targets",
    "models_dir": "models", 
    "output_dir": "output",
    "configs_dir": "configs",
    "cache_dir": "cache",
    "logs_dir": "logs",
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
        self.framework_manager: Optional[FrameworkManager] = None
        self.orchestrator: Optional[LLMOrchestrator] = None
        self.build_engine: Optional[BuildEngine] = None
        self.deployment_manager: Optional[DeploymentManager] = None
        self.verbose: bool = False
        self.quiet: bool = False
        self._initialized: bool = False
        
    def initialize(self):
        """Initialisiert FrameworkManager und Orchestrator."""
        if self._initialized:
            return
            
        try:
            # Framework Manager initialisieren
            try:
                framework_config = FrameworkConfig(**self.config)
            except TypeError:
                # Fallback if config has extra keys
                valid_keys = FrameworkConfig.__annotations__.keys()
                filtered_config = {k: v for k, v in self.config.items() if k in valid_keys}
                framework_config = FrameworkConfig(**filtered_config)

            self.framework_manager = FrameworkManager(framework_config)
            
            if not self.framework_manager.initialize():
                raise RuntimeError("Framework Manager initialization failed")
            
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
            console.print(f"[red]Failed to initialize framework: {e}[/red]")
            # Don't exit here to allow help command to run, but mark as failed
            # sys.exit(1)


pass_context = click.make_pass_decorator(FrameworkContext, ensure=True)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def setup_cli_logging(level: str = "INFO"):
    """Setup logging for CLI operations"""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stderr)]
    )


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
            console.print(f"[yellow]Warning: Failed to load config file: {e}[/yellow]")
    return config


def format_target_table(targets: List[TargetConfig]) -> Table:
    """Format targets as a Rich table"""
    table = Table(title="Available Targets")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Architecture", style="green")
    table.add_column("Status", style="yellow")
    table.add_column("Boards", style="magenta")
    
    for target in targets:
        status = "✅ Ready" if target.status == "available" else "❌ Not Ready"
        boards = ", ".join(target.supported_boards[:2]) if target.supported_boards else "None"
        if target.supported_boards and len(target.supported_boards) > 2:
            boards += f" (+{len(target.supported_boards) - 2} more)"
            
        table.add_row(target.name, target.architecture, status, boards)
    
    return table


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

@click.group()
@click.version_option(version=__version__)
@click.option('--config', '-c', type=click.Path(exists=True), help='Configuration file path')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
@click.option('--quiet', '-q', is_flag=True, help='Quiet output')
@click.option('--log-level', default='INFO', type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR']))
@pass_context
def cli(ctx: FrameworkContext, config: Optional[str], verbose: bool, quiet: bool, log_level: str):
    """
    🚀 LLM Cross-Compiler Framework CLI v2.4.0
    
    Professional command-line interface for cross-compiling Large Language Models
    for edge hardware. Supports automation, CI/CD integration, Deployment, 
    Self-Healing and Smart Calibration (IMatrix).
    """
    # Setup logging
    setup_cli_logging("DEBUG" if verbose else log_level)
    
    # Load configuration
    ctx.config.update(load_config_file(config))
    ctx.verbose = verbose
    ctx.quiet = quiet
    
    # Initialisiere Framework und Orchestrator
    ctx.initialize()
    
    if not quiet:
        console.print(Panel(
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
    """List available hardware targets"""
    
    try:
        if not ctx.build_engine:
            console.print("[red]Build engine not available[/red]")
            sys.exit(1)
        
        available_targets = ctx.build_engine.list_available_targets()
        
        targets_config_objects = []
        for target_info in available_targets:
            target_config = TargetConfig(
                name=target_info.get("name", "unknown"),
                architecture=target_info.get("target_arch", "unknown"),
                status="available" if target_info.get("available", False) else "unavailable",
                version="1.0.0",
                maintainer="Framework Team",
                description=f"Target for {target_info.get('target_arch', 'unknown')} architecture",
                supported_boards=[str(target_info.get("path", ""))],
                docker_image="llm-framework/builder"
            )
            targets_config_objects.append(target_config)
        
        if format == 'table':
            if targets_config_objects:
                table = format_target_table(targets_config_objects)
                console.print(table)
            else:
                console.print("[yellow]No targets found matching criteria[/yellow]")
        elif format == 'json':
            click.echo(json.dumps([asdict(t) for t in targets_config_objects], indent=2))
        elif format == 'yaml':
            click.echo(yaml.dump([asdict(t) for t in targets_config_objects], default_flow_style=False))
            
    except Exception as e:
        console.print(f"[red]Error listing targets: {e}[/red]")
        sys.exit(1)


@targets.command('validate')
@click.argument('target_name')
@pass_context
def validate_target(ctx: FrameworkContext, target_name: str):
    """Validate a target configuration"""
    try:
        # Assuming validate_target exists on framework manager or target manager
        tm = ctx.framework_manager.get_component("target_manager")
        if tm:
             # This is a hypothetical method, adapt if your TM has different API
             target = tm.get_target(target_name)
             if target:
                 console.print(f"[green]✅ Target '{target_name}' is valid[/green]")
             else:
                 console.print(f"[red]❌ Target '{target_name}' not found[/red]")
                 sys.exit(1)
        else:
             console.print("[red]Target Manager not available[/red]")
    except Exception as e:
        console.print(f"[red]Error validating target: {e}[/red]")
        sys.exit(1)


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
        targets_dir = Path(ctx.config.get('targets_dir', 'targets'))
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


@module.command('generate-ai')
@click.argument('probe_file', type=click.Path(exists=True))
@pass_context
def generate_ai(ctx: FrameworkContext, probe_file):
    """
    🤖 AI-Powered: Generate module from hardware probe (target_hardware_config.txt).
    """
    if not DittoCoder:
        console.print("[red]Ditto Manager not available. Install litellm via 'pip install litellm'[/red]")
        sys.exit(1)

    console.print(Panel(f"[bold cyan]🤖 Ditto AI Hardware Agent[/bold cyan]\nAnalyzing: {probe_file}", title="AI Setup"))

    # 1. Interactive Provider Selection
    console.print("\n[bold]Select AI Provider:[/bold]")
    options = [
        "OpenAI (GPT-4o)",
        "Anthropic (Claude 3 Opus)",
        "Google (Gemini Pro)",
        "Ollama (Local)",
        "LocalAI / OpenAI Compatible"
    ]
    for i, opt in enumerate(options, 1):
        console.print(f"{i}. {opt}")
    
    choice = Prompt.ask("Choice", choices=[str(i) for i in range(1, len(options)+1)], default="1")
    choice_idx = int(choice) - 1
    
    provider_map = {
        0: ("OpenAI", "gpt-4o"),
        1: ("Anthropic", "claude-3-opus-20240229"),
        2: ("Google VertexAI", "gemini-1.5-pro"),
        3: ("Ollama (Local)", "llama3"),
        4: ("LocalAI / OpenAI Compatible", "local-model")
    }
    
    provider, default_model = provider_map[choice_idx]
    
    # 2. Model Override
    model = Prompt.ask(f"Model Name", default=default_model)
    
    # 3. Credentials (via SecretsManager if available)
    api_key = None
    base_url = None
    
    # Try fetching from SecretsManager first
    if ctx.framework_manager.secrets_manager:
        key_map = {
            "OpenAI": "openai_api_key",
            "Anthropic": "anthropic_api_key",
            "Google": "gemini_api_key"
        }
        if provider.split()[0] in key_map:
            api_key = ctx.framework_manager.secrets_manager.get_secret(key_map[provider.split()[0]])
    
    if "Local" in provider or "Compatible" in provider:
        base_url = Prompt.ask("Base URL", default="http://localhost:11434")
        api_key = "sk-dummy"
    
    if not api_key:
        api_key = Prompt.ask(f"Enter API Key", password=True)

    try:
        # 4. Execution
        coder = DittoCoder(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            config_manager=ctx.framework_manager.config,
            framework_manager=ctx.framework_manager
        )
        
        with console.status(f"🤖 Asking {provider} to analyze hardware...", spinner="dots"):
            config = coder.generate_module_content(Path(probe_file))
            
        # 5. Confirmation & Save
        console.print("\n[bold green]AI Analysis Successful![/bold green]")
        console.print(json.dumps(config, indent=2))
        
        if Confirm.ask("Generate module with these settings?"):
            targets_dir = Path(ctx.config["targets_dir"])
            mod_name_raw = config.get("module_name", "Unknown_Target")
            module_name = mod_name_raw.replace(" ", "_")
            
            coder.save_module(module_name, config, targets_dir)
            console.print(f"[bold green]✅ Module '{module_name}' created in {targets_dir}![/bold green]")
            
    except Exception as e:
        console.print(f"[bold red]AI Generation failed: {e}[/bold red]")
        sys.exit(1)


# ============================================================================
# BUILD COMMANDS
# ============================================================================

@cli.group()
def build():
    """Build and cross-compile models"""
    pass


@build.command('start')
@click.option('--model', '-m', required=True, help='Model name (HuggingFace) or local path')
@click.option('--target', '-t', required=True, help='Target hardware architecture (folder name in targets/)')
@click.option('--format', '-f', default='gguf', help='Target format (gguf, onnx, tflite)')
@click.option('--quantization', '-q', help='Quantization method (q4_0, q8_0, etc.)')
@click.option('--output-dir', '-o', help='Custom output directory')
@click.option('--optimization', default='balanced', 
              type=click.Choice(['fast', 'balanced', 'size', 'speed', 'aggressive']),
              help='Optimization level')
@click.option('--priority', default='normal',
              type=click.Choice(['low', 'normal', 'high', 'urgent', 'critical']),
              help='Build priority')
@click.option('--parallel', is_flag=True, default=True, help='Enable parallel builds')
@click.option('--follow', '-F', is_flag=True, help='Follow build output in real-time')
@click.option('--gpu/--no-gpu', default=False, help='Enable GPU passthrough')
# --- NEU v2.4.0: IMatrix Flags ---
@click.option('--imatrix/--no-imatrix', default=False, help='Enable Smart Calibration (IMatrix) for quantization')
@click.option('--dataset', type=click.Path(exists=True), help='Custom calibration dataset path (txt)')
@pass_context
def start_build(ctx: FrameworkContext, model: str, target: str, format: str, 
                quantization: Optional[str], output_dir: Optional[str], 
                optimization: str, priority: str, parallel: bool, follow: bool, gpu: bool,
                imatrix: bool, dataset: Optional[str]):
    """Start a new build job"""
    
    try:
        # Target is passed as string (folder name)
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
        
        if not output_dir:
            output_dir = str(Path(ctx.config["output_dir"]) / f"build_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        
        build_request = BuildRequest(
            request_id="",
            workflow_type=WorkflowType.SIMPLE_CONVERSION,
            priority=priority_level,
            models=[model],
            targets=[target_arch_str], # List of strings
            target_formats=[target_format_enum],
            optimization_level=optimization_level,
            quantization_options=[quantization] if quantization else [],
            parallel_builds=parallel,
            output_base_dir=output_dir,
            description=f"CLI build: {model} -> {target} ({format})",
            use_gpu=gpu,
            # Pass IMatrix Flags to Orchestrator
            use_imatrix=imatrix,
            dataset_path=dataset
        )
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            request_id = loop.run_until_complete(ctx.orchestrator.submit_build_request(build_request))
            
            if not ctx.quiet:
                console.print(Panel(
                    f"[bold]Model:[/bold] {model}\n"
                    f"[bold]Target:[/bold] {target}\n"
                    f"[bold]Format:[/bold] {format}\n"
                    f"[bold]Quantization:[/bold] {quantization or 'None'}\n"
                    f"[bold]Output:[/bold] {output_dir}\n"
                    f"[bold]GPU:[/bold] {'✅ Enabled' if gpu else '❌ Disabled'}\n"
                    f"[bold]Smart Calibration (IMatrix):[/bold] {'✅ Enabled' if imatrix else '❌ Disabled'}",
                    title=f"🚀 Starting Build: {request_id}"
                ))
            
            if follow:
                console.print("[blue]Following build progress (Ctrl+C to stop following)...[/blue]")
                try:
                    while True:
                        workflow_status = loop.run_until_complete(ctx.orchestrator.get_workflow_status(request_id))
                        if workflow_status:
                            # v2.0: Enhanced Status Reporting
                            status_color = "yellow"
                            if workflow_status.status == OrchestrationStatus.COMPLETED: status_color = "green"
                            if workflow_status.status == OrchestrationStatus.ERROR: status_color = "red"
                            if workflow_status.status == OrchestrationStatus.HEALING: status_color = "magenta"
                            
                            console.print(f"[{status_color}]Status: {workflow_status.status.value} - {workflow_status.current_stage}[/{status_color}]")
                            console.print(f"[cyan]Progress: {workflow_status.progress_percent}% ({workflow_status.completed_builds}/{workflow_status.total_builds} builds)[/cyan]")
                            
                            # Show Healing Info
                            if workflow_status.healing_proposal:
                                hp = workflow_status.healing_proposal
                                console.print(f"[bold magenta]🚑 Self-Healing Active: {hp.summary}[/bold magenta]")
                                console.print(f"Proposed Fix: [italic]{hp.fix_command}[/italic]")
                            
                            if workflow_status.status in [OrchestrationStatus.COMPLETED, OrchestrationStatus.ERROR, OrchestrationStatus.CANCELLED]:
                                break
                        
                        import time
                        time.sleep(5)
                except KeyboardInterrupt:
                    console.print("\n[yellow]Stopped following build (build continues in background)[/yellow]")
            else:
                console.print(f"[green]Build started with ID: {request_id}[/green]")
                console.print(f"Use 'llm-cli build status {request_id}' to check progress")
                
        finally:
            loop.close()
            
    except Exception as e:
        console.print(f"[red]Build failed: {e}[/red]")
        sys.exit(1)


@build.command('status')
@click.argument('request_id', required=False)
@click.option('--all', '-a', is_flag=True, help='Show all builds')
@pass_context
def build_status(ctx: FrameworkContext, request_id: Optional[str], all: bool):
    """Check build status"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            if all or not request_id:
                workflows = loop.run_until_complete(ctx.orchestrator.list_workflows())
                if not workflows:
                    console.print("[yellow]No builds found[/yellow]")
                    return
                
                table = Table(title="Build Status")
                table.add_column("Request ID", style="cyan", no_wrap=True)
                table.add_column("Status", style="yellow")
                table.add_column("Progress", style="blue")
                
                for workflow in workflows:
                    table.add_row(
                        workflow.request_id[:12] + "...",
                        workflow.status.value,
                        f"{workflow.progress_percent}%"
                    )
                console.print(table)
            else:
                workflow_status = loop.run_until_complete(ctx.orchestrator.get_workflow_status(request_id))
                if not workflow_status:
                    console.print(f"[red]Build '{request_id}' not found[/red]")
                    sys.exit(1)
                
                console.print(f"[bold]ID:[/bold] {workflow_status.request_id}")
                console.print(f"[bold]Status:[/bold] {workflow_status.status.value}")
                console.print(f"[bold]Progress:[/bold] {workflow_status.progress_percent}%")
                
        finally:
            loop.close()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)

# ============================================================================
# SECRETS MANAGEMENT COMMANDS (NEU V2.0)
# ============================================================================

@cli.group()
def secrets():
    """Manage secure credentials (Keyring)"""
    pass

@secrets.command('set')
@click.argument('key')
@click.password_option(help="The secret value")
@pass_context
def set_secret(ctx: FrameworkContext, key, password):
    """Set a secret in the secure store."""
    if not ctx.framework_manager.secrets_manager:
        console.print("[red]SecretsManager not available![/red]")
        sys.exit(1)
        
    if ctx.framework_manager.secrets_manager.set_secret(key, password):
        console.print(f"[green]Secret '{key}' stored successfully in Keyring.[/green]")
    else:
        console.print(f"[red]Failed to store secret '{key}'.[/red]")

@secrets.command('list')
@pass_context
def list_secrets(ctx: FrameworkContext):
    """List available secret keys (names only)."""
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

@cli.group()
def deploy():
    """Manage deployments and artifacts"""
    pass

@deploy.command('list-artifacts')
@pass_context
def list_artifacts(ctx: FrameworkContext):
    """List available golden artifacts in output directory."""
    output_dir = Path(ctx.config["output_dir"])
    if not output_dir.exists():
        console.print("[yellow]No artifacts found (output dir missing).[/yellow]")
        return

    table = Table(title="Golden Artifacts")
    table.add_column("Artifact Name", style="bold yellow")
    table.add_column("Type", style="cyan")
    table.add_column("Size (MB)", justify="right")

    for item in output_dir.glob("*"):
        if item.name.startswith("deploy_"): continue # Skip packages
        
        type_str = "Folder" if item.is_dir() else item.suffix
        size_mb = 0
        if item.is_file():
            size_mb = item.stat().st_size / (1024 * 1024)
        elif item.is_dir():
            size_mb = sum(f.stat().st_size for f in item.glob('**/*') if f.is_file()) / (1024 * 1024)
            
        table.add_row(item.name, type_str, f"{size_mb:.2f}")
    
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
    dep_mgr = ctx.deployment_manager
    if not dep_mgr:
        console.print("[red]Deployment Manager not loaded.[/red]")
        sys.exit(1)
        
    artifact_path = Path(ctx.config["output_dir"]) / artifact_name
    output_dir = Path(ctx.config["output_dir"])
    
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

@deploy.command('run')
@click.argument('package_path', type=click.Path(exists=True))
@click.option('--ip', required=True, help="Target IP")
@click.option('--user', required=True, help="Target Username")
@click.password_option('--password', help="Target Password (RAM only)")
@pass_context
def run_deploy(ctx: FrameworkContext, package_path, ip, user, password):
    """
    Upload and execute a deployment package on the target.
    """
    dep_mgr = ctx.deployment_manager
    if not dep_mgr:
        console.print("[red]Deployment Manager not loaded.[/red]")
        sys.exit(1)
        
    with console.status(f"Deploying to {user}@{ip}...", spinner="earth"):
        success = dep_mgr.deploy_artifact(
            artifact_path=Path(package_path),
            target_ip=ip,
            user=user,
            password=password
        )
        
    if success:
        console.print("[bold green]✅ Deployment Execution Successful![/bold green]")
    else:
        console.print("[bold red]❌ Deployment Failed.[/bold red]")
        sys.exit(1)

# ============================================================================
# SWARM COMMAND (NEU V2.0)
# ============================================================================

@cli.group()
def swarm():
    """Interact with Swarm Memory (Knowledge Base)"""
    pass

@swarm.command('upload')
@click.argument('file_path', type=click.Path(exists=True))
@click.option('--user', default="CLI_User", help="Contributor Username")
@pass_context
def swarm_upload(ctx: FrameworkContext, file_path, user):
    """
    Encrypt and upload a Knowledge Base export to the Swarm.
    Uses 'Fake-Encryption' (XOR+Wingdings) for obfuscation.
    """
    cm = ctx.framework_manager.community_manager
    if not cm:
        console.print("[red]Community Manager not loaded.[/red]")
        sys.exit(1)
        
    # Get Token from Secrets
    sm = ctx.framework_manager.secrets_manager
    token = sm.get_secret("github_token") if sm else None
    
    if not token:
        console.print("[red]No GitHub Token found in secrets![/red]")
        console.print("Use 'llm-cli secrets set github_token <TOKEN>' first.")
        sys.exit(1)

    with console.status(f"Encrypting & Uploading {file_path}...", spinner="dots"):
        success = cm.upload_knowledge_to_swarm(file_path, token, user)
        
    if success:
        console.print("[bold green]✅ Upload successful! You are now part of the Swarm.[/bold green]")
    else:
        console.print("[bold red]❌ Upload failed. Check logs.[/bold red]")
        sys.exit(1)

# ============================================================================
# SCAN COMMAND (NEU V2.0)
# ============================================================================

@cli.command('scan')
@click.option('--output', default="target_hardware_config.txt", help="Output filename")
@pass_context
def run_scan(ctx: FrameworkContext, output):
    """Run hardware probe script."""
    script_dir = Path(ctx.framework_manager.info.installation_path) / "scripts"
    output_file = Path(output).resolve()
    
    is_windows = platform.system() == "Windows"
    script = script_dir / ("hardware_probe.ps1" if is_windows else "hardware_probe.sh")
    
    if not script.exists():
        console.print(f"[red]Probe script not found at {script}[/red]")
        sys.exit(1)
        
    try:
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)] if is_windows else ["bash", str(script)]
        
        with console.status("Running Hardware Probe...", spinner="dots"):
            subprocess.run(cmd, check=True, cwd=os.getcwd())
            
        if output_file.exists():
            console.print(f"[bold green]✅ Probe successful. Config generated: {output_file}[/bold green]")
            
            # Optional: Import into TargetManager
            # tm = ctx.framework_manager.target_manager
            # if tm:
            #     tm.import_hardware_profile(output_file)
        else:
            console.print("[red]Probe script ran, but output file was not created.[/red]")
            
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Probe execution failed: {e}[/red]")
        sys.exit(1)


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
    """Show current configuration"""
    console.print(json.dumps(ctx.config, indent=2, default=str))

@config.command('repos')
@click.option('--add', nargs=2, help="Add repo override: <name> <url>")
@click.option('--remove', help="Remove repo override: <name>")
@pass_context
def config_repos(ctx: FrameworkContext, add, remove):
    """Manage SSOT Source Repositories"""
    cm = ctx.framework_manager.config
    current_repos = getattr(cm, 'source_repositories', {}) or {}
    
    if add:
        name, url = add
        current_repos[name.lower()] = url
        cm.set("source_repositories", current_repos)
        cm.save_user_config()
        console.print(f"[green]Added repo override: {name} -> {url}[/green]")
    elif remove:
        if remove.lower() in current_repos:
            del current_repos[remove.lower()]
            cm.set("source_repositories", current_repos)
            cm.save_user_config()
            console.print(f"[green]Removed repo override: {remove}[/green]")
        else:
            console.print(f"[yellow]Repo {remove} not found.[/yellow]")
    else:
        # List
        if not current_repos:
            console.print("[yellow]No repository overrides defined.[/yellow]")
        else:
            table = Table(title="Source Repositories (SSOT)")
            table.add_column("Key", style="cyan")
            table.add_column("URL", style="green")
            for k, v in current_repos.items():
                url = v['url'] if isinstance(v, dict) else v
                table.add_row(k, url)
            console.print(table)

if __name__ == "__main__":
    cli()
