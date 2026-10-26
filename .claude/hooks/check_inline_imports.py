#!/usr/bin/env python3
"""擋下 inline import：寫在函式或 class 裡的 import。

規則：import 一律放在模組頂層。

    def _startup(state):
        from app.containers import Container   NG

inline import 幾乎都是拿來繞過循環 import，但那只是把問題藏起來 —— 依賴方向
錯了的模組還是錯的。這個 codebase 已經發生過一次：_broker.py 在 worker 啟動
函式裡 import Container，因為 Container -> tasks -> broker 已經構成一圈。
正解是把建立 Container 的職責移到入口（app/workers/main.py），讓 broker 待在
依賴圖的底層，而不是把 import 往函式裡塞。

模組頂層的 `if TYPE_CHECKING:` 與 `try: ... except ImportError:` 仍算頂層，不擋。

用 AST 而不是 regex：縮排後的 import 可能在 if / try / with 底下，字串比對分不出
它的外層是函式還是模組。

退出碼 2 會把訊息回饋給 Claude 要求修正；其餘情況一律 0，不擋任何事。
"""
import ast
import json
import pathlib
import sys

SKIP_DIRS = {".venv", "migrations", "__pycache__", "node_modules", ".git"}

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _import_text(node: ast.Import | ast.ImportFrom) -> str:
    names = ", ".join(a.name + (f" as {a.asname}" if a.asname else "") for a in node.names)
    if isinstance(node, ast.ImportFrom):
        return f"from {'.' * node.level}{node.module or ''} import {names}"
    return f"import {names}"


def find_violations(source: str) -> list[tuple[int, str, str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # 編輯到一半的檔案不是我們要管的事

    violations = []

    def visit(node, scope: str | None):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)) and scope:
                violations.append((child.lineno, scope, _import_text(child)))
            child_scope = scope
            if isinstance(child, SCOPES):
                child_scope = getattr(child, "name", "<lambda>")
            visit(child, child_scope)

    visit(tree, None)
    return violations


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_input = payload.get("tool_input") or {}
    raw_path = (payload.get("tool_response") or {}).get("filePath") or tool_input.get("file_path")
    if not raw_path:
        return 0

    path = pathlib.Path(raw_path)
    if path.suffix != ".py" or SKIP_DIRS & set(path.parts):
        return 0

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0

    violations = find_violations(source)
    if not violations:
        return 0

    try:
        shown_path = path.relative_to(pathlib.Path.cwd())
    except ValueError:
        shown_path = path

    lines = ["偵測到 inline import，請移到模組頂層：", ""]
    lines += [f"  {shown_path}:{lineno}  （在 {scope} 內）{text}" for lineno, scope, text in violations]
    lines += [
        "",
        "若移到頂層會造成循環 import，代表依賴方向有問題：把需要那個依賴的程式碼",
        "移到依賴圖更上層的模組（例如入口檔），而不是把 import 塞進函式裡。",
    ]
    print("\n".join(lines), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
