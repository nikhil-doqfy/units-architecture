FROM python:3.10.12
ENV PYTHONUNBUFFERED 1

# Install system dependencies for WeasyPrint
RUN apt-get update && apt-get install -y \
    build-essential \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
    
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt