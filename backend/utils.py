import os
import uuid
import json
import asyncio
import logging
import datetime

from hashids import Hashids
from autobahn.asyncio.wamp import ApplicationSession


def getLogger(name):
    "Centralized logging setup"
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(levelname)7s: %(asctime)s %(name)s:%(funcName)s:%(lineno)s %(message)s'
    )
    return logging.getLogger(name)


logger = getLogger('utils')


async def record_to_json(record, exclude=set()):
    "Transform record to json, exclude keys if needed"
    out = {}
    for k,v in record.items():
        if k not in exclude:
            # Parse json
            if type(v) == str and v.strip().startswith('{'):
                v = json.loads(v)
            # Json parser cannot serialize datetime by default
            elif type(v) == datetime.datetime:
                v = v.isoformat()
            out[k] = v
    return out

async def records_to_json(records, **kwargs):
    "Converts list of asyncpg.Record to json"
    out = []
    # Can make async using await asyncio.sleep(0) but should be real fast
    for r in records:
        out.append(await record_to_json(r, **kwargs))
    return out


async def friendlyhash():
    "Generate random hash of length 8"
    h = Hashids(salt=str(uuid.uuid4()), min_length=8)
    # Just to make sure that it's length 8, should always be the case anyway
    return h.encode(123)[:8]


async def friendly_auth_code():
    "Generate friendly uppercase code of length 5"
    h = Hashids(salt=str(uuid.uuid4()), min_length=5, alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    return h.encode(456)[:5]


class BackendAppSession(ApplicationSession):
    def __init__(self, config=None):
        ApplicationSession.__init__(self, config)
        logger.info("component created")

    def onConnect(self):
        logger.info("transport connected")
        self.join(self.config.realm, [u"ticket"], 'backend')

    def onChallenge(self, challenge):
        logger.info("authentication challenge received")
        if challenge.method == u"ticket":
            return os.environ['AT_CROSSBAR_TICKET']
        else:
            raise Exception("Invalid auth method %s", challenge.method)

    # def onLeave(self, details):
    #     print("session left")
    #
    def onDisconnect(self):
        logger.warning("transport disconnected, stopping event loop...")
        asyncio.get_event_loop().stop()
