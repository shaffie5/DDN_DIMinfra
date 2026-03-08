import re
import sys
import json
import shutil
import tempfile

from pathlib import Path
from datetime import datetime
from standalone import standalone
from config import Config, logger
from file_manager import FileManager
from contextlib import contextmanager
from werkzeug.utils import secure_filename
from typing import Iterator, Optional, Tuple
from flask import Flask, request, send_file, render_template_string, Response


app = Flask(__name__)
app.config.from_object(Config)

# ---------------------------
# HTML/CSS upload form layout
# ---------------------------
UPLOAD_FORM = """<!doctype html> 
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>PIONEERS GPP Tool - Upload</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      background-color: #f8f9fa;
      color: #333;
      margin: 0;
      padding: 40px;
    }
    .container {
      background: #fff;
      border-radius: 12px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.1);
      padding: 30px 40px;
      max-width: 650px;     /* wider form */
      width: 100%;
    }
    .header {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      margin-bottom: 20px;
    }
    .logo {
      height: 50px;
      margin-bottom: 10px;
    }
    h2 {
      font-size: 1.1em;
      font-weight: 600;
      color: #004080;
      margin: 0;
    }
    form {
      display: flex;
      flex-direction: column;
      gap: 15px;
    }

    /* NEW: improved control row */
    .row {
      display: flex;
      flex-direction: row;
      gap: 12px;
      align-items: center;
      width: 100%;
    }
    .row select {
      flex: 0.3;          /* shrink dropdown to ≈30% */
      min-width: 100px;
      max-width: 150px;   /* prevents dropdown from growing too large */
    }
    .row input[type="file"] {
      flex: 1.7;          /* grow file selector significantly */
    }

    input[type="file"], input[type="submit"], select {
      border-radius: 6px;
      padding: 8px;
    }
    input[type="file"], select {
      border: 1px solid #ccc;
      cursor: pointer;
    }
    input[type="submit"] {
      background-color: #004080;
      color: #fff;
      border: none;
      font-weight: bold;
      cursor: pointer;
      transition: 0.3s;
    }
    input[type="submit"]:hover {
      background-color: #0059b3;
    }
    p {
      font-size: 0.9em;
      color: #666;
      margin-top: 15px;
      text-align: center;
    }
    .error {
      color: #d32f2f;
      background: #ffebee;
      padding: 10px;
      border-radius: 4px;
      margin-top: 15px;
      display: none;
    }
    .success {
      color: #2e7d32;
      background: #e8f5e9;
      padding: 10px;
      border-radius: 4px;
      margin-top: 15px;
      display: none;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <img src="/static/logo.png" alt="Team Logo" class="logo">
      <h2>PIONEERS GPP Tool - Upload Source File</h2>
    </div>

    <form method="post" enctype="multipart/form-data" onsubmit="return validateForm()">

      <div class="row">
        <select name="mapping" id="mappingSelect" required>
          {% for key in mappings %}
            <option value="{{ key }}">{{ key }}</option>
          {% endfor %}
        </select>

        <input type="file" name="file" accept=".xlsx,.xls" required id="fileInput">
      </div>

      <input type="submit" value="Process">
    </form>

    <div class="error" id="errorMessage"></div>
    <div class="success" id="successMessage"></div>
    <p>Maximum file size: 50MB - Allowed formats: .xlsx, .xls</p>
  </div>

  <script>
    const errorDiv = document.getElementById('errorMessage');
    const successDiv = document.getElementById('successMessage');
    const fileInput = document.getElementById('fileInput');

    function validateForm() {
      const file = fileInput.files[0];
      if (!file) return showError('Please select a file.');
      if (!['.xlsx', '.xls'].some(ext => file.name.toLowerCase().endsWith(ext)))
        return showError('Please select an Excel file (.xlsx or .xls).');
      if (file.size > 50 * 1024 * 1024) return showError('File size exceeds 50MB limit.');
      hideMessages();
      return true;
    }

    function showError(message) {
      errorDiv.textContent = message;
      errorDiv.style.display = 'block';
      successDiv.style.display = 'none';
      return false;
    }

    function hideMessages() {
      errorDiv.style.display = 'none';
      successDiv.style.display = 'none';
    }

    fileInput.addEventListener('change', hideMessages);
  </script>
</body>
</html>"""


@contextmanager
def session_logger(session_id: str) -> Iterator[Path]:
    """Context manager for per-session logging with automatic merging to global log."""
    with tempfile.TemporaryDirectory() as tmpdir_raw:
        local_log = Path(tmpdir_raw) / f"upload_{session_id}.log"
        FileManager.log_event(f"=== New session {session_id} ===", local_log)

        try:
            yield local_log
        except Exception as e:
            FileManager.log_event(f"Session error: {e}", local_log)
            raise
        finally:
            if local_log.exists():
                try:
                    # Read local file and append to global log
                    with local_log.open("r", encoding="utf-8") as src, \
                            Config.GLOBAL_LOG_FILE.open("a", encoding="utf-8") as dest:
                        dest.write(f"\n--- Session {session_id} ---\n")
                        shutil.copyfileobj(src, dest)
                        dest.write("--- End Session ---\n\n")
                except (OSError, IOError) as e:
                    logger.error(f"Failed merging session log for {session_id}: {e}")


def generate_download_filename() -> str:
    """Return a unique filename for download based on the template."""
    timestamp = datetime.now().strftime("%H%M%S_%f")[:-3]
    template_name = re.sub(r"_\d{8}$", "", Config.TARGET_TEMPLATE.stem).replace(" ", "_")
    return f"{template_name}_{timestamp}.xlsx"


def handle_upload_error(message: str, status_code: int = 400,
                        local_log: Optional[Path] = None) -> Tuple[str, int]:
    """Return a formatted error message and log the error."""
    if local_log and local_log.exists():
        FileManager.log_event(f"Upload error ({status_code}): {message}", local_log)
    else:
        logger.error(f"Upload error ({status_code}): {message}")

    return f"<h3>Error</h3><pre>{message}</pre><p>Please try again or contact support.</p>", status_code


def validate_uploaded_file(file_path: Path) -> Tuple[bool, str]:
    """Validate uploaded file before processing."""
    if not FileManager.validate_file_size(file_path):
        try:
            size = file_path.stat().st_size
        except OSError:
            size = "unknown"
        return False, f"File too large: {size} bytes (max: {Config.MAX_FILE_SIZE})"

    if not FileManager.validate_excel_file(file_path):
        return False, "File is not a valid Excel file or is corrupted"

    return True, ""


def cleanup_directories(download_dir: Path, processing_dir: Path) -> None:
    """
    Clean up both download and processing directories.

    Args:
        download_dir: Directory containing download files
        processing_dir: Directory containing processing temporary files
    """
    # Clean up download directory
    try:
        if download_dir.exists():
            shutil.rmtree(download_dir, ignore_errors=True)
            logger.debug(f"Cleaned up download directory: {download_dir}")
    except OSError as exc:
        logger.warning(f"Failed to clean up download directory {download_dir}: {exc}")

    # Clean up processing temporary files
    try:
        for temp_file in processing_dir.glob("*"):
            try:
                if temp_file.is_file():
                    temp_file.unlink(missing_ok=True)
            except OSError:
                pass  # Ignore individual file cleanup errors
    except OSError as exc:
        logger.debug(f"Failed to clean up some temporary files in {processing_dir}: {exc}")


@app.route("/", methods=["GET", "POST"])
def upload_file() -> str | Response | Tuple[str, int]:
    """Handle Excel file upload, processing, and download."""
    if request.method == "GET":
        return render_template_string(UPLOAD_FORM, mappings=Config.MAPPING_FILES.keys())

    # Clean up old temp files before processing
    removed_count = FileManager.cleanup_old_temp_files()
    if removed_count > 0:
        logger.info(f"Cleaned up {removed_count} old temporary files")

    # Validate server configuration
    config_valid, config_errors = Config.validate()
    if not config_valid:
        return handle_upload_error(f"Server configuration error: {', '.join(config_errors)}", 500)

    uploaded_file = request.files.get("file")
    if not uploaded_file or uploaded_file.filename == '':
        return handle_upload_error("No file selected.")

    if not FileManager.is_allowed_file(uploaded_file.filename):
        return handle_upload_error("Invalid file type. Only Excel files (.xlsx, .xls) allowed.")

    # Read mapping selection
    selected_mapping = request.form.get("mapping")
    if not selected_mapping:
        return handle_upload_error("No mapping selected.")

    mapping_path_real = Config.get_mapping_file(selected_mapping)

    if not mapping_path_real.exists():
        return handle_upload_error(f"Selected mapping file not found: {selected_mapping}")

    safe_name = secure_filename(uploaded_file.filename) or f"uploaded_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    with session_logger(session_id) as local_log, tempfile.TemporaryDirectory() as tmpdir_raw:
        tmpdir = Path(tmpdir_raw)
        source_path = tmpdir / safe_name

        try:
            # Save uploaded file
            uploaded_file.save(source_path)
            FileManager.log_event(f"Uploaded file saved: {safe_name} ({source_path.stat().st_size} bytes)", local_log)

            # Validate the uploaded file
            is_valid, validation_msg = validate_uploaded_file(source_path)
            if not is_valid:
                return handle_upload_error(validation_msg, 400, local_log)
        except (OSError, IOError, ValueError) as e:
            return handle_upload_error(f"Failed to process uploaded file: {e}", 400, local_log)

        # Prepare processing files (copies into this session's tmpdir)
        target_path = tmpdir / Config.TARGET_TEMPLATE.name
        mapping_copy = tmpdir / mapping_path_real.name

        if not all([
            FileManager.copy_file_safe(Config.TARGET_TEMPLATE, target_path),
            FileManager.copy_file_safe(mapping_path_real, mapping_copy)
        ]):
            return handle_upload_error("Server error: Failed to prepare processing files", 500, local_log)

        FileManager.log_event("Processing files prepared successfully", local_log)

        # Run processing
        try:
            result_msg = standalone(str(source_path), str(target_path), str(mapping_copy))
            FileManager.log_event(f"Successfully processed: {safe_name}", local_log)
            FileManager.log_event(result_msg, local_log)
        except FileNotFoundError as e:
            user_msg = f"Required file not found: {e}"
            return handle_upload_error(user_msg, 400, local_log)
        except ValueError as e:
            user_msg = f"Invalid input data: {e}"
            return handle_upload_error(user_msg, 400, local_log)
        except PermissionError:
            user_msg = "File access denied. Please check file permissions."
            return handle_upload_error(user_msg, 500, local_log)
        except (OSError, IOError) as e:
            user_msg = str(e)
            if any(term in user_msg.lower() for term in ["corrupt", "invalid", "badzip"]):
                user_msg = "The file appears to be corrupt or invalid."
            return handle_upload_error(f"Processing failed: {user_msg}", 500, local_log)
        except Exception as e:
            logger.error(f"Unexpected error during processing: {e}", exc_info=True)
            return handle_upload_error(f"Unexpected error during processing: {e}", 500, local_log)

        # Prepare download: create a dedicated download directory to avoid collisions
        download_filename = generate_download_filename()
        download_dir = Path(tempfile.mkdtemp(prefix=f"download_{session_id}_"))
        download_copy = download_dir / download_filename

        if not FileManager.copy_file_safe(target_path, download_copy):
            # attempt cleanup of directory
            try:
                if download_dir.exists():
                    shutil.rmtree(download_dir, ignore_errors=True)
            except OSError:
                pass

            return handle_upload_error("Failed to prepare download file", 500, local_log)

        FileManager.log_event(f"Download ready: {download_filename}", local_log)

        try:
            response = send_file(
                download_copy,
                as_attachment=True,
                download_name=download_filename,
                max_age=0
            )

            # Remove the download directory when the response is closed
            @response.call_on_close
            def cleanup_download_file():
                cleanup_directories(download_dir, tmpdir)

            return response
        except Exception as e:
            # Clean up download file on error
            cleanup_directories(download_dir, tmpdir)
            return handle_upload_error(f"Failed to send file: {e}", 500, local_log)


@app.route("/health", methods=["GET"])
def health_check() -> Tuple[str, int]:
    """Return a simple health status for monitoring."""
    try:
        files_ok, missing = Config.validate()

        if not files_ok:
            return f"UNHEALTHY: Missing files: {', '.join(missing)}", 503

        disk_free_gb = shutil.disk_usage(Config.BASE_DIR).free / (1024 ** 3)
        if disk_free_gb < 1:
            return f"UNHEALTHY: Low disk space: {disk_free_gb:.1f}GB free", 503

        # Check if we can write to temp directory
        try:
            with tempfile.NamedTemporaryFile(delete=True) as tmp:
                tmp.write(b"test")
        except OSError:
            return "UNHEALTHY: Cannot write to temporary directory", 503

        return "HEALTHY", 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return f"UNHEALTHY: {e}", 503


@app.route("/status", methods=["GET"])
def status_check() -> Tuple[str, int]:
    """Return detailed status information."""
    try:
        status_info = {
            "service": "PIONEERS GPP Tool",
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "disk_free_gb": round(shutil.disk_usage(Config.BASE_DIR).free / (1024 ** 3), 1),
            "config_valid": Config.validate()[0],
        }

        return json.dumps(status_info, indent=2), 200
    except Exception as e:
        return f'{{"status": "unhealthy", "error": "{e}"}}', 503


@app.after_request
def cleanup_after_request(response):
    """Clear caches after each request to prevent memory leaks."""
    FileManager.clear_cache()
    return response


@app.errorhandler(413)
def too_large(e):
    """Handle file too large errors."""
    logger.error(f"File too large error: {e}")
    return handle_upload_error("File too large. Maximum size is 50MB.")


@app.errorhandler(500)
def internal_server_error(e):
    """Handle internal server errors."""
    logger.error(f"Internal server error: {e}")
    return handle_upload_error("Internal server error. Please try again later.", 500)


if __name__ == "__main__":
    cfg_valid, cfg_errors = Config.validate()

    if not cfg_valid:
        logger.error(f"Configuration validation failed: {cfg_errors}")
        sys.exit(1)

    logger.info("Starting PIONEERS GPP Tool webservice")

    # Add startup health check
    try:
        disk_free_size = shutil.disk_usage(Config.BASE_DIR).free / (1024 ** 3)
        logger.info(f"Disk space: {disk_free_size:.1f}GB free")
        logger.info(f"Template file: {Config.TARGET_TEMPLATE}")
        logger.info(f"Mapping file: {Config.MAPPING_FILE}")
    except Exception as ex:
        logger.warning(f"Startup check warning: {ex}")

    app.run(debug=False, host='127.0.0.1', port=5000)
