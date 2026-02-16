from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db.init import init_db
from dotenv import load_dotenv
import os
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger.info("Starting Adams Property Care Backend...")

load_dotenv()

from routers import auth, payment, admin, webhooks

app = FastAPI(title="Adams Property Care Backend")

# Add request logging middleware
@app.middleware("http")
async def log_requests(request, call_next):
    logger = logging.getLogger(__name__)
    logger.info(f"{request.method} {request.url.path}")
    response = await call_next(request)
    logger.info(f"{request.method} {request.url.path} - Status: {response.status_code}")
    return response

@app.on_event("startup")
def startup_event():
    init_db()

# Configure CORS
origins = [
    "http://localhost:8081",
    "http://127.0.0.1:8081",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(auth.router, prefix="/auth", tags=["Auth"])
app.include_router(payment.router, prefix="/payments", tags=["Payments"])
app.include_router(admin.router, prefix="/admin", tags=["Admin"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["Webhooks"])

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/")
def root():
    return {"message": "Adams Property Care Backend running successfully"}
