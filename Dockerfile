FROM python:3.8-alpine
WORKDIR /app
RUN apk add --no-cache gcc musl-dev linux-headers libffi-dev
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
COPY . .
WORKDIR ApiLogicServer
RUN pip install -e .
EXPOSE 5656
WORKDIR /mount/multiapp
CMD ["sh", "./run.sh"]
