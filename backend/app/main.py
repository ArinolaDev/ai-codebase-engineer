from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api import projects, parsing_routes, indexing_routes, chat_routes, analysis_routes, testgen_routes, fix_routes, review_routes, verify_routes, graph_routes, jobs_routes, github_routes

app = FastAPI(title="AI Codebase Engineer", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(parsing_routes.router, prefix="/api/projects", tags=["parsing"])
app.include_router(indexing_routes.router, prefix="/api/projects", tags=["indexing"])
app.include_router(chat_routes.router, prefix="/api/projects", tags=["chat"])
app.include_router(analysis_routes.router, prefix="/api/projects", tags=["analysis"])
app.include_router(testgen_routes.router, prefix="/api/projects", tags=["testgen"])
app.include_router(fix_routes.router, prefix="/api/projects", tags=["fixes"])
app.include_router(review_routes.router, prefix="/api/projects", tags=["review"])
app.include_router(verify_routes.router, prefix="/api/projects", tags=["verification"])
app.include_router(graph_routes.router, prefix="/api/projects", tags=["graph"])
app.include_router(github_routes.router, prefix="/api/projects", tags=["github"])
app.include_router(jobs_routes.router, prefix="/api/projects", tags=["jobs"])
app.include_router(jobs_routes.jobs_router, prefix="/api/jobs", tags=["jobs"])


@app.get("/health")
def health_check():
    return {"status": "ok", "env": settings.app_env}
