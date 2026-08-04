"""全局配置（单一入口）。

对应 PROCESS.md 设计基线：
  - 密钥、模型、base_url 等引擎参数统一在此，环境变量可覆盖
  - 避免硬编码散落（model / base_url / temperature）
"""

import os
import sys
from dataclasses import dataclass
from typing import Optional

# 环境变量名（统一前缀 LGSKILLS_）
ENV_MODEL = "LGSKILLS_MODEL"
ENV_BASE_URL = "LGSKILLS_BASE_URL"
ENV_TEMPERATURE = "LGSKILLS_TEMPERATURE"
ENV_STRICT = "LGSKILLS_STRICT"

# 默认值
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_LOOPS = 10


@dataclass
class Settings:
    """引擎运行时配置。"""

    api_key: Optional[str] = None
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    temperature: float = DEFAULT_TEMPERATURE
    strict: bool = False  # True 时未知 section 等容错从 warning 升级为 error

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_key=get_deepseek_key(),
            model=os.environ.get(ENV_MODEL, DEFAULT_MODEL),
            base_url=os.environ.get(ENV_BASE_URL, DEFAULT_BASE_URL),
            temperature=float(os.environ.get(ENV_TEMPERATURE, DEFAULT_TEMPERATURE)),
            strict=os.environ.get(ENV_STRICT, "").lower() in ("1", "true", "yes"),
        )


def get_deepseek_key() -> Optional[str]:
    """Resolve the API key from, in priority order:
    1. The `DEEPSEEK_API_KEY` environment variable.
    2. A `.env` file in the current working directory (via python-dotenv).
    3. A key file whose path is set via the `DEEPSEEK_API_KEY_FILE`
       environment variable.

    Returns ``None`` when no key is available.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if api_key:
        return api_key.strip()

    key_file = os.environ.get("DEEPSEEK_API_KEY_FILE")
    if key_file:
        key_path = os.path.expanduser(key_file)
        if os.path.exists(key_path):
            try:
                with open(key_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception as e:
                print(f"Warning: Failed to read {key_path}: {e}", file=sys.stderr)
    return None
