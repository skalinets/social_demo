import redis

from main import create_status, create_user, get_status_messages


def test_hi():
    assert 1 == 1


def test_redis():
    r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
    r.set("foo", "bar")
    assert r.get("foo") == "bar"


conn = redis.Redis(decode_responses=True)


def test_create_user():
    login = "yoba"
    name = "Yoba Yobaevich"
    create_user(conn, login, name)
    # assert 1 == conn.get("users:")


def test_create_post():
    conn.flushdb()
    uid = create_user(conn, "fl", "dsdl")
    assert uid == 1
    data = {}
    message = "Hello, world!"
    message_id = create_status(conn, uid, message, **data)
    assert message_id == 1
    posts = conn.hget("user:%s" % uid, "posts")
    assert "1" == posts


def test_get_status_messages():
    conn.flushdb()
    uid = create_user(conn, "fl", "dsdl")
    assert uid == 1
    data = {}
    message = "Hello, world!"
    message_id = create_status(conn, uid, message, **data)
    assert message_id == 1
    messages = get_status_messages(conn, uid)
    assert messages[0]["message"] == message
