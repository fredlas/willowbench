# gunicorn_config.py
import logging

bind = "[::]:8008"
workers = 1 # os.cpu_count() * 2 + 1 TODO
accesslog = "/home/admin/gunicorn_access.log"
errorlog = "/home/admin/gunicorn_error.log"
loglevel = "info"
timeout = 10

class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        # The 'U' format specifier in Gunicorn's access log format gives the URL path without query string.
        # We check this 'U' argument which is part of record.args (a dictionary/tuple Gunicorn uses for logging).
        # The health check path is '/health'
        if 'U' in record.args and record.args['U'] == '/health':
            return False  # Do not log this record
        return True  # Log all other records

def on_starting(_server):
    # Get the gunicorn.access logger
    access_logger = logging.getLogger("gunicorn.access")
    # Add our custom filter
    access_logger.addFilter(HealthCheckFilter())
