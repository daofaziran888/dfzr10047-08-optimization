"""路由汇总：更正协调专用路由先注册，随后注册通用资源路由。"""
from __future__ import annotations

from app.routers import correction_flow, resource

ROUTERS = [correction_flow, resource]
