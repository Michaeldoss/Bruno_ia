import asyncio
import base64
import importlib.util
import pathlib
import unittest
from unittest.mock import AsyncMock, patch

import httpx

spec = importlib.util.spec_from_file_location('realtime_audio_tested', pathlib.Path(__file__).parents[1] / 'app/services/realtime_audio.py')
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)
ORG = '00000000-0000-4000-8000-000000000001'
INLINE = 'data:audio/ogg;base64,' + base64.b64encode(b'OggS' + b'0' * 64).decode()
JOB = {'message_id': 'message', 'conversation_id': 'conversation', 'claim_token': 'lease', 'claim_status':'rt_processing_1','attempt':1,'media_url': INLINE}


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_exhaustion_is_distinct_from_transient_rate_limit(self):
        import json
        for code, expected_status in [('insufficient_quota', 'rt_failed'), ('rate_limit_exceeded', 'rt_waiting_1')]:
            writes = []
            async def transport(request):
                if request.url.host == 'api.openai.com':
                    return httpx.Response(429, json={'error': {'code': code, 'message': 'private provider detail'}})
                writes.append(json.loads(request.content))
                return httpx.Response(200, json=[{'id': 'stored'}])
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                worker = audio.AudioWorker(client, supabase_url='https://crm.supabase.co', service_key='server-key', openai_key='openai-key', organizations=[ORG])
                await worker.process(ORG, JOB)
            self.assertEqual(writes[0]['error'], 'provider_' + code)
            self.assertEqual(writes[0]['status'], expected_status)
            self.assertNotIn('private provider detail', json.dumps(writes))

    async def test_media_failure_is_not_reported_as_provider_quota(self):
        import json
        writes = []
        async def transport(request):
            self.assertNotEqual(request.url.host, 'api.openai.com')
            if request.method == 'GET':
                return httpx.Response(429, json={'error': {'code': 'insufficient_quota'}})
            writes.append(json.loads(request.content))
            return httpx.Response(200, json=[{'id': 'stored'}])
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            worker = audio.AudioWorker(client, supabase_url='https://crm.supabase.co', service_key='server-key', openai_key='openai-key', organizations=[ORG])
            await worker.process(ORG, {**JOB, 'media_url': 'https://crm.supabase.co/storage/v1/object/public/audio.ogg'})
        self.assertEqual(writes[0]['error'], 'media_http_429')
        self.assertEqual(writes[0]['status'], 'rt_waiting_1')

    def test_unrecognized_provider_errors_never_persist_arbitrary_text(self):
        response = httpx.Response(429, json={'error': {'code': 'secret-looking-value', 'message': 'sensitive'}})
        self.assertEqual(audio.transcription_failure(response), ('provider_http_429', True))
        self.assertEqual(audio.transcription_failure(httpx.Response(503, text='not JSON')), ('provider_http_503', True))

    async def test_transcript_saved_with_same_tenant_message_and_claim(self):
        calls = []
        async def transport(request):
            calls.append(request)
            if request.url.host == 'api.openai.com':
                self.assertEqual(request.headers['authorization'], 'Bearer openai-key')
                self.assertNotIn('apikey', request.headers)
                return httpx.Response(200, json={'text': 'Preciso de tinta azul', 'duration': 4})
            return httpx.Response(200, json=[{'id':'stored'}])
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            worker = audio.AudioWorker(client, supabase_url='https://crm.supabase.co', service_key='server-key', openai_key='openai-key', organizations=[ORG])
            await worker.process(ORG, JOB)
        import json
        saved = json.loads(calls[1].content)
        self.assertEqual(calls[1].url.params['org_id'], 'eq.' + ORG)
        self.assertEqual(calls[1].url.params['message_id'], 'eq.message')
        self.assertEqual(calls[1].url.params['updated_at'], 'eq.lease')
        self.assertEqual(saved['transcription'], 'Preciso de tinta azul')
        self.assertFalse(any('send' in str(r.url) for r in calls))

    async def test_arbitrary_media_url_never_downloaded_or_sent_to_whisper(self):
        calls = []
        async def transport(request):
            calls.append(request)
            return httpx.Response(200, json=True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            worker = audio.AudioWorker(client, supabase_url='https://crm.supabase.co', service_key='server-key', openai_key='openai-key', organizations=[ORG])
            await worker.process(ORG, {**JOB, 'media_url': 'http://169.254.169.254/secret'})
        self.assertEqual(len(calls), 1)
        self.assertIn('audio_transcriptions', str(calls[0].url))
        self.assertIn(b'media_origin_not_allowed', calls[0].content)

    async def test_storage_retry_does_not_repeat_paid_transcription(self):
        paid = 0
        writes = 0
        async def transport(request):
            nonlocal paid, writes
            if request.url.host == 'api.openai.com':
                paid += 1
                return httpx.Response(200, json={'text': 'Pedido confirmado'})
            writes += 1
            return httpx.Response(503 if writes == 1 else 200, json=True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            worker = audio.AudioWorker(client, supabase_url='https://crm.supabase.co', service_key='server-key', openai_key='openai-key', organizations=[ORG])
            with patch.object(audio.asyncio, 'sleep', new=AsyncMock()):
                await worker.process(ORG, JOB)
        self.assertEqual(paid, 1)
        self.assertEqual(writes, 2)

    async def test_three_claimed_jobs_run_concurrently(self):
        worker = audio.AudioWorker(None, supabase_url='https://crm.supabase.co', service_key='server-key', openai_key='openai-key', organizations=[ORG])
        worker.claim = AsyncMock(return_value=[{**JOB, 'message_id': str(i)} for i in range(3)])
        started = set()
        gate = asyncio.Event()
        async def process(org, job):
            started.add(job['message_id'])
            if len(started) == 3:
                gate.set()
            await asyncio.wait_for(gate.wait(), timeout=1)
        worker.process = process
        await worker.tick()
        self.assertEqual(len(started), 3)

    async def test_duplicate_reservation_is_not_processed_again(self):
        requests=[]
        async def transport(request):
            requests.append(request)
            if request.method=='POST':
                self.assertEqual(request.url.params['on_conflict'],'message_id')
                self.assertIn('ignore-duplicates',request.headers['prefer'])
                return httpx.Response(201,json=[])
            if request.url.path.endswith('/messages'):
                self.assertEqual(request.url.params['audio_transcriptions'],'is.null')
                return httpx.Response(200,json=[{'id':'message','conversation_id':'conversation','media_url':INLINE}])
            return httpx.Response(200,json=[])
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            worker=audio.AudioWorker(client,supabase_url='https://crm.supabase.co',service_key='server-key',openai_key='openai-key',organizations=[ORG])
            self.assertEqual(await worker.claim(ORG),[])
        self.assertEqual(sum(r.method=='POST' for r in requests),1)

    async def test_expired_reservation_uses_atomic_status_and_timestamp_filters(self):
        async def transport(request):
            if request.method=='PATCH':
                self.assertEqual(request.url.params['status'],'eq.rt_processing_1')
                self.assertEqual(request.url.params['updated_at'],'eq.old-stamp')
                return httpx.Response(200,json=[])
            if request.url.path.endswith('/messages'):
                if 'id' in request.url.params:
                    return httpx.Response(200,json=[{'id':'message','conversation_id':'conversation','media_url':INLINE}])
                return httpx.Response(200,json=[])
            return httpx.Response(200,json=[{'id':'record','message_id':'message','conversation_id':'conversation','status':'rt_processing_1','updated_at':'old-stamp'}])
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            worker=audio.AudioWorker(client,supabase_url='https://crm.supabase.co',service_key='server-key',openai_key='openai-key',organizations=[ORG])
            self.assertEqual(await worker.claim(ORG),[])

    def test_redirect_origin_and_invalid_inline_are_rejected(self):
        for value in ['https://crm.supabase.co.evil/storage/v1/object/public/file',
                      'https://crm.supabase.co/auth/v1/user', 'data:audio/ogg;base64,A',
                      'data:text/html;base64,AAAA']:
            with self.assertRaises(audio.InvalidAudio):
                audio.media_source(value, 'https://crm.supabase.co')


if __name__ == '__main__':
    unittest.main()
