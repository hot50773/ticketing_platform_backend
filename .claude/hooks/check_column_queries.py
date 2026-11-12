#!/usr/bin/env python3
"""擋下 SQLAlchemy 的欄位級別查詢（Column-level Query）。

規則：`select()` 要選整個 entity，不要只選欄位。

    select(EventZone)                     OK
    select(EventZone.id, EventZone.name)  NG

欄位級別查詢回傳的是 Row tuple 而不是 ORM 物件，呼叫端因此得靠位置或字串
取值，型別檢查幫不上忙，之後往投影裡加一欄就會默默改變所有呼叫端的解包
形狀。這個 codebase 已經被這件事咬過一次：seat_layout() 用
`select(EventZone)` 卻直接 `.all()`，拿到 Row 之後 `zone.id` 直接
AttributeError。

用 AST 而不是 regex：`select(` 常常跨好幾行，字串比對會漏掉也會誤判。

豁免：
  1. 統計查詢       select(func.count()) / func.max() / ...
  2. 純量子查詢     select(EventZone.id).scalar_subquery() — 子查詢只能選一欄
  3. INSERT…SELECT  insert(T).from_select([...], source) — 必須對齊欄位清單
  4. 單一主鍵       select(EventZone.id) — 只取主鍵，沒有投影形狀可言

第 4 條的理由和前三條不同：前三條是語法上別無選擇，這條是這種查詢不會有
「往投影裡多加一欄」的演化壓力 —— 它要的就是一串 id，回傳 list[int] 比把
整批 entity 載進來只為了取 id 更誠實。但必須是「剛好一個引數且為主鍵」；
select(Zone.id, Zone.name) 仍然是違規。

退出碼 2 會把訊息回饋給 Claude 要求修正；其餘情況一律 0，不擋任何事。
"""
import ast
import json
import pathlib
import sys

# func.<name>() 視為統計用途
AGGREGATE_FUNCS = {
    "count", "max", "min", "sum", "avg",
    "stddev", "variance", "array_agg", "string_agg", "percentile_cont",
}

# 這些呼叫接的 select 本來就只能是欄位清單
SUBQUERY_WRAPPERS = {"scalar_subquery", "subquery", "exists", "cte", "label"}

# 主鍵欄位名。本專案九張表的主鍵都由 TableBase 統一定義為 id；哪天出現複合
# 主鍵或改名的主鍵，這個常數和 _is_primary_key_only() 要一起重新檢視。
PRIMARY_KEY_ATTR = "id"

SKIP_DIRS = {".venv", "migrations", "__pycache__", "node_modules", ".git"}


def _walk(node):
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _walk(child)


def _is_select_call(node) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "select"
    )


def _is_column_ref(node) -> bool:
    """Model.column 形式。func.now() 之類是 Call，不算；小寫開頭的變數也不算。"""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id[:1].isupper()
        and not node.attr.startswith("__")
    )


def _has_aggregate(node) -> bool:
    """子樹裡出現 func.count() 這類統計函式。"""
    for sub in _walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr in AGGREGATE_FUNCS
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id == "func"
        ):
            return True
    return False


def _exempt_select_nodes(tree) -> set[int]:
    """收集因為語法需求而必須用欄位清單的 select 節點。"""
    exempt: set[int] = set()
    from_select_names: set[str] = set()

    for node in _walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # .scalar_subquery() / .subquery() / .exists() 鏈上的 select
        if isinstance(node.func, ast.Attribute) and node.func.attr in SUBQUERY_WRAPPERS:
            for sub in _walk(node):
                if _is_select_call(sub):
                    exempt.add(id(sub))

        # insert(T).from_select([...], source)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "from_select":
            for arg in node.args:
                for sub in _walk(arg):
                    if _is_select_call(sub):
                        exempt.add(id(sub))
                    elif isinstance(sub, ast.Name):
                        from_select_names.add(sub.id)

    # source = select(...) 之後才餵給 from_select 的情形
    if from_select_names:
        for node in _walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & from_select_names:
                for sub in _walk(node.value):
                    if _is_select_call(sub):
                        exempt.add(id(sub))

    return exempt


def _is_primary_key_only(args, columns) -> bool:
    """剛好只選一個主鍵欄位，例如 select(EventZone.id)。

    條件刻意收緊成「整個 select 只有這一個引數」：select(Zone.id, Zone.name)
    的 columns 也含有主鍵，但它已經是一個有形狀的投影了。
    """
    return (
        len(args) == 1
        and len(columns) == 1
        and columns[0].attr == PRIMARY_KEY_ATTR
    )


def find_violations(source: str) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # 編輯到一半的檔案不是我們要管的事

    exempt = _exempt_select_nodes(tree)
    violations = []

    for node in _walk(tree):
        if not _is_select_call(node) or id(node) in exempt:
            continue
        if _has_aggregate(node):
            continue

        columns = [arg for arg in node.args if _is_column_ref(arg)]
        if _is_primary_key_only(node.args, columns):
            continue
        if columns:
            shown = ", ".join(f"{c.value.id}.{c.attr}" for c in columns[:3])
            if len(columns) > 3:
                shown += ", …"
            violations.append((node.lineno, shown))

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

    lines = [
        "偵測到 SQLAlchemy 欄位級別查詢（Column-level Query），請改為選取整個 entity：",
        "",
    ]
    lines += [f"  {shown_path}:{lineno}  select({cols})" for lineno, cols in violations]
    lines += [
        "",
        "  select(Zone.id, Zone.name)  ->  select(Zone)，再於 Python 端取出需要的欄位",
        "",
        "欄位級別查詢回傳 Row tuple 而非 ORM 物件，呼叫端得靠位置解包，之後往投影",
        "裡加一欄就會默默改變所有呼叫端的形狀。",
        "",
        "已自動豁免：統計查詢（func.count / max / …）、純量子查詢（.scalar_subquery()）、",
        f"INSERT…SELECT 的來源，以及只取單一主鍵的 select(Model.{PRIMARY_KEY_ATTR})。",
        "若這裡確實屬於上述情境卻仍被擋下，請回報。",
    ]
    print("\n".join(lines), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
