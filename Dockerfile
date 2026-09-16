FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

# Aponta o Playwright para o Chromium já instalado na imagem
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD gunicorn zearch:app --bind 0.0.0.0:$PORT --timeout 180 --workers 1 --worker-class gthread --threads 4