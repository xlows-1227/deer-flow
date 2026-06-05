from __future__ import annotations


class ConnectorError(Exception):
    code = "connector.error"
    status_code = 500

    def __init__(self, message: str, *, recoverable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.recoverable = recoverable


class ConnectorNotFoundError(ConnectorError):
    code = "connector.not_found"
    status_code = 404


class ConnectorDisabledError(ConnectorError):
    code = "connector.disabled"
    status_code = 400


class ConnectorValidationError(ConnectorError):
    code = "connector.validation"
    status_code = 400


class ConnectorAuthorizationError(ConnectorError):
    code = "connector.authorization.denied"
    status_code = 403


class ConnectorSecretError(ConnectorError):
    code = "connector.secret"
    status_code = 400


class ConnectorSqlSafetyError(ConnectorError):
    code = "connector.sql.denied"
    status_code = 400


class ConnectorExecutionError(ConnectorError):
    code = "connector.execution"
    status_code = 502
