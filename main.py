from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from paddleocr import PaddleOCR
import tempfile
import shutil

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

ocr = PaddleOCR(
    use_angle_cls=True,
    lang="ch",
    show_log=False
)

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
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
        shutil.copyfileobj(file.file, temp_file)
        temp_path = temp_file.name

    result = ocr.ocr(temp_path)

    extracted_text = []

    for line in result[0]:
        text = line[1][0]
        extracted_text.append(text)

    return {
        "success": True,
        "filename": file.filename,
        "texts": extracted_text
    }
