class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, clear_refresh: bool = False):
        self.status = status
        self.code = code
        self.message = message
        self.clear_refresh = clear_refresh
