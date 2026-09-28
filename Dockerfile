FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
RUN useradd --create-home browseruser
COPY --chown=browseruser:browseruser pipeline ./pipeline
COPY --chown=browseruser:browseruser api_server.py .
USER browseruser
RUN python -c "from pipeline.browser_fetch import browser_probe; assert browser_probe(), 'Browser runtime probe failed'"
CMD ["sh", "-c", "exec uvicorn api_server:app --host 0.0.0.0 --port ${PORT:-8000}"]
