import unittest
from unittest.mock import AsyncMock
from app.services import order_notifications as worker


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class OrderNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_does_not_call_send_endpoint(self):
        client = type('Client', (), {})()
        client.post = AsyncMock(return_value=Response(None))
        await worker.tick(client, 'test-service-credential')
        self.assertEqual(client.post.await_count, 1)

    async def test_claim_is_sent_to_fixed_backend_without_redirecting_credentials(self):
        job = {'id': 'job', 'lease': 'lease'}
        client = type('Client', (), {})()
        client.post = AsyncMock(side_effect=[Response(job), Response({'state': 'sent'})])
        await worker.tick(client, 'test-service-credential')
        calls = client.post.await_args_list
        self.assertEqual(calls[0].args[0], worker.CLAIM_ENDPOINT)
        self.assertEqual(calls[1].args[0], worker.SEND_ENDPOINT)
        self.assertEqual(calls[1].kwargs['json'], {'job': job})
        self.assertFalse(calls[0].kwargs['follow_redirects'])
        self.assertFalse(calls[1].kwargs['follow_redirects'])
        self.assertEqual(worker.order_notifications_status()['state'], 'sent')

    async def test_uncertain_delivery_is_not_sent_again_in_the_same_tick(self):
        client = type('Client', (), {})()
        client.post = AsyncMock(side_effect=[Response({'id': 'job', 'lease': 'lease'}), Response({'state': 'uncertain'})])
        await worker.tick(client, 'test-service-credential')
        self.assertEqual(client.post.await_count, 2)
        self.assertEqual(worker.order_notifications_status()['state'], 'uncertain')
