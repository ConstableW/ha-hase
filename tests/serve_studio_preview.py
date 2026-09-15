"""Loopback-only UI fixtures; no HA instance or real device calls."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def translate_path(self, path):
        aliases = {
            "/config/integrations/integration/hase": "/tests/studio-entry-preview.html",
            "/hase-studio-brand/icon.png": "/custom_components/hase/brand/icon.png",
        }
        return super().translate_path(aliases.get(path.split("?", 1)[0], path))


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), PreviewHandler).serve_forever()
