FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[gcs]'

# Cloud Run supplies PORT; hadro reads it when HADRO_PORT is not set.
ENV HADRO_HOST=0.0.0.0 \
    HADRO_DATA=/data

CMD ["hadro"]
