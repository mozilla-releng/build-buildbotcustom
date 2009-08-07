import sqlalchemy.types as types
try:
    # Python 2.6 has a built-in json module
    import json
except ImportError:
    # Use simplejson if we can't find the built-in module
    import simplejson as json

class JSONColumn(types.TypeDecorator):
    """Simple type that encodes/decodes JSON data in a SQL Text column"""
    impl = types.Text

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return json.loads(value)

    def copy(self):
        return JSONColumn(self.impl.length)
