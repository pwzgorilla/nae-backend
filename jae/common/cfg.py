from jae.common.parser import BaseParser


class Bool(object):
    _boolean_states = {'1': True, '0': False,
                       'yes': True, 'no': False,
                       'true': True, 'false': False,
                       'True': True, 'False': False,
                       'on': True, 'off': False}

    def __new__(cls, value):
        return cls._boolean_states.get(value)


class Int(int):
    def __new__(cls, value):
        return int.__new__(cls, value)


class Str(str):
    def __new__(cls, value):
        return str.__new__(cls, value)


class ConfigParser(BaseParser):
    def __init__(self):
        super(ConfigParser, self).__init__()

        self._opts = {}

    def __call__(self, conf):
        self._parse_config_file(conf)

    def __getattr__(self, key):
        try:
            return self._opts[key]
        except KeyError:
            return None

    def _parse_config_file(self, conf):

        with open(conf) as conf:
            self.parse(conf)

    def assignment(self, key, value):
        self._opts[key] = value


CONF = ConfigParser()


def parse_config():
    try:
        return CONF('/etc/jae/jae.conf')
    except:
        raise


if __name__ == '__main__':
    CONF('/etc/jae/jae.conf')
    for key, value in CONF._opts.items():
        print key, value
