# Build context is the REPO ROOT (see docker-compose.yml) so the engine under
# services/engine is available to copy into the image.
FROM python:3.12-slim
WORKDIR /app
COPY services/api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY services/api/app ./app
COPY services/engine ./engine
# The engine (pure Python here) resolves from /app/engine.
ENV PYTHONPATH=/app/engine
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
