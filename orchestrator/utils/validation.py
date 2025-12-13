#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Validation Utilities (v2.3.0)
DIREKTIVE: Goldstandard, professionell geschrieben.

Comprehensive validation system for configurations, data structures,
system requirements, and runtime validation. Container-native with
Poetry+VENV, robust error recovery and detailed reporting.

Key Responsibilities:
- Configuration validation (YAML, JSON, Python objects)
- System requirements validation (dependencies, hardware, software)
- Runtime data validation (user inputs, API parameters)
- Path and file system validation with security checks
- Docker environment validation
- Target hardware validation
- Model format validation
- Build configuration validation

Updates v2.3.0:
- Integrated centralized logging.
- refined Docker validation.
- Enhanced path security checks.
"""

import os
import sys
import json
import re
import subprocess
import platform
import socket
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple, Set, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import ipaddress
import urllib.parse

import yaml
import psutil
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


@dataclass
class ValidationReport:
    """Comprehensive validation report"""
    overall_valid: bool = True
    results: List[ValidationResult] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)
    validation_time: datetime = field(default_factory=datetime.now)
    
    def add_result(self, result: ValidationResult):
        """Add validation result"""
        self.results.append(result)
        
        # Update overall validity
        if not result.valid and result.severity == ValidationSeverity.ERROR:
            self.overall_valid = False
        
        # Update summary
        severity = result.severity.value
        self.summary[severity] = self.summary.get(severity, 0) + 1
    
    def add_error(self, message: str, details: Dict[str, Any] = None, 
                 validation_type: ValidationType = ValidationType.CONFIGURATION):
        """Add error result"""
        result = ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=message,
            details=details or {},
            validation_type=validation_type
        )
        self.add_result(result)
    
    def add_warning(self, message: str, details: Dict[str, Any] = None,
                   validation_type: ValidationType = ValidationType.CONFIGURATION):
        """Add warning result"""
        result = ValidationResult(
            valid=True,
            severity=ValidationSeverity.WARNING,
            message=message,
            details=details or {},
            validation_type=validation_type
        )
        self.add_result(result)
    
    def add_success(self, message: str, details: Dict[str, Any] = None,
                   validation_type: ValidationType = ValidationType.CONFIGURATION):
        """Add success result"""
        result = ValidationResult(
            valid=True,
            severity=ValidationSeverity.SUCCESS,
            message=message,
            details=details or {},
            validation_type=validation_type
        )
        self.add_result(result)
    
    def get_errors(self) -> List[ValidationResult]:
        """Get all error results"""
        return [r for r in self.results if r.severity == ValidationSeverity.ERROR]
    
    def get_warnings(self) -> List[ValidationResult]:
        """Get all warning results"""
        return [r for r in self.results if r.severity == ValidationSeverity.WARNING]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "overall_valid": self.overall_valid,
            "summary": self.summary,
            "validation_time": self.validation_time.isoformat(),
            "results": [r.to_dict() for r in self.results]
        }


@dataclass
class SystemRequirements:
    """System requirements specification"""
    min_python_version: str = "3.8.0"
    min_memory_gb: float = 4.0
    min_disk_space_gb: float = 10.0
    required_commands: List[str] = field(default_factory=lambda: ["docker", "git"])
    optional_commands: List[str] = field(default_factory=lambda: ["cmake", "ninja"])
    required_ports: List[int] = field(default_factory=list)
    supported_platforms: List[str] = field(default_factory=lambda: ["linux", "windows", "macos"])
    max_cpu_usage_percent: float = 80.0
    max_memory_usage_percent: float = 80.0


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
        # Resolve path to check for traversal
        resolved_path = path.resolve()
        
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


def validate_directory_writable(directory: Union[str, Path]) -> ValidationResult:
    """
    Validate that directory is writable by attempting to create a test file.
    
    Args:
        directory: Directory to test
        
    Returns:
        ValidationResult: Validation result
    """
    dir_path = Path(directory)
    
    # First check basic path validation
    path_result = validate_path(dir_path, PathType.DIRECTORY, must_exist=True)
    if not path_result.valid:
        return path_result
    
    try:
        # Attempt to create and delete a test file
        import tempfile
        import uuid
        
        test_filename = f".test_write_{uuid.uuid4().hex[:8]}"
        test_file = dir_path / test_filename
        
        # Write test
        with open(test_file, 'w') as f:
            f.write("test")
        
        # Read test
        with open(test_file, 'r') as f:
            content = f.read()
        
        # Cleanup
        test_file.unlink()
        
        if content != "test":
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Directory write test failed: {directory}",
                validation_type=ValidationType.SYSTEM,
                details={"test": "write_read_mismatch"}
            )
        
        return ValidationResult(
            valid=True,
            severity=ValidationSeverity.SUCCESS,
            message=f"Directory is writable: {directory}",
            validation_type=ValidationType.SYSTEM
        )
        
    except Exception as e:
        logger.error(f"Directory write test failed for {directory}: {e}")
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Directory write test error: {str(e)}",
            validation_type=ValidationType.SYSTEM,
            details={"exception": str(e), "directory": str(directory)}
        )


# ============================================================================
# CONFIGURATION VALIDATION
# ============================================================================

def validate_config(config: Dict[str, Any], 
                   schema: Optional[Dict[str, Any]] = None,
                   config_name: str = "configuration") -> ValidationReport:
    """
    Validate configuration dictionary against schema and business rules.
    
    Args:
        config: Configuration dictionary to validate
        schema: Optional schema for validation
        config_name: Name of configuration for error messages
        
    Returns:
        ValidationReport: Comprehensive validation report
    """
    report = ValidationReport()
    
    if not isinstance(config, dict):
        report.add_error(
            f"{config_name} must be a dictionary",
            {"actual_type": type(config).__name__},
            ValidationType.CONFIGURATION
        )
        return report
    
    # Schema-based validation if provided
    if schema:
        schema_result = _validate_against_schema(config, schema, config_name)
        for result in schema_result:
            report.add_result(result)
    
    # Business rules validation
    business_rules_result = _validate_business_rules(config, config_name)
    for result in business_rules_result:
        report.add_result(result)
    
    return report


def _validate_against_schema(config: Dict[str, Any], 
                           schema: Dict[str, Any], 
                           config_name: str) -> List[ValidationResult]:
    """Validate configuration against schema"""
    results = []
    
    # Check required fields
    required_fields = schema.get('required', [])
    for field in required_fields:
        if field not in config:
            results.append(ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Required field missing in {config_name}: {field}",
                validation_type=ValidationType.CONFIGURATION,
                details={"field": field, "config": config_name}
            ))
    
    # Check field types and constraints
    properties = schema.get('properties', {})
    for field_name, field_schema in properties.items():
        if field_name in config:
            field_results = _validate_field_against_schema(
                config[field_name], 
                field_schema, 
                f"{config_name}.{field_name}"
            )
            results.extend(field_results)
    
    # Check for unexpected fields
    allowed_fields = set(properties.keys())
    actual_fields = set(config.keys())
    unexpected_fields = actual_fields - allowed_fields
    
    for field in unexpected_fields:
        results.append(ValidationResult(
            valid=True,
            severity=ValidationSeverity.WARNING,
            message=f"Unexpected field in {config_name}: {field}",
            validation_type=ValidationType.CONFIGURATION,
            details={"field": field, "config": config_name}
        ))
    
    return results


def _validate_field_against_schema(value: Any, 
                                 field_schema: Dict[str, Any], 
                                 field_path: str) -> List[ValidationResult]:
    """Validate individual field against its schema"""
    results = []
    
    # Type validation
    expected_type = field_schema.get('type')
    if expected_type:
        type_valid = _check_type(value, expected_type)
        if not type_valid:
            results.append(ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Type mismatch for {field_path}: expected {expected_type}, got {type(value).__name__}",
                validation_type=ValidationType.CONFIGURATION,
                details={"field": field_path, "expected_type": expected_type, "actual_type": type(value).__name__}
            ))
            return results  # Skip further validation if type is wrong
    
    # Range validation for numbers
    if isinstance(value, (int, float)):
        minimum = field_schema.get('minimum')
        maximum = field_schema.get('maximum')
        
        if minimum is not None and value < minimum:
            results.append(ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Value too small for {field_path}: {value} < {minimum}",
                validation_type=ValidationType.CONFIGURATION,
                details={"field": field_path, "value": value, "minimum": minimum}
            ))
        
        if maximum is not None and value > maximum:
            results.append(ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Value too large for {field_path}: {value} > {maximum}",
                validation_type=ValidationType.CONFIGURATION,
                details={"field": field_path, "value": value, "maximum": maximum}
            ))
    
    # String validation
    if isinstance(value, str):
        min_length = field_schema.get('minLength')
        max_length = field_schema.get('maxLength')
        pattern = field_schema.get('pattern')
        
        if min_length is not None and len(value) < min_length:
            results.append(ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"String too short for {field_path}: {len(value)} < {min_length}",
                validation_type=ValidationType.CONFIGURATION,
                details={"field": field_path, "length": len(value), "min_length": min_length}
            ))
        
        if max_length is not None and len(value) > max_length:
            results.append(ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"String too long for {field_path}: {len(value)} > {max_length}",
                validation_type=ValidationType.CONFIGURATION,
                details={"field": field_path, "length": len(value), "max_length": max_length}
            ))
        
        if pattern and not re.match(pattern, value):
            results.append(ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"String does not match pattern for {field_path}: {pattern}",
                validation_type=ValidationType.CONFIGURATION,
                details={"field": field_path, "value": value, "pattern": pattern}
            ))
    
    # Enum validation
    enum_values = field_schema.get('enum')
    if enum_values and value not in enum_values:
        results.append(ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Invalid enum value for {field_path}: {value} not in {enum_values}",
            validation_type=ValidationType.CONFIGURATION,
            details={"field": field_path, "value": value, "allowed_values": enum_values}
        ))
    
    return results


def _check_type(value: Any, expected_type: str) -> bool:
    """Check if value matches expected type"""
    type_mapping = {
        'string': str,
        'integer': int,
        'number': (int, float),
        'boolean': bool,
        'array': list,
        'object': dict
    }
    
    expected_python_type = type_mapping.get(expected_type)
    if expected_python_type is None:
        return True  # Unknown type, assume valid
    
    return isinstance(value, expected_python_type)


def _validate_business_rules(config: Dict[str, Any], config_name: str) -> List[ValidationResult]:
    """Validate business-specific rules"""
    results = []
    
    # Framework-specific validation rules
    
    # Docker configuration validation
    if 'docker' in config:
        docker_config = config['docker']
        if isinstance(docker_config, dict):
            
            # Validate base image
            base_image = docker_config.get('base_image')
            if base_image and not _validate_docker_image_name(base_image):
                results.append(ValidationResult(
                    valid=False,
                    severity=ValidationSeverity.ERROR,
                    message=f"Invalid Docker image name: {base_image}",
                    validation_type=ValidationType.CONFIGURATION,
                    details={"field": "docker.base_image", "value": base_image}
                ))
    
    # Build configuration validation
    if 'build' in config:
        build_config = config['build']
        if isinstance(build_config, dict):
            
            # Validate timeout
            timeout = build_config.get('timeout')
            if timeout is not None:
                if not isinstance(timeout, int) or timeout <= 0:
                    results.append(ValidationResult(
                        valid=False,
                        severity=ValidationSeverity.ERROR,
                        message=f"Build timeout must be positive integer: {timeout}",
                        validation_type=ValidationType.CONFIGURATION,
                        details={"field": "build.timeout", "value": timeout}
                    ))
                elif timeout > 86400:  # 24 hours
                    results.append(ValidationResult(
                        valid=True,
                        severity=ValidationSeverity.WARNING,
                        message=f"Build timeout is very large: {timeout}s (>24h)",
                        validation_type=ValidationType.CONFIGURATION,
                        details={"field": "build.timeout", "value": timeout}
                    ))
    
    # Target configuration validation
    if 'targets' in config:
        targets = config['targets']
        if isinstance(targets, list):
            for i, target in enumerate(targets):
                if isinstance(target, str):
                    if not _validate_target_name(target):
                        results.append(ValidationResult(
                            valid=False,
                            severity=ValidationSeverity.ERROR,
                            message=f"Invalid target name: {target}",
                            validation_type=ValidationType.CONFIGURATION,
                            details={"field": f"targets[{i}]", "value": target}
                        ))
    
    return results


def _validate_docker_image_name(image_name: str) -> bool:
    """Validate Docker image name format"""
    # Basic Docker image name pattern
    # Allows: [registry/]namespace/name[:tag]
    pattern = r'^([a-z0-9]+([-._][a-z0-9]+)*\/)?([a-z0-9]+([-._][a-z0-9]+)*\/)?[a-z0-9]+([-._][a-z0-9]+)*(:[a-z0-9]+([-._][a-z0-9]+)*)?$'
    return bool(re.match(pattern, image_name, re.IGNORECASE))


def _validate_target_name(target_name: str) -> bool:
    """Validate target name format"""
    # Target names should be valid identifiers
    pattern = r'^[a-z][a-z0-9_]*$'
    return bool(re.match(pattern, target_name))


# ============================================================================
# SYSTEM VALIDATION
# ============================================================================

def validate_system_requirements(requirements: Optional[SystemRequirements] = None) -> ValidationReport:
    """
    Validate system meets minimum requirements.
    
    Args:
        requirements: System requirements specification
        
    Returns:
        ValidationReport: System validation report
    """
    if requirements is None:
        requirements = SystemRequirements()
    
    report = ValidationReport()
    
    # Python version check
    python_result = _validate_python_version(requirements.min_python_version)
    report.add_result(python_result)
    
    # Memory check
    memory_result = _validate_memory_requirements(requirements.min_memory_gb)
    report.add_result(memory_result)
    
    # Disk space check
    disk_result = _validate_disk_space(requirements.min_disk_space_gb)
    report.add_result(disk_result)
    
    # Platform check
    platform_result = _validate_platform(requirements.supported_platforms)
    report.add_result(platform_result)
    
    # Command availability check
    for command in requirements.required_commands:
        cmd_result = _validate_command_availability(command, required=True)
        report.add_result(cmd_result)
    
    for command in requirements.optional_commands:
        cmd_result = _validate_command_availability(command, required=False)
        report.add_result(cmd_result)
    
    # Port availability check
    for port in requirements.required_ports:
        port_result = _validate_port_availability(port)
        report.add_result(port_result)
    
    # Resource usage check
    cpu_result = _validate_cpu_usage(requirements.max_cpu_usage_percent)
    report.add_result(cpu_result)
    
    memory_usage_result = _validate_memory_usage(requirements.max_memory_usage_percent)
    report.add_result(memory_usage_result)
    
    return report


def _validate_python_version(min_version: str) -> ValidationResult:
    """Validate Python version"""
    try:
        current_version = platform.python_version()
        
        # Parse versions
        current = version.parse(current_version)
        minimum = version.parse(min_version)
        
        if current >= minimum:
            return ValidationResult(
                valid=True,
                severity=ValidationSeverity.SUCCESS,
                message=f"Python version OK: {current_version} >= {min_version}",
                validation_type=ValidationType.SYSTEM,
                details={"current_version": current_version, "minimum_version": min_version}
            )
        else:
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Python version too old: {current_version} < {min_version}",
                validation_type=ValidationType.SYSTEM,
                details={"current_version": current_version, "minimum_version": min_version}
            )
            
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Python version check failed: {str(e)}",
            validation_type=ValidationType.SYSTEM,
            details={"exception": str(e)}
        )


def _validate_memory_requirements(min_memory_gb: float) -> ValidationResult:
    """Validate system memory"""
    try:
        memory_info = psutil.virtual_memory()
        available_gb = memory_info.total / (1024 ** 3)
        
        if available_gb >= min_memory_gb:
            return ValidationResult(
                valid=True,
                severity=ValidationSeverity.SUCCESS,
                message=f"Memory OK: {available_gb:.1f}GB >= {min_memory_gb}GB",
                validation_type=ValidationType.SYSTEM,
                details={"available_gb": available_gb, "required_gb": min_memory_gb}
            )
        else:
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Insufficient memory: {available_gb:.1f}GB < {min_memory_gb}GB",
                validation_type=ValidationType.SYSTEM,
                details={"available_gb": available_gb, "required_gb": min_memory_gb}
            )
            
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Memory check failed: {str(e)}",
            validation_type=ValidationType.SYSTEM,
            details={"exception": str(e)}
        )


def _validate_disk_space(min_disk_gb: float, path: str = ".") -> ValidationResult:
    """Validate disk space"""
    try:
        disk_usage = psutil.disk_usage(path)
        available_gb = disk_usage.free / (1024 ** 3)
        
        if available_gb >= min_disk_gb:
            return ValidationResult(
                valid=True,
                severity=ValidationSeverity.SUCCESS,
                message=f"Disk space OK: {available_gb:.1f}GB >= {min_disk_gb}GB",
                validation_type=ValidationType.SYSTEM,
                details={"available_gb": available_gb, "required_gb": min_disk_gb, "path": path}
            )
        else:
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Insufficient disk space: {available_gb:.1f}GB < {min_disk_gb}GB",
                validation_type=ValidationType.SYSTEM,
                details={"available_gb": available_gb, "required_gb": min_disk_gb, "path": path}
            )
            
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Disk space check failed: {str(e)}",
            validation_type=ValidationType.SYSTEM,
            details={"exception": str(e), "path": path}
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


def _validate_command_availability(command: str, required: bool = True) -> ValidationResult:
    """Validate command availability"""
    try:
        # Check if command exists
        if platform.system().lower() == "windows":
            result = subprocess.run(
                ["where", command], 
                capture_output=True, 
                text=True, 
                timeout=5
            )
        else:
            result = subprocess.run(
                ["which", command], 
                capture_output=True, 
                text=True, 
                timeout=5
            )
        
        available = result.returncode == 0 and result.stdout.strip()
        
        if available:
            return ValidationResult(
                valid=True,
                severity=ValidationSeverity.SUCCESS,
                message=f"Command available: {command}",
                validation_type=ValidationType.DEPENDENCY,
                details={"command": command, "path": result.stdout.strip()}
            )
        else:
            severity = ValidationSeverity.ERROR if required else ValidationSeverity.WARNING
            return ValidationResult(
                valid=not required,
                severity=severity,
                message=f"Command not available: {command}",
                validation_type=ValidationType.DEPENDENCY,
                details={"command": command, "required": required}
            )
            
    except Exception as e:
        severity = ValidationSeverity.ERROR if required else ValidationSeverity.WARNING
        return ValidationResult(
            valid=not required,
            severity=severity,
            message=f"Command check failed: {command} - {str(e)}",
            validation_type=ValidationType.DEPENDENCY,
            details={"command": command, "exception": str(e)}
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

def validate_docker_environment() -> ValidationReport:
    """Validate Docker environment"""
    report = ValidationReport()
    
    # Check Docker command availability
    docker_cmd_result = _validate_command_availability("docker", required=True)
    report.add_result(docker_cmd_result)
    
    if not docker_cmd_result.valid:
        return report
    
    # Check Docker daemon
    daemon_result = _validate_docker_daemon()
    report.add_result(daemon_result)
    
    # Check Docker version
    version_result = _validate_docker_version()
    report.add_result(version_result)
    
    # Check Docker permissions
    permission_result = _validate_docker_permissions()
    report.add_result(permission_result)
    
    # Check Docker Compose
    compose_result = _validate_docker_compose()
    report.add_result(compose_result)
    
    return report


def _validate_docker_daemon() -> ValidationResult:
    """Validate Docker daemon is running"""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            return ValidationResult(
                valid=True,
                severity=ValidationSeverity.SUCCESS,
                message="Docker daemon is running",
                validation_type=ValidationType.DEPENDENCY
            )
        else:
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Docker daemon not accessible: {result.stderr}",
                validation_type=ValidationType.DEPENDENCY,
                details={"stderr": result.stderr}
            )
            
    except subprocess.TimeoutExpired:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message="Docker daemon check timed out",
            validation_type=ValidationType.DEPENDENCY
        )
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Docker daemon check failed: {str(e)}",
            validation_type=ValidationType.DEPENDENCY,
            details={"exception": str(e)}
        )


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


def _validate_docker_permissions() -> ValidationResult:
    """Validate Docker permissions (non-root access)"""
    try:
        result = subprocess.run(
            ["docker", "ps"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            return ValidationResult(
                valid=True,
                severity=ValidationSeverity.SUCCESS,
                message="Docker permissions OK",
                validation_type=ValidationType.SECURITY
            )
        else:
            error_msg = result.stderr.lower()
            if "permission denied" in error_msg or "access denied" in error_msg:
                return ValidationResult(
                    valid=False,
                    severity=ValidationSeverity.ERROR,
                    message="Docker permission denied - user may need to be in docker group",
                    validation_type=ValidationType.SECURITY,
                    details={"stderr": result.stderr}
                )
            else:
                return ValidationResult(
                    valid=False,
                    severity=ValidationSeverity.ERROR,
                    message=f"Docker access failed: {result.stderr}",
                    validation_type=ValidationType.DEPENDENCY,
                    details={"stderr": result.stderr}
                )
            
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"Docker permission check error: {str(e)}",
            validation_type=ValidationType.SECURITY,
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

def validate_url(url: str) -> ValidationResult:
    """Validate URL format and accessibility"""
    try:
        parsed = urllib.parse.urlparse(url)
        
        if not parsed.scheme or not parsed.netloc:
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.ERROR,
                message=f"Invalid URL format: {url}",
                validation_type=ValidationType.CONFIGURATION,
                details={"url": url}
            )
        
        if parsed.scheme not in ['http', 'https', 'ftp']:
            return ValidationResult(
                valid=False,
                severity=ValidationSeverity.WARNING,
                message=f"Unusual URL scheme: {parsed.scheme}",
                validation_type=ValidationType.CONFIGURATION,
                details={"url": url, "scheme": parsed.scheme}
            )
        
        return ValidationResult(
            valid=True,
            severity=ValidationSeverity.SUCCESS,
            message=f"URL format valid: {url}",
            validation_type=ValidationType.CONFIGURATION,
            details={"url": url, "scheme": parsed.scheme, "netloc": parsed.netloc}
        )
        
    except Exception as e:
        return ValidationResult(
            valid=False,
            severity=ValidationSeverity.ERROR,
            message=f"URL validation error: {str(e)}",
            validation_type=ValidationType.CONFIGURATION,
            details={"url": url, "exception": str(e)}
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


def create_comprehensive_validation_report(config: Dict[str, Any], 
                                         system_requirements: Optional[SystemRequirements] = None,
                                         validate_docker: bool = True,
                                         validate_paths: List[str] = None) -> ValidationReport:
    """
    Create comprehensive validation report covering all aspects.
    
    Args:
        config: Configuration to validate
        system_requirements: System requirements to check
        validate_docker: Whether to validate Docker environment
        validate_paths: List of paths to validate
        
    Returns:
        ValidationReport: Comprehensive validation report
    """
    overall_report = ValidationReport()
    
    # Configuration validation
    config_report = validate_config(config)
    for result in config_report.results:
        overall_report.add_result(result)
    
    # System validation
    system_report = validate_system_requirements(system_requirements)
    for result in system_report.results:
        overall_report.add_result(result)
    
    # Docker validation
    if validate_docker:
        docker_report = validate_docker_environment()
        for result in docker_report.results:
            overall_report.add_result(result)
    
    # Path validation
    if validate_paths:
        for path_str in validate_paths:
            path_result = validate_path(path_str)
            overall_report.add_result(path_result)
    
    return overall_report


# ============================================================================
# MODULE INITIALIZATION
# ============================================================================

def get_validation_info() -> Dict[str, Any]:
    """Get validation module information"""
    return {
        "module": "orchestrator.utils.validation",
        "version": "1.0.0",
        "description": "Comprehensive validation utilities for LLM Cross-Compiler Framework",
        "validation_types": [vt.value for vt in ValidationType],
        "severity_levels": [vs.value for vs in ValidationSeverity],
        "path_types": [pt.value for pt in PathType],
        "functions": [
            "validate_path", "validate_config", "validate_system_requirements",
            "validate_docker_environment", "validate_url", "validate_email",
            "validate_ip_address", "create_comprehensive_validation_report"
        ]
    }


# Initialize validation system
try:
    # Perform basic system validation on import
    basic_requirements = SystemRequirements(
        min_python_version="3.8.0",
        min_memory_gb=2.0,
        min_disk_space_gb=5.0,
        required_commands=[],  # Don't require anything on import
        supported_platforms=["linux", "windows", "macos"]
    )
    
    _init_validation = validate_system_requirements(basic_requirements)
    if not _init_validation.overall_valid:
        # Log warnings using logger but don't break import
        for error in _init_validation.get_errors():
            logger.warning(f"Validation Warning on Import: {error.message}")
            
except Exception:
    # Don't break import if validation fails
    pass
