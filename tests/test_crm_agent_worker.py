import asyncio
import unittest
from unittest.mock import patch

import httpx
from app.services import crm_agent_worker as worker


class AgentWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_endpoint_does_not_follow_redirects(self):
        requests = []
        async def handler(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location": "https://example.com/steal"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                await worker.tick(client, "test-secret")
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url), worker.CLAIM_ENDPOINT)

    async def test_idle_tick_contains_no_customer_target_or_text(self):
        async def handler(request):
            self.assertEqual(str(request.url), worker.CLAIM_ENDPOINT)
            self.assertNotIn(b"number", request.content)
            self.assertNotIn(b"text", request.content)
            self.assertEqual(request.headers["Authorization"], "Bearer test-secret")
            return httpx.Response(200, content=b"null",headers={"Content-Type":"application/json"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await worker.tick(client, "test-secret")
        self.assertEqual(worker.agent_worker_status()["state"], "idle")

    async def test_claim_is_forwarded_without_customer_data(self):
        requests = []
        async def handler(request):
            requests.append(request)
            if str(request.url) == worker.CLAIM_ENDPOINT:
                return httpx.Response(200, json={"id":"job-id","lease":"lease-id"})
            self.assertEqual(request.content,b'{"job": {"id": "job-id", "lease": "lease-id"}}')
            return httpx.Response(200,json={"state":"completed"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await worker.tick(client,"test-secret")
        self.assertEqual(len(requests),2)
        self.assertEqual(str(requests[1].url),worker.ENDPOINT)

    async def test_consumer_cancellation_propagates(self):
        async def cancelled(*args):
            raise asyncio.CancelledError()
        with patch.object(worker, "tick", cancelled):
            with self.assertRaises(asyncio.CancelledError):
                await worker._consume(None, "test-secret")

    async def test_failed_probe_stays_unverified_without_processing_jobs(self):
        async def handler(request):
            return httpx.Response(503,json={"state":"agent_unavailable","code":"model_429_insufficient_quota"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.assertFalse(await worker.verify_model(client,"test-secret"))
        self.assertFalse(worker.agent_worker_status()["model_verified"])
        self.assertEqual(worker.agent_worker_status()["verification_code"],"model_429_insufficient_quota")


if __name__ == "__main__":
    unittest.main()
