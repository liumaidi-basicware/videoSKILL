#!/usr/bin/env python3
"""Regression guard: business modules must not use function-level imports
for core project modules that have no circular dependency.

Context: video_engine.py historically had 10+ inline `import run_manifest`
statements inside function bodies, which served no circular-dependency
purpose (run_manifest does not import video_engine) and obscured the
real dependency graph. After the v2 cleanup, all such imports were hoisted
to module top level. This test prevents reintroduction.

Exemptions:
  - Lazy imports that are genuinely needed for circular dependency avoidance
    are allowed but must be registered in LAZY_IMPORT_EXEMPTIONS below,
    with a comment explaining the cycle.
  - Standard library modules used only in rare code paths (e.g. pyobjc in
    ocr_check) may stay lazy but should use an explicit getter function.

Run: python3 tests/test_no_inline_imports.py
"""
import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(os.path.dirname(HERE), "scripts")

# Core project modules that should always be imported at module top level
# in business scripts. If any of these appear inside a function body,
# it's almost certainly a stale inline import that should be hoisted.
CORE_MODULES = {
    "run_manifest",
    "generation_ledger",
    "artifact_contract",
    "br_client",
    "take_review",
    "script_splitter",
    "media_qc",
    "cost_ledger",
    "aspect_ratio",
    "proc_utils",
    "project_utils",
    "agent_runtime",
}

# Modules allowed to have lazy imports (with reason)
LAZY_IMPORT_EXEMPTIONS = {
    # ocr_check imports pyobjc (Vision framework) which is macOS-only and
    # expensive to import on non-Mac. Lazy import is intentional.
    "ocr_check.py": {"Vision", "Quartz", "CoreFoundation", "AppKit"},
    # remotion_engine may defer node/npm discovery to runtime
    "remotion_engine.py": {"node", "npm"},
    # hf_engine may defer npx discovery to runtime
    "hf_engine.py": {"npx"},
    # matte.py may defer heavy img2img client to runtime
    "matte.py": {"br_client"},
    # run_manifest ↔ generation_ledger have a genuine circular dependency.
    # run_manifest needs generation_ledger for reconcile; generation_ledger
    # needs run_manifest for manifest lookups. The lower-level module
    # (generation_ledger) keeps the lazy import to break the cycle.
    "run_manifest.py": {"generation_ledger"},
    "generation_ledger.py": {"run_manifest"},
}


class InlineImportVisitor(ast.NodeVisitor):
    """Collect all function-level import statements."""

    def __init__(self):
        self.inline_imports = []  # list of (lineno, module_name, func_name)

    def _check_import(self, node, names, in_function):
        if not in_function:
            return
        for alias in names:
            name = alias.name
            # Handle "from X import Y" and "import X"
            top = name.split(".")[0]
            self.inline_imports.append((node.lineno, top, name))

    def visit_FunctionDef(self, node):
        # Mark that we're inside a function, then visit children
        for child in ast.walk(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                names = child.names
                self._check_import(child, names, in_function=True)
        # Don't recurse with generic_visit — ast.walk already covered it
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef


def _find_inline_imports(filepath):
    """Return list of (lineno, module, full_spec) for function-level imports."""
    with open(filepath, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=filepath)

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        top = alias.name.split(".")[0]
                        violations.append((child.lineno, top, alias.name))
                elif isinstance(child, ast.ImportFrom):
                    if child.module:
                        top = child.module.split(".")[0]
                        for alias in child.names:
                            violations.append((child.lineno, top,
                                               "%s.%s" % (child.module, alias.name)))
                    else:  # relative import "from . import X"
                        for alias in child.names:
                            violations.append((child.lineno, alias.name, alias.name))
    return violations


class TestNoInlineCoreImports(unittest.TestCase):

    def test_business_scripts_no_inline_core_imports(self):
        """Core modules must not be imported inside function bodies."""
        offenders = []

        for filename in sorted(os.listdir(SCRIPTS_DIR)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue

            filepath = os.path.join(SCRIPTS_DIR, filename)
            exemptions = LAZY_IMPORT_EXEMPTIONS.get(filename, set())

            violations = _find_inline_imports(filepath)
            for lineno, top_module, full_spec in violations:
                if top_module in CORE_MODULES and top_module not in exemptions:
                    offenders.append("%s:%d — inline import of %s"
                                     % (filename, lineno, full_spec))

        if offenders:
            self.fail(
                "Found %d function-level import(s) of core modules that "
                "should be hoisted to module top level:\n  %s\n\n"
                "These inline imports serve no circular-dependency purpose "
                "(verified: run_manifest does not import video_engine). "
                "Hoist them to the module's top-level import block."
                % (len(offenders), "\n  ".join(offenders)))

    def test_exemptions_are_still_valid(self):
        """Exempted modules should still exist in scripts/."""
        for filename in LAZY_IMPORT_EXEMPTIONS:
            path = os.path.join(SCRIPTS_DIR, filename)
            self.assertTrue(os.path.isfile(path),
                            "Exemption references non-existent file: %s" % filename)


if __name__ == "__main__":
    unittest.main(verbosity=2)
