"""Read-only Xfyun auth/transport probe. Credentials arrive on stdin, never as CLI args or logs.

Accepts JSON {app_id,api_key,api_secret}, or OCR text with APPID/APIKey/APISecret labels.
No audio upload, resource creation, credentials file, redirects or unbounded response output.
"""
import base64
import hashlib
import hmac
import json
import re
import sys
import time
from email.utils import formatdate
from urllib.parse import urlencode

import requests


def credentials_from(text):
    if text.lstrip().startswith("{"):
        value = json.loads(text)
        return {key: value[key].strip() for key in ("app_id", "api_key", "api_secret")}
    result = {}
    for label, key in (("APPID", "app_id"), ("APIKey", "api_key"), ("APISecret", "api_secret")):
        match = re.search(r"\b" + label + r"\s*[:：]?\s*([A-Za-z0-9_\-]+)", text, re.I)
        if not match:
            raise ValueError("Credential labels could not be read")
        result[key] = match.group(1)
    return result


def safe(value, credentials):
    text = str(value)
    for secret in credentials.values():
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"https?://\S+", "[url]", text)
    text = re.sub(r"[A-Za-z0-9+/=_-]{48,}", "[redacted]", text)
    return text[:240]


def probe(credentials, appid_header=False, func="queryFeatureList", group="folo_readonly_auth_probe"):
    host, path = "api.xf-yun.com", "/v1/private/s1aa729d0"
    date = formatdate(usegmt=True)
    original = f"host: {host}\ndate: {date}\nPOST {path} HTTP/1.1"
    signature = base64.b64encode(hmac.new(credentials["api_secret"].encode(), original.encode(), hashlib.sha256).digest()).decode()
    authorization = f'api_key="{credentials["api_key"]}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
    url = f"https://{host}{path}?" + urlencode({"authorization": base64.b64encode(authorization.encode()).decode(), "host": host, "date": date})
    # Intentionally unknown fixed group: a business-level 'not found' proves auth passed.
    body = {"header": {"app_id": credentials["app_id"], "status": 3}, "parameter": {"s1aa729d0": {
        "func": func, "groupId": group, func + "Res": {"encoding": "utf8", "compress": "raw", "format": "json"}}}}
    if func == "createGroup":
        body["parameter"]["s1aa729d0"]["groupName"] = "Folo phone trial"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if appid_header:
        headers["appid"] = credentials["app_id"]
    start = time.monotonic()
    try:
        with requests.Session() as session:
            # Match Android direct access; do not forward credentials through environment proxies.
            session.trust_env = False
            response = session.post(url, headers=headers, data=json.dumps(body).encode(), timeout=(10, 20), allow_redirects=False)
        output = {"func": func, "appid_header": appid_header, "http_status": response.status_code,
                  "elapsed_seconds": round(time.monotonic() - start, 2), "server_date": response.headers.get("Date"),
                  "content_type": response.headers.get("Content-Type")}
        try:
            value = response.json()
            header = value.get("header", {})
            output["header_code"] = header.get("code")
            output["message"] = safe(header.get("message", value.get("message", "")), credentials)
            payload = value.get("payload", {}).get(func + "Res", {}).get("text")
            if payload:
                decoded = json.loads(base64.b64decode(payload))
                if isinstance(decoded, dict):
                    output["business_keys"] = sorted(decoded)
                    if "groupId" in decoded:
                        output["group_id_matches"] = decoded["groupId"] == group
                    for key in ("code", "msg", "message"):
                        if key in decoded:
                            output["business_" + key] = safe(decoded[key], credentials)
                elif isinstance(decoded, list):
                    output["business_list_count"] = len(decoded)
        except (ValueError, TypeError):
            output["json_response"] = False
        return output
    except requests.RequestException as error:
        return {"appid_header": appid_header, "error_class": type(error).__name__, "elapsed_seconds": round(time.monotonic() - start, 2)}


def main():
    credentials = credentials_from(sys.stdin.read())
    print(json.dumps({"credential_lengths": {k: len(v) for k, v in credentials.items()}}), flush=True)
    print(json.dumps(probe(credentials, "--appid-header" in sys.argv), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error_class": type(error).__name__}))
        sys.exit(1)
