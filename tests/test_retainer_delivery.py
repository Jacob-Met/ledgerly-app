import pytest
from concurrent.futures import ThreadPoolExecutor
from ledgerly.retainer import RetainerDesk, RetainerError

@pytest.fixture
def desk(tmp_path):
    def no_provider():
        raise AssertionError('Delivery planning must not call a provider')
    return RetainerDesk(tmp_path/'desk.sqlite', no_provider)

def offer(desk, reviewed=True):
    r=desk.save_draft(dict(customer='Fictional journal',email='journal@example.test',monthly_usd='600',minutes=240,capacity=1200,scope='Two editorial articles each month',source='Fictional agreement USD600, two articles.'))
    return desk.approve(r['id'],r['review_hash']) if reviewed else r

def task(r, **kw):
    return dict(id='delivery-test-00000001',offer_id=r['id'],title='First editorial draft',due_on='2026-11-12',minutes=120,**kw)

def test_delivery_restart_complete_reopen_preserves_commitment(desk):
    r=offer(desk);before=desk.snapshot()['offers'][0]
    d=desk.save_delivery(task(r));restored=RetainerDesk(desk.path,desk.gateway_factory)
    done=restored.set_delivery_state(d['id'],'COMPLETE',d['revision'])
    assert done['history'][0]['state']=='COMPLETE'
    reopened=restored.set_delivery_state(d['id'],'PLANNED',done['revision'])
    assert [h['state'] for h in reopened['history']]==['COMPLETE','PLANNED']
    assert restored.snapshot()['offers'][0]==before
    assert restored.snapshot()['reserved_minutes']==240
    assert restored.snapshot()['deliveries'][0]==reopened

def test_concurrent_repeated_create_is_one_delivery(desk):
    r=offer(desk)
    def save(_):return RetainerDesk(desk.path,desk.gateway_factory).save_delivery(task(r))
    with ThreadPoolExecutor() as pool:rows=list(pool.map(save,range(4)))
    assert len(desk.snapshot()['deliveries'])==1
    assert all(row['revision']==1 for row in rows)

def test_stale_edit_and_status_do_not_replace_newer_plan(desk):
    r=offer(desk);d=desk.save_delivery(task(r))
    changed=desk.save_delivery({**d,'due_on':'2026-11-26','minutes':150})
    with pytest.raises(RetainerError):desk.save_delivery({**d,'title':'Stale title'})
    with pytest.raises(RetainerError):desk.set_delivery_state(d['id'],'COMPLETE',d['revision'])
    assert desk.snapshot()['deliveries'][0]==changed

def test_foreign_retainer_and_unreviewed_plan_refused(desk):
    a=offer(desk);b=offer(desk,False)
    with pytest.raises(RetainerError):desk.save_delivery(task(b))
    d=desk.save_delivery(task(a));b=desk.approve(b['id'],b['review_hash'])
    with pytest.raises(RetainerError):desk.save_delivery({**d,'offer_id':b['id']})
    assert desk.snapshot()['deliveries'][0]['offer_id']==a['id']

def test_archival_preserves_work_but_prevents_new_plans(desk):
    r=offer(desk);d=desk.save_delivery(task(r));desk.archive(r['id'])
    with pytest.raises(RetainerError):desk.save_delivery({**task(r),'id':'delivery-test-00000002'})
    desk.set_delivery_state(d['id'],'COMPLETE',d['revision'])
    assert desk.snapshot()['reserved_minutes']==0
    assert len(desk.snapshot()['deliveries'])==1

@pytest.mark.parametrize('change',[{'due_on':'2026-02-30'},{'due_on':'20261112'},{'minutes':True},{'minutes':1.5},{'minutes':0},{'title':' '},{'id':'short'}])
def test_invalid_delivery_is_not_saved(desk,change):
    r=offer(desk)
    with pytest.raises(RetainerError):desk.save_delivery({**task(r),**change})
    assert desk.snapshot()['deliveries']==[]


def test_dropped_plan_retains_history_and_can_be_restored(desk):
    r=offer(desk);d=desk.save_delivery(task(r))
    dropped=desk.set_delivery_state(d['id'],'DROPPED',d['revision'])
    assert dropped['history'][-1]['state']=='DROPPED'
    restored=desk.set_delivery_state(d['id'],'PLANNED',dropped['revision'])
    assert restored['due_on']==d['due_on']
    assert desk.snapshot()['reserved_minutes']==240
