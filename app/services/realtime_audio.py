"""24/7 transcription of newly received CRM audio. Never sends a reply."""
import asyncio
import base64
import binascii
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)
MAX_BYTES = 24 * 1024 * 1024
ROLLOUT_START = "2026-10-15T18:53:17.529Z"
EXTENSIONS = {"audio/ogg": "ogg", "audio/opus": "ogg", "audio/mpeg": "mp3",
              "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
              "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/webm": "webm",
              "audio/flac": "flac", "application/octet-stream": "ogg"}
_task = None
_state = {"state": "stopped", "completed": 0, "failed": 0}


class InvalidAudio(ValueError):
    pass


def transcription_failure(response):
    """Keep an actionable, allowlisted error code; never persist provider bodies."""
    status = response.status_code
    code = None
    try:
        error = response.json().get('error')
        if isinstance(error, dict):
            code = error.get('code') or error.get('type')
    except (ValueError, AttributeError):
        pass
    if code in ('insufficient_quota', 'billing_hard_limit_reached', 'billing_not_active'):
        return 'provider_' + code, False
    if code in ('rate_limit_exceeded', 'invalid_api_key', 'model_not_found'):
        return 'provider_' + code, status == 429 or status >= 500
    return 'provider_http_' + str(status), status == 429 or status >= 500


def media_source(value, supabase_url):
    """Only persisted CRM media, never arbitrary URLs or redirect targets."""
    if not isinstance(value, str):
        raise InvalidAudio("missing_media")
    if value.startswith("data:"):
        if len(value) > MAX_BYTES * 4 // 3 + 256:
            raise InvalidAudio("media_too_large")
        matched = re.fullmatch(r"data:([^;,]+);base64,([A-Za-z0-9+/=\s]+)", value)
        if not matched or matched[1].lower() not in EXTENSIONS:
            raise InvalidAudio("unsupported_media")
        try:
            content = base64.b64decode(re.sub(r"\s", "", matched[2]), validate=True)
        except (ValueError, binascii.Error):
            raise InvalidAudio("invalid_base64") from None
        if not 32 <= len(content) <= MAX_BYTES:
            raise InvalidAudio("invalid_media_size")
        return content, matched[1].lower()
    target, origin = urlsplit(value), urlsplit(supabase_url)
    if (target.scheme != "https" or target.netloc != origin.netloc or target.username
            or target.password or target.fragment or len(value) > 8000
            or not target.path.startswith(("/storage/v1/object/public/", "/storage/v1/object/sign/"))):
        raise InvalidAudio("media_origin_not_allowed")
    return None, None


async def load_audio(client, value, supabase_url):
    content, mime = media_source(value, supabase_url)
    if content is not None:
        return content, mime
    # No credentials are attached to media requests, even on the allowed origin.
    async with client.stream("GET", value, timeout=30, follow_redirects=False) as response:
        response.raise_for_status()
        if response.is_redirect:
            raise InvalidAudio("media_redirect_not_allowed")
        mime = response.headers.get("content-type", "application/octet-stream").split(";")[0].lower()
        if mime not in EXTENSIONS:
            raise InvalidAudio("unsupported_media")
        if int(response.headers.get("content-length") or 0) > MAX_BYTES:
            raise InvalidAudio("media_too_large")
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_BYTES:
                raise InvalidAudio("media_too_large")
            chunks.append(chunk)
        if size < 32:
            raise InvalidAudio("empty_media")
        return b"".join(chunks), mime


class AudioWorker:
    def __init__(self, client, *, supabase_url, service_key, openai_key, organizations, usage=None):
        self.client = client
        self.base = supabase_url.rstrip("/")
        self.headers = {"apikey": service_key, "Authorization": "Bearer " + service_key}
        self.openai_key = openai_key
        self.organizations = [str(UUID(value)) for value in organizations]
        self.usage = usage

    async def request(self, method, table, *, params=None, body=None, prefer=None):
        headers = {**self.headers}
        if prefer:
            headers['Prefer'] = prefer
        response = await self.client.request(method, self.base + '/rest/v1/' + table,
                                             headers=headers, params=params, json=body, timeout=20)
        response.raise_for_status()
        return response.json() if response.content else []

    async def claim(self, org_id):
        jobs = []
        now = datetime.now(timezone.utc)
        expired = (now - timedelta(minutes=5)).isoformat()
        pending = await self.request('GET', 'audio_transcriptions', params={
            'org_id': 'eq.' + org_id, 'status': 'in.(rt_processing_1,rt_processing_2,rt_processing_3,rt_waiting_1,rt_waiting_2)', 'updated_at': 'lt.' + expired,
            'select': 'id,message_id,conversation_id,status,updated_at', 'order': 'updated_at.asc', 'limit': 3})
        for row in pending:
            if not re.fullmatch(r'rt_(processing|waiting)_[123]', row['status']):
                continue
            attempt = int(row['status'][-1]) + 1
            filters = {'id': 'eq.' + row['id'], 'org_id': 'eq.' + org_id,
                       'status': 'eq.' + row['status'], 'updated_at': 'eq.' + row['updated_at']}
            if attempt > 3:
                await self.request('PATCH', 'audio_transcriptions', params=filters,
                    body={'status': 'rt_failed', 'error': 'retry_exhausted'}, prefer='return=minimal')
                continue
            message = await self.request('GET', 'messages', params={'id': 'eq.' + row['message_id'],
                'org_id': 'eq.' + org_id, 'conversation_id': 'eq.' + row['conversation_id'],
                'deleted_at': 'is.null', 'is_internal_note': 'eq.false',
                'created_at': 'gte.' + ROLLOUT_START, 'select': 'id,conversation_id,media_url', 'limit': 1})
            if not message:
                await self.request('PATCH', 'audio_transcriptions', params=filters,
                    body={'status': 'rt_failed', 'error': 'message_unavailable'}, prefer='return=minimal')
                continue
            stamp = now.isoformat()
            claimed = await self.request('PATCH', 'audio_transcriptions', params=filters,
                body={'status': 'rt_processing_' + str(attempt), 'updated_at': stamp}, prefer='return=representation')
            if claimed:
                jobs.append({**message[0], 'message_id': row['message_id'], 'claim_token': stamp,
                             'claim_status': 'rt_processing_' + str(attempt), 'attempt': attempt})
        if len(jobs) >= 3:
            return jobs
        # Anti-join uses the existing unique message_id constraint as a durable queue.
        fresh = await self.request('GET', 'messages', params={'org_id': 'eq.' + org_id,
            'type': 'in.(audio,ptt,voice,audiomessage)', 'media_url': 'not.is.null',
            'created_at': 'gte.' + ROLLOUT_START, 'deleted_at': 'is.null', 'is_internal_note': 'eq.false',
            'select': 'id,conversation_id,media_url,audio_transcriptions(message_id)',
            'audio_transcriptions': 'is.null', 'order': 'created_at.asc,id.asc', 'limit': 3 - len(jobs)})
        for message in fresh:
            stamp = now.isoformat()
            claimed = await self.request('POST', 'audio_transcriptions', params={'on_conflict': 'message_id'},
                body={'org_id': org_id, 'message_id': message['id'], 'conversation_id': message['conversation_id'],
                      'status': 'rt_processing_1', 'updated_at': stamp},
                prefer='resolution=ignore-duplicates,return=representation')
            if claimed:
                jobs.append({**message, 'message_id': message['id'], 'claim_token': stamp,
                             'claim_status': 'rt_processing_1', 'attempt': 1})
        return jobs

    async def finish(self, org_id, job, result):
        status = 'completed' if result.get('p_text') else (
            'rt_waiting_' + str(job['attempt']) if result.get('p_retry') and job['attempt'] < 3 else 'rt_failed')
        saved = await self.request('PATCH', 'audio_transcriptions', params={
            'org_id': 'eq.' + org_id, 'message_id': 'eq.' + job['message_id'],
            'conversation_id': 'eq.' + job['conversation_id'], 'status': 'eq.' + job['claim_status'],
            'updated_at': 'eq.' + job['claim_token']},
            body={'status': status, 'transcription': result.get('p_text'), 'language': 'pt',
                  'error': result.get('p_error'), 'updated_at': datetime.now(timezone.utc).isoformat()},
            prefer='return=representation')
        return bool(saved)

    async def process(self, org_id, job):
        result = {"p_org_id": org_id, "p_message_id": job["message_id"], "p_token": job["claim_token"]}
        request_stage = 'media'
        try:
            content, mime = await load_audio(self.client, job["media_url"], self.base)
            request_stage = 'transcription'
            response = await self.client.post("https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": "Bearer " + self.openai_key},
                files={"file": ("audio." + EXTENSIONS[mime], content, mime)},
                data={"model": "whisper-1", "response_format": "verbose_json", "language": "pt"}, timeout=100)
            response.raise_for_status()
            data = response.json()
            transcript = str(data.get("text") or "").strip()
            if not transcript or len(transcript) > 100000:
                raise InvalidAudio("empty_or_oversized_transcript")
            result["p_text"] = transcript
            if self.usage:
                try:
                    await asyncio.to_thread(self.usage, agente="crm_audio", segundos_audio=float(data.get("duration") or 0))
                except Exception:
                    logger.warning("[CRM AUDIO] Usage meter unavailable")
        except InvalidAudio as exc:
            result.update(p_error=str(exc), p_retry=False)
        except httpx.HTTPStatusError as exc:
            if request_stage == 'media':
                status = exc.response.status_code
                result.update(p_error='media_http_' + str(status), p_retry=status == 429 or status >= 500)
            else:
                reason, retry = transcription_failure(exc.response)
                result.update(p_error=reason, p_retry=retry)
        except (httpx.RequestError, TimeoutError):
            result.update(p_error="provider_connection_failed", p_retry=True)
        except Exception:
            result.update(p_error="audio_processing_failed", p_retry=False)
        # Persist the result again, not the paid transcription, if storage briefly fails.
        for attempt in range(3):
            try:
                saved = await self.finish(org_id, job, result)
                if saved:
                    key = "completed" if result.get("p_text") else "failed"
                    _state[key] += 1
                return
            except Exception:
                if attempt == 2:
                    logger.error("[CRM AUDIO] Result persistence failed; lease recovery pending")
                else:
                    await asyncio.sleep(2 ** attempt)

    async def tick(self):
        for org_id in self.organizations:
            jobs = await self.claim(org_id)
            if jobs:
                await asyncio.gather(*(self.process(org_id, job) for job in jobs))
        _state["state"] = "running"


async def _loop():
    from app.config import get_settings
    from app.services.usage_tracker import registrar_uso_whisper
    import os
    settings = get_settings()
    keys = (settings.SUPABASE_SERVICE_ROLE_KEY, settings.OPENAI_API_KEY)
    if any(not key or key == "stub" for key in keys):
        _state["state"] = "missing_configuration"
        logger.error("[CRM AUDIO] Required configuration missing")
        return
    organizations = os.getenv("CRM_MEMORY_ORG_IDS", "dafa7ea5-08c4-44dd-886e-d58905fca38c").split(",")
    async with httpx.AsyncClient() as client:
        worker = AudioWorker(client, supabase_url=settings.SUPABASE_URL,
            service_key=keys[0], openai_key=keys[1],
            organizations=[value.strip() for value in organizations if value.strip()], usage=registrar_uso_whisper)
        while True:
            try:
                await worker.tick()
                _state.pop('last_error', None)
            except httpx.HTTPStatusError as exc:
                _state['state'] = 'retrying'
                _state['last_error'] = 'database_http_' + str(exc.response.status_code)
                logger.warning('[CRM AUDIO] Database HTTP %s', exc.response.status_code)
            except Exception:
                _state["state"] = "retrying"
                logger.warning("[CRM AUDIO] Queue unavailable; retrying without sending messages")
            await asyncio.sleep(5)


def start_audio_worker():
    global _task
    if _task is None or _task.done():
        _state["state"] = "starting"
        _task = asyncio.create_task(_loop())


async def stop_audio_worker():
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _state["state"] = "stopped"


def audio_worker_status():
    return {**_state, "task_alive": _task is not None and not _task.done()}
