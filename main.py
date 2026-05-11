from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {
        "success": True,
        "service": "VEXONHQ OCR API",
        "status": "running"
    }

@app.get("/health")
def health():
    return {
        "status": "healthy"
    }
