import hashlib
import hmac
import json
import time
from types import SimpleNamespace as NS
from concurrent.futures import ThreadPoolExecutor
import pytest
import stripe
from remote_yam.payments import Payments, PaymentError


class Sessions:
    def create(self, params, options):
        self.params = params
        self.session = NS(id='cs_test_1',url='https://checkout.stripe.com/c/pay/test',
            metadata=params['metadata'],client_reference_id=params['client_reference_id'],
            payment_status='unpaid',amount_total=250,currency='usd',mode='payment',livemode=False,
            payment_intent=NS(latest_charge=NS(paid=True,captured=True,amount_refunded=0,disputed=False)))
        return self.session
    def retrieve(self, identity, params=None):
        assert identity == self.session.id
        return self.session


@pytest.fixture
def setup(tmp_path):
    sessions = Sessions()
    client = NS(v1=NS(checkout=NS(sessions=sessions)))
    service = Payments(tmp_path/'payments.sqlite','sk_test_fixture','whsec_fixture','https://robot.example',client=client)
    order = service.checkout('browser-a', {'prompt':'Move green block','runner_name':'Andrew'})['order']
    return service, sessions, order


def test_unpaid_wrong_owner_and_amount_fail_closed(setup):
    service,sessions,order = setup
    assert service.redeem('browser-a',order,lambda _: pytest.fail('unpaid run'))['payment_state'] == 'pending'
    with pytest.raises(PaymentError):service.redeem('browser-b',order,lambda _:None)
    sessions.session.payment_status = 'paid';sessions.session.amount_total=1
    with pytest.raises(PaymentError):service.redeem('browser-a',order,lambda _:None)


def test_checkout_preserves_optional_social_handles(tmp_path):
    sessions = Sessions()
    service = Payments(tmp_path/'social-payments.db', 'sk_test_fixture', 'whsec_fixture',
                       'https://robot.example', client=NS(v1=NS(checkout=NS(sessions=sessions))))
    order = service.checkout('social-browser', dict(prompt='Move block', runner_name='Runner',
                             x_handle='@runner_x', instagram_handle='@runner.insta'))['order']
    sessions.session.payment_status = 'paid'
    calls = []
    service.redeem('social-browser', order, lambda payload: calls.append(payload) or 'social-run')
    assert calls[0]['x_handle'] == 'runner_x'
    assert calls[0]['instagram_handle'] == 'runner.insta'


def test_concurrent_redemption_only_launches_once_and_survives_restart(setup):
    service,sessions,order=setup;sessions.session.payment_status='paid';calls=[]
    def redeem(_):return service.redeem('browser-a',order,lambda p:calls.append(p) or 'run-1')
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(redeem,range(4)))
    assert len(calls)==1
    restarted=Payments(service.path,'sk_test_fixture','whsec_fixture',service.origin,client=service.client)
    assert restarted.redeem('browser-a',order,lambda _:pytest.fail('duplicate'))['payment_state']=='used'
    assert sessions.params['line_items'][0]['price_data']['unit_amount']==250


def test_uncertain_dispatch_never_retries(setup):
    service,sessions,order=setup;sessions.session.payment_status='paid'
    def fail(_):raise TimeoutError()
    with pytest.raises(PaymentError):service.redeem('browser-a',order,fail)
    assert service.redeem('browser-a',order,lambda _:pytest.fail('retry'))['payment_state']=='review'


def test_refunded_payment_cannot_launch(setup):
    service,sessions,order=setup
    sessions.session.payment_status='paid'
    sessions.session.payment_intent.latest_charge.amount_refunded=250
    with pytest.raises(PaymentError):
        service.redeem('browser-a',order,lambda _:pytest.fail('refunded run'))


def test_sdk_stripe_object_metadata(setup):
    service,sessions,order=setup
    sessions.session.metadata = stripe.StripeObject.construct_from({'yam_order':order}, 'sk_test_fixture')
    assert service.confirm(sessions.session.id) is False


def test_recover_paid_purchase_without_return_url(setup):
    service,sessions,order=setup
    assert service.recover('browser-a') is None
    sessions.session.payment_status='paid'
    assert service.recover('browser-b') is None
    assert service.recover('browser-a') == order
    service.redeem('browser-a',order,lambda _: 'run-recovered')
    assert service.recover('browser-a') is None


def test_signed_webhook_and_tampering(setup):
    service,sessions,order=setup;sessions.session.payment_status='paid'
    body=json.dumps({'id':'evt_test','object':'event','type':'checkout.session.completed','data':{'object':{'id':sessions.session.id}}}).encode()
    at=str(int(time.time()));signature=hmac.new(b'whsec_fixture',at.encode()+b'.'+body,hashlib.sha256).hexdigest()
    service.webhook(body,f't={at},v1={signature}')
    service.webhook(body,f't={at},v1={signature}')
    assert service.get('browser-a',order)['state']=='paid'
    with pytest.raises(Exception):service.webhook(body+b' ',f't={at},v1={signature}')
