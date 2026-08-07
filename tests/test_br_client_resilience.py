import io
import os
import sys
import tempfile
import unittest
import urllib.error
import ssl
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import br_client  # noqa: E402


class _Socket:
    def getpeername(self):
        return ("93.184.216.34", 443)


class _Response:
    def __init__(self, chunks, headers=None, url="https://cdn.example/asset"):
        self.chunks = iter(chunks)
        self.headers = headers or {}
        self.url = url
        self.fp = mock.Mock(raw=mock.Mock(_sock=_Socket()))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        value = next(self.chunks, b"")
        if isinstance(value, BaseException):
            raise value
        return value

    def geturl(self):
        return self.url


class RequestRetryTests(unittest.TestCase):
    def test_default_endpoint_is_basicrouter_only(self):
        self.assertEqual(br_client.BASICROUTER_BASE_URL, "https://api.basicrouter.ai/api")
        self.assertEqual(br_client.BASE_URL, "https://api.basicrouter.ai/api")
        self.assertNotIn("midwayflow", br_client.BASE_URL)

    def test_paid_post_without_idempotency_key_is_not_retried(self):
        error = urllib.error.HTTPError("https://x", 503, "busy", {}, io.BytesIO(b"busy"))
        with mock.patch.object(br_client.urllib.request, "urlopen", side_effect=error) as opened, \
             mock.patch.object(br_client.time, "sleep"):
            with self.assertRaises(br_client.BRError):
                br_client._request("POST", "/paid", body={}, max_retries=4)
        self.assertEqual(opened.call_count, 1)

    def test_insufficient_credit_5xx_is_terminal_and_not_retried(self):
        error = urllib.error.HTTPError(
            "https://x", 500, "credit", {},
            io.BytesIO(b'{"code":500,"message":"Insufficient credit"}'))
        with mock.patch.object(br_client.urllib.request, "urlopen",
                               side_effect=error) as opened, \
             mock.patch.object(br_client.time, "sleep") as slept:
            with self.assertRaisesRegex(br_client.BRError, "Insufficient credit"):
                br_client._request("POST", "/paid", body={},
                                   idempotency_key="req-credit", max_retries=4)
        self.assertEqual(opened.call_count, 1)
        slept.assert_not_called()

    def test_insufficient_credit_classifier_handles_business_error_text(self):
        error = br_client.BRError(
            "HTTP 500: {\"message\":\"Insufficient credit\"}",
            payload={"message": "Insufficient credit"}, http_status=500)
        self.assertTrue(br_client.is_insufficient_credit(error))

    def test_connection_error_is_wrapped_without_retry_for_unkeyed_post(self):
        with mock.patch.object(br_client.urllib.request, "urlopen",
                               side_effect=ConnectionResetError("reset")) as opened:
            with self.assertRaisesRegex(br_client.BRError, "network error: reset"):
                br_client._request("POST", "/paid", body={}, max_retries=4)
        self.assertEqual(opened.call_count, 1)

    def test_tls_runtime_error_uses_curl_fallback_without_retrying_urllib(self):
        tls_error = urllib.error.URLError(
            ssl.SSLEOFError(8, "EOF occurred in violation of protocol"))
        expected = {"code": 200, "data": {"ok": True}}
        with mock.patch.object(br_client.urllib.request, "urlopen",
                               side_effect=tls_error) as opened, \
             mock.patch.object(br_client, "_curl_json_request",
                               return_value=expected) as fallback:
            result = br_client._request("POST", "/v1/chat/completions",
                                        api_key="sk-secret", body={"x": 1})
        self.assertEqual(result, expected)
        self.assertEqual(opened.call_count, 1)
        args = fallback.call_args.args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[2]["Authorization"], "Bearer sk-secret")

    def test_curl_fallback_keeps_secret_out_of_process_arguments(self):
        observed = {}

        def fake_run(command, **_kwargs):
            observed["command"] = command
            config_path = command[command.index("--config") + 1]
            observed["mode"] = os.stat(config_path).st_mode & 0o777
            with open(config_path, encoding="utf-8") as handle:
                observed["config"] = handle.read()
            response_path = command[command.index("--output") + 1]
            with open(response_path, "w", encoding="utf-8") as handle:
                handle.write('{"code":200,"data":{}}')
            return mock.Mock(returncode=0, stdout=b"200", stderr=b"")

        with mock.patch.object(br_client.shutil, "which", return_value="/usr/bin/curl"), \
             mock.patch.object(br_client.subprocess, "run", side_effect=fake_run):
            result = br_client._curl_json_request(
                "POST", "https://api.basicrouter.ai/api/v1/chat/completions",
                {"Authorization": "Bearer sk-secret", "Content-Type": "application/json"},
                b'{}', 30)
        self.assertEqual(result["code"], 200)
        self.assertNotIn("sk-secret", " ".join(observed["command"]))
        self.assertIn("sk-secret", observed["config"])
        self.assertEqual(observed["mode"], 0o600)

    def test_keyed_post_retries_with_same_header(self):
        responses = [ConnectionResetError("reset"),
                     _Response([b'{"code": 200, "data": {}}'])]
        headers = []

        def open_request(request, **_kwargs):
            headers.append(request.get_header("Idempotency-key"))
            value = responses.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value

        with mock.patch.object(br_client.urllib.request, "urlopen", side_effect=open_request), \
             mock.patch.object(br_client.time, "sleep"):
            br_client._request("POST", "/paid", body={}, idempotency_key="req-1")
        self.assertEqual(headers, ["req-1", "req-1"])

    def test_create_apis_forward_stable_or_explicit_request_id(self):
        calls = []
        bodies = []

        def request(_method, path, **kwargs):
            calls.append((path, kwargs["idempotency_key"]))
            bodies.append(kwargs.get("body") or {})
            if path == "/ai/createImage":
                return {"code": 200, "data": {"imageUrls": ["https://x/image"]}}
            return {"code": 200, "data": {"taskId": "task-1"}}

        with mock.patch.object(br_client, "_request", side_effect=request), \
             mock.patch.object(br_client.uuid, "uuid4") as uuid4:
            uuid4.return_value.hex = "generated-id"
            br_client.create_image("sk-x", "image")
            br_client.create_image_generation("sk-x", "image", request_id="image-id")
            br_client.create_video("sk-x", "video", request_id="video-id")
        self.assertEqual(calls, [
            ("/ai/createImage", "generated-id"),
            ("/v1/image-generations", "image-id"),
            ("/v1/video-generations", "video-id"),
        ])
        self.assertEqual(bodies[-1]["model"], "seedance-2.0-VS-white")


class AtomicDownloadTests(unittest.TestCase):
    def setUp(self):
        self.dns = mock.patch.object(
            br_client.socket, "getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
        self.dns.start()

    def tearDown(self):
        self.dns.stop()

    def test_rejects_local_and_private_download_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "asset.bin")
            for url in ("file:///etc/hosts", "http://example.com/a",
                        "https://127.0.0.1/a", "https://169.254.169.254/a"):
                with self.subTest(url=url):
                    with self.assertRaisesRegex(br_client.BRError, "DOWNLOAD_URL_BLOCKED"):
                        br_client.download(url, target, max_retries=0)

    def test_normalizes_ipv4_mapped_ipv6_peer(self):
        self.assertTrue(br_client._peer_is_acceptable("::ffff:93.184.216.34"))

    def test_nonpublic_peer_requires_explicit_opt_in(self):
        self.assertFalse(br_client._peer_is_acceptable("10.0.0.5"))
        self.assertTrue(br_client._peer_is_acceptable("10.0.0.5", allow_nonpublic=True))

    def test_model_not_found_error_is_classified_for_fallback(self):
        error = br_client.BRError(
            "HTTP 400: Model not found: dreamina-seedance-2-0-260128",
            http_status=400)
        self.assertTrue(br_client.is_video_model_not_found(error))

    def test_explicit_https_proxy_peer_is_allowed_but_unconfigured_private_peer_is_blocked(self):
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.local:8080"}, clear=False), \
                mock.patch.object(br_client.socket, "getaddrinfo", return_value=[
                    (2, 1, 6, "", ("10.0.0.5", 8080))]):
            self.assertTrue(br_client._peer_is_acceptable("10.0.0.5"))
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": ""}, clear=False):
            self.assertFalse(br_client._peer_is_acceptable("10.0.0.5"))

    def test_all_proxy_peer_is_allowed_for_download_peer_validation(self):
        with mock.patch.dict(os.environ, {"ALL_PROXY": "http://proxy.local:7890"}, clear=False), \
                mock.patch.object(br_client.socket, "getaddrinfo", return_value=[
                    (2, 1, 6, "", ("10.0.0.8", 7890))]):
            self.assertTrue(br_client._peer_is_acceptable("10.0.0.8"))

    def test_stream_without_length_stops_at_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "asset.bin")
            response = _Response([b"12345", b"67890", b""])
            with mock.patch.object(br_client, "_open_download", return_value=response):
                with self.assertRaisesRegex(br_client.BRError, "DOWNLOAD_TOO_LARGE"):
                    br_client.download("https://cdn.example/asset", target,
                                       max_retries=0, max_bytes=8)

    def test_partial_read_failure_preserves_old_file_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = os.path.join(directory, "asset.bin")
            with open(dest, "wb") as f:
                f.write(b"old")
            response = _Response([b"partial", ConnectionResetError("reset")])
            with mock.patch.object(br_client, "_open_download", return_value=response):
                with self.assertRaises(br_client.BRError):
                    br_client.download("https://x/asset", dest, max_retries=0)
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"old")
            self.assertEqual(os.listdir(directory), ["asset.bin"])

    def test_fsync_failure_preserves_old_file_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = os.path.join(directory, "asset.bin")
            with open(dest, "wb") as f:
                f.write(b"old")
            with mock.patch.object(br_client, "_open_download",
                                   return_value=_Response([b"new", b""])), \
                 mock.patch.object(br_client.os, "fsync", side_effect=OSError("disk failure")):
                with self.assertRaisesRegex(OSError, "disk failure"):
                    br_client.download("https://x/asset", dest)
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"old")
            self.assertEqual(os.listdir(directory), ["asset.bin"])

    def test_success_fsyncs_before_atomic_replace(self):
        events = []
        original_replace = os.replace
        with tempfile.TemporaryDirectory() as directory:
            dest = os.path.join(directory, "asset.bin")
            with mock.patch.object(br_client, "_open_download",
                                   return_value=_Response([b"new", b""])), \
                 mock.patch.object(br_client.os, "fsync",
                                   side_effect=lambda _fd: events.append("fsync")), \
                 mock.patch.object(br_client.os, "replace",
                                   side_effect=lambda src, dst: (events.append("replace"),
                                                                 original_replace(src, dst))[1]):
                br_client.download("https://x/asset", dest)
            self.assertEqual(events, ["fsync", "replace"])
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"new")

    def test_replace_failure_preserves_old_file_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = os.path.join(directory, "asset.bin")
            with open(dest, "wb") as f:
                f.write(b"old")
            with mock.patch.object(br_client, "_open_download",
                                   return_value=_Response([b"new", b""])), \
                 mock.patch.object(br_client.os, "replace", side_effect=OSError("replace failure")):
                with self.assertRaisesRegex(OSError, "replace failure"):
                    br_client.download("https://x/asset", dest)
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"old")
            self.assertEqual(os.listdir(directory), ["asset.bin"])

    def test_network_retry_discards_partial_temp_before_success(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = os.path.join(directory, "asset.bin")
            responses = [
                _Response([b"partial", ConnectionResetError("reset")]),
                _Response([b"complete", b""]),
            ]
            with mock.patch.object(br_client, "_open_download",
                                   side_effect=responses) as opened, \
                 mock.patch.object(br_client.time, "sleep"):
                br_client.download("https://x/asset", dest, max_retries=1)
            self.assertEqual(opened.call_count, 2)
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"complete")
            self.assertEqual(os.listdir(directory), ["asset.bin"])

    def test_clean_eof_shorter_than_content_length_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = os.path.join(directory, "asset.bin")
            with open(dest, "wb") as f:
                f.write(b"old")
            response = _Response([b"short", b""], {"Content-Length": "10"})
            with mock.patch.object(br_client, "_open_download", return_value=response):
                with self.assertRaisesRegex(br_client.BRError, "truncated download"):
                    br_client.download("https://x/asset", dest, max_retries=0)
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"old")


class HostImageCacheTests(unittest.TestCase):
    def setUp(self):
        br_client._HOST_IMAGE_CACHE.clear()

    def tearDown(self):
        br_client._HOST_IMAGE_CACHE.clear()

    def test_content_change_at_same_path_misses_cache_and_key_hides_api_key(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
            f.write(b"\x89PNG\r\n\x1a\n" + b"first" * 8)
        try:
            with mock.patch.object(br_client, "create_image",
                                   side_effect=AssertionError("legacy sync path used")), \
                 mock.patch.object(br_client, "create_image_generation",
                                   side_effect=["task-one", "task-two"]) as create, \
                 mock.patch.object(br_client, "wait_image_generation",
                                   side_effect=[["https://x/one"], ["https://x/two"]]):
                self.assertEqual(br_client.host_image("sk-secret", path), "https://x/one")
                self.assertEqual(br_client.host_image("sk-secret", path), "https://x/one")
                with open(path, "wb") as f:
                    f.write(b"\x89PNG\r\n\x1a\n" + b"second" * 8)
                self.assertEqual(br_client.host_image("sk-secret", path), "https://x/two")
            self.assertEqual(create.call_count, 2)
            self.assertFalse(any("sk-secret" in repr(key)
                                 for key in br_client._HOST_IMAGE_CACHE))
        finally:
            os.unlink(path)

    def test_timeout_retries_same_hosting_request_with_extended_timeout(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            image.write(b"\x89PNG\r\n\x1a\n" + b"image" * 8)
            image.flush()
            with mock.patch.object(
                    br_client, "create_image",
                    side_effect=AssertionError("legacy sync path used")), \
                    mock.patch.object(
                        br_client, "create_image_generation",
                        return_value="task-host") as create, \
                    mock.patch.object(
                        br_client, "wait_image_generation",
                        side_effect=[br_client.BRError("timeout after 180s"),
                                     ["https://x/hosted.png"]]) as wait:
                result = br_client.host_image("sk-secret", image.name, timeout=600)
        self.assertEqual(result, "https://x/hosted.png")
        self.assertEqual(create.call_count, 1)
        self.assertEqual(wait.call_count, 2)
        self.assertEqual(wait.call_args_list[0].args[1], "task-host")
        self.assertEqual(wait.call_args_list[1].args[1], "task-host")


class AnalyzeImageTimeoutTests(unittest.TestCase):
    def test_hosting_failure_does_not_switch_to_data_url(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            image.write(b"\x89PNG\r\n\x1a\n" + b"image" * 8)
            image.flush()
            with mock.patch.object(br_client, "pick_vision_model", return_value="vision"), \
                    mock.patch.object(br_client, "to_image_ref",
                                      side_effect=br_client.BRError("host timeout")) as image_ref, \
                    mock.patch.object(br_client, "chat_stream") as chat:
                with self.assertRaisesRegex(br_client.BRError, "未切换或重绘"):
                    br_client.analyze_image("sk-x", image.name, "inspect")
        image_ref.assert_called_once_with(
            image.name, api_key="sk-x", prefer_hosted=True, host_timeout=600)
        chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
