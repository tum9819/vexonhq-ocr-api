from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

@app.post("/ocr")
async def upload_ocr(file: UploadFile = File(...)):
    contents = await file.read()

    return {
        "success": True,
        "filename": file.filename,
        "content_type": file.content_type,
        "size": len(contents),
        "message": "Upload received successfully"
    }
