from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

import os
import tempfile
import pytesseract
import cv2


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://vexonhq-ocr.vercel.app",
    ],
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
async def do_ocr(file: UploadFile = File(...)):
    contents = await file.read()

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    image = cv2.imread(tmp_path)

    text = pytesseract.image_to_string(
        image,
        lang="tha+eng"
    )

    os.unlink(tmp_path)

    return {
        "success": True,
        "text": text,
        "filename": file.filename
    }