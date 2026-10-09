from fastapi import Request

from app.config import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_redis(request: Request):
    return request.app.state.redis
