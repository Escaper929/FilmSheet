FROM python:3.11-slim

# Use mirror for slower networks (NAS environments)
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk fonts-noto-cjk-extra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies (use mirror for slower networks)
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

# Copy source modules (main.py depends on processor/, engine/, utils/, filmsheet/)
COPY api/main.py .
COPY api/index.html .
COPY processor/ ./processor/
COPY engine/ ./engine/
COPY utils/ ./utils/
COPY filmsheet/ ./filmsheet/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
