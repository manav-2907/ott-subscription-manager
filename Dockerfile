FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code. Secrets (credentials.json, token.json, .env) and
# generated data are NOT baked into the image — they are mounted at runtime
# (see docker-compose.yml) so the image stays clean and shareable.
COPY *.py ./

EXPOSE 8501

ENTRYPOINT ["streamlit", "run", "app_2.py", \
            "--server.port=8501", \
            "--server.address=0.0.0.0", \
            "--server.headless=true"]
