FROM python:3.11-slim

# Install CJK fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk fonts-noto-cjk-extra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies (always fresh install, no pip cache on disk)
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy all source modules (api/main.py depends on processor/, engine/, utils/, filmsheet/)
COPY processor/ ./processor/
COPY engine/ ./engine/
COPY utils/ ./utils/
COPY filmsheet/ ./filmsheet/

# Copy the API entry point and mobile web frontend
COPY api/main.py .
COPY api/index.html .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
