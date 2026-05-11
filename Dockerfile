FROM python:3.11-slim

ENV FLAGS_use_mkldnn=false
ENV CPU_NUM=1
ENV FLAGS_use_mkldnn=false
ENV GLOG_minloglevel=3

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]