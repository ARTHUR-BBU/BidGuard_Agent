import re

from starlette.formparsers import MultiPartException
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.documents.storage import MAX_UPLOAD_BYTES

RAW_UPLOAD_BODY_LIMIT = MAX_UPLOAD_BYTES + 1024 * 1024
_PROJECT_UPLOAD = re.compile(r"/api/projects/[^/]+/documents/?")


class _BodyTooLarge(MultiPartException):
    def __init__(self) -> None:
        super().__init__("Request body too large")


class UploadBodyLimitMiddleware:
    """Count raw bytes before each chunk reaches the multipart parser."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not (
                scope["path"].rstrip("/") == "/api/company-evidence"
                or _PROJECT_UPLOAD.fullmatch(scope["path"])
            )
        ):
            await self.app(scope, receive, send)
            return

        rejection = JSONResponse({"detail": "Request body too large"}, status_code=413)
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    declared_length = int(value)
                except ValueError:
                    continue
                if declared_length > RAW_UPLOAD_BODY_LIMIT:
                    await rejection(scope, receive, send)
                    return

        total = 0
        finished = False
        oversized = False
        disconnected = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal total, finished, oversized, disconnected
            if oversized:
                raise _BodyTooLarge
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > RAW_UPLOAD_BODY_LIMIT:
                    oversized = True
                    raise _BodyTooLarge
                finished = not message.get("more_body", False)
            elif message["type"] == "http.disconnect":
                disconnected = finished = True
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                # Validation can reject a non-multipart request without reading it.
                # Check its remaining bytes before forwarding any response headers.
                while not finished and not oversized:
                    await limited_receive()
                if oversized:
                    raise _BodyTooLarge
                if disconnected:
                    return
                response_started = True
            if not disconnected:
                await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            if not response_started and not disconnected:
                await rejection(scope, receive, send)
        except ClientDisconnect:
            if not disconnected:
                raise
