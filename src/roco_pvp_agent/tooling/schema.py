"""所有模型可调用工具共享的严格参数基类。"""

from pydantic import BaseModel, ConfigDict


class StrictToolArgs(BaseModel):
    """不接受隐式类型转换或未声明字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)


__all__ = ["StrictToolArgs"]
