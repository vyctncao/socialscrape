import shutil
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).parents[1]

@pytest.mark.skipif(not shutil.which("node") or not (ROOT / "cli/node_modules").exists(), reason="Node/npm dependencies unavailable")
def test_cli_help_smoke():
    result = subprocess.run(["node", "cli/bin/socialscrape.js", "--help"], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "Usage: socialscrape" in result.stdout
    assert "youtube" in result.stdout
