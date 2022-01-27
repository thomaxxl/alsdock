from safrs import SAFRSAPI
import safrs
import importlib
import pathlib
import logging as logging
# use absolute path import for easier multi-{app,model,db} support
db_py = pathlib.Path(__file__).parent.parent / "database/models.py"
db_spec =importlib.util.spec_from_file_location("models", db_py.resolve())
models = importlib.util.module_from_spec(db_spec)
db_spec.loader.exec_module(models)

app_logger = logging.getLogger('api_logic_server_app')
app_logger.info("api/expose_api_models.py - endpoint for each table")


def expose_models(app, HOST="localhost", PORT=5656, API_PREFIX="/api", **kwargs):
    """ create SAFRSAPI, exposing each model (note: end point names are table names) """
    app_logger.debug(f"api/expose_api_models -- host = {HOST}, port = {PORT}")
    api = SAFRSAPI(app, host=HOST, port=PORT, prefix = API_PREFIX, **kwargs)
    safrs_log_level = safrs.log.getEffectiveLevel()
    if True or app_logger.getEffectiveLevel() >= logging.INFO:
        safrs.log.setLevel(logging.WARN)  # warn is 20, info 30
    api.expose_object(models.Category)
    api.expose_object(models.Customer)
    api.expose_object(models.CustomerDemographic)
    api.expose_object(models.Department)
    api.expose_object(models.Employee)
    api.expose_object(models.EmployeeAudit)
    api.expose_object(models.EmployeeTerritory)
    api.expose_object(models.Territory)
    api.expose_object(models.Location)
    api.expose_object(models.Order)
    api.expose_object(models.OrderDetail)
    api.expose_object(models.Product)
    api.expose_object(models.Region)
    api.expose_object(models.Shipper)
    api.expose_object(models.Supplier)
    safrs.log.setLevel(safrs_log_level)
    return api
