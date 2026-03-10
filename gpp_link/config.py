import os
import logging
import tempfile

from pathlib import Path
from typing import List, Tuple
from logging.handlers import RotatingFileHandler


class Config:
    """Global configuration settings for the PIONEERS GPP tool."""

    BASE_DIR: Path = Path(__file__).parent.resolve()
    TARGET_TEMPLATE: Path = BASE_DIR / "PIONEERS GPP TOOL_20260310.xlsx"
    GLOBAL_LOG_FILE: Path = BASE_DIR / "upload_log.txt"

    # -------------------------------
    # Generic mapping registry
    # -------------------------------
    MAPPING_FILES: dict[str, Path] = {
        "VN": BASE_DIR / "mapping_vn.csv",
        "DDN": BASE_DIR / "mapping_ddn.csv",
    }

    # default fallback
    MAPPING_FILE: Path = MAPPING_FILES["VN"]

    TEMP_FILE_MAX_AGE_HOURS: int = 24
    ALLOWED_EXTENSIONS: set[str] = {".xlsx", ".xls"}
    MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MB
    TEMP_PREFIX: str = "PIONEERS_GPP_TOOL_"

    MAX_MAPPING_ROWS: int = 10000
    ENABLE_CACHE: bool = True

    @classmethod
    def get_mapping_file(cls, key: str) -> Path:
        """Return mapping file path for a selected key."""
        return cls.MAPPING_FILES.get(key, cls.MAPPING_FILE)

    @classmethod
    def _validate_file(cls, errors: list[str], label: str, path: Path) -> None:
        if not path.exists():
            errors.append(f"{label} not found: {path}")
        elif not path.is_file() or not os.access(path, os.R_OK):
            errors.append(f"Cannot read {label} at: {path}")

    @classmethod
    def validate(cls) -> Tuple[bool, List[str]]:
        """
        Validate configuration paths and basic setup.

        Returns:
            A tuple (is_valid, errors), where:
                - is_valid: bool indicating overall configuration validity.
                - errors: list of strings describing missing or invalid files.
        """
        errors: List[str] = []

        # File existence checks
        if not cls.BASE_DIR.exists():
            errors.append(f"Base directory does not exist: {cls.BASE_DIR}")

        # Check template and mapping files existence/readability
        cls._validate_file(errors, "Target template", cls.TARGET_TEMPLATE)

        # Validate all mapping files
        for key, path in cls.MAPPING_FILES.items():
            cls._validate_file(errors, f"Mapping file '{key}'", path)

        # Directory permissions: ensure we can create/write to log directory
        log_dir = cls.GLOBAL_LOG_FILE.parent

        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            # Try to create and write a temporary file in the log directory to validate write permissions
            with tempfile.NamedTemporaryFile(dir=str(log_dir), delete=True) as tmp:
                tmp.write(b"test")
        except OSError as e:
            errors.append(f"Unable to create or write to log directory ({log_dir}): {e}")

        # Configuration value validation
        if cls.MAX_FILE_SIZE <= 0:
            errors.append("MAX_FILE_SIZE must be positive")

        if cls.TEMP_FILE_MAX_AGE_HOURS < 1:
            errors.append("TEMP_FILE_MAX_AGE_HOURS must be at least 1")

        if cls.MAX_MAPPING_ROWS <= 0:
            errors.append("MAX_MAPPING_ROWS must be positive")

        return len(errors) == 0, errors

    @classmethod
    def validate_mapping_limit(cls, mappings: List[dict]) -> bool:
        """Prevent processing excessively large mapping files."""
        return len(mappings) <= cls.MAX_MAPPING_ROWS


def _setup_logging() -> logging.Logger:
    """Enhanced logging with rotation and different levels."""
    _logger = logging.getLogger(__name__)

    # If handlers already exist we avoid adding duplicates
    if _logger.hasHandlers():
        return _logger

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)-8s] %(name)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File handler with rotation
    file_handler = RotatingFileHandler(
        Config.GLOBAL_LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    _logger.setLevel(logging.INFO)
    _logger.addHandler(file_handler)
    _logger.addHandler(console_handler)

    # Reduce verbosity from third-party libraries
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('openpyxl').setLevel(logging.WARNING)

    return _logger


# Initialize logger when imported
logger = _setup_logging()
