"""共用 Pydantic 模型。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """所有 response DTO 的基類：欄位 snake_case，序列化成 camelCase（前端友善）。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class StockRef(CamelModel):
    """股票引用基類：代號 + 名稱皆必填（雷達 / 持股 / 自選 / 行事曆等大宗模式）。"""
    stock_id: str
    stock_name: str


class StockRefOptional(CamelModel):
    """股票引用基類：名稱可空（交易紀錄 / 已實現損益 / 回測 summary 等老資料來源）。"""
    stock_id: str
    stock_name: str | None = None


class ErrorResponse(CamelModel):
    code: str
    message: str
    detail: str | None = None
