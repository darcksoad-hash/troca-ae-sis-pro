import os
import sys
import tempfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "outputs" / "troca-ae-sis-pro-app"
sys.path.insert(0, str(APP_DIR))

os.environ.setdefault("APP_ENV", "production")
os.environ.setdefault("APP_STORAGE_ROOT", str(Path(tempfile.gettempdir()) / "troca-ae-sis-pro"))

import server as server_module  # noqa: E402
from server import App, seed  # noqa: E402

VERCEL_PUBLIC = Path(__file__).resolve().parents[1] / "public"
if VERCEL_PUBLIC.exists():
    server_module.PUBLIC = VERCEL_PUBLIC

seed()


class handler(App):
    pass
