import signal
import asyncio

from autobahn.asyncio.wamp import ApplicationSession, ApplicationRunner

from .db import Db
from ..utils import BackendAppSession, getLogger

logger = getLogger('users.main')


class UsersComponent(BackendAppSession):

    async def onJoin(self, details):
        logger.info("session joined")

        db = await Db.create()
        self.db = db

        async def get_user_by_id(user_id, exclude_sensitive=False):
            return await db.get_user_by_id(user_id, exclude_sensitive=exclude_sensitive)

        async def get_user_by_hash(user_id_hash):
            return await db.getuser(user_id_hash, exclude_sensitive=False)

        async def get_user_by_hash_public(user_id_hash):
            return await db.getuser(user_id_hash, exclude_sensitive=True)

        async def get_user_id_by_hash(user_id_hash):
            "Returns only numeric user id"
            return await db.getuser_id(user_id_hash)

        async def get_user_hash_by_id(user_id):
            "Returns user hash"
            return await db.getuser_hash(user_id)

        async def get_user_id_by_authcode(user_auth_code):
            return await db.check_auth(user_auth_code)

        async def insert_user(usr):
            "Insert properly formatted user"
            id = await db.insertuser(usr)
            return await db.get_user_by_id(id, exclude_sensitive=False)

        self.register(get_user_by_id, 'at.users.get_user_by_id')
        self.register(get_user_by_hash, 'at.users.get_user_by_hash')
        self.register(get_user_by_hash_public, 'at.public.users.get_user_by_hash')
        self.register(get_user_id_by_hash, 'at.users.get_user_id_by_hash')
        self.register(get_user_hash_by_id, 'at.users.get_user_hash_by_id')
        self.register(get_user_id_by_authcode, 'at.users.get_user_id_by_authcode')
        self.register(insert_user, 'at.users.insert_user')


if __name__=="__main__":
    UsersComponent.run_forever()
