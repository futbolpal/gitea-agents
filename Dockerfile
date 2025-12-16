FROM python:3.9-slim

WORKDIR /app

# Install kilocode CLI and git
RUN apt-get update && apt-get install -y curl git && \
    curl -fsSL https://kilo.ai/install.sh | sh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]