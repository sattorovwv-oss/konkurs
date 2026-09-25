FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser . .
RUN mkdir -p /app/storage/receipts /app/storage/submissions && chown -R appuser:appuser /app/storage
USER appuser
EXPOSE 17563
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "17563"]
