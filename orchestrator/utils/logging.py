#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Logging Utilities (v2.3.0)
DIREKTIVE: Goldstandard, vollständig, professionell geschrieben.

Enterprise-grade logging system with structured logging, performance metrics,
and multi-output support. Container-native with Poetry+VENV.

Key Responsibilities:
- Structured logging with JSON support
- Performance metrics integration
- Log rotation and size management
- Multi-level configuration per module
- Real-time log streaming for GUI
- Error tracking and alerting
- Debug trace collection
"""

import sys
import json
import logging
import re
import logging.handlers
import threading
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
import queue

import yaml

from orchestrator.security.redaction import configure_sdk_loggers, install_log_redaction
from orchestrator.security.secret_patterns import redact


# ============================================================================
# ENUMS AND CONSTANTS
# ============================================================================

class LogLevel(Enum):
    """Enhanced log levels"""
    TRACE = 5
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50
    PERFORMANCE = 25  # Custom level for performance metrics


class LogFormat(Enum):
    """Log output formats"""
    STANDARD = "standard"
    JSON = "json"
    STRUCTURED = "structured"
    MINIMAL = "minimal"
    DETAILED = "detailed"


class LogDestination(Enum):
    """Log output destinations"""
    CONSOLE = "console"
    FILE = "file"
    ROTATING_FILE = "rotating_file"
    SYSLOG = "syslog"
    MEMORY = "memory"
    STREAM = "stream"


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class LogEntry:
    """Structured log entry"""
    timestamp: datetime
    level: str
    logger_name: str
    message: str
    module: str = ""
    function: str = ""
    line_number: int = 0
    thread_id: int = 0
    
    # Performance metrics
    execution_time_ms: Optional[float] = None
    memory_usage_mb: Optional[float] = None
    cpu_percent: Optional[float] = None
    
    # Context information
    build_id: Optional[str] = None
    request_id: Optional[str] = None
    user_id: Optional[str] = None
    
    # Error information
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    stack_trace: Optional[str] = None
    
    # Additional fields
    extra_fields: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        result = asdict(self)
        result["timestamp"] = self.timestamp.isoformat()
        return result
    
    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), default=str)


@dataclass
class LoggerConfig:
    """Configuration for individual loggers"""
    name: str
    level: LogLevel = LogLevel.INFO
    format: LogFormat = LogFormat.STANDARD
    destinations: List[LogDestination] = field(default_factory=lambda: [LogDestination.CONSOLE])
    
    # File-specific settings
    file_path: Optional[str] = None
    max_file_size_mb: int = 100
    backup_count: int = 5
    
    # Performance tracking
    enable_performance_tracking: bool = False
    performance_threshold_ms: float = 1000.0
    
    # Filtering
    enable_filtering: bool = False
    filter_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)


@dataclass
class LoggingStats:
    """Logging system statistics"""
    total_messages: int = 0
    messages_by_level: Dict[str, int] = field(default_factory=dict)
    messages_by_logger: Dict[str, int] = field(default_factory=dict)
    error_count: int = 0
    warning_count: int = 0
    
    # Performance stats
    average_log_time_ms: float = 0.0
    slowest_operations: List[Dict[str, Any]] = field(default_factory=list)
    
    # Memory stats
    memory_buffer_size: int = 0
    disk_usage_mb: float = 0.0
    
    start_time: datetime = field(default_factory=datetime.now)


# ============================================================================
# CUSTOM FORMATTERS
# ============================================================================

def _redacted_extra(value: Any) -> Any:
    """An `extra=` field for the JSON log. Extra fields are set after the record is created, so the redaction
    of the message does not cover them."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact(value)
    try:
        text = redact(json.dumps(value, default=str))
    except (TypeError, ValueError):  # e.g. a circular structure
        return redact(repr(value))
    try:
        return json.loads(text)
    except ValueError:
        return text


class StructuredFormatter(logging.Formatter):
    """Structured formatter for enhanced logging"""
    
    def __init__(self, format_type: LogFormat = LogFormat.STANDARD):
        super().__init__()
        self.format_type = format_type
        
        # Define format templates
        self.formats = {
            LogFormat.STANDARD: "%(asctime)s - %(name)s - [%(levelname)s] - %(message)s",
            LogFormat.MINIMAL: "%(levelname)s: %(message)s",
            LogFormat.DETAILED: "%(asctime)s - %(name)s - [%(levelname)s] - %(filename)s:%(lineno)d - %(funcName)s() - %(message)s",
            LogFormat.JSON: None,  # Special handling
            LogFormat.STRUCTURED: None  # Special handling
        }
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record based on configured format type"""
        
        # Add custom attributes
        record.thread_id = threading.get_ident()
        
        if self.format_type == LogFormat.JSON:
            return self._format_json(record)
        elif self.format_type == LogFormat.STRUCTURED:
            return self._format_structured(record)
        else:
            # Standard formatting
            formatter = logging.Formatter(
                self.formats[self.format_type],
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            return formatter.format(record)
    
    def _format_json(self, record: logging.LogRecord) -> str:
        """Format as JSON"""
        log_entry = LogEntry(
            timestamp=datetime.fromtimestamp(record.created),
            level=record.levelname,
            logger_name=record.name,
            message=record.getMessage(),
            module=record.module if hasattr(record, 'module') else record.filename,
            function=record.funcName,
            line_number=record.lineno,
            thread_id=getattr(record, 'thread_id', 0)
        )
        
        # Add exception information if present
        if record.exc_info:
            log_entry.exception_type = record.exc_info[0].__name__
            log_entry.exception_message = redact(str(record.exc_info[1]))
            log_entry.stack_trace = record.exc_text or redact("".join(traceback.format_exception(*record.exc_info)))
        
        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename',
                           'module', 'lineno', 'funcName', 'created', 'msecs', 'relativeCreated',
                           'thread', 'threadName', 'processName', 'process', 'message', 'exc_info',
                           'exc_text', 'stack_info', 'thread_id']:
                log_entry.extra_fields[key] = _redacted_extra(value)
        
        return log_entry.to_json()
    
    def _format_structured(self, record: logging.LogRecord) -> str:
        """Format as structured text"""
        parts = [
            f"[{datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')}]",
            f"[{record.levelname}]",
            f"[{record.name}]",
            f"[{record.filename}:{record.lineno}]",
            f"[{record.funcName}()]",
            f"- {record.getMessage()}"
        ]
        
        # Add performance info if available
        if hasattr(record, 'execution_time_ms'):
            parts.insert(-1, f"[{record.execution_time_ms:.2f}ms]")
        elif hasattr(record, 'duration_ms'):
            parts.insert(-1, f"[{record.duration_ms:.0f}ms]")
        
        return " ".join(parts)


class PerformanceFilter(logging.Filter):
    """Filter for performance-related logging"""
    
    def __init__(self, threshold_ms: float = 1000.0):
        super().__init__()
        self.threshold_ms = threshold_ms
    
    def filter(self, record: logging.LogRecord) -> bool:
        """Filter based on performance threshold"""
        if hasattr(record, 'execution_time_ms'):
            return record.execution_time_ms >= self.threshold_ms
        return True


class MemoryHandler(logging.Handler):
    """In-memory handler for GUI log streaming"""
    
    def __init__(self, max_entries: int = 1000):
        super().__init__()
        self.max_entries = max_entries
        self.entries: queue.Queue = queue.Queue(maxsize=max_entries)
        self._lock = threading.Lock()
    
    def emit(self, record: logging.LogRecord):
        """Store log record in memory"""
        try:
            with self._lock:
                if self.entries.full():
                    # Remove oldest entry
                    try:
                        self.entries.get_nowait()
                    except queue.Empty:
                        pass
                
                # Add new entry
                formatted_message = self.format(record)
                self.entries.put_nowait({
                    'timestamp': datetime.fromtimestamp(record.created),
                    'level': record.levelname,
                    'logger': record.name,
                    'message': formatted_message,
                    'raw_record': record
                })
        except Exception:
            # Don't let logging errors break the application
            pass
    
    def get_recent_entries(self, count: int = 100) -> List[Dict[str, Any]]:
        """Get recent log entries"""
        entries = []
        temp_entries = []
        
        with self._lock:
            # Get all entries
            while not self.entries.empty():
                try:
                    entry = self.entries.get_nowait()
                    temp_entries.append(entry)
                except queue.Empty:
                    break
            
            # Put them back and get the last 'count' entries
            for entry in temp_entries:
                try:
                    self.entries.put_nowait(entry)
                except queue.Full:
                    break
            
            # Return the most recent entries
            entries = temp_entries[-count:] if temp_entries else []
        
        return entries


# ============================================================================
# PERFORMANCE TRACKING
# ============================================================================

class PerformanceTracker:
    """Performance tracking for logging"""
    
    def __init__(self):
        self.active_operations: Dict[str, float] = {}
        self._lock = threading.Lock()
    
    def start_operation(self, operation_id: str) -> str:
        """Start tracking an operation"""
        with self._lock:
            start_time = time.perf_counter()
            self.active_operations[operation_id] = start_time
            return operation_id
    
    def end_operation(self, operation_id: str) -> Optional[float]:
        """End tracking and return execution time in ms"""
        with self._lock:
            start_time = self.active_operations.pop(operation_id, None)
            if start_time is not None:
                execution_time = (time.perf_counter() - start_time) * 1000
                return execution_time
            return None


class LoggingMetrics:
    """Collect logging system metrics"""
    
    def __init__(self):
        self.stats = LoggingStats()
        self._lock = threading.Lock()
    
    def record_message(self, level: str, logger_name: str, execution_time_ms: float = None):
        """Record a log message for metrics"""
        with self._lock:
            self.stats.total_messages += 1
            
            # Update level counts
            if level not in self.stats.messages_by_level:
                self.stats.messages_by_level[level] = 0
            self.stats.messages_by_level[level] += 1
            
            # Update logger counts
            if logger_name not in self.stats.messages_by_logger:
                self.stats.messages_by_logger[logger_name] = 0
            self.stats.messages_by_logger[logger_name] += 1
            
            # Update error/warning counts
            if level == 'ERROR':
                self.stats.error_count += 1
            elif level == 'WARNING':
                self.stats.warning_count += 1
            
            # Update performance stats
            if execution_time_ms is not None:
                current_avg = self.stats.average_log_time_ms
                total_messages = self.stats.total_messages
                
                # Calculate new average
                self.stats.average_log_time_ms = (
                    (current_avg * (total_messages - 1) + execution_time_ms) / total_messages
                )
                
                # Track slow operations
                if execution_time_ms > 100:  # 100ms threshold
                    self.stats.slowest_operations.append({
                        'logger': logger_name,
                        'execution_time_ms': execution_time_ms,
                        'timestamp': datetime.now().isoformat()
                    })
                    
                    # Keep only last 10 slow operations
                    if len(self.stats.slowest_operations) > 10:
                        self.stats.slowest_operations = self.stats.slowest_operations[-10:]
    
    def get_stats(self) -> LoggingStats:
        """Get current statistics"""
        with self._lock:
            return self.stats


# ============================================================================
# MAIN LOGGING MANAGER
# ============================================================================

class LoggingManager:
    """
    Enterprise logging manager for the LLM Cross-Compiler Framework.
    
    Provides structured logging, performance tracking, and multi-output support.
    """
    
    def __init__(self):
        self.logger_configs: Dict[str, LoggerConfig] = {}
        self.performance_tracker = PerformanceTracker()
        self.metrics = LoggingMetrics()
        self.memory_handler: Optional[MemoryHandler] = None
        self._initialized = False
        self._lock = threading.Lock()
        # the levels per part (step 7 of the one log): the person's setting log_levels, checked (part_levels_of)
        self.part_levels: Dict[str, str] = {}
        self._before_part: Dict[str, int] = {}   # a logger's own level before a part level was set on it
        self._sink_base: Dict[int, int] = {}      # id(root or framework.log handler) -> its level without the parts
        
        # Add custom log level
        logging.addLevelName(LogLevel.PERFORMANCE.value, "PERFORMANCE")
        logging.addLevelName(LogLevel.TRACE.value, "TRACE")
    
    def initialize(self, config_file: Optional[Path] = None, 
                   default_level: LogLevel = LogLevel.INFO,
                   enable_memory_handler: bool = True):
        """
        Initialize the logging system.
        
        Args:
            config_file: Optional configuration file
            default_level: Default logging level
            enable_memory_handler: Enable in-memory handler for GUI
        """
        with self._lock:
            if self._initialized:
                return
            
            # Setup memory handler for GUI streaming
            if enable_memory_handler:
                self.memory_handler = MemoryHandler(max_entries=1000)
                self.memory_handler.setFormatter(StructuredFormatter(LogFormat.STRUCTURED))
            
            # Load configuration
            if config_file and config_file.exists():
                self._load_config_file(config_file)
            else:
                self._setup_default_configuration(default_level)
            
            # Setup root logger
            self._setup_root_logger()
            
            self._initialized = True
    
    def _load_config_file(self, config_file: Path):
        """Load logging configuration from file"""
        try:
            with open(config_file, 'r') as f:
                config_data = yaml.safe_load(f)
            
            loggers_config = config_data.get('loggers', {})
            
            for logger_name, logger_settings in loggers_config.items():
                config = LoggerConfig(
                    name=logger_name,
                    level=LogLevel(logger_settings.get('level', LogLevel.INFO.value)),
                    format=LogFormat(logger_settings.get('format', LogFormat.STANDARD.value)),
                    destinations=[
                        LogDestination(dest) for dest in logger_settings.get('destinations', ['console'])
                    ],
                    file_path=logger_settings.get('file_path'),
                    max_file_size_mb=logger_settings.get('max_file_size_mb', 100),
                    backup_count=logger_settings.get('backup_count', 5),
                    enable_performance_tracking=logger_settings.get('enable_performance_tracking', False),
                    performance_threshold_ms=logger_settings.get('performance_threshold_ms', 1000.0)
                )
                
                self.logger_configs[logger_name] = config
                
        except Exception as e:
            # Fallback to default configuration
            print(f"Failed to load logging config: {e}", file=sys.stderr)
            self._setup_default_configuration(LogLevel.INFO)
    
    def _setup_default_configuration(self, default_level: LogLevel):
        """Setup default logging configuration"""
        
        # Framework-wide default
        self.logger_configs['framework'] = LoggerConfig(
            name='framework',
            level=default_level,
            format=LogFormat.STANDARD,
            destinations=[LogDestination.CONSOLE]
        )
        
        # Core modules with enhanced logging
        core_modules = [
            'orchestrator.core.framework',
            'orchestrator.core.builder', 
            'orchestrator.core.docker_manager',
            'orchestrator.core.config_manager',
            'orchestrator.core.orchestrator',
            'orchestrator.core.target_manager',
            'orchestrator.core.model_manager'
        ]
        
        for module in core_modules:
            self.logger_configs[module] = LoggerConfig(
                name=module,
                level=default_level,
                format=LogFormat.DETAILED,
                destinations=[LogDestination.CONSOLE, LogDestination.ROTATING_FILE],
                file_path=f"logs/{module.split('.')[-1]}.log",
                enable_performance_tracking=True
            )
    
    def _setup_root_logger(self):
        """Setup root logger configuration"""
        root_logger = logging.getLogger()
        
        # Clear existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        
        # Set root level to lowest configured level
        # Safely calculate min level
        if self.logger_configs:
            min_level = min([config.level.value for config in self.logger_configs.values()])
        else:
            min_level = LogLevel.INFO.value
            
        root_logger.setLevel(min_level)
        
        # Add memory handler if enabled
        if self.memory_handler:
            root_logger.addHandler(self.memory_handler)
    
    def get_logger(self, name: str) -> logging.Logger:
        """
        Get configured logger instance.
        
        Args:
            name: Logger name
            
        Returns:
            logging.Logger: Configured logger
        """
        logger = logging.getLogger(name)
        
        # Find best matching configuration
        config = self._find_logger_config(name)
        
        if config and not logger.handlers:
            self._configure_logger(logger, config)
        if self.part_levels:
            self._apply_part_level(name, logger)

        # Add performance tracking methods
        self._add_performance_methods(logger, config)
        
        return logger
    
    def _find_logger_config(self, name: str) -> Optional[LoggerConfig]:
        """Find best matching logger configuration"""
        # Exact match
        if name in self.logger_configs:
            return self.logger_configs[name]
        
        # Hierarchical match (e.g., 'orchestrator.core.builder' matches 'orchestrator.core')
        name_parts = name.split('.')
        for i in range(len(name_parts), 0, -1):
            partial_name = '.'.join(name_parts[:i])
            if partial_name in self.logger_configs:
                return self.logger_configs[partial_name]
        
        # Default configuration
        return self.logger_configs.get('framework')
    
    def _configure_logger(self, logger: logging.Logger, config: LoggerConfig):
        """Configure individual logger"""
        logger.setLevel(config.level.value)
        
        # Setup formatters and handlers
        formatter = StructuredFormatter(config.format)
        
        for destination in config.destinations:
            handler = self._create_handler(destination, config)
            if handler:
                handler.setFormatter(formatter)
                
                # Add performance filter if enabled
                if config.enable_performance_tracking:
                    perf_filter = PerformanceFilter(config.performance_threshold_ms)
                    handler.addFilter(perf_filter)
                
                logger.addHandler(handler)
    
    def _create_handler(self, destination: LogDestination, config: LoggerConfig) -> Optional[logging.Handler]:
        """Create appropriate handler for destination"""
        
        if destination == LogDestination.CONSOLE:
            # stdout is reserved for command output (e.g. `cli ... -f json`).
            return logging.StreamHandler(sys.stderr)
        
        elif destination == LogDestination.FILE:
            if config.file_path:
                # Ensure directory exists
                log_file = Path(config.file_path)
                log_file.parent.mkdir(parents=True, exist_ok=True)
                return logging.FileHandler(config.file_path)
        
        elif destination == LogDestination.ROTATING_FILE:
            if config.file_path:
                log_file = Path(config.file_path)
                log_file.parent.mkdir(parents=True, exist_ok=True)
                return logging.handlers.RotatingFileHandler(
                    config.file_path,
                    maxBytes=config.max_file_size_mb * 1024 * 1024,
                    backupCount=config.backup_count
                )
        
        elif destination == LogDestination.MEMORY:
            return self.memory_handler
        
        return None
    
    def _add_performance_methods(self, logger: logging.Logger, config: Optional[LoggerConfig]):
        """Add performance tracking methods to logger"""
        
        def start_performance_tracking(operation_name: str) -> str:
            """Start tracking operation performance"""
            operation_id = f"{logger.name}_{operation_name}_{int(time.time()*1000)}"
            self.performance_tracker.start_operation(operation_id)
            return operation_id
        
        def end_performance_tracking(operation_id: str, message: str = ""):
            """End tracking and log performance"""
            execution_time = self.performance_tracker.end_operation(operation_id)
            if execution_time is not None:
                # Record metrics
                self.metrics.record_message(
                    'PERFORMANCE', 
                    logger.name, 
                    execution_time
                )
                
                # Log performance if enabled and above threshold
                if (config and config.enable_performance_tracking and 
                    execution_time >= config.performance_threshold_ms):
                    
                    logger.log(
                        LogLevel.PERFORMANCE.value,
                        f"Performance: {message} took {execution_time:.2f}ms",
                        extra={'execution_time_ms': execution_time}
                    )
        
        def trace(message: str, *args, **kwargs):
            """Log trace level message"""
            logger.log(LogLevel.TRACE.value, message, *args, **kwargs)
        
        # Add methods to logger
        logger.start_perf = start_performance_tracking
        logger.end_perf = end_performance_tracking
        logger.trace = trace
    
    def _apply_part_level(self, name: str, logger: logging.Logger) -> None:
        level = part_level_for(name, self.part_levels)
        if level is not None:
            self._before_part.setdefault(name, logger.level)
            logger.setLevel(level)
        elif name in self._before_part:
            logger.setLevel(self._before_part.pop(name))

    def apply_part_levels(self, levels: Any) -> Dict[str, str]:
        """The one place that applies the levels per part (the setting log_levels; part_levels_of checks it): every
        logger whose name is a part or lies below one (a dotted prefix) gets that level, the most specific part
        winning; a logger no part names any more gets its own level back. The root logger and framework.log take the
        lowest level asked for, so a part set to DEBUG reaches framework.log and every other part stays as it was.
        Called at boot, after a change and after set_global_level. Returns the levels now in force."""
        with self._lock:
            self.part_levels = part_levels_of(levels)
            for name in list(logging.Logger.manager.loggerDict):
                logger = logging.Logger.manager.loggerDict.get(name)
                if isinstance(logger, logging.Logger):
                    self._apply_part_level(name, logger)
            self._open_sinks()
            return dict(self.part_levels)

    def _open_sinks(self) -> None:
        """The root logger and framework.log's handler at the lowest of their own level and the parts' levels."""
        from orchestrator.utils.framework_log import FRAMEWORK_LOG_HANDLER
        root = logging.getLogger()
        lowest = min((logging.getLevelName(v) for v in self.part_levels.values()), default=None)
        sinks = [root] + [h for h in root.handlers if h.get_name() == FRAMEWORK_LOG_HANDLER]
        for sink in sinks:
            base = self._sink_base.setdefault(id(sink), sink.level)
            sink.setLevel(min(base, lowest) if lowest is not None else base)
        if lowest is None:
            self._sink_base.clear()

    def get_recent_logs(self, count: int = 100) -> List[Dict[str, Any]]:
        """Get recent log entries from memory handler"""
        if self.memory_handler:
            return self.memory_handler.get_recent_entries(count)
        return []
    
    def get_logging_stats(self) -> LoggingStats:
        """Get logging system statistics"""
        return self.metrics.get_stats()
    
    def configure_logger(self, name: str, config: LoggerConfig):
        """Dynamically configure or reconfigure a logger"""
        with self._lock:
            self.logger_configs[name] = config
            
            # Reconfigure existing logger if it exists
            logger = logging.getLogger(name)
            if logger.handlers:
                # Remove existing handlers
                for handler in logger.handlers[:]:
                    logger.removeHandler(handler)
                
                # Reconfigure
                self._configure_logger(logger, config)
    
    def set_global_level(self, level: LogLevel):
        """Set global logging level for all loggers"""
        with self._lock:
            for config in self.logger_configs.values():
                config.level = level
            
            # Update existing loggers
            for name in logging.Logger.manager.loggerDict:
                logger = logging.getLogger(name)
                logger.setLevel(level.value)
            self._before_part.clear()   # the new level is every logger's own now
        self.apply_part_levels(self.part_levels)
    
    def export_logs(self, output_file: Path, start_time: Optional[datetime] = None,
                   end_time: Optional[datetime] = None, format: LogFormat = LogFormat.JSON) -> bool:
        """Exports the framework's log (framework.log of every process, utils/framework_log) between `start_time`
        and `end_time` to `output_file`, as JSON or (any other format) text, private and with the home folder
        written as ~USER; `llmcf logs export` and the Help menu's Framework Log window do the same. Without the log
        file (a process that never booted the framework) the newest entries this process keeps in memory are
        exported. True when the file was written."""
        from orchestrator.utils import framework_log
        from orchestrator.utils.framework_log import FRAMEWORK_LOG_HANDLER
        fmt = "json" if format == LogFormat.JSON else "text"
        handler = next((h for h in logging.getLogger().handlers if h.get_name() == FRAMEWORK_LOG_HANDLER), None)
        try:
            if handler is not None and getattr(handler, "baseFilename", None):
                entries = framework_log.read_entries(Path(handler.baseFilename).parent, since=start_time,
                                                     until=end_time)
            else:
                entries = [{"timestamp": e["timestamp"].isoformat() if isinstance(e["timestamp"], datetime)
                            else str(e["timestamp"]), "level": e["level"], "logger_name": e["logger"],
                            "message": e["message"]}
                           for e in self.get_recent_logs(10000)
                           if (start_time is None or e["timestamp"] >= start_time)
                           and (end_time is None or e["timestamp"] <= end_time)]
            framework_log.export(entries, Path(output_file), fmt)
            return True
        except (OSError, ValueError) as e:
            print(f"Failed to export logs: {e}", file=sys.stderr)
            return False
    
    def cleanup(self):
        """Cleanup logging resources"""
        with self._lock:
            # Close all handlers
            for logger_name in list(logging.Logger.manager.loggerDict.keys()):
                logger = logging.getLogger(logger_name)
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)
            
            # Reset state
            self.logger_configs.clear()
            self._initialized = False


# ============================================================================
# GLOBAL LOGGING MANAGER INSTANCE
# ============================================================================

# Global instance for framework-wide use
_logging_manager: Optional[LoggingManager] = None

# The levels per part (step 7 of the one log): the person's setting {part: level}. A part is a logger name (the
# `part` of `llmcf logs show`) or a dotted prefix of one; an AI reads the setting (get_settings), never changes it.
LOG_LEVELS_SETTING = "log_levels"
PART_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
MAX_PARTS = 50
_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}(?:\.[A-Za-z_][A-Za-z0-9_-]{0,63}){0,7}$")


def valid_part(part: Any) -> bool:
    return isinstance(part, str) and len(part) <= 200 and bool(_PART.match(part))


def part_levels_of(value: Any) -> Dict[str, str]:
    """The setting's levels per part, checked: a part that is no logger name, a level that is not DEBUG to
    CRITICAL and everything beyond MAX_PARTS are left out (a hand-edited config.yml never breaks the logging)."""
    found: Dict[str, str] = {}
    if isinstance(value, dict):
        for part, level in value.items():
            word = str(level or "").strip().upper()
            if valid_part(part) and word in PART_LEVELS and len(found) < MAX_PARTS:
                found[part] = word
    return found


def part_level_for(name: str, levels: Dict[str, str]) -> Optional[str]:
    """The level of logger `name`: its own part, else the longest part that is a dotted prefix of it, else None."""
    if not levels:
        return None
    pieces = str(name).split(".")
    for end in range(len(pieces), 0, -1):
        level = levels.get(".".join(pieces[:end]))
        if level is not None:
            return level
    return None


def apply_part_levels(levels: Any) -> Dict[str, str]:
    """The process's levels per part (LoggingManager.apply_part_levels), e.g. from config.get(LOG_LEVELS_SETTING)."""
    global _logging_manager
    if _logging_manager is None:
        _logging_manager = setup_logging()
    return _logging_manager.apply_part_levels(levels)


def set_part_level(config: Any, part: str, level: Optional[str]) -> Dict[str, str]:
    """The person's one writer of the levels per part (`llmcf logs level`, Help > Framework Log...): `level` DEBUG to
    CRITICAL (any case) sets the part's level, None removes it. Checked (ValueError, nothing changes), saved to
    config.yml (OSError when it cannot be saved; the setting stays as it was) and applied in this process at once;
    another running process takes it at its next start. Returns the levels now."""
    if not valid_part(part):
        raise ValueError(f"'{part}' is no part of the framework: a logger name such as BuildEngine or "
                         "orchestrator.Core, as the framework's log names it.")
    levels = part_levels_of(config.get(LOG_LEVELS_SETTING, {}) if config is not None else {})
    before = dict(levels)
    if level is None:
        levels.pop(part, None)
    else:
        word = str(level).strip().upper()
        if word not in PART_LEVELS:
            raise ValueError(f"The level is one of {', '.join(PART_LEVELS)}, not '{level}'.")
        if part not in levels and len(levels) >= MAX_PARTS:
            raise ValueError(f"At most {MAX_PARTS} parts have their own level; reset one first.")
        levels[part] = word
    if config.set(LOG_LEVELS_SETTING, levels) is False:
        raise ValueError("The levels per part could not be set.")
    save = getattr(config, "save_user_config", None)
    if callable(save) and not save():
        config.set(LOG_LEVELS_SETTING, before)
        raise OSError("The levels per part could not be saved to config.yml; they stay as they were.")
    return apply_part_levels(levels)


def setup_logging(config_file: Optional[Path] = None,
                 level: Union[str, LogLevel] = LogLevel.INFO,
                 enable_memory_handler: bool = True,
                 debug_http: Optional[bool] = None) -> LoggingManager:
    """
    Setup framework logging system.

    Args:
        config_file: Optional configuration file
        level: Default logging level
        enable_memory_handler: Enable in-memory handler for GUI
        debug_http: Let the HTTP and provider SDK loggers log DEBUG (--debug-http); None keeps the last choice

    Returns:
        LoggingManager: Configured logging manager
    """
    global _logging_manager
    
    if _logging_manager is None:
        _logging_manager = LoggingManager()
    
    # Convert string level to enum
    if isinstance(level, str):
        level = LogLevel[level.upper()]
    
    if _logging_manager._initialized:
        # get_logger() auto-initializes with INFO, so later calls must still apply their level.
        _logging_manager.set_global_level(level)
    else:
        _logging_manager.initialize(
            config_file=config_file,
            default_level=level,
            enable_memory_handler=enable_memory_handler
        )

    # Credentials never reach a log; set_global_level() above also reset the SDK loggers.
    install_log_redaction()
    configure_sdk_loggers(level.value, debug_http)
    if _logging_manager.part_levels:   # the person's levels per part over the SDK loggers' defaults
        _logging_manager.apply_part_levels(_logging_manager.part_levels)
    return _logging_manager


def get_logger(name: str = "framework") -> logging.Logger:
    """
    Get framework logger instance.
    
    Args:
        name: Logger name (e.g., 'orchestrator.core.builder')
        
    Returns:
        logging.Logger: Configured logger with performance tracking
    """
    global _logging_manager
    
    if _logging_manager is None:
        # Auto-initialize with defaults
        _logging_manager = setup_logging()
    
    return _logging_manager.get_logger(name)


def get_recent_logs(count: int = 100) -> List[Dict[str, Any]]:
    """Get recent log entries from memory"""
    global _logging_manager
    
    if _logging_manager:
        return _logging_manager.get_recent_logs(count)
    return []


def get_logging_stats() -> Optional[LoggingStats]:
    """Get logging system statistics"""
    global _logging_manager
    
    if _logging_manager:
        return _logging_manager.get_logging_stats()
    return None


# ============================================================================
# DECORATORS: WHERE AN ERROR HAPPENED, HOW LONG A STEP TOOK (step 6 of the one log)
# ============================================================================

_LOGGED = "_llmcf_logged"     # set on an exception a step has logged: an outer step does not log it again


def _log_failure(logger: logging.Logger, part: str, step: str, error: BaseException) -> None:
    """One error entry with its trace, its part and its step, once per exception."""
    if getattr(error, _LOGGED, False):
        return
    try:
        setattr(error, _LOGGED, True)
    except Exception:  # noqa: BLE001 - an exception without attributes is logged each time
        pass
    logger.error(f"{step} failed: {error.__class__.__name__}: {error}",
                 exc_info=(type(error), error, error.__traceback__),
                 extra={"step": step, "part": part})


def _log_duration(logger: logging.Logger, part: str, step: str, duration_ms: float, threshold_ms: float,
                  failed: bool) -> None:
    if duration_ms >= threshold_ms:
        logger.log(LogLevel.PERFORMANCE.value, f"{step} took {duration_ms:.0f} ms" + (" and failed" if failed else ""),
                   extra={"step": step, "part": part, "duration_ms": round(duration_ms, 1)})


class log_step:
    """A step of the framework as a `with` block: its duration as one PERFORMANCE entry (`step`, `duration_ms`) when
    it took `threshold_ms` or longer (0: always), and an error it raises logged once with its trace, part and step
    (`errors=False`: the caller logs its errors itself, only the time is written). The error is always raised on;
    nothing of the step's arguments is written, only the part, the step and the time."""

    def __init__(self, part: str, step: str, threshold_ms: float = 0.0, errors: bool = True):
        self.part, self.step, self.threshold_ms, self.errors = part, step, float(threshold_ms), errors
        self.duration_ms: Optional[float] = None

    def __enter__(self) -> "log_step":
        self._began = time.perf_counter()
        return self

    def __exit__(self, kind, error, trace) -> bool:
        self.duration_ms = (time.perf_counter() - self._began) * 1000
        try:
            logger = get_logger(self.part)
            if error is not None and self.errors and isinstance(error, Exception):
                _log_failure(logger, self.part, self.step, error)
            _log_duration(logger, self.part, self.step, self.duration_ms, self.threshold_ms, error is not None)
        except Exception:  # noqa: BLE001 - logging never changes the step's outcome
            pass
        return False    # never swallows the error


def _wrap(func, part: Optional[str], step: Optional[str], threshold_ms: float, errors: bool):
    """`func` (plain or async) inside log_step, with its own name (functools.wraps)."""
    import asyncio
    import functools
    name = step or func.__qualname__
    where = part or func.__module__

    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            with log_step(where, name, threshold_ms, errors):
                return await func(*args, **kwargs)
        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with log_step(where, name, threshold_ms, errors):
            return func(*args, **kwargs)
    return wrapper


def log_performance(logger_name: Optional[str] = None, threshold_ms: float = 100.0, step: Optional[str] = None):
    """Decorator: the function's duration as a PERFORMANCE entry when it took `threshold_ms` or longer (the part is
    `logger_name`, by default the function's module; the step its name or `step`). Its errors are not logged here
    (log_exceptions does that); they are always raised on."""
    def decorator(func):
        return _wrap(func, logger_name, step, threshold_ms, errors=False)
    return decorator


def log_exceptions(logger_name: Optional[str] = None, step: Optional[str] = None, threshold_ms: float = float("inf")):
    """Decorator: an error of the function logged once with its trace, its part (`logger_name`, by default the
    function's module) and its step (its name or `step`), and always raised on (a decorator never swallows an
    error). `threshold_ms`: also its duration, when it took that long or longer (default: never)."""
    def decorator(func):
        return _wrap(func, logger_name, step, threshold_ms, errors=True)
    return decorator


# ============================================================================
# INITIALIZATION
# ============================================================================

# Auto-setup with minimal configuration when module is imported
if _logging_manager is None:
    setup_logging(level=LogLevel.INFO, enable_memory_handler=True)
