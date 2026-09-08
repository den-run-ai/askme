"""Pinned-runtime discovery must never mix historical and current checkouts."""

from pathlib import Path

import pytest
from featurebench.canary_audit import runtime_source_paths


def test_historical_monolith_does_not_borrow_current_modules(tmp_path):
    source = tmp_path / "historical.py"
    source.write_text("import json\n")
    assert runtime_source_paths(source) == {"askme.py": source}


@pytest.mark.parametrize("module", ["actions", "llm", "policies", "loop"])
def test_present_runtime_siblings_are_pinned_even_without_direct_import(tmp_path, module):
    source = tmp_path / "askme.py"
    source.write_text("pass\n")
    dependency = tmp_path / f"{module}.py"
    dependency.write_text("PINNED = True\n")
    assert runtime_source_paths(source) == {"askme.py": source, dependency.name: dependency}


@pytest.mark.parametrize("module", ["actions", "llm", "policies", "loop"])
def test_missing_runtime_import_never_falls_back_to_adapter_checkout(tmp_path, module):
    source = tmp_path / "askme.py"
    source.write_text(f"from {module} import Something\n")
    with pytest.raises(FileNotFoundError, match=rf"{module}\.py"):
        runtime_source_paths(source)


def test_dependency_imports_are_checked_recursively_without_executing_source(tmp_path):
    source = tmp_path / "askme.py"
    source.write_text("import llm\nraise AssertionError('never execute discovery')\n")
    (tmp_path / "llm.py").write_text("from actions import ActionEnvelope\n")
    with pytest.raises(FileNotFoundError, match="actions.py"):
        runtime_source_paths(source)
    (tmp_path / "actions.py").write_text("import json\n")
    assert set(runtime_source_paths(source)) == {"askme.py", "llm.py", "actions.py"}


@pytest.mark.parametrize("module", ["askme", "actions", "llm", "policies", "loop"])
def test_runtime_symlinks_are_rejected(tmp_path, module):
    source = tmp_path / "askme.py"
    source.write_text("pass\n")
    target = tmp_path / "outside.py"
    target.write_text("pass\n")
    dependency = tmp_path / f"{module}.py"
    if module == "askme":
        dependency.unlink()
    dependency.symlink_to(target)
    with pytest.raises(FileNotFoundError, match="symlink"):
        runtime_source_paths(source)


@pytest.mark.parametrize(
    "statement",
    ["from ..llm import Client", "from . import llm", "import llm.transport"],
)
def test_runtime_dependencies_must_be_plain_siblings(tmp_path, statement):
    source = tmp_path / "askme.py"
    source.write_text(statement + "\n")
    (tmp_path / "llm.py").write_text("pass\n")
    with pytest.raises(ValueError, match="sibling"):
        runtime_source_paths(source)


def test_unregistered_sibling_import_is_not_silently_left_out(tmp_path):
    source = tmp_path / "askme.py"
    source.write_text("import unregistered_runtime\n")
    (tmp_path / "unregistered_runtime.py").write_text("pass\n")
    with pytest.raises(ValueError, match="Unsupported runtime dependency"):
        runtime_source_paths(source)


def test_current_runtime_inventory_and_imports_have_no_cycles():
    import ast

    root = Path(__file__).resolve().parents[1]
    paths = runtime_source_paths(root / "askme.py")
    assert set(paths) == {path.name for path in root.glob("*.py")}
    edges = {}
    for name, path in paths.items():
        imports = set()
        for node in ast.walk(ast.parse(path.read_bytes())):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        edges[name] = {module + ".py" for module in imports if module + ".py" in paths}

    def visit(name, ancestors):
        assert name not in ancestors, f"Runtime import cycle: {(*ancestors, name)}"
        for dependency in edges[name]:
            visit(dependency, (*ancestors, name))

    for name in paths:
        visit(name, ())
