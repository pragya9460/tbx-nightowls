FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV ARTHA_DATABASE_URL=mysql://artha:artha@mysql:3306/artha
ENV ARTHA_DATA_DIR=/app/seed-data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
