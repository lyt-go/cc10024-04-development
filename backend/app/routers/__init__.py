"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource, run_captures, site_attribution

# 站点归属与运行附加接口的固定路径要先于通用资源路由注册，避免 /api/{module} 抢先匹配。
ROUTERS = [site_attribution, run_captures, resource]
