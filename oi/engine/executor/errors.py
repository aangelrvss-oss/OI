"""Taxonomía de errores técnicos. Un error técnico se registra SIEMPRE como tal; nunca se convierte en 'criterio UNKNOWN' sin dejar rastro."""
import re

CODES = ("PROVIDER_UNAVAILABLE", "AUTH_ERROR", "RATE_LIMIT", "BUDGET_BLOCKED", "EMPTY_RESULT", "MALFORMED_RESULT", "DUPLICATE",
         "TIMEOUT", "UNKNOWN_ERROR", "OPERATION_NOT_ALLOWED", "AWAITING_EXTERNAL_RESPONSE", "INVALID_PARAMETERS")


class ProviderError(Exception):
    def __init__(self, code, message="", http_status=None):
        super().__init__(message); self.code, self.message, self.http_status = code, message, http_status


class AwaitingExternalResponse(Exception):
    def __init__(self, request):
        super().__init__("respuesta externa pendiente"); self.request = request


def classify(exc):
    """Clasificación determinista de un fallo del proveedor/transporte."""
    if isinstance(exc, ProviderError) and exc.code in CODES: return exc.code
    if isinstance(exc, TimeoutError): return "TIMEOUT"
    m = str(exc).lower(); st = getattr(exc, "http_status", None)
    if st in (401, 403) or any(k in m for k in ("unauthorized", "invalid api key", "authentication", "forbidden")): return "AUTH_ERROR"
    if st == 429 or "rate limit" in m or "too many requests" in m: return "RATE_LIMIT"
    if "invalid params" in m or "-32602" in m: return "INVALID_PARAMETERS"
    if any(k in m for k in ("api_inaccessible", "endpoint_access_denied", "unavailable", "503", "502")): return "PROVIDER_UNAVAILABLE"
    if "timeout" in m or "timed out" in m: return "TIMEOUT"
    return "UNKNOWN_ERROR"


_SECRET_KEYS = re.compile(r"(api[_-]?key|apikey|token|secret|authorization|password|x-api-key|bearer|cookie|session)", re.I)
_SECRET_INLINE = re.compile(r"(?i)(api[_-]?key|apikey|token|secret|authorization|password|x-api-key)(\s*[:=]\s*)([^\s,;&\"']+)")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")


def sanitize(obj):
    """Elimina secretos de cualquier estructura antes de registrarla o hashearla. Nunca guarda API keys."""
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if _SECRET_KEYS.search(str(k)) else sanitize(v)) for k, v in obj.items()}
    if isinstance(obj, list): return [sanitize(x) for x in obj]
    if isinstance(obj, str): return _SECRET_INLINE.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", _BEARER.sub("Bearer [REDACTED]", obj))   # Bearer PRIMERO: si no, se redacta la palabra y queda el token
    return obj
