# Python image for the API and both workers - same image, different command,
# so they cannot drift apart in dependencies.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The embedding model downloads on first use; baking it into the image keeps
# cold starts from paying for it (and from failing if egress is restricted).
RUN python -c "from corpus.embed import embed_text; embed_text('warmup')"

EXPOSE 8000

# Overridden by the worker services in docker-compose / the host's config.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
