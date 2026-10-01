#!/usr/bin/env python3
"""把既有的檔案級檢查掃過這一輪改動到的檔案。

補的是 PostToolUse 的洞：透過 Bash 改檔案（heredoc、sed、tee）一樣會改到程式碼，
但 Bash 的 tool_input 只有 command，拿不到 file_path，那三個 hook 因此既不觸發、
也沒辦法靠調 matcher 補。這支改用 git 當事實來源，誰改的都一樣抓得到。

掛 Stop 而不是 PostToolUse(Bash)：同一個檔案一輪內會被改很多次，每次都回報同樣
的問題只是雜訊。退出碼 2 會把訊息回饋給 Claude 要求修正。
"""
import json
import pathlib
import subprocess
import sys

# 重用既有腳本，不要把判斷邏輯複製一份 —— 兩份遲早會走岔。
CHECKERS = ["check_column_queries.py", "check_redundant_comments.py"]


def _git(root: pathlib.Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True
    )
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def changed_python_files(root: pathlib.Path) -> list[str]:
    """相對 HEAD 有變動的 .py，含未追蹤檔案。

    `git diff HEAD` 同時涵蓋 staged 與 unstaged，所以不需要再跑一次 --cached。
    刪掉的檔案會被 is_file() 濾掉 —— 檔案不在了，沒什麼好檢查的。
    """
    candidates = set(_git(root, "diff", "--name-only", "HEAD"))
    candidates |= set(_git(root, "ls-files", "--others", "--exclude-standard"))
    return sorted(
        name
        for name in candidates
        if name.endswith(".py") and (root / name).is_file()
    )


def run_checker(root: pathlib.Path, checker: str, file_path: pathlib.Path):
    """用 PostToolUse 的 payload 形狀呼叫檢查腳本，回傳它的抱怨（沒有就回 None）。"""
    result = subprocess.run(
        [sys.executable, str(root / ".claude" / "hooks" / checker)],
        input=json.dumps({"tool_input": {"file_path": str(file_path)}}),
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.stderr.strip() if result.returncode == 2 else None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    # 退出碼 2 會叫模型再跑一輪，那一輪結束又會觸發 Stop。Claude Code 用這個
    # 旗標標記「你正在自己上一次回報所引發的續跑裡」，不看它就會無限繞下去。
    if payload.get("stop_hook_active"):
        return 0

    root = pathlib.Path(
        payload.get("cwd") or subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        ).stdout.strip() or "."
    )
    if not (root / ".git").exists():
        return 0

    complaints = []
    for name in changed_python_files(root):
        for checker in CHECKERS:
            found = run_checker(root, checker, root / name)
            if found:
                complaints.append(found)

    if not complaints:
        return 0

    print(
        "這一輪改動到的檔案沒通過檢查（這些檔案是透過 Bash 改的，"
        "PostToolUse 的 file_path hook 看不到）：\n\n"
        + "\n\n".join(complaints),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
