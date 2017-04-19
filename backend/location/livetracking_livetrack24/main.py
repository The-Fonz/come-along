import asyncio

from autobahn.wamp.exception import ApplicationError
from aiohttp import web

from ...utils import BackendAppSession, getLogger

logger = getLogger('location.livetrack24.main')


async def site_factory(wampsess):
    """
    Creates aiohttp server listening on specified port
    :param wampsession:
    """

    async def client(request):
        "Should return 0 if invalid, otherwise user id integer"
        user_id_hash = request.match_info.get('user')
        user_auth_code = request.match_info.get('pass')
        if not user_id_hash or not user_auth_code:
            # Livetrack24 returns status 200 on basically any error
            return web.Response(status=200, text='0')
        try:
            user_id = await wampsess.call('at.users.check_user_authcode', user_id_hash, user_auth_code)
        except ApplicationError:
            logger.exception("Could not reach user service!")
            return web.HTTPInternalServerError(reason="Internal communication error")
        if user_id:
            # Send back actual user ID
            return web.Response(text=str(user_id), status=200)
        else:
            return web.Response(text='0', status=200)

    async def track(request):
        "Track point as per the spec at http://www.livetrack24.com/docs/wiki/LiveTracking%20API"
        g = lambda n: request.match_info.get(n)
        leolive = g('leolive')
        sessionid = g('sid')
        if not sessionid:
            return web.Response(status=200, text="NOK : No session ID")
        # Ignore start/end track packets
        if leolive != 4:
            return web.Response(status=200)

        trackpt = {
            # Rightmost 3 bits are user ID, see spec
            "user_id": sessionid & 0x00ffffff,
            # Unix GPS timestamp in GMT
            "timestamp": g('tm'),
            "ptz": {
                # Lat/lon in decimal notation
                "longitude": g('lon'),
                "latitude": g('lat'),
                # Altitude in meters above MSL (not geoid), no decimals
                "height_m_msl": g('alt')
            },
            # Speed over ground in km/h, no decimals
            "speed_over_ground_kmh": g('sog'),
            # Course over ground in degrees 0-360, no decimals
            "course_over_ground_deg": g('cog')
        }

        await wampsess.call('at.location.insert_gps_point', trackpt)

    app = web.Application()

    app.router.add_get(r'/client.php', client)
    app.router.add_get(r'/track.php', track)

    loop = asyncio.get_event_loop()

    await loop.create_server(app.make_handler(), '127.0.0.1', 5002)


class SiteComponent(BackendAppSession):

    async def onJoin(self, details):
        logger.info("session joined")
        await site_factory(self)


if __name__=="__main__":
    SiteComponent.run_forever()