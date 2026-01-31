FROM python:3.12-slim

WORKDIR /app

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --shell /bin/false app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY static/ static/

RUN chown -R app:app /app
USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://127.0.0.1:8080/api/status', timeout=3)" 2>/dev/null || exit 1

CMD ["sh", "-c", "if [ \"$TRIMARR_RUN\" = \"true\" ]; then python -m app.run; else exec uvicorn app.main:app --host 0.0.0.0 --port 8080; fi"]
