"""In-process HTTP fake for arXiv and PDF hosts.

The app calls `httpx.get` and `httpx.stream`. `FakeHTTP` replaces them with real `httpx.Client`
calls over a `MockTransport`, so query encoding, redirects, and status handling behave exactly as
with real httpx, but nothing leaves the process. Every request the app makes is recorded.
"""

import contextlib

import httpx


class FakeHTTP:
    def __init__(self, *replies):
        """Each reply is an httpx.Response, a (status, text) tuple, or an exception to raise.

        Replies are used one per request, in order.
        """
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, tuple):
            status, text = reply
            return httpx.Response(status, text=text)
        return reply

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def get(self, url, **kwargs) -> httpx.Response:
        with self._client() as client:
            return client.get(url, **kwargs)

    @contextlib.contextmanager
    def stream(self, method, url, **kwargs):
        with self._client() as client, client.stream(method, url, **kwargs) as resp:
            yield resp

    def install(self, monkeypatch) -> "FakeHTTP":
        monkeypatch.setattr(httpx, "get", self.get)
        monkeypatch.setattr(httpx, "stream", self.stream)
        return self


def redirect(location: str) -> httpx.Response:
    return httpx.Response(301, headers={"Location": location})
