"""Las pruebas no deben escribir en los logs reales (logs/daemon.log, logs/web.log)."""
import os
import tempfile

os.environ.setdefault("TQA_LOG_DIR", tempfile.mkdtemp(prefix="tqa_test_logs_"))
