"""Bounded network jobs. Secrets cross a private process pipe, never a log/file."""
from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path
import signal
import time
from urllib.parse import urljoin, urlsplit

import requests
from requests.auth import AuthBase, HTTPBasicAuth

EDL_HOST = "urs.earthdata.nasa.gov"
DAP_HOST = "opendap.earthdata.nasa.gov"
TOKEN_URL = f"https://{EDL_HOST}/api/users/find_or_create_token"
USER_AGENT = "GeoPanel-EO-Chicago/0.1-r6 SoftwareX-acquisition-audit"


class JobError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Cancelled(JobError):
    def __init__(self):
        super().__init__("CANCELLED", "Run cancelled; completed files were preserved.")


class NoImplicitAuth(AuthBase):
    """Do not let Requests implicitly attach unrelated netrc entries."""
    def __call__(self, request):
        request.headers.pop("Authorization", None)
        return request


class ScopedBearer(AuthBase):
    """Explicit auth also prevents a netrc entry overriding the Bearer header."""
    def __init__(self, token):
        self.token = token

    def __call__(self, request):
        url = urlsplit(request.url)
        if url.scheme != "https" or url.hostname != DAP_HOST or url.port not in (None, 443):
            raise JobError("AUTH_SCOPE", "A token may only be sent to the official HTTPS OPeNDAP host.")
        request.headers["Authorization"] = f"Bearer {self.token}"
        return request


def check_status(status, *, account=False):
    if 200 <= status < 300:
        return
    if status == 401:
        raise JobError("ACCOUNT_REJECTED" if account else "TOKEN_REJECTED",
                       "Earthdata returned HTTP 401. Check the account/token in your browser.")
    if status == 403:
        raise JobError("ACCESS_FORBIDDEN", "HTTP 403: access was refused; check Earthdata account/application permissions.")
    if status == 404:
        raise JobError("SOURCE_NOT_FOUND", "HTTP 404: the configured data endpoint was not found.")
    if status == 429:
        raise JobError("RATE_LIMITED", "HTTP 429: the service requested fewer requests. Retry later.")
    if status >= 500:
        raise JobError("SERVICE_ERROR", f"Remote service returned HTTP {status}; this is not proof of a password error.")
    raise JobError("HTTP_ERROR", f"Remote service returned HTTP {status}.")


def token_exchange(session, credentials):
    # Basic credentials are sent ONLY to this fixed HTTPS Earthdata endpoint.
    with session.post(TOKEN_URL, auth=HTTPBasicAuth(*credentials), stream=True,
                      allow_redirects=False, timeout=(15, 45)) as response:
        check_status(response.status_code, account=True)
        body = bytearray()
        for block in response.iter_content(8192):
            body.extend(block)
            if len(body) > 65536:
                raise JobError("AUTH_RESPONSE_INVALID", "Earthdata token response exceeded the size limit.")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            raise JobError("AUTH_RESPONSE_INVALID", "Earthdata did not return a valid token response.") from None
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token or any(c.isspace() for c in token):
        raise JobError("AUTH_RESPONSE_INVALID", "Earthdata response has no usable access_token.")
    return {"access_token": token}  # private pipe only; caller never serializes this dict


def fetch(session, job, connection):
    url = job["url"]
    token = job.get("token")
    auth = ScopedBearer(token) if token else NoImplicitAuth()
    original = urlsplit(url)
    if original.scheme != "https" and original.hostname not in ("127.0.0.1", "localhost"):
        raise JobError("HTTPS_REQUIRED", "Data downloads require HTTPS.")
    for attempt in range(6):
        response = session.get(url, auth=auth, stream=True, allow_redirects=False,
                               timeout=(15, job.get("read_timeout", 90)))
        if response.status_code not in (301, 302, 303, 307, 308):
            break
        target = urljoin(url, response.headers.get("Location", ""))
        response.close()
        destination = urlsplit(target)
        if (destination.scheme, destination.netloc) != (original.scheme, original.netloc):
            # Never forward a credential to another host, even on a login redirect.
            raise JobError("AUTH_REDIRECT" if destination.hostname == EDL_HOST else "UNEXPECTED_REDIRECT",
                           "The server redirected to another host. No credential was forwarded.")
        url = target
    else:
        raise JobError("REDIRECT_LIMIT", "Too many redirects from the data endpoint.")
    with response:
        check_status(response.status_code)
        connection.send({"event": "http", "status": response.status_code})
        limit = job["max_bytes"]
        length = response.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > limit:
            raise JobError("SIZE_LIMIT", "The response is larger than the requested microcase size limit.")
        received = 0
        last_notice = time.monotonic()
        with Path(job["partial"]).open("wb") as stream:
            for block in response.iter_content(65536):
                if not block:
                    continue
                received += len(block)
                if received > limit:
                    raise JobError("SIZE_LIMIT", "The response exceeded the configured size limit.")
                stream.write(block)
                if time.monotonic() - last_notice >= 2:
                    connection.send({"event": "progress", "bytes": received})
                    last_notice = time.monotonic()
        if not received:
            raise JobError("EMPTY_RESPONSE", "The data endpoint returned an empty file.")
        # Cookies, redirect URLs and HTTP response bodies are never returned as errors.
        return {"http_status": response.status_code, "size_bytes": received,
                "content_type": response.headers.get("Content-Type", "").split(";")[0]}


def worker(connection, job):
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # parent owns Ctrl+C and termination
    try:
        with requests.Session() as session:
            session.headers["User-Agent"] = USER_AGENT
            if job["kind"] == "token":
                result = token_exchange(session, job["credentials"])
            else:
                result = fetch(session, job, connection)
        connection.send({"event": "done", "result": result})
    except JobError as exc:
        connection.send({"event": "error", "code": exc.code, "message": str(exc)})
    except requests.exceptions.SSLError:
        connection.send({"event": "error", "code": "TLS_ERROR", "message": "TLS certificate verification failed. Check system time, proxy and trusted certificates."})
    except requests.exceptions.Timeout:
        connection.send({"event": "error", "code": "NETWORK_TIMEOUT", "message": "The network request timed out; no account conclusion can be drawn."})
    except requests.exceptions.ProxyError:
        connection.send({"event": "error", "code": "PROXY_ERROR", "message": "The configured proxy could not complete this request."})
    except requests.exceptions.RequestException:
        connection.send({"event": "error", "code": "NETWORK_ERROR", "message": "Network/DNS/connection failure. Check connectivity to the official hosts."})
    except Exception as exc:
        connection.send({"event": "error", "code": "WORKER_ERROR", "message": f"Network worker failed ({type(exc).__name__}); credential values are omitted."})
    finally:
        connection.close()


def run_job(job, *, seconds, label, stop_file=None, emit=print, cancel_event=None):
    """A spawned worker can be terminated even when DNS/SSL/read is stuck.

    The deadline covers process start, headers, redirects and streaming. Parent
    cleanup has an additional bounded join/kill grace period of at most 4 sec.
    """
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(sender, job), daemon=True)
    start = time.monotonic()
    last_notice = start
    bytes_received = 0
    done = False
    emit(f"{label}: starting; total deadline {seconds}s. Ctrl+C or STOP_DOWNLOAD.bat cancels.")
    try:
        process.start()
        sender.close()
        while True:
            if (stop_file and Path(stop_file).exists()) or (cancel_event and cancel_event.is_set()):
                raise Cancelled()
            now = time.monotonic()
            if now - start >= seconds:
                raise JobError("TOTAL_TIMEOUT", f"{label} reached its {seconds}s deadline. The network worker was stopped.")
            if receiver.poll(0.15):
                try:
                    message = receiver.recv()
                except EOFError:
                    raise JobError("WORKER_EXIT", "Network worker exited before completing the request.") from None
                event = message["event"]
                if event == "done":
                    done = True
                    result = message["result"]
                    result["elapsed_seconds"] = round(time.monotonic() - start, 3)
                    emit(f"{label}: complete ({result['elapsed_seconds']:.1f}s).")
                    return result
                if event == "error":
                    raise JobError(message["code"], message["message"])
                if event == "progress":
                    bytes_received = message["bytes"]
                elif event == "http":
                    emit(f"{label}: HTTP {message['status']}; receiving data.")
            if now - last_notice >= 10:
                emit(f"{label}: active, {now-start:.0f}s elapsed, {bytes_received:,} bytes reported.")
                last_notice = now
            if not process.is_alive() and not receiver.poll():
                raise JobError("WORKER_EXIT", "Network worker stopped without a result.")
    except KeyboardInterrupt:
        raise Cancelled() from None
    finally:
        if process.pid is not None:
            if process.is_alive() and not done:
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        sender.close()
        receiver.close()
        if not done and job.get("partial"):
            Path(job["partial"]).unlink(missing_ok=True)
