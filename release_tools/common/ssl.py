import ssl

import certifi


def create_ssl_context(verify_ssl: bool = True):
    if not verify_ssl:
        return False
    return ssl.create_default_context(cafile=certifi.where())
