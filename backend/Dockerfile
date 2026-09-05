FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so Docker caches them across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# artha:artha is only meaningful when POSTGRES points at the compose service;
# in Docker the URL always comes from ARTHA_DATABASE_URL.
ENV ARTHA_DATABASE_URL="postgresql+psycopg2://artha:artha@postgres:5432/artha"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
