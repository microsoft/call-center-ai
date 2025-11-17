"""
Security middleware for production deployment.

Includes:
- Rate limiting to prevent abuse
- Security headers (CSP, HSTS, etc.)
- Request size limits
"""

import time
from collections import defaultdict
from http import HTTPStatus
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.helpers.logging import logger


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware to prevent API abuse.

    Implements a simple token bucket algorithm with per-IP tracking.
    For production, consider using Redis for distributed rate limiting.
    """

    def __init__(
        self,
        app: ASGIApp,
        requests_per_minute: int = 60,
        burst_size: int = 100,
    ):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size
        self.buckets: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"tokens": burst_size, "last_update": time.time()}
        )
        # Excluded paths that don't need rate limiting
        self.excluded_paths = {
            "/health/liveness",
            "/health/readiness",
        }

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request, considering proxy headers."""
        # Check for X-Forwarded-For header (from load balancers)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take the first IP in the chain
            return forwarded_for.split(",")[0].strip()

        # Check for X-Real-IP header
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # Fall back to direct client
        if request.client:
            return request.client.host

        return "unknown"

    def _refill_tokens(self, bucket: dict[str, float | int]) -> None:
        """Refill tokens based on time elapsed."""
        now = time.time()
        last_update = float(bucket["last_update"])
        time_passed = now - last_update

        # Calculate tokens to add based on time passed
        tokens_to_add = time_passed * (self.requests_per_minute / 60.0)
        bucket["tokens"] = min(
            self.burst_size,
            float(bucket["tokens"]) + tokens_to_add
        )
        bucket["last_update"] = now

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        """Check rate limit and process request."""
        # Skip rate limiting for excluded paths
        if request.url.path in self.excluded_paths:
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        bucket = self.buckets[client_ip]

        # Refill tokens
        self._refill_tokens(bucket)

        # Check if request can proceed
        if float(bucket["tokens"]) >= 1:
            bucket["tokens"] = float(bucket["tokens"]) - 1
            response = await call_next(request)

            # Add rate limit headers
            response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
            response.headers["X-RateLimit-Remaining"] = str(int(bucket["tokens"]))

            return response
        else:
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "client_ip": client_ip,
                    "path": request.url.path,
                },
            )
            return JSONResponse(
                status_code=HTTPStatus.TOO_MANY_REQUESTS,
                content={
                    "error": {
                        "message": "Rate limit exceeded. Please try again later.",
                        "code": "rate_limit_exceeded",
                    }
                },
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": str(self.requests_per_minute),
                    "X-RateLimit-Remaining": "0",
                },
            )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to all responses.

    Implements OWASP recommendations for secure headers.
    """

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        """Add security headers to response."""
        response = await call_next(request)

        # Content Security Policy - prevents XSS attacks
        # Adjust based on your specific needs
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
            "img-src 'self' data: https:; "
            "font-src 'self' data: https://cdn.jsdelivr.net; "
            "connect-src 'self' wss: https:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )

        # Strict Transport Security - force HTTPS
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )

        # X-Content-Type-Options - prevent MIME sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # X-Frame-Options - prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # X-XSS-Protection - enable XSS filter (legacy browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Referrer-Policy - control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions-Policy - control browser features
        response.headers["Permissions-Policy"] = (
            "geolocation=(), "
            "microphone=(), "
            "camera=(), "
            "payment=(), "
            "usb=(), "
            "magnetometer=(), "
            "gyroscope=(), "
            "accelerometer=()"
        )

        # Remove server header to avoid information disclosure
        response.headers.pop("Server", None)

        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Limit request body size to prevent DoS attacks.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_size: int = 10 * 1024 * 1024,  # 10 MB default
    ):
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        """Check request size and process."""
        # Check Content-Length header
        content_length = request.headers.get("Content-Length")
        if content_length and int(content_length) > self.max_size:
            logger.warning(
                "Request size exceeded",
                extra={
                    "content_length": content_length,
                    "max_size": self.max_size,
                    "path": request.url.path,
                },
            )
            return JSONResponse(
                status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                content={
                    "error": {
                        "message": f"Request body too large. Maximum size is {self.max_size} bytes.",
                        "code": "request_too_large",
                    }
                },
            )

        return await call_next(request)
