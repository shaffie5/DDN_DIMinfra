import sys
import traceback

from pathlib import Path
from typing import Optional
from .config import logger, Config
from .file_manager import FileManager
from .excel_updater import ExcelUpdater


def standalone(source_workbook: str, target_workbook: str, mapping_file: Optional[str] = None) -> str:
    """
    Main standalone function to process Excel files.

    Args:
        source_workbook (str): Path to source Excel file
        target_workbook (str): Path to target Excel file
        mapping_file (Optional[str]): Path to mapping CSV file (optional)

    Returns:
        str: Success message

    Raises:
        FileNotFoundError: If required files are missing
        ValueError: If mapping file is invalid
        OSError: For file system errors
    """
    # Resolve file paths
    try:
        source_path = Path(source_workbook).resolve()
        target_path = Path(target_workbook).resolve()
        mapping_path = Path(mapping_file).resolve() if mapping_file else Config.MAPPING_FILE.resolve()
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Required file not found: {e}") from e
    except (OSError, RuntimeError) as e:
        raise ValueError(f"Invalid file path: {e}") from e

    # Validate all required files exist and are accessible
    logger.info(f"Processing: source={source_path}, target={target_path}, mapping={mapping_path}")

    for file_type, file_path in (("source", source_path), ("target", target_path), ("mapping", mapping_path)):
        if not FileManager.validate_file_path(file_path, file_type):
            raise FileNotFoundError(f"Missing or invalid {file_type} file: {file_path}")

    # Additional security check: prevent source and target from being the same
    if source_path == target_path:
        raise ValueError("Source and target files cannot be the same")

    # Load mappings with better error context
    try:
        mappings = FileManager.load_mapping_file(mapping_path)
        logger.info(f"Loaded {len(mappings)} valid mappings from {mapping_path}")
    except ValueError as ex:
        logger.error(f"Mapping file validation failed for {mapping_path}: {ex}")
        raise
    except Exception as ex:
        logger.error(f"Unexpected error loading mapping file {mapping_path}: {ex}")
        raise ValueError(f"Failed to load mapping file: {ex}") from ex

    # Validate we have mappings to process
    if not mappings:
        raise ValueError("No valid mappings found to process")

    # Update Excel files with better error handling and resource management
    try:
        logger.info(f"Starting Excel update process: {len(mappings)} mappings")
        ExcelUpdater.update_xlsx_in_place(source_path, target_path, mappings)

        # Verify the target file was modified and is still valid
        if not FileManager.validate_excel_file(target_path):
            logger.warning("Target file validation failed after update - file may be corrupted")
            # Don't raise here as the operation might have partially succeeded

        success_msg = f"Successfully processed {len(mappings)} mappings, updated target workbook: {target_path}"
        logger.info(success_msg)

        return success_msg
    except FileNotFoundError:
        raise
    except PermissionError as ex:
        logger.error(f"Permission denied during processing: {ex}")
        raise OSError(f"File access denied: {ex}") from ex
    except ValueError:
        raise
    except Exception as ex:
        logger.error(f"Unexpected error during Excel processing: {ex}")
        logger.debug(f"Exception details: {traceback.format_exc()}")
        raise RuntimeError(f"Excel processing failed: {ex}") from ex


def main() -> int:
    """Command-line interface with better error handling."""
    if len(sys.argv) not in (3, 4):
        print("Usage: python standalone.py <source.xlsx> <target.xlsx> [mapping.csv]")
        return 1

    source_file, target_file = sys.argv[1:3]
    mappings_file = sys.argv[3] if len(sys.argv) == 4 else None

    try:
        # Validate basic file existence early
        for file_path in [source_file, target_file]:
            if not Path(file_path).exists():
                print(f"Error: File not found: {file_path}")
                return 1

        result = standalone(source_file, target_file, mappings_file)
        print(result)
        return 0
    except FileNotFoundError as e:
        print(f"Error: File not found - {e}")
        return 1
    except ValueError as e:
        print(f"Error: Invalid input - {e}")
        return 1
    except PermissionError as e:
        print(f"Error: Permission denied - {e}")
        return 1
    except OSError as e:
        print(f"Error: File system error - {e}")
        return 1
    except Exception as e:
        print(f"Error: Unexpected error - {e}")
        logger.error(f"Standalone execution failed: {e}\n{traceback.format_exc()}")
        return 1


if __name__ == "__main__":
    try:
        # Validate configuration before starting
        config_valid, config_errors = Config.validate()
        if not config_valid:
            print(f"Configuration error: {', '.join(config_errors)}")
            sys.exit(1)

        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as exc:
        print(f"Fatal error: {exc}")
        sys.exit(1)
