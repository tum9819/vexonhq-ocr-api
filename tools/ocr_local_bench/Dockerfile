# Benchmark image: same base + same OCR packages as vexonhq-ocr-api/Dockerfile (prod).
FROM python:3.10-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 tesseract-ocr tesseract-ocr-tha tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "pytesseract>=0.3.13,<0.4" "opencv-python-headless>=4.13,<5" "numpy>=1.26,<3" "Pillow>=10,<13" "pypdfium2>=4.30.0"
WORKDIR /work
CMD ["bash"]
