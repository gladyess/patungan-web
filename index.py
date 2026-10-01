import importlib.util
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
BACKEND_PATH = PROJECT_DIR / "Web Patungan.py"
spec = importlib.util.spec_from_file_location("patungan_backend", BACKEND_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"Backend tidak ditemukan: {BACKEND_PATH}")

backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)
app = backend.app