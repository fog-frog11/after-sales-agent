FROM registry-1.docker.io/library/node:22-bookworm-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY web ./web
COPY app/portfolio_cases.json ./app/portfolio_cases.json
RUN npm run build

FROM registry-1.docker.io/library/python:3.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1
COPY requirements.txt requirements-retrieval.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt -r requirements-retrieval.txt
COPY app/__init__.py app/settings.py ./app/
COPY scripts/prepare_retrieval.py ./scripts/prepare_retrieval.py
RUN python -X utf8 -B -m scripts.prepare_retrieval --model-dir /opt/mall-e5 --download
COPY . .
COPY --from=frontend /app/web/commerce-build ./web/commerce-build
RUN useradd --create-home --uid 10001 workbench && mkdir -p /app/.local && chown -R workbench:workbench /app/.local
USER workbench
EXPOSE 8765
CMD ["/bin/sh", "-c", "python -X utf8 -B -m scripts.init_workbench && exec python -X utf8 -B server.py"]
