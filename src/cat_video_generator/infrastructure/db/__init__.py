"""PostgreSQL持久化实现。"""

from .models import Base, SCHEMA_NAME

__all__ = ["Base", "SCHEMA_NAME"]
