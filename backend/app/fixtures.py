import json
from functools import lru_cache

from .core import ROOT, digest

DIRECTORY = ROOT / "fixtures"


@lru_cache(maxsize=1)
def manifest():
    return json.loads((DIRECTORY / "manifest.json").read_text(encoding="utf-8"))


def checked_fixture(filename):
    content = (DIRECTORY / filename).read_bytes()
    if manifest()["files"].get(filename) != digest(content):
        raise ValueError(f"Fixture checksum mismatch: {filename}")
    return json.loads(content)
