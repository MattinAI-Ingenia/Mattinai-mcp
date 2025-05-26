# Stage 1: Dependencies builder
FROM python:3.10-slim AS dependencies

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    cmake \
    git \
    wget \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy and install requirements
COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip wheel setuptools cython && \
    pip install numpy scipy && \
    pip install -r /tmp/requirements.txt

# Download spacy model
RUN python -m spacy download en_core_web_lg && \
    python -m spacy download en_core_web_sm 

# Download and install additional models
RUN python -c "from flair.models import SequenceTagger; SequenceTagger.load('flair/ner-english')"

# Stage 2: Runtime
FROM python:3.10-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Install minimal runtime dependencies
RUN apt-get update && apt-get install -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder stage
COPY --from=dependencies /opt/venv /opt/venv
COPY --from=dependencies /root/.flair /root/.flair

WORKDIR /app

# Copy application code
COPY app/ /app/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]