# © 2026 PocketKode · SPDX-License-Identifier: GPL-3.0-only
"""Runs inside the wt-compile container (muOS's Python 3.11): turns each module of the app's package (e.g.
wtransfer/files.py) into a native module (wtransfer/files.so, built as "wtransfer.files" so the package's own imports
keep working) and removes the .py source. Docstrings are dropped first, so no source text ends up in the libraries.
Kept as Python: main.py (the launcher muOS starts with python3) and each package's __init__.py.
Usage: python compile.py <app folder> <package> [<package> …]"""
import ast
import os
import shutil
import subprocess
import sys
import sysconfig


def strip_docstrings(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    open(path, "w", encoding="utf-8").write(ast.unparse(tree) + "\n")


app, packages = sys.argv[1], sys.argv[2:]
inc = sysconfig.get_paths()["include"]
work = "/tmp/cy"
for pkg in packages:
    d = os.path.join(app, pkg)
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        name = fn[:-3]
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(os.path.join(work, pkg))
        src = os.path.join(work, pkg, fn)
        shutil.copy(os.path.join(d, fn), src)
        strip_docstrings(src)
        c = os.path.join(work, pkg, name + ".c")
        subprocess.run(["cython", "-3", "--module-name", f"{pkg}.{name}",
                        "--directive", "embedsignature=False,emit_code_comments=False,binding=True",
                        "-o", c, src], check=True)
        so = os.path.join(d, name + ".so")
        subprocess.run(["gcc", "-shared", "-fPIC", "-O2", "-g0", "-fno-strict-aliasing", "-DNDEBUG",
                        f"-I{inc}", "-o", so, c], check=True)
        subprocess.run(["strip", "--strip-unneeded", so], check=True)
        os.remove(os.path.join(d, fn))
        print(f"  compiled {pkg}/{name}.so")
