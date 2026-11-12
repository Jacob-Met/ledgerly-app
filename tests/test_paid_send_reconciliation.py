from copy import deepcopy
import pytest

from conftest import INVOICER, load
from ledgerly.agent import Agent
from ledgerly.paypal import SandboxMock
from ledgerly.recovery import DurableAgent


def prepared(clock, tmp_path=None):
    mock = SandboxMock()
    cls = DurableAgent if tmp_path is not None else Agent
    kwargs = {'state_file': tmp_path / 'state.json'} if tmp_path is not None else {}
    agent = cls(mock, INVOICER, today=clock, **kwargs)
    created = agent.tool_create_invoice(load('01_simple_usd_hourly.txt'))['invoices'][0]
    return agent, mock, created['invoice_id'], created['approval_id']


@pytest.mark.parametrize('status', ['SENT', 'PAID', 'PARTIALLY_PAID', 'CANCELLED'])
def test_valid_refresh_retires_only_obsolete_pending_send(clock, status):
    agent, mock, identity, action_id = prepared(clock)
    original = deepcopy(agent.pending[action_id].payload)
    mock.invoices[identity]['status'] = status
    before = len(mock.requests)
    agent.tool_get_status(identity)
    assert agent.list_pending() == []
    action = agent.pending[action_id]
    assert action.status == 'REJECTED'
    assert action.payload == original
    assert status.replace('_', ' ').lower() in action.result['reason']
    assert [call[0] for call in mock.requests[before:]] == ['GET']
    assert agent.client._permits == set()
    audit = deepcopy(agent.audit)
    agent.list_pending()
    assert agent.audit == audit


def test_unavailable_or_malformed_refresh_keeps_original_review(clock):
    agent, mock, identity, action_id = prepared(clock)
    original = agent.pending[action_id].to_dict()
    get = mock.get_invoice
    def unavailable(_):
        raise TimeoutError('controlled unavailable read')
    mock.get_invoice = unavailable
    with pytest.raises(TimeoutError):
        agent.tool_get_status(identity)
    assert agent.pending[action_id].to_dict() == original
    def malformed(identity):
        response = get(identity)
        response.body['status'] = 'PAID'
        response.body['due_amount']['value'] = '1'
        return response
    mock.get_invoice = malformed
    with pytest.raises(ValueError):
        agent.tool_get_status(identity)
    assert agent.pending[action_id].to_dict() == original
    assert agent.ledger[identity].status == 'DRAFT'


@pytest.mark.parametrize('state', ['FAILED', 'INTERRUPTED', 'APPROVED'])
def test_attempted_action_is_preserved_when_invoice_is_paid(clock, state):
    agent, mock, identity, action_id = prepared(clock)
    action = agent.pending[action_id]
    action.status = state
    action.result = {'outcome': 'UNKNOWN', 'reason': 'Retained original outcome'}
    original = deepcopy(action.to_dict())
    mock.invoices[identity]['status'] = 'PAID'
    agent.tool_get_status(identity)
    agent.list_pending()
    assert action.to_dict() == original


def test_retained_paid_queue_is_reconciled_once_and_persisted_without_provider_call(clock, tmp_path):
    agent, mock, identity, action_id = prepared(clock, tmp_path)
    original = deepcopy(agent.pending[action_id].payload)
    # An older checkpoint already has a known paid ledger but obsolete pending send.
    agent.ledger[identity].status = 'PAID'
    agent.ledger[identity].paid_amount = agent.ledger[identity].total
    agent.state_store.save(agent)
    reopened = DurableAgent(mock, INVOICER, today=clock, state_file=tmp_path/'state.json')
    before = list(mock.requests)
    assert reopened.list_pending() == []
    assert reopened.pending[action_id].payload == original
    again = DurableAgent(mock, INVOICER, today=clock, state_file=tmp_path/'state.json')
    assert again.pending[action_id].status == 'REJECTED'
    assert again.pending[action_id].result == reopened.pending[action_id].result
    assert len([e for e in again.audit if e['event']=='invoice_send_invalidated']) == 1
    assert mock.requests == before


def test_unchanged_draft_stays_available_without_provider_work(clock):
    agent, mock, identity, action_id = prepared(clock)
    before = list(mock.requests)
    assert [a['id'] for a in agent.list_pending()] == [action_id]
    assert mock.requests == before


def test_in_flight_approved_send_with_deposit_is_not_relabelled_as_obsolete(clock):
    mock = SandboxMock()
    agent = Agent(mock, INVOICER, today=clock)
    created = agent.tool_create_invoice(load('07_partial_payment_deposit.txt'))['invoices'][0]
    action_id = created['approval_id']
    agent.approve(action_id)
    assert agent.pending[action_id].status == 'APPROVED'
    assert not [event for event in agent.audit
                if event['event']=='invoice_send_invalidated' and event['action']==action_id]
    assert agent.list_pending() == []
