FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Upstream 2.0.7 registers ~30 tools; entrypoint.py keeps open_presentation and
# extract_presentation_text only and refuses to start if the registry layout changes.
COPY entrypoint.py /app/entrypoint.py
RUN python -c "import runpy, sys; sys.argv=['check']; runpy.run_path('/app/entrypoint.py')"
RUN useradd --system --uid 10001 buddy
USER buddy
ENV HOST=0.0.0.0 PORT=8000 PPTX_ROOT=/presentations
EXPOSE 8000
ENTRYPOINT ["python", "/app/entrypoint.py"]
