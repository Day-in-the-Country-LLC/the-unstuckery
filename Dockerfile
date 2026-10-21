FROM python:3.12-slim
WORKDIR /app
COPY requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements-cloud.txt && useradd --uid 10001 --create-home unstuckery
COPY *.py ./
COPY static/ ./static/
COPY draft-plan.json backlog-config.json ./
USER unstuckery
CMD ["python", "server.py", "--data-dir", "/data", "--cloud-config", "/private/cloud-config.json", "--ai-review", "--review-config", "/private/review.env"]
