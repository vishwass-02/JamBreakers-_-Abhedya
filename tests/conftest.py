"""Most tests drive the server through FastAPI's TestClient, whose client address is not the laptop's 127.0.0.1,
so sign-in is switched off for them. tests/test_auth.py switches it back on explicitly and tests it."""
import os
import tempfile

os.environ.setdefault("AEGIS_AUTH", "0")
os.environ.setdefault("AEGIS_USERS_FILE", os.path.join(tempfile.mkdtemp(prefix="abhedya_test_"), "users.json"))
