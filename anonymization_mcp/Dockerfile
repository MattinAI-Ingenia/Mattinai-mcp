FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    cmake \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir wheel setuptools cython numpy && \
    pip install --no-cache-dir -r requirements.txt

RUN python -m spacy download en_core_web_lg

COPY app/ /app/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]