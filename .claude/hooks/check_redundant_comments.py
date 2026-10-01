#!/usr/bin/env python3
"""擋下重述程式碼的註解。

規則：名稱已經說清楚的事，註解不要再說一次。

    def create_venue(...):
        \"\"\"Creates a new venue.\"\"\"          NG  —— 名稱已經講完了

    def create_event(...):
        \"\"\"Creates an unpublished event and clones the venue's layout.\"\"\"
                                              OK  —— 補了「未上架」「複製場地」

判斷方式是「殘餘資訊量」：把 docstring 第一句扣掉函數名稱的詞彙與停用詞，
看還剩幾個字。剩 0~1 個代表這句話幾乎只是把名稱換句話說。

閾值是對這個 repo 實測調出來的。120 個 docstring 中殘餘 0 的有 1 個、殘餘 1
的有 1 個，兩個都是真的多餘；放寬到殘餘 2 會誤殺 9 個好註解（例如
get_event 的「with its zones and seats」確實補了資訊）。寧可漏也不要吵——
會吵的 hook 最後都會被關掉。

判斷還要求句子與名稱有實質重疊。少了這一項，像只寫 "A8." 這種隱晦但不囉唆
的交叉引用也會被誤判成重述——低資訊量和重述名稱是兩回事，這條規則只管後者。

第二條規則（行註解重述下一行）只看得懂詞彙層面的重述，這是它的真實涵蓋範圍：

    # create order
    order = create_order(payload)      抓得到——每個詞都在下一行

    # increment the counter
    counter = counter + 1              抓不到——"increment" 不在下一行

    # 建立 session 並開啟交易
    session = make_session()           抓不到——中文詞彙無法與英文程式碼比對

語意層面的重述和中文註解需要語言模型才判斷得準。真要涵蓋那一半，該用
type: "prompt" 的 hook，而不是把這支腳本的閾值調鬆——調鬆只會開始誤殺。

刻意不管的事：
  - 多行 docstring。太長該砍、常識該刪，都需要判斷，交給 prompt hook；
    這支只抓字面重述。
  - 註解掉的程式碼。那是另一種味道，而且本 repo 的說明註解裡常有示範用的
    程式碼片段，誤判率太高。
  - 單字的 section 標題（# Cache、# Repositories）。那是排版用的組織手段，
    不是在解釋程式碼。

退出碼 2 會把訊息回饋給 Claude 要求修正；其餘情況一律 0，不擋任何事。
"""
import ast
import io
import json
import pathlib
import re
import sys
import tokenize

# 扣掉之後才算「殘餘」的詞：冠詞、介系詞，以及幾個在 docstring 裡沒有資訊量的
# 通用詞（"new"、"details"、"single" 之類）。
STOPWORDS = {
    "a", "an", "the", "of", "to", "for", "its", "it", "this", "that", "and",
    "or", "by", "with", "in", "on", "at", "is", "are", "be", "new", "all",
    "one", "from", "as", "into", "single", "given", "details", "their", "s",
}

# 殘餘幾個字以內視為重述。見模組 docstring 的實測依據。
MAX_RESIDUAL_WORDS = 1

# 句子至少要命中名稱裡的幾個詞，才算是在「重述名稱」。
MIN_NAME_OVERLAP = 2

# 行內註解要有幾個實詞才納入「重述下一行」的判斷。低於這個數的多半是
# section 標題而不是解釋。
MIN_COMMENT_WORDS = 2

SKIP_DIRS = {".venv", "migrations", "__pycache__", "node_modules", ".git"}


def _words(text: str) -> list[str]:
    return [w for w in re.sub(r"[^a-z0-9]+", " ", text.lower()).split() if w]


def _stem(word: str) -> str:
    """夠用就好的字尾處理：只求 creates/create、cloning/clone 對得起來。"""
    if word.endswith("ing") and len(word) > 5:
        return word[:-3]
    return word.rstrip("s")


def _first_sentence(docstring: str) -> str:
    return docstring.strip().split("\n")[0]


def find_restating_docstrings(tree) -> list[tuple[int, str, str]]:
    """單行 docstring 幾乎只是把函數名稱換句話說。"""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        docstring = ast.get_docstring(node)
        if not docstring or len(docstring.strip().split("\n")) > 1:
            continue

        name_stems = {_stem(w) for w in _words(node.name)}
        doc_words = _words(_first_sentence(docstring))
        doc_stems = {_stem(w) for w in doc_words}

        # 「重述名稱」要同時滿足兩件事：句子確實在講名稱裡的東西（有重疊），
        # 而且扣掉之後沒剩下什麼（殘餘低）。少了重疊這一項，只寫 "A8." 這種
        # 隱晦但不囉唆的交叉引用也會被當成重述——那不是這條規則要管的。
        overlap = name_stems & doc_stems
        if len(overlap) < min(MIN_NAME_OVERLAP, len(name_stems)):
            continue

        residual = [
            w for w in doc_words
            if _stem(w) not in name_stems and w not in STOPWORDS
        ]
        if len(residual) <= MAX_RESIDUAL_WORDS:
            found.append((node.lineno, node.name, docstring.strip()))
    return found


def find_restating_comments(source: str) -> list[tuple[int, str, str]]:
    """獨立成行的註解，實詞全部出現在下一行程式碼裡。"""
    lines = source.split("\n")
    found = []

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []

    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue

        line = lines[token.start[0] - 1]
        if line.strip() != token.string.strip():
            continue  # 行尾註解不在此規則範圍

        comment_words = {w for w in _words(token.string.lstrip("#")) if len(w) > 2}
        if len(comment_words) < MIN_COMMENT_WORDS:
            continue

        next_code = next((l for l in lines[token.start[0]:] if l.strip()), "")
        if next_code.lstrip().startswith("#"):
            continue  # 連續註解是一段說明，不是在標註某一行

        next_words = {_stem(w) for w in _words(next_code)}
        if comment_words and {_stem(w) for w in comment_words} <= next_words:
            found.append((token.start[0], token.string.strip(), next_code.strip()))
    return found


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

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0  # 編輯到一半的檔案不是我們要管的事

    docstrings = find_restating_docstrings(tree)
    comments = find_restating_comments(source)
    if not docstrings and not comments:
        return 0

    try:
        shown = path.relative_to(pathlib.Path.cwd())
    except ValueError:
        shown = path

    out = ["註解重述了程式碼本身，沒有補充任何資訊："]

    if docstrings:
        out.append("")
        for lineno, name, doc in docstrings:
            out.append(f"  {shown}:{lineno}  def {name}")
            out.append(f'      """{doc}"""')
        out += [
            "",
            "  這句話扣掉函數名稱的詞彙之後幾乎什麼都不剩。要嘛刪掉，要嘛補上",
            "  名稱說不出口的事 —— 前置條件、副作用、為什麼是這個做法。",
        ]

    if comments:
        out.append("")
        for lineno, comment, code in comments:
            out.append(f"  {shown}:{lineno}  {comment}")
            out.append(f"      -> {code[:70]}")
        out += [
            "",
            "  註解裡的每個詞都出現在下一行程式碼裡，讀者看程式碼就夠了。",
        ]

    out += [
        "",
        "本檢查只看單行的字面重述。多行 docstring 的長度與常識問題由 prompt",
        "hook 判斷，不在這裡。",
    ]
    print("\n".join(out), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
