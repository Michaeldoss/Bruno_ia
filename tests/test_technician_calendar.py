import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from app.services import technician_calendar as worker

JOB = {"id": "10000000-0000-4000-8000-000000000001", "lease": "10000000-0000-4000-8000-000000000002"}
TOKEN = "synthetic_test_key"


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, job, outcome=None, status=200, error=None):
        calls = []
        async def handle(request):
            calls.append(request)
            if str(request.url) == worker.CLAIM_ENDPOINT:
                return httpx.Response(200, content=json.dumps(job), headers={"Content-Type": "application/json"})
            if error:
                raise error
            return httpx.Response(status, json=outcome or {"state": "synced"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await worker.tick(client, TOKEN)
        return result, calls

    async def test_no_job_does_not_invoke_backend(self):
        state, calls = await self.exercise(None)
        self.assertEqual(state, "idle")
        self.assertEqual(len(calls), 1)

    async def test_valid_claim_only_forwards_identity(self):
        state, calls = await self.exercise({**JOB, "url": "https://example.invalid", "number": "invented", "text": "ignored"}, {"state": "sent"})
        self.assertEqual(state, "sent")
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(calls[1].content), {"job": JOB})
        self.assertEqual(str(calls[1].url), worker.WORKER_ENDPOINT)
        self.assertEqual(calls[1].headers["Authorization"], "Bearer " + TOKEN)

    async def test_malformed_claim_never_dispatches(self):
        for job in ({}, {**JOB, "lease": "bad"}, {**JOB, "id": 1}, []):
            with self.subTest(job=job), self.assertRaises(ValueError):
                await self.exercise(job)

    async def test_worker_failure_is_not_replayed(self):
        calls = []
        async def handle(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(200, json=JOB)
            raise httpx.ReadTimeout("lost response")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(httpx.ReadTimeout):
                await worker.tick(client, TOKEN)
        self.assertEqual(len(calls), 2)

    async def test_uncertain_state_never_retries(self):
        state, calls = await self.exercise(JOB, {"state": "uncertain"})
        self.assertEqual(state, "uncertain")
        self.assertEqual(len(calls), 2)

    async def test_unknown_state_not_exposed_in_health(self):
        state, _ = await self.exercise(JOB, {"state": "private message contents"})
        self.assertEqual(state, "unavailable")

    async def test_redirect_not_followed(self):
        calls = []
        async def handle(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(200, json=JOB)
            return httpx.Response(302, headers={"Location": "https://example.invalid"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                await worker.tick(client, TOKEN)
        self.assertEqual(len(calls), 2)

    async def test_backend_auth_failure_not_retried(self):
        with self.assertRaises(httpx.HTTPStatusError):
            await self.exercise(JOB, status=401)

    async def test_database_auth_failure_no_backend_call(self):
        calls = []
        async def handle(request):
            calls.append(request)
            return httpx.Response(403, json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                await worker.tick(client, TOKEN)
        self.assertEqual(len(calls), 1)

    async def test_start_stop_idempotent(self):
        async def idle():
            await asyncio.sleep(3600)
        with patch.object(worker, "_loop", idle):
            worker.start_technician_calendar()
            task = worker._task
            worker.start_technician_calendar()
            self.assertIs(task, worker._task)
            self.assertTrue(worker.technician_calendar_status()["task_alive"])
            await worker.stop_technician_calendar()
            await worker.stop_technician_calendar()
            self.assertFalse(worker.technician_calendar_status()["task_alive"])

    def test_status_contains_no_tokens(self):
        self.assertNotIn(TOKEN, json.dumps(worker.technician_calendar_status()))
        self.assertEqual(worker.technician_calendar_status()["release"], "tech-calendar-1")


if __name__ == "__main__":
    unittest.main()
