from langchain_core.tools import tool

@tool
def list_dir(path: str) -> str:
    """列出指定目录下的条目。path 为目录路径。"""
    import os
    try:
        items = os.listdir(path)
        return "\n".join(sorted(items)) if items else "(empty)"
    except Exception as e:
        return f"Error: {e}"

@tool
def read_text(path: str) -> str:
    """读取文本文件内容。path 为文件路径。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error: {e}"

@tool
def write_text(path: str, content: str) -> str:
    """写入文本文件。path 为文件路径，content 为完整内容。"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"Error: {e}"

@tool
def append_text(path: str, content: str) -> str:
    """追加文本到文件末尾。path 为文件路径，content 为追加内容。"""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        return f"appended {len(content)} chars to {path}"
    except Exception as e:
        return f"Error: {e}"
