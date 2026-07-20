FROM python:3.11-slim

# Install fonts and git
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk fonts-noto-cjk-extra git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clone the repo at build time
ARG GIT_REPO=https://github.com/Escaper929/FilmSheet.git
ARG GIT_BRANCH=main
RUN git clone --depth 1 --branch ${GIT_BRANCH} ${GIT_REPO} . || true

# Install Python dependencies
RUN pip install --no-cache-dir -r api/requirements.txt

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
