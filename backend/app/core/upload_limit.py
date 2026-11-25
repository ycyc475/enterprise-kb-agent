from starlette.responses import JSONResponse


class UploadLimit:
    """Bound multipart bodies before Starlette spools them, including chunked requests."""

    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not (path.endswith("/documents") or path.endswith("/versions"))
        ):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        body = bytearray()
        oversized = False
        try:
            oversized = int(headers.get(b"content-length", b"0")) > self.max_bytes
        except ValueError:
            oversized = True
        while not oversized:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            oversized = len(body) > self.max_bytes
            if not message.get("more_body", False):
                break
        if oversized:
            return await JSONResponse(
                {
                    "request_id": scope.get("state", {}).get("request_id"),
                    "error": {
                        "code": "FILE_TOO_LARGE",
                        "message": "上传请求超过10MB文件及表单大小上限",
                    },
                },
                status_code=413,
            )(scope, receive, send)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
