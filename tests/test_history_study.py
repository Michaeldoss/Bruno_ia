import asyncio
import base64
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

from app.services.history_study import HistoryStudy, StudyBlocked, STUDY_PROMPT, authorship, kind_of, validate_analysis

SETTINGS=SimpleNamespace(SUPABASE_URL='https://db.example',SUPABASE_SERVICE_ROLE_KEY='test-db-key',ANTHROPIC_API_KEY='test-ai-key',OPENAI_API_KEY='test-audio-key')
JOB={'id':'job','study_id':'study','org_id':'org','owner_id':'owner','conversation_id':'conv','lease':'lease','snapshot_at':'2026-10-19T10:00:00Z'}
MESSAGE={'id':'message','source_hash':'hash','type':'text','content':'Vou conferir pra ti.','created_at':'2026-08-01T10:00:00Z','is_from_contact':False,'payload':{'source':'outbound_device'}}
ANALYSIS={'summary':'Trecho histórico, confirmação pendente.','facts':[],'style_examples':[],'uncertainties':[]}

class StudyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests=[]
        self.costs=[]
        self.handler=lambda r:httpx.Response(200,json={'ok':True})
        def transport(request):
            self.requests.append(request)
            return self.handler(request)
        self.client=httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.worker=HistoryStudy(self.client,SETTINGS,budget=lambda estimate:{'allowed':True},record=lambda *x:self.costs.append(x),model='test-model')
        self.worker.job=copy.deepcopy(JOB)

    async def asyncTearDown(self):await self.client.aclose()

    def test_evidence_requires_literal_source_and_verified_author(self):
        source={'message_id':'message','text':MESSAGE['content'],'authorship':'owner_device','status':'read','kind':'text'}
        analysis={**copy.deepcopy(ANALYSIS),'style_examples':[{'source_id':'message','quote':'Vou conferir','observation':'Resposta curta de acompanhamento.'}]}
        self.assertEqual(validate_analysis(analysis,[source])['status'],'research_candidate')
        for bad in ['automation','owner_candidate','unknown','contact','other_staff']:
            result=validate_analysis(copy.deepcopy(analysis),[{**source,'authorship':bad}])
            self.assertEqual(result['style_examples'],[])
            self.assertEqual(result['rejected_evidence_count'],1)
        for kind,status in [('image','read'),('video','partial'),('text','blocked'),('document','read')]:
            result=validate_analysis(copy.deepcopy(analysis),[{**source,'kind':kind,'status':status}])
            self.assertEqual(result['style_examples'],[])
        analysis['style_examples'][0]['quote']='Já foi enviado'
        result=validate_analysis(analysis,[source])
        self.assertEqual(result['style_examples'],[])
        self.assertEqual(result['rejected_evidence_count'],1)
        self.assertEqual(result['summary_status'],'unverified_synthesis')

    def test_authorship_and_media_kind(self):
        self.assertEqual(authorship(MESSAGE,'owner'),'owner_device')
        self.assertEqual(authorship({**MESSAGE,'known_automation':True},'owner'),'automation')
        self.assertEqual(authorship({**MESSAGE,'is_internal_note':True},'owner'),'other_staff')
        self.assertEqual(kind_of({'type':'media','payload':{'media_mime':'application/pdf'}}),'document')
        self.assertEqual(kind_of({'type':'media','payload':{'media_mime':'audio/ogg'}}),'audio')

    def test_unmatched_fact_is_discarded_without_losing_valid_evidence(self):
        source={'message_id':'message','text':'Quero papel de 914.','authorship':'contact','kind':'text','status':'read'}
        value={**copy.deepcopy(ANALYSIS),'facts':[{'source_id':'message','quote':'papel de 914','statement':'Interesse em papel de 914.'},{'source_id':'message','quote':'Já paguei','statement':'Pagamento confirmado.'}]}
        result=validate_analysis(value,[source])
        self.assertEqual(len(result['facts']),1)
        self.assertEqual(result['rejected_evidence_count'],1)
        self.assertNotIn('Pagamento confirmado',json.dumps(result))
        with self.assertRaises(StudyBlocked):validate_analysis({**ANALYSIS,'facts':None},[source])

    async def test_media_scope_redirect_size_and_no_database_credential(self):
        for url in ['https://other.example/audio','https://db.example/storage/v1/object/public/whatsapp-media/other/conv/a',
                    'https://db.example/storage/v1/object/public/whatsapp-media/org/conv/%2e%2e/a']:
            with self.assertRaises(StudyBlocked):await self.worker.load_media({'media_url':url})
        self.assertFalse(self.requests)
        url='https://db.example/storage/v1/object/public/whatsapp-media/org/conv/a'
        self.handler=lambda r:httpx.Response(302,headers={'location':'https://other.example'})
        with self.assertRaises(StudyBlocked):await self.worker.load_media({'media_url':url})
        self.assertNotIn('authorization',self.requests[-1].headers)
        self.assertNotIn('apikey',self.requests[-1].headers)
        self.handler=lambda r:httpx.Response(200,headers={'content-length':str(25*1024*1024)})
        with self.assertRaises(StudyBlocked):await self.worker.load_media({'media_url':url})

    async def test_existing_transcript_reused_without_provider_call(self):
        self.handler=lambda r:httpx.Response(200,json=[{'transcription':'Quero papel de 914.'}])
        text,status,details=await self.worker.media_read(MESSAGE,'audio')
        self.assertEqual(text,'Quero papel de 914.')
        self.assertEqual(status,'read')
        self.assertEqual(len(self.requests),1)
        self.assertEqual(details['method'],'existing_transcript')

    async def test_each_rpc_carries_current_worker_version(self):
        await self.worker.rpc('heartbeat')
        body=json.loads(self.requests[-1].content)
        self.assertEqual(body['p_data']['worker_version'],'openai-v3')
        await self.worker.rpc('source',{'worker_version':'retired','message_id':'message'})
        body=json.loads(self.requests[-1].content)
        self.assertEqual(body['p_data']['worker_version'],'openai-v3')

    async def test_openai_responses_maps_media_and_records_before_accepting(self):
        def handler(request):
            if request.url.host=='api.openai.com':
                body=json.loads(request.content)
                self.assertFalse(body['store'])
                self.assertTrue(body['text']['format']['strict'])
                self.assertFalse(body['text']['format']['schema']['additionalProperties'])
                self.assertEqual([x['type'] for x in body['input'][1]['content']],['input_text','input_image','input_file'])
                self.assertTrue(body['input'][1]['content'][2]['file_data'].startswith('data:application/pdf;base64,'))
                return httpx.Response(200,json={'status':'completed','usage':{'input_tokens':100,'output_tokens':25},'output':[{'type':'message','content':[{'type':'output_text','text':'{"reading":"Documento legível.","uncertainties":[]}'}]}]})
            return httpx.Response(200,json={'ok':True})
        self.handler=handler
        result=await self.worker.model_read('Retorne JSON.',[{'type':'text','text':'Contexto'}, {'type':'image','source':{'media_type':'image/jpeg','data':'test'}},{'type':'document','source':{'media_type':'application/pdf','data':'test'}}])
        self.assertEqual(result['reading'],'Documento legível.')
        self.assertEqual(self.costs[0][0],'openai')
        self.handler=lambda request:httpx.Response(200,json={'status':'incomplete','usage':{'input_tokens':100,'output_tokens':6000},'output':[]}) if request.url.host=='api.openai.com' else httpx.Response(200,json={'ok':True})
        with self.assertRaises(StudyBlocked):await self.worker.model_read('JSON',[{'type':'text','text':'x'}])
        self.assertEqual(len(self.costs),2,'charged incomplete output is still accounted')

    async def test_provider_quota_does_not_fall_back_or_consume_more_sources(self):
        self.handler=lambda request:httpx.Response(429,json={'error':{'code':'insufficient_quota'}}) if request.url.host=='api.openai.com' else httpx.Response(200,json={'ok':True})
        with self.assertRaisesRegex(StudyBlocked,'budget_provider_insufficient_quota'):
            await self.worker.model_read('JSON',[{'type':'text','text':'x'}])
        self.assertFalse(self.costs)
        self.assertFalse(any(r.url.host=='api.anthropic.com' for r in self.requests))

    async def test_structured_study_limits_facts_and_verified_source_ids(self):
        def handler(request):
            if request.url.host!='api.openai.com':return httpx.Response(200,json={'ok':True})
            schema=json.loads(request.content)['text']['format']['schema']['properties']
            self.assertEqual(schema['facts']['maxItems'],12)
            self.assertEqual(schema['facts']['items']['properties']['source_id']['enum'],['contact-message'])
            self.assertEqual(schema['style_examples']['maxItems'],0)
            return httpx.Response(200,json={'status':'completed','usage':{'input_tokens':100,'output_tokens':25},'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(ANALYSIS)}]}]})
        self.handler=handler
        await self.worker.model_read(STUDY_PROMPT,[{'type':'text','text':json.dumps({'sources':[{'message_id':'contact-message','authorship':'contact','kind':'text','status':'read'}]})}])

    async def test_native_pdf_all_pages_and_image_coverage(self):
        from pypdf import PdfWriter
        from PIL import Image
        writer=PdfWriter()
        for _ in range(11):writer.add_blank_page(width=100,height=100)
        buf=io.BytesIO();writer.write(buf)
        async def load(message):return buf.getvalue(),'application/pdf'
        self.worker.load_media=load
        calls=[]
        async def read(prompt,blocks):
            calls.append(blocks)
            return {'reading':'Conteúdo legível.','uncertainties':[]}
        self.worker.model_read=read
        text,status,details=await self.worker.media_read(MESSAGE,'document')
        self.assertEqual(len(calls),2)
        self.assertEqual(details['pages'],11)
        self.assertIn('11–11',text)
        self.assertEqual(status,'read')
        self.assertEqual(calls[0][0]['source']['media_type'],'application/pdf')
        buf=io.BytesIO();Image.new('RGB',(2000,20)).save(buf,format='PNG')
        _,status,details=await self.worker.media_read(MESSAGE,'image')
        self.assertEqual(status,'partial')
        self.assertTrue(details['resolution_reduced'])

    async def test_real_ffmpeg_audio_transcription_and_recording(self):
        import imageio_ffmpeg
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.wav'
            code,_=await self.worker.ffmpeg('-f','lavfi','-i','sine=frequency=440:duration=1','-y',str(path))
            self.assertEqual(code,0)
            def handler(r):
                if r.url.host=='api.openai.com':return httpx.Response(200,json={'text':'Preciso de tinta.'})
                return httpx.Response(200,json={'ok':True})
            self.handler=handler
            text,seconds=await self.worker.transcribe_file(path)
            self.assertIn('Preciso de tinta.',text)
            self.assertAlmostEqual(seconds,1,places=1)
            self.assertEqual(self.costs[0][0],'whisper')

    async def test_video_never_claims_integral_visual_reading(self):
        from PIL import Image
        async def load(message):return b'fake-video','video/mp4'
        async def ffmpeg(*args):
            Image.new('RGB',(30,30)).save(str(args[-1]).replace('%03d','001'))
            return 0,''
        async def transcribe(path):return 'Máquina ajustada.',12
        async def read(prompt,blocks):return {'reading':'Equipamento visível.','uncertainties':[]}
        self.worker.load_media=load;self.worker.ffmpeg=ffmpeg;self.worker.transcribe_file=transcribe;self.worker.model_read=read
        text,status,details=await self.worker.media_read(MESSAGE,'video')
        self.assertEqual(status,'partial')
        self.assertFalse(details['full_visual_coverage'])
        self.assertIn('Máquina ajustada.',text)

    async def test_resume_source_cache_avoids_reprocessing(self):
        self.handler=lambda r:httpx.Response(200,json=[{'kind':'text','authorship':'owner_device','status':'read','source_text':'Texto já lido','details':{}}])
        source=await self.worker.source_read(MESSAGE)
        self.assertEqual(source['text'],'Texto já lido')
        self.assertEqual(len(self.requests),1)

    async def test_budget_failure_pauses_without_reading_or_sending(self):
        operations=[]
        async def rpc(op,data=None):
            operations.append(op)
            return copy.deepcopy(JOB) if op=='claim' else {'ok':True}
        self.worker.rpc=rpc
        self.worker.budget=lambda estimate:{'allowed':False,'reason':'memory_monthly_limit'}
        await self.worker.tick()
        self.assertEqual(operations,['claim','pause'])
        self.assertFalse(self.requests)

    async def test_large_batch_checkpoints_only_processed_sources(self):
        messages=[{**copy.deepcopy(MESSAGE),'id':str(i)} for i in range(3)]
        finished=[]
        async def rpc(op,data=None):
            if op=='claim':return copy.deepcopy(JOB)
            if op=='batch':return messages
            if op=='finish':finished.append(data)
            return {'ok':True}
        async def source(message):return {'message_id':message['id'],'authorship':'owner_device','kind':'text','status':'read','text':'x'*90000}
        async def rows(table,params):return []
        async def model(prompt,blocks):return copy.deepcopy(ANALYSIS)
        self.worker.rpc=rpc;self.worker.source_read=source;self.worker.rows=rows;self.worker.model_read=model
        await self.worker.tick()
        self.assertEqual(finished[0]['cursor_id'],'0')
        self.assertEqual(finished[0]['analysis']['coverage']['sources'],1)

if __name__=='__main__':unittest.main()
