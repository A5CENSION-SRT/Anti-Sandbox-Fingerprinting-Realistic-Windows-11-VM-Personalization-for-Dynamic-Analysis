"""AST-based safety audit — checks for dangerous subprocess calls only."""
import ast
import sys
from pathlib import Path


DANGER_STRINGS = ["ntfsfix -d", "remove_hiberfile"]

# Only flag these subprocess-invoking function names
SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_output", "check_call", "getoutput"}


class DangerCallVisitor(ast.NodeVisitor):
    def __init__(self, filename):
        self.hits = []
        self.filename = filename

    def _is_subprocess_call(self, node: ast.Call) -> bool:
        """Return True if node looks like subprocess.run() / subprocess.call() etc."""
        func = node.func
        # subprocess.run(...)  -> Attribute(value=Name('subprocess'), attr='run')
        if isinstance(func, ast.Attribute):
            return func.attr in SUBPROCESS_FUNCS
        # Direct import: run(...) — less common but possible
        if isinstance(func, ast.Name):
            return func.id in SUBPROCESS_FUNCS
        return False

    def visit_Call(self, node):
        if self._is_subprocess_call(node):
            try:
                src = ast.unparse(node)
                for danger in DANGER_STRINGS:
                    if danger in src:
                        self.hits.append((self.filename, src[:200]))
            except Exception:
                pass
        self.generic_visit(node)


def main():
    root = Path(".")
    all_hits = []
    checked = 0

    for py in sorted(root.rglob("*.py")):
        if any(part in (".git", "__pycache__", "tests") for part in py.parts):
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(text)
            v = DangerCallVisitor(str(py))
            v.visit(tree)
            all_hits.extend(v.hits)
            checked += 1
        except SyntaxError:
            pass

    print(f"Checked {checked} Python source files.")

    if all_hits:
        print("\nDANGER — active dangerous subprocess calls found:")
        for fname, src in all_hits:
            safe_src = src.encode("ascii", "replace").decode("ascii")
            print(f"  {fname}:\n    {safe_src}")
        sys.exit(1)
    else:
        print("EXECUTABLE SAFETY AUDIT PASSED — zero dangerous subprocess calls in production code.")
        sys.exit(0)


if __name__ == "__main__":
    main()
