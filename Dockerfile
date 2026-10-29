FROM python:3.12-slim

# Prevent Python from buffering stdout/stderr and writing pyc files
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install runtime and build dependencies for PostgreSQL client and compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install pinned Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . /app/

# Expose standard application port
EXPOSE 8000

# Default command to run production WSGI application
CMD ["gunicorn", "wallet_project.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]

