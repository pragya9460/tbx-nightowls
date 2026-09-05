FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the local MiniLM embedding model so the running container
# never needs network access for the knowledge layer.
ENV SENTENCE_TRANSFORMERS_HOME=/app/model-cache
RUN python -c "import urllib.request, zipfile, io, os; \
    url='https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz'; \
    urllib.request.urlretrieve(url, '/tmp/m.tar.gz')" \
    && mkdir -p /app/model-cache/all-MiniLM-L6-v2 \
    && tar -xzf /tmp/m.tar.gz -C /app/model-cache/all-MiniLM-L6-v2 \
    && rm /tmp/m.tar.gz

COPY . .

ENV ARTHA_DATABASE_URL=mysql://artha:artha@mysql:3306/artha
ENV ARTHA_DATA_DIR=/app/seed-data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
