FROM python:3.11-slim

# Use mirror for slower networks (NAS environments)
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk fonts-noto-cjk-extra \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Download dependencies first (cache layer)
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

# Clone source code from GitHub (shallow, single branch — avoids old tags/gitlinks)
RUN git clone --depth 1 --single-branch --branch main \
    https://github.com/Escaper929/FilmSheet.git /tmp/filmsheet 2>&1 || true

# Copy source modules
RUN if [ -d /tmp/filmsheet ]; then \
        cp -r /tmp/filmsheet/api/main.py . && \
        cp -r /tmp/filmsheet/api/index.html . && \
        cp -r /tmp/filmsheet/processor/ ./processor/ && \
        cp -r /tmp/filmsheet/engine/ ./engine/ && \
        cp -r /tmp/filmsheet/utils/ ./utils/ && \
        cp -r /tmp/filmsheet/filmsheet/ ./filmsheet/; \
    else \
        echo "FAILED to clone"; exit 1; \
    fi

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
