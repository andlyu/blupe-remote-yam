"""Durable chat email alerts, independent of the web/robot processes."""
from datetime import datetime, timezone
import os
import sqlite3
import time


def initialize(path):
    # Capture future inserts atomically, including messages later pruned from chat.
    # Existing history is deliberately not copied into the outbox.
    with sqlite3.connect(path, timeout=10) as db:
        db.execute("SELECT id FROM messages LIMIT 1")
        db.execute("""CREATE TABLE IF NOT EXISTS chat_alert_outbox (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            text TEXT NOT NULL, at REAL NOT NULL)""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS enqueue_chat_alert
            AFTER INSERT ON messages BEGIN
            INSERT INTO chat_alert_outbox(id,name,text,at)
            VALUES(NEW.id,NEW.name,NEW.text,NEW.at);
            END""")


def deliver_one(path, publish, topic):
    with sqlite3.connect(path, timeout=10) as db:
        row = db.execute("SELECT id,name,text,at FROM chat_alert_outbox ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return False
    message_id, name, text, at = row
    stamp = datetime.fromtimestamp(at, timezone.utc).isoformat()
    receipt = publish(
        TopicArn=topic,
        Subject="New message in BluPe Playground",
        Message=(f"{name} sent a playground chat message.\n\n{text}\n\n"
                 f"Sent (UTC): {stamp}\nMessage: {message_id}\n"
                 "Reply here: https://playground.blupe.io/"),
    )
    if not isinstance(receipt, dict) or not receipt.get("MessageId"):
        raise RuntimeError("SNS did not acknowledge the alert")
    with sqlite3.connect(path, timeout=10) as db:
        db.execute("DELETE FROM chat_alert_outbox WHERE id=?", (message_id,))
    print(f"[chat-email] published message_id={message_id}", flush=True)
    return True


def main():
    import boto3
    from botocore.config import Config
    path = os.environ["YAM_CHAT_DATABASE"]
    topic = os.environ["YAM_CHAT_ALERT_TOPIC_ARN"]
    region = topic.split(":")[3]
    client = boto3.client("sns", region_name=region,
                          config=Config(connect_timeout=5, read_timeout=10,
                                        retries={"max_attempts":2}))
    initialize(path)
    print("[chat-email] started", flush=True)
    delay = 1
    while True:
        try:
            sent = deliver_one(path, client.publish, topic)
            delay = 1
            time.sleep(.25 if sent else 1)
        except Exception as error:
            print(f"[chat-email] retry error_type={type(error).__name__}", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 60)


if __name__ == "__main__":
    main()
