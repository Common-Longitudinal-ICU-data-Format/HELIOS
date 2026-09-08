FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY helios/ ./helios/
COPY config.yaml tokens.yaml ./
EXPOSE 8000
CMD ["uvicorn", "--factory", "helios.fhir.app:create_app", \
     "--host", "0.0.0.0", "--port", "8000"]
