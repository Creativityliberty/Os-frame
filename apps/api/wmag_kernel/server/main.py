from fastapi import FastAPI
from server.api import router

app = FastAPI(title="WMAG Kernel")
app.include_router(router)
