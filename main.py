from dataclasses import dataclass
import time
import redis
import uuid
import math
from fasthtml import common as fh
from icecream import ic


def before(req, sess):
    auth = req.scope["auth"] = sess.get("auth", None)
    if not auth:
        return login_redir


bware = fh.Beforeware(
    before, skip=[r"/favicon\.ico", r"/static/.*", r".*\.css", "/login"]
)

app, rt = fh.fast_app(live=True, before=bware)


@rt("/login")
def get():
    frm = fh.Form(
        fh.Input(id="login", placeholder="login"),
        fh.Input(id="name", placeholder="name"),
        fh.Button("login"),
        action="/login",
        method="post",
    )
    return fh.Titled("Login", frm)


@dataclass()
class Login:
    name: str
    login: str


conn = redis.Redis(decode_responses=True, db=1)


login_redir = fh.RedirectResponse("/login", status_code=303)


def get_login(name):
    pass


def add_login(login):
    create_user(conn, login.name, login.pwd)
    return login


@rt("/login")
def post(login: Login, sess):
    if not login.name:
        return login_redir
    # Indexing into a MiniDataAPI table queries by primary key, which is `name` here.
    # It returns a dataclass object, if `dataclass()` has been called at some point, or a dict otherwise.
    uid = create_user(conn, login.login, login.name)
    if not uid:
        return login_redir
    sess["auth"] = login.login
    return fh.RedirectResponse("/", status_code=303)


# Instead of using `app.route` (or the `rt` shortcut), you can also use `app.get`, `app.post`, etc.
# In this case, the function name is not used to determine the HTTP verb.


@app.get("/logout")
def logout(sess):
    del sess["auth"]
    return login_redir


@rt("/")
def get_home(auth):
    uid = get_user_id(auth)
    posts = get_status_messages(conn, uid)
    ic(posts)
    d_posts = [fh.Div(post["message"]) for post in posts]
    return fh.Titled(
        f"timeline of {auth}",
        fh.Form(
            fh.Input(name="message", placeholder="your message here"),
            fh.Button("Post"),
            method="POST",
            action="/post",
        ),
        *d_posts,
        fh.Form(
            fh.Button("Logout", action="/logout"),
            method="POST",
            action="/logout",
        ),
    )

@rt("/logout", methods=["POST"])
def logout_action(sess):
    sess.pop("auth", None)
    return fh.RedirectResponse("/login", status_code=303)


@rt("/{user}/messages")
def get_user_messages(user: str):
    uid = conn.hget("users:", user)
    if not uid:
        return fh.Response("User not found", status_code=404)
    posts = get_status_messages(conn, uid)
    d_posts = [fh.Div(post["message"]) for post in posts]
    return fh.Titled(f"timeline of {user}", *d_posts)


@rt("/post", methods=["POST"])
def post_message(auth, message: str):
    uid = get_user_id(auth)
    if not uid:
        return fh.RedirectResponse("/logout", status_code=307)

    message_id = create_status(conn, uid, message)
    return fh.RedirectResponse("/", status_code=307)


def get_user_id(auth):
    return conn.hget("users:", auth)


fh.serve()


def acquire_lock_with_timeout(conn, lockname, acquire_timeout=10, lock_timeout=10):
    identifier = str(uuid.uuid4())  # A
    lockname = "lock:" + lockname
    lock_timeout = int(math.ceil(lock_timeout))  # D

    end = time.time() + acquire_timeout
    while time.time() < end:
        if conn.setnx(lockname, identifier):  # B
            conn.expire(lockname, lock_timeout)  # B
            return identifier
        elif conn.ttl(lockname) < 0:  # C
            conn.expire(lockname, lock_timeout)  # C

        time.sleep(0.001)

    return False


def to_bytes(x):
    return x.encode() if isinstance(x, str) else x


def to_str(x):
    return x.decode() if isinstance(x, bytes) else x


def release_lock(conn, lockname, identifier):
    pipe = conn.pipeline(True)
    lockname = "lock:" + lockname
    identifier = to_bytes(identifier)

    while True:
        try:
            pipe.watch(lockname)  # A
            if pipe.get(lockname) == identifier:  # A
                pipe.multi()  # B
                pipe.delete(lockname)  # B
                pipe.execute()  # B
                return True  # B

            pipe.unwatch()
            break

        except redis.WatchError:  # C
            pass  # C

    return False  # D


CONFIGS = {}
CHECKED = {}


def create_user(conn, login, name):
    llogin = login.lower()
    lock = acquire_lock_with_timeout(conn, "user:" + llogin, 1)  # A
    if not lock:  # B
        return None  # B

    if conn.hget("users:", llogin):  # C
        release_lock(conn, "user:" + llogin, lock)  # C
        return None  # C

    id = conn.incr("user:id:")  # D
    pipeline = conn.pipeline(True)
    pipeline.hset("users:", llogin, id)  # E
    pipeline.hmset(
        "user:%s" % id,
        {  # F
            "login": login,  # F
            "id": id,  # F
            "name": name,  # F
            "followers": 0,  # F
            "following": 0,  # F
            "posts": 0,  # F
            "signup": time.time(),  # F
        },
    )
    pipeline.execute()
    release_lock(conn, "user:" + llogin, lock)  # G
    return id  # H


def get_status_messages(conn, uid, timeline="home:", page=1, count=5):  # A
    statuses = conn.zrevrange(  # B
        "%s%s" % (timeline, uid), (page - 1) * count, page * count - 1
    )  # B

    pipeline = conn.pipeline(True)
    for id in statuses:  # C
        pipeline.hgetall("status:%s" % (to_str(id),))  # C

    return [_f for _f in pipeline.execute() if _f]


def create_status(conn, uid, message, **data):
    pipeline = conn.pipeline(True)
    pipeline.hget("user:%s" % uid, "login")  # A
    pipeline.incr("status:id:")  # B
    login, id = pipeline.execute()

    if not login:  # C
        return None  # C

    data.update(
        {
            "message": message,  # D
            "posted": time.time(),  # D
            "id": id,  # D
            "uid": uid,  # D
            "login": login,  # D
        }
    )
    pipeline.hmset("status:%s" % id, data)  # D
    pipeline.hincrby("user:%s" % uid, "posts")  # E
    pipeline.zadd(f"home:{uid}", {id: time.time()})  # F
    pipeline.execute()
    return id  # F
