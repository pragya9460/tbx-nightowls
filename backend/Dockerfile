FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so Docker caches them across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default DB URL is overridden by compose with the MySQL service URL.
ENV ARTHA_DATABASE_URL="mysql+pymysql://artha:artha@mysql:3306/artha"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
