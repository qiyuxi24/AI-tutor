from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.chat import router as chat_router

app = FastAPI(title="AI Tutor API", version="0.1.0")

# 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat_router, prefix="/api/v1")

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}