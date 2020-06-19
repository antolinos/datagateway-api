import json
import sys
from pathlib import Path


class Config(object):

    def __init__(self):
        config_path = Path(__file__).parent.parent / "config.json"
        with open(config_path) as target:

            self.config = json.load(target)
        target.close()

    def get_backend_type(self):
        try:
            return self.config["backend"]
        except:
            sys.exit("Missing config value, backend")

    def get_db_url(self):
        try:
            return self.config["DB_URL"]
        except:
            sys.exit("Missing config value, DB_URL")

    def get_icat_url(self):
        try:
            return self.config["ICAT_URL"]
        except:
            sys.exit("Missing config value, ICAT_URL")

    def get_log_level(self):
        try:
            return self.config["log_level"]
        except:
            sys.exit("Missing config value, log_level")

    def is_debug_mode(self):
        try:
            return self.config["debug_mode"]
        except:
            sys.exit("Missing config value, debug_mode")

    def is_generate_swagger(self):
        try:
            return self.config["generate_swagger"]
        except:
            sys.exit("Missing config value, generate_swagger")

    def get_host(self):
        try:
            return self.config["host"]
        except:
            sys.exit("Missing config value, host")

    def get_port(self):
        try:
            return self.config["port"]
        except:
            sys.exit("Missing config value, port")


config = Config()
