FROM python:3.9-slim

WORKDIR /app

# Create non-root user and set up directories
RUN groupadd -r kilocode && \
    useradd -r -g kilocode kilocode && \
    mkdir -p /home/kilocode/.kilocode /workspace /data && \
    chown -R kilocode:kilocode /home/kilocode /workspace /data


RUN apt-get update && apt-get install -y curl git nodejs npm && \
    npm install -g @kilocode/cli @openai/codex@0.112.0 && \
    which kilocode || (echo "kilocode not found in PATH" && exit 1) && \
    which codex || (echo "codex not found in PATH" && exit 1) && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
#ENV PATH=$PATH:/root/bin

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

USER kilocode
CMD ["python", "main.py"]
