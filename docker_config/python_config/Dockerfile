FROM python:3.10.12
ENV PYTHONUNBUFFERED 1

RUN apt-get update && apt-get install -y wkhtmltopdf
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt