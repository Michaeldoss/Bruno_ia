"""Queue potential lessons for human outcome/privacy review, never auto-approve."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5, UUID
from app.services.memory_tenant import require_conversation_org


async def retire_conversation_learning(client, url, headers, conversation):
    org_id = require_conversation_org(conversation)
    conversation_id = str(UUID(conversation['id']))
    response = await client.patch(f'{url}/rest/v1/bruno_knowledge', headers=headers,
        params={'org_id': f'eq.{org_id}', 'source_type': 'eq.conversation_candidate',
                'source_reference': f'like.*"conversation_id": "{conversation_id}"*',
                'approval_status': 'in.(pending,approved)'},
        json={'approval_status': 'rejected', 'is_active': False, 'updated_at': datetime.now(timezone.utc).isoformat()})
    response.raise_for_status()


async def queue_learning_candidate(client, url, headers, conversation, analysis):
    org_id = require_conversation_org(conversation)
    if not isinstance(analysis, dict):
        return
    coverage = analysis.get('coverage') or {}
    if not isinstance(coverage, dict):
        return
    if not coverage.get('complete') or not analysis.get('analysis_valid'):
        return
    status = analysis.get('analysis_status')
    if status == 'order_authorized':
        category = 'vendas'
    elif status == 'support':
        category = 'assistencia_tecnica'
    else:
        return
    ids = [str(UUID(value)) for value in coverage.get('batch_message_ids', [])]
    if not ids or len(ids) > 400:
        return
    response = await client.get(f'{url}/rest/v1/messages', headers=headers, params={
        'org_id': f'eq.{org_id}', 'conversation_id': f"eq.{conversation['id']}",
        'id': f"in.({','.join(ids)})", 'deleted_at': 'is.null', 'is_internal_note': 'eq.false',
        'select': 'id,content,is_from_contact', 'limit': 400})
    response.raise_for_status()
    messages = response.json()
    if not isinstance(messages, list) or {m['id'] for m in messages} != set(ids):
        return
    if not any(m.get('is_from_contact') and (m.get('content') or '').strip() for m in messages):
        return
    reference = {'version': 1, 'org_id': org_id, 'conversation_id': conversation['id'],
                 'outcome': 'requires_human_confirmation',
                 'evidence': [{'id': m['id'], 'sha256': hashlib.sha256((m.get('content') or '').encode()).hexdigest()}
                              for m in messages]}
    candidate_id = str(uuid5(NAMESPACE_URL, f"{org_id}:{conversation['id']}:{ids[-1]}:{coverage.get('retained_ids_digest', '')}:learning-v1"))
    result = await client.post(f'{url}/rest/v1/bruno_knowledge', headers={
        **headers, 'Prefer': 'resolution=ignore-duplicates,return=minimal'},
        params={'on_conflict': 'id'}, json={
            'id': candidate_id, 'org_id': org_id, 'category': category,
            'title': 'Revisar abordagem de venda' if category == 'vendas' else 'Revisar procedimento técnico',
            'content': 'Confira as mensagens de origem e escreva uma orientação reutilizável, sem dados particulares do cliente.',
            'source_type': 'conversation_candidate', 'source_reference': json.dumps(reference),
            'approval_status': 'pending', 'is_active': False, 'confidence': 0,
            'tags': ['resultado_a_confirmar']})
    result.raise_for_status()
