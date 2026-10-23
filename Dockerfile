# Public control room on Liara (ui/server.py --public): the page, the AI answer builder and the last pushed snapshot.
# The bot itself is not in this image and never runs here; the machine that runs it pushes its state once a minute.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY ui/requirements-public.txt ui/requirements-public.txt
RUN pip install --no-cache-dir -r ui/requirements-public.txt
COPY config.json config.json
COPY wallexbot wallexbot
COPY ui ui
COPY docs/img docs/img
COPY docs/app/data.json seed/data.json
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["python", "ui/server.py", "--public", "--host", "0.0.0.0", "--port", "8000", "--seed", "seed/data.json"]
