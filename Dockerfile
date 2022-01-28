FROM python:3.8-alpine
WORKDIR /app
RUN apk add --no-cache gcc musl-dev linux-headers libffi-dev curl tar

# Python dependencies
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
COPY . .
WORKDIR ApiLogicServer
RUN pip install -e .

# admin-app frontend files : reactjs code and admin.yaml
WORKDIR /app/ui
RUN curl -L https://github.com/thomaxxl/safrs-react-admin/archive/master.tar.gz | tar xfz -
RUN mv safrs-react-admin-master/build/ /app/ui/safrs-react-admin
WORKDIR /app/ui/admin
COPY multiapp/ui/admin/admin.yaml .

EXPOSE 5656
WORKDIR /mount/multiapp
CMD ["sh", "run.sh"]
