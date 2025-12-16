FROM python:3.9-slim

WORKDIR /app

# Install kilocode CLI and git
RUN apt-get update && apt-get install -y curl git && \
    curl -fsSL https://kilo.ai/install.sh | sh && \
    export PATH=$PATH:/root/bin && \
    which kilocode || (echo "kilocode not found in PATH" && find /root -name kilocode 2>/dev/null || echo "kilocode not found anywhere" && exit 1) && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
ENV PATH=$PATH:/root/bin

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]