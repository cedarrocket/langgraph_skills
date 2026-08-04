"""从 skill 文件用 parser 导出 IR 初稿（方案 A：生成金标准期望 IR 的初稿）。

用法:
    python scripts/dump_ir.py <skill.md>            # 打印 IR 到 stdout
    python scripts/dump_ir.py <skill.md> -o <out>   # 写入文件

注意:
    这是**初稿生成器**，输出需人工审阅后才成为金标准（spec/examples/*.ir.json）。
    最终金标准是契约，不是本脚本每次运行的结果。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph_skills.models import compiled_to_dict
from langgraph_skills.parser import parse_compiled_skill


def dump(skill_path: str) -> dict:
    return compiled_to_dict(parse_compiled_skill(skill_path))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print("Usage: python scripts/dump_ir.py <skill.md> [-o <out.json>]", file=sys.stderr)
        sys.exit(2)
    skill_path = args[0]
    out_path = None
    if "-o" in sys.argv:
        out_path = sys.argv[sys.argv.index("-o") + 1]

    data = dump(skill_path)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if out_path:
        Path(out_path).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {out_path}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
