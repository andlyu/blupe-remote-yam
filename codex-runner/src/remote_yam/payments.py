"""Stripe Checkout + durable single-use run purchases. See docs/refs/stripe.

A dispatch with an uncertain outcome is held for review, never automatically retried.
No payment credentials, model keys, card details or raw webhook bodies enter SQLite.
"""
import hashlib
from contextlib import contextmanager
import json
from pathlib import Path
import secrets
import sqlite3
import time
import stripe
from remote_yam.socials import social_handles

AMOUNT = 250
CURRENCY = 'usd'


class PaymentError(Exception):
    pass


class Payments:
    def __init__(self, database, key, webhook_secret, origin, *, client=None):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.client = client or stripe.StripeClient(key, max_network_retries=2)
        self.webhook_secret, self.origin = webhook_secret, origin
        self.live = key.startswith(('sk_live_', 'rk_live_'))
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS purchases (id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, checkout TEXT UNIQUE, url TEXT, created REAL NOT NULL, run TEXT)')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def owner(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def checkout(self, token, payload):
        owner = self.owner(token)
        task, name = payload.get('prompt'), payload.get('runner_name')
        if not isinstance(task, str) or not 1 <= len(task.strip()) <= 4000:
            raise PaymentError('Enter a task of up to 4,000 characters')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 32 or any(ord(c) < 32 for c in name):
            raise PaymentError('Enter a name of 1–32 characters')
        try:
            handles = social_handles(payload)
        except ValueError as exc:
            raise PaymentError(str(exc)) from None
        duration = payload.get('run_duration_s', 300)
        if type(duration) is not int or not 60 <= duration <= 600:
            raise ValueError('Choose a run duration from 1 to 10 minutes')
        stored = json.dumps({'prompt':task.strip(), 'runner_name':name.strip(), 'run_duration_s':duration, **handles})
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute("SELECT * FROM purchases WHERE owner=? AND state IN ('creating','pending','paid','dispatching','review') ORDER BY created DESC LIMIT 1", (owner,)).fetchone()
            if existing:
                if existing['state'] not in ('creating','pending') or existing['payload'] != stored:
                    raise PaymentError('An earlier purchase is pending. Finish it before purchasing another run.')
                purchase = existing['id']
                if existing['url']:
                    return dict(order=purchase, url=existing['url'])
            else:
                purchase = secrets.token_hex(24)
                db.execute('INSERT INTO purchases(id,owner,payload,state,created) VALUES(?,?,?,?,?)', (purchase,owner,stored,'creating',time.time()))
        session = self.client.v1.checkout.sessions.create(params={
            'mode':'payment', 'payment_method_types':['card'],
            'line_items':[{'price_data':{'currency':CURRENCY,'unit_amount':AMOUNT,
                'product_data':{'name':'BluPe · One Astra robot run'}},'quantity':1}],
            'client_reference_id':purchase,'metadata':{'yam_order':purchase},
            'success_url':self.origin+'/?purchase='+purchase,
            'cancel_url':self.origin+'/?payment=cancelled',
        }, options={'idempotency_key':'yam-checkout-'+purchase})
        if not str(session.url).startswith('https://checkout.stripe.com/'):
            raise PaymentError('Payment page unavailable')
        with self.db() as db:
            db.execute("UPDATE purchases SET checkout=?,url=?,state='pending' WHERE id=? AND state='creating'",(session.id,session.url,purchase))
        return dict(order=purchase,url=session.url)

    def confirm(self, checkout_id):
        session = self.client.v1.checkout.sessions.retrieve(checkout_id, params={'expand':['payment_intent.latest_charge']})
        metadata = session.metadata
        if hasattr(metadata, 'to_dict'):
            metadata = metadata.to_dict()
        order = (metadata or {}).get('yam_order')
        with self.db() as db:
            row = db.execute('SELECT * FROM purchases WHERE id=?', (order,)).fetchone()
            if not row or row['checkout'] != session.id:
                raise PaymentError('Unknown payment')
            if session.payment_status != 'paid':
                return False
            if session.amount_total != AMOUNT or session.currency != CURRENCY or session.mode != 'payment' or bool(session.livemode) != self.live or session.client_reference_id != order:
                raise PaymentError('Payment details do not match this run')
            charge = getattr(getattr(session, 'payment_intent', None), 'latest_charge', None)
            if not charge or not charge.paid or not charge.captured or charge.amount_refunded or charge.disputed:
                raise PaymentError('Payment is unavailable for this run. Contact the operator.')
            db.execute("UPDATE purchases SET state='paid' WHERE id=? AND state IN ('creating','pending')",(order,))
        return True

    def webhook(self, body, signature):
        event = stripe.Webhook.construct_event(body, signature, self.webhook_secret)
        with self.db() as db:
            known = db.execute('SELECT id FROM purchases WHERE checkout=?', (event.data.object.id,)).fetchone()
        if not known:
            return  # Other products can share the same Stripe account.
        if event.type in ('checkout.session.completed','checkout.session.async_payment_succeeded'):
            self.confirm(event.data.object.id)
        elif event.type == 'checkout.session.expired':
            with self.db() as db:
                db.execute("UPDATE purchases SET state='expired' WHERE checkout=? AND state='pending'", (event.data.object.id,))

    def get(self, token, order):
        with self.db() as db:
            row = db.execute('SELECT * FROM purchases WHERE id=? AND owner=?',(order,self.owner(token))).fetchone()
        if not row:
            raise PaymentError('Purchase not found in this browser')
        return dict(row)

    def recover(self, token):
        with self.db() as db:
            rows = db.execute("SELECT id,checkout,state FROM purchases WHERE owner=? AND state IN ('pending','paid','dispatching','review') ORDER BY created", (self.owner(token),)).fetchall()
        for row in rows:
            if row['state'] in ('dispatching','review') or (row['checkout'] and self.confirm(row['checkout'])):
                return row['id']
        return None

    def redeem(self, token, order, launch):
        row = self.get(token, order)
        if row['state'] in ('used','dispatching','review'):
            return {'payment_state':row['state']}
        if not row['checkout'] or not self.confirm(row['checkout']):
            return {'payment_state':'pending'}
        with self.db() as db:
            changed = db.execute("UPDATE purchases SET state='dispatching' WHERE id=? AND owner=? AND state='paid'",(order,self.owner(token))).rowcount
        if not changed:
            return {'payment_state':self.get(token,order)['state']}
        try:
            run = launch(json.loads(row['payload']))
        except Exception:
            # Network failure may happen AFTER Session API accepted the run.
            with self.db() as db:
                db.execute("UPDATE purchases SET state='review' WHERE id=?",(order,))
            raise PaymentError('Payment received, but run submission needs operator review. Do not pay again.') from None
        with self.db() as db:
            db.execute("UPDATE purchases SET state='used',run=? WHERE id=?",(run,order))
        return {'payment_state':'used'}
