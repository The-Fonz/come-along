import logging
import asyncio
import datetime
from os import environ
import json

import dateutil.parser
import asyncpg

from ..utils import record_to_dict, records_to_dict

logger = logging.getLogger('messages.db')


SQL_CREATE_TABLE_MESSAGE = '''
CREATE TABLE message
(
  id                  INTEGER PRIMARY KEY NOT NULL,
  user_id             INTEGER NOT NULL,
  -- Time that message was received by server
  received            TIMESTAMP NOT NULL,
  -- Time that message was created
  timestamp           TIMESTAMP,
  -- Might be a video or voice message, so title/text can be omitted
  title               TEXT,
  text                TEXT,
  telegram_message    JSONB
)
'''

SQL_CREATE_TABLE_MEDIA = '''
CREATE TYPE mediatype AS ENUM ('video', 'image', 'audio');

CREATE TABLE media
(
  id                SERIAL PRIMARY KEY,
  msg_id            INTEGER REFERENCES message(id),
  path              VARCHAR(255),
  type              mediatype,
  -- E.g. received from transcoding service
  received          TIMESTAMP,
  -- Media file timestamp
  timestamp         TIMESTAMP,
  -- True if original video file, false if transcoded
  original          BOOLEAN,
  -- Resolution info, only for video, image
  width             SMALLINT,
  height            SMALLINT,
  -- Only for video, audio
  duration          FLOAT,
  -- Transcoder log
  log               TEXT,
  -- Name of transcoding config (e.g. 'thumb', '360p')
  conf_name          VARCHAR(255)
);
-- For fast joining
CREATE INDEX media_msg_id_index ON media (msg_id);
'''

# Fields not to include, both for message and media
MESSAGE_SENSITIVE_FIELDS = {'telegram_message', 'log'}


class Db():
    @classmethod
    async def create(cls, loop=None, num_video_queues=2):
        "Use like `await Db.create()` to enable use of async methods"
        db = Db()
        db.pool = await asyncpg.create_pool(dsn=environ["DB_URI_ATSITE"])
        return db

    async def create_tables(self, existingconn=None):
        conn = existingconn or self.pool
        stat = await conn.execute(SQL_CREATE_TABLE_MESSAGE)
        stat += await conn.execute(SQL_CREATE_TABLE_MEDIA)
        return stat

    async def getmsg(self, msg_id, exclude_sensitive=True, existingconn=None):
        conn = existingconn or self.pool
        # Postgres is amazing. It joins with media and puts media in json list
        row = await conn.fetchrow('''
        SELECT * FROM message LEFT OUTER JOIN
            (SELECT msg_id, json_agg(r) AS media FROM media AS r GROUP BY r.msg_id)
          AS m ON message.id=m.msg_id WHERE message.id=$1;
        ''', msg_id)
        if not row:
            return None
        return await record_to_dict(row, exclude=MESSAGE_SENSITIVE_FIELDS)

    async def getmsgs(self, user_id, exclude_sensitive=True, existingconn=None):
        # Allow passing in an existing connection for unittesting
        conn = existingconn or self.pool
        # Convert to json list of msgs
        rows = await conn.fetch('''
        SELECT * FROM message LEFT OUTER JOIN
            (SELECT msg_id, json_agg(r) AS media FROM media AS r GROUP BY msg_id)
          AS m ON message.id=m.msg_id WHERE message.user_id=$1 ORDER BY message.timestamp DESC;
          ''', user_id)
        return await records_to_dict(rows, exclude=MESSAGE_SENSITIVE_FIELDS)

    async def uniquemsgs(self, n=5, exclude_sensitive=True, existingconn=None):
        "Return last n most recent messages, max. one per user"
        conn = existingconn or self.pool
        rows = await conn.fetch('''
        SELECT DISTINCT ON (message.user_id) * FROM message LEFT OUTER JOIN
          (SELECT msg_id, json_agg(r) AS media FROM media AS r GROUP BY msg_id)
        AS m ON message.id=m.msg_id ORDER BY message.user_id, message.timestamp DESC LIMIT $1;
        ''', n)
        return await records_to_dict(rows, exclude=MESSAGE_SENSITIVE_FIELDS)

    async def insertmsg(self, msg, existingconn=None):
        """
        Parses msg json and inserts into db.
        """
        conn = existingconn or self.pool
        received = datetime.datetime.now()
        user_id = msg['user_id']
        timestamp = msg.get('timestamp', None)
        if timestamp:
            # Parse if ISO string
            timestamp = timestamp if type(timestamp) == datetime.datetime else dateutil.parser.parse(timestamp)
        title = msg.get('title', None)
        text = msg.get('text', None)
        # Let Postgres return generated id and fetch its value
        id = await conn.fetchval('''
        INSERT INTO message (id, user_id, timestamp, received, title, text, telegram_message)
        VALUES (DEFAULT, $1, $2, $3, $4, $5, $6) RETURNING id
        ''', user_id, timestamp, received, title, text, msg.get('telegram_message'))
        return id

    async def insertmedia(self, media, existingconn=None):
        conn = existingconn or self.pool
        received = datetime.datetime.now()
        id = await conn.fetchval('''
        INSERT INTO media (id, msg_id, received, timestamp, original, type, path, log, conf_name, width, height, duration)
        VALUES (DEFAULT, $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING id''',
        media['msg_id'], received, media.get('timestamp'), media.get('original', False), media['type'], media['path'],
        media.get('log'), media.get('conf_name'), media.get('width'), media.get('height'), media.get('duration'))
        return id


class RollbackException(Exception): pass


async def test_db_basics(db):
    "Test basic message saving"
    # Use non-occurring negative value as user id
    intmin = -2147483648
    async with db.pool.acquire() as conn:
        try:
            # Use nested transactions to easily roll back later
            async with conn.transaction():
                # No messages for non-existing user
                msgs = await db.getmsgs(intmin, existingconn=conn)
                assert msgs == []
                msgjson = {
                    'user_id': intmin,
                    'timestamp': datetime.datetime.now().isoformat(),
                    'received': datetime.datetime.now().isoformat(),
                    'title': 'Titre',
                    'text': 'Bodytext'}
                await db.insertmsg(msgjson, existingconn=conn)
                msgs = await db.getmsgs(intmin, existingconn=conn)
                assert len(msgs) == 1
                assert msgs[0]['title'] == 'Titre'
                # Roll back transaction
                raise RollbackException
        except RollbackException:
            pass


if __name__=="__main__":
    # Nice output when run on cmdline
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(levelname)7s: %(message)s'
    )

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--create', action='store_true',
                        help="Create database tables")
    parser.add_argument('--test', action='store_true',
                        help="Test on real db using nested transactions")
    parser.add_argument('--importmsgs',
                        help="Import json file with list of messages into db")
    args = parser.parse_args()

    l = asyncio.get_event_loop()

    db = l.run_until_complete(Db.create())

    if args.create:
        stat = l.run_until_complete(db.create_tables())
        logger.info("Created tables, status: %s", stat)

    if args.test or args.importmsgs:
        try:
            if args.test:
                l.run_until_complete(test_db_basics(db))
            elif args.importmsgs:
                with open(args.importmsgs, 'r') as f:
                    msgs = json.load(f)
                if not 'y' in input("Continue loading {} messages? [y/N] ".format(len(msgs))).lower():
                    raise Warning("Exiting...")
                coros = [db.insertmsg(msg) for msg in msgs]
                l.run_until_complete(asyncio.wait(coros))
                logger.info("Inserted all messages")
        finally:
            l.run_until_complete(db.finish_queues())
            l.close()
        logger.info("Completed successfully")
