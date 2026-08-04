"""金标准示例测试：锁定 parser 行为与 spec 目标语义一致。

遍历 spec/examples/*.md，用当前 parser 解析，断言结果 == 同名 .ir.json。
.ir.json 是**契约**（反映 spec/dsl_spec.yaml 的目标语义），不是 parser 现状。
若契约变了：更新 spec/dsl_spec.yaml -> 重新生成参考文档 -> 手动更新 .ir.json。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph_skills.models import compiled_to_dict
from langgraph_skills.parser import parse_compiled_skill

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "spec" / "examples"


def _parse_to_dict(md_path: Path) -> dict:
    return compiled_to_dict(parse_compiled_skill(str(md_path)))


def _iter_examples():
    for md in sorted(EXAMPLES_DIR.glob("*.md")):
        ir = md.with_suffix(".ir.json")
        if ir.exists():
            yield md, ir


@pytest.mark.parametrize("md_path,ir_path", _iter_examples(), ids=lambda p: str(p.name))
def test_golden_example(md_path, ir_path):
    expected = json.loads(ir_path.read_text(encoding="utf-8"))
    actual = _parse_to_dict(md_path)
    assert actual == expected, (
        f"Parser output for {md_path.name} diverged from golden standard.\n"
        f"Actual:   {json.dumps(actual, ensure_ascii=False, indent=2)}\n"
        f"Expected: {json.dumps(expected, ensure_ascii=False, indent=2)}"
    )
