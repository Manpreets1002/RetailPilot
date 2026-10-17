# One Dockerfile, one image per service. Build a single service with:
#   docker build --target backend -t retailpilot-backend .
# Targets: crawler, mcp, backend, frontend. Each installs only its own requirements file.

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src
WORKDIR /app


# Also used for the queue workers: `python worker.py`. Includes headless Chromium for
# the browser fallback (bot check / "Deliver to" pincode).
FROM base AS crawler
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY requirement-crawler.txt ./
RUN pip install -r requirement-crawler.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY src/retailpilot/__init__.py ./src/retailpilot/
COPY src/retailpilot/config ./src/retailpilot/config
COPY amazon_ecomm_crawler ./amazon_ecomm_crawler
WORKDIR /app/amazon_ecomm_crawler
ENTRYPOINT ["python"]
CMD ["crawler.py", "--help"]


FROM base AS mcp
COPY requirement-mcp.txt ./
RUN pip install -r requirement-mcp.txt
COPY src/retailpilot/__init__.py ./src/retailpilot/
COPY src/retailpilot/config ./src/retailpilot/config
COPY src/retailpilot/mcp ./src/retailpilot/mcp
COPY amazon_ecomm_crawler/cities.json ./amazon_ecomm_crawler/
CMD ["python", "src/retailpilot/mcp/mcp_server.py"]


FROM base AS backend
COPY requirement-mcp.txt requirement-backend.txt ./
RUN pip install -r requirement-backend.txt
COPY src/retailpilot/__init__.py ./src/retailpilot/
COPY src/retailpilot/config ./src/retailpilot/config
COPY src/retailpilot/agent ./src/retailpilot/agent
COPY src/retailpilot/backend ./src/retailpilot/backend
COPY src/retailpilot/mcp ./src/retailpilot/mcp
COPY amazon_ecomm_crawler/cities.json ./amazon_ecomm_crawler/
EXPOSE 8000
CMD ["uvicorn", "retailpilot.backend.agent_route:app", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS frontend
COPY requirement-frontend.txt ./
RUN pip install -r requirement-frontend.txt
COPY src/retailpilot/__init__.py ./src/retailpilot/
COPY src/retailpilot/config ./src/retailpilot/config
COPY src/retailpilot/frontend ./src/retailpilot/frontend
WORKDIR /app/src/retailpilot/frontend
RUN DJANGO_DEBUG=0 DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput
EXPOSE 8080
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8080 --workers 2 --access-logfile -"]
