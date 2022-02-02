FROM python:3.8-alpine
WORKDIR /app
RUN apk add --no-cache gcc g++ musl-dev linux-headers libffi-dev curl tar unixodbc-dev gnupg

# Python dependencies
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
COPY . .
RUN curl -L https://github.com/valhuber/ApiLogicServer/archive/master.tar.gz | tar xfz -
WORKDIR ApiLogicServer-main
RUN pip install -e .

# admin-app frontend files : reactjs code and admin.yaml
WORKDIR /app/ui
RUN curl -L https://github.com/thomaxxl/safrs-react-admin/archive/master.tar.gz | tar xfz -
RUN mv safrs-react-admin-master/build/ /app/ui/safrs-react-admin
WORKDIR /app/ui/admin
COPY multiapp/ui/admin/admin.yaml .

# PyODBC
RUN curl -O https://download.microsoft.com/download/e/4/e/e4e67866-dffd-428c-aac7-8d28ddafb39b/msodbcsql17_17.8.1.1-1_amd64.apk
RUN curl -O https://download.microsoft.com/download/e/4/e/e4e67866-dffd-428c-aac7-8d28ddafb39b/mssql-tools_17.8.1.1-1_amd64.apk
RUN curl -O https://download.microsoft.com/download/e/4/e/e4e67866-dffd-428c-aac7-8d28ddafb39b/msodbcsql17_17.8.1.1-1_amd64.sig
RUN curl -O https://download.microsoft.com/download/e/4/e/e4e67866-dffd-428c-aac7-8d28ddafb39b/mssql-tools_17.8.1.1-1_amd64.sig
RUN curl https://packages.microsoft.com/keys/microsoft.asc  | gpg --import -
RUN gpg --verify msodbcsql17_17.8.1.1-1_amd64.sig msodbcsql17_17.8.1.1-1_amd64.apk
RUN gpg --verify mssql-tools_17.8.1.1-1_amd64.sig mssql-tools_17.8.1.1-1_amd64.apk
RUN apk add --allow-untrusted msodbcsql17_17.8.1.1-1_amd64.apk
RUN apk add --allow-untrusted mssql-tools_17.8.1.1-1_amd64.apk

####
EXPOSE 5656
WORKDIR /mount/multiapp
CMD ["sh", "run.sh"]
