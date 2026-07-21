FROM python:3.11-slim

# Install CJK fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk fonts-noto-cjk-extra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies (no source code here — mounted via volume)
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Pre-clean main.py for Docker standalone mode (remove sys.path, unused imports)
COPY main.py ./main.py
RUN sed -i '/^sys\.path\.insert/d; /^# Add parent directory to path for imports/d; /^import tempfile$/d; /^import sys$/d' main.py

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
