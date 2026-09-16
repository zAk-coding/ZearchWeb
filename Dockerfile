# Imagem oficial já contém Python 3.11 + Chromium + libs do sistema
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

# Instala as dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . .

# Render injeta $PORT automaticamente
CMD gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1