FROM python:3.11-slim

# Install fonts and git (for potential future pulls)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk fonts-noto-cjk-extra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy all project files directly into the image
COPY processor/ ./processor/
COPY engine/ ./engine/
COPY utils/ ./utils/
COPY api/requirements.txt ./api/requirements.txt
COPY api/main.py ./api/main.py
COPY api/index.html ./api/index.html

# Install Python dependencies
RUN pip install --no-cache-dir -r api/requirements.txt

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
