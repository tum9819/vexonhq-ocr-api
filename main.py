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

# grayscale
gray = cv2.cvtColor(
    image,
    cv2.COLOR_BGR2GRAY
)

# denoise
denoised = cv2.fastNlMeansDenoising(
    gray
)

# threshold
processed = cv2.threshold(
    denoised,
    150,
    255,
    cv2.THRESH_BINARY
)[1]

text = pytesseract.image_to_string(
    processed,
    lang="tha+eng",
    config="--psm 6"
)

@app.post("/ocr")
async def do_ocr(file: UploadFile = File(...)):
    contents = await file.read()

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    image = cv2.imread(tmp_path)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    thresh = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )[1]

    denoise = cv2.fastNlMeansDenoising(thresh)

    text = pytesseract.image_to_string(
        denoise,
        lang="tha+eng"
    )

    os.unlink(tmp_path)

    return {
        "success": True,
        "text": text,
        "filename": file.filename
    }