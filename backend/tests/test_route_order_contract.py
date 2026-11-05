"""Route registration order is a compatibility contract, not an incident.

Unlisted mail routes used to raise ValueError while importing the app, and the
account read used to be moved with a name lookup that raised StopIteration.
Both are now declarative; these tests keep them that way.
"""
from fastapi import APIRouter
from app.mail_routes import ROUTE_ORDER, create_router, order_routes


def _router():
    return create_router(lambda request: None)


def test_order_table_covers_every_route_and_holds_no_stale_name():
    registered = {route.name for route in _router().routes}
    assert registered == set(ROUTE_ORDER)
    assert len(ROUTE_ORDER) == len(set(ROUTE_ORDER)), 'ROUTE_ORDER 存在重复项'


def test_routes_follow_the_declared_order():
    position = {name: index for index, name in enumerate(ROUTE_ORDER)}
    indexes = [position[route.name] for route in _router().routes]
    assert indexes == sorted(indexes)


def test_unlisted_route_sorts_last_instead_of_failing():
    router = APIRouter()

    @router.get('/zeta')
    def zeta(): return None

    @router.get('/alpha')
    def alpha(): return None

    @router.get('/omega')
    def omega(): return None

    order_routes(router, ('alpha',))
    assert [route.name for route in router.routes] == ['alpha', 'zeta', 'omega']


def test_legacy_position_of_account_routes():
    from app.main import app

    indexes = {
        (method, route.path): index
        for index, route in enumerate(app.routes)
        if getattr(route, 'methods', None)
        for method in route.methods
    }
    mail = [index for (method, path), index in indexes.items() if path.startswith('/api/mail/')]
    assert indexes[('GET', '/api/ilog/accounts')] < min(mail), '账号列表必须排在邮件接口之前（历史顺序）'
    assert indexes[('POST', '/api/ilog/accounts')] > max(mail)
    for method in ('PUT', 'DELETE'):
        assert indexes[(method, '/api/ilog/accounts/{account_id}')] > max(mail)
