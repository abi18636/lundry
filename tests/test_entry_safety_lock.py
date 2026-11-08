"""Structural regression checks for the temporary entry lock."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_entry_lock_before_entry_post():
    tree = ast.parse((ROOT / 'app/engine.py').read_text())
    loop = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'run_cycle_async')
    asset_loop = next(n for n in ast.walk(loop) if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == 'asset' and any(isinstance(v, ast.Constant) and isinstance(v.value, str) and 'ENTRY LOCKED:' in v.value for v in ast.walk(n)))
    positions = [n for n in ast.walk(asset_loop) if isinstance(n, ast.Constant) and isinstance(n.value, str) and 'ENTRY LOCKED:' in n.value]
    assert len(positions) == 1
    guard_line = positions[0].lineno
    assert any(isinstance(n, ast.Continue) and n.lineno == guard_line + 1 for n in ast.walk(asset_loop))
    # The stop/exit order path remains earlier, the entry post is later.
    posts = sorted(n.lineno for n in ast.walk(asset_loop) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'post')
    assert len(posts) == 2 and posts[0] < guard_line < posts[1]

def test_health_marks_trading_not_ready():
    main = (ROOT / 'app/main.py').read_text()
    assert '"trading_ready": False' in main and '"entry_safety_lock": True' in main
