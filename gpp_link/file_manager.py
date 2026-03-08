import os
import csv
import shutil
import tempfile
import threading

from pathlib import Path
from typing import List, Dict
from zipfile import BadZipFile
from .config import Config, logger
from openpyxl import load_workbook
from datetime import datetime, timedelta
from xml.etree.ElementTree import ParseError
from openpyxl.utils.exceptions import InvalidFileException


class FileManager:
    """Manages file operations and temporary file cleanup."""

    # Use normalized resolved paths as cache keys to avoid mismatches
    _file_validation_cache: Dict[Path, bool] = {}
    _cache_lock = threading.RLock()  # Thread safety for web service

    @staticmethod
    def is_allowed_file(filename: str) -> bool:
        """Check if a file's extension is allowed."""
        if not filename:
            return False

        return Path(filename).suffix.lower() in Config.ALLOWED_EXTENSIONS

    @staticmethod
    def validate_file_size(file_path: Path) -> bool:
        """Validate file size against maximum limit."""
        try:
            if not file_path.exists():
                return False
            return file_path.stat().st_size <= Config.MAX_FILE_SIZE
        except OSError:
            return False

    @staticmethod
    def validate_excel_file(file_path: Path) -> bool:
        """Basic validation that file is a valid Excel file."""
        try:
            wb = load_workbook(file_path, data_only=True, read_only=True)
            wb.close()
            return True
        except (FileNotFoundError, PermissionError, InvalidFileException, BadZipFile, ParseError, KeyError, OSError) as e:
            logger.debug(f"validate_excel_file failed for {file_path}: {e}")
            return False

    @staticmethod
    def log_event(message: str, log_file: Path = Config.GLOBAL_LOG_FILE) -> None:
        """Log event to both console and file, with log rotation."""
        timestamp = datetime.now().isoformat(sep=' ', timespec='seconds')
        log_entry = f"[{timestamp}] {message}"

        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)

            with log_file.open("a", encoding="utf-8") as f:
                f.write(f"{log_entry}\n")

            logger.info(message)
        except (OSError, IOError) as e:
            logger.error(f"Failed to write log to {log_file}: {e}")

    @staticmethod
    def cleanup_old_temp_files(max_age_hours: int = Config.TEMP_FILE_MAX_AGE_HOURS) -> int:
        """Remove temporary files older than specified hours."""
        temp_dir = Path(tempfile.gettempdir())
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        removed_count = 0

        for file_path in temp_dir.glob(f"{Config.TEMP_PREFIX}*.xlsx"):
            try:
                if datetime.fromtimestamp(file_path.stat().st_mtime) < cutoff:
                    file_path.unlink(missing_ok=True)
                    removed_count += 1
            except (OSError, IOError, PermissionError) as e:
                logger.warning(f"Failed removing old temp file {file_path}: {e}")

        if removed_count:
            FileManager.log_event(f"Cleaned up {removed_count} old temp files")

        return removed_count

    @staticmethod
    def copy_file_safe(source: Path, destination: Path) -> bool:
        """Safely copy a file with error handling."""
        try:
            if not source.exists():
                logger.error(f"Source file does not exist: {source}")
                return False

            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

            return destination.exists()
        except (OSError, IOError, PermissionError, shutil.SameFileError) as e:
            logger.error(f"Failed to copy {source} to {destination}: {e}")
            return False

    @staticmethod
    def load_mapping_file(mapping_path: Path) -> List[Dict[str, str]]:
        """Load CSV mapping file and validate mandatory columns."""
        if not FileManager.validate_file_path(mapping_path, "mapping file"):
            raise ValueError(f"Mapping file not accessible: {mapping_path}")

        encodings = ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252']
        _used_encoding = 'unknown'
        rows = []

        for encoding in encodings:
            try:
                with mapping_path.open(newline="", encoding=encoding) as f:
                    reader = csv.DictReader(f)

                    # Normalize headers and row values (strip spaces)
                    if reader.fieldnames:
                        reader.fieldnames = [h.strip() for h in reader.fieldnames]

                    rows = []
                    for r in reader:
                        # normalize keys and values
                        normalized = { (k.strip() if k else ""): (v.strip() if isinstance(v, str) else v) for k, v in r.items() }
                        rows.append(normalized)
                if rows:
                    logger.info(f"Loaded mapping file with {len(rows)} rows using {encoding} encoding")
                    _used_encoding = encoding
                    break
            except UnicodeDecodeError:
                continue
            except (OSError, IOError, PermissionError) as ex:
                raise ValueError(f"Could not access mapping CSV file: {ex}") from ex
        else:
            raise ValueError(f"Cannot read mapping file with any supported encoding: {mapping_path}")

        if not rows:
            raise ValueError("Mapping CSV is empty or could not be read")

        # Validate mapping limit
        if not Config.validate_mapping_limit(rows):
            raise ValueError(f"Mapping file exceeds maximum allowed rows: {Config.MAX_MAPPING_ROWS}")

        required_columns = {"SourceCell", "TargetCell"}
        available_columns = set(rows[0].keys())
        missing_columns = required_columns - available_columns

        if missing_columns:
            raise ValueError(f"Mapping CSV missing required columns: {missing_columns}")

        valid_rows: List[Dict[str, str]] = []
        for i, row in enumerate(rows, 1):
            src = (row.get("SourceCell") or "").strip()
            tgt = (row.get("TargetCell") or "").strip()

            if src and tgt:
                # ensure all keys are present and stripped
                valid_rows.append({k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()})
            else:
                logger.warning(f"Row {i} skipped: missing SourceCell or TargetCell")

        if not valid_rows:
            raise ValueError("No valid mappings found in CSV after validation")

        logger.info(f"Validated {len(valid_rows)} mapping rows (encoding={_used_encoding})")
        return valid_rows

    @staticmethod
    def validate_file_path(file_path: Path, file_type: str = "file") -> bool:
        """Validate that a file exists and is accessible, with thread-safe caching."""
        # Normalize key (resolve) to avoid cache misses for equivalent paths
        try:
            resolved = file_path.resolve()
        except (OSError, RuntimeError) as e:
            logger.debug(f"Could not resolve path {file_path}: {e}")
            return False

        if not Config.ENABLE_CACHE:
            # Bypass caching if disabled
            return FileManager._validate_file_path_uncached(resolved, file_type)

        # Thread-safe cache access
        with FileManager._cache_lock:
            # Return cached result if available
            cached_result = FileManager._file_validation_cache.get(resolved)
            if cached_result is not None:
                return cached_result

            # Compute and cache result
            result = FileManager._validate_file_path_uncached(resolved, file_type)
            FileManager._file_validation_cache[resolved] = result
            return result

    @staticmethod
    def _validate_file_path_uncached(file_path: Path, file_type: str = "file") -> bool:
        """Validate file path without caching."""
        try:
            if not file_path.exists():
                logger.error(f"{file_type.capitalize()} does not exist: {file_path}")
                return False

            if not file_path.is_file():
                logger.error(f"{file_type.capitalize()} is not a file: {file_path}")
                return False

            if not os.access(file_path, os.R_OK):
                logger.error(f"Cannot read {file_type}: {file_path}")
                return False

            # Test file readability by opening
            with file_path.open('rb'):
                pass

            return True
        except (OSError, IOError, PermissionError) as ex:
            logger.error(f"Cannot access {file_type} {file_path}: {ex}")
            return False

    @classmethod
    def clear_cache(cls):
        """Clear file validation cache."""
        with cls._cache_lock:
            cls._file_validation_cache.clear()
