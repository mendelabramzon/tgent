from app.routes.chats import router as chats_router
from app.routes.prompts import router as prompts_router
from app.routes.settings import router as settings_router
from app.routes.suggestions import router as suggestions_router

__all__ = [
    "suggestions_router",
    "chats_router",
    "settings_router",
    "prompts_router",
]


