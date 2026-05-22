"""
模拟炒股系统 - FastAPI 主入口
启动命令: python main.py
"""
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os

from routes import router
from database import init_db

# ─── 创建 FastAPI 应用 ────────────────────────────────────

app = FastAPI(
    title="模拟炒股系统",
    description="支持A股、港股、美股的历史回测和实时模拟盘",
    version="1.0.0"
)

# ─── CORS 中间件 ───────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── 注册路由 ──────────────────────────────────────────────

app.include_router(router)

# ─── 前端页面 ──────────────────────────────────────────────

@app.get("/")
async def root():
    """返回前端页面"""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    return FileResponse(html_path, media_type="text/html")


# ─── 启动事件 ──────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    await init_db()
    print("=" * 50)
    print("  模拟炒股系统已启动")
    print("  访问地址: http://localhost:8000")
    print("=" * 50)


# ─── 启动 ─────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
