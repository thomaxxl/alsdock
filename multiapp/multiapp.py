#
# gunicorn -w 4 app:application -b 0.0.0.0:5656  --threads 5 --error-logfile - --access-logfile - --reload
#

from werkzeug.middleware.dispatcher import DispatcherMiddleware # use to combine each Flask app into a larger one that is dispatched based on prefix
from flask import Flask, send_from_directory, redirect, send_file, make_response
from admin_api import create_app as create_admin_api_app, User, Api
from safrs import SAFRSAPI as SafrsApi, DB as db
from flask import Flask, abort
from flask_swagger_ui import get_swaggerui_blueprint
from pathlib import Path
from flask import request
import yaml
import importlib
import sys
import multiprocessing
import collections
import argparse
import logging
import tempfile
import shutil
import os

logging.basicConfig()
log = logging.getLogger()

#
# safrs-react-admin flask app: host frontend react files from ./ui
#
def create_sra_app(config_filename=None, ui_path=Path('ui')):
    
    sra_app = Flask("API Logic Server", template_folder='ui/templates')  # templates to load ui/admin/admin.yaml
    if not Path(ui_path).exists():
        log.error(f"UI directory does not exist: {ui_path.resolve()}")
        
    @sra_app.route('/')
    def index():
        return redirect('/admin-app/index.html')

    @sra_app.route('/ui/admin/admin.yaml')
    def admin_yaml():
        response = send_file(f"{ui_path}/admin/admin.yaml", mimetype='text/yaml')
        return response

    @sra_app.route("/admin-app/<path:path>")
    def send_spa(path=None):
        if path == "home.js":
            directory = f"{ui_path}/admin"
        else:
            directory = f'{ui_path}/safrs-react-admin'
        return send_from_directory(directory, path)

    return sra_app

#
# 
#
def create_api(app, host="localhost", port=5000, app_prefix="", api_prefix="/api", models = []):
    """
        Add safrsapi endpoints to app for the specified models
        =>
        * create the swagger blueprint
        * create the api endpoints
    """
    api_spec_url = f"/swagger"
    swaggerui_blueprint = get_swaggerui_blueprint(
        api_prefix, f"{app_prefix}{api_prefix}{api_spec_url}.json", config={"docExpansion": "none", "defaultModelsExpandDepth": -1}
    )
    
    app.register_blueprint(swaggerui_blueprint, url_prefix=f"{api_prefix}")
    api = SafrsApi(app, 
                    host=host,
                    port=port,
                    prefix=api_prefix,
                    swaggerui_blueprint=swaggerui_blueprint,
                    api_spec_url=api_spec_url,
                    custom_swagger={"basePath" : f"{app_prefix}{api_prefix}"} )
    
    for model in models:
        api.expose_object(model)
    api.expose_als_schema(api_root=f"//{host}:{port}{app_prefix}{api_prefix}")
    print(f"Created API: http://{host}:{port}{app_prefix}{api_prefix}")
    return api


def project_2_app(project, host, port):
    """
        Create an app for the project generated by apilogicserver
    """
    api_app = Flask(f"{project}")
    # Some  database timeout
    api_app.config.from_object(f"{project}.config.Config")
    api_app_prefix = f"/{project}"
    api_prefix = "/api"
    api_spec_url = f"/swagger"
    api_url = f"//{host}:{port}{api_app_prefix}{api_prefix}"
    swaggerui_blueprint = get_swaggerui_blueprint(
        api_prefix, f"{api_app_prefix}{api_prefix}{api_spec_url}.json", config={"docExpansion": "none", "defaultModelsExpandDepth": -1}
    )
    
    alsr_py = Path(project) / "api_logic_server_run.py"
    als_spec = importlib.util.spec_from_file_location("api_logic_server_run_proj", alsr_py)
    api_logic_server_run_proj = importlib.util.module_from_spec(als_spec)
    cwd = os.getcwd()
    try:
        als_spec.loader.exec_module(api_logic_server_run_proj)
    except Exception as exc:
        log.exception(exc)
        log.error(f"Failed to load spec: {exc}")
        os.chdir(cwd)
        return None, None
    os.chdir(cwd)
    db = api_logic_server_run_proj.db
    
    exp_py = Path(project) / "api/expose_api_models.py"
    exp_spec = importlib.util.spec_from_file_location("expose_api_models_proj", exp_py)
    expose_api_models_proj = importlib.util.module_from_spec(exp_spec)
    exp_spec.loader.exec_module(expose_api_models_proj)
    expose_models = expose_api_models_proj.expose_models
    
    models_py = Path(project) / "database/models.py"
    mod_spec = importlib.util.spec_from_file_location("models_proj", models_py.resolve())
    models_proj = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(models_proj)
    
    db.init_app(api_app)
    with api_app.app_context():
        db.create_all()
        api_app.register_blueprint(swaggerui_blueprint, url_prefix=f"{api_prefix}")
        expose_models(api_app,
                        HOST=host, 
                        PORT=port, 
                        API_PREFIX=api_prefix,
                        swaggerui_blueprint=swaggerui_blueprint,
                        api_spec_url=api_spec_url,
                        custom_swagger={"basePath" : f"{api_app_prefix}{api_prefix}"})

    @api_app.after_request
    def after_request(response):
        #Enable CORS. Disable it if you don't need CORS or install Cors Libaray
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Max-Age"] = "7200"
        response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS, DELETE, PATCH" # json:api methods
        response.headers["Access-Control-Allow-Headers"] = "Accept, Content-Type, Content-Length, Accept-Encoding, X-CSRF-Token, Authorization"
        return response

    @api_app.route('/')
    def admin_yaml():
        yaml_conf_fn = Path(project) / "ui/admin/admin.yaml"
        if not yaml_conf_fn.is_file():
            log.error("")
            abort(404)
            
        with open(yaml_conf_fn) as yaml_conf_fp:
            conf = yaml.safe_load(yaml_conf_fp.read())
        
        conf["api_root"] = api_url
        response = make_response(yaml.dump(conf))
        response.mimetype = "text"
        return response

    @api_app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()

    log.info(f"API: {api_url}")
    return api_app_prefix, api_app


def create_app(args): 
    #
    # MultiApp initialization: 
    # => Create admin apps and api apps
    #
    host = args.hostname
    port = args.port_ext
    
    #
    # Create the admin api (endpoints for /Users, /Apis)
    #
    admin_app = create_admin_api_app(host=host)
    with admin_app.app_context():
        create_api(admin_app, host=host, port=port, app_prefix="/admin", api_prefix="/api", models = [User,Api])
        apis = admin_app.db.session.query(Api).all()
    
    api_apps= {'/admin': admin_app}
    #for project in args.projects:
    for api in apis:
        sys.path.insert(0, str(Path(api.path).resolve()))
        try:
            api_app_prefix, api_app = project_2_app(api.path, host, port)
            if api_app:
                api_apps[api_app_prefix] = api_app
        except Exception as exc:
            log.exception(exc)
            log.error(f"Failed to create project app! ({api})")
        sys.path.pop(0)
    
    sra_app = create_sra_app(ui_path=os.getenv("SRA_UI_PATH","ui"))
    with sra_app.app_context():
        create_api(sra_app, api_prefix="/api")
    # wsgi application
    print('#'*60)
    print(api_apps)
    application = DispatcherMiddleware(sra_app, api_apps)
    
    return application


def get_args():
    argparser = argparse.ArgumentParser(description="MultiApp")
    argparser.add_argument("-i", "--interface", default="0.0.0.0", help="Interface to run the server on")
    argparser.add_argument("-p", "--port", default=5656, help="Port to run the server on", type=int)
    argparser.add_argument("-H", "--hostname", default="localhost", help="Hostname of the API")
    argparser.add_argument("-P", "--port-ext", default=5656, help="Port of the API", type=int) 
    argparser.add_argument("-w", "--workers", default=(multiprocessing.cpu_count() * 2) + 1, help="Server workers", type=int) 
    argparser.add_argument("-t", "--threads", default=2, help="Server threads", type=int)
    argparser.add_argument("-e", "--error-log", default="-", help="Error Log", type=str)
    argparser.add_argument("-a", "--access-log", default="-", help="Access Log", type=str)
    argparser.add_argument("-v", "--verbose", default=logging.INFO, help="LogLevel (0-50)", type=int)
    argparser.add_argument("-o", "--options", default=None, help="Project options")
    argparser.add_argument("projects", action="store", nargs='*', default=[])
    args = argparser.parse_args()
   
    return args


def main(*args):
    #
    # example gunicorn cli invocation:
    # gunicorn -w 5 "multiapp:main()" -b 0.0.0.0:5656  --threads 1 --error-logfile - --access-logfile - --reload
    #
    Args = collections.namedtuple('args',['hostname', 'port_ext', 'projects'])
    args = Args(hostname='localhost', port_ext=5656, projects=args)
    app = create_app(args)
    return app

if __name__ == '__main__':
    main()
    
    
    

