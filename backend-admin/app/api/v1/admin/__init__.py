"""运维后台 API 路由汇总"""
from fastapi import APIRouter

from app.api.v1.admin.admins import router as admins_router
from app.api.v1.admin.audit_logs import router as audit_logs_router
from app.api.v1.admin.auth import router as auth_router
from app.api.v1.admin.users import router as users_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(admins_router)
router.include_router(audit_logs_router)
