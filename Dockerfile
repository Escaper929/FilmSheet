# FilmSheet API — self-contained Docker build from GitHub
# Built via: docker compose build with any local context (this Dockerfile clones everything internally)

FROM python:3.11-slim AS downloader

# Clone source code from GitHub (single branch, shallow)
ARG REPO_URL=https://github.com/Escaper929/FilmSheet.git
ARG REPO_BRANCH=main
RUN git clone --depth 1 --branch ${REPO_BRANCH} ${REPO_URL} /app/src

FROM python:3.11-slim

# Use mirror for slower networks (NAS environments)
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk fonts-noto-cjk-extra \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy source from download stage
COPY --from=downloader /app/src/ .

# Install dependencies (use mirror for slower networks)
RUN pip install --no-cache-dir \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r api/requirements.txt

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
