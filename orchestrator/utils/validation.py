#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Validation Utilities

The framework's original validation toolkit (2025). Round 12 of the decisions (recon first, one implementation) kept
what the newer code uses or what no newer code does, and removed what newer code does better (each with its
replacement in the changelog of that round):

- The SSRF guard for every URL the framework fetches: validate_public_url with is_public_ip, resolve_public_addresses
  and _idna_host (crawler, crawl jobs, target sources, image registry), and validate_ip_address.
- validate_python_version: the supported-Python check that Mojo's Python check (Core/mojo/checks/python.py) runs on.
- check_docker_environment: the build engine's Docker check (ping of the framework's client, buildx), which Mojo's
  Docker check (Core/mojo/checks/docker.py) runs on as well.
- Checks no newer code does: validate_path with its path security check, the platform, port, CPU- and memory-usage
  and Docker version/Compose checks, validate_email, and the ValidationError family.

Importing this module checks nothing: the former check of the host at import time is gone (Mojo reports the Python
version and the free disk space, decision A4; the RAM a build needs is part of every build plan, disk_plan.host_fit).
"""

import ipaddress
import os
import platform
import re
import shutil
import socket
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from packaging import version

# Centralized Logging Integration
try:
    from orchestrator.utils.logging import get_logger
    logger = get_logger("ValidationUtils")
except ImportError:
    import logging
    logger = logging.getLogger("ValidationUtils")


# ============================================================================
# EXCEPTION CLASSES
# ============================================================================

class ValidationError(Exception):
    """Base exception for validation errors"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


class ConfigurationValidationError(ValidationError):
    """Configuration validation specific error"""
    pass


class SystemValidationError(ValidationError):
    """System validation specific error"""
    pass


class PathValidationError(ValidationError):
    """Path validation specific error"""
    pass


class SecurityValidationError(ValidationError):
    """Security validation specific error"""
    pass


class DependencyValidationError(ValidationError):
    """Dependency validation specific error"""
    pass


# ============================================================================
# ENUMS AND CONSTANTS
# ============================================================================

class ValidationSeverity(Enum):
    """Validation message severity levels"""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    SUCCESS = "success"


class ValidationType(Enum):
    """Types of validation"""
    CONFIGURATION = "configuration"
    SYSTEM = "system"
    SECURITY = "security"
    DEPENDENCY = "dependency"
    RUNTIME = "runtime"
    PERFORMANCE = "performance"


class PathType(Enum):
    """Path type expectations"""
    FILE = "file"
    DIRECTORY = "directory"
    EXECUTABLE = "executable"
    ANY = "any"


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class ValidationResult:
    """Validation result container"""
    valid: bool
    severity: ValidationSeverity
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    validation_type: ValidationType = ValidationType.CONFIGURATION
    timestamp: datetime = field(default_factory=datetime.now)

    # Callers write `if not validate_x(...)`; without this every result is truthy.
    def __bool__(self) -> bool:
        return bool(self.valid)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "valid": self.valid,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
            "validation_type": self.validation_type.value,
            "timestamp": self.timestamp.isoformat()
        }


# ============================================================================
# PATH VALIDATION
# ============================================================================

def validate_path(path: Union[str, Path], 
                 path_type: PathType = PathType.ANY,
                 must_exist: bool = True,
                 readable: bool = False,
                 writable: bool = False,
                 executable: bool = False,
                 check_security: bool = True) -> ValidationResult:
    """
    Validate file system path with comprehensive checks.
    
    Args:
        path: Path to validate
        path_type: Expected path type
        must_exist: Whether path must exist
        readable: Whether path must be readable
        writable: Whether path must be writable
        executable: Whether path must be executable
        check_security: Whether to perform security checks
        
    Returns:
        ValidationResult: Validation result
    """
    path_obj = Path(path) if isinstance(path, str) else path
    
    try:
        # Basic existence check
        if must_exist and not path_obj.exists():
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Path does not exist: {path}",
                validation_type=ValidationType.SYSTEM,
                details={"path": str(path), "expected": "exists"}
            )
        
        # If path doesn't exist and that's OK, check parent directory
        if not path_obj.exists() and not must_exist:
            parent_result = validate_path(
                path_obj.parent, 
                PathType.DIRECTORY, 
                must_exist=True, 
                writable=True,
                check_security=check_security
            )
            if not parent_result.valid:
                return ValidationResult(
                    valid=False,
                    severity=ValidationSeverity.ERROR,
                    message=f"Parent directory invalid for: {path}",
                    validation_type=ValidationType.SYSTEM,
                    details={"path": str(path), "parent_error": parent_result.message}
                )
        
        # Skip further checks if path doesn't exist
        if not path_obj.exists():
            return ValidationResult(
                valid=True,
                severity=ValidationSeverity.SUCCESS,
                message=f"Path validation passed (non-existent): {path}",
                validation_type=ValidationType.SYSTEM
            )
        
        # Path type validation
        if path_type == PathType.FILE and not path_obj.is_file():
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Path is not a file: {path}",
                validation_type=ValidationType.SYSTEM,
                details={"path": str(path), "expected": "file", "actual": "directory" if path_obj.is_dir() else "other"}
            )
        
        if path_type == PathType.DIRECTORY and not path_obj.is_dir():
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Path is not a directory: {path}",
                validation_type=ValidationType.SYSTEM,
                details={"path": str(path), "expected": "directory", "actual": "file" if path_obj.is_file() else "other"}
            )
        
        if path_type == PathType.EXECUTABLE and not (path_obj.is_file() and os.access(path_obj, os.X_OK)):
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Path is not executable: {path}",
                validation_type=ValidationType.SYSTEM,
                details={"path": str(path), "expected": "executable"}
            )
        
        # Permission checks
        if readable and not os.access(path_obj, os.R_OK):
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Path is not readable: {path}",
                validation_type=ValidationType.SECURITY,
                details={"path": str(path), "permission": "read"}
            )
        
        if writable and not os.access(path_obj, os.W_OK):
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Path is not writable: {path}",
                validation_type=ValidationType.SECURITY,
                details={"path": str(path), "permission": "write"}
            )
        
        if executable and not os.access(path_obj, os.X_OK):
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Path is not executable: {path}",
                validation_type=ValidationType.SECURITY,
                details={"path": str(path), "permission": "execute"}
            )
        
        # Security checks
        if check_security:
            security_result = _validate_path_security(path_obj)
            if not security_result.valid:
                return security_result
        
        return ValidationResult(
            valid=True,
            severity=ValidationSeverity.SUCCESS,
            message=f"Path validation passed: {path}",
            validation_type=ValidationType.SYSTEM,
            details={"path": str(path), "type": path_type.value}
        )
        
    except Exception as e:
        logger.error(f"Path validation error for {path}: {e}")
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Path validation error: {str(e)}",
            validation_type=ValidationType.SYSTEM,
            details={"path": str(path), "exception": str(e)}
        )


def _validate_path_security(path: Path) -> ValidationResult:
    """Validate path security (prevent path traversal, etc.)"""
    
    try:
        # Resolve path to check for traversal (an unresolvable path fails here)
        path.resolve()
        
        # Check for path traversal attempts
        if ".." in path.parts:
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Path traversal detected: {path}",
                validation_type=ValidationType.SECURITY,
                details={"security_issue": "path_traversal"}
            )
        
        # Check for world-writable files/directories (Unix)
        if hasattr(os, 'stat') and path.exists() and platform.system() != 'Windows':
            stat_info = path.stat()
            mode = stat_info.st_mode
            
            # Check if world-writable
            if mode & 0o002:  # World writable
                return ValidationResult(
                    valid=False,
                    severity=ValidationSeverity.WARNING,
                    message=f"Path is world-writable (security risk): {path}",
                    validation_type=ValidationType.SECURITY,
                    details={"security_issue": "world_writable", "mode": oct(mode)}
                )
        
        return ValidationResult(
            valid=True,
            severity=ValidationSeverity.SUCCESS,
            message=f"Path security validation passed: {path}",
            validation_type=ValidationType.SECURITY
        )
        
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Path security validation error: {str(e)}",
            validation_type=ValidationType.SECURITY,
            details={"exception": str(e)}
        )


# ============================================================================
# SYSTEM VALIDATION
# ============================================================================


def validate_python_version(min_version: str, below: Optional[str] = None,
                            current: Optional[str] = None) -> ValidationResult:
    """Validate the Python version: at least `min_version` and, when `below` is given, older than `below` (Mojo's
    supported range is 3.11 up to, not including, 3.15). `current` is the version to judge (default: this
    interpreter's); a pre-release of `below` counts as `below`."""
    try:
        current_version = current or platform.python_version()

        # Parse versions
        current_parsed = version.parse(current_version)
        minimum = version.parse(min_version)

        if current_parsed < minimum:
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Python version too old: {current_version} < {min_version}",
                validation_type=ValidationType.SYSTEM,
                details={"current_version": current_version, "minimum_version": min_version}
            )
        if below is not None and version.parse(current_parsed.base_version) >= version.parse(below):
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Python version too new: {current_version} >= {below}",
                validation_type=ValidationType.SYSTEM,
                details={"current_version": current_version, "minimum_version": min_version, "below": below}
            )
        details = {"current_version": current_version, "minimum_version": min_version}
        if below is not None:
            details["below"] = below
        return ValidationResult(
            valid=True,
            severity=ValidationSeverity.SUCCESS,
            message=f"Python version OK: {current_version} >= {min_version}"
                    + (f" and < {below}" if below is not None else ""),
            validation_type=ValidationType.SYSTEM,
            details=details
        )

    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Python version check failed: {str(e)}",
            validation_type=ValidationType.SYSTEM,
            details={"exception": str(e)}
        )


def _validate_platform(supported_platforms: List[str]) -> ValidationResult:
    """Validate platform support"""
    try:
        current_platform = platform.system().lower()
        
        # Normalize platform names
        platform_mapping = {
            'windows': 'windows',
            'linux': 'linux',
            'darwin': 'macos'
        }
        
        normalized_platform = platform_mapping.get(current_platform, current_platform)
        
        if normalized_platform in supported_platforms:
            return ValidationResult(
                valid=True,
                severity=ValidationSeverity.SUCCESS,
                message=f"Platform supported: {normalized_platform}",
                validation_type=ValidationType.SYSTEM,
                details={"platform": normalized_platform, "supported": supported_platforms}
            )
        else:
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Platform not supported: {normalized_platform}",
                validation_type=ValidationType.SYSTEM,
                details={"platform": normalized_platform, "supported": supported_platforms}
            )
            
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Platform check failed: {str(e)}",
            validation_type=ValidationType.SYSTEM,
            details={"exception": str(e)}
        )


def _validate_port_availability(port: int) -> ValidationResult:
    """Validate port availability"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', port))
            
            if result == 0:
                # Port is in use
                return ValidationResult(
                    valid=False,
                    severity=ValidationSeverity.WARNING,
                    message=f"Port already in use: {port}",
                    validation_type=ValidationType.SYSTEM,
                    details={"port": port, "status": "in_use"}
                )
            else:
                # Port is available
                return ValidationResult(
                    valid=True,
                    severity=ValidationSeverity.SUCCESS,
                    message=f"Port available: {port}",
                    validation_type=ValidationType.SYSTEM,
                    details={"port": port, "status": "available"}
                )
                
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Port check failed: {port} - {str(e)}",
            validation_type=ValidationType.SYSTEM,
            details={"port": port, "exception": str(e)}
        )


def _validate_cpu_usage(max_cpu_percent: float) -> ValidationResult:
    """Validate current CPU usage"""
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        
        if cpu_percent <= max_cpu_percent:
            return ValidationResult(
                valid=True,
                severity=ValidationSeverity.SUCCESS,
                message=f"CPU usage OK: {cpu_percent}% <= {max_cpu_percent}%",
                validation_type=ValidationType.PERFORMANCE,
                details={"cpu_percent": cpu_percent, "max_percent": max_cpu_percent}
            )
        else:
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.WARNING,
                message=f"High CPU usage: {cpu_percent}% > {max_cpu_percent}%",
                validation_type=ValidationType.PERFORMANCE,
                details={"cpu_percent": cpu_percent, "max_percent": max_cpu_percent}
            )
            
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"CPU usage check failed: {str(e)}",
            validation_type=ValidationType.PERFORMANCE,
            details={"exception": str(e)}
        )


def _validate_memory_usage(max_memory_percent: float) -> ValidationResult:
    """Validate current memory usage"""
    try:
        import psutil
        memory_info = psutil.virtual_memory()
        memory_percent = memory_info.percent
        
        if memory_percent <= max_memory_percent:
            return ValidationResult(
                valid=True,
                severity=ValidationSeverity.SUCCESS,
                message=f"Memory usage OK: {memory_percent}% <= {max_memory_percent}%",
                validation_type=ValidationType.PERFORMANCE,
                details={"memory_percent": memory_percent, "max_percent": max_memory_percent}
            )
        else:
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.WARNING,
                message=f"High memory usage: {memory_percent}% > {max_memory_percent}%",
                validation_type=ValidationType.PERFORMANCE,
                details={"memory_percent": memory_percent, "max_percent": max_memory_percent}
            )
            
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Memory usage check failed: {str(e)}",
            validation_type=ValidationType.PERFORMANCE,
            details={"exception": str(e)}
        )


# ============================================================================
# DOCKER VALIDATION
# ============================================================================


@dataclass
class DockerEnvironment:
    """What the Docker check found: the daemon behind the framework's Docker client answered a ping (`error` says why
    not), the docker command is on PATH, and `docker buildx version` succeeded (None: not asked or no answer).
    `engine`: 'docker', or 'podman' for Podman's Docker-compatible interface ('' unknown; utils/docker_engine.py);
    under Podman buildx is not asked: Podman has no BuildKit."""
    reachable: bool
    error: str = ""
    docker_cli: bool = False
    buildx: Optional[bool] = None
    engine: str = ""


def check_docker_environment(client: Any) -> DockerEnvironment:
    """The one Docker check of the build engine (its start, since 2025) and of Mojo: ping the framework's Docker
    client, the one builds use (the docker command can be set to another daemon by a docker context), name its engine
    (Docker or Podman), then, for Docker and when the docker command is on PATH, ask for BuildKit's buildx (at most 15
    seconds). Never raises: the engine runs it while the framework starts. The question goes through
    helpers.execute_command, the one runner of a short command (stdin closed, so an MCP server's stdio stream never
    reaches docker; no console window on Windows)."""
    if client is None:
        return DockerEnvironment(False, "no client")
    try:
        client.ping()
    except Exception as e:  # noqa: BLE001 - any failure means: builds cannot reach the daemon
        return DockerEnvironment(False, str(e))
    from orchestrator.utils.docker_engine import engine_name, ensure_docker_host
    ensure_docker_host()   # the docker command below talks to the same engine as the client
    engine = engine_name(client)
    if engine == "podman":
        return DockerEnvironment(True, docker_cli=bool(shutil.which("docker")), engine=engine)
    if not shutil.which("docker"):
        return DockerEnvironment(True, engine=engine)
    from orchestrator.utils.helpers import execute_command
    code, _out, _err = execute_command(["docker", "buildx", "version"], timeout=15)
    if code == -1:  # docker could not be started or gave no answer in time: buildx unknown
        return DockerEnvironment(True, docker_cli=True, engine=engine)
    return DockerEnvironment(True, docker_cli=True, buildx=code == 0, engine=engine)


def _validate_docker_version() -> ValidationResult:
    """Validate Docker version"""
    try:
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            version_output = result.stdout.strip()
            
            # Extract version number (basic parsing)
            version_match = re.search(r'(\d+\.\d+\.\d+)', version_output)
            if version_match:
                docker_version = version_match.group(1)
                
                # Check minimum version (example: 20.10.0)
                min_version = "20.10.0"
                if version.parse(docker_version) >= version.parse(min_version):
                    return ValidationResult(
                        valid=True,
                        severity=ValidationSeverity.SUCCESS,
                        message=f"Docker version OK: {docker_version}",
                        validation_type=ValidationType.DEPENDENCY,
                        details={"version": docker_version}
                    )
                else:
                    return ValidationResult(
                        valid=False,
                        severity=ValidationSeverity.WARNING,
                        message=f"Docker version may be outdated: {docker_version} < {min_version}",
                        validation_type=ValidationType.DEPENDENCY,
                        details={"version": docker_version, "minimum": min_version}
                    )
            else:
                return ValidationResult(
                    valid=True,
                    severity=ValidationSeverity.WARNING,
                    message=f"Could not parse Docker version: {version_output}",
                    validation_type=ValidationType.DEPENDENCY,
                    details={"output": version_output}
                )
        else:
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Docker version check failed: {result.stderr}",
                validation_type=ValidationType.DEPENDENCY,
                details={"stderr": result.stderr}
            )
            
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Docker version check error: {str(e)}",
            validation_type=ValidationType.DEPENDENCY,
            details={"exception": str(e)}
        )


def _validate_docker_compose() -> ValidationResult:
    """Validate Docker Compose availability"""
    try:
        # Try docker-compose command first
        result = subprocess.run(
            ["docker-compose", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            return ValidationResult(
                valid=True,
                severity=ValidationSeverity.SUCCESS,
                message="Docker Compose available (standalone)",
                validation_type=ValidationType.DEPENDENCY,
                details={"type": "standalone", "version": result.stdout.strip()}
            )
        
        # Try docker compose plugin
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            return ValidationResult(
                valid=True,
                severity=ValidationSeverity.SUCCESS,
                message="Docker Compose available (plugin)",
                validation_type=ValidationType.DEPENDENCY,
                details={"type": "plugin", "version": result.stdout.strip()}
            )
        
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.WARNING,
            message="Docker Compose not available",
            validation_type=ValidationType.DEPENDENCY
        )
        
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Docker Compose check error: {str(e)}",
            validation_type=ValidationType.DEPENDENCY,
            details={"exception": str(e)}
        )


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def is_public_ip(addr: Union[str, ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    """True only for globally routable unicast addresses, including IPv4 wrapped in IPv6."""
    if isinstance(addr, str):
        addr = ipaddress.ip_address(addr.split("%")[0])
    if addr.version == 6:
        if addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        elif addr in _NAT64_PREFIX:
            addr = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        elif addr.sixtofour:
            addr = addr.sixtofour
        elif addr.teredo:
            return False
    # is_global excludes private, loopback, link-local, CGNAT (100.64/10) and unspecified ranges.
    return addr.is_global and not addr.is_multicast and not addr.is_reserved


def resolve_public_addresses(host: str, port: int) -> List[str]:
    """Resolves host and returns its addresses; raises PermissionError if any of them is not public."""
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    addresses = [info[4][0] for info in infos]
    for address in addresses:
        if not is_public_ip(address):
            raise PermissionError(f"{host} resolves to non-public address {address}")
    return addresses


def _idna_host(host: str) -> str:
    try:
        import idna
        return idna.encode(host, uts46=True).decode("ascii").lower()
    except Exception:
        try:
            return host.encode("idna").decode("ascii").lower()
        except Exception:
            return host.lower()


def validate_public_url(url: str) -> ValidationResult:
    """Validate that a URL is http(s) and only resolves to public addresses (SSRF guard)."""
    def _fail(message: str) -> ValidationResult:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=message,
            validation_type=ValidationType.SECURITY,
            details={"url": url}
        )

    # urllib and urllib3/requests split authorities differently on these characters
    # (e.g. "http://127.0.0.1\\@example.com/"), so such URLs are rejected outright.
    if not isinstance(url, str) or "\\" in url or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7f for ch in url):
        return _fail(f"URL contains backslashes, whitespace or control characters: {url!r}")

    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except Exception as e:
        return _fail(f"URL validation error: {e}")

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return _fail(f"Only http(s) URLs with a host are allowed: {url}")

    try:
        from urllib3.util import parse_url
        fetch_host = (parse_url(url).host or "").strip("[]").lower()
    except Exception as e:
        return _fail(f"URL validation error: {e}")
    host = parsed.hostname.lower()
    if fetch_host != host and fetch_host != _idna_host(host):
        return _fail(f"Ambiguous URL host ({parsed.hostname!r} vs {fetch_host!r}): {url}")

    try:
        # Resolve the IDNA host that urllib3 will actually connect to.
        resolve_public_addresses(fetch_host, port or (443 if parsed.scheme == "https" else 80))
    except PermissionError as e:
        return _fail(f"{e}: {url}")
    except (socket.gaierror, ValueError, UnicodeError) as e:
        return _fail(f"Host could not be resolved: {parsed.hostname} ({e})")

    return ValidationResult(
        valid=True,
        severity=ValidationSeverity.SUCCESS,
        message=f"Public URL valid: {url}",
        validation_type=ValidationType.SECURITY,
        details={"url": url, "host": parsed.hostname}
    )


def validate_email(email: str) -> ValidationResult:
    """Validate email address format"""
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    if re.match(email_pattern, email):
        return ValidationResult(
            valid=True,
            severity=ValidationSeverity.SUCCESS,
            message=f"Email format valid: {email}",
            validation_type=ValidationType.CONFIGURATION,
            details={"email": email}
        )
    else:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Invalid email format: {email}",
            validation_type=ValidationType.CONFIGURATION,
            details={"email": email}
        )


def validate_ip_address(ip: str) -> ValidationResult:
    """Validate IP address format"""
    try:
        ip_obj = ipaddress.ip_address(ip)
        
        return ValidationResult(
            valid=True,
            severity=ValidationSeverity.SUCCESS,
            message=f"IP address valid: {ip} ({type(ip_obj).__name__})",
            validation_type=ValidationType.CONFIGURATION,
            details={"ip": ip, "type": type(ip_obj).__name__}
        )
        
    except ValueError as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Invalid IP address: {ip} - {str(e)}",
            validation_type=ValidationType.CONFIGURATION,
            details={"ip": ip, "error": str(e)}
        )
